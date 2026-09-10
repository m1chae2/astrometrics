"""Purpose: Unit tests for post-stack image quality measurements.

Description: Verifies measure_rejected_fraction's mean-of-rejmap
computation and measure_saturated_pixel_fraction's saturated-pixel
count. measure_image_fwhm now lives in pipelines/astrometry/fwhm.py
and is tested there.
"""

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.quality.quality_metrics import (
    measure_rejected_fraction,
    measure_saturated_pixel_fraction,
)


def test_measure_rejected_fraction_computes_mean_of_rejmap(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the rejected fraction is the rejmap mean, not a nonzero count.

    This is the exact bug found and fixed during development: a rejmap
    pixel holds (frames rejected at that pixel / frames stacked at
    that pixel), so a nonzero-count approach overstates rejection by
    treating "any rejection at all" as "fully rejected".
    """
    stacked_path = tmp_path / "Target_Stacked.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(stacked_path)

    rejmap_path = tmp_path / "Target_Stacked_RejMap.fits"
    rejmap_data = np.zeros((10, 10), dtype=np.float32)
    rejmap_data[0, :] = 0.5  # 10 of 100 pixels at a 50% rejected-frame fraction
    fits.PrimaryHDU(rejmap_data).writeto(rejmap_path)

    result = measure_rejected_fraction(str(stacked_path))
    assert result == pytest.approx(0.05)  # mean, not count-based


def test_measure_rejected_fraction_returns_none_when_rejmap_missing(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a stacked file with no sibling rejmap returns None, not raise."""
    stacked_path = tmp_path / "NoRejmap_Stacked.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(stacked_path)
    assert measure_rejected_fraction(str(stacked_path)) is None


def test_measure_saturated_pixel_fraction_counts_known_saturated_pixels(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verifies a known fraction of saturated pixels is measured exactly."""
    data = np.full((10, 10), 100.0, dtype=np.float32)
    data[:3, :] = 65535.0  # 30 of 100 pixels saturated
    path = tmp_path / "partially_saturated.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_saturated_pixel_fraction(str(path)) == pytest.approx(0.3)
