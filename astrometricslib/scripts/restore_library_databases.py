r"""List or download library database backups made by the backup script.

``--list`` shows the snapshots on the remote. ``--snapshot NAME --destination
DIR`` downloads one into DIR and checks each database with SQLite's integrity
check. The script never writes to the live library. It prints the commands to
put the files in place yourself, which you should do with the backend stopped.

Run::

    python -m astrometricslib.scripts.restore_library_databases --list

    python -m astrometricslib.scripts.restore_library_databases \
        --snapshot 20261011_151519 --destination ~/restored_backup
"""

import argparse
import logging
import sqlite3
import subprocess
import sys
from pathlib import Path

from astrometricslib.foundation.paths import remote_snapshot_path

logger = logging.getLogger(__name__)


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
    parser.add_argument("--list", action="store_true", help="List snapshots on the remote.")
    parser.add_argument("--snapshot", help="Snapshot name to download, for example 20261011_151519.")
    parser.add_argument("--destination", type=Path, help="Empty or new folder to download into.")
    parser.add_argument("--rclone", default="rclone", help="The rclone executable. Default: rclone.")
    parser.add_argument("--remote", default="drive:", help="The rclone remote, with colon. Default: drive:.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """List snapshots, or download and check one.

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

    if arguments.list:
        listing = subprocess.run(
            [arguments.rclone, "lsf", "--dirs-only", remote_snapshot_path(arguments.remote) + "/"],
            capture_output=True,
            text=True,
            check=False,
        )
        if listing.returncode != 0:
            print(listing.stderr.strip(), file=sys.stderr)
            return 1
        for line in sorted(listing.stdout.split()):
            print(line.rstrip("/"))
        return 0

    if not arguments.snapshot or arguments.destination is None:
        parser.print_usage(sys.stderr)
        print("Give --list, or both --snapshot and --destination.", file=sys.stderr)
        return 2

    destination = arguments.destination.expanduser()
    if destination.exists() and any(destination.iterdir()):
        print(f"{destination} is not empty; choose a new folder.", file=sys.stderr)
        return 2
    download = subprocess.run(
        [
            arguments.rclone,
            "copy",
            remote_snapshot_path(arguments.remote, arguments.snapshot) + "/",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if download.returncode != 0:
        print(download.stderr.strip(), file=sys.stderr)
        return 1

    exit_code = 0
    for database_path in sorted(destination.glob("*.db")):
        ok = check_database_integrity(database_path)
        print(f"{database_path.name}: integrity {'ok' if ok else 'FAILED'}")
        if not ok:
            exit_code = 1
    print(f"\nDownloaded to {destination}. Nothing in the live library was changed.")
    print("To put the databases in place, stop the backend, then for each database copy it into the")
    print("library folder and remove the old -wal and -shm files next to it, for example:")
    print(f"  cp {destination}/astrometrics.db <library>/astrometrics.db")
    print("  rm -f <library>/astrometrics.db-wal <library>/astrometrics.db-shm")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
