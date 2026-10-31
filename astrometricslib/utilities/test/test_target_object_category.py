"""Purpose: Tests for the grouping of SIMBAD type codes into broad kinds.

Description: Checks galaxies, nebulae, clusters and stars, candidate codes
that end in a question mark, solar-system names, and codes that fit no kind.
"""

import pytest

from astrometricslib import target_object_category


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("G", "galaxy"),
        ("GiG", "galaxy"),
        ("Sy2", "galaxy"),
        ("AGN", "galaxy"),
        ("LIN", "galaxy"),
        ("QSO", "galaxy"),
        ("PN", "nebula"),
        ("HII", "nebula"),
        ("RNe", "nebula"),
        ("SNR", "nebula"),
        ("EmO", "nebula"),
        ("OpC", "cluster"),
        ("GlC", "cluster"),
        ("Cl*", "cluster"),
        ("As*", "cluster"),
        ("C?*", "cluster"),
        ("*", "star"),
        ("**", "star"),
        ("V*", "star"),
        ("SB*", "star"),
        ("WR*", "star"),
        ("dS*", "star"),
        ("PN?", "nebula"),
        ("LP?", "star"),
        ("G?", "galaxy"),
    ],
)
def test_codes_are_grouped_into_the_broad_kinds(code: str, category: str) -> None:
    """Each SIMBAD code lands in the kind SIMBAD's type tree puts it in."""
    assert target_object_category(code) == category


@pytest.mark.parametrize("code", [None, "", "  ", "Unknown", "Pl"])
def test_unknown_codes_have_no_category(code: str | None) -> None:
    """No code, or a code that fits no kind, gives `None`."""
    assert target_object_category(code) is None


@pytest.mark.parametrize("target_id", ["Moon", "Mars", "Sun", "C 2022 E3 ZTF"])
def test_solar_system_names_win_over_the_code(target_id: str) -> None:
    """A planet, the Moon, the Sun or a comet is a solar-system body."""
    assert target_object_category(None, target_id) == "solar_system"
    assert target_object_category("*", target_id) == "solar_system"


def test_an_ordinary_id_does_not_change_the_code_category() -> None:
    """A deep-sky id leaves the answer to the code."""
    assert target_object_category("GlC", "M 13") == "cluster"
