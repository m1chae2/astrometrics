"""Shared, generic keyed-model Butler.

`Butler` persists pydantic model instances to SQLite tables keyed by
id, following the same "get/put/exists" Rubin-Butler-derived shape
astrometricslib's and wayfindinglib's Butlers already used
independently. It is domain-agnostic: registering a `DatasetSpec` for
a dataset type is all a consumer needs to get targeted-upsert,
full-table-replace, and lock-guarded merge/delete operations for that
table. FITS-file-specific concerns (path resolution, `DataCoordinate`)
have no analog here and stay in astrometricslib's own extension of
this class.
"""

import json
import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from datastore.disk_interface import connect_db, file_lock, safe_json_dumps

logger = logging.getLogger(__name__)

__all__ = ["AbstractButler", "Butler", "DatasetSpec"]


@dataclass(frozen=True)
class DatasetSpec:
    """Registration record for one dataset type's generic persistence shape.

    Attributes
    ----------
    table_name : `str`
        SQLite table backing this dataset type.
    model_class : `type`
        Pydantic model class used to hydrate rows via
        ``model_class.model_validate(...)``. Instances must implement
        ``.serialize()`` for the value written to ``data_json``.
    id_field : `str`, optional
        Attribute name on the model instance holding its primary key.
        Default ``"id"``.
    extra_column_types : `dict` [`str`, `str`], optional
        Additional indexed SQL columns beyond ``(id, data_json)``,
        mapping column name to SQL type (e.g. ``{"ra": "TEXT"}``).
        Lets existing on-disk schemas (targets' name/ra/dec, stellar
        objects' target_id/name/ra/dec/magnitude) survive without a
        migration.
    extra_columns : callable, optional
        ``extra_columns(model_instance) -> {"ra": ..., "dec": ...}``,
        matching the keys in `extra_column_types`.
    """

    table_name: str
    model_class: type
    id_field: str = "id"
    extra_column_types: dict[str, str] = field(default_factory=dict)
    extra_columns: Callable[[Any], dict[str, Any]] | None = None
    serializer: Callable[[Any], Any] | None = None
    """Override for producing the JSON-serializable payload from a model
    instance. Defaults to ``obj.serialize()``; pass e.g.
    ``lambda obj: obj.model_dump(mode="json", by_alias=True)`` for plain
    pydantic models with no `.serialize()` convention."""


class AbstractButler(ABC):
    """Minimal 3-method Rubin-Butler-derived abstract data access layer."""

    @abstractmethod
    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Retrieve a single dataset instance for a coordinate."""

    @abstractmethod
    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Persist a dataset instance under a given type and coordinate."""

    @abstractmethod
    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check whether a dataset instance exists for a coordinate."""


class Butler(AbstractButler):
    """Concrete, generic keyed-model Butler backed by SQLite.

    One instance owns a registry of `DatasetSpec` objects (dataset_type
    -> spec) and a single SQLite database. Both astrometricslib's
    `DiskButler` and wayfindinglib's `DiskButler` compose an instance
    of this class for their non-FITS dataset types.
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        config: Any,
        db_name: str = "astrometrics.db",
        specs: dict[str, DatasetSpec] | None = None,
        db_dir: str | None = None,
    ):
        """Initialize the Butler with configuration and dataset registrations.

        Parameters
        ----------
        config : `Any`
            Application configuration object exposing
            ``get_library_path()``, used to resolve the database
            directory unless `db_dir` is given.
        db_name : `str`, optional
            SQLite database filename. Default ``"astrometrics.db"``.
        specs : `dict` [`str`, `DatasetSpec`], optional
            Initial dataset type registrations.
        db_dir : `str`, optional
            Explicit database directory, overriding
            ``config.get_library_path()``. Lets a consumer keep its own
            library directory (e.g. wayfindinglib's, distinct from
            astrometricslib's) while still using this shared Butler.
        """
        self.config = config
        self._db_name = db_name
        self._db_dir = db_dir
        self._specs: dict[str, DatasetSpec] = dict(specs or {})

    def register_dataset_type(self, dataset_type: str, spec: DatasetSpec) -> None:
        """Register (or replace) the persistence shape for a dataset type."""
        self._specs[dataset_type] = spec

    def _db_path(self) -> str:
        directory = self._db_dir if self._db_dir is not None else str(self.config.get_library_path())
        return os.path.join(directory, self._db_name)

    def _spec(self, dataset_type: str) -> DatasetSpec:
        try:
            return self._specs[dataset_type]
        except KeyError:
            raise ValueError(f"Unknown dataset type: {dataset_type}") from None

    def _ensure_table(self, cursor, spec: DatasetSpec) -> None:  # ruff: ignore[missing-type-function-argument]
        extra_columns_sql = "".join(
            f", {name} {sql_type}" for name, sql_type in spec.extra_column_types.items()
        )
        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS {spec.table_name} "
            f"(id TEXT PRIMARY KEY, data_json TEXT{extra_columns_sql})"
        )

    def _row_to_obj(self, spec: DatasetSpec, row: Any) -> Any:
        return spec.model_class.model_validate(json.loads(row["data_json"]))

    def _row_values(self, spec: DatasetSpec, obj: Any) -> dict[str, Any]:
        payload = spec.serializer(obj) if spec.serializer is not None else obj.serialize()
        values: dict[str, Any] = {"id": getattr(obj, spec.id_field), "data_json": safe_json_dumps(payload)}
        if spec.extra_columns is not None:
            values.update(spec.extra_columns(obj))
        return values

    def _upsert_one(self, cursor, spec: DatasetSpec, obj: Any) -> None:  # ruff: ignore[missing-type-function-argument]
        values = self._row_values(spec, obj)
        columns = list(values.keys())
        placeholders = ",".join("?" for _ in columns)
        cursor.execute(
            f"INSERT OR REPLACE INTO {spec.table_name} ({','.join(columns)}) VALUES ({placeholders})",
            [values[column] for column in columns],
        )

    def get_all(self, dataset_type: str) -> list[Any]:
        """Load every row for a dataset type.

        Returns
        -------
        rows : `list`
            Every persisted instance of `dataset_type`.
        """
        spec = self._spec(dataset_type)
        db_path = self._db_path()
        if not os.path.exists(db_path):
            return []
        conn = connect_db(db_path)
        try:
            cursor = conn.cursor()
            self._ensure_table(cursor, spec)
            cursor.execute(f"SELECT data_json FROM {spec.table_name}")
            return [self._row_to_obj(spec, row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get_by_ids(self, dataset_type: str, ids: list[str]) -> list[Any]:
        """Load only the rows matching the given ids.

        Returns
        -------
        rows : `list`
            The persisted instances matching `ids`.
        """
        if not ids:
            return []
        spec = self._spec(dataset_type)
        db_path = self._db_path()
        if not os.path.exists(db_path):
            return []
        conn = connect_db(db_path)
        try:
            cursor = conn.cursor()
            self._ensure_table(cursor, spec)
            placeholders = ",".join("?" for _ in ids)
            cursor.execute(f"SELECT data_json FROM {spec.table_name} WHERE id IN ({placeholders})", list(ids))
            return [self._row_to_obj(spec, row) for row in cursor.fetchall()]
        finally:
            conn.close()

    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Retrieve a single row by ``coordinate["id"]``.

        Returns
        -------
        row : `Any` or `None`
            The matching instance, or `None` if absent.
        """
        obj_id = coordinate.get("id")
        if obj_id is None:
            return None
        matches = self.get_by_ids(dataset_type, [obj_id])
        return matches[0] if matches else None

    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check whether a row matching ``coordinate["id"]`` exists.

        Returns
        -------
        found : `bool`
            `True` if a matching row exists.
        """
        return self.get(dataset_type, coordinate) is not None

    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any] | None = None) -> None:
        """Upsert a single record."""
        spec = self._spec(dataset_type)
        conn = connect_db(self._db_path())
        try:
            cursor = conn.cursor()
            self._ensure_table(cursor, spec)
            self._upsert_one(cursor, spec, obj)
            conn.commit()
        finally:
            conn.close()

    def put_all(self, dataset_type: str, objects: list[Any]) -> None:
        """Replace the whole table so it exactly matches `objects`.

        Upserts every given object, then deletes any row whose id is
        not present in `objects`. For callers that legitimately hold
        "this list is now the whole table" (e.g. an explicit bulk
        save), not for partial updates from a process that doesn't
        hold the full table -- use `merge_and_persist`/`delete_by_ids`
        for those instead.
        """
        spec = self._spec(dataset_type)
        conn = connect_db(self._db_path())
        try:
            cursor = conn.cursor()
            self._ensure_table(cursor, spec)
            for obj in objects:
                self._upsert_one(cursor, spec, obj)
            active_ids = [getattr(obj, spec.id_field) for obj in objects]
            if active_ids:
                placeholders = ",".join("?" for _ in active_ids)
                cursor.execute(f"DELETE FROM {spec.table_name} WHERE id NOT IN ({placeholders})", active_ids)
            else:
                cursor.execute(f"DELETE FROM {spec.table_name}")
            conn.commit()
        finally:
            conn.close()

    def merge_and_persist(
        self,
        dataset_type: str,
        objects: list[Any],
        merge_function: Callable[[Any | None, Any], Any],
    ) -> None:
        """Combine new records with what is currently stored, then save.

        Reads only the current rows matching the given objects' ids,
        applies ``merge_function(existing_record_or_none,
        updated_record) -> merged_record`` to each, and saves the
        merged results under a single lock, so concurrent callers
        updating overlapping records never race on the same rows the
        way a plain get()-then-put() read-modify-write would. Never
        touches rows outside the given `objects`.

        Parameters
        ----------
        dataset_type : `str`
            Which registered dataset type to merge into.
        objects : `list`
            The new or updated records to merge in.
        merge_function : callable
            Combines an existing record (or `None` if new) with the
            updated record, returning the merged record to save.
        """
        if not objects:
            return
        spec = self._spec(dataset_type)
        lock_path = os.path.join(str(self.config.get_library_path()), "locks", f"{dataset_type}_merge.lock")
        with file_lock(lock_path):
            ids = [getattr(record, spec.id_field) for record in objects]
            existing_rows = self.get_by_ids(dataset_type, ids)
            existing_by_id = {getattr(record, spec.id_field): record for record in existing_rows}
            merged_records = [
                merge_function(existing_by_id.get(getattr(record, spec.id_field)), record)
                for record in objects
            ]

            conn = connect_db(self._db_path())
            try:
                cursor = conn.cursor()
                self._ensure_table(cursor, spec)
                for merged in merged_records:
                    self._upsert_one(cursor, spec, merged)
                conn.commit()
            finally:
                conn.close()

    def delete_by_ids(self, dataset_type: str, ids: list[str]) -> None:
        """Lock-guarded, targeted delete of only the given ids.

        Complements `merge_and_persist`'s upsert-only contract, which
        cannot express row removal.
        """
        if not ids:
            return
        spec = self._spec(dataset_type)
        lock_path = os.path.join(str(self.config.get_library_path()), "locks", f"{dataset_type}_merge.lock")
        with file_lock(lock_path):
            conn = connect_db(self._db_path())
            try:
                cursor = conn.cursor()
                self._ensure_table(cursor, spec)
                placeholders = ",".join("?" for _ in ids)
                cursor.execute(f"DELETE FROM {spec.table_name} WHERE id IN ({placeholders})", list(ids))
                conn.commit()
            finally:
                conn.close()
