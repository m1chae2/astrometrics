"""NotificationService: a notification queue for background tasks.

Used to bridge the gap between long-running backend jobs and the AI
Agent.

The queue lives in one JSON file. Only the backend process writes it, so one
lock in this process keeps the writers in line. Each write goes to a temporary
file first and then replaces the real file, so a crash cannot leave half a
file. A file that cannot be read is moved aside, never deleted, and a fresh
queue starts.
"""

import json
import logging
import os
import tempfile
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

#: The most notifications kept; older ones are dropped.
MAX_NOTIFICATIONS = 50


class NotificationService:
    """Track and retrieve notifications about job completions."""

    def __init__(self, storage_path: str = "backend/notifications.json") -> None:
        self.storage_path = storage_path
        self._lock = threading.Lock()
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Create the storage folder and an empty queue file if needed."""
        os.makedirs(os.path.dirname(os.path.abspath(self.storage_path)), exist_ok=True)
        if not os.path.exists(self.storage_path):
            with self._lock:
                self._write([])

    def _write(self, data: list[dict[str, Any]]) -> None:
        """Replace the queue file with `data` in one step.

        The data goes to a temporary file in the same folder, which then
        replaces the real file. The caller must hold the lock.

        Parameters
        ----------
        data : `list` [`dict`]
            The notifications to store.
        """
        folder = os.path.dirname(os.path.abspath(self.storage_path))
        file_descriptor, temporary_path = tempfile.mkstemp(
            dir=folder, prefix=".notifications-", suffix=".tmp"
        )
        try:
            with os.fdopen(file_descriptor, "w") as handle:
                json.dump(data, handle, indent=2)
            os.replace(temporary_path, self.storage_path)
        except BaseException:
            try:
                os.unlink(temporary_path)
            except OSError:
                pass
            raise

    def _read(self) -> list[dict[str, Any]]:
        """Read the queue, setting aside a file that is not valid.

        The caller must hold the lock. An unreadable file is renamed to
        ``<name>.corrupt-<timestamp>``, one warning names the copy, and a
        new empty queue file is started.

        Returns
        -------
        data : `list` [`dict`]
            The stored notifications.
        """
        with open(self.storage_path, "rb") as handle:
            raw = handle.read()
        problem = ""
        data: Any = None
        try:
            # Bad UTF-8 raises UnicodeDecodeError, a ValueError.
            data = json.loads(raw.decode("utf-8"))
        except ValueError as exc:
            problem = str(exc)
        else:
            if not isinstance(data, list):
                problem = "the top level is not a list"
        if not problem:
            return data
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup_path = f"{self.storage_path}.corrupt-{stamp}"
        suffix = 1
        while os.path.exists(backup_path):
            # Never overwrite an earlier backup.
            backup_path = f"{self.storage_path}.corrupt-{stamp}-{suffix}"
            suffix += 1
        os.replace(self.storage_path, backup_path)
        logger.warning(
            "Notification file %s could not be read (%s); it was moved to %s and a new one started.",
            self.storage_path,
            problem,
            backup_path,
        )
        self._write([])
        return []

    def notify(self, target_id: str, message: str, status: str = "info") -> None:
        """Add a notification to the queue."""
        notification = {
            "id": f"{target_id}_{int(time.time())}",
            "target_id": target_id,
            "message": message,
            "status": status,
            "timestamp": time.time(),
            "read": False,
        }

        try:
            with self._lock:
                data = self._read() if os.path.exists(self.storage_path) else []
                data.append(notification)
                self._write(data[-MAX_NOTIFICATIONS:])
        except OSError:
            logger.exception("Failed to write notification")

    def get_notifications(self, unread_only: bool = True) -> list[dict[str, Any]]:
        """Retrieve notifications.

        Returns
        -------
        notifications : `list` [`dict`]
            All stored notifications, or only unread ones when
            `unread_only` is `True`. Returns an empty list on
            failure to read the storage file.
        """
        try:
            with self._lock:
                data = self._read()
        except OSError as exc:
            logger.warning("Could not read notifications from %s: %s", self.storage_path, exc)
            return []
        if unread_only:
            return [n for n in data if not n.get("read")]
        return data

    def mark_as_read(self, notification_id: str) -> None:
        """Mark a specific notification as read."""
        try:
            with self._lock:
                data = self._read()
                for n in data:
                    if n.get("id") == notification_id:
                        n["read"] = True
                self._write(data)
        except OSError as exc:
            logger.debug("Failed to mark notification '%s' as read: %s", notification_id, exc)
