"""Purpose: Move the alignment and guiding records into wayfinding.db.

Description: Older versions kept each night's plate-solve syncs
(``alignment_logs``), guiding samples (``guiding_logs``) and polar
alignment runs (``polar_alignment_logs``) in the logs database,
``astrometrics_log.db``, next to the job list. The wayfinding library now
owns these records and keeps them in its own ``wayfinding.db``, in the
tables ``alignment_attempts``, ``guiding_samples`` and ``polar_alignments``
(see `wayfindinglib.ControlRecordStore`). The current code never reads the
old tables.

This script copies every old row into the new tables, checks that each
table received as many rows as it sent, and then drops the old tables. It
also drops the unused ``agent_interactions`` and ``agent_knowledge``
tables when they are empty; nothing ever wrote to them. A ``.bak`` copy
of each database is written before anything changes. A guiding sample
stored before samples recorded their source is copied as
``"unverified"``. Running the script again does nothing, because the old
tables are gone.

Usage::

    python build/migrations/move_guiding_alignment_records.py \
        [LOGS_DB WAYFINDING_DB]

Without paths, both files are found through the application
configuration, as the backend finds them.
"""

import argparse
import shutil
import sqlite3
from pathlib import Path

MOVED_TABLES: dict[str, tuple[str, tuple[str, ...]]] = {
    "alignment_logs": (
        "alignment_attempts",
        (
            "session_id",
            "target_name",
            "timestamp",
            "status",
            "delta_ra_arcsec",
            "delta_dec_arcsec",
            "pointing_error_arcsec",
            "mount_ra",
            "mount_dec",
        ),
    ),
    "guiding_logs": (
        "guiding_samples",
        (
            "session_id",
            "target_name",
            "timestamp",
            "dra",
            "ddec",
            "pulse_ra",
            "pulse_dec",
            "snr",
            "rms_ra",
            "rms_dec",
            "star_mass",
            "source",
        ),
    ),
    "polar_alignment_logs": (
        "polar_alignments",
        (
            "session_id",
            "timestamp",
            "status",
            "total_error_arcsec",
            "alt_error_arcsec",
            "az_error_arcsec",
            "pole_ra",
            "pole_dec",
            "paa_points_json",
        ),
    ),
}
"""Old table in the logs database -> (new table, copied columns)."""

UNUSED_TABLES = ("agent_interactions", "agent_knowledge")
"""Tables nothing ever wrote to. They are dropped only when empty."""


def _tables(connection: sqlite3.Connection) -> set[str]:
    """List the tables of a database.

    Returns
    -------
    names : `set` [`str`]
        The table names.
    """
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    """List the columns of a table.

    Returns
    -------
    names : `set` [`str`]
        The column names.
    """
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def move_records(logs_db_path: Path, wayfinding_db_path: Path) -> dict[str, int]:
    """Copy the old records into wayfinding.db, then drop the old tables.

    Parameters
    ----------
    logs_db_path : `Path`
        The logs database (``astrometrics_log.db``) that holds the old tables.
    wayfinding_db_path : `Path`
        The wayfinding library's ``wayfinding.db``. It is created, with the
        new tables, if it does not exist.

    Returns
    -------
    copied : `dict` [`str`, `int`]
        How many rows each old table gave. Empty if no old table was found,
        in which case nothing changes.

    Raises
    ------
    astrometricslib.StorageError
        If a new table did not receive every row. Nothing is dropped and
        no change to wayfinding.db is kept.
    """
    from astrometricslib import StorageError
    from wayfindinglib import ControlRecordStore

    logs = sqlite3.connect(logs_db_path)
    try:
        present = [table for table in MOVED_TABLES if table in _tables(logs)]
        if not present:
            return {}
        for path in (logs_db_path, wayfinding_db_path):
            if path.exists():
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
        ControlRecordStore(str(wayfinding_db_path))

        target = sqlite3.connect(wayfinding_db_path)
        copied: dict[str, int] = {}
        try:
            for old_table in present:
                new_table, columns = MOVED_TABLES[old_table]
                available = _columns(logs, old_table)
                selected = [
                    column if column in available else ("'unverified'" if column == "source" else "NULL")
                    for column in columns
                ]
                rows = logs.execute(f"SELECT {', '.join(selected)} FROM {old_table}").fetchall()
                before = target.execute(f"SELECT COUNT(*) FROM {new_table}").fetchone()[0]
                placeholders = ", ".join("?" * len(columns))
                target.executemany(
                    f"INSERT INTO {new_table} ({', '.join(columns)}) VALUES ({placeholders})", rows
                )
                after = target.execute(f"SELECT COUNT(*) FROM {new_table}").fetchone()[0]
                if after - before != len(rows):
                    target.rollback()
                    raise StorageError(
                        f"{new_table} received {after - before} of {len(rows)} rows from {old_table}."
                    )
                copied[old_table] = len(rows)
            target.commit()
        finally:
            target.close()

        for old_table in present:
            logs.execute(f"DROP TABLE {old_table}")
        for unused in UNUSED_TABLES:
            if unused in _tables(logs) and not logs.execute(f"SELECT COUNT(*) FROM {unused}").fetchone()[0]:
                logs.execute(f"DROP TABLE {unused}")
        logs.commit()
    finally:
        logs.close()
    return copied


def _configured_paths() -> tuple[Path, Path]:
    """Find both databases through the application configuration.

    Returns
    -------
    logs_db_path : `Path`
        The logs database.
    wayfinding_db_path : `Path`
        The wayfinding library's database.
    """
    from astrometricslib import get_configuration
    from wayfindinglib import ControlRecordStore

    configuration = get_configuration()
    return Path(configuration.get_logs_db_path()), Path(
        ControlRecordStore.for_configuration(configuration).db_path
    )


def main() -> None:
    """Move the records between the databases named on the command line."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("logs_db", type=Path, nargs="?", help="The astrometrics_log.db file.")
    parser.add_argument("wayfinding_db", type=Path, nargs="?", help="The wayfinding.db file.")
    args = parser.parse_args()
    if (args.logs_db is None) != (args.wayfinding_db is None):
        parser.error("Give both database paths, or neither.")
    logs_db, wayfinding_db = (
        (args.logs_db, args.wayfinding_db) if args.logs_db is not None else _configured_paths()
    )
    copied = move_records(logs_db, wayfinding_db)
    if not copied:
        print(f"No old alignment or guiding tables in {logs_db}. Nothing changed.")
        return
    for old_table, count in copied.items():
        print(f"Moved {count} row(s) from {old_table} to {MOVED_TABLES[old_table][0]}.")
    print(f"The old tables were dropped from {logs_db}. The originals are saved with a .bak suffix.")


if __name__ == "__main__":
    main()
