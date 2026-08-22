"""Purpose: Unit tests for PHD2GuidingService.

Description: Verifies the guiding_service contract (poll_external_telemetry/
get_status) and drain_guiding_samples against a fake PHD2Client, so no real
PHD2 connection is needed.
"""

import time

import pytest

from wayfindinglib.drivers.phd2.phd2_guiding_service import PHD2GuidingService


class FakeClient:
    """A fake PHD2Client yielding a fixed list of events once, then idling."""

    def __init__(self, events):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._events = events

    def events(self, stop_event=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


def _wait_until(condition, timeout_seconds=2.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
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


def test_poll_external_telemetry_starts_background_thread_and_accumulates_samples():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def test_drain_guiding_samples_returns_and_clears_accumulated_samples():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def test_get_status_returns_empty_history_before_any_events():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify get_status is safe before poll_external_telemetry runs.

    Confirms calling get_status on a freshly constructed service
    returns an empty history rather than raising.
    """
    service = PHD2GuidingService(FakeClient([]))
    assert service.get_status() == {"history": []}
