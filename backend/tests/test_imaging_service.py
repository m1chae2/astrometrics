"""Purpose: Unit tests for the backend's thin ImagingService stop path.

Description: The capture and its stop are in the wayfinding library
(`wayfindinglib/tasks/control_tasks/test/test_imaging_capture.py`). The
backend checks the capture first (`control.imaging.check_capture`), runs it
on a thread and hands the stop to `control.imaging.stop`. The tests use a
stand-in `control.imaging` and check that the RPC method is registered and
declared, that a stop with nothing running returns `False` and is not an
error, and that a refused capture raises before any thread starts.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.exceptions import PermissionDeniedError
from backend import public_interface
from backend.routers import rpc_router
from backend.services.observatory.imaging_service import ImagingService


class _Imaging:
    """A stand-in `control.imaging` that records stops."""

    def __init__(self, running: bool) -> None:
        """Start with a run going, or none."""
        self.running = running
        self.stops = 0

    def get_status(self, include: list[str]) -> SimpleNamespace:
        """Say whether a run is going.

        Returns
        -------
        status : `types.SimpleNamespace`
            With ``capture_running``.
        """
        return SimpleNamespace(capture_running=self.running)

    def stop(self) -> None:
        """Stop the run, if one is going."""
        self.stops += 1
        self.running = False

    def check_capture(self, exposure_s: float, **options: object) -> None:
        """Refuse every capture, as monitor mode does.

        Raises
        ------
        PermissionDeniedError
            Always.
        """
        raise PermissionDeniedError("CAPTURE_ORCHESTRATION is not AUTHORITATIVE")

    def capture(self, exposure_s: float, **options: object) -> None:
        """Fail the test: a refused capture must never start.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("capture ran after a refused check")


def _service(imaging: _Imaging) -> ImagingService:
    """Build the service over a stand-in observatory.

    Returns
    -------
    service : `ImagingService`
        A service whose library calls go to `imaging`.
    """
    return ImagingService(SimpleNamespace(imaging=imaging), job_service=SimpleNamespace())


def test_stop_capture_hands_the_stop_to_the_library() -> None:
    """A running capture is stopped and the service says so."""
    imaging = _Imaging(running=True)

    assert _service(imaging).stop_capture() is True
    assert imaging.stops == 1


def test_stop_capture_with_nothing_running_returns_false() -> None:
    """Stopping nothing is a no-op, not an error."""
    assert _service(_Imaging(running=False)).stop_capture() is False


def test_the_stop_method_is_registered_and_declared() -> None:
    """`imaging:stop_capture` is in the RPC table and the public interface."""
    assert rpc_router._HANDLERS["imaging:stop_capture"] == ("imaging_service", "stop_capture")
    assert "imaging:stop_capture" in public_interface.RPC_METHODS


def test_a_refused_capture_raises_before_any_thread_starts() -> None:
    """Monitor mode's refusal reaches the caller as an error at once."""
    with pytest.raises(PermissionDeniedError):
        _service(_Imaging(running=False)).capture_sequence("M 31", 10.0, 3)
