"""Purpose: Unit tests for the guiding spectrum analysis across many runs.

Description: Real guide samples come in separate stretches, one per guiding
run, with pauses of minutes to months between them. These tests build such data
from a signal with a known period and check that the period is recovered, that
the pauses are never bridged, that the cadence may change between runs, and
that a short run cannot invent a long period. Each of these failed on the
earlier version, which joined every sample into one evenly spaced series.
"""

import math

import numpy as np
import pytest

from wayfindinglib.analytics.guiding_spectrum import (
    SEGMENT_BREAK_SECONDS,
    SLOW_DRIFT,
    analyze_guiding_telemetry,
)

_EPOCH = 1_700_000_000.0
"""An arbitrary start time, as seconds since the Unix epoch."""

_WORM_PERIOD = 480.0
"""The period of the planted repeating error, in seconds."""

_AMPLITUDE = 3.0
"""Its amplitude, in arcseconds."""


def _run(start: float, duration: float, cadence: float, pulse: float = 0.0, noise: float = 0.0) -> list[dict]:
    """Build one continuous guiding run of a worm-period error.

    The signal depends on the absolute time, so two runs taken hours apart
    carry the same repeating error at different phases, as a real mount does.

    Returns
    -------
    samples : `list` [`dict`]
        The run's samples.
    """
    generator = np.random.default_rng(int(start) % 1000)
    times = np.arange(start, start + duration, cadence)
    return [
        {
            "timestamp": float(t),
            "dra": _AMPLITUDE * math.sin(2.0 * math.pi * (t - _EPOCH) / _WORM_PERIOD)
            + float(generator.normal(0.0, noise)),
            "ddec": 0.0,
            "pulse_dec": pulse,
        }
        for t in times
    ]


def test_the_period_is_found_across_runs_separated_by_hours() -> None:
    """Verify a long pause between two runs does not move the period."""
    samples = _run(_EPOCH, 1500.0, 3.2) + _run(_EPOCH + 3 * 3600.0, 1500.0, 3.2)

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.dominant_period_seconds == pytest.approx(_WORM_PERIOD, abs=15.0)
    assert analysis.peaks[0].amplitude_arcsec == pytest.approx(_AMPLITUDE, abs=0.5)
    assert analysis.peaks[0].probable_source == "Worm Fundamental Period"


def test_runs_on_different_nights_are_not_bridged() -> None:
    """Verify nights a month apart give the same answer as one night."""
    one_night = analyze_guiding_telemetry(_run(_EPOCH, 2400.0, 3.2))
    two_nights = analyze_guiding_telemetry(
        _run(_EPOCH, 1500.0, 3.2) + _run(_EPOCH + 30 * 86400.0, 1500.0, 3.2)
    )

    assert two_nights.dominant_period_seconds == pytest.approx(one_night.dominant_period_seconds, abs=15.0)
    assert two_nights.periodic_error_peak_to_peak_arcsec == pytest.approx(
        one_night.periodic_error_peak_to_peak_arcsec, abs=1.0
    )


def test_the_duration_is_the_time_spent_guiding_not_the_span() -> None:
    """Verify a pause is not counted as guiding."""
    samples = _run(_EPOCH, 1500.0, 3.2) + _run(_EPOCH + 86400.0, 1500.0, 3.2)

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.duration_seconds == pytest.approx(3000.0, abs=10.0)
    assert analysis.sample_count == len(samples)
    assert "2 continuous stretch" in analysis.message


def test_a_change_of_guide_cadence_between_runs_does_not_move_the_period() -> None:
    """Verify runs at 2 s and at 5 s give the right period together."""
    samples = _run(_EPOCH, 1500.0, 2.0) + _run(_EPOCH + 7200.0, 1500.0, 5.0)

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.dominant_period_seconds == pytest.approx(_WORM_PERIOD, abs=15.0)


def test_runs_too_short_for_two_cycles_cannot_invent_a_long_period() -> None:
    """Verify 300 s runs give no peak above 150 s, half their length."""
    samples = []
    for index in range(6):
        samples += _run(_EPOCH + index * 1800.0, 300.0, 3.2, noise=0.3)

    analysis = analyze_guiding_telemetry(samples)

    assert all(peak.period_seconds <= 150.0 for peak in analysis.peaks)


def test_the_time_scale_comes_from_the_timestamps_not_the_sample_count() -> None:
    """Verify sparse and dense runs together give the right period."""
    samples = _run(_EPOCH, 1500.0, 6.0) + _run(_EPOCH + 5000.0, 1500.0, 1.5)

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.dominant_period_seconds == pytest.approx(_WORM_PERIOD, abs=15.0)


def test_declination_reversals_are_not_counted_across_a_pause() -> None:
    """Verify opposite pulses either side of a pause are not a reversal."""
    north = _run(_EPOCH, 600.0, 3.2, pulse=200.0)
    south = _run(_EPOCH + 7200.0, 600.0, 3.2, pulse=-200.0)

    analysis = analyze_guiding_telemetry(north + south)

    assert analysis.dec_backlash_estimate_ms is None


def test_declination_reversals_inside_a_run_are_counted() -> None:
    """Verify the backlash estimate still works within a run."""
    samples = _run(_EPOCH, 1200.0, 3.2)
    for index, sample in enumerate(samples):
        sample["pulse_dec"] = 250.0 if (index // 20) % 2 == 0 else -250.0

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.dec_backlash_estimate_ms == pytest.approx(5000.0, rel=0.1)


def test_too_few_samples_say_so() -> None:
    """Verify fewer than 16 samples give a clear message."""
    analysis = analyze_guiding_telemetry(_run(_EPOCH, 40.0, 3.2))

    assert analysis.sample_count < 16
    assert "at least 16" in analysis.message


def test_no_stretch_long_enough_says_so() -> None:
    """Verify many tiny runs that are each too short give a clear message."""
    samples = []
    for index in range(10):
        samples += _run(_EPOCH + index * 3600.0, 12.0, 3.0)

    analysis = analyze_guiding_telemetry(samples)

    assert analysis.duration_seconds == pytest.approx(0.0)
    assert "No continuous stretch" in analysis.message


def test_the_break_between_stretches_is_a_minute() -> None:
    """Verify the pause length that splits stretches."""
    assert SEGMENT_BREAK_SECONDS == pytest.approx(60.0)


def test_a_slow_drift_is_listed_but_is_never_the_dominant_period() -> None:
    """Verify slow wander is labelled and never the phase-locked period."""
    samples = _run(_EPOCH, 6000.0, 3.2)
    for sample in samples:
        sample["dra"] += 8.0 * math.sin(2.0 * math.pi * (sample["timestamp"] - _EPOCH) / 3000.0)

    analysis = analyze_guiding_telemetry(samples)
    slow = [peak for peak in analysis.peaks if peak.period_seconds > 650.0]

    assert slow
    assert all(peak.probable_source == SLOW_DRIFT for peak in slow)
    assert analysis.peaks[0].probable_source == SLOW_DRIFT
    assert analysis.dominant_period_seconds == pytest.approx(_WORM_PERIOD, abs=15.0)
