"""Tests for reading optics and camera-and-optic setups from the config.

Checks the shipped example config, that a mistake in one section skips just
that section with a warning, and that a config with no optics is not an error.
"""

import configparser
import logging
from pathlib import Path

import pytest

from astrometricslib.utilities.observatory_setups import load_observatory_setups
from astrometricslib.utilities.warn_once import warn_once

EXAMPLE_CONFIG_PATH = Path(__file__).resolve().parents[2] / "astrometrics.config.example"


class ConfigParserAdapter:
    """Give a `ConfigParser` the ``get_value`` method the loader asks for."""

    def __init__(self, text: str) -> None:
        """Parse config text.

        Parameters
        ----------
        text : `str`
            The contents of a config file.
        """
        self.parser = configparser.ConfigParser()
        self.parser.read_string(text)

    def get_value(self, section: str, key: str, fallback: str | None = None) -> str | None:
        """Return one value, or `fallback` when it is not there.

        Returns
        -------
        value : `str` or `None`
            The stored text, or `fallback`.
        """
        return self.parser.get(section, key, fallback=fallback)


@pytest.fixture(autouse=True)
def fresh_warning_cache() -> None:
    """Forget which warnings were already logged, for each test."""
    warn_once.cache_clear()


def test_the_shipped_example_config_describes_the_observatorys_optics_and_pairings() -> None:
    """Check the example file, so a broken example is caught here."""
    adapter = ConfigParserAdapter(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    result = load_observatory_setups(adapter)

    optics = {optic.name: optic for optic in result.optics}
    assert set(optics) == {"Apertura 75Q", "Nikkor 300mm"}
    assert optics["Apertura 75Q"].focal_length_mm == pytest.approx(405.0)
    assert optics["Apertura 75Q"].focal_ratio == pytest.approx(5.4)
    assert optics["Nikkor 300mm"].focal_length_mm == pytest.approx(300.0)
    assert optics["Nikkor 300mm"].focal_ratio == pytest.approx(5.6)

    pairings = {(setup.camera_name, setup.optic_name) for setup in result.setups}
    assert pairings == {
        ("ZWO ASI533MM Pro", "Apertura 75Q"),
        ("Nikon D5300", "Apertura 75Q"),
        ("Nikon D5300", "Nikkor 300mm"),
    }


def test_the_example_config_gives_the_d5300_a_default_iso() -> None:
    """Check the key that replaces the old forced ISO 800."""
    parser = configparser.ConfigParser()
    parser.read_string(EXAMPLE_CONFIG_PATH.read_text(encoding="utf-8"))
    assert parser.get("Observatory.Camera.Nikon D5300", "default_iso") == "800"


def test_a_config_with_no_optics_gives_empty_results_and_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that an older config file, without these sections, still loads."""
    with caplog.at_level(logging.WARNING):
        result = load_observatory_setups(ConfigParserAdapter("[Image Library]\npath = x\n"))
    assert result.optics == ()
    assert result.setups == ()
    assert caplog.records == []


def test_an_optic_without_a_focal_length_is_skipped_with_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that one broken optic does not spoil the others."""
    text = """
[Observatory.Optics]
available = Good Scope, Broken Scope
[Observatory.Optic.Good Scope]
focal_length_mm = 400
[Observatory.Optic.Broken Scope]
focal_ratio = 5
"""
    with caplog.at_level(logging.WARNING):
        result = load_observatory_setups(ConfigParserAdapter(text))
        load_observatory_setups(ConfigParserAdapter(text))
    assert [optic.name for optic in result.optics] == ["Good Scope"]
    assert len(caplog.records) == 1
    assert "Broken Scope" in caplog.records[0].getMessage()


def test_a_setup_naming_an_unlisted_optic_is_skipped_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that a typo in a setup's optic name is reported, not ignored."""
    text = """
[Observatory.Optics]
available = Good Scope
[Observatory.Optic.Good Scope]
focal_length_mm = 400
[Observatory.Setups]
available = Camera on Typo, Camera on Good
[Observatory.Setup.Camera on Typo]
camera = Some Camera
optic = Goood Scope
[Observatory.Setup.Camera on Good]
camera = Some Camera
optic = Good Scope
"""
    with caplog.at_level(logging.WARNING):
        result = load_observatory_setups(ConfigParserAdapter(text))
    assert [setup.name for setup in result.setups] == ["Camera on Good"]
    assert len(caplog.records) == 1
    assert "Camera on Typo" in caplog.records[0].getMessage()


def test_a_focal_length_that_is_not_a_positive_number_is_skipped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that zero and text are both refused."""
    text = """
[Observatory.Optics]
available = Zero Scope, Text Scope
[Observatory.Optic.Zero Scope]
focal_length_mm = 0
[Observatory.Optic.Text Scope]
focal_length_mm = long
"""
    with caplog.at_level(logging.WARNING):
        result = load_observatory_setups(ConfigParserAdapter(text))
    assert result.optics == ()
    assert len(caplog.records) == 2
