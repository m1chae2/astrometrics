"""Tests for reading VSX and Gaia DR3 cross-match results.

The tables below copy the shape of what the live CDS XMatch service returned
on 2026-10-09, including the cases that shaped the rules: VSX listing a star
as constant (Vega, Sirius), a blended companion (Mira and VZ Cet), a faint
neighbour near a bright star (Sirius), and bright stars that Gaia DR3 does not
list (Algol, Vega, Sirius).
"""

import astropy.units as u
import pytest
from astropy.table import MaskedColumn, Table

from astrometricslib.models.known_variability import NO_GAIA_MATCH, NOT_IN_VSX, VSX_LISTED_WITHOUT_TYPE
from astrometricslib.pipelines.shared import variable_star_catalogs as catalogs


def vsx_matches() -> Table:
    """Build a VSX cross-match result.

    Returns
    -------
    matches : `astropy.table.Table`
        Rows for Algol, RR Lyr, Vega, Sirius (a constant and a faint
        neighbour) and Mira (itself and its companion).
    """
    return Table({
        "id": ["Algol", "RR Lyr", "Vega", "Sirius", "Sirius", "Mira", "Mira", "Quiet"],
        "angDist": [0.087, 0.053, 0.015, 0.055, 2.9, 0.144, 0.5, 1.0],
        "Type": MaskedColumn(
            ["EA/SD", "RRAB/BL", "CST", "CST:", "", "M", "*", "CST"], mask=[False] * 4 + [True] + [False] * 3
        ),
    })


def test_vsx_variables_get_their_type_and_constants_are_not_variables() -> None:
    """A typed variable keeps its type; CST stays constant."""
    types = catalogs.vsx_types_from_matches(["Algol", "RR Lyr", "Vega", "Quiet", "Absent"], vsx_matches())

    assert types == {
        "Algol": "EA/SD",
        "RR Lyr": "RRAB/BL",
        "Vega": "CST",
        "Quiet": "CST",
        "Absent": NOT_IN_VSX,
    }


def test_a_faint_listed_neighbour_makes_a_constant_star_suspected() -> None:
    """Sirius has CST: itself but also an untyped entry within the radius."""
    assert catalogs.vsx_types_from_matches(["Sirius"], vsx_matches())["Sirius"] == VSX_LISTED_WITHOUT_TYPE


def test_a_typed_variable_beats_an_untyped_companion() -> None:
    """Mira is typed M; its companion with type * does not override it."""
    assert catalogs.vsx_types_from_matches(["Mira"], vsx_matches())["Mira"] == "M"


def test_an_uncertain_constant_alone_stays_uncertain() -> None:
    """Only a CST: entry gives CST:, never CST."""
    matches = Table({"id": ["S"], "angDist": [0.1], "Type": ["CST:"]})

    assert catalogs.vsx_types_from_matches(["S"], matches)["S"] == "CST:"


def test_no_matches_at_all_means_not_in_vsx() -> None:
    """An empty result lists nobody."""
    assert catalogs.vsx_types_from_matches(["A", "B"], None) == {"A": NOT_IN_VSX, "B": NOT_IN_VSX}


def gaia_matches() -> Table:
    """Build a Gaia DR3 cross-match result.

    Returns
    -------
    matches : `astropy.table.Table`
        RR Lyr and Mira (variable), a quiet star (constant), and a faint
        neighbour matched near a bright star.
    """
    return Table({
        "id": ["RR Lyr", "Mira", "Quiet", "Sirius", "Sirius", "NoFlag"],
        "angDist": [0.4, 0.3, 0.2, 2.8, 1.0, 0.1],
        "VarFlag": ["VARIABLE", "VARIABLE", "CONSTANT", "CONSTANT", "NOT_AVAILABLE", ""],
        "Gmag": [7.6, 3.6, 12.1, 10.9, 16.0, 11.0],
    })


def test_gaia_flags_come_from_the_nearest_source() -> None:
    """The nearest source's flag is used, and an absent star has no match."""
    flags = catalogs.gaia_flags_from_matches(["RR Lyr", "Mira", "Quiet", "Algol"], gaia_matches())

    assert flags == {
        "RR Lyr": "VARIABLE",
        "Mira": "VARIABLE",
        "Quiet": "CONSTANT",
        "Algol": NO_GAIA_MATCH,
    }


def test_a_faint_gaia_neighbour_is_not_taken_for_a_bright_star() -> None:
    """Sirius (V = -1.46) cannot be a G = 16 source; that is another object."""
    flags = catalogs.gaia_flags_from_matches(["Sirius"], gaia_matches(), {"Sirius": -1.46})

    assert flags["Sirius"] == NO_GAIA_MATCH


def test_a_star_without_a_magnitude_takes_the_nearest_source() -> None:
    """With no magnitude to check, the nearest source is used."""
    assert catalogs.gaia_flags_from_matches(["Sirius"], gaia_matches())["Sirius"] == "NOT_AVAILABLE"


def test_a_source_with_no_flag_text_counts_as_no_match() -> None:
    """A blank flag is stored as no match, never as a flag."""
    assert catalogs.gaia_flags_from_matches(["NoFlag"], gaia_matches())["NoFlag"] == NO_GAIA_MATCH


def test_the_crossmatch_is_called_with_the_radius_and_the_position_columns() -> None:
    """The positions go out in one request with the matching radius."""
    seen = {}

    def fake_query(**arguments: object) -> None:
        seen.update(arguments)

    table = catalogs.build_position_table([("A", 10.0, 20.0), ("B", 11.0, 21.0)])
    catalogs.run_crossmatch(fake_query, table, catalogs.VSX_CATALOG)

    assert seen["cat2"] == "vizier:B/vsx/vsx"
    assert seen["max_distance"].to_value(u.arcsec) == pytest.approx(3.0)
    assert (seen["colRA1"], seen["colDec1"]) == ("ra", "dec")
    assert len(seen["cat1"]) == 2


def test_an_empty_table_makes_no_request() -> None:
    """Nothing to match, nothing to ask."""
    calls = []

    result = catalogs.run_crossmatch(lambda **kw: calls.append(kw), catalogs.build_position_table([]), "x")

    assert result is None
    assert calls == []
