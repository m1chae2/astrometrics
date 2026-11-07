"""Purpose: Tests for the automatic download of finished camera frames.

Description: Checks `FrameEndDetector` on status sequences, and
`AutoDownloadService` with a fake sync service, a fake clock and fake timers
(no hardware, no ssh, no real waiting).
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from backend.services.observatory import auto_download_service as module
from backend.services.observatory.auto_download_service import AutoDownloadService, FrameEndDetector


def _ends(statuses: list[str | None]) -> list[bool]:
    """Feed `statuses` to a fresh detector.

    Returns
    -------
    ends : `list` [`bool`]
        Whether each status showed a frame end.
    """
    detector = FrameEndDetector()
    return [detector.update(status) for status in statuses]


def test_exposing_downloading_idle_ends_once() -> None:
    """A normal frame ends when the camera goes back to Idle."""
    assert _ends(["Exposing (3s)", "Exposing (1s)", "Downloading", "Idle", "Idle"]) == [
        False,
        False,
        False,
        True,
        False,
    ]


def test_back_to_back_frames() -> None:
    """The next frame starting at once, with or without Downloading seen."""
    assert _ends(["Exposing (2s)", "Exposing (60s)"]) == [False, True]
    assert _ends(["Exposing (1s)", "Exposing (59s)"]) == [False, True]
    assert _ends(["Exposing (1s)", "Downloading", "Exposing (60s)"]) == [False, False, True]


def test_normal_countdown_stale_and_unknown_do_not_end() -> None:
    """A falling, equal or slightly higher countdown is the same frame."""
    assert _ends(["Exposing (50s)", "Exposing (48s)", "Exposing (48s)", "Exposing (50s)"]) == [False] * 4
    assert _ends(["Exposing (30s)", "Unknown", "Exposing (28s)", None]) == [False] * 4
    assert _ends(["Idle", "Idle", "Unknown", "Idle"]) == [False] * 4


def test_link_drop_mid_exposure_is_not_a_frame_end() -> None:
    """An outage mid-exposure ends no frame, even after the link returns."""
    assert _ends(["Exposing (30s)", "Unknown", "Idle", "Exposing (60s)"]) == [False] * 4


def test_autofocus_burst_counts_each_frame() -> None:
    """Short frames in a row each end; the service interval merges them."""
    sequence = ["Exposing (2s)", "Downloading", "Exposing (2s)", "Downloading", "Exposing (2s)", "Idle"]
    assert _ends(sequence) == [False, False, True, False, True, True]


class FakeTimer:
    """A timer that fires only when a test says so."""

    def __init__(self, interval: float, function: Callable[[], None]) -> None:
        """Remember the delay and the function."""
        self.interval = interval
        self.function = function
        self.started = False
        self.cancelled = False

    def start(self) -> None:
        """Mark the timer started."""
        self.started = True

    def cancel(self) -> None:
        """Mark the timer cancelled."""
        self.cancelled = True

    def fire(self) -> None:
        """Call the function unless the timer was cancelled."""
        if not self.cancelled:
            self.function()


class FakeSyncService:
    """Records start_sync calls; a test finishes them by hand."""

    def __init__(self) -> None:
        """Start with nothing running."""
        self.running: set[str] = set()
        self.started: list[tuple[str, Callable[[str, Any], None] | None]] = []
        self.log_syncs = 0
        self.log_reply: dict = {"started": True}
        self.log_error: Exception | None = None

    def start_log_sync(self) -> dict:
        """Count the log sync start, or fail or say busy as a test asks.

        Returns
        -------
        reply : `dict`
            The reply the test set.
        """
        if self.log_error is not None:
            raise self.log_error
        if self.log_reply.get("started"):
            self.log_syncs += 1
        return self.log_reply

    def is_syncing(self, object_id: str) -> bool:
        """Return whether `object_id` is marked running.

        Returns
        -------
        running : `bool`
            `True` while marked running.
        """
        return object_id in self.running

    def start_sync(self, object_id: str, on_finished: Callable[[str, Any], None] | None = None) -> dict:
        """Record the start and mark the target running.

        Returns
        -------
        reply : `dict`
            A started reply.
        """
        if object_id in self.running:
            return {"started": False, "target_id": object_id, "reason": "already running"}
        self.started.append((object_id, on_finished))
        self.running.add(object_id)
        return {"started": True}


class Rig:
    """A service wired to fakes."""

    def __init__(self, enabled: bool = True) -> None:
        """Build the service."""
        self.enabled = enabled
        self.now = 1000.0
        self.timers: list[FakeTimer] = []
        self.idle_timers: list[FakeTimer] = []
        self.sync = FakeSyncService()
        self.service = AutoDownloadService(
            sync_service=self.sync,
            is_enabled=lambda: self.enabled,
            timer_factory=self._make_timer,
            clock=lambda: self.now,
        )

    def _make_timer(self, interval: float, function: Callable[[], None]) -> FakeTimer:
        """Make and keep a fake timer.

        Returns
        -------
        timer : `FakeTimer`
            The new timer.
        """
        timer = FakeTimer(interval, function)
        if interval == module.IDLE_FINAL_LOG_SYNC_SECONDS:
            self.idle_timers.append(timer)
        else:
            self.timers.append(timer)
        return timer

    def frame(self, target: str | None = "M 31") -> None:
        """Send one complete frame: exposing, then idle."""
        self.service.on_camera_status("Exposing (1s)", target)
        self.service.on_camera_status("Idle", target)


def test_sync_starts_after_the_delay() -> None:
    """A frame end sets one delayed timer; firing it starts the sync."""
    rig = Rig()
    rig.frame()
    assert [timer.interval for timer in rig.timers] == [module.FILE_WRITE_DELAY_SECONDS]
    assert rig.timers[0].started
    assert rig.sync.started == []
    rig.timers[0].fire()
    assert [name for name, _ in rig.sync.started] == ["M 31"]


def test_no_target_or_setting_off_means_no_sync() -> None:
    """No target, or the setting off, means nothing is scheduled."""
    rig = Rig()
    rig.frame(None)
    assert rig.timers == []
    off = Rig(enabled=False)
    off.frame()
    assert off.timers == [] and off.sync.started == []
    # Turned off after the timer was set: the fire does nothing.
    rig.frame()
    rig.enabled = False
    rig.timers[0].fire()
    assert rig.sync.started == []


def test_second_frame_while_waiting_adds_no_second_timer() -> None:
    """One pending timer per target."""
    rig = Rig()
    rig.frame()
    rig.frame()
    assert len(rig.timers) == 1


def test_running_sync_defers_and_runs_once_after() -> None:
    """A running sync defers the start; one sync starts after."""
    rig = Rig()
    rig.sync.running.add("M 31")
    rig.frame()
    rig.timers[0].fire()
    assert rig.sync.started == []
    assert rig.timers[1].interval == module.BUSY_RECHECK_SECONDS
    rig.sync.running.clear()
    rig.timers[1].fire()
    assert len(rig.sync.started) == 1


def test_minimum_interval_between_starts() -> None:
    """A start within the interval waits for the interval to end."""
    rig = Rig()
    rig.frame()
    rig.timers[0].fire()
    rig.sync.running.clear()
    rig.now += 10.0
    rig.frame()
    rig.timers[1].fire()
    assert len(rig.sync.started) == 1
    assert rig.timers[2].interval == pytest.approx(module.MINIMUM_INTERVAL_SECONDS - 10.0)
    rig.now += 20.0
    rig.timers[2].fire()
    assert len(rig.sync.started) == 2


def test_different_targets_are_independent() -> None:
    """Each target has its own timer."""
    rig = Rig()
    rig.frame("M 31")
    rig.frame("M 13")
    assert len(rig.timers) == 2


def test_finish_only_logs_and_sends_no_event() -> None:
    """The finish listener sends no event; the sync service does."""
    rig = Rig()
    rig.frame()
    rig.timers[0].fire()
    on_finished = rig.sync.started[0][1]
    assert on_finished is not None
    on_finished("M 31", SimpleNamespace(transferred=3))
    on_finished("M 31", None)


def test_stop_cancels_timers_and_refuses_new_ones() -> None:
    """Shutdown cancels what is pending and nothing is scheduled afterward."""
    rig = Rig()
    rig.frame()
    rig.service.stop()
    assert rig.timers[0].cancelled
    rig.frame("M 13")
    assert len(rig.timers) == 1
    rig.timers[0].fire()
    assert rig.sync.started == []


def _finish_frame_sync(rig: Rig, target: str = "M 31") -> None:
    """Fire the frame timer and finish the sync it started."""
    rig.timers[-1].fire()
    on_finished = rig.sync.started[-1][1]
    assert on_finished is not None
    rig.sync.running.clear()
    on_finished(target, SimpleNamespace(transferred=1))


def test_log_sync_follows_a_frame_sync_only_after_five_minutes() -> None:
    """The first frame sync starts a log sync; one soon after does not."""
    rig = Rig()
    rig.frame()
    _finish_frame_sync(rig)
    assert rig.sync.log_syncs == 1
    rig.now += 100.0
    rig.frame()
    _finish_frame_sync(rig)
    assert rig.sync.log_syncs == 1
    rig.now += module.GUIDE_LOG_MINIMUM_INTERVAL_SECONDS
    rig.frame()
    _finish_frame_sync(rig)
    assert rig.sync.log_syncs == 2


def test_final_log_sync_runs_after_imaging_stops() -> None:
    """Each frame end keeps one idle timer; firing it while Idle syncs logs."""
    rig = Rig()
    rig.frame()
    rig.frame()
    assert len(rig.idle_timers) == 2
    assert rig.idle_timers[0].cancelled
    rig.now += module.IDLE_FINAL_LOG_SYNC_SECONDS
    rig.idle_timers[-1].fire()
    assert rig.sync.log_syncs == 1


def test_final_log_sync_waits_while_a_frame_is_exposing() -> None:
    """A camera that is exposing again is not the end of imaging."""
    rig = Rig()
    rig.frame()
    rig.service.on_camera_status("Exposing (500s)", "M 31")
    rig.idle_timers[-1].fire()
    assert rig.sync.log_syncs == 0


def test_setting_off_means_no_log_sync() -> None:
    """With the setting off neither the follow-up nor the final sync runs."""
    rig = Rig()
    rig.frame()
    rig.timers[0].fire()
    rig.enabled = False
    on_finished = rig.sync.started[0][1]
    assert on_finished is not None
    on_finished("M 31", None)
    rig.idle_timers[-1].fire()
    assert rig.sync.log_syncs == 0


def test_busy_log_sync_is_skipped_quietly() -> None:
    """A busy answer starts nothing, logs nothing and keeps no start time."""
    rig = Rig()
    rig.sync.log_reply = {"started": False, "reason": "already running"}
    rig.frame()
    _finish_frame_sync(rig)
    rig.sync.log_reply = {"started": True}
    rig.frame()
    _finish_frame_sync(rig)
    assert rig.sync.log_syncs == 1


def test_failing_log_sync_warns_and_frames_continue(caplog: pytest.LogCaptureFixture) -> None:
    """A failed log start logs one warning; the next frame still downloads."""
    rig = Rig()
    rig.sync.log_error = RuntimeError("no network")
    rig.frame()
    _finish_frame_sync(rig)
    assert [record.levelname for record in caplog.records if "guide log" in record.message] == ["WARNING"]
    rig.now += 60.0
    rig.frame()
    rig.timers[-1].fire()
    assert len(rig.sync.started) == 2


def test_stop_cancels_the_idle_timer() -> None:
    """Shutdown cancels the last log sync timer too."""
    rig = Rig()
    rig.frame()
    rig.service.stop()
    assert rig.idle_timers[0].cancelled
