"""Purpose: Keep the Image Processing reviews of busy targets warm at night.

Description: The Image Processing screen reads three slow things when it opens
a target: the star-shape review, the frame states and the grouped frame list.
The libraries keep these answers, but a change to the target's data (new
frames, new quality rows, a finished stack, a frame put back) makes the kept
answers old. The first visit after a change then waits seconds for them.

`TargetReviewRewarmer` reads them again in the background after such a change,
so the next visit finds them ready:

* `request` is called by the services that change a target's data. It notes
  the changed target and the few targets the app asked about most recently
  (`note_requested`). It never waits and never raises, so it cannot break the
  work that called it.
* Each noted target waits for a quiet spell (`QUIET_SECONDS` with no new
  change), so a burst of changes gives one re-read per target. A target that
  keeps changing is still read once `LONGEST_WAIT_SECONDS` have passed.
* `run` is the background loop. It starts nothing until the start-up warm-up
  has finished. It reads one target per step in the heavy lane, the same
  places the heavy app requests use, and only when the lane has a free place
  and no request is waiting for one. So a re-read never makes the user wait
  for more than the one target already being read.
* A failed read is logged at debug level. Nothing is shown to the user.
"""

import asyncio
import logging
import threading
import time
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager

from backend.services.infrastructure.heavy_lane import heavy_lane_has_room, run_heavy

logger = logging.getLogger(__name__)

#: Seconds with no new change before a target is read again. A frame sync
#: sends its new-frames change and then its quality change a few seconds
#: later; three seconds lets the pair land as one re-read.
QUIET_SECONDS = 3.0

#: The most seconds a changed target waits, even when changes keep coming.
LONGEST_WAIT_SECONDS = 30.0

#: How many recently asked-about targets are read again after any change.
RECENT_TARGET_COUNT = 3

#: Seconds between looks at the heavy lane while it is busy.
BUSY_RETRY_SECONDS = 1.0

#: Seconds between looks at the start-up warm-up while it is still running.
READY_POLL_SECONDS = 0.5


class TargetReviewRewarmer:
    """Read a changed target's Image Processing reviews again, later.

    Parameters
    ----------
    quiet_seconds : `float`, optional
        Seconds with no new change before a target is read again.
    longest_wait_seconds : `float`, optional
        The most seconds a target waits while changes keep coming.
    recent_count : `int`, optional
        How many recently asked-about targets to keep.
    busy_retry_seconds : `float`, optional
        Seconds between looks at a busy heavy lane.
    """

    def __init__(
        self,
        quiet_seconds: float = QUIET_SECONDS,
        longest_wait_seconds: float = LONGEST_WAIT_SECONDS,
        recent_count: int = RECENT_TARGET_COUNT,
        busy_retry_seconds: float = BUSY_RETRY_SECONDS,
    ) -> None:
        """Start with no waiting targets and no recent ones."""
        self._quiet_seconds = quiet_seconds
        self._longest_wait_seconds = longest_wait_seconds
        self._recent_count = recent_count
        self._busy_retry_seconds = busy_retry_seconds
        self._lock = threading.Lock()
        # Target id -> (time of the first waiting change, time it is due).
        self._waiting: dict[str, tuple[float, float]] = {}
        # Most recent first.
        self._recent: list[str] = []
        self._stopped = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        # Set on a thread while it reads for a warm-up, so those reads are
        # not counted as the app asking about a target.
        self._thread_state = threading.local()

    @contextmanager
    def background_reads(self) -> Generator[None]:
        """Mark the reads made inside this block as background reads.

        The warm-ups read through the same service methods the app calls.
        Inside this block those reads do not count as the app asking about
        the target (`note_requested` ignores them).

        Yields
        ------
        None
            Control, while the reads run.
        """
        self._thread_state.background = True
        try:
            yield
        finally:
            self._thread_state.background = False

    def note_requested(self, target_id: str | None) -> None:
        """Note that the app asked about a target. Never raises.

        Parameters
        ----------
        target_id : `str` or `None`
            The target the app asked about. `None` or empty is ignored, and
            so is a read made inside `background_reads`.
        """
        if not target_id or getattr(self._thread_state, "background", False):
            return
        with self._lock:
            if target_id in self._recent:
                self._recent.remove(target_id)
            self._recent.insert(0, target_id)
            del self._recent[self._recent_count :]

    def recent_targets(self) -> list[str]:
        """Give the targets the app asked about most recently.

        Returns
        -------
        targets : `list` [`str`]
            Most recent first, at most ``recent_count`` of them.
        """
        with self._lock:
            return list(self._recent)

    def waiting_targets(self) -> list[str]:
        """Give the targets waiting to be read again.

        Returns
        -------
        targets : `list` [`str`]
            Sorted by name.
        """
        with self._lock:
            return sorted(self._waiting)

    def request(self, target_ids: Iterable[str] | None = None) -> None:
        """Ask for the reviews to be read again. Returns at once, never raises.

        Parameters
        ----------
        target_ids : `Iterable` [`str`], optional
            The targets whose data changed. The recent targets are always
            added. `None` or empty means only the recent targets, for a
            change that names no target (such as new calibration frames).
        """
        try:
            now = time.monotonic()
            with self._lock:
                if self._stopped:
                    return
                for target_id in [*(target_ids or []), *self._recent]:
                    if not target_id:
                        continue
                    first_at, _ = self._waiting.get(target_id, (now, now))
                    due_at = min(now + self._quiet_seconds, first_at + self._longest_wait_seconds)
                    self._waiting[target_id] = (first_at, due_at)
            self._wake_loop()
        except Exception:  # a trigger must never fail the work that sent it
            logger.debug("Could not note a target review re-read", exc_info=True)

    def stop(self) -> None:
        """Drop the waiting targets and refuse new ones. Safe to call twice."""
        with self._lock:
            self._stopped = True
            self._waiting.clear()
        self._wake_loop()

    async def run(self, warm_target: Callable[[str], object], ready: threading.Event) -> None:
        """Read waiting targets, one per heavy-lane step, until cancelled.

        Parameters
        ----------
        warm_target : callable
            Reads one target's reviews. It is called with the target id in a
            worker thread, inside `background_reads`.
        ready : `threading.Event`
            Set when the start-up warm-up has finished. Nothing is read
            before then.
        """
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while not ready.is_set():
            await asyncio.sleep(READY_POLL_SECONDS)
        while True:
            with self._lock:
                if self._stopped:
                    return
            # Cleared before looking, so a request that lands after the look
            # still wakes the wait below.
            self._wake.clear()
            target_id, wait_seconds = self._next_due(time.monotonic())
            if target_id is None:
                try:
                    await asyncio.wait_for(self._wake.wait(), wait_seconds)
                except TimeoutError:
                    pass
                continue
            if not heavy_lane_has_room():
                # A user request is waiting or every place is taken: wait.
                await asyncio.sleep(self._busy_retry_seconds)
                continue
            with self._lock:
                self._waiting.pop(target_id, None)
            await run_heavy(self._warm_quietly, warm_target, target_id)

    def _next_due(self, now: float) -> tuple[str | None, float | None]:
        """Find the waiting target that is due first.

        Parameters
        ----------
        now : `float`
            The current `time.monotonic` time.

        Returns
        -------
        target_id : `str` or `None`
            A target that is due now, or `None` when none is.
        wait_seconds : `float` or `None`
            When none is due, the seconds until the next one is, or `None`
            when nothing is waiting.
        """
        with self._lock:
            if not self._waiting:
                return None, None
            target_id, (_, due_at) = min(self._waiting.items(), key=lambda item: item[1][1])
        if due_at <= now:
            return target_id, None
        return None, due_at - now

    def _warm_quietly(self, warm_target: Callable[[str], object], target_id: str) -> None:
        """Read one target's reviews; a failure is logged at debug level.

        Parameters
        ----------
        warm_target : callable
            Reads one target's reviews.
        target_id : `str`
            The target to read.
        """
        try:
            with self.background_reads():
                warm_target(target_id)
        except Exception:  # background work: a failure only means a slower visit
            logger.debug("Target review re-read failed for %s", target_id, exc_info=True)

    def _wake_loop(self) -> None:
        """Wake the background loop from any thread, if it is running."""
        loop, wake = self._loop, self._wake
        if loop is None or wake is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # the loop closed between the check and the call
            logger.debug("The target review loop has stopped", exc_info=True)
