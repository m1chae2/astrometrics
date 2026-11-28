"""Purpose: Read and change the stored observing sessions (the night plans).

Description: The owner functions behind ``wayfinder.planning.sessions``. A
session is one observing night's plan: its queue of entries, its checklist and
its framing. There is one session per night.

* `get_session` and `query_sessions` read sessions. The heavy lists
  (guiding and weather samples, divergence records) and the counted progress
  come only when asked for.
* `create_session` records the night's session, empty or with packages placed
  automatically.
* `add_to_session`, `remove_from_session` and `update_session` change the plan.
  Each checks the revision the session object carries against the stored one,
  stores the change in one database step and returns the new session.
  `edit_session` is the shared step; the suggestion refresh uses it too.

Every exposure request put into a queue is merged and checked against the
camera (`night_plan_tasks`). A suggested package that is added to a queue
becomes planned.
"""

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from pydantic import ValidationError

from astrometricslib import ObservatoryConfiguration, Telescope
from astrometricslib.arguments import (
    Page,
    TimeInput,
    check_choice,
    check_include,
    check_limit,
    to_epoch_seconds,
)
from astrometricslib.exceptions import ConfigurationError, ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.analytics.frame_counting import TargetFrameReader, missing_requests
from wayfindinglib.models.planning.observation_package import (
    ExposureRequest,
    FrameType,
    ObservationPackage,
    PackageStatus,
)
from wayfindinglib.models.planning.suggestion_log import SuggestionEvent
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    Framing,
    ObservationSession,
    PlanChange,
    PlanChangeItem,
    PlanRevisionConflictError,
    QueuedObservationPackage,
    QueueEntryStatus,
    QueueRequest,
    SessionStatus,
)
from wayfindinglib.tasks.planning_tasks import planning_context
from wayfindinglib.tasks.planning_tasks.planning_context import (
    PlanningContext,
    equipment_names,
    load_package,
    load_session,
    log_suggestion_event,
    night_date,
    session_for_night,
    target_of,
)

__all__ = [
    "SESSION_PARTS",
    "add_to_session",
    "create_session",
    "edit_session",
    "get_session",
    "query_sessions",
    "remove_from_session",
    "update_session",
]

SESSION_PARTS = ("progress", "guiding_samples", "weather_samples", "divergence_records")
"""The parts a session read adds only when asked for with ``include=``."""

_HEAVY_LISTS = ("guiding_samples", "weather_samples", "divergence_records")
"""The stored lists a session read leaves empty unless asked for."""


def _shaped(
    context: PlanningContext,
    session: ObservationSession,
    parts: frozenset[str],
    reader: TargetFrameReader | None = None,
) -> ObservationSession:
    """Give a stored session the shape a read asked for.

    Each checklist item without a label gets its rule's purpose. The heavy
    lists are emptied unless asked for, and the progress is counted when
    asked for.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The stored session. It is not changed.
    parts : `frozenset` [`str`]
        The parts asked for, from `SESSION_PARTS`.
    reader : `TargetFrameReader`, optional
        Reads the frames for the progress; one is built when omitted.

    Returns
    -------
    session : `ObservationSession`
        A copy in the asked-for shape.
    """
    shaped = session.model_copy(deep=True)
    if not all(item.label for item in shaped.checklist):
        from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES

        purposes = {rule.name: rule.purpose for rule in ALL_RULES}
        for item in shaped.checklist:
            if not item.label:
                item.label = purposes.get(item.rule, item.rule)
    for name in _HEAVY_LISTS:
        if name not in parts:
            setattr(shaped, name, [])
    if "progress" in parts:
        from wayfindinglib.analytics.session_progress import build_session_progress

        shaped.progress = build_session_progress(
            session,
            reader or TargetFrameReader(context.astrometrics),
            equipment_names(context, session),
            planning_context.utc_now(),
        )
    return shaped


def get_session(
    context: PlanningContext, night: str | date, include: Sequence[str] = (), required: bool = False
) -> ObservationSession | None:
    """Read the session of one observing night.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    night : `str` or `datetime.date`
        The observing night, such as ``"2026-09-24"``.
    include : `Sequence` [`str`], optional
        Parts to add, from `SESSION_PARTS`.
    required : `bool`, optional
        Raise `NotFoundError` instead of returning `None`.

    Returns
    -------
    session : `ObservationSession` or `None`
        The night's session, or `None` when it has none. A night with several
        old sessions gives the one with the most queue entries, then the
        newest.

    Raises
    ------
    NotFoundError
        If ``required`` and the night has no session.

    Notes
    -----
    Raises `InvalidArgumentError` if the night is not a date or a part is
    unknown.
    Raises `NotFoundError` if ``required`` and the night has no session.
    """
    parts = check_include(include, SESSION_PARTS)
    wanted = night_date(night)
    session = session_for_night(context, wanted)
    if session is None:
        if required:
            raise NotFoundError(
                f"No observing session for the night {wanted}.", details={"night": str(wanted)}
            )
        return None
    return _shaped(context, session, parts)


def query_sessions(
    context: PlanningContext,
    status: SessionStatus | str | None = None,
    since: TimeInput | None = None,
    until: TimeInput | None = None,
    include: Sequence[str] = (),
    limit: int | None = None,
    offset: int = 0,
) -> Page[ObservationSession]:
    """List the stored sessions, newest night first.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    status : `SessionStatus` or `str`, optional
        Only sessions with this status.
    since, until : `TimeInput`, optional
        Only sessions whose night begins on or after ``since``'s date, and on
        or before ``until``'s date (UTC dates).
    include : `Sequence` [`str`], optional
        Parts to add to each session, from `SESSION_PARTS`.
    limit : `int`, optional
        Most sessions to return. `None` returns every match.
    offset : `int`, optional
        How many matches to skip first.

    Returns
    -------
    sessions : `Page` [`ObservationSession`]
        The matching sessions.

    Notes
    -----
    Raises `InvalidArgumentError` if the status, a time, a part, ``limit`` or
    ``offset`` is not valid.
    """
    from datetime import UTC, datetime

    parts = check_include(include, SESSION_PARTS)
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    if status is not None:
        check_choice("status", str(status), tuple(item.value for item in SessionStatus))
    first = None if since is None else datetime.fromtimestamp(to_epoch_seconds(since, "since"), UTC).date()
    last = None if until is None else datetime.fromtimestamp(to_epoch_seconds(until, "until"), UTC).date()
    sessions = [
        session
        for session in context.butler.get_all("observation_session")
        if (status is None or session.status == SessionStatus(str(status)))
        and (first is None or session.night_date >= first)
        and (last is None or session.night_date <= last)
    ]
    sessions.sort(key=lambda session: (session.night_date, session.created_at), reverse=True)
    page = Page.of(sessions, offset=offset, limit=limit)
    reader = TargetFrameReader(context.astrometrics) if "progress" in parts else None
    return Page(
        items=[_shaped(context, session, parts, reader) for session in page.items],
        total=page.total,
        next_offset=page.next_offset,
    )


def _session_equipment(
    context: PlanningContext, configuration: ObservatoryConfiguration | None
) -> tuple[Any, Telescope, str]:
    """Choose the site, telescope and camera a new session records.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    configuration : `ObservatoryConfiguration` or `None`
        The equipment, or `None` for the active telescope and camera.

    Returns
    -------
    site_profile, telescope, camera_id : `SiteProfile`, `Telescope`, `str`
        The recorded site and the equipment.

    Raises
    ------
    ConfigurationError
        If no configuration is given and no telescope or camera is active.
    """
    from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog
    from wayfindinglib.data_access.site_profile_reader import get_or_seed_default_site_profile

    site_profile = get_or_seed_default_site_profile(context.butler, context.config)
    if configuration is not None:
        return site_profile, configuration.telescope, configuration.camera.id
    catalog = get_equipment_catalog(context.config)
    telescope = catalog.active_telescope()
    camera = catalog.active_camera()
    if telescope is None or camera is None:
        raise ConfigurationError("An observing session needs a telescope and a camera, and none is active.")
    return site_profile, telescope, camera.id


def _schedule_session(
    context: PlanningContext,
    requests: list[QueueRequest],
    site_profile: Any,
    telescope: Telescope,
    camera_id: str,
    night: date,
) -> ObservationSession:
    """Place stored packages into a night, without storing the result.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    requests : `list` [`QueueRequest`]
        The packages to place, each with its start mode.
    site_profile : `SiteProfile`
        The observing site.
    telescope : `Telescope`
        The telescope.
    camera_id : `str`
        The camera, recorded on the session.
    night : `datetime.date`
        The local date the night begins.

    Returns
    -------
    session : `ObservationSession`
        The placed queue and the reasons any package was left out.
    """
    from wayfindinglib.tasks.planning_tasks.scheduling import schedule_session
    from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

    placements = []
    target_reports = {}
    for request in requests:
        package = load_package(context, request.package_id)
        placements.append((package, request.start_time_mode, request.requested_start_time))
        # One report for each target, however many packages name it.
        if package.quality_weighting_enabled and package.target_id not in target_reports:
            target_reports[package.target_id] = build_target_dataset_report(
                target_of(context, package.target_id),
                astrometrics=context.astrometrics,
                butler=context.butler,
                config=context.config,
            )
    return schedule_session(
        context.astrometrics,
        placements,
        site_profile,
        telescope,
        camera_id,
        night,
        target_reports=target_reports,
        planning_config=context.planning_config,
    )


def _replace_placement(
    context: PlanningContext, existing: ObservationSession, placed: ObservationSession
) -> ObservationSession:
    """Put a new automatic placement into a night's existing session.

    The session keeps its id, checklist, framing and history. Its queue, the
    reasons packages were left out, and its site, telescope and camera come
    from ``placed``.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    existing : `ObservationSession`
        The night's stored session.
    placed : `ObservationSession`
        The new placement, not stored.

    Returns
    -------
    session : `ObservationSession`
        The updated, stored session.

    Raises
    ------
    ConflictError
        If an entry of the existing queue has started.
    PlanRevisionConflictError
        If the session changed while this was running.
    """
    started = [entry.id for entry in existing.queue if entry.status != QueueEntryStatus.PENDING]
    if started:
        raise ConflictError(
            "This night's session has entries that already started, so it cannot be placed again.",
            details={"started": started},
        )
    loaded_revision = existing.revision
    existing.queue = placed.queue
    existing.unplaced_package_diagnostics = placed.unplaced_package_diagnostics
    existing.site_profile_id = placed.site_profile_id
    existing.telescope_id = placed.telescope_id
    existing.camera_id = placed.camera_id
    existing.revision = loaded_revision + 1
    if not context.butler.put_session_if_revision(existing, loaded_revision):
        stored = context.butler.get("observation_session", {"session_id": existing.id})
        raise PlanRevisionConflictError(
            "The plan changed while it was being placed. Read it again and redo the edit.",
            details={
                "current_revision": stored.revision if stored is not None else None,
                "expected_revision": loaded_revision,
            },
        )
    return existing


def _queue_request(item: ObservationPackage | QueueRequest) -> QueueRequest:
    """Turn a package or a queue request into a queue request.

    Parameters
    ----------
    item : `ObservationPackage` or `QueueRequest`
        A stored package (placed as soon as possible, with its own rows) or
        a request that says how to place it.

    Returns
    -------
    request : `QueueRequest`
        The request.

    Raises
    ------
    InvalidArgumentError
        If the item is neither.
    """
    if isinstance(item, QueueRequest):
        return item
    if isinstance(item, ObservationPackage):
        return QueueRequest(package_id=item.id)
    raise InvalidArgumentError(
        f"Give observation packages or QueueRequest objects, not {type(item).__name__}.",
        details={"item": repr(item)},
    )


def create_session(
    context: PlanningContext,
    night: str | date,
    configuration: ObservatoryConfiguration | None = None,
    packages: Sequence[ObservationPackage | QueueRequest] | None = None,
) -> ObservationSession:
    """Record the observing session of one night.

    With no ``packages`` the session starts with an empty queue; a night that
    already has a session gets that session back. With ``packages``, each is
    placed automatically, and a night that already has a session gets the new
    placement written into it.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    night : `str` or `datetime.date`
        The observing night, such as ``"2026-09-24"``.
    configuration : `ObservatoryConfiguration`, optional
        The telescope and camera. The active ones when omitted.
    packages : `Sequence`, optional
        Stored packages, or `QueueRequest` objects, to place automatically.

    Returns
    -------
    session : `ObservationSession`
        The stored session.

    Notes
    -----
    Raises `InvalidArgumentError` if the night is not a date or an item is
    neither a package nor a request.
    Raises `ConflictError` if an entry of the night's session has already
    started.
    """
    wanted = night_date(night)
    requests = None if packages is None else [_queue_request(item) for item in packages]
    if requests is None:
        existing = session_for_night(context, wanted)
        if existing is not None:
            return _shaped(context, existing, frozenset())
    site_profile, telescope, camera_id = _session_equipment(context, configuration)
    if requests is None:
        session = ObservationSession(
            id=str(uuid.uuid4()),
            night_date=wanted,
            status=SessionStatus.PLANNED,
            site_profile_id=site_profile.id,
            telescope_id=telescope.id,
            camera_id=camera_id,
        )
        # Check and insert in one transaction: a second caller gets this
        # session back.
        return context.butler.insert_session_for_night(session)
    placed = _schedule_session(context, requests, site_profile, telescope, camera_id, wanted)
    existing = session_for_night(context, wanted)
    if existing is None:
        existing = context.butler.insert_session_for_night(placed)
        if existing is placed:
            return placed
    return _replace_placement(context, existing, placed)


def _allowed_capture_values(context: PlanningContext, session: ObservationSession) -> Any:
    """Work out what the session's camera may be asked to do.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session; its camera id is the camera's name.

    Returns
    -------
    allowed : `AllowedCaptureValues`
        The measured settings from the camera profile and the filters seen
        on that camera's library frames.
    """
    from wayfindinglib.analytics.result_quality.allowed_capture_values import (
        allowed_capture_values_for_camera,
    )

    return allowed_capture_values_for_camera(
        context.astrometrics,
        session.camera_id,
        [stored.setting for stored in context.astrometrics.quality.records.get_capture_settings()],
    )


def _requests_still_missing(
    context: PlanningContext, package: ObservationPackage, session: ObservationSession
) -> list[ExposureRequest]:
    """List the rows a package still has to ask for in a session.

    A planned package partly captured in an earlier session asks a later one
    only for the missing frames. When nothing was captured, every row is met,
    the package is not planned, or the frames cannot be read, the package's
    own rows are used.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package being added.
    session : `ObservationSession`
        The session it is added to. Its camera and telescope decide which
        frames count.

    Returns
    -------
    requests : `list` [`ExposureRequest`]
        The rows for the new queue entry.
    """
    full = list(package.exposure_requests)
    if package.status != PackageStatus.PLANNED or not any(row.frame_type == FrameType.LIGHT for row in full):
        return full
    target_frames = TargetFrameReader(context.astrometrics).read(package.target_id)
    if target_frames.note and not target_frames.frames:
        return full
    remaining = missing_requests(full, target_frames, equipment_names(context, session), package.created_at)
    if not any(row.frame_type == FrameType.LIGHT for row in remaining):
        return full
    return remaining


def _promote_suggested_package(context: PlanningContext, package_id: str) -> None:
    """Make a suggested package planned, with a revision-checked write.

    A package that is no longer suggested is left as it is. A write that
    loses a race is retried against the newly read package.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package_id : `str`
        The package that was added to a queue.
    """
    for _attempt in range(3):
        package = context.butler.get("observation_package", {"id": package_id})
        if package is None or package.status != PackageStatus.SUGGESTED:
            return
        loaded_revision = package.revision
        package.status = PackageStatus.PLANNED
        # Frames count toward the rows only from now on.
        moment = planning_context.utc_now()
        package.exposure_requests = [
            row.model_copy(update={"counted_from": moment}) for row in package.exposure_requests
        ]
        package.revision = loaded_revision + 1
        if context.butler.put_package_if_revision(package, loaded_revision):
            log_suggestion_event(context, SuggestionEvent.PROMOTED, package, "added to a session")
            return


def _merge_checklist(
    stored: ObservationSession, checklist: Sequence[ChecklistItem], changes: list[PlanChangeItem]
) -> None:
    """Make the stored checklist match the one the caller gave.

    A new item with the same rule and capture setting as one already on the
    list is not added again. An item whose ``ticked_at`` changed is ticked:
    the time joins its ``tick_history`` (a time without a zone is UTC). An
    item left out of the list is removed.

    Parameters
    ----------
    stored : `ObservationSession`
        The stored session, changed in place.
    checklist : `Sequence` [`ChecklistItem`]
        The whole new checklist.
    changes : `list` [`PlanChangeItem`]
        What changed; records are added here.
    """
    from datetime import UTC

    from wayfindinglib.tasks.planning_tasks.night_plan_tasks import add_checklist_items

    by_id = {item.item_id: item for item in stored.checklist}
    wanted_ids = {item.item_id for item in checklist}
    kept: list[ChecklistItem] = []
    for item in stored.checklist:
        if item.item_id in wanted_ids:
            kept.append(item)
        else:
            changes.append(PlanChangeItem(kind="checklist_removed", detail=item.rule, entry_id=item.item_id))
    stored.checklist = kept
    new_items = []
    for item in checklist:
        old = by_id.get(item.item_id)
        if old is None:
            new_items.append(item.model_copy(deep=True))
            continue
        if item.note != old.note:
            old.note = item.note
            changes.append(PlanChangeItem(kind="checklist_noted", detail=old.rule, entry_id=old.item_id))
        if item.ticked_at is not None and item.ticked_at != old.ticked_at:
            moment = (
                item.ticked_at if item.ticked_at.tzinfo is not None else item.ticked_at.replace(tzinfo=UTC)
            )
            old.ticked_at = moment
            old.tick_history.append(moment)
            changes.append(PlanChangeItem(kind="checklist_ticked", detail=old.rule, entry_id=old.item_id))
    changes.extend(add_checklist_items(stored, new_items))


def edit_session(
    context: PlanningContext,
    session: ObservationSession,
    *,
    add: Sequence[ObservationPackage | QueueRequest] | None = None,
    remove: Sequence[QueuedObservationPackage] | None = None,
    order: Sequence[QueuedObservationPackage] | None = None,
    exposures: Mapping[str, Sequence[ExposureRequest]] | None = None,
    framing: Framing | None = None,
    checklist: Sequence[ChecklistItem] | None = None,
) -> PlanChange:
    """Apply changes to a stored session in one revision-checked write.

    The queue changes apply in this order: ``remove``, then ``order``, then
    ``add`` (new entries go at the end), then ``exposures``. Added entries
    freeze a copy of their package (exposures, dithering, altitude limit and
    priority), as automatic placement does. Every exposure request is merged
    and checked against the camera.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session as the caller last read it. Its id names the stored
        session and its revision must be the stored one.
    add : `Sequence`, optional
        Packages, or `QueueRequest` objects, to append to the queue.
    remove : `Sequence` [`QueuedObservationPackage`], optional
        Entries to take out. A running entry cannot be removed.
    order : `Sequence` [`QueuedObservationPackage`], optional
        Every entry left after ``remove``, in the new order.
    exposures : `Mapping` [`str`, `Sequence` [`ExposureRequest`]], optional
        New exposure requests for entries that have not started, by entry id.
    framing : `Framing`, optional
        The field center, camera rotation and mosaic panels to store.
    checklist : `Sequence` [`ChecklistItem`], optional
        The whole new checklist (see `_merge_checklist`).

    Returns
    -------
    change : `PlanChange`
        The new revision, what changed, notes on accepted settings and the
        stored session. A change that changes nothing keeps the revision.

    Raises
    ------
    InvalidArgumentError
        If nothing to change is given, ``order`` does not name exactly the
        entries, or an exposure request is malformed or refused.
    NotFoundError
        If the session, an entry or a package does not exist.
    ConflictError
        If a running entry is removed or an entry that started changes.
    PlanRevisionConflictError
        If the session's revision is not the stored one.
    """
    from wayfindinglib.tasks.planning_tasks.night_plan_tasks import (
        check_exposure_requests,
        merge_exposure_requests,
        store_session_change,
    )

    if all(value is None for value in (add, remove, order, exposures, framing, checklist)):
        raise InvalidArgumentError("Give something to change.")
    stored = load_session(context, session.id)
    loaded_revision = stored.revision
    changes: list[PlanChangeItem] = []
    allowed = None
    notes: list[str] = []
    promoted: list[str] = []

    def checked(requests: list[ExposureRequest]) -> list[ExposureRequest]:
        """Merge duplicates and check the camera allows each request.

        Parameters
        ----------
        requests : `list` [`ExposureRequest`]
            The requests of one entry.

        Returns
        -------
        requests : `list` [`ExposureRequest`]
            The merged requests.
        """
        nonlocal allowed
        merged = merge_exposure_requests(requests)
        if allowed is None:
            allowed = _allowed_capture_values(context, stored)
        notes.extend(note for note in check_exposure_requests(merged, allowed) if note not in notes)
        return merged

    if remove:
        entries_by_id = {entry.id: entry for entry in stored.queue}
        remove_ids = [entry.id for entry in remove]
        missing = [entry_id for entry_id in remove_ids if entry_id not in entries_by_id]
        if missing:
            raise NotFoundError("Those entries are not in the queue.", details={"remove": missing})
        running = [
            entry_id for entry_id in remove_ids if entries_by_id[entry_id].status == QueueEntryStatus.RUNNING
        ]
        if running:
            raise ConflictError("A running entry cannot be removed.", details={"running": running})
        stored.queue = [entry for entry in stored.queue if entry.id not in set(remove_ids)]
        changes.extend(PlanChangeItem(kind="queue_removed", entry_id=entry_id) for entry_id in remove_ids)
    if order is not None:
        entries_by_id = {entry.id: entry for entry in stored.queue}
        order_ids = [entry.id for entry in order]
        if sorted(order_ids) != sorted(entries_by_id):
            raise InvalidArgumentError(
                "order must name exactly the entries currently in the queue.",
                details={"order": order_ids, "entries": sorted(entries_by_id)},
            )
        stored.queue = [entries_by_id[entry_id] for entry_id in order_ids]
        changes.append(PlanChangeItem(kind="queue_reordered"))
    for item in add or ():
        request = _queue_request(item)
        package = load_package(context, request.package_id)
        if request.exposure_requests is None:
            base_requests = _requests_still_missing(context, package, stored)
        else:
            base_requests = list(request.exposure_requests)
        entry = QueuedObservationPackage(
            id=str(uuid.uuid4()),
            observation_package_id=package.id,
            target_id=package.target_id,
            exposure_requests=checked(base_requests),
            dither_config=package.dither_config,
            minimum_altitude_deg=package.minimum_altitude_deg,
            priority=package.priority,
            start_time_mode=request.start_time_mode,
            requested_start_time=request.requested_start_time,
            computed_start_time=request.computed_start_time,
            computed_end_time=request.computed_end_time,
        )
        stored.queue.append(entry)
        changes.append(PlanChangeItem(kind="queue_added", detail=package.id, entry_id=entry.id))
        if package.status == PackageStatus.SUGGESTED and package.id not in promoted:
            promoted.append(package.id)
            changes.append(PlanChangeItem(kind="package_promoted", detail=package.id))
    for entry_id, requests in (exposures or {}).items():
        entry = next((candidate for candidate in stored.queue if candidate.id == entry_id), None)
        if entry is None:
            raise NotFoundError("That entry is not in the queue.", details={"exposures": entry_id})
        if entry.status != QueueEntryStatus.PENDING:
            raise ConflictError(
                "Only an entry that has not started can change its exposures.",
                details={"entry_id": entry_id, "status": entry.status.value},
            )
        try:
            rows = [ExposureRequest.model_validate(row) for row in requests]
        except ValidationError as error:
            raise InvalidArgumentError(
                "Each exposure request needs frame_type, exposure_s and count.",
                details={"error": str(error)},
            ) from error
        entry.exposure_requests = checked(rows)
        changes.append(PlanChangeItem(kind="exposures_set", entry_id=entry_id))
    if checklist is not None:
        _merge_checklist(stored, checklist, changes)
    if framing is not None:
        stored.framing = Framing.model_validate(framing)
        changes.append(PlanChangeItem(kind="framing_set"))
    result = store_session_change(context.butler, stored, loaded_revision, session.revision, changes, notes)
    if isinstance(result, PlanRevisionConflictError):
        raise PlanRevisionConflictError(str(result), details=result.details) from result
    for package_id in promoted:
        _promote_suggested_package(context, package_id)
    return result


def add_to_session(
    context: PlanningContext,
    session: ObservationSession,
    packages: Sequence[ObservationPackage | QueueRequest],
) -> ObservationSession:
    """Append packages to a session's queue.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session as last read.
    packages : `Sequence`
        Stored packages (placed as soon as possible, asking for the frames
        still missing), or `QueueRequest` objects that say how.

    Returns
    -------
    session : `ObservationSession`
        The stored session.

    Raises
    ------
    InvalidArgumentError
        If no package is given.
    """
    if not packages:
        raise InvalidArgumentError("Give at least one package to add.")
    return edit_session(context, session, add=packages).session


def remove_from_session(
    context: PlanningContext, session: ObservationSession, entries: Sequence[QueuedObservationPackage]
) -> ObservationSession:
    """Take entries out of a session's queue.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session as last read.
    entries : `Sequence` [`QueuedObservationPackage`]
        The entries to remove, from ``session.queue``.

    Returns
    -------
    session : `ObservationSession`
        The stored session.

    Raises
    ------
    InvalidArgumentError
        If no entry is given.
    """
    if not entries:
        raise InvalidArgumentError("Give at least one entry to remove.")
    return edit_session(context, session, remove=entries).session


def update_session(
    context: PlanningContext,
    session: ObservationSession,
    order: Sequence[QueuedObservationPackage] | None = None,
    exposures: Mapping[str, Sequence[ExposureRequest]] | None = None,
    framing: Framing | None = None,
    checklist: Sequence[ChecklistItem] | None = None,
) -> ObservationSession:
    """Change a session's queue order, exposures, framing or checklist.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    session : `ObservationSession`
        The session as last read.
    order : `Sequence` [`QueuedObservationPackage`], optional
        Every entry, in the new order.
    exposures : `Mapping` [`str`, `Sequence` [`ExposureRequest`]], optional
        New exposure requests by entry id.
    framing : `Framing`, optional
        The field center, camera rotation and mosaic panels.
    checklist : `Sequence` [`ChecklistItem`], optional
        The whole new checklist.

    Returns
    -------
    session : `ObservationSession`
        The stored session.
    """
    return edit_session(
        context, session, order=order, exposures=exposures, framing=framing, checklist=checklist
    ).session
