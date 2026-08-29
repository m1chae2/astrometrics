"""Purpose: Unit tests for site profile resolution and default seeding.

Description: Verifies get_or_seed_default_site_profile() seeds from the
[Observatory.Location] config section when present, falls back to the
same Denver coordinates wayfindinglib.sky.Sky uses when absent, records
the seeded profile so a second call reads it back rather than
re-seeding, and returns an already-recorded profile unchanged.
"""

import pytest

from wayfindinglib.data_access.site_profile_reader import get_or_seed_default_site_profile
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile


@pytest.fixture
def isolated_butler_and_config(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler and AppConfiguration, both fully isolated.

    Overrides `_find_config_file` directly via `monkeypatch.setattr`
    rather than the `ASTROMETRICS_CONFIG_PATH` env var, which
    `astrometricslib/conftest.py`'s session-scoped class-level override
    makes silently ineffective when this session also collects
    `astrometricslib/`.

    Returns
    -------
    butler, config : `tuple` [`DiskButler`, `AppConfiguration`]
        The constructed, isolated butler and its backing configuration.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config), config


def test_seeds_denver_fallback_when_location_unconfigured(isolated_butler_and_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the default profile seeds with Sky exact Denver fallback."""
    butler, config = isolated_butler_and_config
    profile = get_or_seed_default_site_profile(butler, config)
    assert profile.latitude_deg == pytest.approx(39.7392)
    assert profile.longitude_deg == pytest.approx(-104.9903)
    assert profile.elevation_m == pytest.approx(1600.0)


def test_seeds_from_configured_location(isolated_butler_and_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the profile seeds from [Observatory.Location] when set."""
    butler, config = isolated_butler_and_config
    config.update_config({
        "Observatory.Location": {"latitude": "34.0522", "longitude": "-118.2437", "elevation": "71.0"}
    })
    profile = get_or_seed_default_site_profile(butler, config)
    assert profile.latitude_deg == pytest.approx(34.0522)
    assert profile.longitude_deg == pytest.approx(-118.2437)
    assert profile.elevation_m == pytest.approx(71.0)


def test_seeding_persists_so_second_call_does_not_reseed(isolated_butler_and_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the seeded profile records rather than regenerating."""
    butler, config = isolated_butler_and_config
    first = get_or_seed_default_site_profile(butler, config)
    second = get_or_seed_default_site_profile(butler, config)
    assert first.id == second.id
    assert butler.exists("site_profile", {"id": "default"}) is True


def test_returns_existing_profile_unchanged(isolated_butler_and_config):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an already-recorded profile is returned as-is, not reseeded."""
    butler, config = isolated_butler_and_config
    existing = SiteProfile(id="default", name="My Backyard", latitude_deg=1.0, longitude_deg=2.0)
    butler.put(existing, "site_profile", {"id": "default"})

    resolved = get_or_seed_default_site_profile(butler, config)
    assert resolved.name == "My Backyard"
    assert resolved.latitude_deg == pytest.approx(1.0)
