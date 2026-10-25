"""Purpose: Unit tests for the observation package library calls.

Description: Verifies the package status and its old-data default, the
package list filters, `edit_package` (revision conflicts, reopen, delete),
the one-session-per-night rule (including the pick among old duplicates),
packages with no exposures, and the promotion of a suggested package when
it is added to a queue. Uses a temporary database and stand-in targets,
never real data.
"""

import pytest

from astrometricslib import AppConfiguration, ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.api.planning import ObservationPlanning
from wayfindinglib.api.test.test_planning import _package, _planning, _site, _target, _telescope
from wayfindinglib.drivers.butler import DiskButler, pick_session_for_night
from wayfindinglib.models.planning.observation_package import (
    ExposureRequest,
    FrameType,
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
    return planning.create_plan(
        "empty_session", site_profile=_site(), telescope=_telescope(), camera_id="c1", night_id=night
    )


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


def test_a_package_can_have_no_exposures(isolated_butler: DiskButler) -> None:
    """An empty package is recorded for a library target."""
    planning = _planning_with_targets(isolated_butler)
    package = planning.create_plan("package", target="M 81")
    assert package.exposure_requests == []
    assert isolated_butler.get("observation_package", {"id": package.id}) is not None


def test_a_package_for_a_target_not_in_the_library_says_to_add_it(isolated_butler: DiskButler) -> None:
    """A target missing from the library is refused clearly."""
    planning = _planning_with_targets(isolated_butler)
    with pytest.raises(NotFoundError, match="Add the target first"):
        planning.create_plan("package", target="NGC 9999")


def test_packages_list_newest_first_and_filters(isolated_butler: DiskButler) -> None:
    """The package list filters and puts the newest first."""
    planning = _planning_with_targets(isolated_butler)
    first = _package(planning, "M 81")
    second = _package(planning, "M 13")
    third = _package(planning, "M 81")
    third.status = PackageStatus.COMPLETE
    isolated_butler.put(third, "observation_package", {"id": third.id})

    everything = planning.get_plan(kind="packages")
    assert {package.id for package in everything} == {first.id, second.id, third.id}
    assert [package.created_at for package in everything] == sorted(
        (package.created_at for package in everything), reverse=True
    )
    assert [package.id for package in planning.get_plan(kind="packages", status="COMPLETE")] == [third.id]
    assert {package.id for package in planning.get_plan(kind="packages", target="m_81")} == {
        first.id,
        third.id,
    }
    with pytest.raises(InvalidArgumentError):
        planning.get_plan(kind="packages", status="bogus")
    with pytest.raises(InvalidArgumentError):
        planning.get_plan(kind="packages", night_id="2026-08-10")
    with pytest.raises(InvalidArgumentError):
        planning.get_plan(status="PLANNED")


def test_edit_package_changes_values_and_bumps_the_revision(isolated_butler: DiskButler) -> None:
    """An edit stores new values and adds one to the revision."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    edited = planning.edit_package(
        package.id,
        exposure_requests=[{"frame_type": "LIGHT", "exposure_sec": 30.0, "count": 5}],
        priority=2,
        name="Wide M 81",
        expected_revision=0,
    )
    assert edited.revision == 1
    assert edited.priority == 2
    stored = planning.get_plan(kind="packages")[0]
    assert stored.name == "Wide M 81"
    assert stored.exposure_requests[0].count == 5
    with pytest.raises(InvalidArgumentError):
        planning.edit_package(package.id, minimum_altitude_deg=200.0)
    with pytest.raises(InvalidArgumentError):
        planning.edit_package(package.id)


def test_edit_package_refuses_a_stale_revision(isolated_butler: DiskButler) -> None:
    """A stale revision is refused and names the current one."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    planning.edit_package(package.id, notes="first", expected_revision=0)
    with pytest.raises(PlanRevisionConflictError) as caught:
        planning.edit_package(package.id, notes="second", expected_revision=0)
    assert caught.value.details["current_revision"] == 1
    assert planning.get_plan(kind="packages")[0].notes == "first"


def test_reopen_sets_a_complete_package_back_to_planned(isolated_butler: DiskButler) -> None:
    """Reopen works on COMPLETE only."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    with pytest.raises(ConflictError):
        planning.edit_package(package.id, reopen=True)
    package.status = PackageStatus.COMPLETE
    isolated_butler.put(package, "observation_package", {"id": package.id})
    reopened = planning.edit_package(package.id, reopen=True)
    assert reopened.status == PackageStatus.PLANNED
    assert reopened.revision == 1


def test_delete_removes_the_package_and_refuses_mixed_arguments(isolated_butler: DiskButler) -> None:
    """Delete removes a package; mixed or missing is refused."""
    planning = _planning_with_targets(isolated_butler)
    package = _package(planning)
    with pytest.raises(InvalidArgumentError):
        planning.edit_package(package.id, delete=True, priority=1)
    with pytest.raises(PlanRevisionConflictError):
        planning.edit_package(package.id, delete=True, expected_revision=5)
    planning.edit_package(package.id, delete=True)
    assert planning.get_plan(kind="packages") == []
    with pytest.raises(NotFoundError):
        planning.edit_package(package.id, delete=True)


def test_one_session_per_night(isolated_butler: DiskButler) -> None:
    """A second empty session for a night returns the first."""
    planning = _planning_with_targets(isolated_butler)
    first = _night_session(planning)
    again = _night_session(planning)
    other_night = _night_session(planning, "2026-08-11")
    assert again.id == first.id
    assert other_night.id != first.id
    assert len(planning.get_plan()) == 2
    assert planning.get_plan(night_id="2026-08-10").id == first.id
    assert planning.get_plan(night_id="2026-01-01") is None
    with pytest.raises(InvalidArgumentError):
        planning.get_plan(session_id=first.id, night_id="2026-08-10")


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
    planning.edit_queue(base.id, add=[{"package_id": package.id}])
    planning.edit_queue(base.id, remove=[planning.get_plan(base.id).queue[0].id])
    for extra in (older, newer, fuller):
        isolated_butler.put(extra, "observation_session", {"session_id": extra.id})
    fuller_stored = planning.edit_queue("fuller", add=[{"package_id": package.id}]).session
    assert fuller_stored.id == "fuller"
    assert planning.get_plan(night_id="2026-08-10").id == "fuller"
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
    scheduled = planning.create_plan(
        "scheduled_session",
        requests=[QueueRequest(package_id=package.id)],
        site_profile=_site(),
        telescope=_telescope(),
        camera_id="c1",
        night_id="2026-08-10",
    )
    assert scheduled.id == existing.id
    assert scheduled.revision == existing.revision + 1
    assert len(planning.get_plan()) == 1
    assert len(planning.get_plan(existing.id).queue) == 1


def test_adding_a_suggested_package_to_a_queue_promotes_it(isolated_butler: DiskButler) -> None:
    """A suggested package becomes planned when queued."""
    planning = _planning_with_targets(isolated_butler)
    session = _night_session(planning)
    package = planning.create_plan(
        "package",
        target="M 81",
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=60.0, count=2)],
    )
    package.status = PackageStatus.SUGGESTED
    isolated_butler.put(package, "observation_package", {"id": package.id})

    change = planning.edit_queue(session.id, add=[{"package_id": package.id}])

    assert any(item.kind == "package_promoted" for item in change.changes)
    stored = planning.get_plan(kind="packages")[0]
    assert stored.status == PackageStatus.PLANNED
    assert stored.revision == 1
