"""Tests for the script that fills in the SIMBAD object type of saved targets.

A fake SIMBAD driver and a fake catalog stand in, so no network or database is
touched. They check that nothing is written without ``--apply``, a backup comes
before the first write, a wrong-object answer is rejected, the nearest of two
objects sharing a name is chosen, moving bodies and typed targets are skipped,
an outage stops the run, and only the type field is changed.
"""

import re
from typing import Any

import pytest
from astropy.table import Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.target import Target
from astrometricslib.scripts import backfill_target_object_types as backfill
from astrometricslib.scripts.backfill_target_object_types import Outcome

M13 = ("M 13", "16 41 41.24", "+36 27 35.5", 250.4218, 36.4599, "GlC")
FIRST_ANSWER_RA = 250.4218


class FakeSimbad:
    """A SIMBAD stand-in answering TAP queries from a list of rows."""

    def __init__(self, rows: list[tuple[str, float, float, str | None]], fail: bool = False) -> None:
        self.rows = rows
        self.fail = fail
        self.queries: list[str] = []

    def query_tap(self, adql_query: str) -> Table | None:
        """Answer with the rows whose name is in the query's IN list.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            The matching rows.

        Raises
        ------
        ExternalServiceError
            When built to fail.
        """
        self.queries.append(adql_query)
        if self.fail:
            raise ExternalServiceError("down")
        asked = set(re.findall(r"'((?:[^']|'')*)'", adql_query.split(" IN (")[1]))
        found = [row for row in self.rows if row[0] in asked]
        if not found:
            return None
        return Table({
            "queried_id": [row[0] for row in found],
            "main_id": [row[0] for row in found],
            "ra": [row[1] for row in found],
            "dec": [row[2] for row in found],
            "otype": [row[3] or "" for row in found],
        })


class FakeCatalog:
    """A catalog stand-in holding targets and recording writes."""

    def __init__(self, targets: list[Target]) -> None:
        self.targets = targets
        self.saved: list[Target] = []

    def get(self, dataset_type: str, selector: dict) -> list[Target]:
        """Return the targets.

        Returns
        -------
        targets : `list` [`Target`]
            All targets.
        """
        return self.targets

    def merge_and_record(self, dataset_type: str, objects: list[Target], merge: Any) -> None:
        """Record a save, applying the merge rule to a stored copy."""
        assert dataset_type == "target_catalog"
        for updated in objects:
            self.saved.append(merge(Target(id=updated.id, ra=updated.ra, dec=updated.dec), updated))


def make_target(target_id: str, ra: str = "16 41 41.24", dec: str = "+36 27 35.5", **fields: Any) -> Target:
    """Build a stored target.

    Returns
    -------
    target : `Target`
        A target with the given id and position.
    """
    return Target(id=target_id, ra=ra, dec=dec, **fields)


def run(catalog: FakeCatalog, simbad: FakeSimbad, apply: bool = False, backups: list | None = None) -> Any:
    """Run the backfill quietly.

    Returns
    -------
    report : `BackfillReport`
        The run's report.
    """
    backups = backups if backups is not None else []
    return backfill.run_backfill(
        catalog,
        simbad,
        apply=apply,
        make_backup=lambda: backups.append("backup") or "/tmp/backup.bak",
        log=lambda _line: None,
    )


def test_a_dry_run_writes_nothing() -> None:
    """Without apply the report is filled but nothing is saved or backed up."""
    catalog = FakeCatalog([make_target("M 13")])
    backups: list = []
    report = run(catalog, FakeSimbad([("M 13", FIRST_ANSWER_RA, 36.4599, "GlC")]), backups=backups)
    assert report.outcomes[Outcome.FILLED] == 1
    assert report.decisions == [("M 13", Outcome.FILLED, "GlC", "cluster")]
    assert catalog.saved == [] and backups == []


def test_apply_backs_up_then_saves_only_the_type() -> None:
    """The backup comes first and the merge changes only the type."""
    catalog = FakeCatalog([make_target("M 13")])
    backups: list = []
    report = run(
        catalog, FakeSimbad([("M 13", FIRST_ANSWER_RA, 36.4599, "GlC")]), apply=True, backups=backups
    )
    assert backups == ["backup"] and report.saved == 1
    assert catalog.saved[0].simbad_object_type == "GlC"


def test_an_answer_from_another_place_is_rejected() -> None:
    """A row more than 15 arcminutes from the stored position is not used."""
    catalog = FakeCatalog([make_target("M 13")])
    report = run(catalog, FakeSimbad([("M 13", 10.0, 10.0, "GlC")]))
    assert report.outcomes[Outcome.POSITION_MISMATCH] == 1
    assert report.saved == 0


def test_a_framed_extended_object_a_few_arcminutes_off_is_accepted() -> None:
    """A stored position a few arcminutes off SIMBAD's center matches."""
    catalog = FakeCatalog([make_target("M 13")])
    report = run(catalog, FakeSimbad([("M 13", FIRST_ANSWER_RA + 0.05, 36.4599, "GlC")]))
    assert report.outcomes[Outcome.FILLED] == 1


def test_the_nearest_of_two_objects_sharing_a_name_is_used() -> None:
    """A name two objects carry gives the type of the nearer one."""
    catalog = FakeCatalog([make_target("NGC 6888", "20 12 06.54", "+38 21 17.78")])
    star = ("NGC 6888", 303.0272, 38.3549, "WR*")
    nebula = ("NGC 6888", 303.0, 38.35, "HII")
    report = run(catalog, FakeSimbad([nebula, star]))
    assert report.decisions[0][2:] == ("WR*", "star")


def test_a_target_without_a_position_is_filled_and_marked_unchecked() -> None:
    """An exact name match with nothing to check against is kept."""
    catalog = FakeCatalog([make_target("Vega", "0h 0m 0s", "0° 0′ 0′′")])
    report = run(catalog, FakeSimbad([("NAME Vega", 279.2, 38.8, "dS*")]))
    assert report.decisions == [("Vega", Outcome.FILLED_UNCHECKED, "dS*", "star")]


def test_the_first_designation_of_a_two_object_id_is_tried() -> None:
    """The id M 52 - Bubble Nebula is looked up as M 52."""
    catalog = FakeCatalog([make_target("M 52 - Bubble Nebula", "0h 0m 0s", "0° 0′ 0′′")])
    report = run(catalog, FakeSimbad([("M 52", 351.2, 61.6, "OpC")]))
    assert report.decisions[0][2:] == ("OpC", "cluster")


def test_moving_bodies_and_typed_targets_are_skipped() -> None:
    """The Moon, a comet and a target with a type are not looked up."""
    catalog = FakeCatalog([make_target("Moon"), make_target("M 31", simbad_object_type="G")])
    simbad = FakeSimbad([])
    report = run(catalog, simbad)
    assert report.candidates == 0 and simbad.queries == []


def test_a_name_simbad_lacks_is_reported() -> None:
    """A name SIMBAD does not know ends as not in SIMBAD."""
    report = run(FakeCatalog([make_target("Nowhere")]), FakeSimbad([]))
    assert report.outcomes[Outcome.NOT_IN_SIMBAD] == 1


def test_an_outage_stops_the_run() -> None:
    """Three failed requests in a row stop the run without writing."""
    targets = [make_target(f"NGC {number}") for number in range(1, 20 * 3 + 1)]
    catalog = FakeCatalog(targets)
    report = run(catalog, FakeSimbad([], fail=True), apply=True)
    assert report.stopped_early and report.failed_requests == 3 and catalog.saved == []


def test_apply_needs_a_backup_function() -> None:
    """Saving without a backup function is refused."""
    with pytest.raises(ValueError, match="backup"):
        backfill.run_backfill(FakeCatalog([]), FakeSimbad([]), apply=True)


def test_the_report_lists_each_target() -> None:
    """The text report has one line per target and says it was a dry run."""
    report = run(FakeCatalog([make_target("M 13")]), FakeSimbad([("M 13", FIRST_ANSWER_RA, 36.4599, "GlC")]))
    text = backfill.format_report(report, apply=False)
    assert "M 13" in text and "cluster" in text and "Dry run" in text
