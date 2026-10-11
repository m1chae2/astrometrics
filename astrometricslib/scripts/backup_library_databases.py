r"""Back up the library databases and settings to Google Drive with rclone.

The script makes a safe copy of ``astrometrics.db`` and ``astrometrics_log.db``
(SQLite's online backup, then an integrity check), adds
``calibration_frames.json`` and the configuration file, and uploads the folder
to ``drive:Library/_backup/<YYYYmmdd_HHMMSS>/``. It works whether or not the
backend is running.

It does nothing when none of those files changed since the last good upload.
A folder whose upload failed stays on disk and is uploaded first on the next
run. On Drive it keeps the 7 newest snapshots plus the newest snapshot of each
calendar month, and deletes the rest.

Run::

    python -m astrometricslib.scripts.backup_library_databases

    python -m astrometricslib.scripts.backup_library_databases --dry-run
"""

import argparse
import json
import logging
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from astrometricslib.drivers.local_database import snapshot_sqlite_database
from astrometricslib.foundation.paths import remote_snapshot_path, scratch_directory

logger = logging.getLogger(__name__)

DATABASE_FILE_NAMES = ("astrometrics.db", "astrometrics_log.db")
CALIBRATION_FILE_NAME = "calibration_frames.json"
STATE_FILE_NAME = "state.json"
SNAPSHOT_NAME_PATTERN = re.compile(r"^\d{8}_\d{6}$")
NEWEST_SNAPSHOTS_TO_KEEP = 7


def compute_source_fingerprint(
    library_path: Path, config_file_path: Path | None
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


def stage_snapshot(library_path: Path, config_file_path: Path | None, staging_folder: Path) -> bool:
    """Write the safe copies and plain file copies into a staging folder.

    Parameters
    ----------
    library_path : `pathlib.Path`
        Folder holding the databases and ``calibration_frames.json``.
    config_file_path : `pathlib.Path` or `None`
        The configuration file, if there is one.
    staging_folder : `pathlib.Path`
        Folder to fill. It is created.

    Returns
    -------
    ok : `bool`
        `True` if every database that exists was copied and passed its
        integrity check.
    """
    staging_folder.mkdir(parents=True, exist_ok=True)
    all_ok = True
    for database_name in DATABASE_FILE_NAMES:
        source_path = library_path / database_name
        if not source_path.exists():
            logger.warning("%s does not exist; skipping it", source_path)
            continue
        if not snapshot_sqlite_database(str(source_path), str(staging_folder / database_name)):
            logger.error("Snapshot of %s failed", source_path)
            all_ok = False
    for plain_file in (library_path / CALIBRATION_FILE_NAME, config_file_path):
        if plain_file is not None and plain_file.exists():
            shutil.copy2(plain_file, staging_folder / plain_file.name)
    return all_ok


def run_rclone(rclone_command: str, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    """Run rclone and capture its output.

    Parameters
    ----------
    rclone_command : `str`
        The rclone executable.
    arguments : `list` [`str`]
        Arguments after the executable.

    Returns
    -------
    result : `subprocess.CompletedProcess`
        The finished process (the caller checks ``returncode``).
    """
    return subprocess.run([rclone_command, *arguments], capture_output=True, text=True, check=False)


def upload_staging_folder(staging_folder: Path, rclone_command: str, remote: str) -> bool:
    """Upload one staging folder with ``rclone copy``.

    Parameters
    ----------
    staging_folder : `pathlib.Path`
        Folder named by its timestamp.
    rclone_command : `str`
        The rclone executable.
    remote : `str`
        Remote name including the colon.

    Returns
    -------
    uploaded : `bool`
        `True` if rclone exited with status 0.
    """
    result = run_rclone(
        rclone_command, ["copy", str(staging_folder), remote_snapshot_path(remote, staging_folder.name) + "/"]
    )
    if result.returncode != 0:
        logger.error("rclone copy of %s failed: %s", staging_folder, result.stderr.strip())
    return result.returncode == 0


def snapshots_to_delete(snapshot_names: list[str]) -> list[str]:
    """Pick the snapshots that retention removes.

    Keeps the newest 7 plus the newest snapshot of each calendar month.

    Parameters
    ----------
    snapshot_names : `list` [`str`]
        Snapshot folder names (``YYYYmmdd_HHMMSS``). Others are ignored.

    Returns
    -------
    names_to_delete : `list` [`str`]
        Names that fall outside both rules, oldest first.
    """
    valid_names = sorted(name for name in snapshot_names if SNAPSHOT_NAME_PATTERN.match(name))
    keep = set(valid_names[-NEWEST_SNAPSHOTS_TO_KEEP:])
    newest_per_month: dict[str, str] = {}
    for name in valid_names:
        newest_per_month[name[:6]] = name
    keep.update(newest_per_month.values())
    return [name for name in valid_names if name not in keep]


def apply_retention(rclone_command: str, remote: str, dry_run: bool) -> None:
    """Delete old snapshots on the remote.

    Parameters
    ----------
    rclone_command : `str`
        The rclone executable.
    remote : `str`
        Remote name including the colon.
    dry_run : `bool`
        If `True`, only print what would be deleted.
    """
    listing = run_rclone(rclone_command, ["lsf", "--dirs-only", remote_snapshot_path(remote) + "/"])
    if listing.returncode != 0:
        logger.warning("Could not list remote snapshots; skipping retention: %s", listing.stderr.strip())
        return
    snapshot_names = [line.strip().rstrip("/") for line in listing.stdout.splitlines() if line.strip()]
    for name in snapshots_to_delete(snapshot_names):
        if dry_run:
            print(f"[dry-run] would purge {remote_snapshot_path(remote, name)}")
            continue
        purge = run_rclone(rclone_command, ["purge", remote_snapshot_path(remote, name)])
        if purge.returncode != 0:
            logger.warning("Could not purge %s: %s", name, purge.stderr.strip())
        else:
            logger.info("Purged old snapshot %s", name)


def read_state(state_path: Path) -> dict:
    """Read the saved record of the last good upload.

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
    parser.add_argument("--rclone", default="rclone", help="The rclone executable. Default: rclone.")
    parser.add_argument("--remote", default="drive:", help="The rclone remote, with colon. Default: drive:.")
    parser.add_argument(
        "--library-path", type=Path, default=None, help="Library folder. Default: from the config."
    )
    parser.add_argument(
        "--config-path", type=Path, default=None, help="Config file. Default: the one in use."
    )
    parser.add_argument(
        "--staging-root", type=Path, default=None, help="Staging folder. Default: scratch/backup."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Back up the databases and upload them, then apply retention.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. Default: ``sys.argv[1:]``.

    Returns
    -------
    exit_code : `int`
        0 on success or when nothing changed, 1 if a snapshot or upload failed.
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
    staging_root = arguments.staging_root or Path(scratch_directory()) / "backup"
    state_path = staging_root / STATE_FILE_NAME
    dry_run = arguments.dry_run
    exit_code = 0

    if not dry_run:
        staging_root.mkdir(parents=True, exist_ok=True)

    # Retry folders left by an earlier failed upload, oldest first.
    leftover_folders = (
        sorted(
            folder
            for folder in staging_root.iterdir()
            if folder.is_dir() and SNAPSHOT_NAME_PATTERN.match(folder.name)
        )
        if staging_root.exists()
        else []
    )
    for leftover_folder in leftover_folders:
        if dry_run:
            print(f"[dry-run] would upload leftover {leftover_folder}")
        elif upload_staging_folder(leftover_folder, arguments.rclone, arguments.remote):
            shutil.rmtree(leftover_folder)
        else:
            exit_code = 1

    fingerprint = compute_source_fingerprint(library_path, config_file_path)
    if not arguments.force and read_state(state_path).get("fingerprint") == fingerprint:
        logger.info("Backup unchanged since the last upload; nothing to do")
        return exit_code

    snapshot_name = time.strftime("%Y%m%d_%H%M%S")
    staging_folder = staging_root / snapshot_name
    if dry_run:
        print(f"[dry-run] would snapshot {library_path} into {staging_folder}")
        print(f"[dry-run] would upload to {remote_snapshot_path(arguments.remote, snapshot_name)}/")
    else:
        if not stage_snapshot(library_path, config_file_path, staging_folder):
            logger.error("A database snapshot failed; not uploading %s", staging_folder)
            shutil.rmtree(staging_folder, ignore_errors=True)
            return 1
        if not upload_staging_folder(staging_folder, arguments.rclone, arguments.remote):
            return 1
        shutil.rmtree(staging_folder)
        state_path.write_text(
            json.dumps({
                "fingerprint": fingerprint,
                "last_snapshot": snapshot_name,
                "at": datetime.now().isoformat(),
            })
        )
        logger.info("Uploaded snapshot %s", snapshot_name)

    apply_retention(arguments.rclone, arguments.remote, dry_run)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
