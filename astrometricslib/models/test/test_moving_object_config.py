"""Tests for the moving object settings.

Checks that we can load settings properly and create modified copies of them.
"""

import pytest

from astrometricslib.models.moving_object_config import (
    MovingObjectConfig,
    MovingObjectConfigLoader,
)
from astrometricslib.utilities.config_loader import AppConfiguration


def test_moving_object_config_with_overrides():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Make sure we can copy the settings and change just a few of them."""
    original = MovingObjectConfig()
    overridden = original.with_overrides(detection_fwhm_px=6.0, rate_min_arcsec_per_hour=2.5)

    assert overridden.detection_fwhm_px == pytest.approx(6.0)
    assert overridden.rate_min_arcsec_per_hour == pytest.approx(2.5)
    assert original.detection_fwhm_px == pytest.approx(4.0)
    assert original.rate_min_arcsec_per_hour == pytest.approx(1.0)


def test_load_moving_object_config_reads_real_config_section():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Check that we can successfully read the real settings file."""
    config = MovingObjectConfigLoader.load_moving_object_config(AppConfiguration())
    assert isinstance(config, MovingObjectConfig)
    assert config.detection_fwhm_px > 0.0
    assert config.detection_threshold_sigma > 0.0
    assert config.min_frames_for_persistence >= 2


def test_load_moving_object_config_falls_back_to_defaults_when_section_missing(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Check that we use safe default values if the settings file is empty."""
    import configparser

    config_path = tmp_path / "empty.config"
    config_path.write_text("[Library]\npath = libraryIndex\n")

    app_config = AppConfiguration()
    app_config.app_config = configparser.ConfigParser()
    app_config._find_config_file = lambda: config_path
    app_config.load_configuration()

    config = MovingObjectConfigLoader.load_moving_object_config(app_config)

    assert config == MovingObjectConfig()
