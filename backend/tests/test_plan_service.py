"""Purpose: Test the night plan RPC methods and the ``plan:changed`` event.

Description: The plan service wraps the planning library's `get_plan`,
`edit_queue` and `tick_checklist_item`. The tests use a stand-in planning
library and a stand-in socket manager to check that:

* the three methods are declared, served and in the generated event list,
* each successful write announces ``plan:changed`` once with the session id,
  the new revision and the kinds of change,
* a read announces nothing,
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
from wayfindinglib import (
    ObservationSession,
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


class _Planning:
    """A stand-in planning API that keeps a revision and can refuse a write."""

    def __init__(self) -> None:
        """Start at revision 3 with one session."""
        self.revision = 3
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _session(self) -> ObservationSession:
        """Build the stored session at the current revision.

        Returns
        -------
        session : `ObservationSession`
            A minimal session.
        """
        return ObservationSession(
            id="night-1",
            night_date=date(2026, 10, 24),
            site_profile_id="site",
            telescope_id="scope",
            camera_id="camera",
            revision=self.revision,
        )

    def _write(self, kind: str, expected_revision: int | None) -> PlanChange:
        """Apply a write unless the expected revision is stale.

        Parameters
        ----------
        kind : `str`
            The kind of change to report.
        expected_revision : `int` or `None`
            The revision the caller last read.

        Returns
        -------
        change : `PlanChange`
            The change record.

        Raises
        ------
        PlanRevisionConflictError
            If the expected revision is not the stored one.
        """
        if expected_revision is not None and expected_revision != self.revision:
            raise PlanRevisionConflictError(
                "The plan is at a newer revision.",
                details={"current_revision": self.revision, "expected_revision": expected_revision},
            )
        previous = self.revision
        self.revision += 1
        return PlanChange(
            session_id="night-1",
            previous_revision=previous,
            revision=self.revision,
            changes=[PlanChangeItem(kind=kind)],
            session=self._session(),
        )

    def get_plan(self, session_id: str | None = None, **filters: Any) -> ObservationSession:
        """Read the session.

        Returns
        -------
        session : `ObservationSession`
            The stored session.
        """
        self.calls.append(("get_plan", {"session_id": session_id}))
        return self._session()

    def edit_queue(self, session_id: str, **arguments: Any) -> PlanChange:
        """Write a queue change.

        Returns
        -------
        change : `PlanChange`
            The change record.
        """
        self.calls.append(("edit_queue", {"session_id": session_id, **arguments}))
        return self._write("queue_added", arguments.get("expected_revision"))

    def tick_checklist_item(self, session_id: str, item_id: str, **arguments: Any) -> PlanChange:
        """Write a tick.

        Returns
        -------
        change : `PlanChange`
            The change record.
        """
        self.calls.append((
            "tick_checklist_item",
            {"session_id": session_id, "item_id": item_id, **arguments},
        ))
        return self._write("checklist_ticked", arguments.get("expected_revision"))


@pytest.fixture
def plan_setup(monkeypatch: pytest.MonkeyPatch) -> tuple[_Planning, _Sockets]:
    """Put a plan service over the stand-ins into the container.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.

    Returns
    -------
    setup : `tuple` [`_Planning`, `_Sockets`]
        The stand-in planning API and socket manager.
    """
    planning, sockets = _Planning(), _Sockets()
    service = PlanService(SimpleNamespace(planning=planning), sockets)
    monkeypatch.setattr(container, "plan_service", service, raising=False)
    return planning, sockets


def test_the_methods_and_the_event_are_declared_and_served() -> None:
    """Three methods and the event are declared; each method has a handler."""
    for method, handler in (
        ("plan:get", "get_plan"),
        ("plan:edit", "edit_plan"),
        ("plan:tick_checklist_item", "tick_checklist_item"),
    ):
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method] == ("plan_service", handler)
    assert "plan:changed" in [event.name for event in UI_EVENTS]


@pytest.mark.anyio
async def test_a_read_announces_nothing(plan_setup: tuple[_Planning, _Sockets]) -> None:
    """Reading the plan sends no event."""
    _, sockets = plan_setup
    result = await rpc_registry.execute("plan:get", {"session_id": "night-1"})
    assert result.revision == 3
    assert sockets.events == []


@pytest.mark.anyio
async def test_an_edit_announces_once_with_the_new_revision(plan_setup: tuple[_Planning, _Sockets]) -> None:
    """One edit gives one event with the session, revision and change kinds."""
    planning, sockets = plan_setup
    result = await rpc_registry.execute(
        "plan:edit", {"session_id": "night-1", "remove": ["entry-1"], "expected_revision": 3}
    )
    assert result.revision == 4
    assert sockets.events == [
        ("plan:changed", {"sessionId": "night-1", "revision": 4, "changed": ["queue_added"]})
    ]
    assert planning.calls[0][1]["remove"] == ["entry-1"]


@pytest.mark.anyio
async def test_a_tick_announces_once(plan_setup: tuple[_Planning, _Sockets]) -> None:
    """A tick gives one event, and the tick time is parsed from text."""
    planning, sockets = plan_setup
    await rpc_registry.execute(
        "plan:tick_checklist_item",
        {"session_id": "night-1", "item_id": "item-1", "ticked_at": "2026-10-24T03:00:00+00:00"},
    )
    assert sockets.events == [
        ("plan:changed", {"sessionId": "night-1", "revision": 4, "changed": ["checklist_ticked"]})
    ]
    assert planning.calls[0][1]["ticked_at"].year == 2026


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "params"),
    [
        ("plan:edit", {"session_id": "night-1", "remove": ["entry-1"], "expected_revision": 1}),
        ("plan:tick_checklist_item", {"session_id": "night-1", "item_id": "item-1", "expected_revision": 1}),
    ],
)
async def test_a_stale_revision_is_a_conflict_and_announces_nothing(
    plan_setup: tuple[_Planning, _Sockets], method: str, params: dict[str, Any]
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
async def test_a_malformed_tick_time_is_an_invalid_argument(plan_setup: tuple[_Planning, _Sockets]) -> None:
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
    planning = _Planning()

    def unchanged(session_id: str, **arguments: Any) -> PlanChange:
        """Answer as the library does for an edit with nothing to change.

        Returns
        -------
        change : `PlanChange`
            A change whose revision equals the previous one.
        """
        return PlanChange(
            session_id="night-1",
            previous_revision=3,
            revision=3,
            changes=[PlanChangeItem(kind="checklist_already_present")],
            session=planning._session(),
        )

    planning.edit_queue = unchanged
    service = PlanService(SimpleNamespace(planning=planning), sockets)

    change = service.edit_plan("night-1", add_checklist=[{"rule": "focus"}])

    assert change.revision == 3
    assert sockets.events == []
