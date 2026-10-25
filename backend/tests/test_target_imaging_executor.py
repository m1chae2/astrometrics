"""Purpose: Unit tests for the backend's thin sequencer service.

Description: The sequencer's queue is an observing session's queue in
the wayfinding library. These tests use a real `ObservationPlanning` over
a temporary store and a stand-in execution, and check the RPC methods
keep the shape the app shows: added sequences appear as queued items,
removing, reordering and changing work on the session's queue, and
starting runs `advance_session` on a thread.
"""

import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest

from astrometricslib import AppConfiguration, Target
from backend.services.observatory.plan_service import PlanService
from backend.services.observatory.target_imaging_executor import TargetImagingExecutor
from wayfindinglib import ObservationPackage, ObservationPlanning, Telescope


@pytest.fixture
def executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TargetImagingExecutor:
    """Build a sequencer over a temporary store, one target and a rig.

    Returns
    -------
    executor : `TargetImagingExecutor`
        The service under test.
    """
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: tmp_path / "config.toml")
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    targets = {"M 81": Target(id="M 81", ra="09:55:33", dec="+69:03:55")}
    astrometrics = SimpleNamespace(
        targets=SimpleNamespace(get=lambda target_id, refresh=False: targets.get(target_id)),
        quality=SimpleNamespace(capture_settings=lambda ids=None: {}),
    )
    telescope = Telescope(id="t1", name="Scope", focal_length_mm=450.0, focal_ratio=6.0)
    catalog = SimpleNamespace(
        active_telescope=lambda: telescope, active_camera=lambda: SimpleNamespace(id="c1")
    )
    monkeypatch.setattr(
        "wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog", lambda config: catalog
    )
    planning = ObservationPlanning(config, astrometrics=astrometrics)
    runs: list[Any] = []

    def advance_session(session_id: str, stop: threading.Event) -> None:
        """Record the run.

        Parameters
        ----------
        session_id : `str`
            The session.
        stop : `threading.Event`
            The stop signal.
        """
        runs.append(session_id)

    service = TargetImagingExecutor(
        wayfinder=SimpleNamespace(
            planning=planning, execution=SimpleNamespace(advance_session=advance_session)
        )
    )
    service.runs = runs
    return service


def _sequence(count: int = 3) -> dict[str, Any]:
    """Build a sequence as the app sends it.

    Returns
    -------
    sequence : `dict` [`str`, `Any`]
        One target with one item.
    """
    return {
        "target_name": "M 81",
        "items": [{"count": count, "exposure": 60.0, "filter": "Ha", "duration": 60.0 * count}],
    }


def test_added_sequences_show_as_queued_items(executor: TargetImagingExecutor) -> None:
    """A sequence comes back with its items, total and timing."""
    assert executor.get_queue() == []
    assert executor.enqueue_sequence(_sequence(), {"mode": "soonest"}) == {"status": "queued"}

    (item,) = executor.get_queue()
    assert item["target_name"] == "M 81"
    assert item["items"] == [{"count": 3, "exposure": 60.0, "filter": "Ha", "duration": 180.0}]
    assert item["total_duration"] == pytest.approx(180.0)
    assert item["timing"] == {"mode": "soonest"}
    assert item["status"] == "queued"


def test_remove_reorder_and_modify_edit_the_queue(executor: TargetImagingExecutor) -> None:
    """The queue can be cut, reordered and changed in place."""
    executor.enqueue_sequence(_sequence(1), {"mode": "soonest"})
    executor.enqueue_sequence(_sequence(2), {"mode": "at", "time": "2030-01-01T22:00:00Z"})
    first, second = (item["id"] for item in executor.get_queue())

    assert executor.reorder([second]) is True
    assert [item["id"] for item in executor.get_queue()] == [second, first]
    assert executor.get_queue()[0]["timing"]["mode"] == "at"

    assert executor.modify_queue_item(second, _sequence(5)) is True
    changed = executor.get_queue()[0]
    assert changed["items"][0]["count"] == 5
    assert changed["timing"]["mode"] == "at"

    assert executor.remove_from_queue(first) is True
    assert executor.remove_from_queue(first) is False
    assert len(executor.get_queue()) == 1


def test_begin_runs_the_session_queue_on_a_thread(executor: TargetImagingExecutor) -> None:
    """Starting the sequencer runs advance_session for its session."""
    executor.begin_imaging()
    assert executor.runs == []

    executor.enqueue_sequence(_sequence(), {"mode": "soonest"})
    executor.begin_imaging()
    executor._thread.join(timeout=5.0)

    assert executor.runs == [executor._session().id]


def test_every_queue_edit_announces_plan_changed(executor: TargetImagingExecutor) -> None:
    """Add, reorder, modify and remove each send the plan:changed event."""
    from types import SimpleNamespace as _Namespace

    from backend.services.observatory.plan_service import PlanService

    events: list[tuple[str, dict[str, Any]]] = []
    sockets = _Namespace(
        broadcast_ui_event_sync=lambda action, payload=None: (
            events.append((action, payload or {})) if action == "plan:changed" else None
        )
    )
    executor._plan_service = PlanService(executor._wayfinder, sockets)

    executor.enqueue_sequence(_sequence(1), {"mode": "soonest"})
    executor.enqueue_sequence(_sequence(2), {"mode": "soonest"})
    first, second = (item["id"] for item in executor.get_queue())
    assert [name for name, _ in events] == ["plan:changed", "plan:changed"]
    assert events[0][1]["changed"] == ["queue_added"]
    events.clear()

    executor.reorder([second])
    assert [payload["changed"] for _, payload in events] == [["queue_reordered"]]
    events.clear()

    executor.modify_queue_item(second, _sequence(5))
    assert [name for name, _ in events] == ["plan:changed", "plan:changed"]
    events.clear()

    executor.remove_from_queue(first)
    assert [payload["changed"] for _, payload in events] == [["queue_removed"]]
    revisions = {payload["revision"] for _, payload in events}
    assert revisions and all(isinstance(revision, int) for revision in revisions)


def test_adding_a_sequence_announces_the_package(executor: TargetImagingExecutor) -> None:
    """Add to Sequence records one package and announces it."""
    events: list[tuple[str, dict[str, Any]]] = []
    sockets = SimpleNamespace(
        broadcast_ui_event_sync=lambda action, payload=None: events.append((action, payload or {}))
    )
    executor._plan_service = PlanService(executor._wayfinder, sockets)

    executor.enqueue_sequence(_sequence(), {"mode": "soonest"})

    package_events = [payload for name, payload in events if name == "packages:changed"]
    assert len(package_events) == 1
    assert package_events[0]["kind"] == "created"
    assert package_events[0]["targetId"] == "M 81"
    stored = executor._wayfinder.planning.get_plan(kind="packages")
    assert [package.id for package in stored] == [package_events[0]["packageId"]]
    assert isinstance(stored[0], ObservationPackage)


def test_the_sequencer_uses_tonights_one_session_and_follows_the_night(
    executor: TargetImagingExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same night reuses its session; a new night gets its own."""
    night = {"id": "2026-10-24"}
    monkeypatch.setattr(
        "backend.services.observatory.target_imaging_executor.observing_night_id",
        lambda timestamp: night["id"],
    )
    planning = executor._wayfinder.planning

    executor.enqueue_sequence(_sequence(1), {"mode": "soonest"})
    executor.enqueue_sequence(_sequence(2), {"mode": "soonest"})
    assert len(planning.get_plan()) == 1
    assert len(executor.get_queue()) == 2

    # A restart (a fresh executor over the same library) adds no session.
    restarted = TargetImagingExecutor(wayfinder=executor._wayfinder)
    assert len(restarted.get_queue()) == 2
    assert len(planning.get_plan()) == 1

    night["id"] = "2026-10-25"
    assert executor.get_queue() == []
    executor.enqueue_sequence(_sequence(3), {"mode": "soonest"})
    assert len(planning.get_plan()) == 2
    assert [item["items"][0]["count"] for item in executor.get_queue()] == [3]


def test_reading_the_queue_never_creates_a_session(tmp_path: Path) -> None:
    """With no session tonight, the queue is empty and nothing is created."""
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.get_plan.return_value = None
    service = TargetImagingExecutor(wayfinder=SimpleNamespace(planning=planning, execution=None))

    assert service.get_queue() == []
    assert service.remove_from_queue("entry") is False
    assert service.reorder(["entry"]) is False
    assert service.modify_queue_item("entry", _sequence()) is False
    planning.create_plan.assert_not_called()
    assert planning.get_plan.call_args.kwargs["night_id"]


def test_the_session_is_asked_for_by_night_each_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every call asks the library for the night's session."""
    planning = create_autospec(ObservationPlanning, instance=True)
    nights = iter(["2026-10-24", "2026-10-25"])
    monkeypatch.setattr(
        "backend.services.observatory.target_imaging_executor.observing_night_id",
        lambda timestamp: next(nights),
    )
    service = TargetImagingExecutor(wayfinder=SimpleNamespace(planning=planning, execution=None))
    planning.create_plan.side_effect = lambda kind, **arguments: SimpleNamespace(id=arguments["night_id"])

    assert service._session().id == "2026-10-24"
    assert service._session().id == "2026-10-25"
    assert [call.kwargs["night_id"] for call in planning.create_plan.call_args_list] == [
        "2026-10-24",
        "2026-10-25",
    ]
