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


def vsx_range_matches() -> Table:
    """Build a VSX result with the range columns the live service returns.

    The columns and the Algol, delta Cep and Mira values were read from the
    live CDS XMatch service on 2026-10-23. The last two rows are made up to
    cover the amplitude flag and a masked range.

    Returns
    -------
    matches : `astropy.table.Table`
        Rows for Algol, delta Cep, Mira (and its companion), a star whose
        minimum column is an amplitude, and a typed star with no range.
    """
    return Table({
        "id": ["Algol", "dCep", "Mira", "Mira", "AmpStar", "NoRange"],
        "angDist": [0.087, 0.05, 0.144, 0.5, 0.2, 0.3],
        "Type": ["EA/SD", "DCEP", "M", "*", "SR", "LPV"],
        "max": MaskedColumn([2.09, 3.49, 2.0, 9.5, 11.0, 0.0], mask=[False] * 5 + [True]),
        "min": MaskedColumn([3.3, 4.36, 10.1, 12.0, 0.8, 0.0], mask=[False] * 5 + [True]),
        "f_min": ["", "", "", "", "(", ""],
        "n_max": ["V", "V", "V", "V", "p", ""],
        "Period": MaskedColumn([2.867343, 5.36629, 331.3, 4750.0, 120.0, 0.0], mask=[False] * 5 + [True]),
    })


def test_the_vsx_range_is_the_faint_minus_the_bright_magnitude_with_the_period_and_band() -> None:
    """Algol lists 2.09 to 3.30 V, a range of 1.21 mag every 2.867343 d."""
    entries = catalogs.vsx_entries_from_matches(["Algol", "dCep"], vsx_range_matches())

    algol = entries["Algol"]
    assert algol is not None
    assert (algol.bright_magnitude, algol.faint_magnitude) == (2.09, 3.3)
    assert algol.amplitude_mag == pytest.approx(1.21)
    assert algol.period_days == pytest.approx(2.867343)
    assert (algol.variability_type, algol.band) == ("EA/SD", "V")
    assert entries["dCep"].amplitude_mag == pytest.approx(0.87)


def test_the_entry_is_the_one_that_gives_the_type_so_a_companion_cannot_replace_it() -> None:
    """Mira is its nearest typed entry (M, 2.0 to 10.1), not its companion."""
    mira = catalogs.vsx_entries_from_matches(["Mira"], vsx_range_matches())["Mira"]
    assert mira is not None
    assert mira.variability_type == "M"
    assert mira.amplitude_mag == pytest.approx(8.1)
    assert mira.period_days == pytest.approx(331.3)


def test_an_amplitude_flag_means_the_minimum_column_is_the_amplitude() -> None:
    """A ( flag means the minimum column is the amplitude."""
    entry = catalogs.vsx_entries_from_matches(["AmpStar"], vsx_range_matches())["AmpStar"]
    assert entry is not None
    assert entry.amplitude_mag == pytest.approx(0.8)
    assert entry.faint_magnitude is None
    assert entry.band == "p"


def test_a_typed_star_with_a_masked_range_has_an_entry_with_nothing_in_it() -> None:
    """The type is kept; the range and the period are missing, not zero."""
    entry = catalogs.vsx_entries_from_matches(["NoRange"], vsx_range_matches())["NoRange"]
    assert entry is not None
    assert entry.variability_type == "LPV"
    assert (entry.bright_magnitude, entry.faint_magnitude, entry.amplitude_mag, entry.period_days) == (
        None,
        None,
        None,
        None,
    )


def test_constants_untyped_and_absent_stars_have_no_entry() -> None:
    """Only a typed variable gets an entry; the others are None."""
    entries = catalogs.vsx_entries_from_matches(["Vega", "Sirius", "Absent"], vsx_matches())
    assert entries == {"Vega": None, "Sirius": None, "Absent": None}
    assert catalogs.vsx_entries_from_matches(["Algol"], None) == {"Algol": None}


def test_a_result_without_the_range_columns_still_gives_the_type() -> None:
    """A table without range columns gives entries with no range."""
    entry = catalogs.vsx_entries_from_matches(["Algol"], vsx_matches())["Algol"]
    assert entry is not None
    assert entry.variability_type == "EA/SD"
    assert entry.amplitude_mag is None
    assert entry.period_days is None


def test_a_negative_range_or_period_is_dropped() -> None:
    """A negative range or a period of zero is not kept."""
    table = Table({
        "id": ["Odd"],
        "angDist": [0.1],
        "Type": ["EA"],
        "max": [12.0],
        "min": [11.0],
        "f_min": [""],
        "n_max": ["V"],
        "Period": [0.0],
    })
    entry = catalogs.vsx_entries_from_matches(["Odd"], table)["Odd"]
    assert entry is not None
    assert entry.amplitude_mag is None
    assert entry.faint_magnitude is None
    assert entry.bright_magnitude == pytest.approx(12.0)
    assert entry.period_days is None


def test_a_minimum_in_another_band_than_the_maximum_gives_no_range() -> None:
    """A range across two bands gives no faint magnitude and no range."""
    table = Table({
        "id": ["Mixed", "Same"],
        "angDist": [0.1, 0.1],
        "Type": ["EA", "EA"],
        "max": [9.0, 9.0],
        "min": [10.5, 10.5],
        "f_min": ["", ""],
        "n_max": ["V", "V"],
        "n_min": ["p", "V"],
        "Period": [1.0, 1.0],
    })
    entries = catalogs.vsx_entries_from_matches(["Mixed", "Same"], table)
    assert entries["Mixed"].amplitude_mag is None
    assert entries["Mixed"].faint_magnitude is None
    assert entries["Mixed"].bright_magnitude == pytest.approx(9.0)
    assert entries["Same"].amplitude_mag == pytest.approx(1.5)
