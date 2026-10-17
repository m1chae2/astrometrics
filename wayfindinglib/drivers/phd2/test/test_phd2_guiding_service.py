"""Purpose: Unit tests for PHD2GuidingService.

Description: Verifies the guiding_service contract (poll_external_telemetry/
get_status) and drain_guiding_samples against a fake PHD2Client, so no real
PHD2 connection is needed.
"""

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService


class FakeClient:
    """A fake PHD2Client yielding a fixed list of events once, then idling."""

    def __init__(self, events: list[dict[str, Any]]) -> None:
        self._events = events

    def events(self, stop_event: threading.Event | None = None) -> Iterator[dict[str, Any]]:
        """Yield the fixed event list once, then idle until stopped.

        Yields
        ------
        event : `dict`
            Each fixed event from ``self._events``, in order.
        """
        for event in self._events:
            if stop_event is not None and stop_event.is_set():
                return
            yield event
        while stop_event is not None and not stop_event.is_set():
            time.sleep(0.01)


def _wait_until(condition: Callable[[], object], timeout_seconds: float = 2.0) -> bool:
    """Poll condition() until it's truthy or timeout_seconds elapses.

    Returns
    -------
    succeeded : `bool`
        `True` if ``condition()`` became truthy before the timeout.
    """
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_poll_external_telemetry_starts_background_thread_and_accumulates_samples() -> None:
    """Verify poll_external_telemetry starts the thread and updates get_status.

    Confirms the background polling thread accumulates a guiding
    sample from the fake client and that get_status subsequently
    reflects it.
    """
    events = [
        {
            "Event": "GuideStep",
            "Timestamp": 1000.0,
            "RADistanceGuide": 0.3,
            "DECDistanceGuide": 0.1,
            "RADuration": 80,
            "RADirection": "East",
            "DECDuration": 40,
            "DECDirection": "North",
            "SNR": 25.0,
        },
        {"Event": "Settling"},
    ]
    service = PHD2GuidingService(FakeClient(events))
    service.poll_external_telemetry()

    assert _wait_until(lambda: len(service.get_status()["history"]) == 1)
    status = service.get_status()
    assert status["history"][0]["raPulse"] == pytest.approx(80.0)
    service.stop()


def test_drain_guiding_samples_returns_and_clears_accumulated_samples() -> None:
    """Verify drain_guiding_samples returns and clears samples.

    Confirms the returned list holds real GuidingSample objects
    matching the accumulated telemetry, and that the service's
    internal history is emptied afterward.
    """
    events = [
        {"Event": "GuideStep", "Timestamp": 1000.0, "RADuration": 10, "RADirection": "East"},
    ]
    service = PHD2GuidingService(FakeClient(events))
    service.poll_external_telemetry()

    assert _wait_until(lambda: len(service.get_status()["history"]) == 1)
    drained = service.drain_guiding_samples()
    assert len(drained) == 1
    assert drained[0].time == pytest.approx(1000.0)
    assert service.get_status() == {"history": []}
    service.stop()


def test_get_status_returns_empty_history_before_any_events() -> None:
    """Verify get_status is safe before poll_external_telemetry runs.

    Confirms calling get_status on a freshly constructed service
    returns an empty history rather than raising.
    """
    service = PHD2GuidingService(FakeClient([]))
    assert service.get_status() == {"history": []}


def test_stop_waits_for_the_reader_thread_to_end() -> None:
    """stop() returns only once the reader thread has ended."""
    service = PHD2GuidingService(FakeClient([]))
    service.poll_external_telemetry()
    assert service.is_running()

    started_at = time.monotonic()
    assert service.stop() is True

    assert not service.is_running()
    assert time.monotonic() - started_at < 0.5


def test_a_poll_after_stop_starts_the_reader_again() -> None:
    """stop() is a pause: the driver's next read starts the reader again."""
    service = PHD2GuidingService(FakeClient([]))
    service.poll_external_telemetry()
    service.stop()

    service.poll_external_telemetry()

    assert service.is_running()
    service.close()


def test_close_stops_the_reader_for_good() -> None:
    """After close(), polls no longer start the reader thread."""
    service = PHD2GuidingService(FakeClient([]))
    service.poll_external_telemetry()

    assert service.close() is True
    service.poll_external_telemetry()

    assert not service.is_running()


def test_polls_at_the_same_moment_start_only_one_reader() -> None:
    """Several threads polling at once must not each start a reader."""
    started: list[threading.Thread] = []
    original_start = threading.Thread.start

    def record_start(thread: threading.Thread) -> None:
        """Note each PHD2 reader thread started."""
        if thread.name == "PHD2Events":
            started.append(thread)
        original_start(thread)

    service = PHD2GuidingService(FakeClient([]))
    barrier = threading.Barrier(8)

    def poll() -> None:
        """Wait for every poller, then poll together."""
        barrier.wait()
        service.poll_external_telemetry()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(threading.Thread, "start", record_start)
        pollers = [threading.Thread(target=poll) for _ in range(8)]
        for poller in pollers:
            original_start(poller)
        for poller in pollers:
            poller.join(timeout=5)

    assert len(started) == 1
    service.close()
