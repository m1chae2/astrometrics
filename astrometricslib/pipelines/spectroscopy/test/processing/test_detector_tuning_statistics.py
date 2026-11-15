"""Purpose: Unit tests for the statistics the spectral-detector tuners share.

Description: Each function is checked on synthetic numbers with a known
answer.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.processing.detector_tuning_statistics import (
    blocked_cross_validation_error,
    point_to_point_noise,
    raw_noise_inflation,
    smallest_degree_within_tolerance,
    smallest_passing_value,
    widest_gap_midpoint,
    window_variance_spread,
)


def test_smallest_passing_value_needs_every_larger_candidate_to_pass() -> None:
    """Smallest passing value needs every larger candidate to pass.

    The answer is the start of the passing run that reaches the largest
    candidate.
    """
    assert smallest_passing_value([10, 20, 30, 40], [0.2, 0.95, 0.97, 1.0], 0.9) == 20
    assert smallest_passing_value([10, 20, 30, 40], [0.95, 0.5, 0.97, None], 0.9) == 30
    assert smallest_passing_value([10, 20], [0.1, 0.2], 0.9) is None
    assert smallest_passing_value([10, 20], [None, None], 0.9) is None


def test_smallest_degree_within_tolerance() -> None:
    """A lower degree wins when it is within the tolerance of the best."""
    assert smallest_degree_within_tolerance({1: 1.0, 2: 0.5, 3: 0.49}, 0.05) == 2
    assert smallest_degree_within_tolerance({1: 0.5, 2: 0.49}, 0.05) == 1
    assert smallest_degree_within_tolerance({}, 0.05) is None


def test_blocked_cross_validation_error_is_zero_for_the_true_degree() -> None:
    """A quadratic is predicted exactly by a quadratic and badly by a line."""
    positions = np.linspace(-1.0, 1.0, 60)
    values = 10.0 + positions**2
    assert blocked_cross_validation_error(positions, values, 2, 5) == pytest.approx(0.0, abs=1e-9)
    assert blocked_cross_validation_error(positions, values, 1, 5) > 1e-3


def test_blocked_cross_validation_error_needs_enough_samples() -> None:
    """Too few samples for the fit give no error."""
    assert blocked_cross_validation_error(np.arange(4.0), np.ones(4), 3, 2) is None


def test_window_variance_spread_is_small_for_steady_noise() -> None:
    """Window variance spread is small for steady noise.

    White noise gives a steady noise estimate in long windows and none with too
    few windows.
    """
    rng = np.random.default_rng(3)
    positions = np.arange(600.0)
    values = 50.0 + rng.normal(0.0, 1.0, positions.size)
    spread = window_variance_spread(positions, values, 60, 1)
    assert spread is not None
    assert spread < 0.5
    assert window_variance_spread(positions[:100], values[:100], 60, 1) is None


def test_point_to_point_noise_recovers_the_noise_of_a_smooth_curve() -> None:
    """A ramp plus noise of sigma 2 gives a noise near 2."""
    rng = np.random.default_rng(4)
    values = 0.5 * np.arange(2000.0) + rng.normal(0.0, 2.0, 2000)
    assert point_to_point_noise(values) == pytest.approx(2.0, rel=0.2)


def test_raw_noise_inflation() -> None:
    """Raw noise inflation.

    Alternating residuals give no inflation; a smooth drift gives a large one.
    """
    assert raw_noise_inflation(np.array([1.0, -1.0] * 20)) == pytest.approx(1.0)
    assert raw_noise_inflation(np.linspace(-1.0, 1.0, 200)) > 50.0


def test_widest_gap_midpoint() -> None:
    """The midpoint lies in the widest gap, and too few values give none."""
    assert widest_gap_midpoint([1.0, 0.1, 0.2, 0.9], 2) == pytest.approx(0.55)
    assert widest_gap_midpoint([0.1, 0.2], 3) is None
