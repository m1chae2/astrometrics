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
