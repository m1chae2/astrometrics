"""Purpose: Validate the sign convention of auto-detected dispersion angles.

Description: `detect_dispersion_angle` measures a trace's tilt from image
data, and that angle is later fed back into
`SpectroscopyInstrument.get_dispersion_vector()` (via
`self.config.dispersion_angle_degrees`) to build the extraction line and
the visual overlay rectangle. The horizontal branch used to negate the
measured slope's arctan, which -- unlike the vertical branch, where the
negation is mathematically required by `get_dispersion_vector`'s 90-degree
base angle -- reproduced a dispersion vector tilted in the *opposite*
direction from the star's real streak whenever a horizontal-dispersion
camera had any real rotation. These tests catch that class of regression
directly: an angle that isn't self-consistent with
`get_dispersion_vector()` is a real, silent extraction-corrupting bug, not
just a cosmetic one.
"""

import numpy as np
import pytest

from astrometricslib.image_processing.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig


class MockAstrometricsImage(AstrometricsImage):
    """Mock AstrometricsImage that accepts a direct array input."""

    def __init__(self, data: np.ndarray):  # ruff: ignore[missing-return-type-special-method]
        """Initialize MockAstrometricsImage with given data."""
        self._data = data
        self._header = {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Image headers dict."""
        return self._header


def _build_pipeline(orientation: str) -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` with a fixed, known dispersion box.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    config = SpectroscopyConfig(
        camera=camera,
        grating_distance_mm=16.5,
        dispersion_orientation=orientation,
        dispersion_direction="positive",
        dispersion_start_px=200.0,
    )
    return SpectroscopyPipeline(config=config)


def _build_tilted_trace(  # ruff: ignore[missing-return-type-private-function]
    orientation: str, star_pos: tuple[float, float], offset_px: float, length_px: float, true_slope: float
):
    """Build a synthetic image with a known-tilted trace.

    Returns
    -------
    data : `np.ndarray`
        The synthetic image array.
    """
    rng = np.random.default_rng(0)
    data = 10.0 + rng.normal(0, 0.5, size=(900, 900))
    x_star, y_star = star_pos
    data[int(y_star), int(x_star)] = 500.0

    if orientation == "horizontal":
        for x in range(int(x_star + offset_px), int(x_star + offset_px + length_px)):
            y = round(y_star + true_slope * (x - (x_star + offset_px)))
            if 5 <= y < data.shape[0] - 6:
                data[y - 5 : y + 6, x] = 100.0 + rng.normal(0, 2, size=11)
    else:
        for y in range(int(y_star + offset_px), int(y_star + offset_px + length_px)):
            x = round(x_star + true_slope * (y - (y_star + offset_px)))
            if 5 <= x < data.shape[1] - 6:
                data[y, x - 5 : x + 6] = 100.0 + rng.normal(0, 2, size=11)
    return data


@pytest.mark.parametrize("true_slope", [0.15, -0.15])
def test_horizontal_detected_angle_round_trips_through_dispersion_vector(true_slope: float) -> None:
    """A horizontal trace's detected angle must reproduce its own slope.

    Feeding the detected angle back through `get_dispersion_vector()`
    (exactly as `_resolve_global_dispersion_angle` does) must reproduce
    the same slope sign and magnitude that was actually measured in the
    image, not its mirror image.
    """
    pipeline = _build_pipeline("horizontal")
    star_pos = (400.0, 400.0)
    offset_px = pipeline.instrument.zero_order_offset_px
    length_px = pipeline.instrument.expected_length_px
    data = _build_tilted_trace("horizontal", star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    detected_angle = pipeline.detect_dispersion_angle(image, star_pos)

    pipeline.instrument.config.dispersion_angle_degrees = detected_angle
    vec = pipeline.instrument.get_dispersion_vector()
    implied_slope = vec[1] / vec[0]

    assert implied_slope == pytest.approx(true_slope, abs=0.05)


@pytest.mark.parametrize("true_slope", [0.15, -0.15])
def test_vertical_detected_angle_round_trips_through_dispersion_vector(true_slope: float) -> None:
    """A vertical trace's detected angle must reproduce its own slope.

    Guards the vertical branch's negation (which is mathematically
    required by `get_dispersion_vector`'s 90-degree base angle, unlike
    the horizontal branch) against ever being "corrected" away by a
    future change that assumes the same fix applies to both branches.
    """
    pipeline = _build_pipeline("vertical")
    star_pos = (400.0, 400.0)
    offset_px = pipeline.instrument.zero_order_offset_px
    length_px = pipeline.instrument.expected_length_px
    data = _build_tilted_trace("vertical", star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    detected_angle = pipeline.detect_dispersion_angle(image, star_pos)

    pipeline.instrument.config.dispersion_angle_degrees = detected_angle
    vec = pipeline.instrument.get_dispersion_vector()
    implied_slope = vec[0] / vec[1]

    assert implied_slope == pytest.approx(true_slope, abs=0.05)
