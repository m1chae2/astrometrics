"""Purpose: Tests for the safe SQLite snapshot and the catalog backup.

Description: Checks that a snapshot holds the data (including changes still in
the write-ahead log), that a broken source leaves no copy behind, and that
`backup_catalog_database` keeps its return contract.
"""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from astrometricslib.drivers.local_database import backup_catalog_database, snapshot_sqlite_database


def _make_wal_database(database_path: Path) -> sqlite3.Connection:
    """Create a write-ahead-log database with one row and keep it open.

    Parameters
    ----------
    database_path : `pathlib.Path`
        Where to create the database.

    Returns
    -------
    connection : `sqlite3.Connection`
        Open connection; the caller closes it.
    """
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE targets (id TEXT)")
    connection.execute("INSERT INTO targets VALUES ('M 13')")
    connection.commit()
    return connection


def test_snapshot_contains_rows_still_in_the_wal(tmp_path: Path) -> None:
    """A snapshot taken while the source is open includes committed rows."""
    source_path = tmp_path / "source.db"
    connection = _make_wal_database(source_path)
    destination_path = tmp_path / "copy.db"

    assert snapshot_sqlite_database(str(source_path), str(destination_path))
    connection.close()

    copy = sqlite3.connect(destination_path)
    assert copy.execute("SELECT id FROM targets").fetchall() == [("M 13",)]
    copy.close()


def test_snapshot_of_a_non_database_leaves_no_copy(tmp_path: Path) -> None:
    """A source that is not a database returns False and leaves nothing."""
    source_path = tmp_path / "garbage.db"
    source_path.write_bytes(b"this is not a sqlite file" * 100)
    destination_path = tmp_path / "copy.db"

    assert not snapshot_sqlite_database(str(source_path), str(destination_path))
    assert not destination_path.exists()


def test_backup_catalog_database_returns_path_or_none(tmp_path: Path) -> None:
    """The backup returns None with no database, and a usable path with one."""
    app_config = SimpleNamespace(get_library_path=lambda: tmp_path)
    assert backup_catalog_database(app_config) is None

    _make_wal_database(tmp_path / "astrometrics.db").close()
    backup_path = backup_catalog_database(app_config)

    assert backup_path is not None
    assert backup_path.endswith(".bak")
    copy = sqlite3.connect(backup_path)
    assert copy.execute("SELECT count(*) FROM targets").fetchone() == (1,)
    copy.close()
