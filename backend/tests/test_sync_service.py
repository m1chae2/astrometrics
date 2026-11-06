"""Purpose: Unit tests for the backend's thin SyncService.

Description: Copying frames and logs is `control.remote` in the
wayfinding library. The backend runs a frame sync on a thread, so
``telescope:is_syncing`` can follow it, and turns the library's log sync
reply into the shape the app shows.
"""

import threading
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from backend.services.infrastructure import thread_management
from backend.services.infrastructure.sync_service import SyncService


class _Remote:
    """A stand-in `control.remote` that records its calls."""

    def __init__(self) -> None:
        """Start with no calls; frame syncs wait until released."""
        self.synced: list[str] = []
        self.release = threading.Event()
        self.log_release = threading.Event()
        self.log_release.set()
        self.log_syncs = 0
        self.log_error: Exception | None = None
        self.samples = 42

    def sync_frames(self, target: str, on_copied: Callable[[int], None] | None = None) -> SimpleNamespace:
        """Record the target, report 2 copied frames and wait to be released.

        Returns
        -------
        result : `types.SimpleNamespace`
            A successful sync with no measurements.
        """
        self.synced.append(target)
        if on_copied is not None:
            on_copied(2)
        self.release.wait(timeout=5.0)
        return SimpleNamespace(success=True, measurements=None)

    def sync_logs(self, register_job: bool = True) -> SimpleNamespace:
        """Return a log sync reply.

        Returns
        -------
        result : `types.SimpleNamespace`
            Three new guide logs and 42 stored samples.
        """
        self.log_syncs += 1
        self.log_release.wait(timeout=5.0)
        if self.log_error is not None:
            raise self.log_error
        return SimpleNamespace(
            guide_logs=SimpleNamespace(supported=True, remote_files=5, to_download=3),
            ingested={"guide_samples_stored": self.samples, "nights": ["2026-11-05"]},
        )


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


def test_new_frames_are_announced_when_copied_not_when_the_sync_ends() -> None:
    """The UI event goes out from the copy callback, before the sync ends."""
    events: list[tuple[str, dict]] = []
    socket_manager = SimpleNamespace(
        broadcast_ui_event_sync=lambda action, payload: events.append((action, payload))
    )
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote), socket_manager=socket_manager)

    service.start_sync("M 31")
    thread_management._syncing["M 31"].join(timeout=0.5)
    assert service.is_syncing("M 31") is True
    assert events == [("library:frames_updated", {"targetId": "M 31", "framesAdded": 2})]

    remote.release.set()
    thread_management._syncing["M 31"].join(timeout=5.0)
    assert len(events) == 1


def test_two_starts_for_one_target_run_one_sync() -> None:
    """Two callers starting one target at once get one sync and one refusal."""
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))
    replies: list[dict] = []
    barrier = threading.Barrier(8)

    def start() -> None:
        """Wait for the others, then start the sync."""
        barrier.wait()
        replies.append(service.start_sync("M 99"))

    callers = [threading.Thread(target=start) for _ in range(8)]
    for caller in callers:
        caller.start()
    for caller in callers:
        caller.join(timeout=5.0)

    assert sum(1 for reply in replies if reply["started"]) == 1
    refused = [reply for reply in replies if not reply["started"]]
    assert len(refused) == 7
    assert refused[0] == {"started": False, "target_id": "M 99", "reason": "already running"}

    remote.release.set()
    thread_management._syncing["M 99"].join(timeout=5.0)
    assert remote.synced == ["M 99"]


def _wait_for_log_sync(service: SyncService) -> None:
    """Wait for the background log sync thread to end."""
    thread = service._log_sync_thread
    assert thread is not None
    thread.join(timeout=5.0)


def test_log_sync_is_single_flight_and_quiet_when_busy() -> None:
    """A second start while one runs does nothing and reports it."""
    remote = _Remote()
    remote.log_release.clear()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))
    finished: list[object] = []

    assert service.start_log_sync(on_finished=finished.append) == {"started": True}
    assert service.start_log_sync() == {"started": False, "reason": "already running"}

    remote.log_release.set()
    _wait_for_log_sync(service)
    assert remote.log_syncs == 1
    assert len(finished) == 1 and finished[0] is not None
    assert service.start_log_sync() == {"started": True}
    _wait_for_log_sync(service)


def test_failing_log_sync_warns_once_and_reports_none(caplog: pytest.LogCaptureFixture) -> None:
    """A failure is a warning, the listener gets None, and nothing raises."""
    remote = _Remote()
    remote.log_error = RuntimeError("network down")
    service = SyncService(observatory_api=SimpleNamespace(remote=remote))
    finished: list[object] = []

    service.start_log_sync(on_finished=finished.append)
    _wait_for_log_sync(service)

    assert finished == [None]
    assert [record.levelname for record in caplog.records] == ["WARNING"]


def test_new_guiding_samples_are_announced_with_their_nights() -> None:
    """The guiding event carries the nights; no samples, no event."""
    events: list[tuple[str, dict]] = []
    socket_manager = SimpleNamespace(
        broadcast_ui_event_sync=lambda action, payload: events.append((action, payload))
    )
    remote = _Remote()
    service = SyncService(observatory_api=SimpleNamespace(remote=remote), socket_manager=socket_manager)

    service.sync_telescope_logs()
    assert events == [("library:guiding_updated", {"nightIds": ["2026-11-05"], "samplesStored": 42})]

    remote.samples = 0
    service.sync_telescope_logs()
    assert len(events) == 1
