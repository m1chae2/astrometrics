"""Tests for loading a camera's spectroscopy settings from the config.

Checks that a fully configured camera loads without complaint, that a camera
with missing hardware settings still gets the same placeholder numbers as
before but now triggers one clear warning, and that the spectrum start
wavelength can be set per camera.
"""

import logging
from typing import Any

import pytest

from astrometricslib.utilities import spectroscopy_models
from astrometricslib.utilities.spectroscopy_models import (
    DEFAULT_EXTRACTION_START_WAVELENGTH_NM,
    ConfigLoader,
)

# A camera section shaped like the ASI533MM Pro one in
# astrometrics.config.example: every hardware setting is given.
FULL_CAMERA_SECTION = {
    "name": "Test Full Camera",
    "pixel_size_μm": "3.76",
    "sensor_width_px": "3008",
    "sensor_height_px": "3008",
    "sensor_min_wavelength": "300",
    "sensor_max_wavelength": "1000",
    "dispersion_orientation": "vertical",
    "dispersion_direction": "positive",
    "grating_lines_per_mm": "200",
    "grating_distance_mm": "16.49",
}


class FakeAppConfiguration:
    """A minimal stand-in for `AppConfiguration`, made from dictionaries.

    It answers only the two questions the spectroscopy loader asks, so a test
    never reads or writes the real config file.
    """

    def __init__(self, sections: dict[str, dict[str, str]]) -> None:
        """Store the config sections.

        Parameters
        ----------
        sections : `dict` [`str`, `dict` [`str`, `str`]]
            Section name mapped to its keys and values.
        """
        self.sections = sections

    def get_camera_config(self, camera_name: str | None = None) -> dict[str, str]:
        """Return the section for a camera, or an empty dictionary.

        Returns
        -------
        section : `dict` [`str`, `str`]
            The camera's keys and values.
        """
        return dict(self.sections.get(f"Observatory.Camera.{camera_name}", {}))

    def get_value(self, section: str, key: str, fallback: Any = None) -> Any:
        """Return one value, or `fallback` when it is not there.

        Returns
        -------
        value : `Any`
            The stored text, or `fallback`.
        """
        return self.sections.get(section, {}).get(key, fallback)


@pytest.fixture(autouse=True)
def fresh_warning_cache() -> None:
    """Forget which fallback warnings were already logged, for each test."""
    spectroscopy_models._warn_once_about_fallback_settings.cache_clear()


def load(camera_name: str, section: dict[str, str] | None) -> spectroscopy_models.SpectroscopyConfig:
    """Load a camera's spectroscopy config from a fake config file.

    Returns
    -------
    config : `SpectroscopyConfig`
        The loaded settings.
    """
    sections = {f"Observatory.Camera.{camera_name}": section} if section is not None else {}
    return ConfigLoader.load_spectroscopy_config(FakeAppConfiguration(sections), camera_name)


def test_a_fully_configured_camera_loads_its_own_values_without_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that nothing falls back when every hardware setting is given."""
    with caplog.at_level(logging.WARNING):
        config = load("Test Full Camera", FULL_CAMERA_SECTION)

    assert config.camera.pixel_size_um == pytest.approx(3.76)
    assert config.camera.sensor_width_px == 3008
    assert config.camera.sensor_height_px == 3008
    assert config.camera.sensor_min_wavelength == pytest.approx(300.0)
    assert config.camera.sensor_max_wavelength == pytest.approx(1000.0)
    assert config.grating_lines_per_mm == pytest.approx(200.0)
    assert config.grating_distance_mm == pytest.approx(16.49)
    assert config.dispersion_orientation == "vertical"
    assert config.dispersion_direction == "positive"
    assert caplog.records == []


def test_a_camera_missing_from_the_config_gets_the_old_placeholder_numbers() -> None:
    """Check that the numbers are unchanged, only now flagged."""
    config = load("Test Missing Camera", None)

    assert config.camera.pixel_size_um == pytest.approx(3.76)
    assert config.camera.sensor_width_px == 3000
    assert config.camera.sensor_height_px == 2000
    assert config.camera.sensor_min_wavelength == pytest.approx(350.0)
    assert config.camera.sensor_max_wavelength == pytest.approx(900.0)
    assert config.grating_lines_per_mm == pytest.approx(100.0)
    assert config.grating_distance_mm == pytest.approx(0.0)
    assert config.dispersion_orientation == "horizontal"
    assert config.dispersion_direction == "negative"


def test_a_camera_missing_from_the_config_warns_once_and_names_the_settings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that the warning lists every missing setting, once per camera."""
    with caplog.at_level(logging.WARNING):
        load("Test Missing Camera", None)
        load("Test Missing Camera", None)

    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "Test Missing Camera" in message
    for setting in ("pixel_size_μm", "sensor_width_px", "grating_lines_per_mm", "dispersion_orientation"):
        assert setting in message


def test_only_the_missing_settings_are_named(caplog: pytest.LogCaptureFixture) -> None:
    """Check that a nearly complete section names just what it lacks."""
    section = {key: value for key, value in FULL_CAMERA_SECTION.items() if key != "grating_lines_per_mm"}
    with caplog.at_level(logging.WARNING):
        config = load("Test Partial Camera", section)

    assert config.grating_lines_per_mm == pytest.approx(100.0)
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "grating_lines_per_mm" in message
    assert "pixel_size_μm" not in message


def test_tuning_settings_that_are_left_out_do_not_warn(caplog: pytest.LogCaptureFixture) -> None:
    """Check that offsets and extraction defaults are not hardware facts."""
    with caplog.at_level(logging.WARNING):
        config = load("Test Full Camera", FULL_CAMERA_SECTION)

    assert config.dispersion_offset_x == pytest.approx(0.0)
    assert config.expected_fwhm == pytest.approx(8.0)
    assert config.extraction_radius == 10
    assert caplog.records == []


def test_the_spectrum_start_wavelength_defaults_to_380_nm() -> None:
    """Check the value the tuner used to have hard-coded."""
    config = load("Test Full Camera", FULL_CAMERA_SECTION)
    assert config.extraction_start_wavelength_nm == pytest.approx(380.0)
    assert DEFAULT_EXTRACTION_START_WAVELENGTH_NM == pytest.approx(380.0)


def test_the_spectrum_start_wavelength_can_be_set_for_one_camera() -> None:
    """Check that the config file can override the start wavelength."""
    section = {**FULL_CAMERA_SECTION, "extraction_start_wavelength_nm": "400"}
    assert load("Test Full Camera", section).extraction_start_wavelength_nm == pytest.approx(400.0)
