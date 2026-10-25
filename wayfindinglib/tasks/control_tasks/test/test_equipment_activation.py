"""Purpose: Unit tests for equipment_activation.

Description: Verifies activation validates against the resolved
catalog before recording, against a real `AppConfiguration` backed by
a temporary config file (real parsing, not a mock), mirroring
`data_access/test/test_equipment_catalog_reader.py`'s isolation
pattern.
"""

from pathlib import Path

import pytest

from astrometricslib import AppConfiguration
from wayfindinglib.data_access import equipment_catalog_writer
from wayfindinglib.data_access.equipment_catalog_reader import (
    get_active_camera_id,
    get_active_telescope_id,
    list_telescopes,
)
from wayfindinglib.data_access.equipment_catalog_writer import build_telescope_limits_update
from wayfindinglib.tasks.control_tasks.equipment_activation import (
    get_equipment_configuration,
    list_camera_profiles,
    set_active_camera,
    set_active_telescope,
)


@pytest.fixture
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build a real AppConfiguration backed by an isolated temp config file.

    Returns
    -------
    config : `AppConfiguration`
        A fresh, isolated configuration instance.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


def test_set_active_telescope_persists_known_id(app_config: AppConfiguration) -> None:
    """Verify a recognized telescope id is recorded as active."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
    })
    assert set_active_telescope(app_config, "Rig A") is True
    assert get_active_telescope_id(app_config) == "Rig A"


def test_set_active_telescope_rejects_unknown_id(app_config: AppConfiguration) -> None:
    """Verify an unrecognized telescope id is rejected without recording."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Rig A"},
        "Observatory.Telescope.Rig A": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
    })
    assert set_active_telescope(app_config, "does-not-exist") is False
    assert get_active_telescope_id(app_config) is None


def test_set_active_camera_persists_known_id(app_config: AppConfiguration) -> None:
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


def test_set_active_camera_rejects_unknown_id(app_config: AppConfiguration) -> None:
    """Verify an unrecognized camera id is rejected without recording."""
    before = get_active_camera_id(app_config)
    assert set_active_camera(app_config, "does-not-exist") is False
    assert get_active_camera_id(app_config) == before


def test_list_camera_profiles_returns_configured_cameras_as_dicts(app_config: AppConfiguration) -> None:
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


def test_get_equipment_configuration_returns_none_without_active_camera(app_config: AppConfiguration) -> None:
    """Verify no active camera configured yields None rather than raising."""
    app_config.update_config({"Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"}})
    assert get_equipment_configuration(app_config) is None


def test_get_equipment_configuration_reports_fov_geometry_for_active_rig(
    app_config: AppConfiguration,
) -> None:
    """Verify the active telescope+camera pairing reports FOV geometry.

    With no telescope ``models`` list, the catalog builds its one
    telescope from the flat `[Observatory.Telescope]` section.
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
    assert configuration["camera"] == {
        "name": "ASI2600MM",
        "pixelSizeUm": 3.76,
        "sensorWidthPx": 6248,
        "sensorHeightPx": 4176,
    }
    assert configuration["telescope"] == {"name": "Apertura 75Q", "focalLengthMm": 450.0, "focalRatio": 6.0}
    assert configuration["plateScaleArcsecPerPx"] == pytest.approx(206.265 * 3.76 / 450.0, abs=1e-4)
    assert configuration["fovWidthDeg"] == pytest.approx(206.265 * 3.76 / 450.0 * 6248 / 3600.0, abs=1e-5)


@pytest.fixture
def limits_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build a fresh `AppConfiguration` saved to a per-test temp file.

    The path is set through ``ASTROMETRICS_CONFIG_PATH`` and the file lookup,
    so saving really writes the temp file and can never reach the user's
    config. The configuration starts without any telescope section.

    Returns
    -------
    config : `AppConfiguration`
        A fresh configuration saved to the temp file.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(config_path))
    # The test session patches the file lookup; patch it for this test too.
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    assert Path(config.config_file_path or config._find_config_file()) == config_path
    return config


def _seed_rig(config: AppConfiguration, own_section: bool = True) -> None:
    """Add telescope "Rig A", with or without its own section.

    The change is saved to the temp config file.
    """
    base = {"models": "Rig A", "focal_length_mm": "300.0"}
    sections: dict[str, dict[str, str]] = {"Observatory.Telescope": base}
    if own_section:
        sections["Observatory.Telescope.Rig A"] = {
            "focal_length_mm": "450.0",
            "focal_ratio": "6.0",
            "min_altitude_deg": "20.0",
        }
    config.update_config(sections)


def _config_path(config: AppConfiguration) -> Path:
    """Return the file a configuration saves to.

    Returns
    -------
    path : `Path`
        The config file path.
    """
    return Path(config.config_file_path or config._find_config_file())


def _reloaded() -> AppConfiguration:
    """Read the config file from disk into a new `AppConfiguration`.

    Returns
    -------
    config : `AppConfiguration`
        A new configuration loaded from the file.
    """
    return AppConfiguration()


def test_limits_written_only_to_the_resolved_section(limits_config: AppConfiguration) -> None:
    """Limits land in the named section, and the file on disk changes."""
    _seed_rig(limits_config)
    path = _config_path(limits_config)
    before = path.read_text()
    assert set_active_telescope(limits_config, "Rig A", {"min_altitude_deg": 30.0, "max_altitude_deg": 80.0})
    assert path.read_text() != before
    saved = _reloaded().app_config
    assert saved["Observatory.Telescope.Rig A"]["min_altitude_deg"] == "30.0"
    assert saved["Observatory.Telescope.Rig A"]["max_altitude_deg"] == "80.0"
    assert "min_altitude_deg" not in saved["Observatory.Telescope"]
    assert saved["Observatory.Telescope"]["active_telescope"] == "Rig A"


def test_limits_never_create_a_section(limits_config: AppConfiguration) -> None:
    """No new section appears when limits are saved."""
    _seed_rig(limits_config)
    sections_before = set(_reloaded().app_config.sections())
    set_active_telescope(limits_config, "Rig A", {"hour_angle_limits_enabled": True})
    assert set(_reloaded().app_config.sections()) == sections_before


def test_limits_fall_back_to_base_section_when_named_one_is_absent(limits_config: AppConfiguration) -> None:
    """With no per-name section, the base section receives the limits."""
    _seed_rig(limits_config, own_section=False)
    sections_before = set(limits_config.app_config.sections())
    assert set_active_telescope(limits_config, "Rig A", {"min_altitude_deg": 25.0})
    assert _reloaded().app_config["Observatory.Telescope"]["min_altitude_deg"] == "25.0"
    assert set(_reloaded().app_config.sections()) == sections_before


def test_limits_refused_when_no_section_resolves(
    limits_config: AppConfiguration, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When no section resolves, the writer refuses and saves nothing."""
    _seed_rig(limits_config)
    monkeypatch.setattr(equipment_catalog_writer, "telescope_section_name_for", lambda config, name: None)
    path = _config_path(limits_config)
    before = path.read_text()
    sections_before = set(limits_config.app_config.sections())
    with pytest.raises(ValueError, match="never created"):
        set_active_telescope(limits_config, "Rig A", {"min_altitude_deg": 25.0})
    assert path.read_text() == before
    assert set(limits_config.app_config.sections()) == sections_before


def test_limits_for_an_unknown_telescope_are_refused(limits_config: AppConfiguration) -> None:
    """An unknown telescope id is refused, and the choice returns `False`."""
    with pytest.raises(ValueError, match="not a known telescope"):
        build_telescope_limits_update(limits_config, "Rig A", {"min_altitude_deg": 25.0})
    assert set_active_telescope(limits_config, "Rig A", {"min_altitude_deg": 25.0}) is False


def test_list_telescopes_still_returns_the_telescope_with_new_limits(limits_config: AppConfiguration) -> None:
    """After saving, the catalog still lists the rig with the new limits."""
    _seed_rig(limits_config)
    set_active_telescope(
        limits_config,
        "Rig A",
        {"altitude_limits_enabled": False, "min_altitude_deg": 35.0, "max_hour_angle_hours": 3.5},
    )
    telescopes = list_telescopes(_reloaded())
    assert [telescope.id for telescope in telescopes] == ["Rig A"]
    assert telescopes[0].min_altitude_deg == pytest.approx(35.0)
    assert telescopes[0].altitude_limits_enabled is False
    assert telescopes[0].max_hour_angle_hours == pytest.approx(3.5)
    assert telescopes[0].focal_length_mm == pytest.approx(450.0)


@pytest.mark.parametrize(
    "limits",
    [
        {"min_altitude_deg": 70.0, "max_altitude_deg": 60.0},
        {"max_hour_angle_hours": 0.0},
        {"min_altitude_deg": 120.0},
        {"focal_length_mm": 10.0},
    ],
)
def test_invalid_limits_are_refused_and_nothing_is_saved(
    limits_config: AppConfiguration, limits: dict[str, float]
) -> None:
    """Bad limits raise `ValueError` and leave the file untouched."""
    _seed_rig(limits_config)
    path = _config_path(limits_config)
    before = path.read_text()
    with pytest.raises(ValueError):
        set_active_telescope(limits_config, "Rig A", limits)
    assert path.read_text() == before


def test_limits_use_the_temp_config_path_only(limits_config: AppConfiguration, tmp_path: Path) -> None:
    """The configuration points at the per-test file, not the user's."""
    path = _config_path(limits_config)
    assert path.parent == tmp_path
    assert ".config/astrometrics" not in str(path)
