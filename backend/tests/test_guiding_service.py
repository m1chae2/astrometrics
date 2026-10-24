"""Purpose: Unit tests for the backend's thin GuidingService.

Description: The guiding itself is in the wayfinding library (see
`wayfindinglib/tasks/control_tasks/test/test_live_guiding.py`). The
backend only runs `control.guiding.run_loop` on a thread, stops it, and
hands on the live status. The tests use a stand-in `control.guiding`.
"""

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import ConflictError, PermissionDeniedError
from backend.services.observatory.guiding_service import GuidingService
from wayfindinglib import LiveGuidingStatus


class _Guiding:
    """A stand-in `control.guiding` whose loop runs until stopped."""

    def __init__(self, refuse: bool = False, monitor_mode: bool = False) -> None:
        """Start idle; `refuse` blocks a start, `monitor_mode` a stop."""
        self.running = False
        self.refuse = refuse
        self.monitor_mode = monitor_mode
        self.arguments: dict[str, Any] = {}

    def run_loop(self, stop: threading.Event, exposure_seconds: float | None, gain: float | None) -> None:
        """Run until `stop` is set, or refuse.

        Raises
        ------
        ConflictError
            If this stand-in refuses to start.
        """
        if self.refuse:
            raise ConflictError("The mount is not tracking.")
        self.arguments = {"exposure_seconds": exposure_seconds, "gain": gain}
        self.running = True
        stop.wait(timeout=5.0)
        self.running = False

    def stop_loop(self, stop: threading.Event) -> None:
        """End the loop, as the library does, unless in monitor mode.

        Raises
        ------
        PermissionDeniedError
            In monitor mode.
        """
        if self.monitor_mode:
            raise PermissionDeniedError("monitor mode")
        stop.set()

    def status(self, include: list[str]) -> SimpleNamespace:
        """Report whether the loop runs.

        Returns
        -------
        status : `types.SimpleNamespace`
            With a ``live`` section.
        """
        return SimpleNamespace(live=LiveGuidingStatus(is_guiding=self.running))


def test_stop_passes_on_the_librarys_refusal_in_monitor_mode() -> None:
    """Decision U4 lives in the library; the service lets its error through."""
    guiding = _Guiding(monitor_mode=True)
    service = GuidingService(observatory_api=SimpleNamespace(guiding=guiding))

    with pytest.raises(PermissionDeniedError):
        service.stop_guiding()


def test_start_runs_the_library_loop_and_stop_ends_it() -> None:
    """Start waits for the loop, refuses a second run; stop ends it."""
    guiding = _Guiding()
    service = GuidingService(observatory_api=SimpleNamespace(guiding=guiding))

    assert service.start_guiding(exposure=2.0, gain=5) is True
    assert guiding.arguments == {"exposure_seconds": 2.0, "gain": 5}
    assert service.start_guiding() is False
    assert service.get_status()["is_guiding"] is True

    assert service.stop_guiding() is True
    assert guiding.running is False
    assert service.get_status()["is_guiding"] is False


def test_start_reports_false_when_the_loop_refuses() -> None:
    """A loop that cannot start (mount not tracking) gives `False`."""
    service = GuidingService(observatory_api=SimpleNamespace(guiding=_Guiding(refuse=True)))

    assert service.start_guiding() is False
