"""Purpose: Unit tests for the observation package library calls.

Description: Verifies the package status and its old-data default, the
`packages.query` filters, `packages.update` (revision conflicts and the
reopen), `packages.delete`, the one-session-per-night rule (including the
pick among old duplicates), packages with no exposures, and the promotion of
a suggested package when it is added to a queue. Uses a temporary database
and stand-in targets, never real data.
"""

import pytest

from astrometricslib import AppConfiguration
from astrometricslib.exceptions import ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.api.test.test_planning import _configuration, _package, _planning, _rows, _target
from wayfindinglib.drivers.butler import DiskButler, pick_session_for_night
from wayfindinglib.models.planning.observation_package import (
    ObservationPackage,
    PackageStatus,
    PackageSuggestion,
)
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    PlanRevisionConflictError,
    QueueRequest,
)


@pytest.fixture
def isolated_butler(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by a fully isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def _planning_with_targets(butler: DiskButler) -> ObservationPlanning:
    """Build a planning API with two library targets.

    Parameters
    ----------
    butler : `DiskButler`
        The isolated storage.

    Returns
    -------
    planning : `ObservationPlanning`
        The API under test.
    """
    return _planning(butler, _target("M 81"), _target("M 13", ra="16:41:41", dec="+36:27:35"))


def _night_session(planning: ObservationPlanning, night: str = "2026-08-10") -> ObservationSession:
    """Make (or find) the session for a night.

    Parameters
    ----------
    planning : `ObservationPlanning`
        The API under test.
    night : `str`, optional
        The night id.

    Returns
    -------
    session : `ObservationSession`
        The night's session.
    """
    return planning.sessions.create(night, _configuration())


def test_a_new_package_is_planned_at_revision_zero(isolated_butler: DiskButler) -> None:
    """A new package is PLANNED at revision 0."""
    package = _package(_planning_with_targets(isolated_butler))
    assert package.status == PackageStatus.PLANNED
    assert package.revision == 0
    assert package.suggestion is None
    assert package.framing is None


def test_a_package_stored_before_status_existed_loads_as_planned() -> None:
    """Old stored JSON without the new fields loads as PLANNED."""
    old_json = (
        '{"id": "p1", "name": "M 81", "target_id": "M 81", "exposure_requests": [],'
        ' "priority": 0, "notes": "", "created_at": "2026-01-01T00:00:00Z"}'
    )
    package = ObservationPackage.model_validate_json(old_json)
    assert package.status == PackageStatus.PLANNED
    assert package.revision == 0
    assert package.suggestion is None


def test_a_suggestion_keeps_its_source_fields() -> None:
    """The suggestion model keeps its source fields."""
    suggestion = PackageSuggestion(
        source_kind="rule", rule_name="more_frames", source_key="M 81|L", reason="r"
    )
    assert suggestion.source_kind == "rule"
    assert suggestion.created_at.tzinfo is not None


def _library_target(planning: ObservationPlanning, target_id: str = "M 81") -> object:
    """Read a library target from the stand-in catalog.

    Parameters
    ----------
    planning : `ObservationPlanning`
        The API under test.
    target_id : `str`, optional
        The target.

    Returns
    -------
    target : `Target`
        The target.
    """
    return planning._context.astrometrics.targets.get(target_id, required=True)


def test_a_package_can_have_no_exposures(isolated_butler: DiskButler) -> None:
    """An empty package is recorded for a library target."""
    planning = _planning_with_targets(isolated_butler)
    package = planning.packages.create(_library_target(planning))
    assert package.exposure_requests == []
    assert isolated_butler.get("observation_package", {"id": package.id}) is not None


def test_packages_list_newest_first_and_filters(isolated_butler: DiskButler) -> None:
    """The package list filters and puts the newest first."""
    planning = _planning_with_targets(isolated_butler)
    first = _package(planning, "M 81")
    second = _package(planning, "M 13")
    third = _package(planning, "M 81")
    third.status = PackageStatus.COMPLETE
    isolated_butler.put(third, "observation_package", {"id": third.id})

    everything = planning.packages.query()
    assert {package.id for package in everything} == {first.id, second.id, third.id}
    assert [package.created_at for package in everything] == sorted(
        (package.created_at for package in everything), reverse=True
    )
    assert [package.id for package in planning.packages.query(status="COMPLETE")] == [third.id]
    m_81 = _target("m_81")
    assert {package.id for package in planning.packages.query(target=m_81)} == {first.id, third.id}
    assert planning.packages.query(limit=1).next_offset == 1
    assert planning.packages.query(dismissed=True).total == 0
    with pytest.raises(InvalidArgumentError):
        planning.packages.query(status="bogus")


def test_update_changes_values_and_bumps_the_revision(isolated_butler: DiskButler) -> None:
    """An edit stores new values and adds one to the revision."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    edited = planning.packages.update(
        package, exposure_requests=_rows(exposure_s=30.0, count=5), priority=2, name="Wide M 81"
    )
    assert edited.revision == 1
    assert edited.priority == 2
    stored = planning.packages.get(package.id)
    assert stored.name == "Wide M 81"
    assert stored.exposure_requests[0].count == 5
    with pytest.raises(InvalidArgumentError):
        planning.packages.update(edited, minimum_altitude_deg=200.0)
    with pytest.raises(InvalidArgumentError):
        planning.packages.update(edited)
    with pytest.raises(InvalidArgumentError, match="Give one or more"):
        planning.packages.update(edited, colour="red")
    with pytest.raises(InvalidArgumentError, match="PLANNED"):
        planning.packages.update(edited, status="COMPLETE")


def test_update_refuses_a_stale_revision(isolated_butler: DiskButler) -> None:
    """A stale revision is refused and names the current one."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    planning.packages.update(package, notes="first")
    with pytest.raises(PlanRevisionConflictError) as caught:
        planning.packages.update(package, notes="second")
    assert caught.value.details["current_revision"] == 1
    assert planning.packages.get(package.id).notes == "first"


def test_reopen_sets_a_complete_package_back_to_planned(isolated_butler: DiskButler) -> None:
    """A reopen works on COMPLETE only, and may come with other changes."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    with pytest.raises(ConflictError):
        planning.packages.update(package, status="PLANNED")
    package.status = PackageStatus.COMPLETE
    isolated_butler.put(package, "observation_package", {"id": package.id})
    reopened = planning.packages.update(package, status=PackageStatus.PLANNED, notes="again")
    assert reopened.status == PackageStatus.PLANNED
    assert reopened.notes == "again"
    assert reopened.revision == 1


def test_delete_removes_the_package_and_refuses_a_stale_one(isolated_butler: DiskButler) -> None:
    """Delete removes a package; a stale or missing one is refused."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    with pytest.raises(PlanRevisionConflictError):
        planning.packages.delete(package.model_copy(update={"revision": 5}))
    assert planning.packages.delete(package) is None
    assert planning.packages.query().total == 0
    with pytest.raises(NotFoundError):
        planning.packages.delete(package)


def test_one_session_per_night(isolated_butler: DiskButler) -> None:
    """A second empty session for a night returns the first."""
    planning = _planning_with_targets(isolated_butler)
    first = _night_session(planning)
    again = _night_session(planning)
    other_night = _night_session(planning, "2026-08-11")
    assert again.id == first.id
    assert other_night.id != first.id
    assert planning.sessions.query().total == 2
    assert planning.sessions.get("2026-08-10").id == first.id
    assert planning.sessions.get("2026-01-01") is None


def test_the_insert_check_and_write_are_one_step(isolated_butler: DiskButler) -> None:
    """The insert returns the stored session for a night."""
    planning = _planning_with_targets(isolated_butler)
    first = _night_session(planning)
    rival = first.model_copy(update={"id": "rival"})
    assert isolated_butler.insert_session_for_night(rival).id == first.id
    assert isolated_butler.get("observation_session", {"session_id": "rival"}) is None


def test_old_duplicates_pick_the_most_entries_then_the_newest(isolated_butler: DiskButler) -> None:
    """Old duplicates: most entries win, then the newest."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    base = _night_session(planning)
    older = base.model_copy(update={"id": "older"})
    newer = base.model_copy(update={"id": "newer", "created_at": base.created_at.replace(year=2099)})
    fuller = base.model_copy(update={"id": "fuller", "created_at": base.created_at.replace(year=2001)})
    filled = planning.sessions.add(base, [package])
    planning.sessions.remove(filled, [filled.queue[0]])
    for extra in (older, newer, fuller):
        isolated_butler.put(extra, "observation_session", {"session_id": extra.id})
    fuller_stored = planning.sessions.add(fuller, [package])
    assert fuller_stored.id == "fuller"
    assert planning.sessions.get("2026-08-10").id == "fuller"
    assert _night_session(planning).id == "fuller"
    assert pick_session_for_night([older, newer]).id == "newer"
    assert pick_session_for_night([]) is None


def test_a_scheduled_session_replaces_the_placement_in_the_nights_session(
    isolated_butler: DiskButler,
) -> None:
    """Scheduling a night re-places packages in its session."""
    planning = _planning_with_targets(isolated_butler)
    existing = _night_session(planning)
    package = _package(planning)
    scheduled = planning.sessions.create(
        "2026-08-10", _configuration(), packages=[QueueRequest(package_id=package.id)]
    )
    assert scheduled.id == existing.id
    assert scheduled.revision == existing.revision + 1
    assert planning.sessions.query().total == 1
    assert len(planning.sessions.get("2026-08-10").queue) == 1


def test_adding_a_suggested_package_to_a_queue_promotes_it(isolated_butler: DiskButler) -> None:
    """A suggested package becomes planned when queued."""
    planning = _planning_with_targets(isolated_butler)
    session = _night_session(planning)
    package = planning.packages.create(_library_target(planning), _rows(count=2))
    package.status = PackageStatus.SUGGESTED
    isolated_butler.put(package, "observation_package", {"id": package.id})

    planning.sessions.add(session, [package])

    stored = planning.packages.get(package.id)
    assert stored.status == PackageStatus.PLANNED
    assert stored.revision == 1
