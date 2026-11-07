r"""Back up the library databases and settings to a local folder.

The script makes a safe copy of ``astrometrics.db``, ``astrometrics_log.db``
and the observatory database ``wayfinding.db`` (SQLite's online backup, then
an integrity check), adds ``calibration_frames.json`` and the configuration
file, and stores them in ``~/.cache/astrometrics/backup/<YYYYmmdd_HHMMSS>/``.
The copies are a safety net for changes to the app (a migration or a restack
that goes wrong). Nothing is uploaded anywhere. It works whether or not the
backend is running.

Only one backup runs at a time (an exclusive lock on ``.lock`` in the backup
folder); a second run logs that and exits at once. It does nothing when none
of the files changed since the last snapshot (``--force`` overrides). It
keeps the newest `KEEP_SNAPSHOTS` snapshots and deletes older ones, never the
one it just made.

Run::

    python -m astrometricslib.scripts.backup_library_databases

    python -m astrometricslib.scripts.backup_library_databases --dry-run
"""

import argparse
import fcntl
import json
import logging
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import IO

from astrometricslib._internal.drivers.local_database import snapshot_sqlite_database
from astrometricslib.foundation.launch_identity import read_launch_identity
from astrometricslib.foundation.paths import backup_directory, data_directory

logger = logging.getLogger(__name__)

DATABASE_FILE_NAMES = ("astrometrics.db", "astrometrics_log.db")
CALIBRATION_FILE_NAME = "calibration_frames.json"
WAYFINDING_DATABASE_RELATIVE_PATH = Path("wayfinding") / "wayfinding.db"
STATE_FILE_NAME = "state.json"
LOCK_FILE_NAME = ".lock"
SNAPSHOT_NAME_PATTERN = re.compile(r"^\d{8}_\d{6}$")
# Design choice: the snapshots are a regression safety net for changes to the
# app, not an archive. Each is about 1.5 GB, so five recent ones (a few days
# of work) fit comfortably on the local disk. Raise it if you need to go
# further back.
KEEP_SNAPSHOTS = 5


def compute_source_fingerprint(
    library_path: Path, config_file_path: Path | None, extra_database_paths: tuple[Path, ...] = ()
) -> dict[str, list[int] | None]:
    """Describe the files being backed up by their size and modification time.

    A database in write-ahead-log mode keeps recent changes in a ``-wal``
    file, so those files are included. A change to any listed file changes the
    fingerprint.

    Parameters
    ----------
    library_path : `pathlib.Path`
        Folder holding the databases and ``calibration_frames.json``.
    config_file_path : `pathlib.Path` or `None`
        The configuration file, if there is one.
    extra_database_paths : `tuple` [`pathlib.Path`, ...], optional
        Other SQLite databases to back up, such as the observatory database.

    Returns
    -------
    fingerprint : `dict` [`str`, `list` [`int`] or `None`]
        For each file path, ``[size_in_bytes, modification_time_ns]``, or
        `None` if the file does not exist.
    """
    watched_paths = []
    for database_name in DATABASE_FILE_NAMES:
        watched_paths.append(library_path / database_name)
        watched_paths.append(library_path / f"{database_name}-wal")
    for database_path in extra_database_paths:
        watched_paths.append(database_path)
        watched_paths.append(database_path.with_name(f"{database_path.name}-wal"))
    watched_paths.append(library_path / CALIBRATION_FILE_NAME)
    if config_file_path is not None:
        watched_paths.append(config_file_path)

    fingerprint: dict[str, list[int] | None] = {}
    for watched_path in watched_paths:
        if watched_path.exists():
            file_status = watched_path.stat()
            fingerprint[str(watched_path)] = [file_status.st_size, file_status.st_mtime_ns]
        else:
            fingerprint[str(watched_path)] = None
    return fingerprint


def stage_snapshot(
    library_path: Path,
    config_file_path: Path | None,
    staging_folder: Path,
    extra_database_paths: tuple[Path, ...] = (),
) -> bool:
    """Write the safe copies and plain file copies into a staging folder.

    Parameters
    ----------
    library_path : `pathlib.Path`
        Folder holding the databases and ``calibration_frames.json``.
    config_file_path : `pathlib.Path` or `None`
        The configuration file, if there is one.
    staging_folder : `pathlib.Path`
        Folder to fill. It is created.
    extra_database_paths : `tuple` [`pathlib.Path`, ...], optional
        Other SQLite databases to back up. Each is copied under its own
        file name.

    Returns
    -------
    ok : `bool`
        `True` if every database that exists was copied and passed its
        integrity check.
    """
    staging_folder.mkdir(parents=True, exist_ok=True)
    all_ok = True
    source_paths = [library_path / name for name in DATABASE_FILE_NAMES] + list(extra_database_paths)
    for source_path in source_paths:
        if not source_path.exists():
            logger.warning("%s does not exist; skipping it", source_path)
            continue
        if not snapshot_sqlite_database(str(source_path), str(staging_folder / source_path.name)):
            logger.error("Snapshot of %s failed", source_path)
            all_ok = False
    for plain_file in (library_path / CALIBRATION_FILE_NAME, config_file_path):
        if plain_file is not None and plain_file.exists():
            shutil.copy2(plain_file, staging_folder / plain_file.name)
    return all_ok


def snapshots_to_delete(snapshot_names: list[str], protected_name: str | None = None) -> list[str]:
    """Pick the local snapshots that retention removes.

    Keeps the newest `KEEP_SNAPSHOTS` and never the one named in
    ``protected_name``.

    Parameters
    ----------
    snapshot_names : `list` [`str`]
        Snapshot folder names (``YYYYmmdd_HHMMSS``). Others are ignored.
    protected_name : `str`, optional
        A snapshot that must never be deleted, such as the one just made.

    Returns
    -------
    names_to_delete : `list` [`str`]
        Names outside the newest `KEEP_SNAPSHOTS`, oldest first.
    """
    valid_names = sorted(name for name in snapshot_names if SNAPSHOT_NAME_PATTERN.match(name))
    keep = set(valid_names[-KEEP_SNAPSHOTS:])
    if protected_name is not None:
        keep.add(protected_name)
    return [name for name in valid_names if name not in keep]


def folder_size_bytes(folder: Path) -> int:
    """Add up the size of every file below a folder.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder.

    Returns
    -------
    size : `int`
        Total size in bytes.
    """
    return sum(path.stat().st_size for path in folder.rglob("*") if path.is_file())


def apply_retention(backup_root: Path, protected_name: str | None, dry_run: bool) -> int:
    """Delete old local snapshots, keeping the newest `KEEP_SNAPSHOTS`.

    Parameters
    ----------
    backup_root : `pathlib.Path`
        Folder holding the snapshot folders.
    protected_name : `str`, optional
        A snapshot that is never deleted (the one just made).
    dry_run : `bool`
        If `True`, only print what would be deleted.

    Returns
    -------
    freed_bytes : `int`
        Bytes freed (or that would be freed in a dry run).
    """
    existing_names = [folder.name for folder in backup_root.iterdir() if folder.is_dir()]
    freed_bytes = 0
    for name in snapshots_to_delete(existing_names, protected_name):
        folder = backup_root / name
        size = folder_size_bytes(folder)
        if dry_run:
            print(f"[dry-run] would delete old snapshot {folder}")
        else:
            shutil.rmtree(folder)
            logger.info("Deleted old snapshot %s", name)
        freed_bytes += size
    if freed_bytes:
        verb = "would free" if dry_run else "Freed"
        print(f"{verb} {freed_bytes / 1e9:.2f} GB by deleting old snapshots")
    return freed_bytes


def acquire_lock(lock_path: Path) -> IO[str] | None:
    """Take the exclusive lock that allows only one backup at a time.

    The lock is released when the returned file is closed or the process
    ends, even if it crashes.

    Parameters
    ----------
    lock_path : `pathlib.Path`
        The lock file. It is created if missing.

    Returns
    -------
    lock_file : `typing.IO` or `None`
        The open lock file (keep it open while backing up), or `None` if
        another backup already holds the lock.
    """
    lock_file = open(lock_path, "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock_file.close()
        return None
    return lock_file


def read_state(state_path: Path) -> dict:
    """Read the saved record of the last snapshot.

    Parameters
    ----------
    state_path : `pathlib.Path`
        The state file.

    Returns
    -------
    state : `dict`
        The saved record, or an empty dict if missing or unreadable.
    """
    try:
        return json.loads(state_path.read_text())
    except OSError, ValueError:
        return {}


def library_is_disposable(config_file_path: Path | None) -> bool:
    """Say whether the settings file declares its library a throwaway.

    The switch is ``disposable_library = true`` in the ``[Launch]`` section of
    the settings file (see `astrometricslib.foundation.launch_identity`). It
    is the one place a launch says what is disposable, so a backup needs no
    second key and no guess from a folder name.

    Parameters
    ----------
    config_file_path : `pathlib.Path` or `None`
        The settings file being backed up, if there is one.

    Returns
    -------
    disposable : `bool`
        `True` when the library is declared disposable.
    """
    if config_file_path is None:
        return False
    identity = read_launch_identity(config_file_path)
    return identity is not None and identity.disposable_library


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="Print what would happen; change nothing.")
    parser.add_argument("--force", action="store_true", help="Back up even if nothing changed.")
    parser.add_argument(
        "--library-path", type=Path, default=None, help="Library folder. Default: from the config."
    )
    parser.add_argument(
        "--config-path", type=Path, default=None, help="Config file. Default: the one in use."
    )
    parser.add_argument(
        "--backup-root",
        type=Path,
        default=None,
        help="Folder that holds the snapshots. Default: ~/.cache/astrometrics/backup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Make a local snapshot of the databases, then delete the oldest ones.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 on success, when nothing changed, or when another backup is
        running; 1 if a snapshot failed.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    library_path = arguments.library_path
    config_file_path = arguments.config_path
    if library_path is None or config_file_path is None:
        from astrometricslib.foundation.config import get_configuration

        configuration = get_configuration()
        library_path = library_path or Path(str(configuration.get_library_path()))
        config_file_path = config_file_path or configuration.config_file_path
    if library_is_disposable(config_file_path):
        # A launch can say its library is a throwaway (a simulated observatory
        # holds temporary copies). They are not worth a snapshot, and the
        # snapshot would crowd out real ones.
        logger.info("The launch says this library is disposable; nothing to back up")
        return 0
    # The observatory database, at wayfindinglib's default location.
    extra_database_paths = (Path(data_directory()) / WAYFINDING_DATABASE_RELATIVE_PATH,)
    backup_root = arguments.backup_root or Path(backup_directory())
    state_path = backup_root / STATE_FILE_NAME
    dry_run = arguments.dry_run

    backup_root.mkdir(parents=True, exist_ok=True)
    lock_file = acquire_lock(backup_root / LOCK_FILE_NAME)
    if lock_file is None:
        logger.info("Another backup is already running; exiting")
        return 0
    try:
        # A folder left by a crashed run was never finished; remove it.
        for partial_folder in backup_root.glob("*.partial"):
            if not dry_run:
                shutil.rmtree(partial_folder, ignore_errors=True)

        fingerprint = compute_source_fingerprint(library_path, config_file_path, extra_database_paths)
        saved_state = read_state(state_path)
        last_snapshot_exists = (backup_root / str(saved_state.get("last_snapshot", ""))).is_dir()
        if not arguments.force and last_snapshot_exists and saved_state.get("fingerprint") == fingerprint:
            logger.info("Backup unchanged, skipped")
            return 0

        snapshot_name = time.strftime("%Y%m%d_%H%M%S")
        snapshot_folder = backup_root / snapshot_name
        if dry_run:
            print(f"[dry-run] would snapshot {library_path} into {snapshot_folder}")
            apply_retention(backup_root, snapshot_name, dry_run)
            return 0

        # Write into a ".partial" folder and rename when complete, so a
        # snapshot folder is always whole.
        partial_folder = backup_root / f"{snapshot_name}.partial"
        if not stage_snapshot(library_path, config_file_path, partial_folder, extra_database_paths):
            logger.error("A database snapshot failed; removing %s", partial_folder)
            shutil.rmtree(partial_folder, ignore_errors=True)
            return 1
        partial_folder.rename(snapshot_folder)
        state_path.write_text(
            json.dumps({
                "fingerprint": fingerprint,
                "last_snapshot": snapshot_name,
                "at": datetime.now().isoformat(),
            })
        )
        logger.info("Saved snapshot %s", snapshot_folder)
        apply_retention(backup_root, snapshot_name, dry_run)
        return 0
    finally:
        lock_file.close()


if __name__ == "__main__":
    sys.exit(main())
