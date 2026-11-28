"""Tests for the library's one robust spread.

The robust spread gets numbers with a known answer: a formula check, a stray
value, values with gaps (not-a-number), and an empty list. The per-axis
spread of two-dimensional misses gets a large simulated sample whose answer
is known. The clipping loop and the gap-skipping median get small lists
with known answers.
"""

import numpy as np
import pytest

from astrometricslib.utilities.robust_statistics import (
    MAD_TO_SIGMA,
    MEDIAN_DISTANCE_TO_AXIS_SIGMA,
    median_or_none,
    per_axis_sigma_from_distances,
    robust_sigma,
    sigma_clipped_values,
    smooth_median_and_sigma,
)


def test_robust_sigma_matches_the_median_absolute_deviation_formula() -> None:
    """The spread is the scaled median absolute deviation, on any axis."""
    samples = np.random.default_rng(11).normal(5.0, 2.0, size=(6, 40))
    flat = samples.ravel()

    assert MAD_TO_SIGMA == pytest.approx(1.4826)
    assert robust_sigma(flat) == pytest.approx(1.4826 * np.median(np.abs(flat - np.median(flat))), rel=1e-12)
    by_row = robust_sigma(samples, axis=1)
    for row in range(6):
        expected = 1.4826 * np.median(np.abs(samples[row] - np.median(samples[row])))
        assert by_row[row] == pytest.approx(expected, rel=1e-12)
    assert robust_sigma(samples, axis=0).shape == (40,)


def test_robust_sigma_of_a_list_is_a_float() -> None:
    """A plain list gives one float, 1.4826 times its median deviation."""
    spread = robust_sigma([-1.0, 0.0, 1.0, 2.0, 3.0])

    assert isinstance(spread, float)
    assert spread == pytest.approx(1.4826)


def test_robust_sigma_ignores_a_stray_value() -> None:
    """A single wild value does not change the spread of the others much."""
    clean = [0.0, 1.0, -1.0, 2.0, -2.0]
    assert robust_sigma([*clean, 1000.0]) < 2 * robust_sigma(clean)


def test_robust_sigma_of_identical_values_is_zero() -> None:
    """Numbers that are all alike have no spread."""
    assert robust_sigma([3.0, 3.0, 3.0]) == pytest.approx(0.0)


def test_robust_sigma_leaves_out_missing_values() -> None:
    """Not-a-number values are skipped, along an axis too."""
    values = np.array([[-1.0, 0.0, 1.0, 2.0, 3.0, np.nan], [np.nan] * 6])

    assert robust_sigma(values[0]) == pytest.approx(1.4826)
    by_row = robust_sigma(values, axis=1)
    assert by_row[0] == pytest.approx(1.4826)
    assert np.isnan(by_row[1])


def test_robust_sigma_of_nothing_is_not_a_number() -> None:
    """An empty list has no spread to give."""
    assert np.isnan(robust_sigma([]))


def test_per_axis_sigma_recovers_the_error_of_one_axis() -> None:
    """Misses of Gaussian x and y errors give back the error of one axis."""
    errors = np.random.default_rng(3).normal(0.0, 0.7, size=(200_000, 2))
    distances = np.hypot(errors[:, 0], errors[:, 1])

    assert MEDIAN_DISTANCE_TO_AXIS_SIGMA == pytest.approx(1.1774, abs=1e-4)
    assert per_axis_sigma_from_distances(distances) == pytest.approx(0.7, rel=0.01)
    # The rule for one-dimensional values would read the misses as 1.75 sigma.
    assert 1.4826 * np.median(distances) == pytest.approx(1.7456 * 0.7, rel=0.01)


def test_per_axis_sigma_of_nothing_is_not_a_number() -> None:
    """No distances give no spread."""
    assert np.isnan(per_axis_sigma_from_distances([]))


def test_median_or_none_skips_gaps() -> None:
    """Missing values are left out, and no known value gives None."""
    assert median_or_none([3.0, None, 1.0, 2.0]) == pytest.approx(2.0)
    assert median_or_none(value for value in (None, None)) is None
    assert median_or_none([]) is None


def test_sigma_clipped_values_drops_outliers_and_gaps() -> None:
    """Bright outliers and not-a-number values are gone after clipping."""
    rng = np.random.default_rng(3)
    values = np.concatenate([rng.normal(100.0, 2.0, 400), np.full(20, 5000.0), [np.nan]])
    kept = sigma_clipped_values(values, minimum_count=10, maximum_passes=5, clip_sigma=3.0)

    assert kept is not None
    assert kept.max() < 200.0
    assert np.all(np.isfinite(kept))


def test_sigma_clipped_values_keeps_the_minimum_count() -> None:
    """Too few values give None; no pass may keep fewer than the minimum."""
    assert sigma_clipped_values([1.0, 2.0], minimum_count=3, maximum_passes=5, clip_sigma=3.0) is None
    values = [0.0, 0.1, -0.1, 0.05, 50.0]
    kept = sigma_clipped_values(values, minimum_count=5, maximum_passes=5, clip_sigma=3.0)
    assert kept is not None
    assert kept.size == 5


def test_sigma_clipped_values_stops_on_zero_spread() -> None:
    """Values with no spread are returned as they are."""
    kept = sigma_clipped_values([4.0] * 10 + [9.0], minimum_count=3, maximum_passes=5, clip_sigma=3.0)
    assert kept is not None
    assert kept.size == 11


def _quantized(true_sigma: float, step: int, seed: int, size: int = 400_000) -> np.ndarray:
    """Draw bell-curve numbers and round them to a grid of the given step.

    Returns
    -------
    values : `numpy.ndarray`
        Numbers centered near 500, on a grid of ``step``.
    """
    rng = np.random.default_rng(seed)
    return step * np.round(rng.normal(500.0, true_sigma, size) / step)


@pytest.mark.parametrize("step", [1, 4])
def test_smooth_sigma_follows_the_true_noise_on_a_grid(step: int) -> None:
    """On numbers in steps of 1 or 4, the spread follows the noise smoothly.

    Noise from 24 to 30 in small steps is measured on 4-step data. The plain
    MAD jumps between grid values (multiples of 4 times 1.4826), while the
    smoothed spread stays within 1.5 percent of the true noise (the rounding
    itself adds step squared over 12 to the variance, and spreading each value
    over its cell adds as much again) and takes a different value for every
    noise level.
    """
    noises = np.linspace(24.0, 30.0, 13) if step == 4 else np.linspace(3.0, 4.0, 13)
    smooth = np.array([smooth_median_and_sigma(_quantized(n, step, 7 + i))[1] for i, n in enumerate(noises)])
    plain = np.array([robust_sigma(_quantized(n, step, 7 + i)) for i, n in enumerate(noises)])

    np.testing.assert_allclose(smooth, noises, rtol=0.015)
    assert len(set(np.round(smooth, 6))) == noises.size
    assert len(set(np.round(plain, 6))) < noises.size
    assert np.all(np.diff(smooth) > -0.02 * noises[:-1])


def test_smooth_median_is_not_stuck_on_the_grid() -> None:
    """A level 1 ADU off the 4-step grid is read as such."""
    rng = np.random.default_rng(5)
    values = 4.0 * np.round((rng.normal(501.0, 25.0, 400_000)) / 4.0)

    median, _ = smooth_median_and_sigma(values)

    assert abs(median - 4.0 * round(median / 4.0)) > 0.3
    assert median == pytest.approx(501.0, abs=0.2)


def test_smooth_sigma_of_averaged_whole_numbers_is_not_constant() -> None:
    """Means of 6 whole numbers (a grid of 1/6) give a spread that varies."""
    rng = np.random.default_rng(9)
    spreads = []
    for noise in (10.0, 10.2, 10.4, 10.6):
        raw = np.round(rng.normal(680.0, noise, size=(6, 300_000)))
        spreads.append(smooth_median_and_sigma(raw.mean(axis=0))[1])
    spreads_array = np.array(spreads)

    assert len(set(np.round(spreads_array, 4))) == 4
    np.testing.assert_allclose(spreads_array, np.array([10.0, 10.2, 10.4, 10.6]) / np.sqrt(6.0), rtol=0.02)


def test_smooth_values_equal_the_plain_ones_off_a_grid() -> None:
    """Numbers off a grid give the ordinary median and MAD."""
    samples = np.random.default_rng(1).normal(100.0, 3.0, 50_000)
    short = [1.0, 2.0, 3.0, 4.0, 10.0]

    assert smooth_median_and_sigma(samples) == (float(np.median(samples)), robust_sigma(samples))
    assert smooth_median_and_sigma(short) == (3.0, robust_sigma(short))
    median, sigma = smooth_median_and_sigma([np.nan, 5.0, 5.0])
    assert median == pytest.approx(5.0)
    assert sigma == pytest.approx(0.0)
    assert all(np.isnan(smooth_median_and_sigma([])))
