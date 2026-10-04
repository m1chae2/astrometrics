"""Tests for reading the observatory site from the configuration.

Both the planning code and the live-status tools read the site through one
function. These tests check what it returns for each state of the
``Observatory.Location`` section, and that `Sky` uses the same values.
"""

import configparser
from types import SimpleNamespace
from typing import Any

import pytest

from wayfindinglib.observatorylib.site_location import configured_observer_location
from wayfindinglib.tasks.control_tasks import hardware_operations


def _config(**location: str) -> SimpleNamespace:
    """Build a config whose ``app_config`` holds the given location keys.

    Returns
    -------
    config : `types.SimpleNamespace`
        An object with a real `configparser.ConfigParser` as ``app_config``.
    """
    parser = configparser.ConfigParser()
    parser.add_section("Observatory.Location")
    for key, value in location.items():
        parser.set("Observatory.Location", key, value)
    return SimpleNamespace(app_config=parser)


def test_full_section_is_read_as_numbers() -> None:
    """Latitude, longitude and elevation come back as floats."""
    site = configured_observer_location(_config(latitude="45.76", longitude="-110.74", elevation="1450"))
    assert site == {"latitude": 45.76, "longitude": -110.74, "elevation": 1450.0}


def test_missing_elevation_is_zero() -> None:
    """An unset elevation means unknown, which is 0 metres."""
    site = configured_observer_location(_config(latitude="45.76", longitude="-110.74"))
    assert site["elevation"] == pytest.approx(0.0)


@pytest.mark.parametrize("keys", [{}, {"latitude": "45.76"}, {"longitude": "-110.74"}])
def test_missing_latitude_or_longitude_gives_none(keys: dict[str, str]) -> None:
    """A site needs both latitude and longitude."""
    assert configured_observer_location(_config(**keys)) is None


def test_no_config_gives_none() -> None:
    """Without a configuration there is no site."""
    assert configured_observer_location(None) is None
    assert configured_observer_location(SimpleNamespace()) is None


def test_hardware_operations_still_offers_the_reader() -> None:
    """The live-status code reaches the same function by its old name."""
    assert hardware_operations.configured_observer_location is configured_observer_location


def _sky_with(monkeypatch: pytest.MonkeyPatch, config: SimpleNamespace) -> Any:
    """Build a `Sky` for a config without starting the catalog and the library.

    Returns
    -------
    sky : `wayfindinglib.sky.Sky`
        A planning engine that read its site from the config.
    """
    import astrometricslib
    import wayfindinglib.drivers.catalog as catalog_module
    import wayfindinglib.sky as sky_module

    monkeypatch.setattr(astrometricslib, "Astrometrics", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(catalog_module, "LocalDeepStarStore", lambda *a, **k: SimpleNamespace())
    monkeypatch.setattr(sky_module, "build_catalog_driver_registry", lambda **k: SimpleNamespace())
    return sky_module.Sky(config=config)


def test_sky_uses_the_configured_site_and_elevation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Planning reads the same site and elevation as the live-status tools."""
    config = _config(latitude="45.76", longitude="-110.74")
    sky = _sky_with(monkeypatch, config)
    site = configured_observer_location(config)
    assert (sky.latitude, sky.longitude, sky.elevation) == (
        site["latitude"],
        site["longitude"],
        site["elevation"],
    )
    assert sky.elevation == pytest.approx(0.0)


def test_sky_warns_and_uses_denver_without_a_site(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With no site, planning uses Denver and says so."""
    with caplog.at_level("WARNING"):
        sky = _sky_with(monkeypatch, _config())
    assert (sky.latitude, sky.longitude, sky.elevation) == (39.7392, -104.9903, 1600.0)
    assert "No Observatory.Location" in caplog.text
