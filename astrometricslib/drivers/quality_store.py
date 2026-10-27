"""The append-only store of quality measurements in astrometrics.db.

Each `QualityMeasurement` becomes one row of the table
``quality_measurements``, with one typed column per field (the subject and
the check limit are spread over several columns). Nothing is stored as JSON,
so any field can be filtered or sorted with plain SQL.

The table is append-only. A second write of the same run, metric, subject
and channel is ignored (``ON CONFLICT ... DO NOTHING``, so any other
constraint error still raises), never replaced, so re-running a job cannot
change or duplicate what is already stored. Nothing in this module deletes
rows. The view ``quality_measurements_latest`` gives the newest row for
each subject, metric and channel; "newest" means the latest
``measured_at``, never the pipeline version.

The camera and equipment settings are kept once each in
``quality_capture_setting``; a measurement refers to them by id.

The schema version is kept in its own table, ``quality_schema_version``
(one row). This store never reads or writes ``PRAGMA user_version``, because
that number is one value for the whole file and other code has used it. A
database written by the first version of this code has the quality tables
but no version table; it is recognized as version 1 and gets the table.
Version 2 adds the column ``variant`` (the label of a subset stack, empty for
a library stack); the move from version 1 adds the column, fills it for the
subset rows that already exist, and rebuilds the "latest" view. Version 3 adds
the side table ``quality_calibration_identity`` (the calibration, optic,
camera and software each library stack used; see
`QualityStore.write_calibration_identities`). The move from version 2 only
creates that table, so it is additive and safe to repeat. Version 4 rounds the
pixel scale in ``quality_capture_setting`` (as `CaptureSetting` now does),
merges the rows that become equal into the one with the smallest id, points
their measurements at it and deletes the others; see
`QualityStore._migrate_from_version_3`. Version 5 renames the stored metric
names that began ``stacking.half`` plus ``_difference_`` to
``stacking.odd_even_difference_*`` (the measurement is now called the
odd-even difference); see
`QualityStore._migrate_from_version_4`. Take a backup first
with ``scripts/backup_library_databases.py``. A migration only touches the
quality tables, view and version table and never any other table.

Reading and writing are separate paths. A read opens the file read-only
(``mode=ro``), takes no write lock, never changes the journal mode and never
migrates. A file that does not exist, or has no quality tables yet, reads as
empty. Only a write creates the file and brings the schema up to date.

Times are stored as UTC with the offset ``+00:00``, so text order is time
order. A naive time is refused.

Every database problem is raised as `StorageError`.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote

from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.models.quality.calibration_identities import (
    CalibrationFileIdentity,
    CalibrationIdentities,
)
from astrometricslib.models.quality.capture_setting import CaptureSetting, round_pixel_scale
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.limit import LimitTier, QualityLimit
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)

logger = logging.getLogger(__name__)

#: The schema version this code writes and expects.
SCHEMA_VERSION = 5

_MEASUREMENT_COLUMNS = (
    "metric",
    "definition_version",
    "subject_level",
    "subject_id",
    "target_id",
    "observing_night_id",
    "capture_setting_id",
    "channel",
    "variant",
    "status",
    "value",
    "uncertainty",
    "sample_count",
    "reason",
    "check_outcome",
    "check_limit_value",
    "check_limit_unit",
    "check_limit_tier",
    "check_limit_derivation",
    "check_limit_sample_count",
    "check_limit_register_entry",
    "action_taken",
    "origin",
    "pipeline_version",
    "run_id",
    "job_id",
    "measured_at",
)

# The view is made again by the migration to version 2, because SQLite fixes
# the columns a view named with ``*`` when the view is created.
_CREATE_LATEST_VIEW_STATEMENT = """
    CREATE VIEW IF NOT EXISTS quality_measurements_latest AS
    SELECT * FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY subject_level, subject_id, metric, channel
            ORDER BY measured_at DESC, row_id DESC
        ) AS recency_rank
        FROM quality_measurements
    )
    WHERE recency_rank = 1
"""

# The folder name that marks a subset stack in a subject id written before
# the ``variant`` column existed (``<target>/_subsets/<label>/<file>``).
_LEGACY_SUBSET_MARKER = "/_subsets/"

_CREATE_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS quality_capture_setting (
        capture_setting_id INTEGER PRIMARY KEY,
        camera TEXT NOT NULL,
        telescope TEXT,
        focal_length_mm REAL,
        pixel_scale_arcsec REAL,
        filter_name TEXT,
        exposure_seconds REAL,
        gain_setting INTEGER,
        offset INTEGER,
        binning INTEGER
    )
    """,
    # A unique index over expressions, because SQLite treats NULLs as
    # distinct inside a plain UNIQUE constraint.
    """
    CREATE UNIQUE INDEX IF NOT EXISTS quality_capture_setting_unique
    ON quality_capture_setting (
        camera,
        IFNULL(telescope, ''),
        IFNULL(focal_length_mm, -1.0),
        IFNULL(pixel_scale_arcsec, -1.0),
        IFNULL(filter_name, ''),
        IFNULL(exposure_seconds, -1.0),
        IFNULL(gain_setting, -1),
        IFNULL(offset, -1),
        IFNULL(binning, -1)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_measurements (
        row_id INTEGER PRIMARY KEY,
        metric TEXT NOT NULL,
        definition_version INTEGER NOT NULL,
        subject_level TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        target_id TEXT,
        observing_night_id TEXT,
        capture_setting_id INTEGER REFERENCES quality_capture_setting (capture_setting_id),
        channel TEXT NOT NULL DEFAULT '',
        variant TEXT,
        status TEXT NOT NULL,
        value REAL,
        uncertainty REAL,
        sample_count INTEGER,
        reason TEXT NOT NULL DEFAULT '',
        check_outcome TEXT,
        check_limit_value REAL,
        check_limit_unit TEXT,
        check_limit_tier TEXT,
        check_limit_derivation TEXT,
        check_limit_sample_count INTEGER,
        check_limit_register_entry TEXT,
        action_taken TEXT,
        origin TEXT NOT NULL,
        pipeline_version TEXT,
        run_id TEXT NOT NULL,
        job_id TEXT,
        measured_at TEXT NOT NULL,
        UNIQUE (run_id, metric, subject_level, subject_id, channel)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS quality_measurements_metric_night
    ON quality_measurements (metric, observing_night_id)
    """,
    """
    CREATE INDEX IF NOT EXISTS quality_measurements_target_metric
    ON quality_measurements (target_id, metric)
    """,
    """
    CREATE INDEX IF NOT EXISTS quality_measurements_subject
    ON quality_measurements (subject_id)
    """,
    _CREATE_LATEST_VIEW_STATEMENT,
)

# Version 3 adds this table. One row per (run, stack subject). Each master
# column holds a JSON list of ``[path, size_bytes, modified_ns]`` file
# identities, because a stack can use several masters and the list is only
# ever compared as a whole, never filtered on.
_CREATE_CALIBRATION_IDENTITY_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS quality_calibration_identity (
        run_id TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        target_id TEXT,
        camera TEXT,
        optic TEXT,
        focal_length_mm REAL,
        flat_masters TEXT NOT NULL DEFAULT '[]',
        bias_masters TEXT NOT NULL DEFAULT '[]',
        dark_masters TEXT NOT NULL DEFAULT '[]',
        siril_version TEXT,
        stacking_algorithm_version INTEGER,
        recorded_at TEXT NOT NULL,
        PRIMARY KEY (run_id, subject_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS quality_calibration_identity_target
    ON quality_calibration_identity (target_id)
    """,
)

_INSERT_CALIBRATION_IDENTITY_SQL = (
    "INSERT INTO quality_calibration_identity (run_id, subject_id, target_id, camera, optic, "
    "focal_length_mm, flat_masters, bias_masters, dark_masters, siril_version, "
    "stacking_algorithm_version, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (run_id, subject_id) DO NOTHING"
)

_CAPTURE_SETTING_FIELDS = (
    "camera",
    "telescope",
    "focal_length_mm",
    "pixel_scale_arcsec",
    "filter_name",
    "exposure_seconds",
    "gain_setting",
    "offset",
    "binning",
)

# These statements are built only from the fixed column names above, never
# from caller input, so no value can reach the SQL text.
_INSERT_CAPTURE_SETTING_SQL = (
    f"INSERT INTO quality_capture_setting ({', '.join(_CAPTURE_SETTING_FIELDS)}) "  # ruff: ignore[hardcoded-sql-expression]
    f"VALUES ({', '.join('?' * len(_CAPTURE_SETTING_FIELDS))}) "
    "ON CONFLICT DO NOTHING"
)
# "IS" compares NULL equal to NULL, unlike "=".
_SELECT_CAPTURE_SETTING_ID_SQL = (
    "SELECT capture_setting_id FROM quality_capture_setting WHERE "  # ruff: ignore[hardcoded-sql-expression]
    + " AND ".join(f"{name} IS ?" for name in _CAPTURE_SETTING_FIELDS)
)
_INSERT_MEASUREMENT_SQL = (
    f"INSERT INTO quality_measurements ({', '.join(_MEASUREMENT_COLUMNS)}) "  # ruff: ignore[hardcoded-sql-expression]
    f"VALUES ({', '.join('?' * len(_MEASUREMENT_COLUMNS))}) "
    "ON CONFLICT (run_id, metric, subject_level, subject_id, channel) DO NOTHING"
)


def _format_time(moment: datetime) -> str:
    """Format a time as UTC so that text order equals time order.

    Every stored time and every ``since`` bound goes through here, so there
    is one stored format: an ISO 8601 string ending in ``+00:00``.

    Parameters
    ----------
    moment : `datetime`
        The time to store. It must carry a time zone.

    Returns
    -------
    text : `str`
        An ISO 8601 string in UTC with microseconds always present.

    Raises
    ------
    InvalidArgumentError
        If the time has no time zone.
    """
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise InvalidArgumentError("A time given to the quality store must carry a time zone.")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


def _decode_masters(text: str) -> list[CalibrationFileIdentity]:
    """Decode the JSON text of a list of file identities.

    Parameters
    ----------
    text : `str`
        ``[[path, size_bytes, modified_ns], ...]`` as stored.

    Returns
    -------
    masters : `list` [`CalibrationFileIdentity`]
        The identities, in stored order.
    """
    return [
        CalibrationFileIdentity(path=path, size_bytes=size_bytes, modified_ns=modified_ns)
        for path, size_bytes, modified_ns in json.loads(text)
    ]


def _measurement_to_values(measurement: QualityMeasurement) -> tuple[Any, ...]:
    """Spread a measurement over the columns in `_MEASUREMENT_COLUMNS`.

    Parameters
    ----------
    measurement : `QualityMeasurement`
        The measurement to store.

    Returns
    -------
    values : `tuple`
        One value per column, in column order.
    """
    subject = measurement.subject
    limit = measurement.check_limit
    return (
        measurement.metric,
        measurement.definition_version,
        subject.level.value,
        subject.subject_id,
        subject.target_id,
        subject.observing_night_id,
        subject.capture_setting_id,
        subject.channel or "",
        subject.variant,
        measurement.status.value,
        measurement.value,
        measurement.uncertainty,
        measurement.sample_count,
        measurement.reason,
        None if measurement.check_outcome is None else measurement.check_outcome.value,
        None if limit is None else limit.value,
        None if limit is None else limit.unit,
        None if limit is None else limit.tier.value,
        None if limit is None else limit.derivation,
        None if limit is None else limit.sample_count,
        None if limit is None else limit.register_entry,
        measurement.action_taken,
        measurement.origin.value,
        measurement.pipeline_version,
        measurement.run_id,
        measurement.job_id,
        _format_time(measurement.measured_at),
    )


def _row_to_measurement(row: sqlite3.Row) -> QualityMeasurement:
    """Build a measurement from one database row.

    Parameters
    ----------
    row : `sqlite3.Row`
        A row of ``quality_measurements`` (or of the latest view).

    Returns
    -------
    measurement : `QualityMeasurement`
        The measurement, with `row_id` filled.

    Raises
    ------
    StorageError
        If the stored row fails the model's checks. The error names the row.
    """
    try:
        return _decode_row(row)
    except (ValueError, TypeError, KeyError) as error:
        raise StorageError(
            "A stored quality measurement could not be read back.",
            details={"row_id": row["row_id"], "error": str(error)},
            retryable=False,
        ) from error


def _decode_row(row: sqlite3.Row) -> QualityMeasurement:
    """Build a measurement from one row, letting validation errors escape.

    Parameters
    ----------
    row : `sqlite3.Row`
        A row of ``quality_measurements`` (or of the latest view).

    Returns
    -------
    measurement : `QualityMeasurement`
        The measurement, with `row_id` filled.
    """
    limit = None
    if row["check_limit_unit"] is not None:
        limit = QualityLimit(
            value=row["check_limit_value"],
            unit=row["check_limit_unit"],
            tier=LimitTier(row["check_limit_tier"]),
            derivation=row["check_limit_derivation"],
            sample_count=row["check_limit_sample_count"],
            register_entry=row["check_limit_register_entry"],
        )
    outcome = row["check_outcome"]
    return QualityMeasurement(
        metric=row["metric"],
        definition_version=row["definition_version"],
        subject=QualitySubject(
            level=SubjectLevel(row["subject_level"]),
            subject_id=row["subject_id"],
            target_id=row["target_id"],
            observing_night_id=row["observing_night_id"],
            capture_setting_id=row["capture_setting_id"],
            channel=row["channel"] or None,
            variant=row["variant"] if "variant" in row.keys() else None,
        ),
        status=MeasurementStatus(row["status"]),
        value=row["value"],
        uncertainty=row["uncertainty"],
        sample_count=row["sample_count"],
        reason=row["reason"],
        check_outcome=None if outcome is None else GateStatus(outcome),
        check_limit=limit,
        action_taken=row["action_taken"],
        origin=MeasurementOrigin(row["origin"]),
        pipeline_version=row["pipeline_version"],
        run_id=row["run_id"],
        job_id=row["job_id"],
        measured_at=datetime.fromisoformat(row["measured_at"]),
        row_id=row["row_id"],
    )


def quality_database_path(app_config: AppConfiguration | None = None) -> str:
    """Give the path of the database that holds the quality tables.

    Parameters
    ----------
    app_config : `AppConfiguration`, optional
        Application configuration. The process-wide one is used if omitted.

    Returns
    -------
    db_path : `str`
        ``<library path>/astrometrics.db``.
    """
    if app_config is None:
        app_config = get_configuration()
    return os.path.join(str(app_config.get_library_path()), "astrometrics.db")


class QualityStore:
    """Writes and reads quality measurements in a SQLite database.

    Creating a store touches nothing on disk. A write (`write_measurements`
    or `get_or_create_capture_setting`) creates the file and the quality
    tables if they are missing. A read (`query` and `get_capture_setting`)
    opens the file read-only and never creates or migrates anything.

    Parameters
    ----------
    db_path : `str`
        Filesystem path to the SQLite database (normally astrometrics.db).

    Attributes
    ----------
    db_path : `str`
        The database path.
    """

    def __init__(self, db_path: str) -> None:
        """Remember the database path.

        Parameters
        ----------
        db_path : `str`
            Filesystem path to the SQLite database.
        """
        self.db_path = db_path

    @classmethod
    def from_configuration(cls, app_config: AppConfiguration | None = None) -> QualityStore:
        """Open the store in the configured image library's astrometrics.db.

        Parameters
        ----------
        app_config : `AppConfiguration`, optional
            Application configuration. The process-wide one is used if
            omitted.

        Returns
        -------
        store : `QualityStore`
            A store on ``<library path>/astrometrics.db``.
        """
        return cls(quality_database_path(app_config))

    @contextmanager
    def _write_transaction(self) -> Generator[sqlite3.Connection]:
        """Open a connection, bring the schema up to date and run the body.

        The body runs in the same transaction as the schema check, so two
        writers never both migrate. The transaction commits when the body
        finishes and rolls back if it raises. Any `sqlite3.Error` or
        `OSError` becomes a `StorageError`.

        Yields
        ------
        connection : `sqlite3.Connection`
            A connection with a write transaction already begun.

        Raises
        ------
        StorageError
            If the database cannot be opened, migrated or written, or was
            written by a newer version of this code.
        """
        try:
            connection = connect_db(self.db_path)
        except (sqlite3.Error, OSError) as error:
            raise StorageError(
                "The quality database could not be opened.",
                details={"path": self.db_path, "error": str(error)},
            ) from error
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._migrate(connection)
            yield connection
            connection.commit()
        except (sqlite3.Error, OSError) as error:
            connection.rollback()
            raise StorageError(
                "The quality database could not be used.",
                details={"path": self.db_path, "error": str(error)},
            ) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Generator[sqlite3.Connection | None]:
        """Open the database read-only, or give `None` if nothing is there.

        The connection uses ``mode=ro``. It takes no write lock, does not
        change the journal mode and does not migrate. A missing file, a
        file with no quality tables and a file whose schema version is older
        than the code's all give `None`: the writer migrates, so there is
        nothing to read yet.

        Yields
        ------
        connection : `sqlite3.Connection` or `None`
            A read-only connection, or `None` if there is nothing to read.

        Raises
        ------
        StorageError
            If the file cannot be read, or was written by a newer version of
            this code.
        """
        if not os.path.isfile(self.db_path):
            yield None
            return
        uri = f"file:{quote(self.db_path)}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=30.0)
            connection.row_factory = sqlite3.Row
        except (sqlite3.Error, OSError) as error:
            raise StorageError(
                "The quality database could not be opened.",
                details={"path": self.db_path, "error": str(error)},
            ) from error
        try:
            version = self._read_schema_version(connection)
            if version > SCHEMA_VERSION:
                raise StorageError(
                    "The quality database is newer than this software understands.",
                    details={"found": version, "supported": SCHEMA_VERSION},
                    retryable=False,
                )
            yield connection if version >= 1 else None
        except (sqlite3.Error, OSError) as error:
            raise StorageError(
                "The quality database could not be read.",
                details={"path": self.db_path, "error": str(error)},
            ) from error
        finally:
            connection.close()

    @staticmethod
    def _read_schema_version(connection: sqlite3.Connection) -> int:
        """Return the quality schema version stored in the database.

        Parameters
        ----------
        connection : `sqlite3.Connection`
            Any open connection. Nothing is written.

        Returns
        -------
        version : `int`
            0 if there are no quality tables. 1 if the quality tables exist
            but the version table does not (the first release of this
            store). Otherwise the number in ``quality_schema_version``.
        """
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('quality_schema_version', 'quality_measurements')"
            )
        }
        if "quality_schema_version" in names:
            row = connection.execute("SELECT MAX(version) FROM quality_schema_version").fetchone()
            return int(row[0]) if row[0] is not None else 0
        return 1 if "quality_measurements" in names else 0

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Bring the quality schema to `SCHEMA_VERSION`.

        Call this inside a write transaction. Version 0 means the quality
        tables do not exist yet. Running this again on an up-to-date
        database changes nothing. Other tables in the file are never touched.

        Parameters
        ----------
        connection : `sqlite3.Connection`
            A connection with a write transaction begun.

        Raises
        ------
        StorageError
            If the database was written by a newer version of this code.
        """
        version = QualityStore._read_schema_version(connection)
        if version > SCHEMA_VERSION:
            raise StorageError(
                "The quality database is newer than this software understands.",
                details={"found": version, "supported": SCHEMA_VERSION},
                retryable=False,
            )
        if version < 1:
            for statement in _CREATE_SCHEMA_STATEMENTS:
                connection.execute(statement)
        elif version < 2:
            QualityStore._migrate_from_version_1(connection)
        if version < 3:
            # Version 3 only adds a table. For a new file (version 0) the
            # statements above did not include it, so it is created here too.
            for statement in _CREATE_CALIBRATION_IDENTITY_STATEMENTS:
                connection.execute(statement)
        if 1 <= version < 4:
            # A new file (version 0) has no rows to merge.
            QualityStore._migrate_from_version_3(connection)
        if 1 <= version < 5:
            # A new file (version 0) has no rows to rename.
            QualityStore._migrate_from_version_4(connection)
        has_version_table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'quality_schema_version'"
        ).fetchone()
        if has_version_table is None:
            connection.execute("CREATE TABLE quality_schema_version (version INTEGER NOT NULL)")
        connection.execute("DELETE FROM quality_schema_version")
        connection.execute("INSERT INTO quality_schema_version (version) VALUES (?)", (SCHEMA_VERSION,))

    @staticmethod
    def _migrate_from_version_1(connection: sqlite3.Connection) -> None:
        """Add the ``variant`` column to a version-1 database.

        Rows written before the column existed keep their subject ids. A row
        whose subject id holds ``/_subsets/<label>/`` is a subset stack, and
        gets that label as its variant, so a reader can filter on the column
        for old rows too.

        Parameters
        ----------
        connection : `sqlite3.Connection`
            A connection with a write transaction begun.
        """
        columns = {row[1] for row in connection.execute("PRAGMA table_info(quality_measurements)")}
        if "variant" not in columns:
            connection.execute("ALTER TABLE quality_measurements ADD COLUMN variant TEXT")
        subject_ids = [
            row[0]
            for row in connection.execute(
                "SELECT DISTINCT subject_id FROM quality_measurements WHERE variant IS NULL "
                "AND instr(subject_id, ?) > 0",
                (_LEGACY_SUBSET_MARKER,),
            )
        ]
        for subject_id in subject_ids:
            label = subject_id.split(_LEGACY_SUBSET_MARKER, 1)[1].split("/", 1)[0]
            if label:
                connection.execute(
                    "UPDATE quality_measurements SET variant = ? WHERE subject_id = ?", (label, subject_id)
                )
        connection.execute("DROP VIEW IF EXISTS quality_measurements_latest")
        connection.execute(_CREATE_LATEST_VIEW_STATEMENT)

    @staticmethod
    def _migrate_from_version_3(connection: sqlite3.Connection) -> None:
        """Merge capture settings that differ only by pixel-scale precision.

        Frame headers write one pixel scale with different precision, and
        each distinct value used to be its own setting row. This rounds every
        stored scale with `round_pixel_scale` (the rounding `CaptureSetting`
        uses) and merges the rows that then match on every field (NULL
        matches NULL). The row with the smallest id is kept.

        The steps run in this order, so the unique index
        ``quality_capture_setting_unique`` is never broken:

        1. Group the rows by their fields with the scale rounded.
        2. In each group, point the measurements of the other rows at the
           kept row, then delete the other rows.
        3. Write the rounded scale into the kept row. Every row that could
           have matched it is gone by now.

        A second run finds nothing to change. Settings that differ in any
        other field are left alone. Only ``quality_measurements`` refers to a
        setting id; ``quality_calibration_identity`` stores camera and optic
        as text.

        Parameters
        ----------
        connection : `sqlite3.Connection`
            A connection with a write transaction begun.
        """
        rows = connection.execute(
            f"SELECT capture_setting_id, {', '.join(_CAPTURE_SETTING_FIELDS)} "  # ruff: ignore[hardcoded-sql-expression]
            "FROM quality_capture_setting ORDER BY capture_setting_id"
        ).fetchall()
        scale_position = 1 + _CAPTURE_SETTING_FIELDS.index("pixel_scale_arcsec")
        groups: dict[tuple, list[tuple[int, float | None]]] = {}
        for row in rows:
            rounded_scale = round_pixel_scale(row[scale_position])
            key = (*row[1:scale_position], rounded_scale, *row[scale_position + 1 :])
            groups.setdefault(key, []).append((row[0], row[scale_position]))
        for members in groups.values():
            kept_id, kept_scale = members[0]
            for duplicate_id, _ in members[1:]:
                connection.execute(
                    "UPDATE quality_measurements SET capture_setting_id = ? WHERE capture_setting_id = ?",
                    (kept_id, duplicate_id),
                )
                connection.execute(
                    "DELETE FROM quality_capture_setting WHERE capture_setting_id = ?", (duplicate_id,)
                )
            rounded_scale = round_pixel_scale(kept_scale)
            if rounded_scale != kept_scale:
                connection.execute(
                    "UPDATE quality_capture_setting SET pixel_scale_arcsec = ? WHERE capture_setting_id = ?",
                    (rounded_scale, kept_id),
                )

    @staticmethod
    def _migrate_from_version_4(connection: sqlite3.Connection) -> None:
        """Rename the stored odd-even difference metric names.

        The measurement was first called the half difference, so its rows
        carry metric names that start with ``stacking.half`` followed by
        ``_difference_``. This replaces that start with
        ``stacking.odd_even_difference_`` and keeps the rest of each name.
        Only the ``metric`` column of ``quality_measurements`` stores a
        metric name; the view ``quality_measurements_latest`` reads from it.
        Row ids, values, times and every other column stay as they are. A
        second run finds no row with the old start and changes nothing.

        Parameters
        ----------
        connection : `sqlite3.Connection`
            A connection with a write transaction begun.
        """
        old_prefix = "stacking.half" + "_difference_"
        new_prefix = "stacking.odd_even_difference_"
        connection.execute(
            "UPDATE quality_measurements SET metric = ? || substr(metric, ?) WHERE substr(metric, 1, ?) = ?",
            (new_prefix, len(old_prefix) + 1, len(old_prefix), old_prefix),
        )

    def get_or_create_capture_setting(self, setting: CaptureSetting) -> int:
        """Return the id of a capture setting, adding it if it is new.

        Parameters
        ----------
        setting : `CaptureSetting`
            The settings to look up or store.

        Returns
        -------
        capture_setting_id : `int`
            The same id every time for the same settings.
        """
        values = [getattr(setting, name) for name in _CAPTURE_SETTING_FIELDS]
        with self._write_transaction() as connection:
            connection.execute(_INSERT_CAPTURE_SETTING_SQL, values)
            row = connection.execute(_SELECT_CAPTURE_SETTING_ID_SQL, values).fetchone()
        return int(row[0])

    def get_capture_setting(self, capture_setting_id: int) -> CaptureSetting | None:
        """Read one capture setting.

        Parameters
        ----------
        capture_setting_id : `int`
            The id returned by `get_or_create_capture_setting`.

        Returns
        -------
        setting : `CaptureSetting` or `None`
            The settings, or `None` if the id is unknown.
        """
        with self._read_connection() as connection:
            if connection is None:
                return None
            row = connection.execute(
                "SELECT * FROM quality_capture_setting WHERE capture_setting_id = ?",
                (capture_setting_id,),
            ).fetchone()
        if row is None:
            return None
        return CaptureSetting(**{name: row[name] for name in _CAPTURE_SETTING_FIELDS})

    def get_capture_settings(
        self, capture_setting_ids: Sequence[int] | None = None
    ) -> dict[int, CaptureSetting]:
        """Read several capture settings in one query.

        Parameters
        ----------
        capture_setting_ids : `Sequence` of `int`, optional
            The ids to read. ``None`` reads every stored setting.

        Returns
        -------
        settings : `dict` [`int`, `CaptureSetting`]
            The settings by id. An unknown id is left out. A library with no
            quality database gives an empty dictionary, and nothing is created.
        """
        if capture_setting_ids is not None and not capture_setting_ids:
            return {}
        with self._read_connection() as connection:
            if connection is None:
                return {}
            if capture_setting_ids is None:
                rows = connection.execute("SELECT * FROM quality_capture_setting").fetchall()
            else:
                # One fixed statement: SQLite unpacks the JSON list into rows,
                # so there is no limit on the number of ids and no SQL text is
                # built.
                rows = connection.execute(
                    "SELECT * FROM quality_capture_setting "
                    "WHERE capture_setting_id IN (SELECT value FROM json_each(?))",
                    (json.dumps([int(identifier) for identifier in capture_setting_ids]),),
                ).fetchall()
        return {
            int(row["capture_setting_id"]): CaptureSetting(**{
                name: row[name] for name in _CAPTURE_SETTING_FIELDS
            })
            for row in rows
        }

    def write_calibration_identities(self, identities: CalibrationIdentities) -> bool:
        """Store the calibration one library stack used.

        The stacking stage calls this once per library stack, with the run id
        and stack subject id the stack's measurements carry. A second call
        for the same run id and subject id is ignored, so a repeated run adds
        nothing and never replaces a stored record.

        Parameters
        ----------
        identities : `CalibrationIdentities`
            The record to store. Its ``recorded_at`` is ignored; the store
            stamps the time itself.

        Returns
        -------
        is_new : `bool`
            `True` if a row was added, `False` if the run and subject were
            already stored.

        Notes
        -----
        This creates the file and tables if they are missing, and migrates an
        older file (run ``scripts/backup_library_databases.py`` first when the
        file is a real library).
        """

        def encode(masters: Sequence[CalibrationFileIdentity]) -> str:
            """Encode a list of file identities as compact JSON text.

            Returns
            -------
            text : `str`
                ``[[path, size_bytes, modified_ns], ...]``.
            """
            return json.dumps(
                [[master.path, master.size_bytes, master.modified_ns] for master in masters],
                separators=(",", ":"),
            )

        values = (
            identities.run_id,
            identities.subject_id,
            identities.target_id,
            identities.camera,
            identities.optic,
            identities.focal_length_mm,
            encode(identities.flat_masters),
            encode(identities.bias_masters),
            encode(identities.dark_masters),
            identities.siril_version,
            identities.stacking_algorithm_version,
            _format_time(datetime.now(UTC)),
        )
        with self._write_transaction() as connection:
            cursor = connection.execute(_INSERT_CALIBRATION_IDENTITY_SQL, values)
            return cursor.rowcount > 0

    def get_calibration_identities(
        self, run_ids: Sequence[str] | None = None, target_id: str | None = None
    ) -> list[CalibrationIdentities]:
        """Read the stored calibration records.

        Parameters
        ----------
        run_ids : `Sequence` of `str`, optional
            Only these runs. An empty sequence gives an empty list.
        target_id : `str`, optional
            Only the stacks of this target.

        Returns
        -------
        records : `list` [`CalibrationIdentities`]
            The records, oldest first. A library with no quality database, or
            one older than schema version 3, gives an empty list and nothing
            is created or migrated.
        """
        if run_ids is not None and not run_ids:
            return []
        with self._read_connection() as connection:
            if connection is None or self._read_schema_version(connection) < 3:
                return []
            clauses = []
            parameters: list[Any] = []
            if run_ids is not None:
                clauses.append("run_id IN (SELECT value FROM json_each(?))")
                parameters.append(json.dumps([str(run_id) for run_id in run_ids]))
            if target_id is not None:
                clauses.append("target_id = ?")
                parameters.append(target_id)
            where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
            # The WHERE text is built only from the fixed clauses above.
            rows = connection.execute(
                "SELECT * FROM quality_calibration_identity"  # ruff: ignore[hardcoded-sql-expression]
                + where
                + " ORDER BY recorded_at, run_id, subject_id",
                parameters,
            ).fetchall()
        return [
            CalibrationIdentities(
                run_id=row["run_id"],
                subject_id=row["subject_id"],
                target_id=row["target_id"],
                camera=row["camera"],
                optic=row["optic"],
                focal_length_mm=row["focal_length_mm"],
                flat_masters=_decode_masters(row["flat_masters"]),
                bias_masters=_decode_masters(row["bias_masters"]),
                dark_masters=_decode_masters(row["dark_masters"]),
                siril_version=row["siril_version"],
                stacking_algorithm_version=row["stacking_algorithm_version"],
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    def write_measurements(self, measurements: Sequence[QualityMeasurement]) -> int:
        """Append the measurements of one run in a single transaction.

        A measurement that duplicates a stored one (same run, metric, subject
        level, subject id and channel) is ignored. Stored rows are never
        replaced.

        Parameters
        ----------
        measurements : `Sequence` of `QualityMeasurement`
            The measurements to store.

        Returns
        -------
        new_count : `int`
            How many rows were new.

        Notes
        -----
        A database error is raised as `StorageError` and nothing is stored:
        the batch commits whole or not at all. This creates the file and
        the quality tables if they are missing.
        """
        if not measurements:
            return 0
        new_count = 0
        with self._write_transaction() as connection:
            for measurement in measurements:
                cursor = connection.execute(_INSERT_MEASUREMENT_SQL, _measurement_to_values(measurement))
                new_count += max(cursor.rowcount, 0)
        return new_count

    def query(
        self,
        metric: str | None = None,
        level: SubjectLevel | None = None,
        target_id: str | None = None,
        observing_night_id: str | None = None,
        camera: str | None = None,
        telescope: str | None = None,
        since: datetime | None = None,
        row_ids: Sequence[int] | None = None,
        latest_only: bool = True,
        variant: str | None = None,
        library_only: bool = False,
    ) -> list[QualityMeasurement]:
        """Read stored measurements.

        Parameters
        ----------
        metric : `str`, optional
            Only this metric.
        level : `SubjectLevel`, optional
            Only subjects of this level.
        target_id : `str`, optional
            Only this target.
        observing_night_id : `str`, optional
            Only this observing night.
        camera : `str`, optional
            Only measurements whose capture setting has this camera.
        telescope : `str`, optional
            Only measurements whose capture setting has this telescope.
        since : `datetime`, optional
            Only measurements made at or after this time (with a time zone).
        row_ids : `Sequence` of `int`, optional
            Only these rows. When given, exactly these rows are returned
            even if newer ones exist, so a citation always finds what it
            cited; `latest_only` is then ignored.
        latest_only : `bool`, optional
            Read the newest row for each subject, metric and channel
            (the latest view) instead of every row. Defaults to `True`.
        variant : `str`, optional
            Only the rows of the subset stack with this label.
        library_only : `bool`, optional
            Only rows that are not about a subset stack (their variant is
            empty). The default, `False`, gives library and subset rows
            alike, so a caller that wants one kind asks for it by field.

        Returns
        -------
        measurements : `list` of `QualityMeasurement`
            Matching measurements, oldest first, with `row_id` filled. Empty
            if the file or the quality tables do not exist yet.

        Raises
        ------
        InvalidArgumentError
            If both ``variant`` and ``library_only`` are given.

        Notes
        -----
        A `StorageError` is raised if the file is unreadable, is newer than
        this code, or a stored row fails the model's checks. A
        version-1 file has no variant column: it holds no subset rows the
        store can name, so ``library_only`` reads everything and ``variant``
        reads nothing.
        """
        if variant is not None and library_only:
            raise InvalidArgumentError("Ask for a subset label or for library rows only, not both.")
        source = "quality_measurements_latest" if latest_only and row_ids is None else "quality_measurements"
        conditions: list[str] = []
        parameters: list[Any] = []
        for column, wanted in (
            ("m.metric", metric),
            ("m.subject_level", None if level is None else SubjectLevel(level).value),
            ("m.target_id", target_id),
            ("m.observing_night_id", observing_night_id),
            ("cs.camera", camera),
            ("cs.telescope", telescope),
        ):
            if wanted is not None:
                conditions.append(f"{column} = ?")
                parameters.append(wanted)
        if since is not None:
            conditions.append("m.measured_at >= ?")
            parameters.append(_format_time(since))
        if row_ids is not None:
            conditions.append(f"m.row_id IN ({', '.join('?' * len(row_ids))})")
            parameters.extend(row_ids)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._read_connection() as connection:
            if connection is None:
                return []
            has_variant_column = self._read_schema_version(connection) >= 2
            if (variant is not None or library_only) and has_variant_column:
                variant_condition = "m.variant = ?" if variant is not None else "m.variant IS NULL"
                where = f"{where} AND {variant_condition}" if where else f"WHERE {variant_condition}"
                if variant is not None:
                    parameters.append(variant)
            elif variant is not None:
                return []
            # `source` and the conditions are fixed strings chosen above; every
            # caller value travels as a bound parameter.
            sql = (
                f"SELECT m.* FROM {source} AS m "  # ruff: ignore[hardcoded-sql-expression]
                "LEFT JOIN quality_capture_setting AS cs "
                "ON cs.capture_setting_id = m.capture_setting_id "
                f"{where} ORDER BY m.measured_at, m.row_id"
            )
            rows = connection.execute(sql, parameters).fetchall()
        return [_row_to_measurement(row) for row in rows]
