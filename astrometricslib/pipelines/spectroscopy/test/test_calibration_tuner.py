"""Purpose: Unit tests for SpectroscopyCalibrationTuner.

Description: Verifies autonomous physical-model fitting of grating
parameters and configuration recording.
"""

import os

import pytest

from astrometricslib.pipelines.spectroscopy.calibration_tuner import (
    SpectroscopyCalibrationTuner,
)
from astrometricslib.utilities.config_loader import get_configuration

# Environment variable naming a real stacked Vega frame for the opt-in
# integration test below.
_CALIBRATION_FRAME_ENV_VAR = "ASTROMETRICS_SPECTROSCOPY_TEST_FRAME"


def test_spectroscopy_calibration_tuning():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Tests the SpectroscopyCalibrationTuner on the Vega stacked FITS file.

    Verifies that the physical model solver successfully converges to the
    correct physical parameters, returns a successful report, and restores the
    original config file afterward.

    Opt-in: the tuner fits a physical grating model to a real stellar
    spectrum, so it needs genuine observational data. The synthetic frames
    the test fixtures generate carry no detectable sources, and resolving
    this path from the configured frames directory would land on one of
    them. Point `ASTROMETRICS_SPECTROSCOPY_TEST_FRAME` at a real stacked
    Vega frame to exercise it.
    """
    calibration_file = os.environ.get(_CALIBRATION_FRAME_ENV_VAR)

    if not calibration_file:
        pytest.skip(
            f"Set {_CALIBRATION_FRAME_ENV_VAR} to a real stacked Vega frame to run this "
            "integration test; it cannot run against synthetic fixture data."
        )

    if not os.path.exists(calibration_file):
        pytest.skip(f"Test FITS file not found at {calibration_file}. Skipping integration test.")

    config = get_configuration()
    camera_name = "ZWO ASI533MM Pro"
    section_name = f"Observatory.Camera.{camera_name}"

    # Setup the mock camera configuration in the temporary config file
    section_params = {
        section_name: {
            "name": camera_name,
            "pixel_size_μm": "3.76",
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
            "grating_distance_mm": "11.0",  # Start untuned
            "sensor_min_wavelength": "300",
            "sensor_max_wavelength": "1000",
            "dispersion_orientation": "vertical",  # Crucial to extract vertically
            "dispersion_direction": "positive",
            "dispersion_start_px": "320.0",  # Start untuned
            "grating_lines_per_mm": "200",  # Crucial for grating math
            "dispersion_offset_x": "0.0",
        }
    }
    config.update_config(section_params)

    # Initialize tuner and run tuning
    tuner = SpectroscopyCalibrationTuner(config)
    res = tuner.tune_calibration(calibration_file, camera_name=camera_name)

    # Assertions on returned report
    assert res["status"] == "success"
    assert res["camera_name"] == camera_name
    assert 16.0 <= res["fitted_grating_distance_mm"] <= 17.0
    assert 320.0 <= res["fitted_dispersion_start_px"] <= 350.0
    assert res["rms_error_nm"] < 1.0

    details = res["detailed_calibration"]
    assert len(details) == 3

    # Verify specific feature references mapped correctly
    features = [item["feature"] for item in details]
    assert "H-delta" in features
    assert "H-gamma" in features
    assert "H-beta" in features

    # Verify that the config was updated with tuned values
    assert config.get_value(section_name, "grating_distance_mm") == str(res["fitted_grating_distance_mm"])
    assert config.get_value(section_name, "dispersion_start_px") == str(res["fitted_dispersion_start_px"])
