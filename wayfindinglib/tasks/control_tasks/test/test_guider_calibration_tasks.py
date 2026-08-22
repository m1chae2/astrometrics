"""Purpose: Unit tests for compute_guider_calibration.

Description: Verifies guider/focus calibration recovers known values
from simulated sequences -- the case
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.tasks.control_tasks.guider_calibration_tasks import compute_guider_calibration


def test_recovers_known_calibration_from_axis_aligned_moves():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify axis-aligned moves recover known rates and a 0deg angle."""
    calibration = compute_guider_calibration(
        "cal-1",
        "cam-1",
        "scope-1",
        arcsec_per_pixel=2.0,
        ra_pulse_duration_sec=1.0,
        ra_start_xy=(100.0, 100.0),
        ra_end_xy=(110.0, 100.0),  # 10px in +x, duration 1s
        dec_pulse_duration_sec=2.0,
        dec_start_xy=(100.0, 100.0),
        dec_end_xy=(100.0, 110.0),  # 10px in +y, duration 2s
    )
    assert calibration.camera_angle_deg == pytest.approx(0.0, abs=1e-6)
    assert calibration.ra_rate_arcsec_per_sec == pytest.approx(20.0)  # 10px * 2 arcsec/px / 1s
    assert calibration.dec_rate_arcsec_per_sec == pytest.approx(10.0)  # 10px * 2 arcsec/px / 2s
    assert calibration.arcsec_per_pixel == pytest.approx(2.0)


def test_recovers_nonzero_camera_angle():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a diagonal RA move recovers the correct camera angle."""
    calibration = compute_guider_calibration(
        "cal-2",
        "cam-1",
        "scope-1",
        arcsec_per_pixel=1.0,
        ra_pulse_duration_sec=1.0,
        ra_start_xy=(0.0, 0.0),
        ra_end_xy=(10.0, 10.0),  # 45deg
        dec_pulse_duration_sec=1.0,
        dec_start_xy=(0.0, 0.0),
        dec_end_xy=(0.0, 5.0),
    )
    assert calibration.camera_angle_deg == pytest.approx(45.0)


def test_raises_when_ra_axis_produces_no_displacement():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an RA pulse with no displacement raises, not divides by zero."""
    with pytest.raises(ValueError, match="no measurable star displacement"):
        compute_guider_calibration(
            "cal-3",
            "cam-1",
            "scope-1",
            arcsec_per_pixel=2.0,
            ra_pulse_duration_sec=1.0,
            ra_start_xy=(100.0, 100.0),
            ra_end_xy=(100.0, 100.0),
            dec_pulse_duration_sec=1.0,
            dec_start_xy=(100.0, 100.0),
            dec_end_xy=(100.0, 110.0),
        )
