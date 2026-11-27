"""Purpose: Serve the night plan and package read and write methods.

Description: The night plan is an observing session stored by the planning
library, one per night. The app reads it with
``Wayfinder.planning.sessions.get`` and edits it with ``sessions.add``,
``remove`` and ``update`` (a checklist tick is an ``update`` of the
checklist). ``planning.packages`` lists, records and changes the observation
packages. This service is the adapter between the RPC route and those
objects: it turns the ids and JSON the app sends into the library's objects
and input models (the revision the app last read goes onto the object),
shapes each reply as the app has always received it, and after each write
that succeeds sends the ``plan:changed`` event (session writes) or the
``packages:changed`` event (package writes) to every connected app window,
so any open screen can read again.

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

The event comes from the session or the package the library returns, never
from a second look at the database.
"""

import logging
import time
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from astrometricslib import FilterType, Target
from astrometricslib.arguments import observing_night_id
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.subject_resolver import SubjectResolver
from wayfindinglib import (
    ChecklistItem,
    DitherConfig,
    ExposureRequest,
    Framing,
    ObservationPackage,
    ObservationSession,
    QueueRequest,
)

logger = logging.getLogger(__name__)


SESSION_PARTS = ["progress", "guiding_samples", "weather_samples", "divergence_records"]
"""Everything a session read can add: the current-session reply has always
carried the counted progress and the stored sample lists."""


def _model_list(model: type, items: list[Any] | None, message: str) -> list[Any]:
    """Build input models from the JSON the app sent.

    Parameters
    ----------
    model : `type`
        The pydantic model class.
    items : `list` or `None`
        The JSON objects (or models).
    message : `str`
        The error message for a value that does not fit.

    Returns
    -------
    models : `list`
        One model per item; empty for `None`.

    Raises
    ------
    InvalidArgumentError
        If an item does not fit the model.
    """
    try:
        return [model.model_validate(item) for item in items or []]
    except ValidationError as error:
        raise InvalidArgumentError(message, details={"error": str(error)}) from error


def _filter_type(name: str) -> FilterType:
    """Read a filter name, such as ``"L"`` or ``"Ha"``, as a `FilterType`.

    Parameters
    ----------
    name : `str`
        A filter's value or short name, in any case.

    Returns
    -------
    filter_type : `FilterType`
        The filter.

    Raises
    ------
    InvalidArgumentError
        If the name matches no filter.
    """
    wanted = name.strip().lower()
    for member_name, member in FilterType.__members__.items():
        if wanted in (member_name.lower(), member.value.lower()):
            return member
    raise InvalidArgumentError(
        f"Unknown filter {name!r}. Choose from: {', '.join(FilterType.__members__)}.",
        details={"filter": name},
    )


def exposures_from_plan_items(plan_items: list[dict[str, Any]]) -> list[ExposureRequest]:
    """Turn the sequencer's items into the exposure rows of a package.

    Parameters
    ----------
    plan_items : `list` [`dict`]
        Each item's ``count``, ``exposure`` (seconds), ``filter`` (``"L"``
        when missing) and optional frame ``type`` (``"LIGHT"`` when missing).

    Returns
    -------
    rows : `list` [`ExposureRequest`]
        One row per item.

    Raises
    ------
    InvalidArgumentError
        If an item names an unknown filter or frame type, or has no exposure
        or count above zero.
    """
    rows = []
    for item in plan_items:
        frame_type = str(item.get("type") or "LIGHT").upper()
        if frame_type not in ("LIGHT", "DARK", "FLAT", "BIAS"):
            raise InvalidArgumentError(f"Unknown frame type {frame_type!r}.", details={"type": frame_type})
        try:
            rows.append(
                ExposureRequest(
                    frame_type=frame_type,
                    filter=_filter_type(str(item.get("filter") or "L")),
                    exposure_sec=float(item.get("exposure", 0.0)),
                    count=int(item.get("count", 0)),
                )
            )
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each item needs an exposure and a count above zero.", details={"item": dict(item)}
            ) from error
    return rows


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

    def _target(self, target: str, *, from_catalog: bool = False) -> Target:
        """Turn the target name the app sent into the library target.

        Parameters
        ----------
        target : `str`
            The target's id.
        from_catalog : `bool`, optional
            Add a target the library does not hold yet from the catalog, with
            its position.

        Returns
        -------
        target : `Target`
            The library target.

        Raises
        ------
        NotFoundError
            If the target is neither in the library nor (with
            ``from_catalog``) known to the catalog.
        """
        astrometrics = self.wayfinder.astrometrics
        found = astrometrics.targets.get(target)
        if found is not None:
            return found
        if not from_catalog:
            return SubjectResolver(astrometrics).target(target)
        try:
            return astrometrics.targets.create(target, from_catalog=True)
        except NotFoundError as error:
            raise NotFoundError(f"No target '{target}' in the library or the catalog: {error}") from error

    def _filter_target(self, target: str | None) -> Target | None:
        """Turn a target filter into a target, known to the library or not.

        Parameters
        ----------
        target : `str` or `None`
            The target id the app filters by.

        Returns
        -------
        target : `Target` or `None`
            The library target, or a bare one with that id (packages may name
            a target the library no longer holds).
        """
        if target is None:
            return None
        return self.wayfinder.astrometrics.targets.get(target) or Target(id=target)

    def _session(self, session_id: str, expected_revision: int | None = None) -> ObservationSession:
        """Find a stored session by id, with the revision the app last read.

        Parameters
        ----------
        session_id : `str`
            The session id.
        expected_revision : `int`, optional
            The revision the app last read. The library refuses the write
            when it is not the stored one. Left out, the stored revision.

        Returns
        -------
        session : `ObservationSession`
            The session.

        Raises
        ------
        NotFoundError
            If no session has that id.
        """
        session = next(
            (stored for stored in self.wayfinder.planning.sessions.query() if stored.id == session_id), None
        )
        if session is None:
            raise NotFoundError(f"No observing session {session_id!r}.", details={"session_id": session_id})
        if expected_revision is not None:
            session = session.model_copy(update={"revision": expected_revision})
        return session

    def _package(self, package_id: str, expected_revision: int | None = None) -> ObservationPackage:
        """Find a stored package by id, with the revision the app last read.

        Parameters
        ----------
        package_id : `str`
            The package id.
        expected_revision : `int`, optional
            The revision the app last read. Left out, the stored revision.

        Returns
        -------
        package : `ObservationPackage`
            The package.
        """
        package = self.wayfinder.planning.packages.get(package_id, required=True)
        if expected_revision is not None:
            package = package.model_copy(update={"revision": expected_revision})
        return package

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
        page = self.wayfinder.planning.packages.query(
            status=status, target=self._filter_target(target), dismissed=None if include_dismissed else False
        )
        return list(page)

    def package_for_target(self, target: str) -> ObservationPackage | None:
        """Read the one package that stands for a target.

        Parameters
        ----------
        target : `str`
            The target. The library matches it ignoring case, with ``_`` read
            as a space.

        Returns
        -------
        package : `ObservationPackage` or `None`
            The target's planned package, else its suggested package that is
            not dismissed, else `None`. The library does the choosing.
        """
        return self.wayfinder.planning.packages.get_for_target(self._filter_target(target))

    def current_session(self) -> ObservationSession | None:
        """Read tonight's session, the one session the app works with.

        Returns
        -------
        session : `ObservationSession` or `None`
            Tonight's session with its progress (the frames requested and
            captured for each entry) and its stored sample lists, or `None` if
            none was created yet. Reading never creates one.
        """
        return self.wayfinder.planning.sessions.get(observing_night_id(time.time()), include=SESSION_PARTS)

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

        A target the library does not hold yet is added from the catalog,
        with its position, first.

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

        Raises
        ------
        InvalidArgumentError
            If both `exposure_requests` and `plan_items` are given, or a value
            is malformed.
        """
        if exposure_requests is not None and plan_items is not None:
            raise InvalidArgumentError("Give exposure_requests or plan_items, not both.")
        if plan_items is not None:
            rows = exposures_from_plan_items(plan_items)
        else:
            rows = _model_list(ExposureRequest, exposure_requests, "A package value is not valid.")
        dither = (
            None
            if dither_config is None
            else _model_list(DitherConfig, [dither_config], "A package value is not valid.")[0]
        )
        package = self.wayfinder.planning.packages.create(
            self._target(target, from_catalog=True),
            rows,
            dither_config=dither,
            minimum_altitude_deg=minimum_altitude_deg,
            priority=priority or 0,
            notes=notes or "",
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

        One call makes one kind of write. Delete, dismiss and undismiss stand
        alone. A reopen may come with changes; both go in one write.

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

        Raises
        ------
        InvalidArgumentError
            If nothing is asked for, or delete, dismiss or undismiss is
            mixed with another change.
        """
        packages = self.wayfinder.planning.packages
        changes: dict[str, Any] = {
            "exposure_requests": exposure_requests,
            "dither_config": dither_config,
            "minimum_altitude_deg": minimum_altitude_deg,
            "priority": priority,
            "notes": notes,
            "name": name,
            "framing": framing,
        }
        changes = {key: value for key, value in changes.items() if value is not None}
        flags = [
            label
            for label, wanted in (("delete", delete), ("dismiss", dismiss), ("undismiss", undismiss))
            if wanted
        ]
        if flags and (changes or reopen or len(flags) > 1):
            raise InvalidArgumentError(
                f"{flags[0]} cannot be combined with a change or with delete, dismiss or undismiss."
            )
        if source_key is not None and not (dismiss or undismiss):
            raise InvalidArgumentError("source_key is used only with dismiss or undismiss.")
        if not (flags or changes or reopen):
            raise InvalidArgumentError(
                "Give exposure_requests, dither_config, minimum_altitude_deg, priority, notes, name, "
                "framing, reopen, delete, dismiss or undismiss."
            )
        package = self._package(package_id, expected_revision)
        if delete:
            packages.delete(package)
        elif dismiss:
            if source_key is None:
                package = packages.update(package, dismissed=True)
            else:
                package = packages.update(package, dismissed_rows=[source_key])
        elif undismiss:
            package = packages.restore(package, None if source_key is None else [source_key])
        else:
            if "exposure_requests" in changes:
                changes["exposure_requests"] = _model_list(
                    ExposureRequest, changes["exposure_requests"], "A package value is not valid."
                )
            if reopen:
                changes["status"] = "PLANNED"
            package = packages.update(package, **changes)
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
    ) -> ObservationSession:
        """Change the night plan by hand and announce the change.

        One call makes one kind of change: give exactly one of `add`,
        `order`, `remove`, `set_exposures`, `add_checklist` or `framing`.

        Parameters
        ----------
        session_id : `str`
            The session to change.
        add : `list` [`dict`], optional
            Packages to append to the queue. Each is a dictionary with a
            ``package_id`` and a start mode.
        order : `list` [`str`], optional
            Every entry id, in the new order.
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
        session : `ObservationSession`
            The stored session.

        Raises
        ------
        InvalidArgumentError
            If not exactly one kind of change is given, or ``order`` does not
            name exactly the entries.
        NotFoundError
            If ``remove`` names an entry that is not in the queue.
        """
        sessions = self.wayfinder.planning.sessions
        given = {
            name_: value
            for name_, value in (
                ("add", add),
                ("order", order),
                ("remove", remove),
                ("set_exposures", set_exposures),
                ("add_checklist", add_checklist),
                ("framing", framing),
            )
            if value is not None
        }
        if len(given) != 1:
            raise InvalidArgumentError(
                "Give exactly one of add, order, remove, set_exposures, add_checklist or framing.",
                details={"given": sorted(given)},
            )
        session = self._session(session_id, expected_revision)
        entries = {entry.id: entry for entry in session.queue}
        promoted: list[str] = []
        if add is not None:
            requests = _model_list(QueueRequest, add, "Each entry to add needs a package_id.")
            for request in requests:
                package = self.wayfinder.planning.packages.get(request.package_id)
                if package is not None and package.status == "SUGGESTED" and package.id not in promoted:
                    promoted.append(package.id)
            changed = sessions.add(session, requests)
            kinds = ["queue_added"] * len(requests) + ["package_promoted"] * len(promoted)
        elif order is not None:
            if sorted(order) != sorted(entries):
                raise InvalidArgumentError(
                    "order must name exactly the entries currently in the queue.",
                    details={"order": list(order), "entries": sorted(entries)},
                )
            changed = sessions.update(session, order=[entries[entry_id] for entry_id in order])
            kinds = ["queue_reordered"]
        elif remove is not None:
            missing = [entry_id for entry_id in remove if entry_id not in entries]
            if missing:
                raise NotFoundError("Those entries are not in the queue.", details={"remove": missing})
            changed = sessions.remove(session, [entries[entry_id] for entry_id in remove])
            kinds = ["queue_removed"] * len(remove)
        elif set_exposures is not None:
            exposures = {
                entry_id: _model_list(
                    ExposureRequest, rows, "Each exposure request needs frame_type, exposure_sec and count."
                )
                for entry_id, rows in set_exposures.items()
            }
            changed = sessions.update(session, exposures=exposures)
            kinds = ["exposures_set"] * len(exposures)
        elif add_checklist is not None:
            items = _model_list(ChecklistItem, add_checklist, "Each checklist item needs a rule.")
            changed = sessions.update(session, checklist=[*session.checklist, *items])
            kinds = ["checklist_added"]
        else:
            framed = _model_list(Framing, [framing], "Framing needs center_ra_deg and center_dec_deg.")[0]
            changed = sessions.update(session, framing=framed)
            kinds = ["framing_set"]
        self._announce(session.revision, changed, kinds, promoted)
        return changed

    def tick_checklist_item(
        self,
        session_id: str,
        item_id: str,
        expected_revision: int | None = None,
        note: str | None = None,
        ticked_at: str | None = None,
    ) -> dict[str, Any]:
        """Record that the user did one checklist item, and announce it.

        The library ticks an item when its ``ticked_at`` changes in the
        checklist the session is updated with.

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
        change : `dict`
            The change record the app has always received: ``sessionId``,
            ``previousRevision``, ``revision``, ``changes`` (the tick),
            ``settingNotes`` and the stored ``session``.

        Raises
        ------
        InvalidArgumentError
            If `ticked_at` is not an ISO 8601 time.
        NotFoundError
            If the session has no such item.
        """
        try:
            moment = datetime.fromisoformat(ticked_at) if ticked_at else datetime.now(UTC)
        except ValueError as error:
            raise InvalidArgumentError(
                f"ticked_at must be an ISO 8601 time, such as 2026-10-24T22:30:00+00:00; got {ticked_at!r}.",
                details={"ticked_at": ticked_at},
            ) from error
        session = self._session(session_id, expected_revision)
        item = next((candidate for candidate in session.checklist if candidate.item_id == item_id), None)
        if item is None:
            raise NotFoundError(
                f"No checklist item {item_id!r} in session {session_id!r}.",
                details={"item_id": item_id, "session_id": session_id},
            )
        ticked = item.model_copy(update={"ticked_at": moment, **({} if note is None else {"note": note})})
        checklist = [ticked if candidate.item_id == item_id else candidate for candidate in session.checklist]
        changed = self.wayfinder.planning.sessions.update(session, checklist=checklist)
        self._announce(session.revision, changed, ["checklist_ticked"], [])
        return {
            "sessionId": changed.id,
            "previousRevision": session.revision,
            "revision": changed.revision,
            "changes": [{"kind": "checklist_ticked", "detail": item.rule, "entryId": item_id}],
            "settingNotes": [],
            "session": changed,
        }

    def _announce(
        self, previous_revision: int, session: ObservationSession, kinds: list[str], promoted: list[str]
    ) -> None:
        """Send the ``plan:changed`` event for one successful write.

        Parameters
        ----------
        previous_revision : `int`
            The revision the write started from.
        session : `ObservationSession`
            The session the library returned.
        kinds : `list` [`str`]
            The kind of each change, such as ``queue_added``.
        promoted : `list` [`str`]
            The suggested packages the write promoted.
        """
        if self._socket_manager is None:
            return
        if session.revision == previous_revision:
            # Nothing was stored, so there is nothing to announce.
            return
        for package_id in promoted:
            self._socket_manager.broadcast_ui_event_sync(
                "packages:changed", {"packageId": package_id, "targetId": None, "kind": "promoted"}
            )
        self._socket_manager.broadcast_ui_event_sync(
            "plan:changed", {"sessionId": session.id, "revision": session.revision, "changed": kinds}
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
