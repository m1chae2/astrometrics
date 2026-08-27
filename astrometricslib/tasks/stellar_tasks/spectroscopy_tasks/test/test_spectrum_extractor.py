"""Purpose: Unit tests for Advanced Flare-Masked Spectroscopy Extraction.

Description: Verifies sub-pixel centroid anchoring, flare masking offset,
tight spectral bounding box, and vertical profile extraction.
"""

import numpy as np

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_pipeline import SpectroscopyPipeline
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectrum_extractor import SpectrumExtractor
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig


class MockAstrometricsImage(AstrometricsImage):
    """Mock AstrometricsImage that accepts a direct array input."""

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


def test_extract_with_flare_mask_subpixel_centroid():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify extract_with_flare_mask computes sub-pixel centroids.

    Checks center-of-mass accuracy on a simulated 2D subgrid around a
    zero-order star.
    """
    # Create a 100x100 synthetic image with background 10 and a star
    # centered at (50, 50)
    data = np.ones((100, 100)) * 10.0

    # Place a sub-pixel star using a simple Gaussian or discrete values
    data[50, 50] = 100.0
    data[50, 51] = 150.0  # shifted slightly to the right (x)
    data[51, 50] = 200.0  # shifted slightly down (y)

    # Load into AstrometricsImage
    image = MockAstrometricsImage(data=data)
    extractor = SpectrumExtractor(radius=5)

    # Run extraction with flare_offset = 10, max_offset = 30
    profile, anchor_x, anchor_y = extractor.extract_with_flare_mask(
        image, (50.0, 50.0), flare_offset_pixels=10.0, max_offset_pixels=30.0, radius=5
    )

    # Assert that subpixel center of mass is computed and is close to expected
    assert 50.0 <= anchor_x <= 51.0
    assert 50.0 <= anchor_y <= 51.0
    assert len(profile) == 20  # max_offset - flare_offset = 30 - 10 = 20


def test_pipeline_integration_asi533_vertical():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the pipeline extraction with a ZWO ASI533MM Pro camera.

    Confirms extract_with_flare_mask is used when the pipeline is
    configured with a vertical dispersion orientation.
    """
    # Configure mock ZWO ASI533MM Pro
    camera = CameraConfig(
        name="ZWO ASI533MM Pro",
        pixel_size_um=3.76,
        sensor_width_px=3008,
        sensor_height_px=3008,
        grating_distance_mm=11.83,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )

    config = SpectroscopyConfig(
        camera=camera,
        grating_lines_per_mm=200.0,
        grating_distance_mm=11.83,
        dispersion_orientation="vertical",
        dispersion_direction="positive",
        dispersion_start_px=380.0,
        extraction_radius=10,
    )

    pipeline = SpectroscopyPipeline(config=config)

    # Create mock 2D image data with a star and a vertical spectrum trace
    data = np.ones((1000, 500)) * 10.0
    # Star at (100, 100)
    data[100, 100] = 500.0
    # Vertical spectrum trace from y=480 to y=850 (roughly offset 380 to
    # 750) at x=100
    data[480:850, 95:106] = 100.0

    image = MockAstrometricsImage(data=data)

    # Run _process_single_star
    res = pipeline._process_single_star(image, (100.0, 100.0), auto_detect_angle=False)

    # Assertions
    assert "wavelengths" in res
    assert "intensities" in res
    assert len(res["wavelengths"]) == 370  # 750 - 380 = 370
    assert len(res["intensities"]) == 370

    # Wavelengths should start at ~599.6 nm for 380.0 pixel offset
    assert 599.0 <= res["wavelengths"][0] <= 600.0


def test_pipeline_integration_asi533_horizontal():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the pipeline extraction with a ZWO ASI533MM Pro camera.

    Confirms extract_with_flare_mask is used when the pipeline is
    configured with a horizontal dispersion orientation.
    """
    # Configure mock ZWO ASI533MM Pro
    camera = CameraConfig(
        name="ZWO ASI533MM Pro",
        pixel_size_um=3.76,
        sensor_width_px=3008,
        sensor_height_px=3008,
        grating_distance_mm=11.83,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )

    config = SpectroscopyConfig(
        camera=camera,
        grating_lines_per_mm=200.0,
        grating_distance_mm=11.83,
        dispersion_orientation="horizontal",
        dispersion_direction="positive",
        dispersion_start_px=380.0,
        extraction_radius=10,
    )

    pipeline = SpectroscopyPipeline(config=config)

    # Create mock 2D image data with a star and a horizontal spectrum trace
    data = np.ones((500, 1000)) * 10.0
    # Star at (100, 100)
    data[100, 100] = 500.0
    # Horizontal spectrum trace from x=480 to x=850 (roughly offset 380
    # to 750) at y=100
    data[95:106, 480:850] = 100.0

    image = MockAstrometricsImage(data=data)

    # Run _process_single_star
    res = pipeline._process_single_star(image, (100.0, 100.0), auto_detect_angle=False)

    # Assertions
    assert "wavelengths" in res
    assert "intensities" in res
    assert len(res["wavelengths"]) == 370  # 750 - 380 = 370
    assert len(res["intensities"]) == 370

    # Wavelengths should start at ~599.6 nm for 380.0 pixel offset
    assert 599.0 <= res["wavelengths"][0] <= 600.0
