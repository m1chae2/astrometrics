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

import logging
import os
import sqlite3
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
