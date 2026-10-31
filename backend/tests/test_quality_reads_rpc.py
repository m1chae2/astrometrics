"""Purpose: Test the RPC reads and changed calls of issue #96.

Description: Every new method is checked against an autospec of the real
library class, so a call with a wrong argument name fails the test. The tests
cover:

* the nine new reads (declared, served, passing exactly the library
  arguments, and registering no background job for history reads),
* ``plan:create``,
* ``handoff:update_state`` with ``planning_time`` and ``clear_planning_time``,
* ``mosaic:preview`` passing the rotation,
* ``planetarium:get_visibility`` passing ``end`` and ``include``.
"""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, create_autospec

import pytest

from astrometricslib import InvalidArgumentError
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.stellar_service import StellarService
from backend.services.infrastructure.handoff_service import HandoffService
from backend.services.observatory.advisory_service import AdvisoryService
from backend.services.observatory.mosaic_service import MosaicService
from backend.services.observatory.plan_service import PlanService
from wayfindinglib.api.control.history import HistoryControl
from wayfindinglib.api.control.safety import SafetyControl
from wayfindinglib.api.planning import ObservationPlanning


def _wayfinder() -> SimpleNamespace:
    """Build a stand-in Wayfinder made of autospecs of the real classes.

    Returns
    -------
    wayfinder : `types.SimpleNamespace`
        With ``planning``, and ``control`` holding ``history`` and ``safety``.
    """
    control = SimpleNamespace(
        history=create_autospec(HistoryControl, instance=True),
        safety=create_autospec(SafetyControl, instance=True),
    )
    return SimpleNamespace(planning=create_autospec(ObservationPlanning, instance=True), control=control)


NEW_READS = {
    "advisory:target_report": "get_target_report",
    "advisory:tonight": "get_tonight_ranking",
    "advisory:result": "get_result_statements",
    "advisory:depth_goal": "get_depth_goal",
    "advisory:set_depth_goal": "set_depth_goal",
    "history:nights": "list_nights",
    "history:night_quality": "get_night_quality",
    "history:target_changes": "list_target_changes",
    "history:live_session_status": "get_live_session_status",
    "observatory:safety_status": "get_safety_status",
}
"""The new reads served by the advisory service, with their methods."""


def test_new_methods_are_declared_and_served() -> None:
    """Every new method is declared and routed to the right handler."""
    for method, handler in NEW_READS.items():
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method] == ("advisory_service", handler)
    assert rpc_registry._handlers["calibration:query"] == ("astrometrics.processing.calibration", "query")
    assert rpc_registry._handlers["plan:create"] == ("plan_service", "create_plan")
    for method in ("calibration:query", "plan:create"):
        assert method in RPC_METHODS


def test_target_report_asks_for_the_quality_kind() -> None:
    """The target report passes only the target."""
    wayfinder = _wayfinder()
    AdvisoryService(wayfinder).get_target_report("M 81")
    wayfinder.planning.get_advisory.assert_called_once_with(kind="quality", target="M 81")


def test_tonight_ranking_passes_the_planning_time() -> None:
    """The ranking passes only the moment."""
    wayfinder = _wayfinder()
    AdvisoryService(wayfinder).get_tonight_ranking("2026-10-24T22:00:00-06:00")
    wayfinder.planning.get_advisory.assert_called_once_with(kind="tonight", at="2026-10-24T22:00:00-06:00")


def test_result_statements_pass_target_and_object() -> None:
    """The result read passes the target and the star."""
    wayfinder = _wayfinder()
    AdvisoryService(wayfinder).get_result_statements("M 81", "star-1")
    wayfinder.planning.get_advisory.assert_called_once_with(kind="result", target="M 81", object_id="star-1")


def test_depth_goal_methods_pass_the_target_and_the_level() -> None:
    """The depth goal read passes the target; the write adds the level."""
    wayfinder = _wayfinder()
    service = AdvisoryService(wayfinder)
    service.get_depth_goal("NGC 7331")
    wayfinder.planning.get_depth_goal.assert_called_once_with("NGC 7331")
    service.set_depth_goal("NGC 7331", "deep")
    wayfinder.planning.set_depth_goal.assert_called_once_with("NGC 7331", "deep")


def test_history_reads_register_no_job() -> None:
    """Each history read passes ``register_job=False`` and its arguments."""
    wayfinder = _wayfinder()
    service = AdvisoryService(wayfinder)
    history = wayfinder.control.history
    service.list_nights()
    history.query.assert_called_with(kind="nights", limit=10, register_job=False)
    service.get_night_quality("2026-10-23")
    history.query.assert_called_with(kind="night_quality", session_id="2026-10-23", register_job=False)
    service.list_target_changes("2026-10-23", limit=5)
    history.query.assert_called_with(
        kind="target_changes", session_id="2026-10-23", limit=5, register_job=False
    )


def test_live_status_does_not_refresh_the_logs() -> None:
    """The live status reads the logs on disk and copies no new ones."""
    wayfinder = _wayfinder()
    AdvisoryService(wayfinder).get_live_session_status(window_minutes=5.0, include=["captures"])
    wayfinder.control.history.get_live_session_status.assert_called_once_with(
        window_minutes=5.0, refresh=False, include=["captures"], limit=10
    )


def test_safety_status_asks_only_for_the_policy() -> None:
    """The safety read includes only the delegation policy."""
    wayfinder = _wayfinder()
    AdvisoryService(wayfinder).get_safety_status()
    wayfinder.control.safety.status.assert_called_once_with(include=["delegation_policy"])


def test_plan_create_makes_an_empty_session_for_the_night() -> None:
    """``plan:create`` wraps ``create_plan(kind="empty_session")``."""
    wayfinder = _wayfinder()
    PlanService(wayfinder).create_plan(night_id="2026-10-24")
    wayfinder.planning.create_plan.assert_called_once_with(kind="empty_session", night_id="2026-10-24")


def test_mosaic_preview_passes_the_rotation() -> None:
    """The mosaic preview passes ``rotation_deg`` on, and 0 when omitted."""
    wayfinder = _wayfinder()
    service = MosaicService(wayfinder)
    service.preview_mosaic(10.0, 20.0, {"rows": 2, "cols": 3, "overlap": 10}, rotation_deg=35.0)
    arguments = wayfinder.planning.calculate_panels.call_args.kwargs
    assert arguments["rotation_deg"] == pytest.approx(35.0)
    assert arguments["rows"] == 2
    service.preview_mosaic(10.0, 20.0, {})
    assert wayfinder.planning.calculate_panels.call_args.kwargs["rotation_deg"] == pytest.approx(0.0)


def _stellar_service(wayfinder: Any) -> StellarService:
    """Build a stellar service that uses a stand-in Wayfinder.

    Parameters
    ----------
    wayfinder : `~typing.Any`
        The Wayfinder to use.

    Returns
    -------
    service : `StellarService`
        A service with no other setup.
    """
    service = StellarService.__new__(StellarService)
    service._wayfinder = wayfinder
    return service


def test_visibility_passes_end_and_include() -> None:
    """The visibility read passes ``end`` and ``include``; old calls hold."""
    wayfinder = _wayfinder()
    wayfinder.planning.get_visibility.return_value = SimpleNamespace(objects=[])
    service = _stellar_service(wayfinder)
    service.get_visibility(
        [{"id": "M 81", "type": "target"}],
        time="2026-10-24T20:00:00+00:00",
        end="2026-10-25T10:00:00+00:00",
        include=["twilight"],
    )
    arguments = wayfinder.planning.get_visibility.call_args
    assert arguments.args == (["M 81"],)
    assert arguments.kwargs == {
        "time": "2026-10-24T20:00:00+00:00",
        "end_time": "2026-10-25T10:00:00+00:00",
        "include": ["twilight"],
    }
    service.get_visibility([{"id": "M 81", "type": "target"}])
    assert wayfinder.planning.get_visibility.call_args.kwargs == {
        "time": None,
        "end_time": None,
        "include": ["meridian"],
    }


def test_planning_time_is_set_kept_and_cleared() -> None:
    """A planning time is stored, kept by a mode change, then cleared."""
    service = HandoffService()
    assert service.get_state()["planning_time"] is None
    state = service.update_state(planning_time="2026-10-24T22:00:00-06:00")
    assert state["planning_time"] == "2026-10-24T22:00:00-06:00"
    state = service.update_state(active_mode="Planetarium")
    assert state["planning_time"] == "2026-10-24T22:00:00-06:00"
    state = service.update_state(clear_planning_time=True)
    assert state["planning_time"] is None


@pytest.mark.parametrize("bad_text", ["2026-10-24T22:00:00", "tonight", ""])
def test_planning_time_without_an_offset_is_refused(bad_text: str) -> None:
    """Text that is not ISO 8601 with a UTC offset is refused."""
    service = HandoffService()
    service.update_state(planning_time="2026-10-24T22:00:00+00:00")
    with pytest.raises(InvalidArgumentError):
        service.update_state(planning_time=bad_text)
    assert service.get_state()["planning_time"] == "2026-10-24T22:00:00+00:00"


def test_planning_time_is_broadcast_in_the_handoff_event() -> None:
    """The ``handoff`` event carries the planning time."""
    sockets = MagicMock()
    service = HandoffService(socket_manager=sockets)
    service.update_state(planning_time="2026-10-24T22:00:00-06:00")
    event_name, payload = sockets.broadcast_ui_event_sync.call_args.args
    assert event_name == "handoff"
    assert payload["planning_time"] == "2026-10-24T22:00:00-06:00"
    service.update_state(clear_planning_time=True)
    assert sockets.broadcast_ui_event_sync.call_args.args[1]["planning_time"] is None


def test_calibration_query_accepts_the_match_arguments() -> None:
    """The route target takes the match arguments (autospec checks names)."""
    from unittest.mock import create_autospec

    from astrometricslib import CalibrationCatalog

    catalog = create_autospec(CalibrationCatalog, instance=True)
    catalog.query(
        detail="match",
        kind="bias",
        camera_id="ASI533",
        gain=100,
        offset=10.0,
        binning="2",
        temperature_c=-9.0,
    )
    catalog.query.assert_called_once()
