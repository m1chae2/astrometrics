"""Purpose: Tests for the local library backup and restore scripts.

Description: Uses small SQLite files in tmp_path. Checks that nothing is
uploaded (any subprocess call fails the test), that a lock stops a second run,
that an unchanged library is skipped, that only the newest snapshots are kept,
and that the SQLite copy is valid. Never touches the real library.
"""

import sqlite3
from pathlib import Path

import pytest

from astrometricslib.scripts import backup_library_databases as backup
from astrometricslib.scripts import restore_library_databases as restore


@pytest.fixture
def environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict:
    """Build a fake library and config file, and forbid any subprocess call.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Pytest temporary folder.
    monkeypatch : `pytest.MonkeyPatch`
        Used to make every subprocess call fail the test.

    Returns
    -------
    environment : `dict`
        Paths and the common command-line arguments.
    """

    def forbidden(*_args: object, **_kwargs: object) -> None:
        """Fail the test: the backup must not start any program."""
        pytest.fail("the backup started a subprocess")

    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("subprocess.Popen", forbidden)
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
    backup_root = tmp_path / "backup"
    # Point the observatory database at a folder that has none.
    monkeypatch.setattr(backup, "data_directory", lambda: str(tmp_path / "data"))
    return {
        "library": library,
        "backup_root": backup_root,
        "arguments": [
            "--library-path", str(library),
            "--config-path", str(config),
            "--backup-root", str(backup_root),
        ],
    }  # fmt: skip


def _snapshots(environment: dict) -> list[str]:
    """List the snapshot folders.

    Parameters
    ----------
    environment : `dict`
        The fixture value.

    Returns
    -------
    names : `list` [`str`]
        Sorted snapshot folder names.
    """
    root = environment["backup_root"]
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def test_backup_writes_a_local_snapshot_and_no_upload(environment: dict) -> None:
    """A first run stores all files locally and starts no program."""
    assert backup.main(environment["arguments"]) == 0

    names = _snapshots(environment)
    assert len(names) == 1
    stored = {path.name for path in (environment["backup_root"] / names[0]).iterdir()}
    assert stored == {
        "astrometrics.db",
        "astrometrics_log.db",
        "calibration_frames.json",
        "astrometrics.config.toml",
    }


def test_sqlite_copy_is_valid(environment: dict) -> None:
    """The copied database opens and holds the rows of the original."""
    backup.main(environment["arguments"])

    copy = environment["backup_root"] / _snapshots(environment)[0] / "astrometrics.db"
    connection = sqlite3.connect(copy)
    try:
        assert connection.execute("SELECT x FROM t").fetchall() == [(1,)]
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()


def test_lock_stops_a_second_run(environment: dict) -> None:
    """While one backup holds the lock, another exits and makes nothing."""
    environment["backup_root"].mkdir()
    held = backup.acquire_lock(environment["backup_root"] / backup.LOCK_FILE_NAME)
    assert held is not None
    try:
        assert backup.acquire_lock(environment["backup_root"] / backup.LOCK_FILE_NAME) is None
        assert backup.main(environment["arguments"]) == 0
        assert _snapshots(environment) == []
    finally:
        held.close()
    # Once released, a run goes ahead.
    assert backup.main(environment["arguments"]) == 0
    assert len(_snapshots(environment)) == 1


def test_unchanged_run_is_skipped_and_force_overrides(
    environment: dict, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An unchanged library is skipped; a change or --force snapshots."""
    names = iter(["20261011_100000", "20261011_110000", "20261011_120000", "20261011_130000"])
    monkeypatch.setattr(backup.time, "strftime", lambda _format: next(names))
    backup.main(environment["arguments"])
    with caplog.at_level("INFO"):
        backup.main(environment["arguments"])
    assert "unchanged, skipped" in caplog.text
    assert len(_snapshots(environment)) == 1

    (environment["library"] / "calibration_frames.json").write_text('{"changed": true}')
    backup.main(environment["arguments"])
    assert len(_snapshots(environment)) == 2

    backup.main([*environment["arguments"], "--force"])
    assert len(_snapshots(environment)) == 3


def test_retention_keeps_newest_and_never_the_protected(environment: dict) -> None:
    """Only the newest snapshots stay; a protected one is never deleted."""
    names = [f"202610{day:02d}_000000" for day in range(1, 11)]

    assert backup.snapshots_to_delete(names) == names[: -backup.KEEP_SNAPSHOTS]
    assert names[0] not in backup.snapshots_to_delete(names, protected_name=names[0])

    environment["backup_root"].mkdir()
    for name in names:
        (environment["backup_root"] / name).mkdir()
        (environment["backup_root"] / name / "file").write_text("x" * 10)
    freed = backup.apply_retention(environment["backup_root"], None, dry_run=False)
    assert _snapshots(environment) == names[-backup.KEEP_SNAPSHOTS :]
    assert freed == 10 * (len(names) - backup.KEEP_SNAPSHOTS)


def test_dry_run_changes_nothing(environment: dict, capsys: pytest.CaptureFixture) -> None:
    """--dry-run prints actions and makes no snapshot."""
    assert backup.main([*environment["arguments"], "--dry-run"]) == 0

    assert "[dry-run]" in capsys.readouterr().out
    assert _snapshots(environment) == []


def test_restore_lists_and_copies_without_touching_library(
    environment: dict, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """Restore lists local snapshots and copies one out and checks it."""
    backup.main(environment["arguments"])
    name = _snapshots(environment)[0]
    common = ["--backup-root", str(environment["backup_root"])]

    assert restore.main(["--list", *common]) == 0
    assert name in capsys.readouterr().out

    destination = tmp_path / "restored"
    assert restore.main(["--snapshot", name, "--destination", str(destination), *common]) == 0
    assert "astrometrics.db: integrity ok" in capsys.readouterr().out
    assert (destination / "astrometrics.db").exists()


def test_restore_refuses_a_non_empty_destination(environment: dict, tmp_path: Path) -> None:
    """A destination that already holds files is rejected."""
    backup.main(environment["arguments"])
    name = _snapshots(environment)[0]
    destination = tmp_path / "occupied"
    destination.mkdir()
    (destination / "file").write_text("x")

    code = restore.main([
        "--snapshot", name, "--destination", str(destination),
        "--backup-root", str(environment["backup_root"]),
    ])  # fmt: skip

    assert code == 2
