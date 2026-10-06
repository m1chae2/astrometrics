"""Tests for NotificationService's handling of a broken storage file.

The service catches only file and JSON errors. These tests check that a
corrupt notifications file is reported in the log instead of being
silently ignored, and that it never crashes the caller.
"""

import logging
from pathlib import Path

import pytest

from backend.services.infrastructure.notification_service import NotificationService


def test_notify_and_read_back(tmp_path: Path) -> None:
    """A stored notification comes back as unread."""
    service = NotificationService(str(tmp_path / "notes" / "notifications.json"))
    service.notify("M 31", "Stack finished", status="success")

    notifications = service.get_notifications()

    assert [n["message"] for n in notifications] == ["Stack finished"]


def test_corrupt_file_is_logged_not_raised(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    """A file that is not valid JSON is logged, and reads return nothing."""
    storage = tmp_path / "notifications.json"
    storage.write_text("{not json")
    service = NotificationService(str(storage))

    with caplog.at_level(logging.DEBUG, logger="backend.services.infrastructure.notification_service"):
        service.notify("M 31", "Stack finished")
        notifications = service.get_notifications()
        service.mark_as_read("M 31_0")

    assert notifications == []
    assert "Failed to write notification" in caplog.text
    assert "Could not read notifications" in caplog.text
    assert "Failed to mark notification" in caplog.text
