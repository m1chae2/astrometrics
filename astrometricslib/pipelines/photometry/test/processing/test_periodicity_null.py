"""Tests for the noise-only versions the period search compares with.

A point-by-point shuffle destroys correlated noise, so a light curve of pure
red noise looked significant almost every time. The block null keeps the
correlation. The statistical tests use simulated light curves with fixed seeds
and nothing real in them, so every "significant" call is a false alarm.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.processing import periodicity_search as search


def correlated_noise(count: int, phi: float, generator: np.random.Generator) -> np.ndarray:
    """Make an AR(1) light curve: each point partly repeats the one before.

    Parameters
    ----------
    count : `int`
        The number of points.
    phi : `float`
        How much of the last point carries over (0 is white noise).
    generator : `numpy.random.Generator`
        The source of randomness.

    Returns
    -------
    flux : `numpy.ndarray`
        A light curve near 1 with 1% scatter.
    """
    values = np.zeros(count)
    steps = generator.standard_normal(count)
    for index in range(1, count):
        values[index] = phi * values[index - 1] + steps[index]
    return 1.0 + 0.01 * values


# Three nights of 25 measurements, ten minutes apart.
NIGHTLY_TIMES = np.concatenate([night + np.arange(25) * (10 / 1440) for night in range(3)])


def false_alarm_rate(phi: float, block_length: int | None, simulations: int = 20) -> float:
    """Measure how often pure noise is called significant at the 5% level.

    Returns
    -------
    rate : `float`
        The share of simulated light curves with false-alarm probability at
        or below 0.05. It should be about 0.05.
    """
    generator = np.random.default_rng(31)
    hits = 0
    for _ in range(simulations):
        flux = correlated_noise(NIGHTLY_TIMES.size, phi, generator)
        result = search.lomb_scargle_search(NIGHTLY_TIMES, flux, shuffle_count=99, block_length=block_length)
        hits += result.false_alarm_probability <= 0.05
    return hits / simulations


def test_white_noise_gets_a_block_of_one_which_is_the_plain_shuffle() -> None:
    """With no correlation the null is the old point shuffle."""
    flux = 1.0 + 0.01 * np.random.default_rng(1).standard_normal(120)

    assert search.correlation_block_length(flux) == 1


def test_correlated_noise_gets_a_longer_block_the_more_it_is_correlated() -> None:
    """The block grows with the correlation."""
    generator = np.random.default_rng(2)
    weak = search.correlation_block_length(correlated_noise(200, 0.5, generator))
    strong = search.correlation_block_length(correlated_noise(200, 0.95, generator))

    assert 1 < weak < strong


def test_degenerate_light_curves_get_a_block_of_one() -> None:
    """A constant or very short light curve has no correlation to keep."""
    assert search.correlation_block_length(np.ones(50)) == 1
    assert search.correlation_block_length(np.array([1.0, 2.0, 1.5])) == 1


def test_the_block_never_exceeds_a_quarter_of_the_data() -> None:
    """A slow drift cannot make the block swallow the light curve."""
    drift = np.linspace(0.0, 1.0, 80)

    assert search.correlation_block_length(drift) <= 20


def test_a_block_null_keeps_the_values_and_the_runs_inside_each_block() -> None:
    """The null is a reordering of whole blocks: same values, runs kept."""
    flux = np.arange(40, dtype=float)
    shuffled = search.null_flux(flux, np.random.default_rng(3), block_length=5)

    assert sorted(shuffled) == sorted(flux)
    assert not np.array_equal(shuffled, flux)
    steps = np.diff(shuffled)
    # Inside a block consecutive values still step by one.
    assert np.sum(np.isclose(steps, 1.0)) >= 40 - 1 - 9


def test_a_block_of_one_is_a_permutation() -> None:
    """Block length 1 is the plain shuffle."""
    flux = np.arange(30, dtype=float)

    assert sorted(search.null_flux(flux, np.random.default_rng(4), 1)) == sorted(flux)


def test_the_old_point_shuffle_calls_correlated_noise_significant_almost_always() -> None:
    """The failure the block null fixes: nothing real, yet 'significant'."""
    assert false_alarm_rate(phi=0.95, block_length=1) >= 0.7


def test_the_block_null_keeps_false_alarms_within_a_few_times_the_stated_rate() -> None:
    """With the block null, pure red noise is called significant rarely.

    Measured over many simulations the rate is about 5% for moderate
    correlation (phi 0.7) and about 10% for very strong correlation
    (phi 0.95), against a stated 5% and against 97% to 100% for the old point
    shuffle. The limits leave room for the sampling noise of 30 simulations.
    """
    assert false_alarm_rate(phi=0.7, block_length=None, simulations=30) <= 0.2
    assert false_alarm_rate(phi=0.95, block_length=None, simulations=30) <= 0.3


def test_white_noise_is_unchanged_by_the_block_null() -> None:
    """On white noise the automatic block gives the same false-alarm rate."""
    assert false_alarm_rate(phi=0.0, block_length=None) == pytest.approx(
        false_alarm_rate(phi=0.0, block_length=1)
    )


def test_a_search_records_how_its_noise_comparison_was_made() -> None:
    """The result carries the noise-only version count and the block length."""
    flux = correlated_noise(NIGHTLY_TIMES.size, 0.9, np.random.default_rng(5))

    cycle = search.lomb_scargle_search(NIGHTLY_TIMES, flux, shuffle_count=50)
    dip = search.box_search(NIGHTLY_TIMES, flux, shuffle_count=50)

    assert cycle.shuffle_count == 50
    assert dip.shuffle_count == 50
    assert cycle.null_block_length is not None
    assert cycle.null_block_length > 1


def test_a_real_signal_still_stands_out_against_the_block_null() -> None:
    """A strong sinusoid in correlated noise is still found."""
    generator = np.random.default_rng(6)
    flux = correlated_noise(NIGHTLY_TIMES.size, 0.7, generator) + 0.05 * np.sin(
        2 * np.pi * NIGHTLY_TIMES / 0.2
    )

    result = search.lomb_scargle_search(NIGHTLY_TIMES, flux, shuffle_count=199)

    assert result.false_alarm_probability <= 0.02
    assert result.best_period_days == pytest.approx(0.2, rel=0.1)
