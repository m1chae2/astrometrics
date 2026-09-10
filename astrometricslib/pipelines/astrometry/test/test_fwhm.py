"""Purpose: Unit tests for FWHM (star sharpness) measurement.

Description: Verifies measure_image_fwhm against synthetic Gaussian
stars with a known FWHM. Moved here from
pipelines/shared/quality/quality_metrics.py
along with the function itself, since measuring FWHM this way means
detecting stars first (the same SourceDetector step astrometry uses).
"""

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

from astrometricslib.pipelines.astrometry.fwhm import measure_image_fwhm


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
