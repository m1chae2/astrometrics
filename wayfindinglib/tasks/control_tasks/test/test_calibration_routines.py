"""Purpose: Unit tests for the M7b calibration/polar-alignment-assist routines.

Description: Verifies `run_guider_calibration`/`run_backlash_calibration`
sequence their injected steps correctly (mirroring `SafeStateSteps`'s
fake-callable testing discipline), and that `run_polar_alignment_assist`
is a thin, correctly-parameterized wrapper over `fit_pointing_model`.
"""

import pytest

from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.tasks.control_tasks.calibration_routines import (
    BacklashCalibrationSteps,
    GuiderCalibrationSteps,
    run_backlash_calibration,
    run_guider_calibration,
    run_polar_alignment_assist,
)

_ATTEMPTS = [
    {"ra": 10.0, "dec": 20.0, "delta_ra_arcsec": 5.0, "delta_dec_arcsec": -3.0},
    {"ra": 11.0, "dec": 21.0, "delta_ra_arcsec": 4.5, "delta_dec_arcsec": -2.5},
    {"ra": 12.0, "dec": 22.0, "delta_ra_arcsec": 5.5, "delta_dec_arcsec": -3.5},
    {"ra": 13.0, "dec": 23.0, "delta_ra_arcsec": 5.2, "delta_dec_arcsec": -3.2},
]


def test_run_guider_calibration_sequences_ra_then_dec_pulses():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the RA pulse and measurement happen before the Dec pulse."""
    centroids = iter([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (10.0, 8.0)])
    call_order: list[str] = []

    def pulse_ra(duration_sec: float) -> bool:
        call_order.append("pulse_ra")
        assert duration_sec == pytest.approx(3.0)
        return True

    def pulse_dec(duration_sec: float) -> bool:
        call_order.append("pulse_dec")
        assert duration_sec == pytest.approx(3.0)
        return True

    steps = GuiderCalibrationSteps(
        pulse_ra=pulse_ra, pulse_dec=pulse_dec, measure_guide_star_centroid=lambda: next(centroids)
    )

    calibration = run_guider_calibration(steps, "cal-1", "cam-1", "scope-1", arcsec_per_pixel=2.0)

    assert call_order == ["pulse_ra", "pulse_dec"]
    assert isinstance(calibration, GuiderCalibration)
    assert calibration.id == "cal-1"
    assert calibration.ra_rate_arcsec_per_sec == pytest.approx((10.0 * 2.0) / 3.0)
    assert calibration.dec_rate_arcsec_per_sec == pytest.approx((8.0 * 2.0) / 3.0)


def test_run_guider_calibration_raises_when_ra_pulse_command_fails():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed RA pulse command raises rather than measuring anyway."""
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: False,
        pulse_dec=lambda duration_sec: True,
        measure_guide_star_centroid=lambda: (0.0, 0.0),
    )

    with pytest.raises(RuntimeError, match="RA calibration pulse"):
        run_guider_calibration(steps, "cal-2", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_guider_calibration_raises_when_dec_pulse_command_fails():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a failed Dec pulse command raises instead of measuring."""
    centroids = iter([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)])
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: True,
        pulse_dec=lambda duration_sec: False,
        measure_guide_star_centroid=lambda: next(centroids),
    )

    with pytest.raises(RuntimeError, match="Dec calibration pulse"):
        run_guider_calibration(steps, "cal-3", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_guider_calibration_propagates_no_displacement_value_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a run with no measurable displacement raises ValueError."""
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: True,
        pulse_dec=lambda duration_sec: True,
        measure_guide_star_centroid=lambda: (5.0, 5.0),
    )

    with pytest.raises(ValueError, match="no measurable star displacement"):
        run_guider_calibration(steps, "cal-4", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_backlash_calibration_returns_elapsed_time_to_first_motion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the reported delay matches the pulse count before motion."""
    centroids = iter([
        (0.0, 0.0),  # after the settle pulse
        (0.0, 0.0),  # test pulse 1: no motion yet (backlash)
        (0.0, 0.0),  # test pulse 2: no motion yet
        (0.0, 0.6),  # test pulse 3: motion detected
    ])
    pulse_calls: list[tuple[str, float]] = []

    def pulse_dec(direction: str, duration_sec: float) -> bool:
        pulse_calls.append((direction, duration_sec))
        return True

    steps = BacklashCalibrationSteps(pulse_dec=pulse_dec, measure_guide_star_centroid=lambda: next(centroids))

    backlash_ms = run_backlash_calibration(
        steps, reversal_test_pulse_sec=0.05, motion_detection_threshold_px=0.5
    )

    assert backlash_ms == pytest.approx(150.0)  # 3 test pulses * 50ms
    assert pulse_calls[0] == ("north", 1.0)
    assert all(call[0] == "south" for call in pulse_calls[1:])
    assert len(pulse_calls) == 4  # 1 settle + 3 reversed-direction test pulses


def test_run_backlash_calibration_reports_full_budget_if_motion_never_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify exhausting the probe budget returns the full elapsed time."""
    steps = BacklashCalibrationSteps(
        pulse_dec=lambda direction, duration_sec: True, measure_guide_star_centroid=lambda: (0.0, 0.0)
    )

    backlash_ms = run_backlash_calibration(steps, reversal_test_pulse_sec=0.05, max_test_pulses=10)

    assert backlash_ms == pytest.approx(10 * 0.05 * 1000.0)


def test_run_polar_alignment_assist_fits_from_the_given_attempts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the assist routine fits directly from the passed-in attempts."""
    model = run_polar_alignment_assist(_ATTEMPTS, latitude_deg=39.7)

    assert model.sample_count == len(_ATTEMPTS)
    assert model.confidence != "insufficient_data" or model.sample_count < 4


def test_run_polar_alignment_assist_reports_insufficient_data_early():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify fewer than 4 attempts reports insufficient_data, not a fit."""
    model = run_polar_alignment_assist(_ATTEMPTS[:2])

    assert model.confidence == "insufficient_data"
