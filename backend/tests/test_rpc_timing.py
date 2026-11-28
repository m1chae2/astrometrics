"""Purpose: Tests for the per-call RPC timing lines.

Description: At the backend's ``VERBOSE`` log level the backend logs one
``RPC_TIMING`` line per RPC call, with the wait for a worker and the run
time. These tests check that the lines are off at INFO, that a heavy call's
wait for a heavy-lane place shows up as wait and not as run time, that an
event-loop handler is timed too, and that the lines go through ordinary
logging as VERBOSE records of the timing logger.
"""

import asyncio
import json
import logging
import time
from collections.abc import Iterator

import pytest

from backend.routers import rpc_router
from backend.services.infrastructure import heavy_lane, rpc_timing


class _TimingLines(logging.Handler):
    """Keep the JSON part of every timing line logged."""

    def __init__(self) -> None:
        """Start with no lines."""
        super().__init__()
        self.lines: list[dict] = []
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        """Keep one timing record and its JSON part.

        Parameters
        ----------
        record : `logging.LogRecord`
            The timing record.
        """
        message = record.getMessage()
        assert message.startswith(rpc_timing.RPC_TIMING_MARKER + " ")
        self.records.append(record)
        self.lines.append(json.loads(message.split(" ", 1)[1]))


@pytest.fixture
def timing_handler() -> Iterator[_TimingLines]:
    """Turn the timing lines on for one test and collect them from logging.

    Tests may set up logging; the app's own code never does outside its
    entry points.

    Yields
    ------
    handler : `_TimingLines`
        The handler that kept the lines logged during the test.
    """
    handler = _TimingLines()
    previous_level = rpc_timing.logger.level
    rpc_timing.logger.addHandler(handler)
    rpc_timing.logger.setLevel(rpc_timing.VERBOSE)
    try:
        yield handler
    finally:
        rpc_timing.logger.removeHandler(handler)
        rpc_timing.logger.setLevel(previous_level)


@pytest.fixture
def timing_lines(timing_handler: _TimingLines) -> list[dict]:
    """Give the timing lines logged during one test.

    Parameters
    ----------
    timing_handler : `_TimingLines`
        The collecting handler.

    Returns
    -------
    lines : `list` [`dict`]
        The lines, filled as calls are made.
    """
    return timing_handler.lines


def test_the_timing_lines_are_off_at_info(caplog: pytest.LogCaptureFixture) -> None:
    """At INFO, the app's normal level, no timing line is logged."""
    caplog.set_level(logging.INFO, logger=rpc_timing.logger.name)
    assert rpc_timing.RpcTiming("system:pulse", "light").enabled is False


def test_the_verbose_level_turns_the_lines_on(caplog: pytest.LogCaptureFixture) -> None:
    """VERBOSE, the lowest level (below DEBUG), turns the timing lines on."""
    assert 0 < rpc_timing.VERBOSE < logging.DEBUG
    caplog.set_level(rpc_timing.VERBOSE, logger=rpc_timing.logger.name)
    assert rpc_timing.RpcTiming("system:pulse", "light").enabled is True


def test_off_timing_passes_the_function_through(caplog: pytest.LogCaptureFixture) -> None:
    """When the lines are off, the work is not wrapped at all."""
    caplog.set_level(logging.INFO, logger=rpc_timing.logger.name)

    def work() -> int:
        """Stand in for a handler.

        Returns
        -------
        answer : `int`
            Always 1.
        """
        return 1

    assert rpc_timing.RpcTiming("system:pulse", "light").wrap(work) is work


def test_a_heavy_call_splits_its_wait_from_its_run(
    monkeypatch: pytest.MonkeyPatch, timing_lines: list[dict]
) -> None:
    """A heavy call that waits for a lane place reports the wait apart."""
    monkeypatch.setattr(heavy_lane, "HEAVY_LANE_SLOTS", 1)
    heavy_lane._heavy_lane_limiters.clear()

    def slow(**_: object) -> str:
        """Hold the one heavy-lane place for a while.

        Returns
        -------
        answer : `str`
            Always ``"done"``.
        """
        time.sleep(0.15)
        return "done"

    async def two_calls() -> None:
        """Run two heavy calls at once, so the second waits."""
        await asyncio.gather(
            rpc_router.run_in_lane("advisory:star_shapes", slow),
            rpc_router.run_in_lane("advisory:star_shapes", slow),
        )

    asyncio.run(two_calls())
    heavy_lane._heavy_lane_limiters.clear()

    assert [line["lane"] for line in timing_lines] == ["heavy", "heavy"]
    assert all(line["ok"] for line in timing_lines)
    assert all(line["run_ms"] >= 140.0 for line in timing_lines)
    assert max(line["wait_ms"] for line in timing_lines) >= 100.0
    assert min(line["wait_ms"] for line in timing_lines) < 100.0


def test_a_light_call_and_a_failure_are_timed(timing_lines: list[dict]) -> None:
    """A light call runs in the light lane; a call that raises says so."""

    def broken(**_: object) -> None:
        """Fail like a handler that hits a bad argument.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("bad")

    asyncio.run(rpc_router.run_in_lane("system:pulse", lambda **_: 1))
    with pytest.raises(ValueError, match="bad"):
        asyncio.run(rpc_router.run_in_lane("system:pulse", broken))

    assert [(line["method"], line["lane"], line["ok"]) for line in timing_lines] == [
        ("system:pulse", "light", True),
        ("system:pulse", "light", False),
    ]


def test_an_event_loop_handler_is_timed(timing_lines: list[dict]) -> None:
    """A coroutine handler has no wait and its own run time."""

    async def handler() -> str:
        """Stand in for an async handler.

        Returns
        -------
        answer : `str`
            Always ``"ok"``.
        """
        await asyncio.sleep(0.01)
        return "ok"

    answer = asyncio.run(rpc_timing.RpcTiming("ui:navigate", "event-loop").run_coroutine(handler()))

    assert answer == "ok"
    assert timing_lines[0]["lane"] == "event-loop"
    assert timing_lines[0]["wait_ms"] == pytest.approx(0.0)
    assert timing_lines[0]["run_ms"] >= 5.0


def test_the_timing_lines_go_through_logging(timing_handler: _TimingLines) -> None:
    """Each timing line is a VERBOSE record of the timing logger."""
    asyncio.run(rpc_router.run_in_lane("system:pulse", lambda **_: 1))

    assert len(timing_handler.records) == 1
    record = timing_handler.records[0]
    assert record.name == rpc_timing.logger.name
    assert record.levelno == rpc_timing.VERBOSE
