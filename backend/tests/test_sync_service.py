"""Purpose: Unit tests for the backend's thin SyncService.

Description: Copying frames and logs is `control.remote` in the
wayfinding library. The backend runs a frame sync on a thread, so
``telescope:is_syncing`` can follow it, and turns the library's log sync
reply into the shape the app shows.
"""

import threading
from types import SimpleNamespace
from typing import Any

from backend.services.infrastructure import thread_management
from backend.services.infrastructure.sync_service import SyncService


class _Remote:
    """A stand-in `control.remote` that records its calls."""

    def __init__(self) -> None:
        """Start with no calls; frame syncs wait until released."""
        self.synced: list[str] = []
        self.release = threading.Event()

    def sync_frames(self, target: str) -> dict[str, Any]:
        """Record the target and wait to be released.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            A successful sync.
        """
        self.synced.append(target)
        self.release.wait(timeout=5.0)
        return {"success": True}

    def sync_logs(self, register_job: bool = True) -> dict[str, Any]:
        """Return a log sync reply.

        Returns
        -------
        result : `dict` [`str`, `Any`]
            Three new guide logs and 42 stored samples.
        """
        return {
            "guide_logs": {"supported": True, "remote_files": 5, "to_download": 3},
            "ingested": {"guide_samples_stored": 42},
        }


def test_start_sync_runs_the_library_sync_on_a_thread() -> None:
    """The sync runs in the background and `is_syncing` follows it."""
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))

    assert service.start_sync("M 42") == {"started": True, "target_id": "M 42"}
    assert service.is_syncing("M 42") is True
    assert "M 42" in service.get_all_active_syncs()

    remote.release.set()
    thread_management._syncing["M 42"].join(timeout=5.0)
    assert remote.synced == ["M 42"]
    assert service.is_syncing("M 42") is False


def test_the_log_sync_reply_keeps_the_apps_shape() -> None:
    """The library's reply becomes the counts the app shows."""
    service = SyncService(observatory_api=SimpleNamespace(remote=_Remote()))

    reply = service.sync_telescope_logs()

    assert reply["status"] == "success"
    assert reply["guideLogsDownloaded"] == 3
    assert reply["guidingSamplesIngested"] == 42
    assert reply["fitsSolvesRecorded"] == 0
