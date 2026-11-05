"""Tests for NotificationService storage.

These tests check that concurrent writers keep the file valid, that a write
never leaves a partial file, and that a corrupt file is moved aside with one
warning instead of breaking every later notification.
"""

import json
import logging
import threading
from pathlib import Path

import pytest

from backend.services.infrastructure.notification_service import NotificationService


def test_notify_and_read_back(tmp_path: Path) -> None:
    """A stored notification comes back as unread."""
    service = NotificationService(str(tmp_path / "notes" / "notifications.json"))
    service.notify("M 31", "Stack finished", status="success")

    notifications = service.get_notifications()

    assert [n["message"] for n in notifications] == ["Stack finished"]


def test_corrupt_file_is_backed_up_and_replaced(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A file that is not valid JSON is backed up and replaced."""
    storage = tmp_path / "notifications.json"
    storage.write_text('[{"id": "a"}]\n{extra data')
    service = NotificationService(str(storage))

    with caplog.at_level(logging.DEBUG, logger="backend.services.infrastructure.notification_service"):
        service.notify("M 31", "Stack finished")
        notifications = service.get_notifications()
        service.mark_as_read("M 31_0")

    backups = list(tmp_path.glob("notifications.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text() == '[{"id": "a"}]\n{extra data'
    assert [n["message"] for n in notifications] == ["Stack finished"]
    assert caplog.text.count("could not be read") == 1
    assert backups[0].name in caplog.text


def test_reads_tolerate_a_corrupt_file(tmp_path: Path) -> None:
    """Reads on a corrupt file return nothing and set it aside."""
    storage = tmp_path / "notifications.json"
    storage.write_text("{not json")
    service = NotificationService(str(storage))

    assert service.get_notifications() == []
    service.mark_as_read("x")

    assert len(list(tmp_path.glob("notifications.json.corrupt-*"))) == 1
    assert json.loads(storage.read_text()) == []


def test_concurrent_notify_keeps_valid_json_and_all_entries(tmp_path: Path) -> None:
    """Many threads writing at once leave a valid file holding every entry."""
    storage = tmp_path / "notifications.json"
    service = NotificationService(str(storage))

    threads = [threading.Thread(target=service.notify, args=(f"T{i}", f"m{i}")) for i in range(30)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    stored = json.loads(storage.read_text())
    assert len(stored) == 30
    assert {n["message"] for n in stored} == {f"m{i}" for i in range(30)}
    assert not list(tmp_path.glob("notifications.json.corrupt-*"))


def test_queue_is_capped_at_fifty(tmp_path: Path) -> None:
    """Only the newest fifty notifications are kept."""
    service = NotificationService(str(tmp_path / "notifications.json"))
    for i in range(60):
        service.notify("T", f"m{i}")

    messages = [n["message"] for n in service.get_notifications(unread_only=False)]

    assert messages == [f"m{i}" for i in range(10, 60)]


def test_write_leaves_no_temporary_file_and_failure_keeps_old_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A write that fails midway keeps the old file and leaves no strays."""
    storage = tmp_path / "notifications.json"
    service = NotificationService(str(storage))
    service.notify("T", "first")
    assert sorted(path.name for path in tmp_path.iterdir()) == ["notifications.json"]

    def broken_replace(source: str, destination: str) -> None:
        """Fail like a full disk.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("disk full")

    monkeypatch.setattr("backend.services.infrastructure.notification_service.os.replace", broken_replace)
    service.notify("T", "second")

    assert sorted(path.name for path in tmp_path.iterdir()) == ["notifications.json"]
    assert [n["message"] for n in json.loads(storage.read_text())] == ["first"]
