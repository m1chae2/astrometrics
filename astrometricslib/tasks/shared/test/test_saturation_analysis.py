"""Purpose: Unit tests for the saturation soft-flag pure logic.

Description: Verifies compute_saturated_pixel_fraction's pixel-counting and
is_saturation_significant's threshold comparison.
"""

import numpy as np
import pytest

from astrometricslib.tasks.shared.saturation_analysis import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)


def test_compute_saturated_pixel_fraction_no_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an all-unsaturated frame returns zero."""
    data = np.full((10, 10), 100.0)
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.0)


def test_compute_saturated_pixel_fraction_partial_saturation():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a known fraction of saturated pixels is counted exactly."""
    data = np.zeros((10, 10))
    data[:2, :] = 65535  # 20 of 100 pixels saturated
    assert compute_saturated_pixel_fraction(data, saturation_threshold=65535) == pytest.approx(0.2)


def test_compute_saturated_pixel_fraction_empty_array():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies an empty array doesn't raise a division error."""
    assert compute_saturated_pixel_fraction(np.array([]), saturation_threshold=65535) == pytest.approx(0.0)


def test_is_saturation_significant_below_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a fraction below the flag threshold is not significant."""
    assert not is_saturation_significant(0.0005, flag_threshold=0.001)


def test_is_saturation_significant_at_or_above_threshold():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies a fraction at or above the flag threshold is significant."""
    assert is_saturation_significant(0.001, flag_threshold=0.001)
    assert is_saturation_significant(0.05, flag_threshold=0.001)
