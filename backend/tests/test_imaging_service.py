"""Purpose: Unit tests for the backend's thin ImagingService stop path.

Description: The capture and its stop are in the wayfinding library
(`wayfindinglib/tasks/control_tasks/test/test_imaging_capture.py`). The
backend only runs the capture on a thread and hands the stop to
`control.imaging.stop_capture`. The tests use a stand-in `control.imaging`
and check that the RPC method is registered and declared, and that a stop
with nothing running returns `False` and is not an error.
"""

from types import SimpleNamespace

from backend import public_interface
from backend.routers import rpc_router
from backend.services.observatory.imaging_service import ImagingService


class _Imaging:
    """A stand-in `control.imaging` that records stops."""

    def __init__(self, running: bool) -> None:
        """Start with a run going, or none."""
        self.running = running
        self.stops = 0

    def stop_capture(self) -> bool:
        """Stop the run, if one is going.

        Returns
        -------
        stopped : `bool`
            Whether a run was going.
        """
        self.stops += 1
        was_running, self.running = self.running, False
        return was_running


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
