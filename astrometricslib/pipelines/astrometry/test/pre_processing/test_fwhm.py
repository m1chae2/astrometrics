"""Purpose: Unit tests for FWHM (star sharpness) measurement.

Description: Verifies measure_image_fwhm against synthetic Gaussian
stars with a known FWHM. Moved here from
pipelines/shared/quality/quality_metrics.py
along with the function itself, since measuring FWHM this way means
detecting stars first (the same SourceDetector step astrometry uses).
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits
from astropy.modeling.models import Gaussian2D

from astrometricslib.pipelines.astrometry.pre_processing.fwhm import (
    measure_fwhm_from_data,
    measure_image_fwhm,
)


def test_measure_image_fwhm_matches_known_gaussian_sigma(tmp_path: Path) -> None:
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


def test_measure_image_fwhm_returns_none_for_empty_field(tmp_path: Path) -> None:
    """Verify a field with no detectable stars returns None, not raise."""
    data = np.random.default_rng(0).normal(100, 5, (100, 100)).astype(np.float32)
    path = tmp_path / "empty.fits"
    fits.PrimaryHDU(data).writeto(path)
    assert measure_image_fwhm(str(path)) is None


def test_measure_fwhm_from_data_ignores_a_trail_attached_to_a_star() -> None:
    """Verify a star's own dispersed trail doesn't inflate its FWHM.

    A slitless spectrograph disperses every star's light into a trail
    running away from its own position. Reproduces the failure found
    on a real Albireo session (2026-09-22): before this test's fix,
    the trail attached to a star pushed its measured FWHM from ~4px to
    ~9px, which then mis-sized the detection kernel used to find every
    other star in the frame.
    """
    rng = np.random.default_rng(0)
    data = rng.normal(50, 2, (200, 200))
    true_sigma = 1.7
    star_x, star_y = 100, 100
    data += Gaussian2D(4000, star_x, star_y, true_sigma, true_sigma)(*np.mgrid[0:200, 0:200][::-1])
    # The trail: a faint streak running away from the star along one
    # axis only, the way a grating disperses light -- much fainter
    # than the star's own peak, but many pixels long.
    data[star_y - 1 : star_y + 2, star_x : star_x + 80] += 150

    path_data = data.astype(np.float32)
    measured = measure_fwhm_from_data(path_data)
    expected = 2.3548 * true_sigma
    assert measured == pytest.approx(expected, rel=0.25)


def test_measure_fwhm_from_data_ignores_noise_peaks_next_to_one_bright_star() -> None:
    """Verify faint noise peaks don't skew the FWHM away from the real star.

    A field with only one or two genuinely bright stars (again, the
    real Albireo session) otherwise fills out its brightest-N sample
    with ordinary noise peaks, each reporting some FWHM of its own
    that has nothing to do with the real star's sharpness.
    """
    rng = np.random.default_rng(1)
    data = rng.normal(50, 2, (200, 200))
    true_sigma = 2.0
    data += Gaussian2D(6000, 100, 100, true_sigma, true_sigma)(*np.mgrid[0:200, 0:200][::-1])
    # A dozen faint, broad, noise-level blobs -- much fainter than the
    # real star, but round, so they would not be caught by an
    # elongation check.
    for cx, cy in [(20, 20), (40, 150), (60, 30), (80, 170), (120, 40), (140, 160), (160, 60), (180, 100)]:
        data += Gaussian2D(60, cx, cy, 4.0, 4.0)(*np.mgrid[0:200, 0:200][::-1])

    measured = measure_fwhm_from_data(data.astype(np.float32))
    expected = 2.3548 * true_sigma
    assert measured == pytest.approx(expected, rel=0.25)


def _field_with_wide_bright_stars_and_narrow_faint_ones() -> tuple[np.ndarray, np.ndarray]:
    """Draw four wide bright stars and four narrow fainter ones.

    The bright stars are brighter and wider, standing in for stars whose
    saturated cores were patched when exposure groups were combined; they come
    first in the measurement, which takes the brightest stars.

    Returns
    -------
    data, mask : `numpy.ndarray`
        The image, and a boolean mask set on the four bright stars' cores.
    """
    rng = np.random.default_rng(3)
    data = rng.normal(100, 3, (300, 300))
    yy, xx = np.mgrid[0:300, 0:300]
    mask = np.zeros(data.shape, dtype=bool)
    for cx, cy in [(60, 60), (150, 60), (240, 60), (60, 150)]:
        data += Gaussian2D(9000, cx, cy, 2.6, 2.6)(xx, yy)
        mask[cy - 2 : cy + 3, cx - 2 : cx + 3] = True
    for cx, cy in [(150, 150), (240, 150), (60, 240), (150, 240)]:
        data += Gaussian2D(2500, cx, cy, 1.0, 1.0)(xx, yy)
    return data, mask


def test_an_excluded_mask_skips_stars_whose_cores_are_masked() -> None:
    """Masked bright stars are left out; the narrow stars set the width."""
    data, mask = _field_with_wide_bright_stars_and_narrow_faint_ones()

    unmasked = measure_fwhm_from_data(data)
    masked = measure_fwhm_from_data(data, excluded_mask=mask)

    assert unmasked is not None
    assert masked is not None
    # The narrow stars have a sigma of 1.0 px, a FWHM of about 2.35 px.
    assert masked == pytest.approx(2.35, rel=0.2)
    assert unmasked > 1.5 * masked


def test_an_empty_excluded_mask_changes_nothing() -> None:
    """A mask with nothing set gives the same width as no mask."""
    data, _ = _field_with_wide_bright_stars_and_narrow_faint_ones()

    assert measure_fwhm_from_data(
        data, excluded_mask=np.zeros(data.shape, dtype=bool)
    ) == measure_fwhm_from_data(data)


def test_a_mask_that_covers_every_star_gives_no_width() -> None:
    """If every star is masked there is nothing to measure."""
    data, _ = _field_with_wide_bright_stars_and_narrow_faint_ones()

    assert measure_fwhm_from_data(data, excluded_mask=np.ones(data.shape, dtype=bool)) is None
