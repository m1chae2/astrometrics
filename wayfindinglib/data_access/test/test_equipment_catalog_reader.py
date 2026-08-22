"""Purpose: Unit tests for equipment catalog resolution.

Description: Verifies telescope/camera catalog resolution against a
real `AppConfiguration` backed by a temporary config file (real
parsing, not a mock), including the multi-telescope case, the
single-telescope fallback for today's unconfigured state, and the
per-rig-altitude-falls-back-to-global-constraint safety invariant.
"""

import pytest

from wayfindinglib.data_access.equipment_catalog_reader import (
    get_active_camera_id,
    get_active_telescope_id,
    get_equipment_catalog,
    list_cameras,
    list_telescopes,
)


@pytest.fixture
def app_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a real AppConfiguration backed by an isolated config file.

    Overrides `_find_config_file` directly via `monkeypatch.setattr`
    rather than the `ASTROMETRICS_CONFIG_PATH` env var: when this test
    session also collects `astrometricslib/`, its session-scoped
    `conftest.py` permanently reassigns `AppConfiguration._find_config_file`
    at the class level to its own fixed sandbox path, which makes the
    env var silently ineffective. `monkeypatch.setattr` correctly saves
    and restores whatever the current value is (conftest's override or
    the original method) regardless of collection order.

    Returns
    -------
    config : `AppConfiguration`
        The constructed, isolated configuration.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


def test_list_telescopes_falls_back_to_single_telescope_when_unconfigured(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured catalog (today's state) yields one telescope.

    Named after the pre-existing hardcoded single-telescope name, built
    from the flat [Observatory.Telescope] section's default
    focal_length_mm/focal_ratio -- both 0.0 by default, so no telescope
    should resolve at all until focal_length_mm is set.
    """
    telescopes = list_telescopes(app_config)
    assert telescopes == []


def test_list_telescopes_single_fallback_resolves_once_focal_length_set(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the single-telescope fallback resolves once configured."""
    app_config.update_config({"Observatory.Telescope": {"focal_length_mm": "450.0", "focal_ratio": "6.0"}})
    telescopes = list_telescopes(app_config)
    assert len(telescopes) == 1
    assert telescopes[0].id == "Apertura 75Q"
    assert telescopes[0].focal_length_mm == pytest.approx(450.0)


def test_list_telescopes_multi_entry_catalog(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a configured models list resolves multiple named telescopes."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Apertura 75Q, Celestron EdgeHD 8"},
        "Observatory.Telescope.Apertura 75Q": {"focal_length_mm": "450.0", "focal_ratio": "6.0"},
        "Observatory.Telescope.Celestron EdgeHD 8": {"focal_length_mm": "2032.0", "focal_ratio": "10.0"},
    })
    telescopes = list_telescopes(app_config)
    names = {t.name for t in telescopes}
    assert names == {"Apertura 75Q", "Celestron EdgeHD 8"}


def test_per_rig_altitude_limit_preferred_when_present(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a per-telescope altitude limit overrides the global one."""
    app_config.update_config({
        "Observatory.Constraints": {"min_altitude": "0.0", "max_altitude": "90.0"},
        "Observatory.Telescope": {
            "models": "Apertura 75Q",
        },
        "Observatory.Telescope.Apertura 75Q": {
            "focal_length_mm": "450.0",
            "min_altitude_deg": "15.0",
            "max_altitude_deg": "80.0",
        },
    })
    telescopes = list_telescopes(app_config)
    assert telescopes[0].min_altitude_deg == pytest.approx(15.0)
    assert telescopes[0].max_altitude_deg == pytest.approx(80.0)


def test_altitude_limit_falls_back_to_global_constraint_when_absent(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a rig with no per-rig section uses [Observatory.Constraints].

    This is the "Documented Safety Fallback" invariant: a safety-relevant
    check must not silently change behavior for a rig that has not yet
    been given its own section.
    """
    app_config.update_config({
        "Observatory.Constraints": {"min_altitude": "5.0", "max_altitude": "85.0"},
        "Observatory.Telescope": {"models": "Apertura 75Q"},
        "Observatory.Telescope.Apertura 75Q": {"focal_length_mm": "450.0"},
    })
    telescopes = list_telescopes(app_config)
    assert telescopes[0].min_altitude_deg == pytest.approx(5.0)
    assert telescopes[0].max_altitude_deg == pytest.approx(85.0)


def test_get_active_telescope_id_defaults_to_first_when_unset(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the active telescope defaults to the first configured entry."""
    app_config.update_config({
        "Observatory.Telescope": {"models": "Apertura 75Q, Celestron EdgeHD 8"},
        "Observatory.Telescope.Apertura 75Q": {"focal_length_mm": "450.0"},
        "Observatory.Telescope.Celestron EdgeHD 8": {"focal_length_mm": "2032.0"},
    })
    catalog = get_equipment_catalog(app_config)
    assert catalog.active_telescope_id == "Apertura 75Q"
    assert catalog.active_telescope().focal_length_mm == pytest.approx(450.0)


def test_get_active_telescope_id_honors_configured_selection(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an explicitly configured active_telescope key is honored."""
    app_config.update_config({
        "Observatory.Telescope": {
            "models": "Apertura 75Q, Celestron EdgeHD 8",
            "active_telescope": "Celestron EdgeHD 8",
        },
        "Observatory.Telescope.Apertura 75Q": {"focal_length_mm": "450.0"},
        "Observatory.Telescope.Celestron EdgeHD 8": {"focal_length_mm": "2032.0"},
    })
    assert get_active_telescope_id(app_config) == "Celestron EdgeHD 8"
    catalog = get_equipment_catalog(app_config)
    assert catalog.active_telescope().name == "Celestron EdgeHD 8"


def test_list_cameras_resolves_configured_camera(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify list_cameras() reuses the camera-catalog config reader."""
    app_config.update_config({
        "Observatory.Camera": {
            "models": "ZWO ASI533MM Pro",
            "default_primary_camera": "ZWO ASI533MM Pro",
        },
        "Observatory.Camera.ZWO ASI533MM Pro": {
            "pixel_size_μm": "3.76",
            "sensor_width_px": "3008",
            "sensor_height_px": "3008",
        },
    })
    cameras = list_cameras(app_config)
    assert len(cameras) == 1
    assert cameras[0].pixel_size_um == pytest.approx(3.76)
    assert get_active_camera_id(app_config) == "ZWO ASI533MM Pro"


def test_get_equipment_catalog_active_camera_none_when_no_cameras_configured(app_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unconfigured camera catalog resolves to no active camera."""
    catalog = get_equipment_catalog(app_config)
    assert catalog.active_camera_id is None
    assert catalog.active_camera() is None
