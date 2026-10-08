"""Purpose: Tests for the script that drops the old session table."""

import importlib.util
import json
import sqlite3
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "drop_old_observation_sessions", Path(__file__).parent.parent / "drop_old_observation_sessions.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _make_database(path: Path, with_old_table: bool) -> None:
    """Create a wayfinding.db with the current table and maybe the old one."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE wayfinding_observation_sessions (id TEXT PRIMARY KEY, data_json TEXT)")
    connection.execute("INSERT INTO wayfinding_observation_sessions VALUES ('new', '{\"id\": \"new\"}')")
    if with_old_table:
        connection.execute(
            "CREATE TABLE observation_sessions "
            "(id TEXT PRIMARY KEY, target_session_id TEXT, created_at TEXT, data_json TEXT)"
        )
        connection.execute(
            "INSERT INTO observation_sessions VALUES "
            "('old', 'M 13:2026-07-25', '2026-07-25', '{\"id\": \"old\", \"createdAt\": \"2026-07-25\"}')"
        )
    connection.commit()
    connection.close()


def _tables(path: Path) -> set[str]:
    """Return the names of the tables in a database.

    Returns
    -------
    names : `set` [`str`]
        The table names.
    """
    connection = sqlite3.connect(path)
    try:
        return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        connection.close()


def test_old_rows_are_saved_and_the_old_table_is_dropped(tmp_path: Path) -> None:
    """The old rows go to JSON and the old table goes; the rest stays."""
    db_path = tmp_path / "wayfinding.db"
    _make_database(db_path, with_old_table=True)

    count = _MODULE.export_and_drop(db_path)

    assert count == 1
    exported = json.loads((tmp_path / "wayfinding.observation_sessions.json").read_text(encoding="utf-8"))
    assert exported == [{"id": "old", "createdAt": "2026-07-25"}]
    assert _tables(db_path) == {"wayfinding_observation_sessions"}
    assert "observation_sessions" in _tables(tmp_path / "wayfinding.db.bak")


def test_a_database_without_the_old_table_is_left_alone(tmp_path: Path) -> None:
    """Without the old table, nothing is written and nothing changes."""
    db_path = tmp_path / "wayfinding.db"
    _make_database(db_path, with_old_table=False)

    assert _MODULE.export_and_drop(db_path) == -1
    assert _tables(db_path) == {"wayfinding_observation_sessions"}
    assert not (tmp_path / "wayfinding.db.bak").exists()
    assert not (tmp_path / "wayfinding.observation_sessions.json").exists()
