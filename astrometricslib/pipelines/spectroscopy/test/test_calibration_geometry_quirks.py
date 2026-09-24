"""Purpose: Unit tests for calibration-derived flare-mask/length detection.

Description: Verifies SpectroscopyCalibrationTuner._detect_flare_contamination
and _detect_max_extraction_length_px, the two helpers that let
use_flare_mask_extraction and max_extraction_length_px be calculated from
a calibration frame instead of hand-set in config.
"""

import numpy as np
import pytest

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.pipelines.spectroscopy.calibration_tuner import (
    SpectroscopyCalibrationTuner,
)


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


def test_flare_contamination_detected_when_extraction_start_is_saturated():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A saturated dispersion start reads as flare contamination."""
    data = np.full((200, 200), 500.0)
    data[95:106, 95:106] = 65535.0
    image = MockAstrometricsImage(data=data)

    contaminated = SpectroscopyCalibrationTuner._detect_flare_contamination(
        image, extraction_start=(100.0, 100.0), extraction_radius=5, saturation_threshold_adu=65000.0
    )

    assert contaminated is True


def test_flare_contamination_uses_the_threshold_it_is_given():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The same pixels count as saturated only under a low threshold."""
    data = np.full((200, 200), 500.0)
    data[95:106, 95:106] = 20000.0
    image = MockAstrometricsImage(data=data)

    def detect(threshold_adu: float) -> bool:
        """Run the detection with one threshold.

        Returns
        -------
        contaminated : `bool`
            Whether the dispersion start counts as flare contaminated.
        """
        return SpectroscopyCalibrationTuner._detect_flare_contamination(
            image,
            extraction_start=(100.0, 100.0),
            extraction_radius=5,
            saturation_threshold_adu=threshold_adu,
        )

    assert detect(15000.0) is True
    assert detect(65000.0) is False


def test_flare_contamination_not_detected_on_clean_extraction_start():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A clean, unsaturated dispersion start reads as no contamination."""
    data = np.full((200, 200), 500.0)
    image = MockAstrometricsImage(data=data)

    contaminated = SpectroscopyCalibrationTuner._detect_flare_contamination(
        image, extraction_start=(100.0, 100.0), extraction_radius=5, saturation_threshold_adu=65000.0
    )

    assert contaminated is False


def test_max_extraction_length_uncapped_when_it_fits_the_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """No cap is reported when the physics-derived length fits on sensor."""
    cap = SpectroscopyCalibrationTuner._detect_max_extraction_length_px(
        base_pos=(100.0, 100.0),
        vector=np.array([0.0, 1.0]),
        offset_px=20.0,
        length_px=50.0,
        image_shape=(1000, 1000),
    )

    assert cap is None


def test_max_extraction_length_capped_when_it_would_run_off_the_sensor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A physics-derived length overshooting the sensor edge gets capped."""
    cap = SpectroscopyCalibrationTuner._detect_max_extraction_length_px(
        base_pos=(100.0, 100.0),
        vector=np.array([0.0, 1.0]),
        offset_px=20.0,
        length_px=1000.0,
        image_shape=(200, 200),
    )

    assert cap == pytest.approx(99.0)


def test_max_extraction_length_none_when_star_already_past_the_edge():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """No sensible cap can be reported if the anchor is already off-sensor."""
    cap = SpectroscopyCalibrationTuner._detect_max_extraction_length_px(
        base_pos=(100.0, 100.0),
        vector=np.array([0.0, 1.0]),
        offset_px=200.0,
        length_px=50.0,
        image_shape=(150, 200),
    )

    assert cap is None


def test_the_spectrum_start_offset_uses_the_configs_start_wavelength():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The spectrum start moves when the config sets another wavelength."""
    from astrometricslib.pipelines.spectroscopy.optics_physics import calculate_pixel_offset
    from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

    camera = CameraConfig(name="TestCam", pixel_size_μm=3.76, sensor_width_px=3008, sensor_height_px=3008)
    default_config = SpectroscopyConfig(camera=camera, grating_lines_per_mm=200.0, grating_distance_mm=16.5)
    later_config = default_config.with_overrides(extraction_start_wavelength_nm=400.0)

    default_offset = SpectroscopyCalibrationTuner._spectrum_start_offset_px(default_config, 16.5)
    later_offset = SpectroscopyCalibrationTuner._spectrum_start_offset_px(later_config, 16.5)

    assert default_offset == pytest.approx(
        calculate_pixel_offset(
            wavelength_nm=380.0, grating_distance_mm=16.5, lines_per_mm=200.0, pixel_size_um=3.76
        )
    )
    assert later_offset > default_offset
