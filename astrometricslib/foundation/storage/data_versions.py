"""Purpose: Count the writes made to the science library, in its database.

Description: A cache that keeps a slow answer needs a cheap way to ask "has
anything this answer was built from changed?". This module keeps one small
counter for each kind of library data in a table named ``data_versions`` in
the same database file as the data. Every code path that writes the data adds
one to its counter inside the same database transaction as the write, so the
counter and the data change together or not at all. Reading the counters
takes one tiny query.

Because the counters live in the file and not in memory, they also change
when another program (a maintenance script, a second copy of the app) writes
to the library, and they survive a restart.

The kinds are `TARGETS_KIND` (the target documents, which hold the frame
records and the stack records), `QUALITY_KIND` (the quality measurements,
including the rows that record set-aside frames) and `STARS_KIND` (the star
catalog).

A write made by raw SQL that does not call `bump_data_version` is not seen.
So is a database file swapped for an older copy. Restore a backup only while
the app is stopped.
"""

import hashlib
import logging
import os
import sqlite3
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from astrometricslib.foundation.config import AppConfiguration

logger = logging.getLogger(__name__)

TARGETS_KIND = "targets"
"""Counter for the target documents (they hold frames and stacks)."""

QUALITY_KIND = "quality"
"""Counter for the quality measurements, including set-aside frame rows."""

STARS_KIND = "stars"
"""Counter for the star catalog."""

ALL_KINDS = (QUALITY_KIND, STARS_KIND, TARGETS_KIND)
"""Every kind of library data that has a counter, in sorted order."""

_CREATE_TABLE_STATEMENT = (
    "CREATE TABLE IF NOT EXISTS data_versions (kind TEXT PRIMARY KEY, version INTEGER NOT NULL)"
)

_BUMP_STATEMENT = (
    "INSERT INTO data_versions (kind, version) VALUES (?, 1) "
    "ON CONFLICT(kind) DO UPDATE SET version = version + 1"
)


def bump_data_version(connection: sqlite3.Connection, kind: str) -> None:
    """Add one to the counter of a kind of data.

    Call this on the connection that makes the write, before the commit, so
    the counter and the data are saved together.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        The connection whose open transaction holds the write.
    kind : `str`
        The kind of data written, such as `TARGETS_KIND`.
    """
    connection.execute(_CREATE_TABLE_STATEMENT)
    connection.execute(_BUMP_STATEMENT, (kind,))


def read_data_versions(database_path: str) -> dict[str, int] | None:
    """Read the counters from a database file without writing to it.

    Parameters
    ----------
    database_path : `str`
        The path of the database file (normally ``astrometrics.db``).

    Returns
    -------
    versions : `dict` [`str`, `int`] or `None`
        The counter of each kind that has been written. A kind that was
        never written is not in the dict, and counts as 0. The dict is empty
        when the file or the table does not exist yet. `None` when the file
        could not be read (for example, it is locked for longer than the
        wait), which means the version is unknown.
    """
    if not os.path.isfile(database_path):
        return {}
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(database_path)}?mode=ro", uri=True, timeout=5.0)
        rows = connection.execute("SELECT kind, version FROM data_versions").fetchall()
        return {str(kind): int(version) for kind, version in rows}
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return {}
        logger.warning("Could not read the data versions from %s.", database_path, exc_info=True)
        return None
    except sqlite3.Error, OSError:
        logger.warning("Could not read the data versions from %s.", database_path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_library_data_version(
    config: AppConfiguration, kinds: tuple[str, ...] | None = None
) -> tuple[tuple[str, int], ...] | None:
    """Give a value that changes whenever the chosen kinds of data change.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder, where
        ``astrometrics.db`` is.
    kinds : `tuple` [`str`, ...], optional
        The kinds the answer depends on. Defaults to every kind.

    Returns
    -------
    version : `tuple` [`tuple` [`str`, `int`], ...] or `None`
        The (kind, counter) pairs in sorted order. Two calls give equal values
        only if none of those kinds were written in between. `None` when the
        counters could not be read; the caller must not keep an answer then.
    """
    try:
        database_path = os.path.join(str(config.get_library_path()), "astrometrics.db")
    except Exception:
        # No library folder means no version, and so no answer is kept.
        logger.warning("Could not find the library folder for the data version.", exc_info=True)
        return None
    versions = read_data_versions(database_path)
    if versions is None:
        return None
    wanted = ALL_KINDS if kinds is None else tuple(sorted(kinds))
    return tuple((kind, versions.get(kind, 0)) for kind in wanted)


def get_target_data_version(config: AppConfiguration, target_id: str) -> tuple[str | int, ...] | None:
    """Give a value that changes whenever one target's own data change.

    The library counters (`get_library_data_version`) change when any target
    is written, so a new frame of one target changes them for every target.
    This version looks at one target only: its stored document (the frame
    records and the stack records), its quality measurements and its
    calibration identities. Two calls give equal values only if none of
    those changed in between. Data of other targets that a result may also
    read (for example the frames of the same camera) are not seen.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder, where
        ``astrometrics.db`` is.
    target_id : `str`
        The target's id as the library stores it.

    Returns
    -------
    version : `tuple` [`str` or `int`, ...] or `None`
        The stored document's size and hash, then the count, newest row
        number and newest time of the target's measurements, then the count
        and newest time of its calibration identities. `None` when the
        database could not be read; the caller must not keep an answer then.
    """
    try:
        database_path = os.path.join(str(config.get_library_path()), "astrometrics.db")
    except Exception:
        # No library folder means no version, and so no answer is kept.
        logger.warning("Could not find the library folder for the target data version.", exc_info=True)
        return None
    if not os.path.isfile(database_path):
        return (0, "", 0, 0, "", 0, "")
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(database_path)}?mode=ro", uri=True, timeout=5.0)
        document = _read_or_default(
            connection, "SELECT data_json FROM targets WHERE id = ?", (target_id,), (None,)
        )
        text = document[0] or ""
        digest = hashlib.blake2b(text.encode("utf-8"), digest_size=16).hexdigest() if text else ""
        measurements = _read_or_default(
            connection,
            "SELECT COUNT(*), COALESCE(MAX(row_id), 0), COALESCE(MAX(measured_at), '') "
            "FROM quality_measurements WHERE target_id = ?",
            (target_id,),
            (0, 0, ""),
        )
        identities = _read_or_default(
            connection,
            "SELECT COUNT(*), COALESCE(MAX(recorded_at), '') FROM quality_calibration_identity "
            "WHERE target_id = ?",
            (target_id,),
            (0, ""),
        )
        return (len(text), digest, *measurements, *identities)
    except sqlite3.Error, OSError:
        logger.warning("Could not read the target data version from %s.", database_path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_camera_data_versions(
    config: AppConfiguration, cameras: Sequence[str], finished_before_night: str | None = None
) -> dict[str, tuple[str | int, ...]] | None:
    """Give a value for each camera that changes whenever its measurements do.

    A result that compares a target with the rest of the library (an
    efficiency baseline, a sky model, a noise model pooled over targets) reads
    the measurements of every target taken with the same camera. The library
    counters change when anything is written, and the target version
    (`get_target_data_version`) sees one target only. This version sees one
    camera: all quality measurements whose capture setting names it, at every
    level (frame and stack), and the calibration records of its stacks. A
    measurement or calibration record of another camera does not change it.

    With ``finished_before_night`` the version counts only the measurements
    of finished nights: a measurement whose observing night is older than
    that night, or that has no night (a stack row). A frame measured tonight
    then leaves the version as it is, and the version changes once when the
    night rolls over (the frames of that night start to count).

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.
    cameras : `Sequence` [`str`]
        The camera names, as the capture settings store them.
    finished_before_night : `str`, optional
        The observing night that is not finished yet, as
        `~astrometricslib.reports.observing_night_id` names it (the night
        of now). Measurements of this night and later ones are not counted.
        Defaults to counting every measurement.

    Returns
    -------
    versions : `dict` [`str`, `tuple`] or `None`
        For each camera: the count, the newest row number and the newest time
        of its measurements (with ``finished_before_night``, also the newest
        observing night among them, after the newest time), then the count and
        newest time of its calibration records. A camera with no data gets
        zeros. `None` when
        the database could not be read; the caller must not keep an answer
        then.
    """
    wanted = sorted(set(cameras))
    empty: tuple[str | int, ...] = (
        (0, 0, "", 0, "") if finished_before_night is None else (0, 0, "", "", 0, "")
    )
    path = _library_database_path(config)
    if path is None:
        return None
    if not os.path.isfile(path):
        return dict.fromkeys(wanted, empty)
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)
        result: dict[str, tuple[str | int, ...]] = {}
        for camera in wanted:
            if finished_before_night is None:
                measurements = _read_or_default(
                    connection,
                    "SELECT COUNT(*), COALESCE(MAX(m.row_id), 0), COALESCE(MAX(m.measured_at), '') "
                    "FROM quality_measurements m JOIN quality_capture_setting s "
                    "ON s.capture_setting_id = m.capture_setting_id WHERE s.camera = ?",
                    (camera,),
                    (0, 0, ""),
                )
            else:
                measurements = _read_or_default(
                    connection,
                    "SELECT COUNT(*), COALESCE(MAX(m.row_id), 0), COALESCE(MAX(m.measured_at), ''), "
                    "COALESCE(MAX(m.observing_night_id), '') "
                    "FROM quality_measurements m JOIN quality_capture_setting s "
                    "ON s.capture_setting_id = m.capture_setting_id WHERE s.camera = ? "
                    "AND (m.observing_night_id IS NULL OR m.observing_night_id < ?)",
                    (camera, finished_before_night),
                    (0, 0, "", ""),
                )
            identities = _read_or_default(
                connection,
                "SELECT COUNT(*), COALESCE(MAX(recorded_at), '') FROM quality_calibration_identity "
                "WHERE camera = ?",
                (camera,),
                (0, ""),
            )
            result[camera] = (*measurements, *identities)
        return result
    except sqlite3.Error, OSError:
        logger.warning("Could not read the camera data versions from %s.", path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_stack_quality_data_version(config: AppConfiguration) -> tuple[str | int, ...] | None:
    """Give a value that changes whenever the library's stack-level data do.

    A result built from the stack-level quality rows of every target (the
    noise model pooled over targets, the fixed-pattern history) does not read
    frame rows. A new frame of any target writes frame rows only, so it leaves
    this version as it is. A stack run writes stack rows, so it changes it.
    The version also sees the calibration identity records and the capture
    settings the stack rows point at.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.

    Returns
    -------
    version : `tuple` [`str` or `int`, ...] or `None`
        The count, newest row number and newest time of the stack-level
        measurements, then the count and newest time of the calibration
        identities, then the count of capture settings. `None` when the
        database could not be read; the caller must not keep an answer then.
    """
    path = _library_database_path(config)
    if path is None:
        return None
    if not os.path.isfile(path):
        return (0, 0, "", 0, "", 0)
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)
        measurements = _read_or_default(
            connection,
            "SELECT COUNT(*), COALESCE(MAX(row_id), 0), COALESCE(MAX(measured_at), '') "
            "FROM quality_measurements WHERE subject_level = 'stack'",
            (),
            (0, 0, ""),
        )
        identities = _read_or_default(
            connection,
            "SELECT COUNT(*), COALESCE(MAX(recorded_at), '') FROM quality_calibration_identity",
            (),
            (0, ""),
        )
        settings = _read_or_default(connection, "SELECT COUNT(*) FROM quality_capture_setting", (), (0,))
        return (*measurements, *identities, *settings)
    except sqlite3.Error, OSError:
        logger.warning("Could not read the stack data version from %s.", path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_frame_metric_signs(
    config: AppConfiguration, metric: str, subject_groups: Sequence[Collection[str]]
) -> tuple[tuple[int, int, int], ...] | None:
    """Give a value for each group of frames that changes when its rows do.

    A result that reads one metric of the frames (for example whether each
    frame has a background level) changes when a row of that metric is added
    to or removed from one of the frames it reads. The frames are given in
    groups, so a result that keeps the part about older frames can ignore the
    rows of the frames being taken tonight. A row of a frame in no group is
    not counted.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.
    metric : `str`
        The metric's name, for example ``"frame.background_level_adu"``.
    subject_groups : `~collections.abc.Sequence`
        The subject ids (see `Target.frame_subject_id`) of each group, each
        group a collection of `str`.

    Returns
    -------
    signs : `tuple` [`tuple` [`int`, `int`, `int`], ...] or `None`
        For each group: the count of its rows, the largest row number and
        the sum of the row numbers. The sum catches a row replaced by a
        newer one when the count stays the same. All zeros when there is no
        database. `None` when the database could not be read; the caller
        must not keep an answer then.
    """
    path = _library_database_path(config)
    if path is None:
        return None
    groups = [frozenset(group) if not isinstance(group, frozenset) else group for group in subject_groups]
    if not os.path.isfile(path):
        return ((0, 0, 0),) * len(groups)
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = connection.execute(
                "SELECT subject_id, row_id FROM quality_measurements "
                "WHERE metric = ? AND subject_level = 'frame'",
                (metric,),
            ).fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                logger.warning("Could not read the frame metric signs from %s.", path, exc_info=True)
                return None
            rows = []
        row_ids: list[list[int]] = [[] for _ in groups]
        for subject_id, row_id in rows:
            for group, ids in zip(groups, row_ids, strict=True):
                if subject_id in group:
                    ids.append(row_id)
        return tuple((len(ids), max(ids, default=0), sum(ids)) for ids in row_ids)
    except sqlite3.Error, OSError:
        logger.warning("Could not read the frame metric signs from %s.", path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_target_document_signs(config: AppConfiguration) -> dict[str, tuple[int, str]] | None:
    """Give a value for each target that changes whenever its document does.

    A result built from every target's frame records can keep the part about
    a target as long as the target's stored document is the same. This reads
    each document once, in one query, and gives its size and a short hash.
    It reads the whole table but decodes nothing, which takes a few
    milliseconds for a library of 56 targets.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.

    Returns
    -------
    signs : `dict` [`str`, `tuple` [`int`, `str`]] or `None`
        For each target id, the size and the hash of its stored document.
        Empty when there is no database or no targets table. `None` when the
        database could not be read; the caller must not keep an answer then.
    """
    path = _library_database_path(config)
    if path is None:
        return None
    if not os.path.isfile(path):
        return {}
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)
        try:
            rows = connection.execute("SELECT id, data_json FROM targets").fetchall()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                logger.warning("Could not read the target document signs from %s.", path, exc_info=True)
                return None
            rows = []
        return {
            str(target_id): (
                len(text or ""),
                hashlib.blake2b((text or "").encode("utf-8"), digest_size=16).hexdigest(),
            )
            for target_id, text in rows
        }
    except sqlite3.Error, OSError:
        logger.warning("Could not read the target document signs from %s.", path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def get_target_camera_names(config: AppConfiguration, target_id: str) -> tuple[str, ...] | None:
    """List the cameras that one target's measurements were taken with.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the library folder.
    target_id : `str`
        The target's id as the library stores it.

    Returns
    -------
    cameras : `tuple` [`str`, ...] or `None`
        The camera names in the capture settings of the target's
        measurements, sorted. Empty when it has none. `None` when the
        database could not be read.
    """
    path = _library_database_path(config)
    if path is None:
        return None
    if not os.path.isfile(path):
        return ()
    connection = None
    try:
        connection = sqlite3.connect(f"file:{quote(path)}?mode=ro", uri=True, timeout=5.0)
        rows = connection.execute(
            "SELECT DISTINCT s.camera FROM quality_measurements m JOIN quality_capture_setting s "
            "ON s.capture_setting_id = m.capture_setting_id WHERE m.target_id = ? ORDER BY s.camera",
            (target_id,),
        ).fetchall()
        return tuple(str(row[0]) for row in rows)
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return ()
        logger.warning("Could not read the target's cameras from %s.", path, exc_info=True)
        return None
    except sqlite3.Error, OSError:
        logger.warning("Could not read the target's cameras from %s.", path, exc_info=True)
        return None
    finally:
        if connection is not None:
            connection.close()


def _library_database_path(config: AppConfiguration) -> str | None:
    """Find the library database file.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    path : `str` or `None`
        The file's path, or `None` when the configuration names no library.
    """
    try:
        return os.path.join(str(config.get_library_path()), "astrometrics.db")
    except Exception:
        # No library folder means no version, and so no answer is kept.
        logger.warning("Could not find the library folder for a data version.", exc_info=True)
        return None


def _read_or_default(
    connection: sqlite3.Connection, statement: str, parameters: tuple, default: tuple
) -> tuple:
    """Read the one row of a query, or a default for a missing table.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        A read-only connection.
    statement : `str`
        A fixed statement that gives one row.
    parameters : `tuple`
        The statement's bound values.
    default : `tuple`
        The row to give when the table is missing or no row matches.

    Returns
    -------
    row : `tuple`
        The row.

    Raises
    ------
    sqlite3.OperationalError
        If the query fails for a reason other than a missing table.
    """
    try:
        row = connection.execute(statement, parameters).fetchone()
    except sqlite3.OperationalError as error:
        if "no such table" in str(error):
            return default
        raise
    return tuple(row) if row is not None else default
