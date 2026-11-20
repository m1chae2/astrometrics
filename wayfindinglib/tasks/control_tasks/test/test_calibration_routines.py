"""Purpose: Unit tests for the calibration and polar-alignment-assist routines.

Description: Verifies `run_guider_calibration`/`run_backlash_calibration`
sequence their injected steps correctly (mirroring `SafeStateSteps`'s
fake-callable testing discipline), and that `run_polar_alignment_assist`
is a thin, correctly-parameterized wrapper over `fit_pointing_model`.
"""

import pytest

from astrometricslib.exceptions import HardwareError, ProcessingError
from wayfindinglib.analytics.guide_pulses import GUIDE_RATE_ARCSEC_PER_S
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.tasks.control_tasks.calibration_routines import (
    BACKLASH_MAX_TEST_PULSES,
    BACKLASH_MOTION_THRESHOLD_PX,
    BACKLASH_REVERSAL_TEST_PULSE_SECONDS,
    BACKLASH_SETTLE_PULSE_SECONDS,
    CALIBRATION_MOVE_GUIDE_PX,
    BacklashCalibrationSteps,
    GuiderCalibrationSteps,
    calibration_pulse_seconds,
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


def test_run_guider_calibration_sequences_ra_then_dec_pulses() -> None:
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

    calibration = run_guider_calibration(
        steps,
        "cal-1",
        "cam-1",
        "scope-1",
        arcsec_per_pixel=2.0,
        ra_pulse_duration_sec=3.0,
        dec_pulse_duration_sec=3.0,
    )

    assert call_order == ["pulse_ra", "pulse_dec"]
    assert isinstance(calibration, GuiderCalibration)
    assert calibration.id == "cal-1"
    assert calibration.ra_rate_arcsec_per_sec == pytest.approx((10.0 * 2.0) / 3.0)
    assert calibration.dec_rate_arcsec_per_sec == pytest.approx((8.0 * 2.0) / 3.0)


def test_calibration_pulse_moves_the_star_the_declared_distance() -> None:
    """The pulse is the move, times the plate scale, over the guide rate."""
    move_px = CALIBRATION_MOVE_GUIDE_PX.default

    pulse_seconds = calibration_pulse_seconds(4.0, guide_rate_arcsec_per_s=8.0)

    assert pulse_seconds == pytest.approx(move_px * 4.0 / 8.0)


def test_calibration_pulse_keeps_three_seconds_for_the_reference_setup() -> None:
    """The Apertura 32 mm and ASI120MC-S setup gets the old 3.0 s."""
    plate_scale = 206.265 * 3.75 / 121.05

    pulse_seconds = calibration_pulse_seconds(plate_scale, guide_rate_arcsec_per_s=7.52)

    assert pulse_seconds == pytest.approx(3.0, abs=0.01)


def test_calibration_pulse_reads_the_equipment_guide_rate_by_default() -> None:
    """Leaving the rate out uses the declared mount guide rate."""
    plate_scale = 6.39

    assert calibration_pulse_seconds(plate_scale) == pytest.approx(
        calibration_pulse_seconds(plate_scale, GUIDE_RATE_ARCSEC_PER_S.default)
    )


def test_run_guider_calibration_derives_the_pulse_when_none_is_given() -> None:
    """Both axis pulses get the derived length when none is named."""
    centroids = iter([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0), (10.0, 8.0)])
    durations: list[float] = []

    def record_pulse(duration_sec: float) -> bool:
        durations.append(duration_sec)
        return True

    steps = GuiderCalibrationSteps(
        pulse_ra=record_pulse, pulse_dec=record_pulse, measure_guide_star_centroid=lambda: next(centroids)
    )

    run_guider_calibration(steps, "cal-d", "cam-1", "scope-1", arcsec_per_pixel=6.39)

    expected = calibration_pulse_seconds(6.39)
    assert durations == [pytest.approx(expected), pytest.approx(expected)]
    assert expected == pytest.approx(3.0, abs=0.01)


def test_run_guider_calibration_raises_when_ra_pulse_command_fails() -> None:
    """Verify a failed RA pulse command raises rather than measuring anyway."""
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: False,
        pulse_dec=lambda duration_sec: True,
        measure_guide_star_centroid=lambda: (0.0, 0.0),
    )

    with pytest.raises(HardwareError, match="RA calibration pulse"):
        run_guider_calibration(steps, "cal-2", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_guider_calibration_raises_when_dec_pulse_command_fails() -> None:
    """Verify a failed Dec pulse command raises instead of measuring."""
    centroids = iter([(0.0, 0.0), (10.0, 0.0), (10.0, 0.0)])
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: True,
        pulse_dec=lambda duration_sec: False,
        measure_guide_star_centroid=lambda: next(centroids),
    )

    with pytest.raises(HardwareError, match="Dec calibration pulse"):
        run_guider_calibration(steps, "cal-3", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_guider_calibration_propagates_no_displacement_value_error() -> None:
    """Verify a run with no measurable displacement raises ProcessingError."""
    steps = GuiderCalibrationSteps(
        pulse_ra=lambda duration_sec: True,
        pulse_dec=lambda duration_sec: True,
        measure_guide_star_centroid=lambda: (5.0, 5.0),
    )

    with pytest.raises(ProcessingError, match="no measurable star displacement"):
        run_guider_calibration(steps, "cal-4", "cam-1", "scope-1", arcsec_per_pixel=2.0)


def test_run_backlash_calibration_returns_elapsed_time_to_first_motion() -> None:
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


def test_run_backlash_calibration_reports_full_budget_if_motion_never_detected() -> None:
    """Verify exhausting the probe budget returns the full elapsed time."""
    steps = BacklashCalibrationSteps(
        pulse_dec=lambda direction, duration_sec: True, measure_guide_star_centroid=lambda: (0.0, 0.0)
    )

    backlash_ms = run_backlash_calibration(steps, reversal_test_pulse_sec=0.05, max_test_pulses=10)

    assert backlash_ms == pytest.approx(10 * 0.05 * 1000.0)


def test_run_backlash_calibration_uses_the_declared_values_by_default() -> None:
    """Leaving every limit out uses the declared values."""
    pulse_calls: list[tuple[str, float]] = []

    def pulse_dec(direction: str, duration_sec: float) -> bool:
        pulse_calls.append((direction, duration_sec))
        return True

    steps = BacklashCalibrationSteps(pulse_dec=pulse_dec, measure_guide_star_centroid=lambda: (0.0, 0.0))

    backlash_ms = run_backlash_calibration(steps)

    test_pulse_seconds = BACKLASH_REVERSAL_TEST_PULSE_SECONDS.default
    test_pulse_count = int(BACKLASH_MAX_TEST_PULSES.default)
    assert pulse_calls[0] == ("north", pytest.approx(BACKLASH_SETTLE_PULSE_SECONDS.default))
    assert len(pulse_calls) == 1 + test_pulse_count
    assert backlash_ms == pytest.approx(test_pulse_count * test_pulse_seconds * 1000.0)
    assert BACKLASH_MOTION_THRESHOLD_PX.default == pytest.approx(0.5)


def test_run_backlash_calibration_stops_at_the_declared_motion_threshold() -> None:
    """A move over the declared threshold ends the probe; under it does not."""
    threshold = BACKLASH_MOTION_THRESHOLD_PX.default
    centroids = iter([(0.0, 0.0), (0.0, threshold * 0.9), (0.0, threshold * 0.9 + threshold * 1.1)])
    steps = BacklashCalibrationSteps(
        pulse_dec=lambda direction, duration_sec: True, measure_guide_star_centroid=lambda: next(centroids)
    )

    backlash_ms = run_backlash_calibration(steps)

    assert backlash_ms == pytest.approx(2 * BACKLASH_REVERSAL_TEST_PULSE_SECONDS.default * 1000.0)


def test_run_polar_alignment_assist_fits_from_the_given_attempts() -> None:
    """Verify the assist routine fits directly from the passed-in attempts."""
    model = run_polar_alignment_assist(_ATTEMPTS, latitude_deg=39.7)

    assert model.sample_count == len(_ATTEMPTS)
    assert model.confidence != "insufficient_data" or model.sample_count < 4


def test_run_polar_alignment_assist_reports_insufficient_data_early() -> None:
    """Verify fewer than 4 attempts reports insufficient_data, not a fit."""
    model = run_polar_alignment_assist(_ATTEMPTS[:2], latitude_deg=45.0)

    assert model.confidence == "insufficient_data"
