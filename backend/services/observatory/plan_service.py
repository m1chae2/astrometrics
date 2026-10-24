"""Purpose: Serve the night plan's read, edit and tick methods.

Description: The night plan is an observing session stored by the planning
library. The app reads it with `Wayfinder.planning.get_plan`, edits it with
`edit_queue` and ticks checklist items with `tick_checklist_item`. This
service wraps those three calls for the RPC route and does one more thing:
after each write that succeeds, it sends the ``plan:changed`` event to every
connected app window, so any open screen can read the plan again.

The event payload is ``{sessionId, revision, changed}``. ``revision`` is the
plan revision now stored. ``changed`` lists the kind of each change the write
made, such as ``queue_added`` or ``checklist_ticked``. Reads send no event.
A write that changes nothing (for example, adding a checklist item that is
already on the list) keeps the revision and sends no event. A write that
names an out-of-date revision stores nothing and sends no event;
the library raises a conflict error, and this service lets it pass up so the
caller gets the ``conflict`` error code with the current revision in its
details.

The event comes from the change record the library returns, never from a
second look at the database.
"""

import logging
from datetime import datetime
from typing import Any

from astrometricslib import InvalidArgumentError
from wayfindinglib import PlanChange

logger = logging.getLogger(__name__)


class PlanService:
    """Read and change the night plan, and announce each change."""

    def __init__(self, wayfinder: Any, socket_manager: Any = None) -> None:
        """Keep the Wayfinder that does the work and the event sender.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        socket_manager : `SocketManager`, optional
            Sends events to the app windows. With none, nothing is announced.
        """
        self.wayfinder = wayfinder
        self._socket_manager = socket_manager

    def get_plan(self, session_id: str | None = None) -> Any:
        """Read one night plan, or list every recorded one.

        Parameters
        ----------
        session_id : `str`, optional
            The session to read. When left out, one summary line per session.

        Returns
        -------
        plan : `ObservationSession` or `list` [`ObservationSessionSummary`]
            The whole session with its checklist, framing and revision, or
            the list of summaries.
        """
        return self.wayfinder.planning.get_plan(session_id)

    def edit_plan(
        self,
        session_id: str,
        add: list[dict[str, Any]] | None = None,
        order: list[str] | None = None,
        remove: list[str] | None = None,
        set_exposures: dict[str, list[dict[str, Any]]] | None = None,
        add_checklist: list[dict[str, Any]] | None = None,
        framing: dict[str, Any] | None = None,
        expected_revision: int | None = None,
    ) -> PlanChange:
        """Change the night plan by hand and announce the change.

        Parameters
        ----------
        session_id : `str`
            The session to change.
        add : `list` [`dict`], optional
            Packages to append to the queue (a `QueueRequest` each).
        order : `list` [`str`], optional
            Every entry id left after `remove`, in the new order.
        remove : `list` [`str`], optional
            Entry ids to take out of the queue.
        set_exposures : `dict` [`str`, `list` [`dict`]], optional
            New exposure requests for existing entries, by entry id.
        add_checklist : `list` [`dict`], optional
            Checklist items to add.
        framing : `dict`, optional
            The field center, camera rotation and mosaic panels to store.
        expected_revision : `int`, optional
            The plan revision the caller last read. A different stored
            revision means nothing is written, and the library's
            `PlanRevisionConflictError` passes up with ``current_revision``
            in its details.

        Returns
        -------
        change : `PlanChange`
            The new revision, what changed and the stored session.
        """
        change = self.wayfinder.planning.edit_queue(
            session_id,
            add=add,
            order=order,
            remove=remove,
            set_exposures=set_exposures,
            add_checklist=add_checklist,
            framing=framing,
            expected_revision=expected_revision,
        )
        self._announce(change)
        return change

    def tick_checklist_item(
        self,
        session_id: str,
        item_id: str,
        expected_revision: int | None = None,
        note: str | None = None,
        ticked_at: str | None = None,
    ) -> PlanChange:
        """Record that the user did one checklist item, and announce it.

        Parameters
        ----------
        session_id : `str`
            The session whose checklist holds the item.
        item_id : `str`
            The item to tick.
        expected_revision : `int`, optional
            The plan revision the caller last read. A different stored
            revision raises `PlanRevisionConflictError`.
        note : `str`, optional
            A note to keep with the item.
        ticked_at : `str`, optional
            When the user did it, as an ISO 8601 time. Now when left out.

        Returns
        -------
        change : `PlanChange`
            The new revision and the tick.

        Raises
        ------
        InvalidArgumentError
            If `ticked_at` is not an ISO 8601 time.
        """
        try:
            moment = datetime.fromisoformat(ticked_at) if ticked_at else None
        except ValueError as error:
            raise InvalidArgumentError(
                f"ticked_at must be an ISO 8601 time, such as 2026-10-24T22:30:00+00:00; got {ticked_at!r}.",
                details={"ticked_at": ticked_at},
            ) from error
        change = self.wayfinder.planning.tick_checklist_item(
            session_id,
            item_id,
            expected_revision=expected_revision,
            note=note,
            ticked_at=moment,
        )
        self._announce(change)
        return change

    def _announce(self, change: PlanChange) -> None:
        """Send the ``plan:changed`` event for one successful write.

        Parameters
        ----------
        change : `PlanChange`
            What the library returned for the write.
        """
        if self._socket_manager is None:
            return
        if change.revision == change.previous_revision:
            # Nothing was stored, so there is nothing to announce.
            return
        self._socket_manager.broadcast_ui_event_sync(
            "plan:changed",
            {
                "sessionId": change.session_id,
                "revision": change.revision,
                "changed": [item.kind for item in change.changes],
            },
        )
