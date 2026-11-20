"""Purpose: Tests for the straight-line fit that keeps only real slopes.

Description: A clear trend is kept, a slope within the noise is reported as
zero, and too few points or times that do not vary give zero.
"""

import pytest

from wayfindinglib.analytics.line_fit import fit_slope, significant_slope


def test_a_clear_trend_is_kept() -> None:
    """Points rising one per step with little scatter keep their slope."""
    assert significant_slope([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.1], 3.0) == pytest.approx(1.02)


def test_a_slope_within_the_noise_counts_as_none() -> None:
    """Scatter around zero gives no drift."""
    assert significant_slope([0, 1, 2, 3], [0.1, -0.2, 0.15, -0.05], 3.0) == pytest.approx(0.0)


def test_two_points_or_constant_times_give_no_slope() -> None:
    """Two points leave no scatter; equal times leave no line."""
    assert significant_slope([0, 1], [0.0, 5.0], 3.0) == pytest.approx(0.0)
    assert significant_slope([2, 2, 2], [0.0, 1.0, 2.0], 3.0) == pytest.approx(0.0)


def test_fit_slope_gives_the_slope_and_its_standard_error() -> None:
    """A noisy rise has a slope near one and a small positive error."""
    fit = fit_slope([0, 1, 2, 3, 4], [0.0, 1.0, 2.0, 3.0, 4.1])
    assert fit is not None
    assert fit[0] == pytest.approx(1.02)
    assert 0.0 < fit[1] < 0.1


def test_fit_slope_error_is_zero_on_an_exact_line() -> None:
    """Points exactly on a line have no scatter, so no standard error."""
    fit = fit_slope([0, 1, 2, 3], [1.0, 3.0, 5.0, 7.0])
    assert fit is not None
    assert fit[0] == pytest.approx(2.0)
    assert fit[1] == pytest.approx(0.0, abs=1e-12)


def test_fit_slope_needs_three_points_and_varying_times() -> None:
    """Two points, or equal times, give no fit."""
    assert fit_slope([0, 1], [0.0, 5.0]) is None
    assert fit_slope([2, 2, 2], [0.0, 1.0, 2.0]) is None
