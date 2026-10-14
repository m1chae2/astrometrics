"""Purpose: Tests for the library backup and restore scripts.

Description: Uses small SQLite files in tmp_path and a fake rclone (a Python
script that maps a fake remote onto a local folder). Never touches the real
library or Google Drive.
"""

import sqlite3
import stat
import sys
from pathlib import Path

import pytest

from astrometricslib.scripts import backup_library_databases as backup
from astrometricslib.scripts import restore_library_databases as restore

FAKE_RCLONE_SOURCE = """#!{python}
import os, shutil, sys
root = os.environ["FAKE_RCLONE_ROOT"]
def local(path):
    return os.path.join(root, path.split(":", 1)[1]) if ":" in path else path
command, rest = sys.argv[1], sys.argv[2:]
if os.environ.get("FAKE_RCLONE_FAIL") and command == "copy":
    sys.exit(3)
if command == "copy":
    shutil.copytree(local(rest[0]), local(rest[1]), dirs_exist_ok=True)
elif command == "lsf":
    folder = local(rest[-1])
    for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
        print(name + "/")
elif command == "purge":
    shutil.rmtree(local(rest[0]))
"""


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a fake library, config file, remote folder and fake rclone.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Pytest temporary folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to set the fake remote's root folder.

    Returns
    -------
    environment : `dict`
        Paths and the common command-line arguments.
    """
    library = tmp_path / "library"
    library.mkdir()
    for name in backup.DATABASE_FILE_NAMES:
        connection = sqlite3.connect(library / name)
        connection.execute("CREATE TABLE t (x)")
        connection.execute("INSERT INTO t VALUES (1)")
        connection.commit()
        connection.close()
    (library / "calibration_frames.json").write_text("{}")
    config = tmp_path / "astrometrics.config.toml"
    config.write_text("a = 1")
    remote_root = tmp_path / "remote"
    remote_root.mkdir()
    monkeypatch.setenv("FAKE_RCLONE_ROOT", str(remote_root))
    monkeypatch.delenv("FAKE_RCLONE_FAIL", raising=False)
    fake = tmp_path / "fake_rclone"
    fake.write_text(FAKE_RCLONE_SOURCE.format(python=sys.executable))
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    staging = tmp_path / "staging"
    return {
        "library": library,
        "remote_root": remote_root,
        "staging": staging,
        "arguments": [
            "--library-path", str(library),
            "--config-path", str(config),
            "--staging-root", str(staging),
            "--rclone", str(fake),
            "--remote", "fake:",
        ],
    }  # fmt: skip


def _remote_snapshots(environment: dict) -> list[str]:
    """List snapshot folders on the fake remote.

    Parameters
    ----------
    environment : `dict`
        The fixture value.

    Returns
    -------
    names : `list` [`str`]
        Sorted snapshot folder names.
    """
    folder = environment["remote_root"] / "Library" / "_backup"
    return sorted(path.name for path in folder.iterdir()) if folder.exists() else []


def test_backup_uploads_all_files_and_clears_staging(environment: dict) -> None:
    """A first run uploads databases, calibration file and config."""
    assert backup.main(environment["arguments"]) == 0

    names = _remote_snapshots(environment)
    assert len(names) == 1
    uploaded = {
        path.name for path in (environment["remote_root"] / "Library" / "_backup" / names[0]).iterdir()
    }
    assert uploaded == {
        "astrometrics.db",
        "astrometrics_log.db",
        "calibration_frames.json",
        "astrometrics.config.toml",
    }
    assert not [p for p in environment["staging"].iterdir() if p.is_dir()]


def test_unchanged_run_is_skipped_and_force_overrides(
    environment: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second run with no changes uploads nothing; a changed file does."""
    names = iter(["20261011_100000", "20261011_110000", "20261011_120000"])
    monkeypatch.setattr(backup.time, "strftime", lambda _format: next(names))
    backup.main(environment["arguments"])
    backup.main(environment["arguments"])
    assert len(_remote_snapshots(environment)) == 1

    (environment["library"] / "calibration_frames.json").write_text('{"changed": true}')
    backup.main(environment["arguments"])
    assert len(_remote_snapshots(environment)) == 2


def test_failed_upload_keeps_staging_and_is_retried(
    environment: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failing rclone keeps the folder; the next good run uploads it."""
    monkeypatch.setenv("FAKE_RCLONE_FAIL", "1")
    assert backup.main(environment["arguments"]) == 1
    assert len([p for p in environment["staging"].iterdir() if p.is_dir()]) == 1
    assert _remote_snapshots(environment) == []

    monkeypatch.delenv("FAKE_RCLONE_FAIL")
    assert backup.main(environment["arguments"]) == 0
    assert len(_remote_snapshots(environment)) >= 1
    assert not [p for p in environment["staging"].iterdir() if p.is_dir()]


def test_dry_run_changes_nothing(environment: dict, capsys: pytest.CaptureFixture) -> None:
    """--dry-run prints actions and writes no staging or remote files."""
    assert backup.main([*environment["arguments"], "--dry-run"]) == 0

    assert "[dry-run]" in capsys.readouterr().out
    assert not environment["staging"].exists()
    assert _remote_snapshots(environment) == []


def test_retention_keeps_seven_newest_plus_newest_per_month() -> None:
    """Old snapshots are deleted unless newest of their month."""
    names = ["20260101_000000", "20260102_000000", "20260201_000000"] + [
        f"202610{day:02d}_000000" for day in range(1, 11)
    ]

    assert backup.snapshots_to_delete(names) == [
        "20260101_000000",
        "20261001_000000",
        "20261002_000000",
        "20261003_000000",
    ]


def test_restore_lists_and_downloads_without_touching_library(
    environment: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Restore lists snapshots and downloads one, checking each database."""
    backup.main(environment["arguments"])
    name = _remote_snapshots(environment)[0]
    common = ["--rclone", environment["arguments"][7], "--remote", "fake:"]

    assert restore.main(["--list", *common]) == 0
    assert name in capsys.readouterr().out

    destination = tmp_path / "restored"
    assert restore.main(["--snapshot", name, "--destination", str(destination), *common]) == 0
    assert "astrometrics.db: integrity ok" in capsys.readouterr().out
    assert (destination / "astrometrics.db").exists()


def test_restore_refuses_a_non_empty_destination(environment: dict, tmp_path: Path) -> None:
    """A destination that already holds files is rejected."""
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "file").write_text("x")

    code = restore.main(["--snapshot", "20260101_000000", "--destination", str(destination)])

    assert code == 2
