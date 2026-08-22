"""NotificationService: a notification queue for background tasks.

Used to bridge the gap between long-running backend jobs and the AI
Agent.
"""

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class NotificationService:
    """Track and retrieve notifications about job completions."""

    def __init__(self, storage_path: str = "backend/notifications.json"):  # ruff: ignore[missing-return-type-special-method]
        self.storage_path = storage_path
        self._ensure_storage()

    def _ensure_storage(self):  # ruff: ignore[missing-return-type-private-function]
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)
        if not os.path.exists(self.storage_path):
            with open(self.storage_path, "w") as f:
                json.dump([], f)

    def notify(self, target_id: str, message: str, status: str = "info"):  # ruff: ignore[missing-return-type-undocumented-public-function]
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
            with open(self.storage_path, "r+") as f:
                data = json.load(f)
                data.append(notification)
                # Keep only last 50 notifications
                if len(data) > 50:
                    data = data[-50:]
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"Failed to write notification: {e}")

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
            with open(self.storage_path) as f:
                data = json.load(f)
                if unread_only:
                    return [n for n in data if not n.get("read")]
                return data
        except Exception:
            return []

    def mark_as_read(self, notification_id: str):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Mark a specific notification as read."""
        try:
            with open(self.storage_path, "r+") as f:
                data = json.load(f)
                for n in data:
                    if n["id"] == notification_id:
                        n["read"] = True
                f.seek(0)
                f.truncate()
                json.dump(data, f, indent=2)
        except Exception as exc:
            logger.debug("Failed to mark notification '%s' as read: %s", notification_id, exc)
