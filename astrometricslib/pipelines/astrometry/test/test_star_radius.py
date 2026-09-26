"""Tests that source detection measures how big each star looks."""

import numpy as np

from astrometricslib.pipelines.astrometry.source_detection import (
    SourceDetector,
    measure_star_radius_px,
)


def _image_with_gaussian_stars(sigmas: list[float]) -> np.ndarray:
    """Build a noisy image with one Gaussian star of each given sigma.

    Returns
    -------
    image : `numpy.ndarray`
        The 200x200 test image.
    """
    rng = np.random.default_rng(1)
    image = rng.normal(100.0, 1.0, (200, 200))
    y_grid, x_grid = np.mgrid[0:200, 0:200]
    for index, sigma in enumerate(sigmas):
        x_center = 50 + index * 60
        image += 2000.0 * np.exp(-((x_grid - x_center) ** 2 + (y_grid - 100) ** 2) / (2 * sigma**2))
    return image


def test_bigger_star_gets_bigger_radius() -> None:
    """A star with a wider profile is measured with a larger radius."""
    image = _image_with_gaussian_stars([1.5, 3.5])
    small = measure_star_radius_px(image, 50.0, 100.0)
    large = measure_star_radius_px(image, 110.0, 100.0)
    assert small is not None
    assert large is not None
    assert large > small


def test_detect_records_radius_on_each_source() -> None:
    """Every detected source carries a `radius_px` value."""
    sources = SourceDetector(fwhm=4.0).detect(_image_with_gaussian_stars([2.0]))
    assert sources
    assert all(source["radius_px"] is not None for source in sources)


def test_flat_image_has_no_radius() -> None:
    """A featureless cutout gives no radius rather than a bogus one."""
    assert measure_star_radius_px(np.full((50, 50), 5.0), 25.0, 25.0) is None
