"""Purpose: Cap how many slow whole-catalog reads run at once.

Description: Every RPC call runs on one of a fixed set of worker threads. A
slow catalog read that the client has given up on keeps its thread until it
finishes, so a burst of retries could use every worker and block even a
simple status call. `HeavyReadLimiter` stops that in two ways. First, only a
few heavy reads run at the same time; a read that cannot get a place within
a short wait fails at once with a "busy, try again" error. Second, identical
requests that arrive together share one computation instead of each running
it.
"""

import logging
import threading
import time
from collections.abc import Callable, Hashable
from typing import Any, TypeVar

from astrometricslib import ConflictError

logger = logging.getLogger(__name__)

_Answer = TypeVar("_Answer")

#: Heavy reads allowed at once. Three leaves most of the 40 workers free.
DEFAULT_MAXIMUM_CONCURRENT_READS = 3

#: Seconds a read waits for a place before it is refused. The client gives
#: up on a call after 45 seconds, so waiting much longer helps nobody.
DEFAULT_SLOT_WAIT_SECONDS = 20.0

#: Seconds a request that joined a running identical read waits for its
#: answer. It is just under the client's 45 second limit.
DEFAULT_SHARED_WAIT_SECONDS = 40.0


class _Flight:
    """One running computation that identical requests share."""

    def __init__(self) -> None:
        """Start with no answer and an unset finished signal."""
        self.finished = threading.Event()
        self.answer: Any = None
        self.error: BaseException | None = None


class HeavyReadLimiter:
    """Run slow reads a few at a time, and once per identical request.

    Parameters
    ----------
    maximum_concurrent : `int`, optional
        How many reads may compute at the same time.
    slot_wait_seconds : `float`, optional
        How long a read waits for a place before it is refused.
    shared_wait_seconds : `float`, optional
        How long a request waits for an identical read already running.
    """

    def __init__(
        self,
        maximum_concurrent: int = DEFAULT_MAXIMUM_CONCURRENT_READS,
        slot_wait_seconds: float = DEFAULT_SLOT_WAIT_SECONDS,
        shared_wait_seconds: float = DEFAULT_SHARED_WAIT_SECONDS,
    ) -> None:
        """Set the limits and start with nothing running."""
        self._slots = threading.BoundedSemaphore(maximum_concurrent)
        self._slot_wait_seconds = slot_wait_seconds
        self._shared_wait_seconds = shared_wait_seconds
        self._flights: dict[Hashable, _Flight] = {}
        self._flights_lock = threading.Lock()

    def run(self, key: Hashable, compute: Callable[[], _Answer]) -> _Answer:
        """Compute an answer, sharing it with identical concurrent requests.

        Parameters
        ----------
        key : `Hashable`
            Names the request: the method and all its parameters. Requests
            with equal keys running together share one computation.
        compute : `Callable`
            Does the slow read. Called by only one of the identical
            requests.

        Returns
        -------
        answer : `Any`
            What `compute` returned, for this request or the one it joined.
            If no place becomes free in time, or the shared read does not
            finish in time, the call raises a retryable `ConflictError`.

        """
        with self._flights_lock:
            flight = self._flights.get(key)
            is_leader = flight is None
            if flight is None:
                flight = _Flight()
                self._flights[key] = flight

        if not is_leader:
            logger.info("Heavy read %r joined an identical read already running", key)
            if not flight.finished.wait(self._shared_wait_seconds):
                raise self._busy_error(key)
            if flight.error is not None:
                raise flight.error
            return flight.answer

        try:
            flight.answer = self._compute_in_slot(key, compute)
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._flights_lock:
                self._flights.pop(key, None)
            flight.finished.set()
        return flight.answer

    def _compute_in_slot(self, key: Hashable, compute: Callable[[], _Answer]) -> _Answer:
        """Wait for a place, then run `compute` in it.

        Returns
        -------
        answer : `Any`
            What `compute` returned. A wait for a place that runs out raises
            a retryable `ConflictError`.
        """
        started = time.monotonic()
        if not self._slots.acquire(timeout=self._slot_wait_seconds):
            raise self._busy_error(key)
        waited = time.monotonic() - started
        if waited > 1.0:
            logger.info("Heavy read %r waited %.1f s for a place", key, waited)
        try:
            return compute()
        finally:
            self._slots.release()

    def _busy_error(self, key: Hashable) -> ConflictError:
        """Log a refusal and build the error that tells the caller to retry.

        Returns
        -------
        error : `ConflictError`
            A retryable "busy" error.
        """
        logger.info("Heavy read %r refused: the catalog is busy with other reads", key)
        return ConflictError(
            "The star catalog is busy with other large reads. Try again in a moment.",
            details={"reason": "busy"},
            retryable=True,
        )
