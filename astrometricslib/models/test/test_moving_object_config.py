"""Tests for the moving object settings.

Checks that we can load settings properly from the config file.
"""

from pathlib import Path

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.moving_object_config import (
    MovingObjectConfig,
    MovingObjectConfigLoader,
)


def test_load_moving_object_config_reads_real_config_section() -> None:
    """Check that we can successfully read the real settings file."""
    config = MovingObjectConfigLoader.load_moving_object_config(AppConfiguration())
    assert isinstance(config, MovingObjectConfig)
    assert config.detection_fwhm_px > 0.0
    assert config.detection_threshold_sigma > 0.0
    assert config.min_frames_for_persistence >= 2


def test_load_moving_object_config_falls_back_to_defaults_when_section_missing(tmp_path: Path) -> None:
    """Check that we use safe default values if the settings file is empty."""
    import configparser

    config_path = tmp_path / "empty.config"
    config_path.write_text("[Library]\npath = library\n")

    app_config = AppConfiguration()
    app_config.app_config = configparser.ConfigParser()
    app_config._find_config_file = lambda: config_path
    app_config.load_configuration()

    config = MovingObjectConfigLoader.load_moving_object_config(app_config)

    assert config == MovingObjectConfig()
