"""Data access layer following the Rubin Observatory Butler pattern.

Defines the abstract base class for dataset retrieval, the
`DataCoordinate` (Data ID) model, and the concrete `DiskButler`
implementation wrapping local FITS files and a SQLite index.

Notes
-----
Implements requirement REQ: BKD-5.
"""

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from astrometricslib.utilities.enums import FilterType
from datastore.butler import Butler as _GenericButler
from datastore.butler import DatasetSpec

# Declares this module's own public surface. Without it, sphinx-automodapi
# documents every imported name too, which is what produced the
# "stub file not found" warnings for re-exports and typing helpers.
__all__ = [
    "AbstractButler",
    "DataCoordinate",
    "DiskButler",
]

logger = logging.getLogger(__name__)


class DataCoordinate(BaseModel):
    """Structured metadata coordinates (Data ID) identifying a dataset.

    Attributes
    ----------
    target : `str`, optional
        Astronomical target name, e.g. "M 81". Default `None`.
    role : `str`, optional
        Frame role, e.g. LIGHT, DARK, FLAT, BIAS. Default `None`.
    camera : `str`, optional
        Camera sensor identifier. Default `None`.
    telescope : `str`, optional
        Telescope optics identifier. Default `None`.
    filter : `FilterType`, optional
        Filter type enum. Default `None`.
    exposure : `float`, optional
        Exposure duration in seconds. Default `None`.
    iso : `str`, optional
        ISO or gain setting. Default `None`.
    sequence_id : `int`, optional
        Frame sequence index. Default `None`.
    path : `str`, optional
        Optional physical path override. Default `None`.
    """

    target: str | None = Field(default=None, description="Astronomical target name, e.g. M 81")
    role: str | None = Field(default=None, description="Frame role, e.g. LIGHT, DARK, FLAT, BIAS")
    camera: str | None = Field(default=None, description="Camera sensor identifier")
    telescope: str | None = Field(default=None, description="Telescope optics identifier")
    filter: FilterType | None = Field(default=None, description="Filter type enum")
    exposure: float | None = Field(default=None, description="Exposure duration in seconds")
    iso: str | None = Field(default=None, description="ISO or Gain setting")
    sequence_id: int | None = Field(default=None, description="Frame sequence index")
    path: str | None = Field(default=None, description="Optional physical path override")


class AbstractButler(ABC):
    """Abstract data access layer (DAL) following the Rubin Butler pattern."""

    @abstractmethod
    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Retrieve a dataset for a coordinate.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to retrieve (e.g.
            "target_catalog", "raw_frame").
        coordinate : `dict`
            Data ID fields identifying which dataset instance to load.

        Returns
        -------
        dataset : `Any`
            The hydrated domain model object, array, or catalog.
        """
        pass

    @abstractmethod
    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Persist a dataset under a given type and coordinate.

        Parameters
        ----------
        obj : `Any`
            The dataset object to persist.
        dataset_type : `str`
            Identifier of the dataset kind being written.
        coordinate : `dict`
            Data ID fields identifying where to store the dataset.
        """
        pass

    @abstractmethod
    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check if the dataset exists for the coordinate.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to check.
        coordinate : `dict`
            Data ID fields identifying which dataset instance to check.

        Returns
        -------
        exists : `bool`
            `True` if the dataset is present, `False` otherwise.
        """
        pass

    @abstractmethod
    def query_coordinates(self, dataset_type: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Find coordinates matching a query template.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to query.
        query : `dict`
            Partial Data ID fields to match against.

        Returns
        -------
        coordinates : `list` of `dict`
            Data ID dictionaries matching the query template.
        """
        pass

    @abstractmethod
    def get_local_path(self, dataset_type: str, coordinate: dict[str, Any]) -> str:
        """Get a local filesystem path/URI for physical file access.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to resolve.
        coordinate : `dict`
            Data ID fields identifying which dataset instance to
            locate.

        Returns
        -------
        path : `str`
            Local filesystem path or URI for the dataset.
        """
        pass


def _target_extra_columns(target: Any) -> dict[str, Any]:
    """Return the indexed columns kept alongside a target's data_json.

    Returns
    -------
    columns : `dict`
        The `name`/`ra`/`dec` column values for `target`.
    """
    return {"name": target.common_name, "ra": target.ra, "dec": target.dec}


def _coerce_float(value: Any) -> float | None:
    """Best-effort float coercion of a value.

    Returns
    -------
    coerced : `float` or `None`
        `value` as a `float`, or `None` if it is empty or invalid.
    """
    if value in ("", None):
        return None
    try:
        return float(value)
    except ValueError, TypeError:
        return None


def _stellar_extra_columns(stellar_object: Any) -> dict[str, Any]:
    """Return the indexed columns kept alongside a stellar object's data_json.

    Returns
    -------
    columns : `dict`
        The `target_id`/`name`/`ra`/`dec`/`magnitude` column values
        for `stellar_object`.
    """
    target_ids = getattr(stellar_object, "target_ids", None)
    return {
        "target_id": ",".join(target_ids) if target_ids else None,
        "name": stellar_object.name,
        "ra": _coerce_float(stellar_object.right_ascension),
        "dec": _coerce_float(stellar_object.declination),
        "magnitude": _coerce_float(getattr(stellar_object, "magnitude", None)),
    }


class DiskButler(AbstractButler):
    """Concrete Butler backed by the local SQLite WAL database and disk.

    `target_catalog`/`stellar_catalog` are keyed-model tables with no
    FITS-file analog, so they're delegated to a shared, generic
    `datastore.Butler` instance -- the same class wayfindinglib's
    Butler composes for its own record types. `raw_frame`/
    `stacked_image`/`raw_frames`/`DataCoordinate` path resolution have
    no equivalent in datastore and stay implemented directly here.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize DiskButler with standard configuration.

        Parameters
        ----------
        config : `AppConfiguration`, optional
            Application configuration object. If `None` (default), the
            process-wide singleton from `get_configuration` is used.
        """
        if config is None:
            from astrometricslib.utilities.config_loader import get_configuration

            config = get_configuration()
        self.config = config
        self._stellar_catalog_cache: list[Any] | None = None
        self._generic = self._build_generic_butler(config)

    @staticmethod
    def _build_generic_butler(config: Any) -> _GenericButler:
        from astrometricslib.models.stellar_source import StellarObject
        from astrometricslib.models.target import Target

        return _GenericButler(
            config,
            db_name="astrometrics.db",
            specs={
                "target_catalog": DatasetSpec(
                    table_name="targets",
                    model_class=Target,
                    extra_column_types={"name": "TEXT", "ra": "TEXT", "dec": "TEXT"},
                    extra_columns=_target_extra_columns,
                ),
                "stellar_catalog": DatasetSpec(
                    table_name="stellar_objects",
                    model_class=StellarObject,
                    extra_column_types={
                        "target_id": "TEXT",
                        "name": "TEXT",
                        "ra": "REAL",
                        "dec": "REAL",
                        "magnitude": "REAL",
                    },
                    extra_columns=_stellar_extra_columns,
                ),
            },
        )

    def get(self, dataset_type: str, coordinate: dict[str, Any]) -> Any:
        """Hydrate targets, stellar catalogs, or frame images.

        Parameters
        ----------
        dataset_type : `str`
            One of "target_catalog", "stellar_catalog", "raw_frame",
            "stacked_image", or "raw_frames".
        coordinate : `dict`
            Data ID fields identifying which dataset instance to load.
            For "raw_frame"/"stacked_image" this resolves a local
            path; for "raw_frames" this must include "target".

        Returns
        -------
        dataset : `Any`
            The hydrated target list, stellar catalog, image, or list
            of frames, depending on `dataset_type`.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not one of the supported
            values.
        """
        if dataset_type == "target_catalog":
            # Goes through disk_interface.load_targets rather than
            # self._generic.get_all so the one-time legacy JSON-shard
            # migration it performs on first boot still runs; writes
            # still go through the shared generic Butler below.
            from astrometricslib.drivers import disk_interface

            return disk_interface.load_targets(self.config)
        elif dataset_type == "stellar_catalog":
            # put()/merge_and_persist_records() write through to (or
            # invalidate) self._stellar_catalog_cache specifically so
            # this can skip disk I/O on repeated reads -- serve from it
            # when populated, instead of unconditionally hitting disk.
            if self._stellar_catalog_cache is None:
                self._stellar_catalog_cache = self._generic.get_all("stellar_catalog")
            return self._stellar_catalog_cache

        elif dataset_type == "raw_frame" or dataset_type == "stacked_image":
            path = self.get_local_path(dataset_type, coordinate)
            from astrometricslib.utilities.image import AstrometricsImage

            return AstrometricsImage(path)
        elif dataset_type == "raw_frames":
            target = coordinate.get("target")
            if not target:
                return []
            from astrometricslib.data_access import frame_scanning as frame_scanning_operations

            frames_root_path = self.config.get_frames_path()
            frame_scanning_operations.scan_target_directory(target, frames_root_path)
            return target.frames
        else:
            raise ValueError(f"Unknown dataset type: {dataset_type}")

    def put(self, obj: Any, dataset_type: str, coordinate: dict[str, Any]) -> None:
        """Persist data to SQLite or files.

        Parameters
        ----------
        obj : `Any`
            The object(s) to persist: a `Target` list for
            "target_catalog", a single `Target` for "target_record",
            or a `StellarObject` list for "stellar_catalog".
        dataset_type : `str`
            One of "target_catalog", "target_record", or
            "stellar_catalog".
        coordinate : `dict`
            Data ID fields (unused by the current write paths, but
            required by the `AbstractButler` interface).

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not one of the supported
            values.
        """
        if dataset_type == "target_catalog":
            self._generic.put_all("target_catalog", obj)
        elif dataset_type == "target_record":
            self._generic.put(obj, "target_catalog")
        elif dataset_type == "stellar_catalog":
            self._generic.put_all("stellar_catalog", obj)
            self._stellar_catalog_cache = obj
        else:
            raise ValueError(f"Write operation not supported on dataset type: {dataset_type}")

    def merge_and_persist_records(
        self,
        dataset_type: str,
        objects: list[Any],
        merge_function: Callable[[Any | None, Any], Any],
    ) -> None:
        """Combine new records with what is currently stored, then save.

        Reads only the current rows matching the given objects' ids,
        applies ``merge_function(existing_record_or_none,
        updated_record) -> merged_record`` to each, and saves the
        merged results under a single lock, so concurrent processes
        updating overlapping records never race on the same rows the
        way a plain get()-then-put() read-modify-write would.

        Parameters
        ----------
        dataset_type : `str`
            Which dataset to merge into. One of "stellar_catalog" or
            "target_catalog".
        objects : `list`
            The new or updated records to merge in.
        merge_function : `Callable`
            Callable combining an existing record (or `None` if the
            record is new) with the updated record, returning the
            merged record to save.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not "stellar_catalog" or
            "target_catalog".
        """
        if dataset_type not in ("stellar_catalog", "target_catalog"):
            raise ValueError(f"merge_and_persist_records is not supported for dataset type: {dataset_type}")

        self._generic.merge_and_persist(dataset_type, objects, merge_function)

        # Invalidate the cache so the next get() re-reads the merged
        # state from disk.
        if dataset_type == "stellar_catalog":
            self._stellar_catalog_cache = None

    def delete_by_ids(self, dataset_type: str, ids: list[str]) -> None:
        """Lock-guarded, targeted delete of only the given ids.

        Parameters
        ----------
        dataset_type : `str`
            One of "stellar_catalog" or "target_catalog".
        ids : `list` [`str`]
            Ids to remove.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not "stellar_catalog" or
            "target_catalog".
        """
        if dataset_type not in ("stellar_catalog", "target_catalog"):
            raise ValueError(f"delete_by_ids is not supported for dataset type: {dataset_type}")

        self._generic.delete_by_ids(dataset_type, ids)

        if dataset_type == "stellar_catalog":
            self._stellar_catalog_cache = None

    def exists(self, dataset_type: str, coordinate: dict[str, Any]) -> bool:
        """Check if a database record or file path exists.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to check.
        coordinate : `dict`
            Data ID fields identifying which dataset instance to
            check.

        Returns
        -------
        exists : `bool`
            `True` if the resolved local path exists, `False`
            otherwise (including if path resolution itself fails).
        """
        try:
            path = self.get_local_path(dataset_type, coordinate)
            return os.path.exists(path)
        except Exception:
            return False

    def query_coordinates(self, dataset_type: str, query: dict[str, Any]) -> list[dict[str, Any]]:
        """Find matching coordinates.

        Parameters
        ----------
        dataset_type : `str`
            Identifier of the dataset kind to query (unused by this
            minimal implementation).
        query : `dict`
            Partial Data ID fields to match against.

        Returns
        -------
        coordinates : `list` of `dict`
            A single-element list containing `query` unchanged. This
            is a minimal query implementation for file routing rather
            than a real catalog search.
        """
        # Minimal query implementation for file routing
        return [query]

    def get_local_path(self, dataset_type: str, coordinate: dict[str, Any]) -> str:
        """Resolve a local file path for the given dataset and coordinate.

        Parameters
        ----------
        dataset_type : `str`
            One of "stacked_image" or "raw_frame".
        coordinate : `dict`
            Data ID fields. If "path" is present and truthy, it is
            returned as-is; otherwise the path is derived from
            "target" and "role".

        Returns
        -------
        path : `str`
            Local filesystem path for the dataset.

        Raises
        ------
        ValueError
            Raised if `dataset_type` is not one of the supported
            values.
        """
        if coordinate.get("path"):
            return coordinate["path"]

        target = coordinate.get("target", "Unknown")
        role = coordinate.get("role", "LIGHT").upper()

        if dataset_type == "stacked_image":
            safe_target = target.replace(" ", "_")
            return os.path.join(
                self.config.get_frames_path(), "lights", target, f"{safe_target}_Stacked.fits"
            )
        elif dataset_type == "raw_frame":
            # Just fallback to frames root lights mapping
            return os.path.join(self.config.get_frames_path(), role.lower() + "s", target)
        else:
            raise ValueError(f"Local path resolution not supported for dataset type: {dataset_type}")
