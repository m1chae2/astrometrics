"""Purpose: Unit tests for guiding processing.

Description: Verifies the guiding error is measured robustly, that its effect
on star width follows the equipment's own star width, and that the
declination drift and the polar alignment bound derived from it are correct.
"""

import math
from typing import Any

import pytest

from wayfindinglib.session_analysis.guiding.processing.measure_guiding_performance import (
    measure_guiding_performance,
)


def _measure(samples: Any, runs: Any, envelope: Any, match: str = "exact") -> Any:
    """Run processing with this module's usual arguments.

    Returns
    -------
    performance : `GuidingPerformance`
        The measurements.
    """
    return measure_guiding_performance(samples, runs, envelope, match)


def test_the_guiding_error_matches_the_error_the_samples_were_drawn_with(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify the robust error recovers a known sigma."""
    performance = _measure(make_samples(count=4000, sigma=1.2), [make_run()], make_envelope())

    assert performance.rms_ra_arcsec == pytest.approx(1.2, rel=0.08)
    assert performance.rms_dec_arcsec == pytest.approx(1.2, rel=0.08)
    assert performance.rms_per_axis_arcsec == pytest.approx(1.2, rel=0.08)


def test_excursions_do_not_distort_the_guiding_error(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a few wild samples leave the measured error alone."""
    envelope = make_envelope()
    clean = make_samples(count=2000, sigma=1.0)
    wild = make_samples(count=2000, sigma=1.0)
    for sample in wild[:40]:
        sample["dra"] = 500.0

    clean_result = _measure(clean, [make_run()], envelope)
    wild_result = _measure(wild, [make_run()], envelope)

    assert wild_result.rms_per_axis_arcsec == pytest.approx(clean_result.rms_per_axis_arcsec, rel=0.1)
    assert wild_result.rms_including_excursions_arcsec > 10.0 * clean_result.rms_per_axis_arcsec


def test_without_limits_no_sample_is_an_excursion(make_samples: Any, make_run: Any) -> None:
    """Verify the error is still measured when no limit applies."""
    performance = _measure(make_samples(count=2000, sigma=1.0), [make_run()], None, match="none")

    assert performance.rms_per_axis_arcsec == pytest.approx(1.0, rel=0.1)
    assert performance.expected_star_widening_fraction is None


def test_star_widening_follows_the_equipments_measured_star_width(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify widening = sqrt(1 + (2.3548 x error / width)^2) - 1."""
    samples = make_samples(count=4000, sigma=1.0)
    performance = _measure(samples, [make_run()], make_envelope(star_width=5.59))

    expected = math.sqrt(1.0 + (2.3548 * performance.rms_per_axis_arcsec / 5.59) ** 2) - 1.0
    assert performance.expected_star_widening_fraction == pytest.approx(expected, rel=1e-3)


def test_a_sharper_equipment_is_widened_more_by_the_same_guiding(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify the same error costs more on sharper equipment."""
    samples = make_samples(count=2000, sigma=1.0)

    blurry = _measure(samples, [make_run()], make_envelope(star_width=8.0))
    sharp = _measure(samples, [make_run()], make_envelope(star_width=3.0))

    assert sharp.expected_star_widening_fraction > blurry.expected_star_widening_fraction


def test_each_run_gets_its_own_error_and_sky_position(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify samples are assigned to the run they fall in."""
    first = make_run(0, start=1.79e9, duration=1000.0)
    second = make_run(1, start=1.79e9 + 3000.0, duration=1000.0)
    samples = make_samples(count=200, start=1.79e9 + 10.0, sigma=0.5) + make_samples(
        count=200, start=1.79e9 + 3010.0, sigma=2.0, seed=2
    )

    performance = _measure(samples, [first, second], make_envelope())

    assert [run.samples for run in performance.runs] == [200, 200]
    assert performance.runs[0].rms_per_axis_arcsec < performance.runs[1].rms_per_axis_arcsec
    assert performance.runs[0].altitude_degrees == pytest.approx(66.0)
    assert performance.runs[0].pier_side == "West"


def test_a_run_with_too_few_samples_gets_no_error_of_its_own(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a stretch too short to be stable is not given an error."""
    run = make_run(0, start=1.79e9, duration=100.0)

    performance = _measure(make_samples(count=10, start=1.79e9 + 5.0), [run], make_envelope())

    assert performance.runs[0].rms_per_axis_arcsec is None


def test_the_net_dec_correction_is_the_pulse_total_times_speed_over_the_run(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify rate = net pulse length x calibrated speed / run length."""
    run = make_run(0, duration=1200.0, dec_rate=7.5)
    samples = make_samples(count=300, pulse_dec_ms=40.0)

    performance = _measure(samples, [run], make_envelope())

    net_arcsec = 300 * 0.040 * 7.5
    assert performance.net_dec_correction_arcsec_per_minute == pytest.approx(net_arcsec / 20.0)
    assert performance.runs[0].net_dec_correction_arcsec_per_minute == pytest.approx(net_arcsec / 20.0)


def test_a_run_shorter_than_the_periodic_error_gives_no_correction_rate(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a short run is excluded: periodic error would contaminate it."""
    run = make_run(0, duration=300.0)

    performance = _measure(make_samples(count=90, pulse_dec_ms=40.0), [run], make_envelope())

    assert performance.net_dec_correction_arcsec_per_minute is None


def test_no_declination_corrections_means_no_rate(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a run that never guided in Dec has no rate to report."""
    run = make_run(duration=1200.0)

    performance = _measure(make_samples(count=300, pulse_dec_ms=0.0), [run], make_envelope())

    assert performance.net_dec_correction_arcsec_per_minute is None


def test_an_unknown_calibrated_speed_means_no_rate(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify the pulse lengths cannot be converted without a speed."""
    run = make_run(0, duration=1200.0, dec_rate=None)

    performance = _measure(make_samples(count=300, pulse_dec_ms=40.0), [run], make_envelope())

    assert performance.net_dec_correction_arcsec_per_minute is None


@pytest.mark.parametrize("impossible_speed", [0.0, -3.0, 419.1, 2600.9])
def test_a_run_with_an_impossible_calibrated_speed_is_left_out(
    make_samples: Any, make_run: Any, make_envelope: Any, impossible_speed: float
) -> None:
    """Verify garbage calibrations do not produce a correction rate.

    Regression test, found on real data: the night of 2026-05-23 had runs
    calibrated at 419 and 2601 arcsec/s, and converting their pulses with
    those speeds gave a meaningless rate that then dominated the night.
    """
    run = make_run(0, duration=1200.0, dec_rate=impossible_speed)

    performance = _measure(make_samples(count=300, pulse_dec_ms=40.0), [run], make_envelope())

    assert performance.net_dec_correction_arcsec_per_minute is None


def test_only_the_valid_run_counts_when_another_has_a_garbage_calibration(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify a bad run beside a good one changes nothing."""
    good = make_run(0, start=1.79e9, duration=1200.0, dec_rate=7.5)
    bad = make_run(1, start=1.79e9 + 3000.0, duration=1200.0, dec_rate=2600.9)
    samples = make_samples(count=300, start=1.79e9 + 5.0, pulse_dec_ms=40.0) + make_samples(
        count=300, start=1.79e9 + 3005.0, pulse_dec_ms=40.0, seed=2
    )

    performance = _measure(samples, [good, bad], make_envelope())

    assert performance.net_dec_correction_arcsec_per_minute == pytest.approx(300 * 0.040 * 7.5 / 20.0)


def test_runs_combine_as_a_duration_weighted_signed_mean(
    make_samples: Any, make_run: Any, make_envelope: Any
) -> None:
    """Verify opposite-direction runs offset each other, weighted by length."""
    first = make_run(0, start=1.79e9, duration=1200.0, dec_rate=7.5)
    second = make_run(1, start=1.79e9 + 3000.0, duration=2400.0, dec_rate=7.5)
    samples = make_samples(count=300, start=1.79e9 + 5.0, pulse_dec_ms=40.0) + make_samples(
        count=600, start=1.79e9 + 3005.0, pulse_dec_ms=-40.0, seed=2
    )

    performance = _measure(samples, [first, second], make_envelope())

    first_rate = 300 * 0.040 * 7.5 / 20.0
    second_rate = -600 * 0.040 * 7.5 / 40.0
    expected = (first_rate * 1200.0 + second_rate * 2400.0) / 3600.0
    assert performance.net_dec_correction_arcsec_per_minute == pytest.approx(expected)


def test_excursion_pulses_are_not_counted(make_samples: Any, make_run: Any, make_envelope: Any) -> None:
    """Verify the huge corrections issued on a wrong star are left out."""
    envelope = make_envelope()
    samples = make_samples(count=300, pulse_dec_ms=10.0)
    for sample in samples[:5]:
        sample["dra"] = 1000.0
        sample["pulse_dec"] = 5000.0

    performance = _measure(samples, [make_run(duration=1200.0)], envelope)

    assert performance.net_dec_correction_arcsec_per_minute == pytest.approx(295 * 0.010 * 7.5 / 20.0)


def test_no_samples_gives_an_empty_measurement(make_run: Any, make_envelope: Any) -> None:
    """Verify an empty night does not raise."""
    performance = _measure([], [make_run()], make_envelope())

    assert performance.rms_per_axis_arcsec is None
    assert performance.rms_including_excursions_arcsec is None
