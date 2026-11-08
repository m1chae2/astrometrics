"""Purpose: Tests for the unguided mount error reconstruction and fit.

Description: Builds synthetic guiding runs from a known mount (a sine of
known period, amplitude and phase, a known drift and random jitter) and a
simple guider that corrects what it measures. The tests check that
`measure_run` recovers the inputs inside its own stated uncertainties, that a
dither step does not corrupt the fit, that a run too short or too noisy makes
no claim, and that runs pool correctly.
"""

import math

import numpy as np
import pytest

from wayfindinglib.analytics.test.unguided_error_helpers import RATE, START, synthetic_run
from wayfindinglib.analytics.unguided_mount_error import (
    MINIMUM_AMPLITUDE_SIGNIFICANCE,
    GuideRunSeries,
    correction_sign,
    measure_run,
    reconstruct_unguided_error,
    split_stretches,
    summarize_runs,
)


def test_reconstruction_adds_back_the_corrections_sent_before_each_frame() -> None:
    """Frame k is corrected by the pulses of frames before k, not its own."""
    measured = np.array([1.0, 0.5, 0.2])
    pulses = np.array([-100.0, -50.0, 0.0])
    unguided = reconstruct_unguided_error(measured, pulses, 2.0)
    # Pulses move the star by -0.2 and -0.1 arcsec, so the mount's own error
    # is the measured one plus what the guider already took out.
    assert unguided == pytest.approx([1.0, 0.7, 0.5])


def test_stretches_split_at_a_pause_of_a_minute() -> None:
    """A gap of 60 s or more ends a stretch."""
    times = np.array([0.0, 3.0, 6.0, 100.0, 103.0])
    assert split_stretches(times) == [slice(0, 3), slice(3, 5)]


def test_the_periodic_error_is_recovered_inside_its_uncertainty() -> None:
    """Period, amplitude and phase come back within their uncertainties."""
    result = measure_run(synthetic_run())
    fit = result.periodic_error
    assert fit is not None
    assert result.significant_periodic_error
    assert abs(fit.period_seconds - 480.0) < max(4.0 * fit.period_uncertainty_seconds, 1.0)
    assert abs(fit.amplitude_arcsec - 4.0) < max(4.0 * fit.amplitude_uncertainty_arcsec, 0.1)
    phase_difference = (fit.phase_radians - 1.1 + math.pi) % (2.0 * math.pi) - math.pi
    assert abs(phase_difference) < max(4.0 * fit.phase_uncertainty_radians, 0.05)
    assert fit.reference_epoch_seconds == pytest.approx(START)


@pytest.mark.parametrize("seed", [2, 3, 4])
def test_the_recovery_holds_for_other_noise_and_periods(seed: int) -> None:
    """Other periods, phases and noise draws are recovered closely."""
    period = 300.0 + 100.0 * seed
    phase = 0.7 * seed
    result = measure_run(synthetic_run(seed=seed, period=period, phase=phase, noise=0.5, duration=3000.0))
    fit = result.periodic_error
    assert fit is not None
    assert fit.period_seconds == pytest.approx(period, rel=0.02)
    assert fit.amplitude_arcsec == pytest.approx(4.0, rel=0.1)


def test_drift_and_random_terms_are_recovered() -> None:
    """The drift is found in arcseconds per minute, and so is the jitter."""
    result = measure_run(synthetic_run(drift_per_minute=0.8, walk=0.0, noise=0.4))
    assert result.ra is not None
    assert result.ra.drift_arcsec_per_minute == pytest.approx(
        0.8, abs=max(4.0 * result.ra.drift_uncertainty_arcsec_per_minute, 0.05)
    )
    assert result.ra.random_rms_arcsec == pytest.approx(0.4, rel=0.15)
    assert result.ra.random_step_rms_arcsec == pytest.approx(0.4 * math.sqrt(2.0), rel=0.15)
    assert result.dec is not None
    assert result.dec.drift_arcsec_per_minute == pytest.approx(0.2, abs=0.05)
    assert result.dec.random_rms_arcsec == pytest.approx(0.1, rel=0.2)


def test_a_dither_does_not_corrupt_the_fit() -> None:
    """Steps from moving the lock position are cut out; the sine is found."""
    result = measure_run(synthetic_run(dither_every=60, dither=5.0))
    fit = result.periodic_error
    assert fit is not None
    assert fit.period_seconds == pytest.approx(480.0, rel=0.02)
    assert fit.amplitude_arcsec == pytest.approx(4.0, rel=0.1)
    assert result.ra.drift_arcsec_per_minute == pytest.approx(0.5, abs=0.15)


def test_a_run_too_short_for_two_cycles_of_a_worm_period_is_not_claimed() -> None:
    """A ten-minute run cannot hold two 480 s cycles; no claim is made."""
    result = measure_run(synthetic_run(duration=600.0))
    assert not result.significant_periodic_error
    assert result.ra is not None


def test_a_run_with_too_few_samples_fits_no_periodic_error() -> None:
    """Under 60 samples there is no periodic-error fit; a note says so."""
    result = measure_run(synthetic_run(duration=150.0))
    assert result.periodic_error is None
    assert any("too few to fit a periodic error" in note for note in result.notes)


def test_noise_alone_is_not_called_a_periodic_error() -> None:
    """With no sine in the mount, the fit is below the significance limit."""
    result = measure_run(synthetic_run(amplitude=0.0, noise=0.6, walk=0.0))
    assert not result.significant_periodic_error
    if result.periodic_error is not None:
        assert result.periodic_error.significance < MINIMUM_AMPLITUDE_SIGNIFICANCE


def test_a_run_without_a_guide_rate_says_so() -> None:
    """With no rate the pulses cannot be turned into arcseconds."""
    result = measure_run(synthetic_run(rate=None))
    assert result.ra is None
    assert result.periodic_error is None
    assert any("no RA guide rate" in note for note in result.notes)


def test_a_pause_inside_a_run_does_not_break_the_reconstruction() -> None:
    """Two stretches 10 minutes apart share one sine."""
    first = synthetic_run(seed=7, duration=1500.0)
    second = synthetic_run(
        seed=8, duration=1500.0, start=START + 2100.0, phase=1.1 + 2.0 * math.pi * 2100.0 / 480.0
    )
    joined = GuideRunSeries(
        run_id="joined",
        session_id="2026-01-01",
        times=np.concatenate([first.times, second.times]),
        ra_error_arcsec=np.concatenate([first.ra_error_arcsec, second.ra_error_arcsec]),
        dec_error_arcsec=np.concatenate([first.dec_error_arcsec, second.dec_error_arcsec]),
        ra_pulse_ms=np.concatenate([first.ra_pulse_ms, second.ra_pulse_ms]),
        dec_pulse_ms=np.concatenate([first.dec_pulse_ms, second.dec_pulse_ms]),
        ra_rate_arcsec_per_second=RATE,
        dec_rate_arcsec_per_second=RATE,
    )
    fit = measure_run(joined).periodic_error
    assert fit is not None
    assert fit.period_seconds == pytest.approx(480.0, rel=0.01)
    assert fit.amplitude_arcsec == pytest.approx(4.0, rel=0.1)


def test_pooling_averages_runs_and_reports_their_scatter() -> None:
    """Pooled period and amplitude sit between the runs; no pooled phase."""
    runs = [
        measure_run(
            synthetic_run(seed=seed, period=480.0 + 2.0 * seed, amplitude=3.0 + 0.2 * seed, night=night)
        )
        for seed, night in ((1, "2026-01-01"), (2, "2026-01-02"), (3, "2026-01-03"))
    ]
    summary = summarize_runs(runs)
    pooled = summary.periodic_error
    assert pooled is not None
    assert pooled.runs_used == 3
    assert pooled.phase_radians is None
    assert pooled.period_seconds == pytest.approx(484.0, abs=3.0)
    assert pooled.amplitude_arcsec == pytest.approx(3.4, abs=0.4)
    assert pooled.period_scatter_seconds is not None
    assert summary.nights_used == ["2026-01-01", "2026-01-02", "2026-01-03"]
    assert summary.ra.runs_used == 3
    assert summary.dec.runs_used == 3
    assert summary.ra.drift_median_arcsec_per_minute == pytest.approx(0.5, abs=0.2)


def test_outlier_runs_do_not_move_the_pooled_terms() -> None:
    """Two wild runs among six typical ones leave the medians typical."""
    typical = [
        measure_run(synthetic_run(seed=seed, drift_per_minute=-5.0, noise=0.4, night=f"2026-01-0{seed}"))
        for seed in range(1, 7)
    ]
    wild = [
        measure_run(synthetic_run(seed=20, drift_per_minute=40.0, noise=6.0, night="2026-02-01")),
        measure_run(synthetic_run(seed=21, drift_per_minute=-60.0, noise=9.0, night="2026-02-02")),
    ]
    clean = summarize_runs(typical).ra
    mixed = summarize_runs(typical + wild).ra
    assert clean is not None and mixed is not None
    assert mixed.drift_median_arcsec_per_minute == pytest.approx(-5.0, abs=1.0)
    assert mixed.drift_median_arcsec_per_minute == pytest.approx(
        clean.drift_median_arcsec_per_minute, abs=0.7
    )
    assert mixed.drift_spread_arcsec_per_minute < 3.0
    assert mixed.random_step_median_arcsec == pytest.approx(clean.random_step_median_arcsec, rel=0.5)


def test_an_outlier_period_does_not_move_the_pooled_periodic_error() -> None:
    """A wild period and amplitude leave the pooled median near the rest."""
    runs = [
        measure_run(synthetic_run(seed=seed, period=480.0, amplitude=4.0, night=f"2026-01-0{seed}"))
        for seed in range(1, 6)
    ]
    runs.append(measure_run(synthetic_run(seed=9, period=900.0, amplitude=12.0, night="2026-02-01")))
    pooled = summarize_runs(runs).periodic_error
    assert pooled is not None
    assert pooled.period_seconds == pytest.approx(480.0, rel=0.02)
    assert pooled.amplitude_arcsec == pytest.approx(4.0, rel=0.15)
    assert pooled.period_scatter_seconds is not None and pooled.period_scatter_seconds < 30.0


def test_pooling_nothing_gives_an_empty_summary() -> None:
    """With no run there is no pooled error and no night."""
    summary = summarize_runs([])
    assert summary.periodic_error is None
    assert summary.ra is None
    assert summary.runs_measured == 0


def test_the_direction_of_a_pulse_is_read_from_the_run() -> None:
    """A guider that corrects gives a negative correlation, and sign +1."""
    run = synthetic_run()
    assert correction_sign(run.ra_error_arcsec, run.ra_pulse_ms) == pytest.approx(1.0)
    assert correction_sign(run.ra_error_arcsec, -run.ra_pulse_ms) == pytest.approx(-1.0)


def test_an_axis_whose_pulses_run_the_other_way_gives_the_same_result() -> None:
    """The Ekos declination convention (opposite to PHD2's) is handled."""
    normal = synthetic_run(amplitude=0.0, drift_per_minute=0.0)
    flipped = GuideRunSeries(
        run_id="flipped",
        session_id=normal.session_id,
        times=normal.times,
        ra_error_arcsec=normal.ra_error_arcsec,
        dec_error_arcsec=normal.dec_error_arcsec,
        ra_pulse_ms=-normal.ra_pulse_ms,
        dec_pulse_ms=normal.dec_pulse_ms,
        ra_rate_arcsec_per_second=RATE,
        dec_rate_arcsec_per_second=RATE,
    )
    # The flipped series is one where a positive pulse lowers the error: the
    # mount motion it implies is the same as the original's.
    assert measure_run(flipped).ra.random_rms_arcsec == pytest.approx(
        measure_run(normal).ra.random_rms_arcsec, rel=1e-6
    )


def test_pulses_that_do_not_follow_the_error_leave_the_axis_unmeasured() -> None:
    """Pulses unrelated to the error mean guiding was off: no claim is made."""
    run = synthetic_run()
    generator = np.random.default_rng(9)
    shuffled = GuideRunSeries(
        run_id="noise",
        session_id=run.session_id,
        times=run.times,
        ra_error_arcsec=run.ra_error_arcsec,
        dec_error_arcsec=run.dec_error_arcsec,
        ra_pulse_ms=generator.permutation(run.ra_pulse_ms),
        dec_pulse_ms=run.dec_pulse_ms,
        ra_rate_arcsec_per_second=RATE,
        dec_rate_arcsec_per_second=RATE,
    )
    result = measure_run(shuffled)
    assert result.ra is None
    assert any("do not follow the error" in note for note in result.notes)


def test_an_unguided_axis_is_measured_from_its_raw_error() -> None:
    """With no corrections sent, the measured error is the mount's error."""
    result = measure_run(synthetic_run(seed=3))
    assert result.dec is not None
    assert any("Fewer than 10 DEC corrections" in note for note in result.notes)
