"""Purpose: Copy finished frames and guide logs into the library by itself.

Description: The camera runs on the observatory computer (KStars/Ekos on a
StellarMate). The telescope status poll reports the camera state about every
2 seconds. This module watches that state. `FrameEndDetector` is a small
pure class that says when one frame has ended. `AutoDownloadService` then
waits a moment for the file to be written and starts the same sync as the
"Download Frames" button, never more than one at a time per target. The same
service also starts the guide log sync: after a frame sync ends, at most
every `GUIDE_LOG_MINIMUM_INTERVAL_SECONDS`, and once more after imaging has
stopped. It is the only scheduler of both downloads, and both use the
library's one copy code.
"""

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from backend.services.infrastructure.background_logging import background_log_context

logger = logging.getLogger(__name__)

# Seconds to wait after a frame ends before syncing. Ekos writes the FITS
# file to disk a moment after the camera download finishes. A design estimate
# (validation register WAY-D96).
FILE_WRITE_DELAY_SECONDS = 5.0

# Fewest seconds between two automatic sync starts for one target. Autofocus
# takes many short frames in a row, and one sync after the burst is enough.
# A design estimate (validation register WAY-D97).
MINIMUM_INTERVAL_SECONDS = 30.0

# Fewest seconds between two guide log sync starts. The logs grow all night,
# so a copy every few minutes keeps the past-night view close to current
# without a copy after every frame. A design estimate (validation register
# WAY-D99).
GUIDE_LOG_MINIMUM_INTERVAL_SECONDS = 300.0

# Seconds the camera must stay quiet after a frame before the last guide log
# sync of the night. It is longer than the interval above, so the last
# frame's guiding is in the logs, and shorter than a long exposure plus its
# download, so a slow frame is not taken for the end of imaging. A design
# estimate (validation register WAY-D100).
IDLE_FINAL_LOG_SYNC_SECONDS = 600.0

# Seconds to wait before trying again when a sync of the target still runs.
BUSY_RECHECK_SECONDS = 5.0

# How much the exposure countdown may rise between two polls before it means
# a new frame. The poll runs about every 2 s and the countdown is in whole
# seconds, so a rise of up to 3 s is rounding and jitter. A design estimate
# (validation register WAY-D98).
COUNTDOWN_JUMP_SECONDS = 3.0

_EXPOSING_PATTERN = re.compile(r"^\s*Exposing\b\D*(\d+)")


class TimerLike(Protocol):
    """The part of `threading.Timer` that the service uses."""

    def start(self) -> None:
        """Start the timer."""

    def cancel(self) -> None:
        """Stop the timer if it has not fired."""


def _default_timer_factory(interval: float, function: Callable[[], None]) -> threading.Timer:
    """Make a daemon timer that calls `function` after `interval` seconds.

    Returns
    -------
    timer : `threading.Timer`
        The timer, not yet started.
    """
    timer = threading.Timer(interval, function)
    timer.daemon = True
    return timer


class FrameEndDetector:
    """Say when a camera frame has ended, from the camera status text only.

    The statuses are ``"Exposing (58s)"``, ``"Downloading"``, ``"Idle"`` and
    ``"Unknown"``. A frame has ended when the camera:

    * goes from Downloading or Exposing to Idle;
    * goes from Downloading to Exposing (the next frame started at once);
    * goes from Exposing (a s) to Exposing (b s) with b more than
      `COUNTDOWN_JUMP_SECONDS` above a (the next frame started and the
      Downloading state fell between two polls).

    A lower or equal countdown is the same frame, so a late or repeated
    reading never counts twice.
    """

    def __init__(self) -> None:
        """Start with no earlier status."""
        self._previous_kind: str | None = None
        self._previous_seconds: int = 0

    @property
    def is_idle(self) -> bool:
        """Whether the last readable status was Idle."""
        return self._previous_kind == "idle"

    @staticmethod
    def _classify(camera_status: str | None) -> tuple[str | None, int]:
        """Turn a status text into a kind and a countdown.

        Returns
        -------
        kind : `str` or `None`
            ``"exposing"``, ``"downloading"``, ``"idle"`` or `None` for
            anything else (such as ``"Unknown"``).
        seconds : `int`
            The countdown for ``"exposing"``, else 0.
        """
        text = (camera_status or "").strip()
        match = _EXPOSING_PATTERN.match(text)
        if match:
            return "exposing", int(match.group(1))
        lowered = text.lower()
        if lowered.startswith("downloading"):
            return "downloading", 0
        if lowered == "idle":
            return "idle", 0
        return None, 0

    def update(self, camera_status: str | None) -> bool:
        """Take the newest status.

        Parameters
        ----------
        camera_status : `str` or `None`
            The camera status text from the telescope poll.

        Returns
        -------
        frame_ended : `bool`
            `True` when this status shows that a frame has just ended.
        """
        if (camera_status or "").strip().lower() == "unknown":
            # The link to the camera dropped. Forget the last state, so the
            # status coming back after the outage is not taken as a frame end.
            self._previous_kind = None
            self._previous_seconds = 0
            return False
        kind, seconds = self._classify(camera_status)
        previous_kind = self._previous_kind
        ended = False
        if previous_kind in ("downloading", "exposing") and kind == "idle":
            ended = True
        elif previous_kind == "downloading" and kind == "exposing":
            ended = True
        elif previous_kind == "exposing" and kind == "exposing":
            ended = seconds > self._previous_seconds + COUNTDOWN_JUMP_SECONDS
        if kind is None:
            # An unreadable status says nothing; keep the last good one.
            return False
        self._previous_kind = kind
        self._previous_seconds = seconds
        return ended


class AutoDownloadService:
    """Start a library sync after each finished frame, and the guide log syncs.

    One timer per target is pending at most. It waits
    `FILE_WRITE_DELAY_SECONDS` after a frame ends. When it fires, the
    service starts the sync unless the setting is off, a sync of that target
    is running, or the last automatic start was under
    `MINIMUM_INTERVAL_SECONDS` ago; in the last two cases it sets the timer
    again, so the frame is not lost.

    When a frame sync that this service started ends, a guide log sync
    starts if `GUIDE_LOG_MINIMUM_INTERVAL_SECONDS` have passed since the last
    one started. Each frame end also sets one idle timer. If the camera is
    still Idle `IDLE_FINAL_LOG_SYNC_SECONDS` later, one last log sync runs.
    A log sync that is busy, or that fails, is skipped quietly and never
    stops the frame downloads.
    """

    def __init__(
        self,
        sync_service: Any,
        is_enabled: Callable[[], bool],
        timer_factory: Callable[[float, Callable[[], None]], TimerLike] = _default_timer_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Keep the collaborators.

        Parameters
        ----------
        sync_service : `SyncService`
            Starts the syncs and says whether one is running.
        is_enabled : `Callable` [[], `bool`]
            Answers whether automatic download is on. Called at decision
            time, so a change in the settings applies without a restart.
        timer_factory : `Callable`, optional
            Makes a timer from a delay and a function. Tests replace it.
        clock : `Callable` [[], `float`], optional
            Seconds on a steady clock. Tests replace it.
        """
        self._sync_service = sync_service
        self._is_enabled = is_enabled
        self._timer_factory = timer_factory
        self._clock = clock
        self._detector = FrameEndDetector()
        self._lock = threading.Lock()
        self._timers: dict[str, TimerLike] = {}
        self._last_start: dict[str, float] = {}
        self._last_log_sync_start: float | None = None
        self._idle_timer: TimerLike | None = None
        self._stopped = False

    def on_camera_status(self, camera_status: str | None, target_name: str | None) -> None:
        """Take one telescope poll. Never raises.

        Parameters
        ----------
        camera_status : `str` or `None`
            The camera status text.
        target_name : `str` or `None`
            The current target, which names the folder to sync.
        """
        try:
            with self._lock:
                if not self._detector.update(camera_status):
                    return
            if not self._is_enabled():
                return
            if not target_name:
                logger.debug("A camera frame ended with no current target; not downloading.")
                return
            self._schedule(target_name, FILE_WRITE_DELAY_SECONDS)
            self._restart_idle_timer()
        except Exception:  # the status poll must never fail because of this feature
            logger.exception("Automatic frame download could not handle a camera status")

    def stop(self) -> None:
        """Cancel every pending timer and refuse new ones."""
        with self._lock:
            self._stopped = True
            timers = list(self._timers.values())
            if self._idle_timer is not None:
                timers.append(self._idle_timer)
                self._idle_timer = None
            self._timers.clear()
        for timer in timers:
            timer.cancel()

    def _restart_idle_timer(self) -> None:
        """Set the one timer for the last log sync, replacing an older one."""
        with self._lock:
            if self._stopped:
                return
            if self._idle_timer is not None:
                self._idle_timer.cancel()
            timer = self._timer_factory(IDLE_FINAL_LOG_SYNC_SECONDS, self._fire_idle)
            self._idle_timer = timer
        timer.start()

    def _fire_idle(self) -> None:
        """Run when imaging has been quiet long enough: one last log sync."""
        with background_log_context("auto-download"):
            try:
                with self._lock:
                    self._idle_timer = None
                    if self._stopped or not self._detector.is_idle:
                        # Not idle: a new frame runs and will set the timer.
                        return
                if not self._is_enabled():
                    return
                self._start_log_sync(respect_interval=False)
            except Exception:  # a timer thread must log, never die silently
                logger.exception("The last guide log download could not start")

    def _start_log_sync(self, respect_interval: bool) -> None:
        """Start a guide log sync, unless it is too soon or one already runs.

        Parameters
        ----------
        respect_interval : `bool`
            `True` to skip the start when the last one was under
            `GUIDE_LOG_MINIMUM_INTERVAL_SECONDS` ago.
        """
        now = self._clock()
        with self._lock:
            last = self._last_log_sync_start
            if respect_interval and last is not None and now - last < GUIDE_LOG_MINIMUM_INTERVAL_SECONDS:
                return
        reply = self._sync_service.start_log_sync()
        if reply.get("started"):
            with self._lock:
                self._last_log_sync_start = now
            logger.info("Downloading new guide logs.")

    def _schedule(self, target: str, delay: float) -> None:
        """Set the one pending timer of `target`, unless it already has one.

        Parameters
        ----------
        target : `str`
            The target to sync.
        delay : `float`
            Seconds until the timer fires.
        """
        with self._lock:
            if self._stopped or target in self._timers:
                return
            timer = self._timer_factory(delay, lambda: self._fire(target))
            self._timers[target] = timer
        timer.start()

    def _fire(self, target: str) -> None:
        """Run when a timer fires: start the sync or wait again."""
        with background_log_context("auto-download", target_id=target):
            self._fire_in_context(target)

    def _fire_in_context(self, target: str) -> None:
        """Start the sync or wait again; the body of `_fire`."""
        try:
            with self._lock:
                self._timers.pop(target, None)
                if self._stopped:
                    return
            if not self._is_enabled():
                return
            now = self._clock()
            last = self._last_start.get(target)
            if last is not None and now - last < MINIMUM_INTERVAL_SECONDS:
                self._schedule(target, MINIMUM_INTERVAL_SECONDS - (now - last))
                return
            reply = self._sync_service.start_sync(target, on_finished=self._on_finished)
            if not reply.get("started"):
                # A sync of this target still runs; look again soon.
                self._schedule(target, BUSY_RECHECK_SECONDS)
                return
            self._last_start[target] = now
            logger.info("Camera frame finished; downloading new frames of %s.", target)
        except Exception:  # a timer thread must log, never die silently
            logger.exception("Automatic frame download of %s failed to start", target)

    def _on_finished(self, target: str, result: Any) -> None:
        """Log an automatic sync's result.

        The app hears about the new frames earlier, from the sync service,
        as soon as they are copied and indexed.

        Parameters
        ----------
        target : `str`
            The target that was synced.
        result : `FrameSyncResult` or `None`
            What the sync returned; `None` when it failed.
        """
        added = int(getattr(result, "transferred", 0) or 0)
        logger.info("Automatic download of %s finished: %d new frames.", target, added)
        try:
            if self._is_enabled() and not self._stopped:
                self._start_log_sync(respect_interval=True)
        except Exception:  # a log sync must never stop the frame downloads
            logger.warning("The guide log download could not start", exc_info=True)
