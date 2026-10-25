"""Purpose: wayfindinglib's single storage path.

Description: `DiskButler` is the one place wayfindinglib reads and writes
its own records. It follows the Rubin Observatory Butler pattern that
astrometricslib uses: a small get/put/exists interface keyed by a dataset
type name. It implements astrometricslib's shared `AbstractButler` and
hands every call to astrometricslib's generic SQLite `Butler`, which keeps
one table per dataset type in wayfindinglib's own ``wayfinding.db`` file.
That file lives in wayfindinglib's own library folder, kept apart from
astrometricslib's library folder.

Each dataset type maps to a table and a pydantic model class in
`_DATASET_TYPES`. A record's key is its ``id`` field, except for
``calibration_stats``, whose key is ``camera_id``.

The alignment, guiding and polar alignment records of each night are too
many, and are read back by time range, so they live in plain SQL tables of
the same ``wayfinding.db`` file. `DiskButler.control_records` gives the
`ControlRecordStore` that reads and writes them.
"""

import configparser
import json
import sqlite3
from pathlib import Path
from typing import Any

from astrometricslib import AbstractButler, DatasetSpec, InvalidArgumentError, connect_db, safe_json_dumps
from astrometricslib import Butler as _GenericButler
from wayfindinglib.drivers.control_record_store import ControlRecordStore
from wayfindinglib.models.equipment_and_site.calibration import CalibrationStats
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure
from wayfindinglib.models.equipment_and_site.focus_model import FocusModel
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.planning.observation_package import ObservationPackage
from wayfindinglib.models.planning.suggestion_log import SuggestionLogEntry
from wayfindinglib.models.policy.commissioning import CommissioningRun
from wayfindinglib.models.policy.delegation import DelegationPolicy
from wayfindinglib.models.policy.safety import SafetyRuleSet
from wayfindinglib.models.session.divergence import DivergenceRecord
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
)
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis

_DATASET_TYPES: dict[str, tuple[str, type]] = {
    "observation_session": ("wayfinding_observation_sessions", ObservationSession),
    "observation_package": ("observation_packages", ObservationPackage),
    "package_suggestion_event": ("package_suggestion_events", SuggestionLogEntry),
    "site_profile": ("site_profiles", SiteProfile),
    "enclosure": ("enclosures", Enclosure),
    "guider_calibration": ("guider_calibrations", GuiderCalibration),
    "focus_model": ("focus_models", FocusModel),
    "calibration_stats": ("calibration_stats", CalibrationStats),
    "delegation_policy": ("delegation_policies", DelegationPolicy),
    "safety_rule_set": ("safety_rule_sets", SafetyRuleSet),
    "divergence_record": ("divergence_records", DivergenceRecord),
    "commissioning_run": ("commissioning_runs", CommissioningRun),
    "guide_star_loss_event": ("guide_star_loss_events", GuideStarLossEvent),
    "guiding_spectrum_analysis": ("guiding_spectrum_analyses", GuidingSpectrumAnalysis),
    "ekos_session_context": ("ekos_session_contexts", EkosSessionContext),
    "guiding_run": ("guiding_runs", GuidingRunSummary),
}
"""Maps a dataset_type string to its (table_name, model_class).

`calibration_stats` uses `camera_id` as its key, because one record
holds the calibration inventory of one camera
(`Wayfinding_Library_Architecture.md` Section 3.2).
"""

_ID_FIELD_FOR: dict[str, str] = {"calibration_stats": "camera_id"}
"""Overrides the primary-key field name for dataset types whose natural
key is not `id` -- everything else defaults to `id`."""


_EXECUTION_OWNED_ENTRY_FIELDS = frozenset({
    "status",
    "status_detail",
    "actual_start_time",
    "actual_end_time",
    "frames_captured",
})
"""Queue entry fields that only Execution writes. Planning owns every other
entry field (see `Wayfinding_Library_Architecture.md` Section 3.3)."""

_PLANNING_OWNED_SESSION_FIELDS = (
    "night_date",
    "site_profile_id",
    "telescope_id",
    "camera_id",
    "unplaced_package_diagnostics",
    "checklist",
    "framing",
)
"""Session fields that only Planning writes (besides the queue and the
revision, which `_merge_planning_fields` handles on its own)."""


def _merge_planning_fields(session: ObservationSession, stored: ObservationSession) -> None:
    """Copy what Planning changed since `session` was loaded into `session`.

    The session object is changed in place, because Execution keeps using
    the same object after a save. Execution's own fields (status,
    outcomes, telemetry, faults) stay as they are in `session`.

    The merge rules are:

    * The fields in `_PLANNING_OWNED_SESSION_FIELDS` come from `stored`.
    * The revision is the larger of the two, so it never goes backwards.
    * An entry still in both queues keeps the execution fields of `session`
      and takes every other field from `stored`.
    * An entry only in `stored` was added by Planning after the load. It is
      appended.
    * An entry only in `session` was removed by Planning. It is dropped when
      it has not started. If it has started, it stays, so its record is not
      lost.
    * The queue order is the stored order. Kept entries that Planning had
      dropped follow at the end.

    Parameters
    ----------
    session : `ObservationSession`
        The session Execution wants to save. Changed in place.
    stored : `ObservationSession`
        The session as it is stored right now.
    """
    for field_name in _PLANNING_OWNED_SESSION_FIELDS:
        setattr(session, field_name, getattr(stored, field_name))
    session.revision = max(session.revision, stored.revision)

    local_by_id = {entry.id: entry for entry in session.queue}
    merged: list[QueuedObservationPackage] = []
    for stored_entry in stored.queue:
        local_entry = local_by_id.pop(stored_entry.id, None)
        if local_entry is None:
            merged.append(stored_entry)
            continue
        for field_name in QueuedObservationPackage.model_fields:
            if field_name not in _EXECUTION_OWNED_ENTRY_FIELDS:
                setattr(local_entry, field_name, getattr(stored_entry, field_name))
        merged.append(local_entry)
    # Entries Planning removed: keep only those Execution has started.
    merged.extend(entry for entry in local_by_id.values() if entry.status != QueueEntryStatus.PENDING)
    session.queue[:] = merged


def pick_session_for_night(sessions: list[ObservationSession]) -> ObservationSession | None:
    """Choose the one session that stands for a night.

    A night should have one session. Older data can hold several. The one
    with the most queue entries wins. When entries tie, the newest session
    (by its creation time) wins.

    Parameters
    ----------
    sessions : `list` [`ObservationSession`]
        The sessions recorded for one night.

    Returns
    -------
    session : `ObservationSession` or `None`
        The winner, or `None` when the list is empty.
    """
    if not sessions:
        return None
    return max(sessions, key=lambda session: (len(session.queue), session.created_at))


def _wayfinding_library_path(app_config: Any) -> Path:
    """Return wayfindinglib's own library folder, creating it if needed.

    The folder comes from the ``path`` option of the
    ``[Wayfinding Library]`` configuration section. A relative path is
    taken relative to the project root. Without that option the folder
    is ``wayfinding`` inside the user's data folder
    (``~/.local/share/astrometrics/wayfinding``).

    Parameters
    ----------
    app_config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    path : `Path`
        Absolute path to the folder.
    """
    try:
        path = Path(app_config.app_config.get("Wayfinding Library", "path"))
        if not path.is_absolute():
            path = (app_config.get_project_root() / path).absolute()
    except configparser.NoSectionError, configparser.NoOptionError, KeyError:
        path = app_config.get_data_path() / "wayfinding"
    path.mkdir(parents=True, exist_ok=True)
    return path.absolute()


class DiskButler(AbstractButler):
    """Local SQLite-backed Butler implementation for wayfindinglib."""

    def __init__(self, app_config: Any = None) -> None:
        """Initialize the Butler with an application configuration.

        Parameters
        ----------
        app_config : `AppConfiguration`, optional
            Application configuration object. If `None` (default), the
            process-wide singleton from `get_configuration` is used.
        """
        if app_config is None:
            from astrometricslib import get_configuration

            app_config = get_configuration()
        self.config = app_config
        self.__generic: _GenericButler | None = None
        self.__control_records: ControlRecordStore | None = None

    @property
    def library_path(self) -> Path:
        """Wayfindinglib's own library folder, created if needed.

        Holds ``wayfinding.db`` and the files wayfindinglib keeps next to
        it, such as downloaded Ekos logs and the latest centering frame.
        """
        return _wayfinding_library_path(self.config)

    @property
    def control_records(self) -> ControlRecordStore:
        """The store of alignment, guiding and polar alignment records.

        It keeps its tables in this butler's ``wayfinding.db`` file. It is
        built on first use.

        Returns
        -------
        records : `ControlRecordStore`
            The record store.
        """
        if self.__control_records is None:
            self.__control_records = ControlRecordStore(str(self.library_path / "wayfinding.db"))
        return self.__control_records

    @property
    def _generic(self) -> _GenericButler:
        """Build the shared generic Butler on first use.

        Built here rather than in `__init__` so that a `DiskButler` made
        with a stand-in `config` (as some tests do, to check error paths
        without ever calling get or put) does not look up a real library
        folder.
        """
        if self.__generic is None:
            specs = {
                dataset_type: DatasetSpec(
                    table_name=table_name,
                    model_class=model_class,
                    id_field=_ID_FIELD_FOR.get(dataset_type, "id"),
                    serializer=lambda obj: obj.model_dump(mode="json", by_alias=True),
                )
                for dataset_type, (table_name, model_class) in _DATASET_TYPES.items()
            }
            self.__generic = _GenericButler(
                self.config, db_name="wayfinding.db", db_dir=str(self.library_path), specs=specs
            )
        return self.__generic

    def get(self, dataset_type: str, selector: dict[str, Any]) -> Any:
        """Retrieve the dataset a selector identifies.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to read, one of the keys of `_DATASET_TYPES`.
        selector : `dict`
            Names the record: a ``"session_id"`` key for
            ``"observation_session"``, otherwise an ``"id"`` key (or the
            record's own key field, ``"camera_id"`` for
            ``"calibration_stats"``).

        Returns
        -------
        dataset : `Any`
            The record, or `None` if there is none.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        if dataset_type == "observation_session":
            return self._generic.get(dataset_type, {"id": selector.get("session_id", "")})
        id_field = _ID_FIELD_FOR.get(dataset_type, "id")
        model_id = selector.get("id") or selector.get(id_field, "")
        return self._generic.get(dataset_type, {"id": model_id})

    def get_all(self, dataset_type: str) -> list[Any]:
        """Retrieve every recorded instance of a dataset type.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to read, one of the keys of `_DATASET_TYPES`.

        Returns
        -------
        datasets : `list`
            Every recorded instance of that dataset type.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        return self._generic.get_all(dataset_type)

    def put(self, obj: Any, dataset_type: str, selector: dict[str, Any]) -> None:
        """Record a dataset under a given type.

        Parameters
        ----------
        obj : `Any`
            The model instance to record.
        dataset_type : `str`
            The dataset kind to write, one of the keys of `_DATASET_TYPES`.
        selector : `dict`
            Not used: the record's own key field (``obj.id``, or
            ``obj.camera_id`` for ``"calibration_stats"``) is its key.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        self._generic.put(obj, dataset_type)

    def exists(self, dataset_type: str, selector: dict[str, Any]) -> bool:
        """Check whether the dataset a selector identifies exists.

        Parameters
        ----------
        dataset_type : `str`
            The dataset kind to check.
        selector : `dict`
            Names the record, as for `get`.

        Returns
        -------
        exists : `bool`
            `True` if a matching record is found.

        Raises
        ------
        InvalidArgumentError
            Raised if `dataset_type` is not recognized.
        """
        if dataset_type not in _DATASET_TYPES:
            raise InvalidArgumentError(f"Unknown dataset type: {dataset_type}")
        return self.get(dataset_type, selector) is not None

    def put_session_if_revision(self, session: ObservationSession, expected_revision: int) -> bool:
        """Write a night plan only if it has not changed since it was read.

        The check and the write are one SQLite ``UPDATE`` statement, so they
        happen together: a second writer that read the same revision finds
        the revision already moved and writes nothing. The stored revision
        is read from the record's own JSON. A record stored before plans had
        a revision counts as revision 0.

        Parameters
        ----------
        session : `ObservationSession`
            The session to store, already carrying its new revision.
        expected_revision : `int`
            The revision the caller read. The write happens only if the
            stored revision is still this one.

        Returns
        -------
        written : `bool`
            `True` when the session was stored. `False` when the stored
            revision was no longer `expected_revision`, or no such session is
            stored. Nothing is changed in that case.

        Raises
        ------
        sqlite3.OperationalError
            For a database error other than a missing table, such as a locked
            file.
        """
        # Reading first makes sure the table exists.
        self._generic.get("observation_session", {"id": session.id})
        payload = safe_json_dumps(session.model_dump(mode="json", by_alias=True))
        connection = connect_db(str(self.library_path / "wayfinding.db"))
        try:
            cursor = connection.execute(
                "UPDATE wayfinding_observation_sessions SET data_json = ? WHERE id = ? "
                "AND COALESCE(json_extract(data_json, '$.revision'), 0) = ?",
                (payload, session.id, expected_revision),
            )
            connection.commit()
            return cursor.rowcount == 1
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                # No sessions table yet, so no such session is stored.
                return False
            # A locked or broken database is a real failure; do not hide it.
            raise
        finally:
            connection.close()

    def insert_session_for_night(self, session: ObservationSession) -> ObservationSession:
        """Store a new session unless its night already has one.

        The check and the insert are one database transaction, so two
        callers cannot both add a session for the same night. When the night
        already has sessions, nothing is written and the winner
        (`pick_session_for_night`) is returned.

        Parameters
        ----------
        session : `ObservationSession`
            The session to store when its night has none.

        Returns
        -------
        stored : `ObservationSession`
            The session now standing for that night: `session` itself when it
            was stored, otherwise the one already there.
        """
        connection = connect_db(str(self.library_path / "wayfinding.db"))
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS wayfinding_observation_sessions "
                "(id TEXT PRIMARY KEY, data_json TEXT)"
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute("SELECT data_json FROM wayfinding_observation_sessions").fetchall()
            same_night = [
                stored
                for stored in (
                    ObservationSession.model_validate(json.loads(row["data_json"])) for row in rows
                )
                if stored.night_date == session.night_date
            ]
            existing = pick_session_for_night(same_night)
            if existing is not None:
                connection.rollback()
                return existing
            payload = safe_json_dumps(session.model_dump(mode="json", by_alias=True))
            connection.execute(
                "INSERT INTO wayfinding_observation_sessions (id, data_json) VALUES (?, ?)",
                (session.id, payload),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return session

    def put_package_if_revision(self, package: ObservationPackage, expected_revision: int) -> bool:
        """Write a package only if it has not changed since it was read.

        Works like `put_session_if_revision`: the check and the write are one
        SQLite ``UPDATE``. A package stored before packages had a revision
        counts as revision 0.

        Parameters
        ----------
        package : `ObservationPackage`
            The package to store, already carrying its new revision.
        expected_revision : `int`
            The revision the caller read.

        Returns
        -------
        written : `bool`
            `True` when the package was stored. `False` when the stored
            revision was no longer `expected_revision`, or no such package is
            stored.

        Raises
        ------
        sqlite3.OperationalError
            For a database error other than a missing table, such as a locked
            file.
        """
        # Reading first makes sure the table exists.
        self._generic.get("observation_package", {"id": package.id})
        payload = safe_json_dumps(package.model_dump(mode="json", by_alias=True))
        connection = connect_db(str(self.library_path / "wayfinding.db"))
        try:
            cursor = connection.execute(
                "UPDATE observation_packages SET data_json = ? WHERE id = ? "
                "AND COALESCE(json_extract(data_json, '$.revision'), 0) = ?",
                (payload, package.id, expected_revision),
            )
            connection.commit()
            return cursor.rowcount == 1
        except sqlite3.OperationalError as error:
            if "no such table" in str(error):
                return False
            raise
        finally:
            connection.close()

    def delete_package(
        self, package_id: str, *, expected_revision: int | None = None, only_status: str | None = None
    ) -> bool:
        """Remove one observation package from the library.

        With `expected_revision` or `only_status`, the check and the delete
        are one SQLite statement, so a package that was edited or promoted
        in the meantime is not removed.

        Parameters
        ----------
        package_id : `str`
            The package id.
        expected_revision : `int`, optional
            Delete only if the stored revision is this one.
        only_status : `str`, optional
            Delete only if the stored status is this one, such as
            ``"SUGGESTED"``.

        Returns
        -------
        deleted : `bool`
            `True` when a package was removed, `False` when none had that id
            (or the revision or status check failed).
        """
        # Reading first makes sure the table exists.
        self._generic.get("observation_package", {"id": package_id})
        connection = connect_db(str(self.library_path / "wayfinding.db"))
        try:
            cursor = connection.execute(
                "DELETE FROM observation_packages WHERE id = ? "
                "AND (? IS NULL OR COALESCE(json_extract(data_json, '$.revision'), 0) = ?) "
                "AND (? IS NULL OR json_extract(data_json, '$.status') = ?)",
                (package_id, expected_revision, expected_revision, only_status, only_status),
            )
            connection.commit()
            return cursor.rowcount == 1
        finally:
            connection.close()

    def put_session_from_execution(self, session: ObservationSession) -> ObservationSession:
        """Save a session for Execution without erasing what Planning wrote.

        Execution holds its own copy of the session while it runs. Planning
        can change the stored copy at the same time (tick a checklist item,
        add or reorder queue entries, set the framing). A plain `put` would
        write Execution's old copy over those changes. This method does not.
        In one database transaction it reads the stored session, copies the
        Planning-owned fields from it into `session` (see
        `_merge_planning_fields`), and writes the result. The transaction
        holds the database write lock from the read to the write, so a
        Planning write cannot slip in between.

        This method never changes the revision. Only Planning edits count
        as plan edits.

        Parameters
        ----------
        session : `ObservationSession`
            The session Execution wants to save. It is updated in place with
            the Planning-owned fields that are stored, so later saves
            from the same object keep them.

        Returns
        -------
        session : `ObservationSession`
            The same object, now merged and saved. When no copy is stored
            yet, it is saved as it is.
        """
        connection = connect_db(str(self.library_path / "wayfinding.db"))
        try:
            # The same table the generic butler keeps. Created here because a
            # first save may come before any other write.
            connection.execute(
                "CREATE TABLE IF NOT EXISTS wayfinding_observation_sessions "
                "(id TEXT PRIMARY KEY, data_json TEXT)"
            )
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT data_json FROM wayfinding_observation_sessions WHERE id = ?", (session.id,)
            ).fetchone()
            if row is not None:
                stored = ObservationSession.model_validate(json.loads(row["data_json"]))
                _merge_planning_fields(session, stored)
            payload = safe_json_dumps(session.model_dump(mode="json", by_alias=True))
            connection.execute(
                "INSERT OR REPLACE INTO wayfinding_observation_sessions (id, data_json) VALUES (?, ?)",
                (session.id, payload),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return session
