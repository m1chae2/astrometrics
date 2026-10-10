"""Purpose: Test reading the observatory site from the config file.

Description: The BJD_TDB time correction needs the observer's position. The
site comes from the ``[Observatory.Location]`` section. These tests check
that a complete section is read, that a missing or invalid section gives
`None` (never a made-up default site), and that the shipped example config,
which has the three keys commented out, gives no site.
"""

import logging
from pathlib import Path

import pytest

from astrometricslib.foundation.config import _TomlSectionedConfig
from astrometricslib.foundation.observatory_site import load_observatory_site

EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "astrometrics.config.example.toml"


class ConfigParserAdapter:
    """Give a parsed config the ``get_value`` method the loader asks for."""

    def __init__(self, text: str) -> None:
        """Parse config text.

        Parameters
        ----------
        text : `str`
            The contents of a config file.
        """
        self.parser = _TomlSectionedConfig()
        self.parser.read_string(text)

    def get_value(self, section: str, key: str, fallback: str | None = None) -> str | None:
        """Return one value, or `fallback` when it is not there.

        Returns
        -------
        value : `str` or `None`
            The stored text, or `fallback`.
        """
        return self.parser.get(section, key, fallback=fallback)


def test_a_complete_location_section_is_read() -> None:
    """Latitude, longitude and elevation come back as numbers."""
    config = ConfigParserAdapter(
        '["Observatory.Location"]\nlatitude = "45.76"\nlongitude = "-110.74"\nelevation = "1500"\n'
    )

    site = load_observatory_site(config)

    assert site is not None
    assert site.latitude_deg == pytest.approx(45.76)
    assert site.longitude_deg == pytest.approx(-110.74)
    assert site.elevation_m == pytest.approx(1500.0)


def test_a_missing_elevation_counts_as_sea_level() -> None:
    """The elevation is optional; the other two keys are not."""
    config = ConfigParserAdapter('["Observatory.Location"]\nlatitude = "45.0"\nlongitude = "-110.0"\n')

    site = load_observatory_site(config)

    assert site is not None
    assert site.elevation_m == pytest.approx(0.0)


@pytest.mark.parametrize(
    "section",
    [
        '["Observatory.Telescope"]\nhostname = "localhost"\n',
        '["Observatory.Location"]\nlatitude = "45.0"\n',
    ],
)
def test_no_site_is_made_up_when_the_config_has_none(section: str) -> None:
    """A missing section, or a section without longitude, gives `None`."""
    assert load_observatory_site(ConfigParserAdapter(section)) is None


@pytest.mark.parametrize(
    ("latitude", "longitude"),
    [("north", "-110"), ("95.0", "-110"), ("nan", "-110"), ("45", "inf")],
)
def test_an_invalid_site_gives_none_and_a_warning(
    latitude: str, longitude: str, caplog: pytest.LogCaptureFixture
) -> None:
    """Text, an out-of-range latitude, NaN and infinity are all refused."""
    config = ConfigParserAdapter(
        f'["Observatory.Location"]\nlatitude = "{latitude}"\nlongitude = "{longitude}"\n'
    )

    with caplog.at_level(logging.WARNING):
        site = load_observatory_site(config)

    assert site is None
    assert "not a valid site" in caplog.text


def test_the_shipped_example_config_gives_no_site_until_the_keys_are_uncommented() -> None:
    """The example leaves the site for the owner to fill in."""
    config = ConfigParserAdapter(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))

    assert load_observatory_site(config) is None
