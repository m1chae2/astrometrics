"""Tests for the script that fills in VSX and Gaia DR3 variability flags.

A fake cross-match service and a fake catalog stand in, so no network or
database is touched. They check what the script must get right: nothing is
written without ``--apply``, a backup comes before the first write, a finished
star is skipped, an outage stops the run, and only the two flag fields change.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from astropy.table import Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.known_variability import NO_GAIA_MATCH, NOT_IN_VSX
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.scripts import backfill_variable_star_flags as backfill


def make_star(
    star_id: str, ra: float | None = 10.0, dec: float | None = 20.0, **fields: Any
) -> StellarObject:
    """Build a stored star.

    Returns
    -------
    star : `StellarObject`
        A star with the given id, position and fields.
    """
    return StellarObject(id=star_id, name=star_id, ra=ra, dec=dec, spectralType="A0V", **fields)


class FakeXMatch:
    """A cross-match stand-in that answers from fixed tables by catalog."""

    def __init__(self, vsx: dict[str, str], gaia: dict[str, str], fail: bool = False) -> None:
        self.vsx = vsx
        self.gaia = gaia
        self.fail = fail
        self.calls: list[str] = []

    def __call__(self, **arguments: Any) -> Table | None:
        """Answer one cross-match request.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            Matches for the stars in the request that the fake knows.

        Raises
        ------
        ExternalServiceError
            When this fake was built to fail, like an unreachable service.
        """
        self.calls.append(arguments["cat2"])
        if self.fail:
            raise ExternalServiceError("The cross-match failed.")
        ids = list(arguments["cat1"]["id"])
        if arguments["cat2"].endswith("vsx"):
            known = [(i, t) for i, t in self.vsx.items() if i in ids]
            if not known:
                return None
            return Table({
                "id": [k[0] for k in known],
                "angDist": [0.1] * len(known),
                "Type": [k[1] for k in known],
            })
        known = [(i, f) for i, f in self.gaia.items() if i in ids]
        if not known:
            return None
        return Table({
            "id": [k[0] for k in known],
            "angDist": [0.1] * len(known),
            "VarFlag": [k[1] for k in known],
            "Gmag": [10.0] * len(known),
        })


class FakeCatalog:
    """A catalog stand-in that records what is saved."""

    def __init__(self, stars: list[StellarObject]) -> None:
        self.stars = {star.id: star for star in stars}
        self.saved: list[list[StellarObject]] = []
        self.with_photometry: set[str] = set()

    def list_star_summaries(self, target_id: str | None = None) -> list[SimpleNamespace]:
        """List the stars in short form.

        Returns
        -------
        summaries : `list`
            One entry with an ``id`` per star.
        """
        return [
            SimpleNamespace(id=star_id, has_photometry=star_id in self.with_photometry)
            for star_id in self.stars
        ]

    def get_by_ids(self, table: str, ids: list[str]) -> list[StellarObject]:
        """Load stars by id.

        Returns
        -------
        stars : `list` [`StellarObject`]
            The requested stars.
        """
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
        self.saved.append(list(stars))


def no_log(_message: str) -> None:
    """Swallow a progress line."""


def small_catalog() -> FakeCatalog:
    """Build a catalog with a variable, a constant star, a bare star and skips.

    Returns
    -------
    catalog : `FakeCatalog`
        The catalog.
    """
    return FakeCatalog([
        make_star("* bet Per"),
        make_star("* alf Lyr"),
        make_star("HD 1"),
        make_star("FIELD_J000000+000000"),
        make_star("HD 2", ra=None, dec=None),
        make_star("HD 3", vsxVariabilityType="CST", gaiaVariableFlag="CONSTANT"),
    ])


def fake_service() -> FakeXMatch:
    """Build a cross-match stand-in that knows Algol and Vega.

    Returns
    -------
    service : `FakeXMatch`
        Algol is a VSX variable and Gaia variable; Vega is VSX constant.
    """
    return FakeXMatch(
        {"* bet Per": "EA/SD", "* alf Lyr": "CST"}, {"* bet Per": "VARIABLE", "* alf Lyr": "CONSTANT"}
    )


def test_the_selection_skips_finished_unplaced_and_position_only_stars() -> None:
    """Only identifiable, placed stars missing an answer are looked up."""
    stars = small_catalog().stars

    chosen = [sid for sid, star in stars.items() if backfill.needs_lookup(star, include_position_only=False)]
    with_position_only = [sid for sid, star in stars.items() if backfill.needs_lookup(star, True)]

    assert chosen == ["* bet Per", "* alf Lyr", "HD 1"]
    assert with_position_only == ["* bet Per", "* alf Lyr", "HD 1", "FIELD_J000000+000000"]


def test_a_dry_run_writes_and_backs_up_nothing_and_reports_what_it_found() -> None:
    """Without apply nothing is saved; the report counts the answers."""
    catalog = small_catalog()
    backups: list[str] = []

    report = backfill.run_backfill(
        catalog, fake_service(), make_backup=lambda: backups.append("x") or "b", log=no_log
    )

    assert report.candidates == 3
    assert catalog.saved == []
    assert backups == []
    assert report.vsx_types == {"EA/SD": 1}
    assert report.gaia_flags == {"VARIABLE": 1, "CONSTANT": 1, NO_GAIA_MATCH: 1}
    assert report.confirmed_constant == 1  # Vega only
    assert "Dry run" in backfill.format_report(report, apply=False)


def test_apply_backs_up_first_and_saves_the_looked_up_stars() -> None:
    """The backup comes before the write, and the stars carry the answers."""
    catalog = small_catalog()
    order: list[str] = []
    original = catalog.merge_and_record
    catalog.merge_and_record = lambda *args: (order.append("write"), original(*args))[1]

    report = backfill.run_backfill(
        catalog, fake_service(), apply=True, make_backup=lambda: order.append("backup") or "b.bak", log=no_log
    )

    assert order == ["backup", "write"]
    assert report.saved == 3
    assert catalog.stars["* bet Per"].vsx_variability_type == "EA/SD"
    assert catalog.stars["* bet Per"].gaia_variable_flag == "VARIABLE"
    assert catalog.stars["HD 1"].vsx_variability_type == NOT_IN_VSX


def test_the_merge_rule_changes_only_the_two_flag_fields() -> None:
    """Anything a pipeline wrote since the star was loaded is kept."""
    saved = make_star("A")
    saved.spectral_type = "B8V"
    saved.simbad_object_types = "*|V*"
    loaded = make_star("A", vsxVariabilityType="CST", gaiaVariableFlag="CONSTANT")
    loaded.spectral_type = "STALE"

    merged = backfill.copy_flags(saved, loaded)

    assert merged is saved
    assert (merged.vsx_variability_type, merged.gaia_variable_flag) == ("CST", "CONSTANT")
    assert (merged.spectral_type, merged.simbad_object_types) == ("B8V", "*|V*")
    assert backfill.copy_flags(None, loaded) is loaded


def test_a_failed_backup_aborts_before_any_write() -> None:
    """Without a safety copy nothing is saved."""
    catalog = small_catalog()

    with pytest.raises(RuntimeError, match="backup"):
        backfill.run_backfill(catalog, fake_service(), apply=True, make_backup=lambda: None, log=no_log)

    assert catalog.saved == []


def test_apply_without_a_backup_function_is_refused() -> None:
    """Saving cannot be asked for without a way to back up."""
    with pytest.raises(ValueError, match="backup"):
        backfill.run_backfill(small_catalog(), fake_service(), apply=True)


def test_an_outage_stops_the_run_and_saves_nothing() -> None:
    """Three failed requests in a row end the run."""
    stars = [make_star(f"HD {number}") for number in range(backfill.BATCH_SIZE * 4)]
    catalog = FakeCatalog(stars)
    service = FakeXMatch({}, {}, fail=True)

    report = backfill.run_backfill(catalog, service, apply=True, make_backup=lambda: "b", log=no_log)

    assert report.stopped_early is True
    assert catalog.saved == []
    assert report.failed_requests == backfill.CONSECUTIVE_FAILURE_LIMIT


def test_the_limit_is_respected() -> None:
    """A limit of 2 looks up two stars."""
    report = backfill.run_backfill(small_catalog(), fake_service(), limit=2, log=no_log)

    assert report.candidates == 2


def test_stars_are_looked_up_in_batches() -> None:
    """Twice the batch size takes two requests per catalog."""
    stars = [make_star(f"HD {number}") for number in range(backfill.BATCH_SIZE * 2)]
    service = FakeXMatch({}, {})

    backfill.run_backfill(FakeCatalog(stars), service, log=no_log)

    assert len(service.calls) == 4


def test_only_stars_with_a_light_curve_can_be_chosen() -> None:
    """With the photometry option, only stars with light curves are used."""
    catalog = small_catalog()
    catalog.with_photometry = {"FIELD_J000000+000000", "HD 1"}

    report = backfill.run_backfill(
        catalog, fake_service(), include_position_only=True, only_with_photometry=True, log=no_log
    )

    assert report.candidates == 2
