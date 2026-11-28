"""Purpose: Test the package RPC methods and the ``packages:changed`` event.

Description: The plan service is the adapter between the RPC route and the
planning library's ``sessions`` and ``packages`` objects. It turns the ids
and JSON the app sends into objects and input models, puts the revision the
app last read on the object, and chooses the package call (``update``,
``restore``, ``delete``) for each ``plan:edit_package`` request. The library
objects are `create_autospec` mocks of the real classes, so a call with a
wrong argument name fails. The tests check that:

* the new methods and the event are declared and served,
* `plan:list_packages` passes the package filters through as objects,
* `plan:current_session` reads tonight's session by the library's night name,
  with every part the reply has always carried, and never creates one,
* each package write announces ``packages:changed`` once with the package id
  and the change kind, and a refused write announces nothing,
* a new package may have no exposures, and a catalog name becomes a target,
* promoting a suggested package by adding it to the queue is announced.
"""

from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import create_autospec

import pytest

from astrometricslib import Target
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.public_interface import RPC_METHODS, UI_EVENTS
from backend.routers.rpc_router import rpc_registry
from backend.services.observatory.plan_service import SESSION_PARTS, PlanService
from wayfindinglib import ExposureRequest, ObservationPackage, ObservationSession
from wayfindinglib.api.planning.packages import PlanningPackages
from wayfindinglib.api.planning.sessions import PlanningSessions
from wayfindinglib.exceptions import PlanRevisionConflictError
from wayfindinglib.reports import PackageStatus


class _Sockets:
    """A stand-in socket manager that records the events it is given."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def broadcast_ui_event_sync(self, action: str, payload: dict | None = None) -> None:
        """Record one event."""
        self.events.append((action, payload or {}))


class _Targets:
    """A stand-in target catalog that knows M 81 and can add catalog names."""

    def __init__(self) -> None:
        """Know only M 81."""
        self.known = {"M 81": Target(id="M 81")}
        self.created: list[str] = []

    def get(self, target_id: str, required: bool = False) -> Target | None:
        """Find a known target.

        Returns
        -------
        target : `Target` or `None`
            The target, or `None`.

        Raises
        ------
        NotFoundError
            For an unknown id when ``required`` is set.
        """
        found = self.known.get(target_id)
        if found is None and required:
            raise NotFoundError(f"No target {target_id!r}.")
        return found

    def create(self, name: str, from_catalog: bool = False) -> Target:
        """Add a catalog name, as the library does for NGC 7331 only.

        Returns
        -------
        target : `Target`
            The new target.

        Raises
        ------
        NotFoundError
            For a name the stand-in catalog does not know.
        """
        assert from_catalog
        if name != "NGC 7331":
            raise NotFoundError(f"The catalog does not know {name!r}.")
        self.created.append(name)
        self.known[name] = Target(id=name)
        return self.known[name]


def _package(revision: int = 1, status: PackageStatus = PackageStatus.PLANNED) -> ObservationPackage:
    """Build a stored package.

    Returns
    -------
    package : `ObservationPackage`
        A package for M 81 with no exposures.
    """
    return ObservationPackage(id="pkg-1", name="M 81", target_id="M 81", revision=revision, status=status)


def _session(revision: int = 1) -> ObservationSession:
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
        revision=revision,
    )


@pytest.fixture
def setup() -> tuple[Any, _Sockets, PlanService]:
    """Put a plan service over autospec planning objects.

    Returns
    -------
    setup : `tuple`
        The planning stand-in (autospec ``sessions`` and ``packages``), the
        socket recorder and the service.
    """
    planning = SimpleNamespace(
        sessions=create_autospec(PlanningSessions, instance=True),
        packages=create_autospec(PlanningPackages, instance=True),
    )
    planning.packages.get.return_value = _package()
    planning.sessions.query.return_value = [_session()]
    sockets = _Sockets()
    wayfinder = SimpleNamespace(planning=planning, astrometrics=SimpleNamespace(targets=_Targets()))
    return planning, sockets, PlanService(wayfinder, sockets)


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
    """The filters reach the library as objects; a read announces nothing."""
    planning, sockets, service = setup
    planning.packages.query.return_value = [_package()]

    result = service.list_packages(status="planned", target="M 81")

    assert result[0].id == "pkg-1"
    arguments = planning.packages.query.call_args.kwargs
    assert (arguments["status"], arguments["target"].id, arguments["dismissed"]) == ("planned", "M 81", False)
    service.list_packages(include_dismissed=True, target="Gone")
    arguments = planning.packages.query.call_args.kwargs
    assert (arguments["target"].id, arguments["dismissed"]) == ("Gone", None)
    assert sockets.events == []


def test_package_for_target_asks_the_library_to_pick_one(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """The service passes the target on and chooses nothing."""
    planning, sockets, service = setup
    planning.packages.get_for_target.return_value = _package()

    assert service.package_for_target("M 81").id == "pkg-1"
    assert planning.packages.get_for_target.call_args.args[0].id == "M 81"
    planning.packages.get_for_target.return_value = None
    assert service.package_for_target("M 1") is None
    assert sockets.events == []


def test_current_session_reads_tonights_night_and_creates_nothing(
    setup: tuple[Any, _Sockets, PlanService], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The night comes from the library; no session gives `None`."""
    planning, sockets, service = setup
    monkeypatch.setattr(
        "backend.services.observatory.plan_service.observing_night_id", lambda timestamp: "2026-10-24"
    )
    planning.sessions.get.return_value = None

    assert service.current_session() is None

    planning.sessions.get.assert_called_once_with("2026-10-24", include=SESSION_PARTS)
    planning.sessions.create.assert_not_called()
    assert sockets.events == []


def test_current_session_returns_the_session_without_a_progress_key_when_absent(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """Tonight's session comes back whole; no progress key is added."""
    planning, _, service = setup
    planning.sessions.get.return_value = _session()

    session = service.current_session()

    assert session.id == "night-1"
    assert "progress" not in session.model_dump(by_alias=True)


def test_creating_a_package_with_no_exposures_announces_it(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A package needs only a target name; its creation is announced."""
    planning, sockets, service = setup
    planning.packages.create.return_value = _package(revision=0)

    result = service.create_package("M 81")

    assert result.id == "pkg-1"
    target, rows = planning.packages.create.call_args.args
    assert (target.id, rows) == ("M 81", [])
    assert sockets.events == [
        ("packages:changed", {"packageId": "pkg-1", "targetId": "M 81", "kind": "created", "revision": 0})
    ]


def test_creating_a_package_builds_the_exposure_rows(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """Exposure dictionaries and sequence items become exposure rows."""
    planning, _, service = setup
    planning.packages.create.return_value = _package(revision=0)
    row = {"frame_type": "LIGHT", "filter": "Ha", "exposure_s": 60, "count": 3}

    service.create_package("M 81", exposure_requests=[row])
    assert planning.packages.create.call_args.args[1] == [ExposureRequest.model_validate(row)]

    service.create_package("M 81", plan_items=[{"count": 2, "exposure_s": 30, "filter": "ha"}])
    [made] = planning.packages.create.call_args.args[1]
    assert (made.count, made.exposure_s, made.filter.value, made.frame_type.value) == (
        2,
        30.0,
        "Ha",
        "LIGHT",
    )

    with pytest.raises(InvalidArgumentError, match="not both"):
        service.create_package("M 81", exposure_requests=[], plan_items=[])
    with pytest.raises(InvalidArgumentError, match="Unknown filter"):
        service.create_package("M 81", plan_items=[{"count": 1, "exposure_s": 1, "filter": "Z"}])
    with pytest.raises(InvalidArgumentError, match="not valid"):
        service.create_package("M 81", exposure_requests=[{"count": 1}])


def test_a_package_for_a_catalog_name_creates_the_target(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A name the library lacks comes from the catalog, else is refused."""
    planning, _, service = setup
    planning.packages.create.return_value = _package(revision=0)
    targets = service.wayfinder.astrometrics.targets

    service.create_package("NGC 7331")

    assert targets.created == ["NGC 7331"]
    assert planning.packages.create.call_args.args[0].id == "NGC 7331"
    with pytest.raises(NotFoundError, match="does not know"):
        service.create_package("NGC 9999")


@pytest.mark.parametrize(
    ("arguments", "verb", "kind"),
    [
        ({"priority": 2}, "update", "edited"),
        ({"reopen": True}, "update", "reopened"),
        ({"delete": True}, "delete", "deleted"),
        ({"dismiss": True, "source_key": "row-1"}, "update", "dismissed"),
        ({"undismiss": True}, "restore", "undismissed"),
        ({"framing": {"ra_deg": 1.0}}, "update", "framing"),
        ({"framing": {"ra_deg": 1.0}, "notes": "x"}, "update", "edited"),
    ],
)
def test_each_package_write_announces_once_with_its_kind(
    setup: tuple[Any, _Sockets, PlanService], arguments: dict[str, Any], verb: str, kind: str
) -> None:
    """Each write gives one event and carries the app's revision."""
    planning, sockets, service = setup
    getattr(planning.packages, verb).return_value = _package(revision=2)

    service.edit_package("pkg-1", expected_revision=1, **arguments)

    sent = getattr(planning.packages, verb).call_args.args[0]
    assert (sent.id, sent.revision) == ("pkg-1", 1)
    revision = 1 if verb == "delete" else 2
    assert sockets.events == [
        ("packages:changed", {"packageId": "pkg-1", "targetId": "M 81", "kind": kind, "revision": revision})
    ]


def test_a_refused_package_write_announces_nothing(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A stale revision passes up as an error and sends no event."""
    planning, sockets, service = setup
    planning.packages.update.side_effect = PlanRevisionConflictError(
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
    planning.packages.update.return_value = _package(revision=2)
    monkeypatch.setattr(container, "plan_service", service, raising=False)

    result = await rpc_registry.execute("plan:edit_package", {"package_id": "pkg-1", "priority": 4})

    assert result.revision == 2
    assert planning.packages.update.call_args.kwargs == {"priority": 4}
    assert [name for name, _ in sockets.events] == ["packages:changed"]


def test_adding_a_suggested_package_to_the_queue_announces_the_promotion(
    setup: tuple[Any, _Sockets, PlanService],
) -> None:
    """A suggested package that is queued also gives ``packages:changed``."""
    planning, sockets, service = setup
    planning.packages.get.return_value = _package(status=PackageStatus.SUGGESTED)
    planning.sessions.add.return_value = _session(revision=2)

    service.edit_plan("night-1", add=[{"package_id": "pkg-1"}])

    session, requests = planning.sessions.add.call_args.args
    assert (session.id, requests[0].package_id) == ("night-1", "pkg-1")
    assert [name for name, _ in sockets.events] == ["packages:changed", "plan:changed"]
    assert sockets.events[0][1] == {"packageId": "pkg-1", "targetId": None, "kind": "promoted"}
    assert sockets.events[1][1] == {
        "sessionId": "night-1",
        "revision": 2,
        "changed": ["queue_added", "package_promoted"],
    }


def test_a_dismissal_passes_its_row_key_to_the_library(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A dismissal passes its row key, or asks for the whole package."""
    planning, _, service = setup
    planning.packages.update.return_value = _package(revision=2)

    service.edit_package("pkg-1", dismiss=True, source_key="row-1")
    assert planning.packages.update.call_args.kwargs == {"dismissed_rows": ["row-1"]}
    service.edit_package("pkg-1", dismiss=True)
    assert planning.packages.update.call_args.kwargs == {"dismissed": True}


@pytest.mark.parametrize(
    "arguments",
    [
        {"dismiss": True, "framing": {"ra_deg": 1.0}},
        {"delete": True, "priority": 1},
        {"dismiss": True, "undismiss": True},
        {"source_key": "row-1", "priority": 2},
        {},
    ],
)
def test_a_package_write_that_mixes_actions_or_gives_none_is_refused(
    setup: tuple[Any, _Sockets, PlanService], arguments: dict[str, Any]
) -> None:
    """Delete and dismissal stand alone; a call must ask for something."""
    _, sockets, service = setup

    with pytest.raises(InvalidArgumentError):
        service.edit_package("pkg-1", **arguments)

    assert sockets.events == []


def test_reopen_and_a_change_go_in_one_write(setup: tuple[Any, _Sockets, PlanService]) -> None:
    """A reopen and a change are one update, from the app's revision."""
    planning, _, service = setup
    planning.packages.update.return_value = _package(revision=5)

    service.edit_package("pkg-1", reopen=True, notes="again", expected_revision=4)

    assert planning.packages.update.call_args.args[0].revision == 4
    assert planning.packages.update.call_args.kwargs == {"notes": "again", "status": "PLANNED"}


def test_refresh_suggestions_hands_the_targets_to_the_worker() -> None:
    """The RPC method only queues the work and reports that it started."""
    worker = SimpleNamespace(requests=[])
    worker.request = worker.requests.append
    service = PlanService(SimpleNamespace(planning=SimpleNamespace()), None, worker)

    assert service.refresh_suggestions(["M 81"]) == {"started": True}
    assert worker.requests == [["M 81"]]
    assert PlanService(SimpleNamespace(), None).refresh_suggestions() == {"started": False}
    assert rpc_registry._handlers["plan:refresh_suggestions"] == ("plan_service", "refresh_suggestions")
