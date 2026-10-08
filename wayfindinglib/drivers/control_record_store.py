"""Purpose: Keep each night's alignment, guiding and polar alignment records.

Description: `ControlRecordStore` reads and writes three tables in
wayfindinglib's own database, ``wayfinding.db``, which `DiskButler` places
in wayfindinglib's library folder:

* ``alignment_attempts``: one row per plate-solve sync or centering round,
  with the pointing error it measured.
* ``guiding_samples``: one row per guide-camera frame: the star's drift,
  the guide pulses sent, the signal-to-noise ratio, and where the sample
  came from (``source``, see `GuidingSampleSource`).
* ``polar_alignments``: one row per polar alignment assistant run.

These records are high-volume and are read back by time range, so they
live in plain SQL tables rather than in the Butler's one-JSON-row-per-record
tables. `DiskButler.control_records` gives the store for its database.

A row's ``session_id`` names the observing night it belongs to. A record
saved without one is filed under the night its timestamp falls in (see
`astrometricslib.observing_night_id`). A night's date is the local date
on which it began, so a night that crosses midnight is one session. The
SQL below computes the same date with ``timestamp - 43200`` (twelve hours
earlier) in local time.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import (
    InvalidArgumentError,
    StorageError,
    connect_db,
    observing_night_id,
    parse_coordinate_string,
)

if TYPE_CHECKING:
    from astrometricslib import AppConfiguration, Target

__all__ = ["ControlRecordStore"]

logger = logging.getLogger(__name__)

_NIGHT_OF_TIMESTAMP = "strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime')"
"""SQL that names the observing night a row's ``timestamp`` falls in."""

_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS alignment_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        target_name TEXT,
        timestamp REAL NOT NULL,
        status TEXT NOT NULL,
        delta_ra_arcsec REAL,
        delta_dec_arcsec REAL,
        pointing_error_arcsec REAL,
        mount_ra REAL,
        mount_dec REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_alignment_attempts_time ON alignment_attempts(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_alignment_attempts_target ON alignment_attempts(target_name)",
    """
    CREATE TABLE IF NOT EXISTS guiding_samples (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        target_name TEXT,
        timestamp REAL NOT NULL,
        dra REAL NOT NULL,
        ddec REAL NOT NULL,
        pulse_ra REAL DEFAULT 0.0,
        pulse_dec REAL DEFAULT 0.0,
        snr REAL,
        rms_ra REAL,
        rms_dec REAL,
        star_mass REAL,
        source TEXT NOT NULL DEFAULT 'unverified'
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_guiding_samples_time ON guiding_samples(timestamp)",
    """
    CREATE TABLE IF NOT EXISTS polar_alignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT,
        timestamp REAL NOT NULL,
        status TEXT NOT NULL,
        total_error_arcsec REAL,
        alt_error_arcsec REAL,
        az_error_arcsec REAL,
        pole_ra REAL,
        pole_dec REAL,
        paa_points_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_polar_alignments_time ON polar_alignments(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_polar_alignments_session ON polar_alignments(session_id)",
)
"""The tables and indexes the store creates when they are missing."""

_INSERT_GUIDING_SAMPLE_SQL = """
    INSERT INTO guiding_samples (
        session_id, target_name, timestamp, dra, ddec,
        pulse_ra, pulse_dec, snr, rms_ra, rms_dec, star_mass, source
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

SPURIOUS_ALIGNMENT_ERROR_ARCSEC = 0.5
"""A sync with a smaller pointing error is a tracking-loop echo, not a solve.

Some mount drivers report each tracking update as a sync. Those updates
move the mount by a tiny amount and are not plate solves, so they are not
stored unless the caller forces it.
"""


class ControlRecordStore:
    """Read and write the alignment, guiding and polar alignment records.

    Each method opens and closes its own connection, so one store may be
    shared between threads.

    Parameters
    ----------
    db_path : `str`
        Path of the ``wayfinding.db`` file. `DiskButler.control_records`
        passes the file in wayfindinglib's library folder.

    Attributes
    ----------
    db_path : `str`
        Path of the database file.
    """

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        connection = self._connect()
        try:
            for statement in _SCHEMA:
                connection.execute(statement)
            connection.commit()
        except sqlite3.Error as schema_error:
            raise StorageError(f"Could not set up the control records in {db_path}.") from schema_error
        finally:
            connection.close()

    @classmethod
    def for_configuration(cls, app_config: AppConfiguration) -> ControlRecordStore:
        """Give the store in the ``wayfinding.db`` a configuration names.

        This is how a program outside the library, such as the backend,
        reaches the records the `Wayfinder` keeps: the file is found the
        same way `DiskButler` finds it.

        Parameters
        ----------
        app_config : `astrometricslib.AppConfiguration`
            The application configuration.

        Returns
        -------
        records : `ControlRecordStore`
            The store.
        """
        from wayfindinglib.drivers.butler import DiskButler

        return DiskButler(app_config=app_config).control_records

    def _connect(self) -> sqlite3.Connection:
        """Open a connection with write-ahead logging and named-column rows.

        Returns
        -------
        connection : `sqlite3.Connection`
            The open connection. The caller closes it.

        Raises
        ------
        StorageError
            Raised if the database file cannot be opened.
        """
        try:
            return connect_db(self.db_path)
        except (sqlite3.Error, OSError) as open_error:
            raise StorageError(f"Could not open the control records in {self.db_path}.") from open_error

    # -- Alignment attempts ------------------------------------------------

    def record_alignment_attempt(self, attempt: dict[str, Any]) -> None:
        """Store one plate-solve sync or centering round.

        A sync whose pointing error is below `SPURIOUS_ALIGNMENT_ERROR_ARCSEC`
        is a tracking-loop echo and is skipped, unless ``force_record`` is
        set. A record that cannot be stored is logged and dropped, so a
        database problem never stops the hardware loop that reported it.

        Parameters
        ----------
        attempt : `dict` [`str`, `Any`]
            The attempt: ``timestamp`` (or ``time``), ``status``,
            ``delta_ra_arcsec``, ``delta_dec_arcsec``,
            ``pointing_error_arcsec`` (worked out from the two offsets when
            missing), ``target_name``, ``session_id``, and the mount
            position as ``ra`` and ``dec`` in degrees. Optional
            ``force_record`` stores even a tiny error.
        """
        d_ra = attempt.get("delta_ra_arcsec")
        d_dec = attempt.get("delta_dec_arcsec")
        pointing_error = attempt.get("pointing_error_arcsec")
        if pointing_error is None and d_ra is not None and d_dec is not None:
            pointing_error = math.hypot(d_ra, d_dec)
        if (
            pointing_error is not None
            and pointing_error < SPURIOUS_ALIGNMENT_ERROR_ARCSEC
            and not attempt.get("force_record")
        ):
            logger.debug("Ignoring a tracking-loop echo of %.3f arcsec.", pointing_error)
            return
        timestamp = attempt.get("timestamp", attempt.get("time", datetime.now().timestamp()))
        connection = None
        try:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO alignment_attempts (
                    session_id, target_name, timestamp, status,
                    delta_ra_arcsec, delta_dec_arcsec, pointing_error_arcsec,
                    mount_ra, mount_dec
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attempt.get("session_id") or observing_night_id(timestamp),
                    attempt.get("target_name"),
                    timestamp,
                    attempt.get("status", "unknown"),
                    d_ra,
                    d_dec,
                    pointing_error,
                    attempt.get("ra", attempt.get("mount_ra")),
                    attempt.get("dec", attempt.get("mount_dec")),
                ),
            )
            connection.commit()
        except sqlite3.Error, StorageError:
            logger.exception("Could not store an alignment attempt.")
        finally:
            if connection is not None:
                connection.close()

    def get_alignment_attempts(
        self, target_name: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Read the most recent alignment attempts, newest first.

        Parameters
        ----------
        target_name : `str`, optional
            Only attempts on this target.
        limit : `int`, optional
            Most rows to return. Defaults to 100.

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            One dictionary per row, keyed by column name. Empty if the
            records cannot be read.
        """
        return self._read(
            "SELECT * FROM alignment_attempts WHERE (? IS NULL OR target_name = ?) "
            "ORDER BY timestamp DESC LIMIT ?",
            (target_name, target_name, limit),
        )

    def get_session_alignment_attempts(
        self, session_id: str | None = None, limit: int = 10000
    ) -> list[dict[str, Any]]:
        """Read the alignment attempts of one night, or of every night.

        Parameters
        ----------
        session_id : `str`, optional
            The night's session id or date (``YYYY-MM-DD``). `None`, ``"all"``
            or ``"*"`` reads every night.
        limit : `int`, optional
            Most rows to return when reading every night. Defaults to 10000.

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            The attempts, oldest first.
        """
        if session_id in ("all", "*", None):
            return self._read("SELECT * FROM alignment_attempts ORDER BY timestamp ASC LIMIT ?", (limit,))
        return self._read(
            f"SELECT * FROM alignment_attempts WHERE session_id = ? OR {_NIGHT_OF_TIMESTAMP} = ? "
            "ORDER BY timestamp ASC",
            (session_id, session_id),
        )

    def get_alignment_sessions(self) -> list[dict[str, Any]]:
        """Summarize the nights that recorded alignment or guiding data.

        Returns
        -------
        sessions : `list` [`dict` [`str`, `Any`]]
            The 50 most recent nights, newest first. Each has
            ``session_id``, ``session_date``, ``sync_count``,
            ``guiding_count``, ``start_time``, ``end_time``,
            ``avg_error_arcsec``, and the night's latest polar alignment
            errors (``polar_error_arcsec``, ``polar_alt_error_arcsec``,
            ``polar_az_error_arcsec``, each `None` without a run).
        """
        sessions = self._read(
            f"""
            WITH combined AS (
                SELECT COALESCE(session_id, {_NIGHT_OF_TIMESTAMP}) AS session_id,
                       {_NIGHT_OF_TIMESTAMP} AS session_date, timestamp, pointing_error_arcsec,
                       1 AS is_sync, 0 AS is_guiding
                FROM alignment_attempts
                UNION ALL
                SELECT COALESCE(session_id, {_NIGHT_OF_TIMESTAMP}) AS session_id,
                       {_NIGHT_OF_TIMESTAMP} AS session_date, timestamp, NULL AS pointing_error_arcsec,
                       0 AS is_sync, 1 AS is_guiding
                FROM guiding_samples
            )
            SELECT session_id, session_date, SUM(is_sync) AS sync_count, SUM(is_guiding) AS guiding_count,
                   MIN(timestamp) AS start_time, MAX(timestamp) AS end_time,
                   ROUND(AVG(pointing_error_arcsec), 2) AS avg_error_arcsec
            FROM combined
            GROUP BY session_id, session_date
            ORDER BY MAX(timestamp) DESC
            LIMIT 50
            """,
            (),
        )
        for session in sessions:
            polar = self._read(
                "SELECT total_error_arcsec, alt_error_arcsec, az_error_arcsec FROM polar_alignments "
                f"WHERE session_id = ? OR {_NIGHT_OF_TIMESTAMP} = ? ORDER BY timestamp DESC LIMIT 1",
                (session["session_id"], session["session_date"]),
            )
            latest = polar[0] if polar else {}
            session["polar_error_arcsec"] = latest.get("total_error_arcsec")
            session["polar_alt_error_arcsec"] = latest.get("alt_error_arcsec")
            session["polar_az_error_arcsec"] = latest.get("az_error_arcsec")
        return sessions

    def get_session_target_telemetry(
        self, session_id: str | None, targets: Iterable[Target]
    ) -> list[dict[str, Any]]:
        """Build tracking attempts for the targets imaged during a night.

        For each target with frames taken that night, the night's guiding
        samples near the frames become attempts that carry the guiding
        drift as the offset. A target imaged without guiding samples gets
        one zero-offset attempt per frame, so the night view still shows
        when it was tracked.

        Parameters
        ----------
        session_id : `str` or `None`
            The night's date (``YYYY-MM-DD``). `None`, ``"all"`` or ``"*"``
            uses every night.
        targets : `~collections.abc.Iterable` [`astrometricslib.Target`]
            The targets to look at, usually every target in the library.

        Returns
        -------
        attempts : `list` [`dict` [`str`, `Any`]]
            Attempt dictionaries with camelCase keys, as
            `AlignmentAttempt` reads them.
        """
        every_night = session_id in ("all", "*", None)
        if every_night:
            guiding_rows = self._read(
                "SELECT timestamp, dra, ddec, rms_ra, rms_dec, target_name FROM guiding_samples "
                "ORDER BY timestamp ASC",
                (),
            )
        else:
            guiding_rows = self._read(
                "SELECT timestamp, dra, ddec, rms_ra, rms_dec, target_name FROM guiding_samples "
                f"WHERE session_id = ? OR {_NIGHT_OF_TIMESTAMP} = ? ORDER BY timestamp ASC",
                (session_id, session_id),
            )

        attempts: list[dict[str, Any]] = []
        for target in targets:
            frame_times = [
                frame.timestamp
                for frame in target.frames
                if frame.timestamp
                and (
                    every_night
                    or datetime.fromtimestamp(frame.timestamp - 43200).strftime("%Y-%m-%d") == session_id
                )
            ]
            if not frame_times:
                continue
            try:
                target_ra_deg = parse_coordinate_string(target.ra, is_ra=True) if target.ra else 0.0
                target_dec_deg = parse_coordinate_string(target.dec, is_ra=False) if target.dec else 0.0
            except InvalidArgumentError:
                continue
            # Coordinates that could not be read are stored as (0, 0).
            if not (target_ra_deg or target_dec_deg):
                continue
            first, last = min(frame_times), max(frame_times)
            matched = [
                sample
                for sample in guiding_rows
                if sample.get("target_name") == target.id or first - 60 <= sample["timestamp"] <= last + 60
            ]
            if matched:
                for sample in matched:
                    dra = float(sample.get("dra") or 0.0)
                    ddec = float(sample.get("ddec") or 0.0)
                    attempts.append(
                        _tracking_attempt(
                            target.id, target_ra_deg, target_dec_deg, sample["timestamp"], dra, ddec
                        )
                    )
            else:
                attempts.extend(
                    _tracking_attempt(target.id, target_ra_deg, target_dec_deg, time, 0.0, 0.0)
                    for time in frame_times
                )
        return attempts

    # -- Guiding samples ---------------------------------------------------

    def record_guiding_samples(self, samples: list[dict[str, Any]]) -> None:
        """Store guiding samples.

        A batch that cannot be stored is logged and dropped, so a database
        problem never stops the guiding loop that reported it.

        Parameters
        ----------
        samples : `list` [`dict` [`str`, `Any`]]
            Samples with ``timestamp`` (or ``time``), ``dra``, ``ddec``,
            ``pulse_ra``, ``pulse_dec``, ``snr``, ``rms_ra``, ``rms_dec``,
            ``star_mass``, ``target_name``, ``session_id`` and ``source``.
            A sample with no ``source`` is stored as ``"unverified"``;
            callers should say where each sample came from, so that
            estimates are never mistaken for measurements.
        """
        if not samples:
            return
        connection = None
        try:
            connection = self._connect()
            connection.executemany(_INSERT_GUIDING_SAMPLE_SQL, _guiding_sample_rows(samples))
            connection.commit()
        except sqlite3.Error, StorageError:
            logger.exception("Could not store %d guiding samples.", len(samples))
        finally:
            if connection is not None:
                connection.close()

    def replace_guiding_samples(self, samples: list[dict[str, Any]]) -> int:
        """Store guiding samples, replacing any earlier copy of the same log.

        Reading the same log file twice must not double its samples. This
        deletes every stored sample with the same ``source`` whose time
        falls inside the batch's time span, then inserts the batch, all in
        one transaction. Samples from other sources are untouched, so a
        measurement is never replaced by an estimate covering the same time.

        Parameters
        ----------
        samples : `list` [`dict` [`str`, `Any`]]
            Samples, as for `record_guiding_samples`. Every sample must
            carry the same ``source`` and a timestamp.

        Returns
        -------
        replaced_count : `int`
            How many earlier samples were removed.

        Raises
        ------
        InvalidArgumentError
            Raised if the samples do not all have one ``source``, or one
            has no timestamp.
        StorageError
            Raised if the database cannot be written. Unlike the
            ``record_*`` methods, this one does not drop the error:
            ingestion reports how many samples it stored, and that count
            must not claim samples that never reached the database.
        """
        if not samples:
            return 0
        sources = {sample.get("source") or "unverified" for sample in samples}
        if len(sources) != 1:
            raise InvalidArgumentError(
                f"replace_guiding_samples needs a single source, got {sorted(sources)}"
            )
        (source,) = sources
        timestamps = [sample.get("timestamp", sample.get("time")) for sample in samples]
        if any(timestamp is None for timestamp in timestamps):
            raise InvalidArgumentError("replace_guiding_samples needs a timestamp on every sample")
        connection = None
        try:
            connection = self._connect()
            cursor = connection.execute(
                "DELETE FROM guiding_samples WHERE source = ? AND timestamp BETWEEN ? AND ?",
                (source, min(timestamps), max(timestamps)),
            )
            replaced_count = cursor.rowcount
            connection.executemany(_INSERT_GUIDING_SAMPLE_SQL, _guiding_sample_rows(samples))
            connection.commit()
        except sqlite3.Error as write_error:
            raise StorageError(f"Could not store {len(samples)} guiding samples.") from write_error
        finally:
            if connection is not None:
                connection.close()
        return replaced_count

    def get_guiding_samples(
        self,
        target_name: str | None = None,
        session_id: str | None = None,
        start_time: float | None = None,
        limit: int = 1000,
        sources: Iterable[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Read stored guiding samples, oldest first.

        Parameters
        ----------
        target_name : `str`, optional
            Only samples on this target.
        session_id : `str`, optional
            Only samples of this night, by session id or date.
        start_time : `float`, optional
            Only samples at or after this time, in seconds since 1970.
        limit : `int`, optional
            Most rows to return. Defaults to 1000.
        sources : `~collections.abc.Iterable` [`str`], optional
            Keep only samples whose ``source`` is one of these. `None`
            keeps every sample, including estimates.

        Returns
        -------
        samples : `list` [`dict` [`str`, `Any`]]
            One dictionary per row, keyed by column name.
        """
        query = "SELECT * FROM guiding_samples WHERE 1=1"
        parameters: list[Any] = []
        if target_name:
            query += " AND target_name = ?"
            parameters.append(target_name)
        if session_id:
            query += f" AND (session_id = ? OR {_NIGHT_OF_TIMESTAMP} = ?)"
            parameters.extend([session_id, session_id])
        if start_time is not None:
            query += " AND timestamp >= ?"
            parameters.append(start_time)
        if sources is not None:
            allowed_sources = list(sources)
            query += f" AND source IN ({','.join('?' * len(allowed_sources))})"
            parameters.extend(allowed_sources)
        query += " ORDER BY timestamp ASC LIMIT ?"
        parameters.append(limit)
        return self._read(query, tuple(parameters))

    # -- Polar alignment ---------------------------------------------------

    def record_polar_alignment(self, data: dict[str, Any]) -> None:
        """Store one polar alignment assistant run.

        A run that cannot be stored is logged and dropped.

        Parameters
        ----------
        data : `dict` [`str`, `Any`]
            The run: ``session_id``, ``timestamp``, ``status``,
            ``total_error_arcsec``, ``alt_error_arcsec``,
            ``az_error_arcsec``, ``pole_ra``, ``pole_dec`` and
            ``paa_points``.
        """
        points = data.get("paa_points") or []
        connection = None
        try:
            connection = self._connect()
            connection.execute(
                """
                INSERT INTO polar_alignments (
                    session_id, timestamp, status, total_error_arcsec, alt_error_arcsec,
                    az_error_arcsec, pole_ra, pole_dec, paa_points_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    data.get("session_id"),
                    data.get("timestamp", datetime.now().timestamp()),
                    data.get("status", "aligned"),
                    data.get("total_error_arcsec"),
                    data.get("alt_error_arcsec"),
                    data.get("az_error_arcsec"),
                    data.get("pole_ra"),
                    data.get("pole_dec"),
                    json.dumps(points) if points else None,
                ),
            )
            connection.commit()
        except sqlite3.Error, StorageError:
            logger.exception("Could not store a polar alignment run.")
        finally:
            if connection is not None:
                connection.close()

    def get_polar_alignments(self, session_id: str | None = None, limit: int = 10) -> list[dict[str, Any]]:
        """Read polar alignment runs, newest first.

        Parameters
        ----------
        session_id : `str`, optional
            Only runs of this session.
        limit : `int`, optional
            Most runs to return. Defaults to 10.

        Returns
        -------
        runs : `list` [`dict` [`str`, `Any`]]
            One dictionary per run, with the stored columns and the
            measured points decoded into ``paa_points``.
        """
        rows = self._read(
            "SELECT * FROM polar_alignments WHERE (? IS NULL OR session_id = ?) "
            "ORDER BY timestamp DESC LIMIT ?",
            (session_id, session_id, limit),
        )
        for row in rows:
            try:
                row["paa_points"] = json.loads(row["paa_points_json"]) if row.get("paa_points_json") else []
            except ValueError:
                row["paa_points"] = []
        return rows

    # -- Shared ------------------------------------------------------------

    def _read(self, query: str, parameters: tuple[Any, ...]) -> list[dict[str, Any]]:
        """Run a read query and return its rows as dictionaries.

        A query that fails is logged, and no rows are returned, so a view
        of past nights shows nothing rather than failing.

        Returns
        -------
        rows : `list` [`dict` [`str`, `Any`]]
            The rows, keyed by column name.
        """
        connection = None
        try:
            connection = self._connect()
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]
        except sqlite3.Error, StorageError:
            logger.exception("Could not read the control records.")
            return []
        finally:
            if connection is not None:
                connection.close()


def _guiding_sample_rows(samples: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    """Turn guiding sample dictionaries into database rows.

    Returns
    -------
    rows : `list` [`tuple`]
        One tuple per sample, in the column order of
        `_INSERT_GUIDING_SAMPLE_SQL`.
    """
    rows = []
    for sample in samples:
        timestamp = sample.get("timestamp", sample.get("time", datetime.now().timestamp()))
        rows.append((
            sample.get("session_id") or observing_night_id(timestamp),
            sample.get("target_name"),
            timestamp,
            sample.get("dra", 0.0),
            sample.get("ddec", 0.0),
            sample.get("pulse_ra", 0.0),
            sample.get("pulse_dec", 0.0),
            sample.get("snr"),
            sample.get("rms_ra"),
            sample.get("rms_dec"),
            sample.get("star_mass"),
            sample.get("source") or "unverified",
        ))
    return rows


def _tracking_attempt(
    target_id: str, ra_deg: float, dec_deg: float, timestamp: float, dra: float, ddec: float
) -> dict[str, Any]:
    """Build one attempt for `ControlRecordStore.get_session_target_telemetry`.

    Returns
    -------
    attempt : `dict` [`str`, `Any`]
        The attempt, with camelCase keys.
    """
    return {
        "status": "aligned",
        "deltaRaArcsec": dra,
        "deltaDecArcsec": ddec,
        "ra": ra_deg,
        "dec": dec_deg,
        "pointingErrorArcsec": math.hypot(dra, ddec),
        "timestamp": timestamp,
        "targetName": target_id,
    }
