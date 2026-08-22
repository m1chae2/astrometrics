"""Purpose: Unit tests for compute_guiding_correction.

Description: Verifies correctly signed pulses per axis, deadband
suppression, max-pulse clamping, and camera-rotation handling at 0 deg,
90 deg, and a non-right angle -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.tasks.control_tasks.guiding_correction import compute_guiding_correction


def _calibration(camera_angle_deg: float = 0.0) -> GuiderCalibration:
    return GuiderCalibration(
        id="cal-1",
        camera_id="cam-1",
        telescope_id="scope-1",
        arcsec_per_pixel=2.0,
        camera_angle_deg=camera_angle_deg,
        ra_rate_arcsec_per_sec=10.0,
        dec_rate_arcsec_per_sec=10.0,
    )


def _config(**overrides) -> CorrectionConfig:  # ruff: ignore[missing-type-kwargs]
    return CorrectionConfig(**overrides)


def test_zero_drift_at_zero_rotation_is_suppressed_by_deadband():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a drift below the deadband suppresses both pulses."""
    correction = compute_guiding_correction("f-1", 0.0, 0.0, _calibration(), _config())
    assert correction.pulse_ra_ms == 0
    assert correction.pulse_dec_ms == 0
    assert correction.suppressed_by_deadband is True


def test_positive_ra_drift_at_zero_rotation_yields_positive_ra_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a positive x-pixel drift at 0deg rotation yields a +RA pulse."""
    correction = compute_guiding_correction("f-2", 5.0, 0.0, _calibration(camera_angle_deg=0.0), _config())
    assert correction.pulse_ra_ms > 0
    assert correction.pulse_dec_ms == 0
    assert correction.suppressed_by_deadband is False


def test_ninety_degree_rotation_maps_x_drift_to_dec_axis():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a 90deg rotation maps a pure x-pixel drift onto the Dec axis."""
    correction = compute_guiding_correction("f-3", 5.0, 0.0, _calibration(camera_angle_deg=90.0), _config())
    assert correction.pulse_ra_ms == 0
    assert correction.pulse_dec_ms > 0


def test_non_right_angle_rotation_splits_drift_across_both_axes():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a 45deg rotation produces nonzero pulses on both axes."""
    correction = compute_guiding_correction("f-4", 10.0, 0.0, _calibration(camera_angle_deg=45.0), _config())
    assert correction.pulse_ra_ms != 0
    assert correction.pulse_dec_ms != 0
    # At 45deg, drift splits evenly between axes.
    assert abs(correction.drift_ra_arcsec) == pytest.approx(abs(correction.drift_dec_arcsec), rel=1e-6)


def test_negative_drift_yields_negative_signed_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a negative x-pixel drift yields a negative-signed RA pulse."""
    correction = compute_guiding_correction("f-5", -5.0, 0.0, _calibration(), _config())
    assert correction.pulse_ra_ms < 0


def test_large_drift_is_clamped_to_max_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a drift exceeding guiding_max_pulse_ms is clamped."""
    config = _config(guiding_max_pulse_ms=200)
    correction = compute_guiding_correction("f-6", 500.0, 0.0, _calibration(), config)
    assert abs(correction.pulse_ra_ms) == 200
    assert correction.clamped_by_max_move is True


def test_calibration_required_no_default():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify calling without a calibration raises rather than defaulting."""
    with pytest.raises(TypeError):
        compute_guiding_correction("f-7", 5.0, 0.0, config=_config())
