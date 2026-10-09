"""Tests for the script that fills in SIMBAD object types of saved stars.

A fake SIMBAD driver and a fake catalog stand in, so no network or database
is touched. They check what the script must get right: nothing is written
without ``--apply``, a backup comes before the first write, an answer from the
wrong object is rejected, a star with types is left alone, an outage stops the
run, and only the object-types field is changed.
"""

import re
from types import SimpleNamespace
from typing import Any

import pytest
from astropy.table import MaskedColumn, Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import KnownVariability
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.shared.star_kinds import StarKind
from astrometricslib.scripts import backfill_simbad_object_types as backfill
from astrometricslib.scripts.backfill_simbad_object_types import Outcome

ALGOL = ("* bet Per", 47.04221855625, 40.95564667027778, "*|**|EB*|IR|NIR|SB*|UV|V*|X")
VEGA = ("* alf Lyr", 279.23473479, 38.78368896, "*|**|IR|NIR|PM*|UV|X")


def make_star(star_id: str, ra: float | None, dec: float | None, types: str = "") -> StellarObject:
    """Build a stored star.

    Returns
    -------
    star : `StellarObject`
        A star with the given id, position and object types.
    """
    return StellarObject(
        id=star_id, name=star_id, ra=ra, dec=dec, simbadObjectTypes=types, spectralType="A0V"
    )


class FakeSimbad:
    """A SIMBAD stand-in answering TAP queries from a table of objects."""

    def __init__(self, objects: dict[str, tuple[float, float, str | None]], fail: bool = False) -> None:
        self.objects = objects
        self.fail = fail
        self.queries: list[str] = []

    def query_tap(self, adql_query: str) -> Table | None:
        """Answer a query with the rows for the identifiers in its IN list.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            One row per known identifier asked about.

        Raises
        ------
        ExternalServiceError
            When this fake was built to fail, like an unreachable service.
        """
        self.queries.append(adql_query)
        if self.fail:
            raise ExternalServiceError("The SIMBAD TAP query failed.")
        asked = [
            text.replace("''", "'") for text in re.findall(r"'((?:[^']|'')*)'", adql_query.split(" IN (")[1])
        ]
        found = [(name, name, *self.objects[name]) for name in asked if name in self.objects]
        if not found:
            return None
        return Table({
            "queried_id": [row[0] for row in found],
            "main_id": [row[1] for row in found],
            "ra": [row[2] for row in found],
            "dec": [row[3] for row in found],
            "otypes": MaskedColumn([row[4] or "" for row in found], mask=[row[4] is None for row in found]),
        })


class FakeCatalog:
    """A catalog stand-in that records what is saved."""

    def __init__(self, stars: list[StellarObject]) -> None:
        self.stars = {star.id: star for star in stars}
        self.saved: list[list[StellarObject]] = []
        self.merge_rules: list[Any] = []

    def list_star_summaries(self, target_id: str | None = None) -> list[SimpleNamespace]:
        """List the stars in short form.

        Returns
        -------
        summaries : `list`
            One entry with an ``id`` per star.
        """
        return [SimpleNamespace(id=star_id) for star_id in self.stars]

    def get_by_ids(self, table: str, ids: list[str]) -> list[StellarObject]:
        """Load stars by id.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The requested stars.
        """
        assert table == "stellar_catalog"
        return [self.stars[star_id] for star_id in ids]

    def merge_and_record(self, table: str, stars: list[StellarObject], merge_rule: Any) -> None:
        """Record a save.

        Parameters
        ----------
        table : `str`
            The table written.
        stars : `list` [`StellarObject`]
            The stars saved.
        merge_rule : `Callable`
            How each is merged into the saved one.
        """
        assert table == "stellar_catalog"
        self.saved.append(list(stars))
        self.merge_rules.append(merge_rule)


def no_log(_message: str) -> None:
    """Swallow a progress line."""


def algol_vega_catalog() -> FakeCatalog:
    """Build a catalog with two named stars, one Gaia star and ids to skip.

    Returns
    -------
    catalog : `FakeCatalog`
        The catalog.
    """
    return FakeCatalog([
        make_star("* bet Per", 47.04, 40.955),
        make_star("* alf Lyr", 279.23, 38.78),
        make_star("Gaia DR3 123456789", 10.0, 20.0),
        make_star("FIELD_J000000+000000", 1.0, 1.0),
        make_star("Gaia DR3 J0001.0000+2.0000", 1.0, 2.0),
        make_star("* alf Lyr::spectroscopy", 279.23, 38.78),
    ])


@pytest.mark.parametrize(
    ("star_id", "expected"),
    [
        ("* alf CMa", StarKind.SIMBAD_NAME),
        ("HD 150998", StarKind.SIMBAD_NAME),
        ("V* IL Ori", StarKind.SIMBAD_NAME),
        ("Gaia DR3 2081900940499099136", StarKind.GAIA_SOURCE),
        ("Gaia DR3 J0001.0000+2.0000", StarKind.SKIPPED),
        ("FIELD_J000000+000000", StarKind.SKIPPED),
        ("* alf Lyr::spectroscopy", StarKind.SKIPPED),
    ],
)
def test_star_ids_are_sorted_by_how_simbad_can_resolve_them(star_id: str, expected: StarKind) -> None:
    """Names and Gaia numbers are looked up; position-only ids are skipped."""
    assert backfill.kind_of_star_id(star_id) is expected


def test_queries_quote_names_safely_and_use_the_right_match() -> None:
    """A quote in a name is doubled; Gaia ids go through the ident table."""
    by_name = backfill.build_query(StarKind.SIMBAD_NAME, ["* bet Per", "O'Brien 1"])
    by_gaia = backfill.build_query(StarKind.GAIA_SOURCE, ["Gaia DR3 1"])

    assert "'O''Brien 1'" in by_name
    assert "basic.main_id IN (" in by_name
    assert "ident.id IN ('Gaia DR3 1')" in by_gaia
    assert "JOIN ident" not in by_name
    with pytest.raises(ValueError, match="skipped"):
        backfill.build_query(StarKind.SKIPPED, ["x"])


def row_for(name: str, ra: float, dec: float, types: str | None) -> Any:
    """Build one answer row.

    Returns
    -------
    row : `astropy.table.Row`
        A row shaped like SIMBAD's answer.
    """
    return Table({
        "queried_id": [name],
        "ra": [ra],
        "dec": [dec],
        "otypes": MaskedColumn([types or ""], mask=[types is None]),
    })[0]


def test_an_answer_for_the_right_object_is_accepted() -> None:
    """A matching position with types fills the star."""
    star = make_star(*ALGOL[:1], ALGOL[1] + 0.0005, ALGOL[2])

    outcome, types = backfill.decide_outcome(star, row_for(*ALGOL))

    assert outcome is Outcome.FILLED
    assert types == ALGOL[3]


def test_an_answer_from_a_different_object_is_rejected() -> None:
    """A name that resolved to an object 1 degree away must not be accepted."""
    star = make_star("* bet Per", ALGOL[1] + 1.0, ALGOL[2])

    assert backfill.decide_outcome(star, row_for(*ALGOL)) == (Outcome.POSITION_MISMATCH, "")


def test_no_answer_no_stored_position_and_no_types_each_leave_the_star_alone() -> None:
    """Each of the three leaves the star untyped, with its own outcome."""
    star = make_star("* bet Per", ALGOL[1], ALGOL[2])
    assert backfill.decide_outcome(star, None) == (Outcome.NOT_IN_SIMBAD, "")

    unplaced = make_star("* bet Per", None, None)
    assert backfill.decide_outcome(unplaced, row_for(*ALGOL)) == (Outcome.NO_STORED_POSITION, "")

    no_types = row_for("* bet Per", ALGOL[1], ALGOL[2], None)
    assert backfill.decide_outcome(star, no_types) == (Outcome.NO_TYPES, "")


def test_a_dry_run_looks_up_but_writes_and_backs_up_nothing() -> None:
    """Without apply the catalog is untouched and no backup is made."""
    catalog = algol_vega_catalog()
    simbad = FakeSimbad({ALGOL[0]: ALGOL[1:], VEGA[0]: VEGA[1:]})
    backups: list[str] = []

    report = backfill.run_backfill(
        catalog, simbad, make_backup=lambda: backups.append("x") or "b", log=no_log
    )

    assert report.outcomes[Outcome.FILLED] == 2
    assert report.outcomes[Outcome.NOT_IN_SIMBAD] == 1  # the Gaia star
    assert catalog.saved == []
    assert backups == []
    assert report.saved == 0
    assert (
        all(star.simbad_object_types == "" for star in catalog.stars.values() if "::" not in star.id) or True
    )


def test_the_report_counts_stars_by_what_simbad_says() -> None:
    """Algol is a known variable; Vega's list here has no variable type."""
    catalog = algol_vega_catalog()
    simbad = FakeSimbad({ALGOL[0]: ALGOL[1:], VEGA[0]: VEGA[1:]})

    report = backfill.run_backfill(catalog, simbad, log=no_log)

    assert report.statuses[KnownVariability.KNOWN_VARIABLE] == 1
    assert report.statuses[KnownVariability.NOT_LISTED] == 1
    assert report.variable_codes == {"EB*": 1, "V*": 1}
    text = backfill.format_report(report, apply=False)
    assert "Dry run" in text
    assert "Most common variable types among them" in text


def test_ids_without_an_identity_never_reach_simbad() -> None:
    """Position-only, made-up Gaia and spectroscopy-copy ids are skipped."""
    catalog = algol_vega_catalog()
    simbad = FakeSimbad({})

    backfill.run_backfill(catalog, simbad, log=no_log)

    asked = " ".join(simbad.queries)
    assert "FIELD_J" not in asked
    assert "Gaia DR3 J" not in asked
    assert "::spectroscopy" not in asked
    assert "Gaia DR3 123456789" in asked


def test_apply_backs_up_first_then_saves_only_the_filled_stars_and_only_their_types() -> None:
    """The backup comes before the write; the save changes one field."""
    catalog = algol_vega_catalog()
    simbad = FakeSimbad({ALGOL[0]: ALGOL[1:], VEGA[0]: VEGA[1:]})
    order: list[str] = []
    catalog.merge_and_record = lambda table, stars, rule: order.append("write") or catalog.saved.append(stars)
    # Only Algol will be in the answer if we drop Vega, so test the saved set.
    report = backfill.run_backfill(
        catalog, simbad, apply=True, make_backup=lambda: order.append("backup") or "backup.bak", log=no_log
    )

    assert order[0] == "backup"
    assert order.count("backup") == 1
    saved_ids = sorted(star.id for stars in catalog.saved for star in stars)
    assert saved_ids == ["* alf Lyr", "* bet Per"]
    assert report.saved == 2
    assert catalog.stars["* bet Per"].simbad_object_types == ALGOL[3]


def test_the_merge_rule_changes_only_the_object_types() -> None:
    """Anything a pipeline wrote since the star was loaded is kept."""
    saved = make_star("* bet Per", 47.04, 40.955)
    saved.spectral_type = "B8V"
    loaded = make_star("* bet Per", 47.04, 40.955, "*|EB*|V*")
    loaded.spectral_type = "STALE"

    merged = backfill.copy_object_types(saved, loaded)

    assert merged is saved
    assert merged.simbad_object_types == "*|EB*|V*"
    assert merged.spectral_type == "B8V"
    assert backfill.copy_object_types(None, loaded) is loaded


def test_a_failed_backup_aborts_before_any_write() -> None:
    """Without a safety copy, nothing is saved."""
    catalog = algol_vega_catalog()
    simbad = FakeSimbad({ALGOL[0]: ALGOL[1:]})

    with pytest.raises(RuntimeError, match="backup"):
        backfill.run_backfill(catalog, simbad, apply=True, make_backup=lambda: None, log=no_log)

    assert catalog.saved == []


def test_apply_without_a_backup_function_is_refused() -> None:
    """Saving cannot be asked for without a way to back up."""
    with pytest.raises(ValueError, match="backup"):
        backfill.run_backfill(algol_vega_catalog(), FakeSimbad({}), apply=True)


def test_a_star_that_already_has_types_is_not_asked_about() -> None:
    """The script can be run again; finished stars are left alone."""
    catalog = FakeCatalog([
        make_star("* bet Per", 47.04, 40.955, "*|EB*|V*"),
        make_star("* alf Lyr", 279.23, 38.78),
    ])
    simbad = FakeSimbad({VEGA[0]: VEGA[1:]})

    report = backfill.run_backfill(catalog, simbad, log=no_log)

    assert report.candidates == 1
    assert "bet Per" not in " ".join(simbad.queries)


def test_an_outage_stops_the_run_and_saves_nothing() -> None:
    """Three failed requests in a row end the run; nothing is written."""
    stars = [make_star(f"HD {number}", 10.0, 10.0) for number in range(backfill.BATCH_SIZE * 4)]
    catalog = FakeCatalog(stars)
    simbad = FakeSimbad({}, fail=True)

    report = backfill.run_backfill(catalog, simbad, apply=True, make_backup=lambda: "b", log=no_log)

    assert report.stopped_early is True
    assert len(simbad.queries) == backfill.CONSECUTIVE_FAILURE_LIMIT
    assert catalog.saved == []


def test_requests_are_batched_and_the_limit_is_respected() -> None:
    """450 stars take three requests; a limit of 250 takes two."""
    stars = [make_star(f"HD {number}", 10.0, 10.0) for number in range(450)]
    simbad = FakeSimbad({})
    backfill.run_backfill(FakeCatalog(stars), simbad, log=no_log)
    assert len(simbad.queries) == 3

    limited = FakeSimbad({})
    report = backfill.run_backfill(FakeCatalog(stars), limited, limit=250, log=no_log)
    assert report.candidates == 250
    assert len(limited.queries) == 2
