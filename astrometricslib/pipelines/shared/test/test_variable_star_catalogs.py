"""Tests for asking VizieR about VSX and for reading VSX and Gaia DR3 matches.

The match tables below copy the shape of what the CDS XMatch service returned
on 2026-10-09 (the VizieR path builds the same columns), including the
cases that shaped the rules: VSX listing a star
as constant (Vega, Sirius), a blended companion (Mira and VZ Cet), a faint
neighbour near a bright star (Sirius), and bright stars that Gaia DR3 does not
list (Algol, Vega, Sirius).
"""

import sys
import types
from pathlib import Path

import numpy as np
import pytest
import requests
from astropy.table import MaskedColumn, Table

from astrometricslib._internal.drivers.catalog_answer_store import catalog_answers_path
from astrometricslib._internal.drivers.catalog_cache import CatalogUnavailableError
from astrometricslib._internal.drivers.outside_services import gateway
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import (
    NO_GAIA_MATCH,
    NOT_IN_VSX,
    VSX_LISTED_WITHOUT_TYPE,
    KnownVariability,
    classify_gaia_variable_flag,
)
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


def test_gaia_flags_are_matched_by_source_number() -> None:
    """A listed source keeps its class; an unlisted one has no match."""
    flags = catalogs.gaia_flags_from_classes(
        {"RR Lyr": 111, "Quiet": 222, "Algol": None}, {111: "RR", 333: "ECL"}
    )

    assert flags == {"RR Lyr": "VARIABLE:RR", "Quiet": NO_GAIA_MATCH}
    assert classify_gaia_variable_flag(flags["RR Lyr"]) is KnownVariability.KNOWN_VARIABLE
    assert classify_gaia_variable_flag(flags["Quiet"]) is KnownVariability.NOT_LISTED


def test_a_gaia_class_that_is_blank_is_still_a_variable() -> None:
    """The source is in the variability table, so it is a listed variable."""
    assert catalogs.gaia_flags_from_classes({"S": 5}, {5: ""}) == {"S": "VARIABLE:UNCLASSIFIED"}


def test_gaia_classes_are_read_from_one_field_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """One request answers for the field: source number to class."""
    rows = Table({
        "Source": [111, 333],
        "Class": ["RR", "ECL"],
        "RA_ICRS": [10.0, 10.1],
        "DE_ICRS": [20.0, 20.1],
    })
    asked = install_fake_vizier(monkeypatch, {HARVARD_VIZIER: rows, CDS_VIZIER: rows})

    classes = catalogs.look_up_gaia_classes(catalogs.build_position_table([("A", 10.0, 20.0)]))

    assert classes == {111: "RR", 333: "ECL"}
    assert asked == [HARVARD_VIZIER]


def test_gaia_classes_fall_back_and_fail_like_vsx(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strasbourg answers when Harvard is down; both down raises."""
    rows = Table({"Source": [111], "Class": ["RR"], "RA_ICRS": [10.0], "DE_ICRS": [20.0]})
    positions = catalogs.build_position_table([("A", 10.0, 20.0)])
    asked = install_fake_vizier(monkeypatch, {HARVARD_VIZIER: ConnectionError("down"), CDS_VIZIER: rows})
    assert catalogs.look_up_gaia_classes(positions) == {111: "RR"}
    assert asked == [HARVARD_VIZIER, CDS_VIZIER]
    forget_stored_answers()
    install_fake_vizier(monkeypatch, {HARVARD_VIZIER: ConnectionError("a"), CDS_VIZIER: ConnectionError("b")})
    with pytest.raises(CatalogUnavailableError, match="no stored results"):
        catalogs.look_up_gaia_classes(positions)


@pytest.fixture(autouse=True)
def isolated_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test its own stored catalog answers.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's folder.
    monkeypatch : `pytest.MonkeyPatch`
        Sets the environment.
    """
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))


def forget_stored_answers() -> None:
    """Delete the stored catalog answers so the next question goes out."""
    catalog_answers_path().unlink(missing_ok=True)


CDS_VIZIER = "vizier.cds.unistra.fr"
HARVARD_VIZIER = "vizier.cfa.harvard.edu"


def vsx_field_rows() -> Table:
    """Build the rows VizieR would return for a field around (10, 20).

    Returns
    -------
    rows : `astropy.table.Table`
        An Algol-like variable 1 arcsecond from star A, a constant 2
        arcseconds from star B, and a variable 10 arcseconds from star B.
    """
    return Table({
        "Name": ["Near", "Quiet", "Far"],
        "RAJ2000": [10.0, 10.0 + 2.0 / 3600.0, 10.0],
        "DEJ2000": [20.0 + 1.0 / 3600.0, 21.0, 21.0 + 10.0 / 3600.0],
        "Type": ["EA", "CST", "RRAB"],
    })


def install_fake_vizier(
    monkeypatch: pytest.MonkeyPatch, outcomes: dict[str, object], preferred: str = HARVARD_VIZIER
) -> list[str]:
    """Replace astroquery's `Vizier` with one that answers per server.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Undoes the replacement.
    outcomes : `dict` [`str`, `object`]
        For each server, an exception to raise or the rows to return.
    preferred : `str`, optional
        The server the setting names.

    Returns
    -------
    asked : `list` [`str`]
        Filled in as the code runs: each server asked, in order.
    """
    asked: list[str] = []

    class FakeVizier:
        """A VizieR client that answers from `outcomes`."""

        def __init__(self, **options: object) -> None:
            self.server = str(options["vizier_server"])

        def query_region(self, *args: object, **kwargs: object) -> list[Table]:
            """Raise or return what `outcomes` says for this server.

            Returns
            -------
            result : `list` [`astropy.table.Table`]
                The configured rows as a one-table list.
            """
            asked.append(self.server)
            outcome = outcomes[self.server]
            if isinstance(outcome, BaseException):
                raise outcome
            return [outcome]  # type: ignore[list-item]

    configuration = types.SimpleNamespace(get_vizier_preferred_server=lambda: preferred)
    monkeypatch.setitem(sys.modules, "astroquery.vizier", types.SimpleNamespace(Vizier=FakeVizier))
    # The gateway now owns the settings lookup and the memory of which
    # server answered last.
    monkeypatch.setattr(gateway, "get_configuration", lambda: configuration)
    monkeypatch.setattr(gateway, "_last_answered", {})
    monkeypatch.setattr(gateway, "_preference_when_remembered", {})
    return asked


def test_the_field_is_asked_of_harvard_first_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The preferred server answers and the other is not asked."""
    asked = install_fake_vizier(monkeypatch, {HARVARD_VIZIER: vsx_field_rows(), CDS_VIZIER: vsx_field_rows()})
    positions = catalogs.build_position_table([("A", 10.0, 20.0)])

    matches = catalogs.look_up_vsx(positions)

    assert asked == [HARVARD_VIZIER]
    assert list(matches["id"]) == ["A"]


def test_the_field_falls_back_to_strasbourg_when_harvard_is_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """A refused connection or a 504 hands the field to the other server."""
    for failure in (
        ConnectionError("down"),
        requests.exceptions.HTTPError(response=types.SimpleNamespace(status_code=504)),
    ):
        forget_stored_answers()
        asked = install_fake_vizier(monkeypatch, {HARVARD_VIZIER: failure, CDS_VIZIER: vsx_field_rows()})

        matches = catalogs.look_up_vsx(catalogs.build_position_table([("A", 10.0, 20.0)]))

        assert asked == [HARVARD_VIZIER, CDS_VIZIER]
        assert list(matches["id"]) == ["A"]


def test_when_both_servers_fail_the_error_reaches_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """No answer is invented; with nothing stored the star stays unverified."""
    asked = install_fake_vizier(
        monkeypatch, {HARVARD_VIZIER: ConnectionError("first"), CDS_VIZIER: TimeoutError("second")}
    )

    with pytest.raises(CatalogUnavailableError, match="second"):
        catalogs.look_up_vsx(catalogs.build_position_table([("A", 10.0, 20.0)]))

    assert len(asked) == 2


def test_an_answer_that_hit_the_row_limit_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """A full table may be cut off, so it is not trusted."""
    monkeypatch.setattr(catalogs, "FIELD_ROW_LIMIT", 3)
    install_fake_vizier(monkeypatch, {HARVARD_VIZIER: vsx_field_rows(), CDS_VIZIER: vsx_field_rows()})

    with pytest.raises(ExternalServiceError):
        catalogs.look_up_vsx(catalogs.build_position_table([("A", 10.0, 20.0)]))


def test_stars_match_the_field_rows_only_within_the_match_radius() -> None:
    """An entry 1 arcsec away matches; one 10 arcsec away does not."""
    positions = catalogs.build_position_table([("A", 10.0, 20.0), ("B", 10.0, 21.0)])

    matches = catalogs.match_to_catalog_rows(positions, vsx_field_rows(), "RAJ2000", "DEJ2000")

    assert sorted(zip(matches["id"], matches["Name"], strict=True)) == [("A", "Near"), ("B", "Quiet")]
    assert matches["angDist"][list(matches["id"]).index("A")] == pytest.approx(1.0, abs=0.01)
    types_found = catalogs.vsx_types_from_matches(["A", "B"], matches)
    assert types_found == {"A": "EA", "B": "CST"}


def test_a_field_with_no_rows_matches_nothing() -> None:
    """An empty answer gives no match table: every star is not in VSX."""
    positions = catalogs.build_position_table([("A", 10.0, 20.0)])

    matches = catalogs.match_to_catalog_rows(positions, vsx_field_rows()[:0], "RAJ2000", "DEJ2000")

    assert matches is None
    assert catalogs.vsx_types_from_matches(["A"], matches) == {"A": NOT_IN_VSX}


def test_distant_stars_are_split_into_separate_fields() -> None:
    """Near stars share a request; far ones get their own."""
    positions = catalogs.build_position_table([("A", 10.0, 20.0), ("B", 10.5, 20.0), ("C", 200.0, -30.0)])

    groups = catalogs.group_into_fields(positions)

    assert [list(group) for group in groups] == [[0, 1], [2]]
    assert np.concatenate(groups).tolist() == [0, 1, 2]
    assert catalogs.group_into_fields(catalogs.build_position_table([])) == []


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
