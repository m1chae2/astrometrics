"""Tests for `is_solar_system_target`.

A planet or the Moon has no star-catalog entry, so code that names the star at
the centre of a frame must skip these targets. The check works on the target's
name, so these tests cover the spellings a target folder can have.
"""

import pytest

from astrometricslib.pipelines.shared.solar_system_targets import is_solar_system_target


@pytest.mark.parametrize("name", ["Mars", "mars", "MARS", " Mars ", "Moon", "Sun", "Jupiter", "Pluto"])
def test_solar_system_names_are_recognised(name: str) -> None:
    """Planets, the Moon, the Sun and dwarf planets match in any case."""
    assert is_solar_system_target(name) is True


@pytest.mark.parametrize("name", ["Alhena", "Sirius", "M 57", "M_13", "NGC 7331", "Mars Pathfinder", "Marsh"])
def test_stars_and_deep_sky_targets_are_not_solar_system(name: str) -> None:
    """Only the exact body name matches; "Marsh" and the like do not."""
    assert is_solar_system_target(name) is False


@pytest.mark.parametrize("name", [None, ""])
def test_a_missing_name_is_not_a_solar_system_body(name: str | None) -> None:
    """No name means no information, which is treated as a star."""
    assert is_solar_system_target(name) is False


def test_underscores_and_hyphens_are_read_as_spaces() -> None:
    """Folder names use underscores, so they are read as spaces."""
    assert is_solar_system_target("_Mars_") is True
    assert is_solar_system_target("-Venus-") is True
