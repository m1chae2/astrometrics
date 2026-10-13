"""Purpose: Tests for the library upload script (local library -> Drive).

Description: The safety checks and the rclone command are tested directly.
The sync behaviour is tested with the real rclone program against two local
folders in tmp_path standing in for the library and the Drive copy (rclone
treats a local folder like any remote). Those tests are skipped when rclone
is not installed. Nothing touches the real library or Google Drive.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from astrometricslib.scripts import upload_library as upload

needs_rclone = pytest.mark.skipif(shutil.which("rclone") is None, reason="rclone is not installed")


def make_library(root: Path, file_names: list[str]) -> Path:
    """Create a small fake library.

    Parameters
    ----------
    root : `pathlib.Path`
        Folder to create the library in.
    file_names : `list` [`str`]
        Paths, relative to the library, of files to create.

    Returns
    -------
    library : `pathlib.Path`
        The library folder.
    """
    library = root / "library"
    for name in file_names:
        path = library / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(name.encode() * 50)
    library.mkdir(exist_ok=True)
    return library


def run_upload(library: Path, destination: Path, state_directory: Path, *extra: str) -> int:
    """Run the upload script against local folders.

    Parameters
    ----------
    library : `pathlib.Path`
        The fake library.
    destination : `pathlib.Path`
        Folder standing in for the Drive account root.
    state_directory : `pathlib.Path`
        Where the script keeps its state.
    *extra : `str`
        Further command-line arguments.

    Returns
    -------
    exit_code : `int`
        The script's exit code.
    """
    return upload.main([
        "--library",
        str(library),
        "--remote",
        str(destination / "Library"),
        "--deleted-remote",
        str(destination / "Library_deleted"),
        "--state-directory",
        str(state_directory),
        "--no-notify",
        *extra,
    ])


def test_the_sync_command_carries_every_safety_option() -> None:
    """The rclone command keeps deletions recoverable, limited and safe."""
    command = upload.build_sync_command(
        "rclone", Path("/lib"), "drive:Library", "drive:Library_deleted/x", 100, False
    )
    assert command[:4] == ["rclone", "sync", "/lib", "drive:Library"]
    assert command[command.index("--backup-dir") + 1] == "drive:Library_deleted/x"
    assert command[command.index("--max-delete") + 1] == "100"
    assert "--track-renames" in command
    assert "--create-empty-src-dirs" in command
    excludes = [command[i + 1] for i, value in enumerate(command) if value == "--exclude"]
    assert "/_backup/**" in excludes
    assert "--dry-run" not in command
    assert "--dry-run" in upload.build_sync_command("rclone", Path("/lib"), "r:", "r:d", 1, True)


def test_a_missing_library_is_refused(tmp_path: Path) -> None:
    """A library folder that does not exist must never be synced."""
    assert run_upload(tmp_path / "missing", tmp_path / "drive", tmp_path / "state") == 2


def test_an_empty_library_is_refused(tmp_path: Path) -> None:
    """An empty library would empty the Drive copy, so it is refused."""
    library = make_library(tmp_path, [])
    assert run_upload(library, tmp_path / "drive", tmp_path / "state") == 2


def test_a_library_that_shrank_by_more_than_half_is_refused(tmp_path: Path) -> None:
    """A big drop since the last good run is refused unless accepted."""
    library = make_library(tmp_path, ["lights/a.fits", "lights/b.fits"])
    state_directory = tmp_path / "state"
    state_directory.mkdir()
    (state_directory / upload.STATE_FILE_NAME).write_text(json.dumps({"file_count": 10}))
    assert run_upload(library, tmp_path / "drive", state_directory, "--rclone", "/nonexistent/rclone") == 2
    # Accepting the drop gets past the safety check (and then fails on the
    # missing rclone program, which shows the check was passed).
    assert (
        run_upload(
            library,
            tmp_path / "drive",
            state_directory,
            "--rclone",
            "/nonexistent/rclone",
            "--accept-file-count",
        )
        == 1
    )


def test_hidden_files_and_the_backup_folder_are_not_counted(tmp_path: Path) -> None:
    """The file count matches what the sync considers."""
    library = make_library(tmp_path, ["lights/a.fits", ".hidden", ".cache/x", "_backup/db"])
    assert upload.count_library_files(library) == 1


def test_a_missing_rclone_program_is_a_failure(tmp_path: Path) -> None:
    """If rclone cannot start, the run fails with exit code 1."""
    library = make_library(tmp_path, ["lights/a.fits"])
    assert run_upload(library, tmp_path / "drive", tmp_path / "state", "--rclone", "/nonexistent/rclone") == 1


@needs_rclone
def test_a_sync_uploads_changes_and_keeps_deleted_files(tmp_path: Path) -> None:
    """New files upload, deleted files are kept aside, backups stay."""
    library = make_library(tmp_path, ["lights/A/f1.fits", "lights/A/f2.fits", "lights/A/f3.fits"])
    (library / ".hidden").write_text("x")
    (library / "lights/A/half.fits.partial").write_text("x")
    destination = tmp_path / "drive"
    state_directory = tmp_path / "state"
    assert run_upload(library, destination, state_directory) == 0
    assert (destination / "Library/lights/A/f1.fits").is_file()
    assert not (destination / "Library/.hidden").exists()
    assert not (destination / "Library/lights/A/half.fits.partial").exists()

    backup_file = destination / "Library/_backup/20260101_000000/astrometrics.db"
    backup_file.parent.mkdir(parents=True)
    backup_file.write_text("snapshot")
    (library / "lights/A/f2.fits").unlink()
    (library / "lights/A/_excluded").mkdir()
    (library / "lights/A/f3.fits").rename(library / "lights/A/_excluded/f3.fits")

    assert run_upload(library, destination, state_directory) == 0
    assert not (destination / "Library/lights/A/f2.fits").exists()
    assert list(destination.glob("Library_deleted/*/lights/A/f2.fits"))
    assert (destination / "Library/lights/A/_excluded/f3.fits").is_file()
    assert backup_file.read_text() == "snapshot"


@needs_rclone
def test_too_many_deletions_stop_the_run(tmp_path: Path) -> None:
    """A run that would delete more than --max-delete files fails."""
    names = [f"lights/A/f{index}.fits" for index in range(6)]
    library = make_library(tmp_path, names)
    destination = tmp_path / "drive"
    state_directory = tmp_path / "state"
    assert run_upload(library, destination, state_directory) == 0
    for name in names[:4]:
        (library / name).unlink()
    assert run_upload(library, destination, state_directory, "--max-delete", "1", "--accept-file-count") == 1


def test_only_old_recycle_folders_with_run_names_are_chosen() -> None:
    """Folders older than the keeping period are chosen; others never are."""
    now = datetime(2026, 10, 11, 12, 0, 0)
    names = [
        "20260601_000000/",  # 132 days old: removed
        "20260714_120000/",  # 89 days old: kept
        "20261011_110000/",  # today: kept
        "notes/",  # not a run name: never removed
        "20261399_000000/",  # not a real date: never removed
    ]
    assert upload.recycle_folders_to_remove(names, now, 90) == ["20260601_000000"]


@needs_rclone
def test_a_good_run_removes_old_recycle_folders(tmp_path: Path) -> None:
    """After a good sync, recycle folders past the keeping time are removed."""
    library = make_library(tmp_path, ["lights/A/f1.fits"])
    destination = tmp_path / "drive"
    old_folder = destination / "Library_deleted/20200101_000000/lights/A"
    recent_folder = destination / f"Library_deleted/{datetime.now():%Y%m%d_%H%M%S}/lights/A"
    other_folder = destination / "Library_deleted/keep_me"
    for folder in (old_folder, recent_folder, other_folder):
        folder.mkdir(parents=True)
        (folder / "x.fits").write_text("x")

    assert run_upload(library, destination, tmp_path / "state", "--dry-run") == 0
    assert old_folder.exists()

    assert run_upload(library, destination, tmp_path / "state") == 0
    assert not (destination / "Library_deleted/20200101_000000").exists()
    assert recent_folder.exists()
    assert other_folder.exists()


@needs_rclone
def test_a_missing_recycle_area_is_not_an_error(tmp_path: Path) -> None:
    """Before anything was ever deleted there is no recycle area yet."""
    assert upload.prune_recycle_folders("rclone", str(tmp_path / "none"), 90, datetime.now(), False)
