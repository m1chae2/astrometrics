"""The one place that opens the stored catalog answers database.

Every outside catalog the library asks (SIMBAD, the AAVSO Variable Star
Index, Gaia DR3's variability classes) is asked once for a piece of sky and
the answer is kept here, so the next run reads it from disk instead of asking
again. Gaia DR3 star positions have their own tile tables in ``catalog_store``;
this file does the same job for every other catalog, and for SIMBAD name and
object lookups.

The file is ``catalog_answers.db`` in the same catalog cache folder as the
Gaia star cache (``<scratch folder>/catalogs``). It holds one table,
``catalog_answers``, with one row for each question already asked:

- ``catalog``: the catalog's name, such as ``SIMBAD`` or ``VSX``.
- ``key``: the question. For a sky question it names the sky tile and the
  columns asked for; for a name lookup it holds the name.
- ``fetched_at``: when the answer arrived (UTC, ISO 8601). A caller compares
  it with a lifetime (see ``catalog_cache``) to decide whether the answer is
  still fresh.
- ``row_count``, ``service`` and ``payload``: how many rows came back, which
  service answered, and the rows themselves as compressed text (an
  astropy table written in the ECSV format, which keeps units and missing
  values). An answer with no rows at all is stored too, with an empty
  payload, so a question whose answer is "nothing there" is not asked again.

``PRAGMA user_version`` holds the schema version. The file can be deleted at
any time; the answers are then fetched again.
"""

import logging
import sqlite3
import zlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from astropy.table import Table

from astrometricslib.foundation.paths import catalog_cache_directory
from astrometricslib.foundation.storage.local_database import connect_db

logger = logging.getLogger(__name__)

_ANSWERS_DB_FILENAME = "catalog_answers.db"

# The schema version this file writes into ``PRAGMA user_version``.
_SCHEMA_VERSION = 1

__all__ = [
    "StoredAnswer",
    "catalog_answers_path",
    "count_stored_answers",
    "read_stored_answer",
    "write_stored_answer",
]


@dataclass(frozen=True)
class StoredAnswer:
    """One stored answer to one catalog question.

    Attributes
    ----------
    table : `astropy.table.Table` or `None`
        The rows, or `None` when the catalog answered with nothing.
    fetched_at : `datetime.datetime`
        When the answer was fetched (UTC).
    service : `str`
        The service that answered, such as ``"SIMBAD"``.
    """

    table: Table | None
    fetched_at: datetime
    service: str


def catalog_answers_path() -> Path:
    """Find where the stored catalog answers database lives on disk.

    Returns
    -------
    path : `pathlib.Path`
        The full path of the database file, in the catalog cache folder. The
        file may not exist yet, which only means nothing is stored.
    """
    return Path(catalog_cache_directory()) / _ANSWERS_DB_FILENAME


def _open() -> sqlite3.Connection:
    """Open the database, creating the table when it is missing.

    Returns
    -------
    connection : `sqlite3.Connection`
        An open connection. The caller closes it.
    """
    connection = connect_db(str(catalog_answers_path()))
    if connection.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS catalog_answers (
                catalog TEXT NOT NULL,
                key TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                row_count INTEGER NOT NULL,
                service TEXT,
                payload BLOB,
                PRIMARY KEY (catalog, key)
            )
        """)
        connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
        connection.commit()
    return connection


def _encode(table: Table | None) -> bytes | None:
    """Turn a table into compressed ECSV text.

    Parameters
    ----------
    table : `astropy.table.Table` or `None`
        The rows to store.

    Returns
    -------
    payload : `bytes` or `None`
        The compressed text, or `None` for no rows.
    """
    if table is None or len(table) == 0:
        return None
    from io import StringIO

    buffer = StringIO()
    table.write(buffer, format="ascii.ecsv")
    return zlib.compress(buffer.getvalue().encode("utf-8"))


def _decode(payload: bytes | None) -> Table | None:
    """Turn stored bytes back into a table.

    Parameters
    ----------
    payload : `bytes` or `None`
        What `_encode` made.

    Returns
    -------
    table : `astropy.table.Table` or `None`
        The rows, or `None` for an empty payload.
    """
    if payload is None:
        return None
    return Table.read(zlib.decompress(payload).decode("utf-8"), format="ascii.ecsv")


def read_stored_answer(catalog: str, key: str) -> StoredAnswer | None:
    """Read one stored answer.

    A file that cannot be read (damaged, or locked for too long) counts as
    holding nothing, because the answer can always be fetched again.

    Parameters
    ----------
    catalog : `str`
        The catalog's name.
    key : `str`
        The question.

    Returns
    -------
    answer : `StoredAnswer` or `None`
        The answer, or `None` when the question was never stored.
    """
    try:
        connection = _open()
        try:
            row = connection.execute(
                "SELECT fetched_at, service, payload FROM catalog_answers WHERE catalog = ? AND key = ?",
                (catalog, key),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return StoredAnswer(
            table=_decode(row["payload"]),
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            service=row["service"] or catalog,
        )
    except (sqlite3.Error, OSError, ValueError, zlib.error) as read_error:
        logger.warning("Could not read the stored %s answer %r: %s", catalog, key, read_error)
        return None


def write_stored_answer(
    catalog: str, key: str, table: Table | None, service: str, fetched_at: datetime | None = None
) -> None:
    """Store one answer, replacing an older one for the same question.

    A failed write is logged and ignored: the caller already has the answer
    in hand, and only the saving for later is lost.

    Parameters
    ----------
    catalog : `str`
        The catalog's name.
    key : `str`
        The question.
    table : `astropy.table.Table` or `None`
        The rows, or `None` when the catalog answered with nothing.
    service : `str`
        The service that answered.
    fetched_at : `datetime.datetime`, optional
        When the answer arrived. Defaults to now.
    """
    moment = fetched_at or datetime.now(UTC)
    try:
        payload = _encode(table)
        connection = _open()
        try:
            connection.execute(
                "INSERT OR REPLACE INTO catalog_answers "
                "(catalog, key, fetched_at, row_count, service, payload) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    catalog,
                    key,
                    moment.isoformat(timespec="seconds"),
                    0 if table is None else len(table),
                    service,
                    payload,
                ),
            )
            connection.commit()
        finally:
            connection.close()
    except (sqlite3.Error, OSError, ValueError, TypeError) as write_error:
        logger.warning("Could not store the %s answer %r: %s", catalog, key, write_error)


def count_stored_answers(catalog: str | None = None) -> int:
    """Count the stored answers.

    Parameters
    ----------
    catalog : `str`, optional
        Count only this catalog's answers. Defaults to all.

    Returns
    -------
    count : `int`
        The number of stored questions; 0 when the file does not exist.
    """
    if not catalog_answers_path().exists():
        return 0
    connection = _open()
    try:
        if catalog is None:
            return int(connection.execute("SELECT COUNT(*) FROM catalog_answers").fetchone()[0])
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM catalog_answers WHERE catalog = ?", (catalog,)
            ).fetchone()[0]
        )
    finally:
        connection.close()
