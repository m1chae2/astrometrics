r"""List local library database snapshots, or copy one out and check it.

The snapshots are made by ``backup_library_databases.py`` and live in
``~/.cache/astrometrics/backup/``. ``--list`` shows them. ``--snapshot NAME
--destination DIR`` copies one into DIR and checks each database with SQLite's
integrity check. The script never writes to the live library. It prints the
commands to put the files in place yourself, which you should do with the
backend stopped.

Run::

    python -m astrometricslib.scripts.restore_library_databases --list

    python -m astrometricslib.scripts.restore_library_databases \
        --snapshot 20261011_151519 --destination ~/restored_backup
"""

import argparse
import logging
import re
import shutil
import sqlite3
import sys
from pathlib import Path

from astrometricslib.foundation.paths import backup_directory

logger = logging.getLogger(__name__)

SNAPSHOT_NAME_PATTERN = re.compile(r"^\d{8}_\d{6}$")


def check_database_integrity(database_path: Path) -> bool:
    """Run SQLite's integrity check on a downloaded database.

    Parameters
    ----------
    database_path : `pathlib.Path`
        The database file.

    Returns
    -------
    ok : `bool`
        `True` if the check reports only "ok".
    """
    try:
        connection = sqlite3.connect(database_path)
        try:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
        finally:
            connection.close()
    except sqlite3.Error:
        logger.exception("Could not check %s", database_path)
        return False
    return [row[0] for row in rows] == ["ok"]


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--list", action="store_true", help="List the local snapshots.")
    parser.add_argument("--snapshot", help="Snapshot name to copy out, for example 20261011_151519.")
    parser.add_argument("--destination", type=Path, help="Empty or new folder to copy into.")
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="Folder that holds the snapshots. Default: ~/.cache/astrometrics/backup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """List snapshots, or copy one out and check it.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 on success, 1 on failure, 2 for bad arguments.
    """
    parser = _build_argument_parser()
    arguments = parser.parse_args(argv)
    backup_root = arguments.backup_root or Path(backup_directory())

    if arguments.list:
        names = (
            sorted(
                folder.name
                for folder in backup_root.iterdir()
                if folder.is_dir() and SNAPSHOT_NAME_PATTERN.match(folder.name)
            )
            if backup_root.is_dir()
            else []
        )
        for name in names:
            print(name)
        return 0

    if not arguments.snapshot or arguments.destination is None:
        parser.print_usage(sys.stderr)
        print("Give --list, or both --snapshot and --destination.", file=sys.stderr)
        return 2

    source = backup_root / arguments.snapshot
    if not SNAPSHOT_NAME_PATTERN.match(arguments.snapshot) or not source.is_dir():
        print(f"No snapshot named {arguments.snapshot} in {backup_root}.", file=sys.stderr)
        return 1
    destination = arguments.destination.expanduser()
    if destination.exists() and any(destination.iterdir()):
        print(f"{destination} is not empty; choose a new folder.", file=sys.stderr)
        return 2
    shutil.copytree(source, destination, dirs_exist_ok=True)

    exit_code = 0
    for database_path in sorted(destination.glob("*.db")):
        ok = check_database_integrity(database_path)
        print(f"{database_path.name}: integrity {'ok' if ok else 'FAILED'}")
        if not ok:
            exit_code = 1
    print(f"\nCopied to {destination}. Nothing in the live library was changed.")
    print("To put the databases in place, stop the backend, then for each database copy it into the")
    print("library folder and remove the old -wal and -shm files next to it, for example:")
    print(f"  cp {destination}/astrometrics.db <library>/astrometrics.db")
    print("  rm -f <library>/astrometrics.db-wal <library>/astrometrics.db-shm")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
