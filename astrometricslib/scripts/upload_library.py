r"""Copy changes in the local image library up to its backup on Google Drive.

The local library (the ``frames_path`` setting: raw frames, calibration
frames, stacks and previews) is the working copy. Google Drive holds the
backup. This script makes the Drive copy match the local one with
``rclone sync``, with four safety measures:

* A file deleted or replaced locally is not lost on Drive. It moves into
  ``drive:Library_deleted/<YYYYmmdd_HHMMSS>/`` instead (``--backup-dir``).
* A file renamed or moved locally, for example a frame the stacking step
  quarantines into ``_excluded``, is moved on Drive too, not uploaded again
  (``--track-renames``, which matches files by their MD5 checksum).
* One run deletes at most ``--max-delete`` files (default 100). A larger
  deletion stops the run with an error and a desktop notification.
* The run is refused when the local library is missing, or when it holds
  fewer than half the files it held after the last good run. A disk that
  failed to mount, or a folder deleted by mistake, would otherwise empty the
  Drive copy.

The database backups in ``drive:Library/_backup`` are never touched: that
folder is excluded, so the sync neither uploads into it nor deletes from it.
Hidden files and half-written temporary files (rsync's ``.name.XXXXXX``,
``*.partial``, rclone's ``*.partial`` and ``~tmp~`` files) are skipped.

A file lock stops two runs at once (the watcher and the timer can both start
one). A second run waits for the first to finish.

Run::

    python -m astrometricslib.scripts.upload_library

    python -m astrometricslib.scripts.upload_library --dry-run

Exit codes: 0 the Drive copy matches, 1 rclone failed, 2 the run was refused
for safety.
"""

import argparse
import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from astrometricslib.foundation.config import get_configuration
from astrometricslib.foundation.paths import locks_directory, scratch_directory

logger = logging.getLogger(__name__)

# The most files one run may delete (or replace) on Drive. A night of frame
# quarantine moves files, it does not delete them, so normal work stays far
# below this. Chosen as a round number, not measured; raise it with
# --max-delete for a deliberate large clean-up.
DEFAULT_MAX_DELETE = 100

# A run is refused when the library holds fewer than this share of the files
# it held after the last good run. One half allows a large deliberate
# clean-up but stops an empty or half-missing folder. Not tuned.
MINIMUM_FILE_SHARE = 0.5

DEFAULT_REMOTE = "drive:Library"
DEFAULT_DELETED_REMOTE = "drive:Library_deleted"

# Paths the sync ignores on both sides. Excluded files on Drive are never
# deleted. `/_backup/**` holds the database snapshots, which exist only on
# Drive. The others are hidden files and the temporary names rsync, Siril and
# rclone use while a file is still being written.
EXCLUDE_PATTERNS = (
    "/_backup/**",
    ".*",
    ".*/**",
    "*.partial",
    "*~tmp~*",
)

STATE_FILE_NAME = "upload_state.json"


def count_library_files(library_path: Path) -> int:
    """Count the files the sync would consider, skipping hidden ones.

    Parameters
    ----------
    library_path : `pathlib.Path`
        Root of the local library.

    Returns
    -------
    file_count : `int`
        Number of regular files not inside a hidden folder and not hidden.
    """
    file_count = 0
    for folder, sub_folders, file_names in os.walk(library_path):
        sub_folders[:] = [name for name in sub_folders if not name.startswith(".")]
        if os.path.relpath(folder, library_path).startswith("_backup"):
            continue
        file_count += sum(1 for name in file_names if not name.startswith("."))
    return file_count


def check_library_is_safe(library_path: Path, file_count: int, state: dict) -> str | None:
    """Decide whether syncing this library to Drive is safe.

    Parameters
    ----------
    library_path : `pathlib.Path`
        Root of the local library.
    file_count : `int`
        Files in the library now (see `count_library_files`).
    state : `dict`
        What the last good run recorded; may be empty.

    Returns
    -------
    refusal : `str` or `None`
        Why the run must not go ahead, or `None` if it may.
    """
    if not library_path.is_dir():
        return f"The local library {library_path} is not a folder."
    if file_count == 0:
        return f"The local library {library_path} is empty."
    last_count = state.get("file_count")
    if isinstance(last_count, int) and last_count > 0 and file_count < MINIMUM_FILE_SHARE * last_count:
        return (
            f"The local library holds {file_count} files, fewer than half of the "
            f"{last_count} after the last good upload. Check the folder, then run "
            "again with --accept-file-count if the drop is deliberate."
        )
    return None


def build_sync_command(
    rclone_executable: str,
    library_path: Path,
    remote: str,
    deleted_folder: str,
    max_delete: int,
    dry_run: bool,
) -> list[str]:
    """Assemble the ``rclone sync`` command.

    Parameters
    ----------
    rclone_executable : `str`
        The rclone program.
    library_path : `pathlib.Path`
        Root of the local library (the source).
    remote : `str`
        The Drive copy (the destination), for example ``drive:Library``.
    deleted_folder : `str`
        Where this run moves files it deletes or replaces on Drive.
    max_delete : `int`
        The most files the run may delete.
    dry_run : `bool`
        If `True`, rclone only reports what it would do.

    Returns
    -------
    command : `list` [`str`]
        The program and its arguments.
    """
    command = [
        rclone_executable,
        "sync",
        str(library_path),
        remote,
        "--backup-dir",
        deleted_folder,
        "--track-renames",
        # Keep empty folders in step, so the sync never removes the empty
        # folders of the Drive copy's layout.
        "--create-empty-src-dirs",
        "--max-delete",
        str(max_delete),
        "--drive-skip-gdocs",
        "--drive-skip-shortcuts",
        # rclone's shared Google client ID has a per-minute request limit
        # that all its users share; on 2026-10-11 a sync hit it and only the
        # retry succeeded. --fast-list lists the whole library in a few large
        # requests instead of one per folder, and the retries wait it out.
        "--fast-list",
        "--retries",
        "5",
        "--retries-sleep",
        "30s",
        "--transfers",
        "8",
        "--checkers",
        "16",
        "--stats-one-line",
        "--stats",
        "0",
        "--log-level",
        "INFO",
    ]
    for pattern in EXCLUDE_PATTERNS:
        command += ["--exclude", pattern]
    if dry_run:
        command.append("--dry-run")
    return command


def read_state(state_path: Path) -> dict:
    """Read what the last good run recorded.

    Parameters
    ----------
    state_path : `pathlib.Path`
        The state file.

    Returns
    -------
    state : `dict`
        The recorded values, or an empty dictionary if there are none.
    """
    try:
        return json.loads(state_path.read_text())
    except OSError, ValueError:
        return {}


def send_notification(message: str) -> None:
    """Show a desktop notification, if ``notify-send`` is available.

    Parameters
    ----------
    message : `str`
        The text to show.
    """
    executable = shutil.which("notify-send")
    if executable is None:
        return
    try:
        subprocess.run([executable, "Astrometrics library upload", message], check=False, timeout=10)
    except OSError, subprocess.SubprocessError:
        logger.debug("Could not send the desktop notification.")


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="report what would change; change nothing")
    parser.add_argument("--library", type=Path, default=None, help="local library (default: frames_path)")
    parser.add_argument("--remote", default=DEFAULT_REMOTE, help="Drive copy (default: %(default)s)")
    parser.add_argument(
        "--deleted-remote", default=DEFAULT_DELETED_REMOTE, help="recycle folder (default: %(default)s)"
    )
    parser.add_argument(
        "--max-delete",
        type=int,
        default=DEFAULT_MAX_DELETE,
        help="most deletions in one run (default: %(default)s)",
    )
    parser.add_argument(
        "--accept-file-count", action="store_true", help="run even if the library shrank by more than half"
    )
    parser.add_argument("--rclone", default="rclone", help="the rclone program (default: %(default)s)")
    parser.add_argument("--state-directory", type=Path, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--no-notify", action="store_true", help="no desktop notification on failure")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run one upload of the local library to Drive.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; defaults to `sys.argv`.

    Returns
    -------
    exit_code : `int`
        0 on success, 1 if rclone failed, 2 if the run was refused.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    arguments = _build_argument_parser().parse_args(argv)
    library_path = arguments.library or Path(get_configuration().get_frames_path())
    state_directory = arguments.state_directory or Path(scratch_directory()) / "upload"
    state_directory.mkdir(parents=True, exist_ok=True)
    state_path = state_directory / STATE_FILE_NAME
    lock_path = Path(locks_directory()) / "library_upload.lock"

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        file_count = count_library_files(library_path)
        state = {} if arguments.accept_file_count else read_state(state_path)
        refusal = check_library_is_safe(library_path, file_count, state)
        if refusal:
            logger.error("Upload refused: %s", refusal)
            if not arguments.no_notify:
                send_notification(f"Upload refused: {refusal}")
            return 2

        run_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        command = build_sync_command(
            arguments.rclone,
            library_path,
            arguments.remote,
            f"{arguments.deleted_remote}/{run_name}",
            arguments.max_delete,
            arguments.dry_run,
        )
        logger.info("Uploading %s (%d files) to %s", library_path, file_count, arguments.remote)
        try:
            completed = subprocess.run(command, check=False)
        except OSError as error:
            logger.exception("Could not start rclone")
            if not arguments.no_notify:
                send_notification(f"Drive upload failed: could not start rclone ({error}).")
            return 1
        if completed.returncode != 0:
            logger.error("rclone sync failed with exit code %d", completed.returncode)
            if not arguments.no_notify:
                send_notification(
                    f"Drive upload failed (rclone exit code {completed.returncode}). "
                    "See: journalctl --user -u astrometrics-library-upload"
                )
            return 1
        if not arguments.dry_run:
            state_path.write_text(json.dumps({"file_count": file_count, "finished": run_name}))
        logger.info("Upload finished.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
