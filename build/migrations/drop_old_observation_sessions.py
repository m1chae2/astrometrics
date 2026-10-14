"""Purpose: Remove the old ``observation_sessions`` table from wayfinding.db.

Description: Older versions of wayfindinglib kept a second, smaller
observation session record (a target session id, guiding samples and
weather samples) in a table named ``observation_sessions`` inside
``wayfinding.db``. The current code keeps one observation session model,
stored in the ``wayfinding_observation_sessions`` table, and never reads
the old table. The two shapes are too different to convert one into the
other, so this script saves every old row to a JSON file next to the
database and then drops the old table. A ``.bak`` copy of the database
is written first.

Usage::

    python build/migrations/drop_old_observation_sessions.py DB_PATH

``DB_PATH`` is the ``wayfinding.db`` file to migrate.
"""

import argparse
import json
import shutil
import sqlite3
from pathlib import Path

OLD_TABLE = "observation_sessions"
"""The table the old observation session model was stored in."""


def export_and_drop(db_path: Path) -> int:
    """Save the old table's rows to JSON, then drop the table.

    Parameters
    ----------
    db_path : `Path`
        The ``wayfinding.db`` file to migrate.

    A ``.bak`` copy of the database is written before anything changes.

    Returns
    -------
    count : `int`
        How many rows were saved. ``-1`` if the old table was not found,
        in which case nothing changes.
    """
    connection = sqlite3.connect(db_path)
    try:
        found = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", (OLD_TABLE,)
        ).fetchone()
        if found is None:
            return -1
        shutil.copy2(db_path, db_path.with_suffix(db_path.suffix + ".bak"))
        rows = connection.execute("SELECT data_json FROM observation_sessions").fetchall()
        sessions = [json.loads(row[0]) for row in rows]
        export_path = db_path.with_name(f"{db_path.stem}.{OLD_TABLE}.json")
        export_path.write_text(json.dumps(sessions, indent=2), encoding="utf-8")
        connection.execute("DROP TABLE observation_sessions")
        connection.commit()
    finally:
        connection.close()
    return len(sessions)


def main() -> None:
    """Migrate the database named on the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("db_path", type=Path, help="The wayfinding.db file to migrate.")
    args = parser.parse_args()
    count = export_and_drop(args.db_path)
    if count < 0:
        print("No old observation_sessions table found. Nothing changed.")
        return
    print(
        f"Saved {count} old session(s) to JSON and dropped the old table. "
        "The original is saved with a .bak suffix."
    )


if __name__ == "__main__":
    main()
