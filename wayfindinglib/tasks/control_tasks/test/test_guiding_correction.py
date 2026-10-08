"""Purpose: Unit tests for compute_guiding_correction.

Description: Verifies correctly signed pulses per axis, deadband
suppression, max-pulse clamping, and camera-rotation handling at 0 deg,
90 deg, and a non-right angle -- the cases
`Wayfinding_Library_Architecture.md` calls out.
"""

import math

import pytest

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.session.correction_config import CorrectionConfig
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis
from wayfindinglib.tasks.control_tasks.guiding_correction import compute_guiding_correction


def _mount_model(
    dominant_period_seconds: float | None = 480.0,
    periodic_error_peak_to_peak_arcsec: float = 10.0,
    dec_backlash_estimate_ms: float | None = None,
) -> GuidingSpectrumAnalysis:
    return GuidingSpectrumAnalysis(
        sample_count=100,
        duration_seconds=3600.0,
        dominant_period_seconds=dominant_period_seconds,
        periodic_error_peak_to_peak_arcsec=periodic_error_peak_to_peak_arcsec,
        dec_backlash_estimate_ms=dec_backlash_estimate_ms,
    )


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


def test_no_mount_model_disables_all_feedforward():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify omitting mount_model reproduces pre-M7b reactive-only output."""
    without_model = compute_guiding_correction("f-8", 0.0, 0.0, _calibration(), _config())
    with_none_elapsed = compute_guiding_correction(
        "f-8", 0.0, 0.0, _calibration(), _config(), mount_model=None, elapsed_guiding_seconds=100.0
    )
    assert without_model.pulse_ra_ms == with_none_elapsed.pulse_ra_ms == 0
    assert without_model.suppressed_by_deadband is with_none_elapsed.suppressed_by_deadband is True


def test_periodic_error_feedforward_issues_a_pulse_even_within_deadband():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify PEC anticipates the error instead of waiting for the deadband.

    At phase=0.25 (a quarter into the worm cycle), the modeled sinusoid
    peaks, so a pulse must be issued canceling it even though the
    measured instantaneous drift is zero (well within the deadband).
    """
    mount_model = _mount_model(dominant_period_seconds=480.0, periodic_error_peak_to_peak_arcsec=10.0)
    elapsed_seconds = 480.0 * 0.25

    correction = compute_guiding_correction(
        "f-9",
        0.0,
        0.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        elapsed_guiding_seconds=elapsed_seconds,
    )

    assert correction.pulse_ra_ms != 0
    assert correction.suppressed_by_deadband is False


def test_periodic_error_feedforward_requires_elapsed_time():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a mount_model without elapsed_guiding_seconds adds none."""
    mount_model = _mount_model()

    correction = compute_guiding_correction(
        "f-10", 0.0, 0.0, _calibration(), _config(), mount_model=mount_model
    )

    assert correction.pulse_ra_ms == 0
    assert correction.suppressed_by_deadband is True


def test_periodic_error_feedforward_is_near_zero_at_zero_crossing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the modeled sinusoid's zero crossing adds ~no pulse."""
    mount_model = _mount_model(dominant_period_seconds=480.0, periodic_error_peak_to_peak_arcsec=10.0)

    correction = compute_guiding_correction(
        "f-11",
        0.0,
        0.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        elapsed_guiding_seconds=0.0,
    )

    assert correction.pulse_ra_ms == 0
    assert correction.suppressed_by_deadband is True


def test_backlash_feedforward_adds_lead_in_pulse_on_reversal():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a flagged Dec reversal adds the learned backlash lead-in."""
    mount_model = _mount_model(dominant_period_seconds=None, dec_backlash_estimate_ms=150.0)

    with_reversal = compute_guiding_correction(
        "f-12",
        0.0,
        5.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        dec_direction_reversal=True,
    )
    without_reversal = compute_guiding_correction(
        "f-12",
        0.0,
        5.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        dec_direction_reversal=False,
    )

    assert abs(with_reversal.pulse_dec_ms) > abs(without_reversal.pulse_dec_ms)


def test_backlash_feedforward_direction_matches_the_reactive_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the lead-in pulse's sign matches the reactive correction's."""
    mount_model = _mount_model(dominant_period_seconds=None, dec_backlash_estimate_ms=150.0)

    positive_drift = compute_guiding_correction(
        "f-13", 0.0, 5.0, _calibration(), _config(), mount_model=mount_model, dec_direction_reversal=True
    )
    negative_drift = compute_guiding_correction(
        "f-14", 0.0, -5.0, _calibration(), _config(), mount_model=mount_model, dec_direction_reversal=True
    )

    assert positive_drift.pulse_dec_ms > 0
    assert negative_drift.pulse_dec_ms < 0


def test_combined_feedforward_and_reactive_pulse_is_clamped_to_max_pulse():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a large reactive plus feedforward sum is still clamped."""
    mount_model = _mount_model(dominant_period_seconds=None, dec_backlash_estimate_ms=900.0)
    config = _config(guiding_max_pulse_ms=200)

    correction = compute_guiding_correction(
        "f-15",
        0.0,
        50.0,
        _calibration(),
        config,
        mount_model=mount_model,
        dec_direction_reversal=True,
    )

    assert abs(correction.pulse_dec_ms) == 200
    assert correction.clamped_by_max_move is True


def test_periodic_error_feedforward_direction_flips_across_the_period():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the feedforward pulse's sign flips a half-period apart."""
    mount_model = _mount_model(dominant_period_seconds=480.0, periodic_error_peak_to_peak_arcsec=10.0)

    quarter_phase = compute_guiding_correction(
        "f-16",
        0.0,
        0.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        elapsed_guiding_seconds=480.0 * 0.25,
    )
    three_quarter_phase = compute_guiding_correction(
        "f-17",
        0.0,
        0.0,
        _calibration(),
        _config(),
        mount_model=mount_model,
        elapsed_guiding_seconds=480.0 * 0.75,
    )

    assert quarter_phase.pulse_ra_ms != 0
    assert three_quarter_phase.pulse_ra_ms != 0
    assert math.copysign(1.0, quarter_phase.pulse_ra_ms) != math.copysign(
        1.0, three_quarter_phase.pulse_ra_ms
    )
