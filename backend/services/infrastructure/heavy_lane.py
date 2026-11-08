"""Purpose: The shared places for heavy work, so it does not crowd light calls.

Description: Some work spends seconds of pure Python on a large library:
the heavy RPC methods and the start-up warm-ups. Python lets one thread run
Python code at a time (the global interpreter lock), so every extra busy
thread slows the event loop and the light calls. All heavy work therefore
shares `HEAVY_LANE_SLOTS` places. The RPC router and the start-up warm-ups
both use `run_heavy`, so a warm-up that is still running cannot add to the
user's first heavy calls.
"""

import asyncio
import functools
from collections.abc import Callable
from typing import Any

import anyio

#: How many heavy calls may run at once. Two keeps the processor busy while
#: leaving the lock mostly free for light calls.
HEAVY_LANE_SLOTS = 2

_heavy_lane_limiters: dict[Any, anyio.CapacityLimiter] = {}
"""One limiter per running event loop, made on first use."""


def heavy_lane_limiter() -> anyio.CapacityLimiter:
    """Give the limiter that holds the heavy lane's places for this loop.

    Returns
    -------
    limiter : `anyio.CapacityLimiter`
        Holds `HEAVY_LANE_SLOTS` places. A call waits for a place without
        using a worker thread.
    """
    loop = asyncio.get_running_loop()
    if loop not in _heavy_lane_limiters:
        _heavy_lane_limiters.clear()
        _heavy_lane_limiters[loop] = anyio.CapacityLimiter(HEAVY_LANE_SLOTS)
    return _heavy_lane_limiters[loop]


async def run_heavy(function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking heavy work in a worker thread, in the heavy lane.

    Parameters
    ----------
    function : callable
        The blocking function.
    *args, **kwargs
        Passed to `function`.

    Returns
    -------
    result : `~typing.Any`
        What `function` returns.
    """
    return await anyio.to_thread.run_sync(
        functools.partial(function, *args, **kwargs), limiter=heavy_lane_limiter()
    )
