"""Purpose: Unit tests for the backend's thin AlignmentService.

Description: The centering loop itself is `control.mount.slew(center=True)`
in the wayfinding library, tested in
`wayfindinglib/tasks/control_tasks/test/test_centering.py`. Here the
backend only starts and stops that call on a thread, lists the recorded
attempts in their camelCase form, lists the nights through
`control.history.query(kind="alignment")`, and parses the coordinate
strings of the ``telescope:alignment_start`` call.
"""

import threading
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from astrometricslib import InvalidArgumentError
from backend.services.observatory.alignment_service import CENTERING_MAX_ITERATIONS, AlignmentService
from wayfindinglib import ObservatoryControl
from wayfindinglib.api.control.history import HistoryControl
from wayfindinglib.api.control.mount import MountControl


class _Logs:
    """A stand-in log database holding two recorded attempts."""

    def get_alignment_logs(self, limit: int = 100) -> list[dict[str, Any]]:
        """Return the attempts, newest first.

        Returns
        -------
        logs : `list` [`dict` [`str`, `Any`]]
            Two rows, the newer one with an old status name.
        """
        return [
            {"status": "synced", "delta_ra_arcsec": 3.0, "mount_ra": 11.0, "timestamp": 200.0},
            {"status": "warning", "delta_ra_arcsec": 1.5, "delta_dec_arcsec": -2.5, "timestamp": 100.0},
        ]


def _autospec_control() -> MagicMock:
    """Build an autospec'd `ObservatoryControl` with autospec'd children.

    Returns
    -------
    observatory : `unittest.mock.MagicMock`
        A double that raises `AttributeError` for any method the real
        classes do not have.
    """
    observatory = create_autospec(ObservatoryControl, instance=True)
    observatory.mount = create_autospec(MountControl, instance=True)
    observatory.history = create_autospec(HistoryControl, instance=True)
    return observatory


def test_get_attempts_lists_recorded_attempts_oldest_first() -> None:
    """Attempts come back camelCase, oldest first; no position, no target."""
    service = AlignmentService(observatory_api=_autospec_control(), logger_interface=_Logs())

    live = service.get_attempts()
    attempts = live["alignmentAttempts"]
    assert live["alignmentTargets"] == []

    assert [attempt["timestamp"] for attempt in attempts] == [100.0, 200.0]
    assert attempts[0]["deltaRaArcsec"] == pytest.approx(1.5)
    assert attempts[0]["deltaDecArcsec"] == pytest.approx(-2.5)
    assert attempts[1]["status"] == "aligned"


def test_list_sessions_reads_the_alignment_history() -> None:
    """The nights come from `control.history.query(kind="alignment")`."""
    observatory = _autospec_control()
    observatory.history.query.return_value = {"sessions": [{"sessionId": "2026-09-25"}]}
    service = AlignmentService(observatory_api=observatory)

    assert service.list_sessions() == [{"sessionId": "2026-09-25"}]
    observatory.history.query.assert_called_once_with(kind="alignment", limit=50, register_job=False)


def test_start_then_cancel_alignment_runs_and_stops_the_centering() -> None:
    """The centering call runs on a thread, and cancel stops the mount."""
    observatory = _autospec_control()
    released = threading.Event()
    observatory.mount.slew.side_effect = lambda *args, **kwargs: released.wait(timeout=5.0)
    observatory.mount.abort_motion.side_effect = lambda: released.set()
    service = AlignmentService(observatory_api=observatory, logger_interface=_Logs())

    assert service.start_alignment(target_ra="0h 40m 00s", target_dec="+20d 00m 00s") is True
    assert service.is_active() is True
    assert service.start_alignment(target_ra="1h 00m 00s", target_dec="+20d 00m 00s") is False
    assert service.get_attempts()["alignmentAttempts"][-1]["status"] == "solving"

    assert service.cancel_alignment() is True
    assert service.is_active() is False
    (position,), options = observatory.mount.slew.call_args
    assert position.ra_deg == pytest.approx(10.0)
    assert position.dec_deg == pytest.approx(20.0)
    assert options == {"center": True, "max_iterations": CENTERING_MAX_ITERATIONS}


def test_cancel_without_a_run_reports_false() -> None:
    """Cancelling when nothing runs changes nothing."""
    observatory = _autospec_control()
    service = AlignmentService(observatory_api=observatory)

    assert service.cancel_alignment() is False
    observatory.mount.abort_motion.assert_not_called()


def test_start_alignment_raises_on_unparseable_coordinates() -> None:
    """A coordinate that cannot be read is reported, and nothing starts."""
    observatory = _autospec_control()
    service = AlignmentService(observatory_api=observatory)

    with pytest.raises(InvalidArgumentError):
        service.start_alignment(target_ra="", target_dec="+45d 00m 00s")

    assert service.is_active() is False
    observatory.mount.slew.assert_not_called()
