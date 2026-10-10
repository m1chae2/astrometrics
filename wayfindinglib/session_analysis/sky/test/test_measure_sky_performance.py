"""Purpose: Unit tests for sky-position processing.

Description: Plants a known effect in synthetic nights and checks that the
analysis finds it, finds nothing when there is none, is not fooled by good and
bad nights, and does not judge a part of the sky that too few nights observed
alongside another.
"""

from typing import Any

import pytest

from wayfindinglib.models.session.sky_quality import SkySample
from wayfindinglib.session_analysis.sky.processing.measure_sky_performance import measure_sky_performance


def _bin(performance: Any, metric: str, dimension: str, label: str) -> Any:
    """Find one part of the sky in a result.

    Returns
    -------
    result : `SkyBinResult`
        The comparison of that part.
    """
    (result,) = [m for m in performance.metrics if m.metric == metric]
    (found,) = [b for b in result.bins if b.dimension == dimension and b.label == label]
    return found


def test_a_planted_low_altitude_effect_is_found(make_nights: Any) -> None:
    """Verify stars 25 percent wider at 35 degrees make that band poor."""
    samples = make_nights(bands={35.0: 1.25, 65.0: 1.0})

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    low = _bin(performance, "star_width", "altitude", "30-45 deg")
    high = _bin(performance, "star_width", "altitude", "60-75 deg")
    assert low.judged
    assert low.is_poor
    assert low.worse_by == pytest.approx(0.11, abs=0.03)
    assert low.z_score >= 3.0
    assert not high.is_poor


def test_no_effect_finds_nothing(make_nights: Any) -> None:
    """Verify uniform nights give no poor part of the sky."""
    performance = measure_sky_performance(make_nights(bands={35.0: 1.0, 65.0: 1.0}), 0.0, 90.0, 0.10)

    assert not any(bin_.is_poor for metric in performance.metrics for bin_ in metric.bins)
    assert performance.suggested_minimum_altitude_degrees is None


def test_an_effect_smaller_than_the_tolerance_is_not_poor(make_nights: Any) -> None:
    """Verify a clear but small difference is not worth acting on."""
    samples = make_nights(bands={35.0: 1.06, 65.0: 1.0}, noise=0.005)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    low = _bin(performance, "star_width", "altitude", "30-45 deg")
    assert low.judged
    assert low.z_score >= 3.0
    assert low.worse_by < 0.10
    assert not low.is_poor


def test_a_poor_night_everywhere_is_not_a_poor_region(make_night: Any) -> None:
    """Verify comparing within each night removes that night's seeing."""
    samples = []
    for index in range(8):
        factor = 2.0 if index == 3 else 1.0
        samples += make_night(f"2026-02-{index + 1:02d}", night_factor=factor, seed=index)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    assert not any(bin_.is_poor for metric in performance.metrics for bin_ in metric.bins)


def test_nights_that_stayed_in_one_part_do_not_judge_it(make_night: Any) -> None:
    """Verify a night in a single band says nothing about that band.

    Regression test, found on real data: such a night's relative value is 1 by
    construction, which made the scatter zero and the significance test
    meaningless.
    """
    samples = []
    for index in range(8):
        altitude = 35.0 if index % 2 else 65.0
        samples += make_night(f"2026-03-{index + 1:02d}", bands={altitude: 1.0}, seed=index)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    low = _bin(performance, "star_width", "altitude", "30-45 deg")
    assert low.samples == 40
    assert low.nights == 0
    assert not low.judged


def test_a_part_seen_on_too_few_nights_is_not_judged(make_night: Any) -> None:
    """Verify two nights are not enough to judge a part."""
    samples = []
    for index in range(8):
        bands = {35.0: 1.4, 65.0: 1.0} if index < 2 else {65.0: 1.0, 55.0: 1.0}
        samples += make_night(f"2026-04-{index + 1:02d}", bands=bands, seed=index)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    low = _bin(performance, "star_width", "altitude", "30-45 deg")
    assert low.nights == 2
    assert not low.judged
    assert not low.is_poor


def test_less_round_stars_are_worse(make_nights: Any) -> None:
    """Verify for roundness a smaller value is the poor direction."""
    samples = make_nights(bands={35.0: 0.80, 65.0: 1.0}, metric="star_roundness", base=0.9, noise=0.005)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    low = _bin(performance, "star_roundness", "altitude", "30-45 deg")
    assert low.is_poor
    assert low.median_relative_value < 1.0
    assert low.worse_by > 0.0


def test_guiding_error_uses_one_measurement_per_run(make_night: Any) -> None:
    """Verify runs, one measurement each, are compared like frames."""
    samples = []
    for index in range(8):
        samples += make_night(
            f"2026-05-{index + 1:02d}",
            bands={35.0: 1.5, 45.0: 1.0, 65.0: 1.0},
            per_band=1,
            metric="guiding_error",
            base=1.0,
            noise=0.01,
            seed=index,
        )

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    assert _bin(performance, "guiding_error", "altitude", "30-45 deg").judged


def test_the_suggested_minimum_altitude_is_the_top_of_the_highest_poor_band(make_nights: Any) -> None:
    """Verify the suggestion comes from star width in the altitude bands."""
    samples = make_nights(bands={35.0: 1.3, 50.0: 1.0, 65.0: 1.0})

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    assert performance.suggested_minimum_altitude_degrees == pytest.approx(45.0)


def test_nights_with_too_few_measurements_are_left_out(make_night: Any) -> None:
    """Verify a night of a few frames gives no typical value to compare."""
    samples = make_night("2026-06-01", per_band=2)

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    assert performance.metrics == []


def test_unknown_positions_place_a_sample_nowhere() -> None:
    """Verify a sample with no pointing joins no part of the sky."""
    samples = [SkySample(night="2026-07-01", metric="star_width", value=5.0) for _ in range(12)]

    performance = measure_sky_performance(samples, 0.0, 90.0, 0.10)

    (result,) = performance.metrics
    assert all(bin_.samples == 0 for bin_ in result.bins)
