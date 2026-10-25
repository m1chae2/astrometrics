"""Purpose: Serve the night plan and package read and write methods.

Description: The night plan is an observing session stored by the planning
library. The app reads it with `Wayfinder.planning.get_plan`, edits it with
`edit_queue` and ticks checklist items with `tick_checklist_item`. The same
`get_plan` lists the observation package library, `create_plan` records a
package or the night's session, and `edit_package` changes, reopens or
deletes a package. This service wraps those calls for the RPC route and does
one more thing: after each write that succeeds, it sends the ``plan:changed``
event (session writes) or the ``packages:changed`` event (package writes) to
every connected app window, so any open screen can read again.

The event payload is ``{sessionId, revision, changed}``. ``revision`` is the
plan revision now stored. ``changed`` lists the kind of each change the write
made, such as ``queue_added`` or ``checklist_ticked``. Reads send no event.
A write that changes nothing (for example, adding a checklist item that is
already on the list) keeps the revision and sends no event. A write that
names an out-of-date revision stores nothing and sends no event;
the library raises a conflict error, and this service lets it pass up so the
caller gets the ``conflict`` error code with the current revision in its
details.

The ``packages:changed`` payload is ``{packageId, targetId, kind, revision}``.
``kind`` is ``created``, ``edited``, ``reopened``, ``deleted``,
``dismissed``, ``undismissed``, ``framing`` (only the framing changed) or
``promoted`` (a suggested package added to the queue). The suggestion refresh
sends ``refreshed`` instead (see `suggestion_refresh_worker`).
The current session is tonight's: the night comes from the library's
`observing_night_id`, never from the app.

The event comes from the change record or the package the library returns,
never from a second look at the database.
"""

import logging
import time
from datetime import datetime
from typing import Any

from astrometricslib import InvalidArgumentError, observing_night_id
from wayfindinglib import DitherConfig, ExposureRequest, ObservationPackage, PlanChange

logger = logging.getLogger(__name__)


class PlanService:
    """Read and change the night plan, and announce each change."""

    def __init__(self, wayfinder: Any, socket_manager: Any = None, refresh_worker: Any = None) -> None:
        """Keep the Wayfinder that does the work and the event sender.

        Parameters
        ----------
        wayfinder : `wayfindinglib.Wayfinder`
            The shared Wayfinder.
        socket_manager : `SocketManager`, optional
            Sends events to the app windows. With none, nothing is announced.
        refresh_worker : `SuggestionRefreshWorker`, optional
            Runs the suggestion refresh. With none, `refresh_suggestions`
            starts nothing.
        """
        self.wayfinder = wayfinder
        self._socket_manager = socket_manager
        self._refresh_worker = refresh_worker

    def get_plan(self, session_id: str | None = None) -> Any:
        """Read one night plan, or list every recorded one.

        Parameters
        ----------
        session_id : `str`, optional
            The session to read. When left out, one summary line per session.

        Returns
        -------
        plan : `ObservationSession` or `list` [`ObservationSessionSummary`]
            The whole session, or the list of summaries.
        """
        return self.wayfinder.planning.get_plan(session_id)

    def list_packages(
        self, status: str | None = None, target: str | None = None, include_dismissed: bool = False
    ) -> list[ObservationPackage]:
        """List the observation package library, newest first.

        Parameters
        ----------
        status : `str`, optional
            Keep packages with this status.
        target : `str`, optional
            Keep packages for this target.
        include_dismissed : `bool`, optional
            Also list suggestions the user dismissed. They are left out by
            default.

        Returns
        -------
        packages : `list` [`ObservationPackage`]
            The matching packages.
        """
        return self.wayfinder.planning.get_plan(
            kind="packages", status=status, target=target, include_dismissed=include_dismissed
        )

    def current_session(self) -> Any:
        """Read tonight's session, the one session the app works with.

        Returns
        -------
        session : `ObservationSessionWithProgress` or `None`
            Tonight's session with its progress (the frames requested and
            captured for each entry), or `None` if none was created yet.
            Reading never creates one.
        """
        return self.wayfinder.planning.get_plan(
            night_id=observing_night_id(time.time()), include=["progress"]
        )

    def refresh_suggestions(self, target_ids: list[str] | None = None) -> dict[str, Any]:
        """Start a refresh of the suggested packages and the checklist.

        The refresh runs in the background, one run at a time. Its result
        arrives as a ``packages:changed`` event (kind ``refreshed``) and, if
        the checklist grew, a ``plan:changed`` event.

        Parameters
        ----------
        target_ids : `list` [`str`], optional
            The targets whose data changed. Left out, every target.

        Returns
        -------
        result : `dict`
            ``{"started": True}`` when a worker took the request.
        """
        if self._refresh_worker is None:
            return {"started": False}
        self._refresh_worker.request(target_ids)
        return {"started": True}

    def create_plan(self, night_id: str | None = None) -> Any:
        """Create an empty night plan to fill by hand.

        Parameters
        ----------
        night_id : `str`, optional
            The night the plan is for. The library chooses when left out.
            The night's existing session comes back if it has one.

        Returns
        -------
        plan : `ObservationSession`
            The night's session.
        """
        return self.wayfinder.planning.create_plan(kind="empty_session", night_id=night_id)

    def create_package(
        self,
        target: str,
        exposure_requests: list[dict[str, Any]] | None = None,
        plan_items: list[dict[str, Any]] | None = None,
        dither_config: dict[str, Any] | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int | None = None,
        notes: str | None = None,
    ) -> ObservationPackage:
        """Record an observation package and announce it.

        Parameters
        ----------
        target : `str`
            The target's name.
        exposure_requests : `list` [`dict`], optional
            The exposures it asks for. None or empty gives a package with none.
        plan_items : `list` [`dict`], optional
            Sequence items instead of `exposure_requests`.
        dither_config : `dict`, optional
            How it dithers.
        minimum_altitude_deg : `float`, optional
            The lowest altitude it may be imaged at.
        priority : `int`, optional
            Higher is placed first.
        notes : `str`, optional
            Free text.

        Returns
        -------
        package : `ObservationPackage`
            The stored package.
        """
        package = self.wayfinder.planning.create_plan(
            kind="package",
            target=target,
            plan_items=plan_items,
            exposure_requests=(
                None
                if exposure_requests is None
                else [ExposureRequest.model_validate(item) for item in exposure_requests]
            ),
            dither_config=None if dither_config is None else DitherConfig.model_validate(dither_config),
            minimum_altitude_deg=minimum_altitude_deg,
            priority=priority,
            notes=notes,
        )
        self._announce_package(package, "created")
        return package

    def edit_package(
        self,
        package_id: str,
        exposure_requests: list[dict[str, Any]] | None = None,
        dither_config: dict[str, Any] | None = None,
        minimum_altitude_deg: float | None = None,
        priority: int | None = None,
        notes: str | None = None,
        name: str | None = None,
        framing: dict[str, Any] | None = None,
        reopen: bool = False,
        delete: bool = False,
        dismiss: bool = False,
        undismiss: bool = False,
        source_key: str | None = None,
        expected_revision: int | None = None,
    ) -> ObservationPackage:
        """Change, reopen, dismiss or delete a package and announce it.

        Parameters
        ----------
        package_id : `str`
            The package to change.
        exposure_requests : `list` [`dict`], optional
            The new full list of exposures.
        dither_config : `dict`, optional
            The new dithering.
        minimum_altitude_deg : `float`, optional
            The new lowest altitude.
        priority : `int`, optional
            The new priority.
        notes : `str`, optional
            The new free text.
        name : `str`, optional
            The new package name.
        framing : `dict`, optional
            The field center, camera rotation and mosaic panels to keep with
            the package.
        reopen : `bool`, optional
            Set a complete package back to planned.
        delete : `bool`, optional
            Remove the package.
        dismiss : `bool`, optional
            Hide a suggested package (or one of its rows, with `source_key`)
            until the evidence for it changes.
        undismiss : `bool`, optional
            Bring a dismissed suggestion back.
        source_key : `str`, optional
            The row of a suggestion to dismiss or bring back.
        expected_revision : `int`, optional
            The package revision the caller last read. A different stored
            revision raises `PlanRevisionConflictError`.

        Returns
        -------
        package : `ObservationPackage`
            The stored package (as it was when removed, for a delete).
        """
        package = self.wayfinder.planning.edit_package(
            package_id,
            exposure_requests=exposure_requests,
            dither_config=dither_config,
            minimum_altitude_deg=minimum_altitude_deg,
            priority=priority,
            notes=notes,
            name=name,
            framing=framing,
            reopen=reopen,
            delete=delete,
            dismiss=dismiss,
            undismiss=undismiss,
            source_key=source_key,
            expected_revision=expected_revision,
        )
        if delete:
            kind = "deleted"
        elif reopen:
            kind = "reopened"
        elif dismiss:
            kind = "dismissed"
        elif undismiss:
            kind = "undismissed"
        elif framing is not None and not _changes_more_than_framing(
            exposure_requests, dither_config, minimum_altitude_deg, priority, notes, name
        ):
            kind = "framing"
        else:
            kind = "edited"
        self._announce_package(package, kind)
        return package

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
        self._announce_promotions(change)
        self._socket_manager.broadcast_ui_event_sync(
            "plan:changed",
            {
                "sessionId": change.session_id,
                "revision": change.revision,
                "changed": [item.kind for item in change.changes],
            },
        )

    def _announce_promotions(self, change: PlanChange) -> None:
        """Announce each suggested package a write promoted.

        Parameters
        ----------
        change : `PlanChange`
            What the library returned for the write. A ``package_promoted``
            item carries the package id in its detail.
        """
        if self._socket_manager is None:
            return
        for item in change.changes:
            if item.kind != "package_promoted":
                continue
            self._socket_manager.broadcast_ui_event_sync(
                "packages:changed", {"packageId": item.detail, "targetId": None, "kind": "promoted"}
            )

    def _announce_package(self, package: ObservationPackage, kind: str) -> None:
        """Announce one successful package write.

        Parameters
        ----------
        package : `ObservationPackage`
            The package the library returned.
        kind : `str`
            ``created``, ``edited``, ``reopened``, ``deleted``, ``dismissed``,
            ``undismissed`` or ``framing``.
        """
        if self._socket_manager is None:
            return
        self._socket_manager.broadcast_ui_event_sync(
            "packages:changed",
            {
                "packageId": package.id,
                "targetId": package.target_id,
                "kind": kind,
                "revision": package.revision,
            },
        )


def _changes_more_than_framing(*values: Any) -> bool:
    """Tell whether an edit set any value besides the framing.

    Parameters
    ----------
    *values : `Any`
        The other edit arguments. `None` means the caller left it out.

    Returns
    -------
    more : `bool`
        `True` if any of them was given.
    """
    return any(value is not None for value in values)
