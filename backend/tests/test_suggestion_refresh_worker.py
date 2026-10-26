"""Purpose: Test the background worker that refreshes package suggestions.

Description: The worker takes triggers from the measurement announcer, the
frame sync and the `plan:refresh_suggestions` method. These tests use an
autospec of the real `ObservationPlanning` so a wrong argument fails. They
check that many triggers make at most one run going and one waiting, that a
failing refresh does not stop the worker, that the events carry the counts,
and that a measurement announcement and a finished sync both start a refresh.
"""

import threading
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest

from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.infrastructure.sync_service import SyncService
from backend.services.observatory.equipment_service import EquipmentService
from backend.services.observatory.suggestion_refresh_worker import SuggestionRefreshWorker
from backend.services.processing.quality_measurement_service import QualityMeasurementService
from wayfindinglib import ObservationPlanning, PackageChange


class _Sockets:
    """A stand-in socket manager that records the events it is given."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def broadcast_ui_event_sync(self, action: str, payload: dict | None = None) -> None:
        """Record one event."""
        self.events.append((action, payload or {}))


def _worker(planning: Any, sockets: _Sockets | None = None) -> SuggestionRefreshWorker:
    """Build a worker over an autospec planning API.

    Returns
    -------
    worker : `SuggestionRefreshWorker`
        The worker.
    """
    return SuggestionRefreshWorker(SimpleNamespace(planning=planning), sockets)


def test_many_triggers_run_one_refresh_and_one_more() -> None:
    """Triggers during a run join one queued run; two never overlap."""
    planning = create_autospec(ObservationPlanning, instance=True)
    started = threading.Event()
    release = threading.Event()
    running = []
    peak = []

    def refresh(target_ids: list[str] | None = None, register_job: bool = True) -> PackageChange:
        """Block the first run so more triggers can pile up.

        Returns
        -------
        change : `PackageChange`
            An empty change.
        """
        running.append(1)
        peak.append(len(running))
        started.set()
        release.wait(timeout=5.0)
        running.pop()
        return PackageChange()

    planning.refresh_suggestions.side_effect = refresh
    worker = _worker(planning)

    worker.request(["M 81"])
    assert started.wait(timeout=5.0)
    for name in ("M 13", "M 57", "M 13", "NGC 2403"):
        worker.request([name])
    release.set()

    assert worker.wait_until_idle()
    assert planning.refresh_suggestions.call_count == 2
    assert planning.refresh_suggestions.call_args_list[0].args == (["M 81"],)
    assert planning.refresh_suggestions.call_args_list[1].args == (["M 13", "M 57", "NGC 2403"],)
    assert max(peak) == 1


def test_a_request_with_no_targets_refreshes_everything() -> None:
    """No target names means all targets, and wins over named ones."""
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.refresh_suggestions.return_value = PackageChange()
    worker = _worker(planning)
    worker.request(None)
    assert worker.wait_until_idle()

    planning.refresh_suggestions.assert_called_once_with(None)


def test_a_failing_refresh_is_logged_and_the_worker_goes_on(caplog: pytest.LogCaptureFixture) -> None:
    """A raising refresh does not reach the caller or stop later runs."""
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.refresh_suggestions.side_effect = [RuntimeError("boom"), PackageChange()]
    worker = _worker(planning)

    worker.request(["M 81"])
    assert worker.wait_until_idle()
    worker.request(["M 13"])
    assert worker.wait_until_idle()

    assert planning.refresh_suggestions.call_count == 2
    assert "The suggestion refresh failed" in caplog.text


def test_a_finished_refresh_announces_the_counts_and_the_checklist() -> None:
    """A change sends `packages:changed` with counts, then `plan:changed`."""
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.refresh_suggestions.return_value = PackageChange(
        added=["a"], completed=["b"], deleted=["d"], checklist_item_ids=["c"]
    )
    planning.get_plan.return_value = SimpleNamespace(id="night-1", revision=4)
    sockets = _Sockets()
    worker = _worker(planning, sockets)

    worker.request(["M 81"])
    assert worker.wait_until_idle()

    assert sockets.events[0] == (
        "packages:changed",
        {
            "packageId": None,
            "targetId": None,
            "kind": "refreshed",
            "added": 1,
            "updated": 0,
            "deleted": 1,
            "completed": 1,
            "reverted": 0,
            "checklistItemsAdded": 1,
            "deletedPackageIds": ["d"],
            "completedPackageIds": ["b"],
        },
    )
    assert sockets.events[1] == (
        "plan:changed",
        {"sessionId": "night-1", "revision": 4, "changed": ["checklist_added"]},
    )


def test_a_refresh_that_changed_nothing_announces_nothing() -> None:
    """An empty change sends no event."""
    planning = create_autospec(ObservationPlanning, instance=True)
    planning.refresh_suggestions.return_value = PackageChange()
    sockets = _Sockets()
    worker = _worker(planning, sockets)

    worker.request(["M 81"])
    assert worker.wait_until_idle()

    assert sockets.events == []


def test_a_measurement_announcement_asks_for_a_refresh() -> None:
    """Rows stored for a target trigger a refresh; none stored does not."""
    triggers: list[list[str]] = []
    service = QualityMeasurementService(SimpleNamespace(), _Sockets(), suggestion_trigger=triggers.append)

    service.announce_summaries([
        {"target_id": "M 81", "frames_measured": 3, "rows_written": 9, "observing_night_ids": []},
        {"target_id": "M 13", "frames_measured": 0, "rows_written": 0, "observing_night_ids": []},
    ])

    assert triggers == [["M 81"]]


def test_a_failing_trigger_never_breaks_the_announcement() -> None:
    """A trigger that raises is logged; the event still went out."""
    sockets = _Sockets()

    def trigger(target_ids: list[str]) -> None:
        """Fail.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("boom")

    service = QualityMeasurementService(SimpleNamespace(), sockets, suggestion_trigger=trigger)

    service.announce_summaries([{"target_id": "M 81", "frames_measured": 1, "rows_written": 1}])

    assert [event[0] for event in sockets.events] == ["quality:measured"]


def test_a_finished_target_sync_asks_for_a_refresh_and_a_calibration_sync_does_not() -> None:
    """A target sync triggers a refresh for it; a Flat folder sync does not."""
    triggers: list[list[str]] = []
    remote = SimpleNamespace(sync_frames=lambda object_id: {})
    service = SyncService(SimpleNamespace(remote=remote), suggestion_trigger=triggers.append)

    service._trigger_suggestions("M 81")
    service._trigger_suggestions("Flat")

    assert triggers == [["M 81"]]


def test_the_sync_thread_triggers_after_the_library_sync() -> None:
    """Starting a sync runs the library sync, then the trigger."""
    from backend.services.infrastructure import thread_management

    triggers: list[list[str]] = []
    remote = SimpleNamespace(sync_frames=lambda object_id: {})
    service = SyncService(SimpleNamespace(remote=remote), suggestion_trigger=triggers.append)

    service.start_sync("M 42")
    thread_management._syncing["M 42"].join(timeout=5.0)

    assert triggers == [["M 42"]]


def test_the_telescope_methods_pass_limits_through_an_autospec() -> None:
    """The limits reach the library; the read asks for the telescope."""
    from wayfindinglib.api.control.equipment import EquipmentControl

    equipment = create_autospec(EquipmentControl, instance=True)
    equipment.set_active_telescope.return_value = True
    equipment.status.return_value = SimpleNamespace(telescope="scope")
    service = EquipmentService(SimpleNamespace(equipment=equipment))

    assert service.set_active_telescope("scope-1", {"min_altitude_deg": 25.0}) is True
    equipment.set_active_telescope.assert_called_once_with("scope-1", limits={"min_altitude_deg": 25.0})
    assert service.get_active_telescope() == "scope"
    equipment.status.assert_called_once_with(include=["telescope"])
    for method in ("observatory:get_active_telescope", "observatory:set_active_telescope"):
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method][0] == "equipment_service"
