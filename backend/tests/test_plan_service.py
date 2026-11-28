"""Purpose: Test the night plan RPC methods and the ``plan:changed`` event.

Description: The plan service adapts the RPC calls to the planning
library's ``sessions`` object (``remove`` and ``update``; a checklist tick is
an ``update`` of the checklist). The tests use a stand-in ``sessions`` object
and a stand-in socket manager to check that:

* the tick method and the event are declared and served,
* each successful write announces ``plan:changed`` once with the session id,
  the new revision and the kinds of change,
* a stale revision announces nothing and reaches the caller as the
  ``conflict`` error with the current revision in its details.
"""

import json
from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from backend.container import container
from backend.public_interface import RPC_METHODS, UI_EVENTS
from backend.routers import rpc_router
from backend.routers.rpc_router import rpc_registry
from backend.services.observatory.plan_service import PlanService
from backend.services.rpc_protocol import RPCRequest
from wayfindinglib import ObservationSession
from wayfindinglib.exceptions import PlanRevisionConflictError


class _Sockets:
    """A stand-in socket manager that records the events it is given."""

    def __init__(self) -> None:
        """Start with no events."""
        self.events: list[tuple[str, dict]] = []

    def broadcast_ui_event_sync(self, action: str, payload: dict | None = None) -> None:
        """Record one event."""
        self.events.append((action, payload or {}))


class _Sessions:
    """A stand-in ``planning.sessions`` that keeps a revision, may refuse."""

    def __init__(self) -> None:
        """Start at revision 3 with one entry and one checklist item."""
        self.revision = 3
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.changes_nothing = False

    def _session(self) -> ObservationSession:
        """Build the stored session at the current revision.

        Returns
        -------
        session : `ObservationSession`
            A session with entry ``entry-1`` and checklist item ``item-1``.
        """
        return ObservationSession.model_validate({
            "id": "night-1",
            "night_date": date(2026, 10, 24),
            "site_profile_id": "site",
            "telescope_id": "scope",
            "camera_id": "camera",
            "revision": self.revision,
            "queue": [
                {
                    "id": "entry-1",
                    "observation_package_id": "pkg-1",
                    "target_id": "M 81",
                    "start_time_mode": "SOONEST",
                }
            ],
            "checklist": [{"item_id": "item-1", "rule": "focus"}],
        })

    def query(self) -> list[ObservationSession]:
        """List the one stored session.

        Returns
        -------
        sessions : `list` [`ObservationSession`]
            The session.
        """
        return [self._session()]

    def _write(self, session: ObservationSession) -> ObservationSession:
        """Store a change unless the session's revision is stale.

        Parameters
        ----------
        session : `ObservationSession`
            The session as the caller read it.

        Returns
        -------
        session : `ObservationSession`
            The stored session.

        Raises
        ------
        PlanRevisionConflictError
            If the session's revision is not the stored one.
        """
        if session.revision != self.revision:
            raise PlanRevisionConflictError(
                "The plan is at a newer revision.",
                details={"current_revision": self.revision, "expected_revision": session.revision},
            )
        if not self.changes_nothing:
            self.revision += 1
        return self._session()

    def remove(self, session: ObservationSession, entries: list[Any]) -> ObservationSession:
        """Write a queue change.

        Returns
        -------
        session : `ObservationSession`
            The stored session.
        """
        self.calls.append(("remove", {"entries": [entry.id for entry in entries]}))
        return self._write(session)

    def update(self, session: ObservationSession, **changes: Any) -> ObservationSession:
        """Write a change of the order, exposures, framing or checklist.

        Returns
        -------
        session : `ObservationSession`
            The stored session.
        """
        self.calls.append(("update", changes))
        return self._write(session)


@pytest.fixture
def plan_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[_Sessions, _Sockets]:
    """Put a plan service over the stand-ins into the container.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.

    Returns
    -------
    setup : `tuple` [`_Sessions`, `_Sockets`]
        The stand-in sessions object and socket manager.
    """
    planning, sockets = _Sessions(), _Sockets()
    service = PlanService(SimpleNamespace(planning=SimpleNamespace(sessions=planning)), sockets)
    monkeypatch.setattr(container, "plan_service", service, raising=False)
    return planning, sockets


def test_the_methods_and_the_event_are_declared_and_served() -> None:
    """The tick method and the event are declared; the tick has a handler."""
    assert "plan:tick_checklist_item" in RPC_METHODS
    assert rpc_registry._handlers["plan:tick_checklist_item"] == ("plan_service", "tick_checklist_item")
    assert "plan:changed" in [event.name for event in UI_EVENTS]


def test_an_edit_announces_once_with_the_new_revision(plan_setup: tuple[_Sessions, _Sockets]) -> None:
    """One edit gives one event with the session, revision and change kinds."""
    planning, sockets = plan_setup
    result = container.plan_service.edit_plan("night-1", remove=["entry-1"], expected_revision=3)
    assert result.revision == 4
    assert sockets.events == [
        ("plan:changed", {"sessionId": "night-1", "revision": 4, "changed": ["queue_removed"]})
    ]
    assert planning.calls[0][1]["entries"] == ["entry-1"]


@pytest.mark.anyio
async def test_a_tick_announces_once(plan_setup: tuple[_Sessions, _Sockets]) -> None:
    """A tick gives one event, and the tick time is parsed from text."""
    planning, sockets = plan_setup
    reply = await rpc_registry.execute(
        "plan:tick_checklist_item",
        {
            "session_id": "night-1",
            "item_id": "item-1",
            "ticked_at": "2026-10-24T03:00:00+00:00",
            "note": "done",
        },
    )
    assert sockets.events == [
        ("plan:changed", {"sessionId": "night-1", "revision": 4, "changed": ["checklist_ticked"]})
    ]
    [item] = planning.calls[0][1]["checklist"]
    assert (item.ticked_at.year, item.note) == (2026, "done")
    assert (reply["previousRevision"], reply["revision"], reply["session"].id) == (3, 4, "night-1")
    assert reply["changes"] == [{"kind": "checklist_ticked", "detail": "focus", "entryId": "item-1"}]


@pytest.mark.anyio
async def test_a_tick_of_an_unknown_item_is_not_found(plan_setup: tuple[_Sessions, _Sockets]) -> None:
    """An item the session does not hold is refused, and nothing is written."""
    planning, sockets = plan_setup
    response = await rpc_router.handle_rpc(
        RPCRequest(method="plan:tick_checklist_item", params={"session_id": "night-1", "item_id": "x"}, id=9)
    )
    assert json.loads(response.body)["error"]["data"]["code"] == "not_found"
    assert planning.calls == []
    assert sockets.events == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("plan:tick_checklist_item", {"session_id": "night-1", "item_id": "item-1", "expected_revision": 1}),
    ],
)
async def test_a_stale_revision_is_a_conflict_and_announces_nothing(
    plan_setup: tuple[_Sessions, _Sockets], method: str, params: dict[str, Any]
) -> None:
    """A stale write returns the conflict error with the current revision."""
    _, sockets = plan_setup
    response = await rpc_router.handle_rpc(RPCRequest(method=method, params=params, id=7))
    error = json.loads(response.body)["error"]
    assert error["data"]["code"] == "conflict"
    assert error["data"]["details"]["current_revision"] == 3
    assert "revision" in error["message"]
    assert sockets.events == []


@pytest.mark.anyio
async def test_a_malformed_tick_time_is_an_invalid_argument(plan_setup: tuple[_Sessions, _Sockets]) -> None:
    """A tick time that is not ISO 8601 is refused and nothing is announced."""
    planning, sockets = plan_setup
    response = await rpc_router.handle_rpc(
        RPCRequest(
            method="plan:tick_checklist_item",
            params={"session_id": "night-1", "item_id": "item-1", "ticked_at": "last night"},
            id=8,
        )
    )
    error = json.loads(response.body)["error"]
    assert error["data"]["code"] == "invalid_argument"
    assert "ISO 8601" in error["message"]
    assert planning.calls == []
    assert sockets.events == []


def test_a_write_that_changed_nothing_announces_nothing() -> None:
    """When the revision did not move, no event is sent."""
    sockets = _Sockets()
    planning = _Sessions()
    planning.changes_nothing = True
    service = PlanService(SimpleNamespace(planning=SimpleNamespace(sessions=planning)), sockets)

    session = service.edit_plan("night-1", add_checklist=[{"rule": "focus"}])

    assert session.revision == 3
    assert sockets.events == []
