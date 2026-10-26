"""Purpose: Test the package RPC methods and the ``packages:changed`` event.

Description: The plan service wraps the planning library's `get_plan` (with
its package list), `create_plan` and `edit_package`. The library is a
`create_autospec` of the real `ObservationPlanning`, so a call with a wrong
argument name fails. The tests check that:

* the new methods and the event are declared and served,
* `plan:get` passes the package filters through,
* `plan:current_session` reads tonight's session by the library's night name
  and never creates one,
* each package write announces ``packages:changed`` once with the package id
  and the change kind, and a refused write announces nothing,
* a new package may have no exposures,
* promoting a suggested package by adding it to the queue is announced.
"""

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest

from backend.public_interface import RPC_METHODS, UI_EVENTS
from backend.routers.rpc_router import rpc_registry
from backend.services.observatory.plan_service import PlanService
from wayfindinglib import (
    ObservationPackage,
    ObservationPlanning,
    ObservationSession,
    PackageStatus,
    PlanChange,
    PlanChangeItem,
    PlanRevisionConflictError,
)


class _Sockets:
    """A stand-in socket manager that records the events it is given."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def broadcast_ui_event_sync(self, action: str, payload: dict | None = None) -> None:
        """Record one event."""
        self.events.append((action, payload or {}))


def _package(revision: int = 1, status: PackageStatus = PackageStatus.PLANNED) -> ObservationPackage:
    """Build a stored package.

    Returns
    -------
    package : `ObservationPackage`
        A package for M 81 with no exposures.
    """
    return ObservationPackage(id="pkg-1", name="M 81", target_id="M 81", revision=revision, status=status)


def _session() -> ObservationSession:
    """Build a minimal session.

    Returns
    -------
    session : `ObservationSession`
        A session for 2026-10-24.
    """
    return ObservationSession(
        id="night-1",
        night_date=date(2026, 10, 24),
        site_profile_id="site",
        telescope_id="scope",
        camera_id="camera",
        revision=1,
    )


@pytest.fixture
def setup() -> tuple[Any, _Sockets, PlanService]:
    """Put a plan service over an autospec planning API.

    Returns
    -------
    setup : `tuple`
        The autospec planning mock, the socket recorder and the service.
    """
    planning = create_autospec(ObservationPlanning, instance=True)
    sockets = _Sockets()
    return planning, sockets, PlanService(SimpleNamespace(planning=planning), sockets)


def test_the_new_methods_and_the_event_are_declared_and_served() -> None:
    """The new methods and the event are declared and served."""
    for method, handler in (
        ("plan:current_session", "current_session"),
        ("plan:list_packages", "list_packages"),
        ("plan:package_for_target", "package_for_target"),
        ("plan:create_package", "create_package"),
        ("plan:edit_package", "edit_package"),
    ):
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method] == ("plan_service", handler)
    assert "packages:changed" in [event.name for event in UI_EVENTS]


def test_list_packages_passes_the_filters_through(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """The package filters reach the library; a read announces nothing."""
    planning, sockets, service = setup
    planning.get_plan.return_value = [_package()]

    result = service.list_packages(status="planned", target="M 81")

    assert result[0].id == "pkg-1"
    planning.get_plan.assert_called_once_with(
        kind="packages", status="planned", target="M 81", include_dismissed=False
    )
    assert sockets.events == []


def test_package_for_target_asks_the_library_to_pick_one(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """The service passes `pick="one"` and the target, and chooses nothing."""
    planning, sockets, service = setup
    planning.get_plan.return_value = _package()

    assert service.package_for_target("m_81").id == "pkg-1"
    planning.get_plan.assert_called_once_with(kind="packages", target="m_81", pick="one")
    planning.get_plan.return_value = None
    assert service.package_for_target("M 1") is None
    assert sockets.events == []


def test_plan_get_keeps_the_old_call_working(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A call with only a session id still reads that session."""
    planning, _, service = setup
    planning.get_plan.return_value = _session()

    service.get_plan("night-1")

    planning.get_plan.assert_called_once_with("night-1")


def test_current_session_reads_tonights_night_and_creates_nothing(
    setup: tuple[Any, _Sockets, PlanService], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The night comes from the library; no session gives `None`."""
    planning, sockets, service = setup
    monkeypatch.setattr(
        "backend.services.observatory.plan_service.observing_night_id", lambda timestamp: "2026-10-24"
    )
    planning.get_plan.return_value = None

    assert service.current_session() is None

    planning.get_plan.assert_called_once_with(night_id="2026-10-24", include=["progress"])
    planning.create_plan.assert_not_called()
    assert sockets.events == []


def test_current_session_returns_the_session(
    setup: tuple[Any, _Sockets, PlanService], monkeypatch: pytest.MonkeyPatch
) -> None:
    """When tonight has a session, it comes back whole."""
    planning, _, service = setup
    monkeypatch.setattr(
        "backend.services.observatory.plan_service.observing_night_id", lambda timestamp: "2026-10-24"
    )
    planning.get_plan.return_value = _session()

    assert service.current_session().id == "night-1"


def test_creating_a_package_with_no_exposures_announces_it(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A package needs only a target name; its creation is announced."""
    planning, sockets, service = setup
    planning.create_plan.return_value = _package(revision=0)

    result = service.create_package("M 81")

    assert result.id == "pkg-1"
    planning.create_plan.assert_called_once()
    arguments = planning.create_plan.call_args.kwargs
    assert arguments["kind"] == "package"
    assert arguments["target"] == "M 81"
    assert arguments["exposure_requests"] is None
    assert sockets.events == [
        ("packages:changed", {"packageId": "pkg-1", "targetId": "M 81", "kind": "created", "revision": 0})
    ]


def test_creating_a_package_turns_exposure_dicts_into_requests(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """Exposure dictionaries become the library's `ExposureRequest`."""
    planning, _, service = setup
    planning.create_plan.return_value = _package(revision=0)

    service.create_package(
        "M 81",
        exposure_requests=[{"frame_type": "LIGHT", "filter": "Ha", "exposure_sec": 60, "count": 3}],
    )

    (request,) = planning.create_plan.call_args.kwargs["exposure_requests"]
    assert request.count == 3
    assert request.exposure_sec == 60


def test_creating_a_session_still_asks_for_the_empty_session(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """The default kind is the night's session; no package event is sent."""
    planning, sockets, service = setup
    planning.create_plan.return_value = _session()

    service.create_plan(night_id="2026-10-24")

    planning.create_plan.assert_called_once_with(kind="empty_session", night_id="2026-10-24")
    assert sockets.events == []


@pytest.mark.parametrize(
    ("arguments", "kind"),
    [
        ({"priority": 2}, "edited"),
        ({"reopen": True}, "reopened"),
        ({"delete": True}, "deleted"),
        ({"dismiss": True, "source_key": "row-1"}, "dismissed"),
        ({"undismiss": True}, "undismissed"),
        ({"framing": {"ra_deg": 1.0}}, "framing"),
        ({"framing": {"ra_deg": 1.0}, "notes": "x"}, "edited"),
    ],
)
def test_each_package_write_announces_once_with_its_kind(
    setup: tuple[Any, _Sockets, PlanService], arguments: dict[str, Any], kind: str
) -> None:
    """Edit, reopen and delete each give one event with id and kind."""
    planning, sockets, service = setup
    planning.edit_package.return_value = _package(revision=2)

    service.edit_package("pkg-1", expected_revision=1, **arguments)

    assert planning.edit_package.call_args.args == ("pkg-1",)
    assert planning.edit_package.call_args.kwargs["expected_revision"] == 1
    assert sockets.events == [
        ("packages:changed", {"packageId": "pkg-1", "targetId": "M 81", "kind": kind, "revision": 2})
    ]


def test_a_refused_package_write_announces_nothing(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A stale revision passes up as an error and sends no event."""
    planning, sockets, service = setup
    planning.edit_package.side_effect = PlanRevisionConflictError(
        "The package is at a newer revision.", details={"current_revision": 3}
    )

    with pytest.raises(PlanRevisionConflictError):
        service.edit_package("pkg-1", notes="x", expected_revision=1)

    assert sockets.events == []


@pytest.mark.anyio
async def test_the_rpc_route_reaches_edit_package(
    setup: tuple[Any, _Sockets, PlanService], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``plan:edit_package`` through the RPC registry reaches the library."""
    from backend.container import container

    planning, sockets, service = setup
    planning.edit_package.return_value = _package(revision=2)
    monkeypatch.setattr(container, "plan_service", service, raising=False)

    result = await rpc_registry.execute("plan:edit_package", {"package_id": "pkg-1", "priority": 4})

    assert result.revision == 2
    assert planning.edit_package.call_args.kwargs["priority"] == 4
    assert [name for name, _ in sockets.events] == ["packages:changed"]


def test_adding_a_suggested_package_to_the_queue_announces_the_promotion(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """A ``package_promoted`` item also gives ``packages:changed``."""
    planning, sockets, service = setup
    planning.edit_queue.return_value = PlanChange(
        session_id="night-1",
        previous_revision=1,
        revision=2,
        changes=[PlanChangeItem(kind="queue_added"), PlanChangeItem(kind="package_promoted", detail="pkg-1")],
        session=_session(),
    )

    service.edit_plan("night-1", add=[{"package_id": "pkg-1"}])

    assert [name for name, _ in sockets.events] == ["packages:changed", "plan:changed"]
    assert sockets.events[0][1]["packageId"] == "pkg-1"
    assert sockets.events[0][1]["kind"] == "promoted"


def test_list_packages_can_include_dismissed_suggestions(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """`include_dismissed` reaches the library."""
    planning, _, service = setup
    planning.get_plan.return_value = []

    service.list_packages(include_dismissed=True)

    planning.get_plan.assert_called_once_with(
        kind="packages", status=None, target=None, include_dismissed=True
    )


def test_edit_package_passes_the_suggestion_arguments_to_the_library(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """Framing, dismiss, undismiss and source_key reach the library by name."""
    planning, _, service = setup
    planning.edit_package.return_value = _package(revision=2)

    service.edit_package("pkg-1", framing={"ra_deg": 1.0}, dismiss=True, source_key="row-1")

    kwargs = planning.edit_package.call_args.kwargs
    assert kwargs["framing"] == {"ra_deg": 1.0}
    assert kwargs["dismiss"] is True
    assert kwargs["undismiss"] is False
    assert kwargs["source_key"] == "row-1"


def test_refresh_suggestions_hands_the_targets_to_the_worker() -> None:
    """The RPC method only queues the work and reports that it started."""
    worker = SimpleNamespace(requests=[])
    worker.request = worker.requests.append
    service = PlanService(
        SimpleNamespace(planning=create_autospec(ObservationPlanning, instance=True)), None, worker
    )

    assert service.refresh_suggestions(["M 81"]) == {"started": True}
    assert worker.requests == [["M 81"]]
    assert PlanService(SimpleNamespace(), None).refresh_suggestions() == {"started": False}
    assert rpc_registry._handlers["plan:refresh_suggestions"] == ("plan_service", "refresh_suggestions")
