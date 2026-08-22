"""Purpose: Unit tests for the zero-order saturation check.

Applies to the spectral extraction pipeline.

Description: Verifies _process_single_star reports the fraction of
saturated pixels in the aperture around the zero-order position, using
the same generic saturation-fraction function already used by stacking
and photometry.
"""

import numpy as np
import pytest

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import SpectroscopyPipeline
from astrometricslib.utilities import AstrometricsImage, CameraConfig, SpectroscopyConfig


class MockAstrometricsImage(AstrometricsImage):
    """A mock AstrometricsImage for testing, allowing direct array input."""

    def __init__(self, data: np.ndarray, header=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize MockAstrometricsImage with given data and header."""
        self._data = data
        self._header = header or {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Image headers dict."""
        return self._header


def _build_pipeline(extraction_radius: int = 5) -> SpectroscopyPipeline:
    """Build a SpectroscopyPipeline with a non-ASI533 test camera.

    Returns
    -------
    SpectroscopyPipeline
        A pipeline configured with a synthetic test camera.
    """
    config = SpectroscopyConfig(
        camera=CameraConfig(name="TestCam", pixel_size_um=5.0, sensor_width_px=2000, sensor_height_px=2000),
        grating_distance_mm=10.0,
        dispersion_start_px=20.0,
        extraction_radius=extraction_radius,
    )
    return SpectroscopyPipeline(config=config)


def test_zero_order_saturation_fraction_zero_when_unsaturated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unsaturated zero-order aperture reports zero saturation."""
    data = np.full((1000, 1000), 500.0)
    image = MockAstrometricsImage(data=data)
    pipeline = _build_pipeline()

    result = pipeline._process_single_star(image, (500.0, 500.0), auto_detect_angle=False)

    assert result["zero_order_saturated_pixel_fraction"] == pytest.approx(0.0)


def test_zero_order_saturation_fraction_reflects_saturated_aperture():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a fully-saturated zero-order aperture reports a fraction of 1."""
    data = np.full((1000, 1000), 500.0)
    star_x, star_y, radius = 500, 500, 5
    data[star_y - radius : star_y + radius + 1, star_x - radius : star_x + radius + 1] = 65535.0
    image = MockAstrometricsImage(data=data)
    pipeline = _build_pipeline(extraction_radius=radius)

    result = pipeline._process_single_star(image, (float(star_x), float(star_y)), auto_detect_angle=False)

    assert result["zero_order_saturated_pixel_fraction"] == pytest.approx(1.0)
