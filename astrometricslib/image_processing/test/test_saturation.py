"""Tests for the overexposure checking math.

We make sure it correctly counts the bright pixels and correctly decides
whether there are enough to bother warning the user.
"""

import numpy as np
import pytest

from astrometricslib.image_processing.saturation import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)


def test_compute_saturated_pixel_fraction_no_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that a perfectly exposed photo returns 0% overexposed."""
    data = np.full((10, 10), 100.0)
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.0)


def test_compute_saturated_pixel_fraction_partial_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that we correctly count a small number of blown-out pixels."""
    data = np.zeros((10, 10))
    data[:2, :] = 65535  # 20 of 100 pixels saturated
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.2)


def test_compute_saturated_pixel_fraction_empty_array():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that we don't crash if someone passes in an empty image."""
    assert compute_saturated_pixel_fraction(np.array([]), saturation_threshold=65535) == pytest.approx(0.0)


def test_is_saturation_significant_below_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that we don't warn the user if only a tiny bit is overexposed."""
    assert not is_saturation_significant(0.0005, flag_threshold=0.001)


def test_is_saturation_significant_at_or_above_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that we do warn the user if it crosses the limit."""
    assert is_saturation_significant(0.001, flag_threshold=0.001)
    assert is_saturation_significant(0.05, flag_threshold=0.001)
