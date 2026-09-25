"""Tests for the overexposure checking math.

Ensures it correctly counts the bright pixels and correctly decides
whether there are enough to bother warning the user.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.shared.quality.saturation import (
    compute_saturated_pixel_fraction,
    find_saturation_plateau_ceiling,
    is_normalised_stack_scale,
    is_saturation_significant,
)


def test_compute_saturated_pixel_fraction_no_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that a perfectly exposed photo returns 0% overexposed."""
    data = np.full((10, 10), 100.0)
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.0)


def test_compute_saturated_pixel_fraction_partial_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that a small number of blown-out pixels is correctly counted."""
    data = np.zeros((10, 10))
    data[:2, :] = 65535  # 20 of 100 pixels saturated
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.2)


def test_compute_saturated_pixel_fraction_empty_array():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that it doesn't crash if someone passes in an empty image."""
    assert compute_saturated_pixel_fraction(np.array([]), saturation_threshold=65535) == pytest.approx(0.0)


def test_is_saturation_significant_below_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that the user isn't warned if only a tiny bit is overexposed."""
    assert not is_saturation_significant(0.0005, flag_threshold=0.001)


def test_is_saturation_significant_at_or_above_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that the user is warned if it crosses the limit."""
    assert is_saturation_significant(0.001, flag_threshold=0.001)
    assert is_saturation_significant(0.05, flag_threshold=0.001)


def test_is_normalised_stack_scale_separates_stacks_from_raw_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that 0-1 data is a stack and 16-bit ADU data is a raw frame."""
    assert is_normalised_stack_scale(np.array([0.0, 0.5, 1.0]))
    assert not is_normalised_stack_scale(np.array([0.0, 500.0, 65535.0]))


def test_find_saturation_plateau_ceiling_needs_a_pile_of_pixels():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Test that 100 pixels at 0.8 form a ceiling but one peak does not."""
    plateau = np.random.default_rng(1).uniform(0.03, 0.07, (100, 100))
    plateau[0, :] = 0.8
    peak = np.random.default_rng(2).uniform(0.03, 0.07, (100, 100))
    peak[5, 5] = 1.0
    assert find_saturation_plateau_ceiling(plateau) == pytest.approx(0.8)
    assert find_saturation_plateau_ceiling(peak) is None
