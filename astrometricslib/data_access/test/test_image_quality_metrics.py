"""Purpose: Unit tests for post-stack image quality measurements.

Description: Verifies measure_image_fwhm against synthetic Gaussian
stars with a known FWHM, and measure_rejected_fraction's
mean-of-rejmap computation.
"""

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

from astrometricslib.data_access.image_quality_metrics import (
    measure_image_fwhm,
    measure_rejected_fraction,
    measure_saturated_pixel_fraction,
)


def test_measure_image_fwhm_matches_known_gaussian_sigma(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify measured FWHM is close to synthetic Gaussian stars' true FWHM.

    Cross-checked manually during development: for true_sigma=3.0
    (true FWHM=7.06px), this measurement approach (box=15,
    sigma-clipped background subtraction) returned ~7.15px against
    synthetic data -- well within a few percent.
    """
    rng = np.random.default_rng(0)
    data = rng.normal(100, 5, (200, 200))
    yy, xx = np.mgrid[0:200, 0:200]
    true_sigma = 3.0
    for cx, cy, amp in [(50, 50, 3000), (150, 60, 2500), (100, 150, 2800)]:
        data += Gaussian2D(amp, cx, cy, true_sigma, true_sigma)(xx, yy)

    path = tmp_path / "synthetic.fits"
    fits.PrimaryHDU(data.astype(np.float32)).writeto(path)

    measured = measure_image_fwhm(str(path))
    expected = 2.3548 * true_sigma
    assert measured == pytest.approx(expected, rel=0.15)


def test_measure_image_fwhm_returns_none_for_empty_field(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a field with no detectable stars returns None, not raise."""
    data = np.random.default_rng(0).normal(100, 5, (100, 100)).astype(np.float32)
    path = tmp_path / "empty.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_image_fwhm(str(path)) is None


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
