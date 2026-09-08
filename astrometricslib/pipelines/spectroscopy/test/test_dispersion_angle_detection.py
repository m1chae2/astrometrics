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

A second, independent consumer of the same `detect_dispersion_angle()`
value -- `extract_with_flare_mask[_traced]`'s per-column tilt tracking,
used for the ZWO ASI533MM Pro flare-masking extraction path -- expects
the *opposite* sign convention from `get_dispersion_vector()` for
horizontal dispersion (the two already agree for vertical). Fixing the
`get_dispersion_vector()` side alone silently broke this second consumer,
so `_extract_via_flare_mask` applies a compensating sign flip for
horizontal orientation before calling into the extractor; the tests below
verify the actual end-to-end extracted signal follows the real trace
rather than checking an intermediate angle value in isolation.
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


def _build_pipeline_asi533(orientation: str) -> SpectroscopyPipeline:
    """Build an ASI533-named `SpectroscopyPipeline` (flare-mask path).

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name="ZWO ASI533MM Pro",
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
        extraction_method="fixed",
    )
    return SpectroscopyPipeline(config=config)


def _build_tilted_trace_from_anchor(  # ruff: ignore[missing-return-type-private-function]
    orientation: str, star_pos: tuple[float, float], offset_px: float, length_px: float, true_slope: float
):
    """Build a synthetic image whose trace runs straight through the star.

    This is the physically correct model for a real optical tilt, and
    what `extract_with_flare_mask`'s per-column dynamic centering
    assumes when following the trace outward from the anchor.

    Returns
    -------
    data : `np.ndarray`
        The synthetic image array.
    """
    rng = np.random.default_rng(3)
    data = 10.0 + rng.normal(0, 0.5, size=(900, 900))
    x_star, y_star = star_pos
    data[int(y_star), int(x_star)] = 500.0

    if orientation == "horizontal":
        for x in range(int(x_star + offset_px), int(x_star + offset_px + length_px)):
            y = round(y_star + true_slope * (x - x_star))
            if 5 <= y < data.shape[0] - 6:
                data[y - 5 : y + 6, x] = 150.0 + rng.normal(0, 3, size=11)
    else:
        for y in range(int(y_star + offset_px), int(y_star + offset_px + length_px)):
            x = round(x_star + true_slope * (y - y_star))
            if 5 <= x < data.shape[1] - 6:
                data[y, x - 5 : x + 6] = 150.0 + rng.normal(0, 3, size=11)
    return data


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
@pytest.mark.parametrize("true_slope", [0.03, -0.03, 0.06, -0.06])
def test_flare_mask_extraction_follows_a_real_tilted_trace(orientation: str, true_slope: float) -> None:
    """The ASI533 flare-mask path must extract the real trace, not noise.

    With auto-detection enabled, the extracted intensities for a
    tilted trace must sit well above the background level -- if the
    sign convention feeding `extract_with_flare_mask[_traced]` were
    wrong, the per-column window would walk away from the real trace
    and this would silently return near-background noise instead.
    """
    pipeline = _build_pipeline_asi533(orientation)
    star_pos = (400.0, 400.0)
    offset_px = 200.0
    length_px = 250.0
    data = _build_tilted_trace_from_anchor(orientation, star_pos, offset_px, length_px, true_slope)
    image = MockAstrometricsImage(data)

    result = pipeline._process_single_star(image, star_pos, auto_detect_angle=True)
    intensities = np.array(result["intensities"])

    # Background-only columns sum to ~10 * 11 == 110; a correctly
    # followed trace (peak ~150 over an 11px window) should average
    # well above that.
    assert intensities.mean() > 300.0
