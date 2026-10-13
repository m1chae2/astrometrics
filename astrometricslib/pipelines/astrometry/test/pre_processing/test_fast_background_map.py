"""Tests for the fast background map and the optional star radius."""

import numpy as np
from astropy.stats import SigmaClip
from photutils.background import Background2D, MedianBackground

from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import (
    SourceDetector,
    background_map_from_mesh,
)


def _star_field(seed: int = 3) -> np.ndarray:
    """Build a 610x730 noisy image with a gradient and Gaussian stars.

    Returns
    -------
    image : `numpy.ndarray`
        The test image (its size is not a multiple of the box size).
    """
    rng = np.random.default_rng(seed)
    y_grid, x_grid = np.mgrid[0:610, 0:730]
    image = rng.normal(100.0, 3.0, (610, 730)) + 0.02 * x_grid + 0.01 * y_grid
    for y_center in range(60, 580, 90):
        for x_center in range(60, 700, 100):
            image += 1500.0 * np.exp(-((x_grid - x_center) ** 2 + (y_grid - y_center) ** 2) / (2 * 1.8**2))
    return image


def test_fast_background_map_matches_photutils() -> None:
    """The matrix-product map equals photutils' own `.background`."""
    image = _star_field()
    box_size = 30
    background = Background2D(
        image, box_size=box_size, sigma_clip=SigmaClip(sigma=3.0), bkg_estimator=MedianBackground()
    )
    fast = background_map_from_mesh(background.background_mesh, box_size, image.shape)
    assert fast is not None
    np.testing.assert_allclose(fast, background.background, rtol=1e-10, atol=0.0)


def test_fast_background_map_falls_back_on_odd_input() -> None:
    """A mesh with NaN, or one too small for the picture, returns None."""
    mesh = np.ones((5, 5))
    mesh[2, 2] = np.nan
    assert background_map_from_mesh(mesh, 10, (50, 50)) is None
    assert background_map_from_mesh(np.ones((3, 3)), 10, (50, 50)) is None


def test_fast_background_map_flat_mesh() -> None:
    """A constant mesh gives a constant picture of the right shape."""
    result = background_map_from_mesh(np.full((4, 4), 7.0), 10, (35, 38))
    assert result is not None
    assert result.shape == (35, 38)
    np.testing.assert_array_equal(result, np.full((35, 38), 7.0))


def test_with_radius_false_skips_radius_but_keeps_sources() -> None:
    """`with_radius=False` returns the same stars, minus `radius_px`."""
    image = _star_field()
    with_radius = SourceDetector().detect(image)
    without_radius = SourceDetector().detect(image, with_radius=False)
    assert len(with_radius) == len(without_radius) > 0
    assert all("radius_px" in source for source in with_radius)
    assert all("radius_px" not in source for source in without_radius)
    for full, light in zip(with_radius, without_radius, strict=True):
        assert full["flux"] == light["flux"]
        assert full["x_centroid"] == light["x_centroid"]


def test_measure_fwhm_unchanged_on_star_field() -> None:
    """The FWHM of sigma-1.8 Gaussian stars is near 2.355 * 1.8 pixels."""
    result = measure_fwhm_from_data(_star_field())
    assert result is not None
    assert abs(result - 2.355 * 1.8) < 0.4
