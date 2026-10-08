"""Purpose: Tests for centering the mount by plate solving.

Description: `center_on` slews, takes a frame, solves it, measures the
pointing error, and syncs and slews again until the error is within the
tolerance. These tests replace the camera, the plate solver and the mount
commands with stand-ins, and check: the recorded offsets are distances on
the sky (scaled by cos(dec), wrapped at 0h/24h); a solve within the
tolerance stops the loop without a sync; a solve outside it syncs to the
solved position and slews back, in degrees that the mount turns into
hours; failures are recorded and retried; and `abort_motion` stops the
loop. `solved_center` reads the middle of a plate-solved image.
"""

import math
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from astropy.wcs import WCS

from astrometricslib import PlateSolveFailedError
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.tasks.control_tasks import centering, hardware_operations


class _RecordingLogs:
    """Stands in for the log database, keeping each recorded attempt."""

    def __init__(self) -> None:
        """Start with no attempts."""
        self.attempts: list[dict[str, Any]] = []

    def record_alignment_attempt(self, attempt: dict[str, Any]) -> None:
        """Keep one attempt."""
        self.attempts.append(attempt)


@pytest.fixture
def mount_calls(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[SkyPosition]]:
    """Replace the mount commands and the waits; record each command.

    Returns
    -------
    calls : `dict` [`str`, `list` [`SkyPosition`]]
        The positions each of ``slew`` and ``sync`` was called with.
    """
    calls: dict[str, list[SkyPosition]] = {"slew": [], "sync": []}
    monkeypatch.setattr(hardware_operations, "slew", lambda context, position: calls["slew"].append(position))
    monkeypatch.setattr(
        hardware_operations, "sync_mount", lambda context, position: calls["sync"].append(position)
    )
    monkeypatch.setattr(centering, "time", SimpleNamespace(sleep=lambda seconds: None, time=lambda: 1000.0))
    return calls


def _context(tolerance_arcsec: float = 30.0) -> SimpleNamespace:
    """Build the parts of a `ControlContext` the centering loop reads.

    Returns
    -------
    context : `types.SimpleNamespace`
        The correction settings, the stop flag, the log database and the
        observer latitude.
    """
    return SimpleNamespace(
        correction_config=CorrectionConfig(alignment_convergence_tolerance_arcsec=tolerance_arcsec),
        motion_stop=threading.Event(),
        records=_RecordingLogs(),
        observer_latitude_deg=lambda: 45.0,
    )


def _solving_to(monkeypatch: pytest.MonkeyPatch, *solutions: tuple[float, float] | None) -> None:
    """Make each frame solve to the next position (`None` means it fails)."""
    remaining = list(solutions)

    def solve(context: Any, path: Path) -> tuple[float, float]:
        """Return the next solution, or raise for `None`.

        Returns
        -------
        ra_deg, dec_deg : `float`
            The solved center.

        Raises
        ------
        PlateSolveFailedError
            If the next solution is `None`.
        """
        solution = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if solution is None:
            raise PlateSolveFailedError("No stars matched.")
        return solution

    monkeypatch.setattr(centering, "_capture_frame", lambda context, exposure_seconds: Path("frame.fits"))
    monkeypatch.setattr(centering, "_solve", solve)


def test_a_solve_within_tolerance_stops_without_a_sync(
    monkeypatch: pytest.MonkeyPatch, mount_calls: dict
) -> None:
    """One arcsecond off on each axis is centered on the first round."""
    _solving_to(monkeypatch, (180.0 + 1 / 3600, 45.0 + 1 / 3600))
    context = _context()

    assert centering.center_on(context, SkyPosition(ra_deg=180.0, dec_deg=45.0)) is True

    assert [attempt["status"] for attempt in context.records.attempts] == ["aligned"]
    assert mount_calls["sync"] == []
    assert len(mount_calls["slew"]) == 1


def test_the_recorded_offset_is_a_distance_on_the_sky(
    monkeypatch: pytest.MonkeyPatch, mount_calls: dict
) -> None:
    """0.01 degree of RA at dec 20 is 36 cos(20) arcsec, no hours factor."""
    _solving_to(monkeypatch, (100.01, 20.02))
    context = _context(tolerance_arcsec=1.0)

    centering.center_on(context, SkyPosition(ra_deg=100.0, dec_deg=20.0), max_iterations=1)

    attempt = context.records.attempts[0]
    assert attempt["delta_ra_arcsec"] == pytest.approx(36.0 * math.cos(math.radians(20.0)), rel=1e-3)
    assert attempt["delta_dec_arcsec"] == pytest.approx(72.0, rel=1e-3)
    assert attempt["status"] == "warning"


@pytest.mark.parametrize(
    ("target_ra", "solved_ra", "expected_delta_deg"),
    [(359.995, 0.005, 0.01), (0.005, 359.995, -0.01)],
)
def test_the_offset_wraps_across_zero_hours(
    monkeypatch: pytest.MonkeyPatch,
    mount_calls: dict,
    target_ra: float,
    solved_ra: float,
    expected_delta_deg: float,
) -> None:
    """An offset across 0h/24h is small, not about 360 degrees."""
    _solving_to(monkeypatch, (solved_ra, 0.0))
    context = _context(tolerance_arcsec=1.0)

    centering.center_on(context, SkyPosition(ra_deg=target_ra, dec_deg=0.0), max_iterations=1)

    assert context.records.attempts[0]["delta_ra_arcsec"] == pytest.approx(
        expected_delta_deg * 3600.0, rel=1e-3
    )


def test_a_solve_outside_tolerance_syncs_and_slews_back(
    monkeypatch: pytest.MonkeyPatch, mount_calls: dict
) -> None:
    """The mount is synced to the solve and sent back, in degrees."""
    _solving_to(monkeypatch, (151.0, 31.0), (150.0, 30.0))
    context = _context()

    assert centering.center_on(context, SkyPosition(ra_deg=150.0, dec_deg=30.0), max_iterations=3) is True

    (synced,) = mount_calls["sync"]
    assert synced.ra_deg == pytest.approx(151.0)
    assert synced.ra_hours == pytest.approx(151.0 / 15.0)
    assert synced.dec_deg == pytest.approx(31.0)
    assert [position.ra_deg for position in mount_calls["slew"]] == [150.0, 150.0]
    assert [attempt["status"] for attempt in context.records.attempts] == ["warning", "aligned"]


def test_failed_solves_are_recorded_and_retried(monkeypatch: pytest.MonkeyPatch, mount_calls: dict) -> None:
    """A frame that will not solve is a failed attempt; the loop goes on."""
    _solving_to(monkeypatch, None)
    context = _context()

    assert centering.center_on(context, SkyPosition(ra_deg=10.0, dec_deg=20.0), max_iterations=4) is False

    assert [attempt["status"] for attempt in context.records.attempts] == ["failed"] * 4


def test_a_missing_frame_is_a_failed_attempt(monkeypatch: pytest.MonkeyPatch, mount_calls: dict) -> None:
    """A camera that sends no frame gives a failed attempt."""
    monkeypatch.setattr(centering, "_capture_frame", lambda context, exposure_seconds: None)
    context = _context()

    assert centering.center_on(context, SkyPosition(ra_deg=10.0, dec_deg=20.0), max_iterations=2) is False

    assert len(context.records.attempts) == 2


def test_the_iteration_limit_defaults_to_the_correction_settings(
    monkeypatch: pytest.MonkeyPatch, mount_calls: dict
) -> None:
    """With no limit given, the settings' iteration limit applies."""
    _solving_to(monkeypatch, None)
    context = _context()

    centering.center_on(context, SkyPosition(ra_deg=10.0, dec_deg=20.0))

    assert len(context.records.attempts) == CorrectionConfig().alignment_iteration_limit


def test_abort_motion_stops_the_loop(monkeypatch: pytest.MonkeyPatch, mount_calls: dict) -> None:
    """A stop set during a round ends the loop before it syncs."""
    context = _context(tolerance_arcsec=1.0)

    def solve_then_stop(context_: Any, path: Path) -> tuple[float, float]:
        """Ask the loop to stop, then return a far-off solve.

        Returns
        -------
        ra_deg, dec_deg : `float`
            A solve one degree off.
        """
        context.motion_stop.set()
        return 11.0, 20.0

    monkeypatch.setattr(centering, "_capture_frame", lambda context_, exposure_seconds: Path("frame.fits"))
    monkeypatch.setattr(centering, "_solve", solve_then_stop)

    assert centering.center_on(context, SkyPosition(ra_deg=10.0, dec_deg=20.0), max_iterations=5) is False
    assert mount_calls["sync"] == []
    assert len(context.records.attempts) == 1


def test_solved_center_reads_the_middle_of_the_image() -> None:
    """The center pixel, not the reference pixel, gives the solved position."""
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [10.0, 20.0]
    wcs.wcs.crpix = [1.0, 1.0]
    wcs.wcs.cdelt = [-1 / 3600, 1 / 3600]

    ra_deg, dec_deg = centering.solved_center(wcs, image_shape=(201, 201))

    assert dec_deg == pytest.approx(20.0 + 100 / 3600, abs=1e-5)
    assert ra_deg < 10.0


def test_solved_center_falls_back_to_the_reference_point() -> None:
    """Without an image size, the reference point is used, wrapped."""
    wcs = WCS(naxis=2)
    wcs.wcs.crval = [-5.0, 12.0]

    assert centering.solved_center(wcs) == pytest.approx((355.0, 12.0))
