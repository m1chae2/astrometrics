"""Tests for the scheduled library backup in the backend.

The backup script itself is tested in astrometricslib; these tests replace
``subprocess.run`` and check only when the service runs the script, how, and
how it reports the result. No real backup is made.
"""

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend.services.infrastructure import library_backup_service as backup_module
from backend.services.infrastructure.library_backup_service import BACKUP_MODULE, LibraryBackupService


class _Settings:
    """Stand-in configuration with a chosen backup folder."""

    def __init__(self, backup_path: Path | None) -> None:
        """Remember the backup folder to report."""
        self.backup_path = backup_path

    def get_backup_path(self) -> Path | None:
        """Give the backup folder.

        Returns
        -------
        backup_path : `pathlib.Path` or `None`
            The folder, or `None` for none.
        """
        return self.backup_path

    def get_project_root(self) -> Path:
        """Give a project folder.

        Returns
        -------
        root : `pathlib.Path`
            A fixed relative folder.
        """
        return Path("project")


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    """Record every subprocess.run call and answer success.

    Returns
    -------
    calls : `list` [`dict`]
        The arguments of each call.
    """
    recorded: list[dict] = []

    def fake_run(command: list[str], **keywords: object) -> SimpleNamespace:
        """Record the call; pretend the backup worked.

        Returns
        -------
        result : `types.SimpleNamespace`
            A finished process with exit code 0.
        """
        recorded.append({"command": command, **keywords})
        return SimpleNamespace(returncode=0, stdout="", stderr="Saved snapshot X")

    monkeypatch.setattr(backup_module.subprocess, "run", fake_run)
    monkeypatch.delattr(sys, "frozen", raising=False)
    return recorded


def test_nothing_runs_without_a_backup_folder(calls: list[dict]) -> None:
    """Without backup_path the service makes no backup (Linux uses a timer)."""
    assert LibraryBackupService(_Settings(None)).run_backup() is False
    assert calls == []


def test_the_backup_script_runs_with_this_python(calls: list[dict], tmp_path: Path) -> None:
    """With backup_path the script runs as its own process."""
    assert LibraryBackupService(_Settings(tmp_path / "drive")).run_backup() is True
    assert calls[0]["command"] == [sys.executable, "-m", BACKUP_MODULE]
    assert calls[0]["cwd"] == "project"
    assert calls[0]["env"]["PYTHONPATH"] == "project"


def test_a_failed_backup_is_a_warning_not_an_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed backup leaves the library alone; it is logged as a warning."""
    monkeypatch.setattr(
        backup_module.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout="", stderr="Cannot reach the backup folder"),
    )
    with caplog.at_level(logging.INFO, logger=backup_module.__name__):
        assert LibraryBackupService(_Settings(tmp_path / "drive")).run_backup() is False
    assert [record.levelname for record in caplog.records] == ["WARNING"]
    assert "Cannot reach the backup folder" in caplog.text


def test_a_backup_that_hangs_is_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A backup that runs past its time limit is stopped and reported."""

    def too_slow(command: list[str], **keywords: object) -> None:
        """Fail like a run that passed its time limit.

        Raises
        ------
        subprocess.TimeoutExpired
            Always.
        """
        raise backup_module.subprocess.TimeoutExpired(command, keywords["timeout"])

    monkeypatch.setattr(backup_module.subprocess, "run", too_slow)
    assert LibraryBackupService(_Settings(tmp_path / "drive")).run_backup(timeout_seconds=1) is False


def test_the_packaged_app_skips_the_backup(
    calls: list[dict], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaged backend has no Python to run the script with, so it skips."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert LibraryBackupService(_Settings(tmp_path / "drive")).run_backup() is False
    assert calls == []


def test_stop_makes_one_last_backup(calls: list[dict], tmp_path: Path) -> None:
    """Stopping the service backs up once more, with the shutdown limit."""
    service = LibraryBackupService(_Settings(tmp_path / "drive"))
    service.start()
    service.stop()
    assert len(calls) == 1
    assert calls[0]["timeout"] == backup_module.SHUTDOWN_BACKUP_TIMEOUT_SECONDS
