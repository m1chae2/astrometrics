"""Tests for the shared significance-to-confidence heuristic.

Ensures the saturation curve behaves sanely at its important points:
no signal gives no confidence, a strong signal saturates toward 1, and
it never crashes on the edge cases callers actually hit (zero noise,
zero or negative significance).
"""

import math

import pytest

from astrometricslib.pipelines.shared.quality.detection_confidence import (
    significance_to_confidence,
)


def test_zero_significance_gives_zero_confidence() -> None:
    """Test that no signal above the noise means no confidence."""
    assert significance_to_confidence(0.0) == pytest.approx(0.0)


def test_negative_significance_gives_zero_confidence() -> None:
    """Test that a nonsensical negative significance doesn't crash."""
    assert significance_to_confidence(-1.0) == pytest.approx(0.0)


def test_significance_at_scale_is_about_two_thirds() -> None:
    """Test the curve's defining point: significance == scale gives ~63%."""
    assert significance_to_confidence(2.0, scale=2.0) == pytest.approx(1.0 - math.exp(-1.0))


def test_confidence_increases_monotonically_with_significance() -> None:
    """Test that a stronger signal never reports lower confidence."""
    low = significance_to_confidence(1.0)
    mid = significance_to_confidence(3.0)
    high = significance_to_confidence(10.0)
    assert low < mid < high


def test_infinite_significance_saturates_to_one() -> None:
    """Test a noise-free detection (division by zero noise) caps at 1.0."""
    assert significance_to_confidence(float("inf")) == pytest.approx(1.0)


def test_zero_scale_does_not_divide_by_zero() -> None:
    """Test a degenerate scale is treated as no confidence, not a crash."""
    assert significance_to_confidence(5.0, scale=0.0) == pytest.approx(0.0)
