"""Purpose: Time each RPC call inside the backend, for the TPM harness.

Description: The page measures how long each ``/api/rpc`` call takes from
its own end, but that time mixes the backend's work with the wait for a
place in the heavy lane (`heavy_lane`) and the network. This module splits
the backend's part into two numbers:

* ``wait_ms``, the time between the call reaching its lane and its work
  starting on a worker thread (the wait for a heavy-lane place or a free
  thread);
* ``run_ms``, the time the work itself took.

Each call logs one line at the backend's ``VERBOSE`` level through its
ordinary logging: ``RPC_TIMING`` followed by a JSON object, which
``build/tpm/rpc_timings.py`` reads from the backend log. ``VERBOSE`` (5) is
the lowest level, below DEBUG, INFO, WARNING and ERROR. The app runs at INFO,
so it logs none; the TPM harness starts its backend with
``ASTROMETRICS_LOG_LEVEL=VERBOSE``, which shows these lines and everything
above them, DEBUG included. The entry point (``backend/main_backend.py``)
registers the level's name; this module sets up no logging. While the level
is off a call costs only one clock read.
"""

import functools
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

#: The backend's lowest log level, below DEBUG (10): the per-call timing
#: lines and anything else too detailed for DEBUG.
VERBOSE = 5

#: The name of the `VERBOSE` level, as ``ASTROMETRICS_LOG_LEVEL`` gives it.
VERBOSE_LEVEL_NAME = "VERBOSE"

#: The word that starts each timing line, so a reader can find it in a log
#: that holds other lines too.
RPC_TIMING_MARKER = "RPC_TIMING"

logger = logging.getLogger(__name__)


class RpcTiming:
    """The timing of one RPC call: its wait for a worker and its run time.

    Make one when the call reaches its lane, pass the work through `wrap`,
    and call `report` when the call ends.

    Parameters
    ----------
    method : `str`
        The RPC method name.
    lane : `str`
        ``"heavy"``, ``"light"`` or ``"event-loop"`` (a handler that runs on
        the event loop itself).
    """

    def __init__(self, method: str, lane: str) -> None:
        """Note the time the call reached its lane."""
        self.method = method
        self.lane = lane
        self.enabled = logger.isEnabledFor(VERBOSE)
        self.queued_at = time.perf_counter()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.succeeded = False

    def wrap(self, function: Callable[..., Any]) -> Callable[..., Any]:
        """Give a function that notes when the work starts and ends.

        Parameters
        ----------
        function : callable
            The blocking work.

        Returns
        -------
        timed : callable
            ``function`` itself when timing is off, else a wrapper that
            notes the start and end times.
        """
        if not self.enabled:
            return function

        @functools.wraps(function)
        def timed(*args: Any, **kwargs: Any) -> Any:
            """Run the work and note when it starts and ends.

            Returns
            -------
            result : `~typing.Any`
                What ``function`` returns.
            """
            self.started_at = time.perf_counter()
            try:
                result = function(*args, **kwargs)
            finally:
                self.finished_at = time.perf_counter()
            self.succeeded = True
            return result

        return timed

    async def run_coroutine(self, work: Awaitable[Any]) -> Any:
        """Await work that runs on the event loop, and write its timing line.

        Such work never waits for a worker, so its wait is zero and its run
        time counts from when the call reached its lane.

        Parameters
        ----------
        work : awaitable
            The handler's coroutine.

        Returns
        -------
        result : `~typing.Any`
            What ``work`` gives.
        """
        self.started_at = self.queued_at
        try:
            result = await work
            self.succeeded = True
            return result
        finally:
            self.finished_at = time.perf_counter()
            self.report()

    def report(self) -> None:
        """Write the call's timing line, when timing is on.

        A call that never started (cancelled while it waited) has a run time
        of zero and a wait up to now.
        """
        if not self.enabled:
            return
        now = time.perf_counter()
        started_at = self.started_at if self.started_at is not None else now
        finished_at = self.finished_at if self.finished_at is not None else started_at
        line = {
            "method": self.method,
            "lane": self.lane,
            "wait_ms": round((started_at - self.queued_at) * 1000.0, 3),
            "run_ms": round((finished_at - started_at) * 1000.0, 3),
            "ok": self.succeeded,
        }
        logger.log(VERBOSE, "%s %s", RPC_TIMING_MARKER, json.dumps(line, separators=(",", ":")))
