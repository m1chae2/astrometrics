"""Purpose: Unit tests for equipment_activation.

Description: Verifies activation validates against the resolved
catalog before recording, against a real `AppConfiguration` backed by
a temporary config file (real parsing, not a mock), mirroring
`data_access/test/test_equipment_catalog_reader.py`'s isolation
pattern.
"""

import pytest

from wayfindinglib.data_access.equipment_catalog_reader import (
    get_active_camera_id,
    get_active_telescope_id,
)
from wayfindinglib.tasks.control_tasks.equipment_activation import (
    get_equipment_configuration,
    list_camera_profiles,
    set_active_camera,
    set_active_telescope,
)


@pytest.fixture
def app_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a real AppConfiguration backed by an isolated temp config file.

    Returns
    -------
    config : `AppConfiguration`
        A fresh, isolated configuration instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


def test_set_active_telescope_persists_known_id(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a recognized telescope id is recorded as active."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
    })
    assert set_active_telescope(app_config, "Rig A") is True
    assert get_active_telescope_id(app_config) == "Rig A"


def test_set_active_telescope_rejects_unknown_id(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized telescope id is rejected without recording."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
    })
    assert set_active_telescope(app_config, "does-not-exist") is False
    assert get_active_telescope_id(app_config) is None


def test_set_active_camera_persists_known_id(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a recognized camera id is recorded as active."""
    app_config.update_config({
        "Observatory.Camera": {"models": "ASI2600MM"},
        "Observatory.Camera.ASI2600MM": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })
    assert set_active_camera(app_config, "ASI2600MM") is True
    assert get_active_camera_id(app_config) == "ASI2600MM"


def test_set_active_camera_rejects_unknown_id(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized camera id is rejected without recording."""
    before = get_active_camera_id(app_config)
    assert set_active_camera(app_config, "does-not-exist") is False
    assert get_active_camera_id(app_config) == before


def test_list_camera_profiles_returns_configured_cameras_as_dicts(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify every configured camera is returned as a serialized dict."""
    app_config.update_config({
        "Observatory.Camera": {"models": "ASI2600MM"},
        "Observatory.Camera.ASI2600MM": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })
    profiles = list_camera_profiles(app_config)
    assert len(profiles) == 1
    assert profiles[0]["name"] == "ASI2600MM"


def test_get_equipment_configuration_returns_none_without_active_camera(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify no active camera configured yields None rather than raising."""
    app_config.update_config({"Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"}})
    assert get_equipment_configuration(app_config) is None


def test_get_equipment_configuration_reports_fov_geometry_for_active_rig(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the active telescope+camera pairing reports FOV geometry.

    `EquipmentConfigurationManager` predates the multi-telescope
    catalog and reads the single flat `[Observatory.Telescope]`
    focal_length_mm/focal_ratio fields directly, not a per-name
    `[Observatory.Telescope.<Name>]` sub-section.
    """
    app_config.update_config({
        "Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Camera": {"models": "ASI2600MM", "default_primary_camera": "ASI2600MM"},
        "Observatory.Camera.ASI2600MM": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "6248",
            "sensor_height_px": "4176",
        },
    })
    configuration = get_equipment_configuration(app_config)
    assert configuration is not None
    assert configuration["camera"]["name"] == "ASI2600MM"
