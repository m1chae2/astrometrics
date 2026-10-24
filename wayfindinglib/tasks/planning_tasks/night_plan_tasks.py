"""Purpose: The steps that change a stored night plan.

Description: The night plan is an `ObservationSession` (register entry
WAY-50). `ObservationPlanning.edit_queue` and `tick_checklist_item` hand
their work to the functions here. They do four jobs:

* Merge or refuse repeated exposure requests, so the app never has to.
* Check each request against what the camera allows (`allowed_capture_values`).
* Add checklist items and tick them.
* Store the session with a revision check. The check and the write are one
  database statement, so two windows cannot overwrite each other.

An edit that changes nothing (every item was already on the list) stores
nothing and keeps the revision. A write that names an old revision raises
`PlanRevisionConflictError`, which names the revision now stored.
"""

from collections.abc import Iterable
from datetime import UTC, datetime

from astrometricslib import InvalidArgumentError
from wayfindinglib.analytics.result_quality.allowed_capture_values import AllowedCaptureValues
from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import ExposureRequest
from wayfindinglib.models.session.observation_session import (
    ChecklistItem,
    ObservationSession,
    PlanChange,
    PlanChangeItem,
    PlanRevisionConflictError,
)


def merge_exposure_requests(requests: Iterable[ExposureRequest]) -> list[ExposureRequest]:
    """Merge exact duplicate exposure requests and refuse conflicting ones.

    Two requests are exact duplicates when frame type, filter, exposure
    length, gain setting, offset, binning and delay all match. Their counts
    add. Two requests that match in everything except the delay are in
    conflict: one job cannot be paced two ways. Requests that differ in gain
    setting, offset or binning are different jobs, and are kept apart.

    Parameters
    ----------
    requests : `Iterable` [`ExposureRequest`]
        The requests, in order.

    Returns
    -------
    merged : `list` [`ExposureRequest`]
        The requests in first-seen order, duplicates merged.

    Raises
    ------
    InvalidArgumentError
        If two requests conflict, naming both delays.
    """
    merged: dict[tuple, ExposureRequest] = {}
    for request in requests:
        key = (
            request.frame_type,
            request.filter,
            request.exposure_sec,
            request.gain_setting,
            request.offset,
            request.binning,
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = request.model_copy()
        elif existing.delay_sec != request.delay_sec:
            raise InvalidArgumentError(
                "Two requests for the same frame type, filter, exposure and camera setting give different "
                f"delays ({existing.delay_sec} s and {request.delay_sec} s). Keep one.",
                details={"delays": [existing.delay_sec, request.delay_sec]},
            )
        else:
            merged[key] = existing.model_copy(update={"count": existing.count + request.count})
    return list(merged.values())


def check_exposure_requests(requests: Iterable[ExposureRequest], allowed: AllowedCaptureValues) -> list[str]:
    """Refuse a request the camera cannot honor, and collect notes on the rest.

    Parameters
    ----------
    requests : `Iterable` [`ExposureRequest`]
        The requests to check.
    allowed : `AllowedCaptureValues`
        What the camera may be asked to do.

    Returns
    -------
    notes : `list` [`str`]
        One note for each accepted request that needs one, such as
        "setting not measured". Repeated notes appear once.

    Raises
    ------
    InvalidArgumentError
        If any request is refused. The message gives every reason.
    """
    notes: list[str] = []
    reasons: list[str] = []
    for request in requests:
        verdict = allowed.check(request.gain_setting, request.offset, request.binning, request.filter)
        reasons.extend(reason for reason in verdict.reasons if reason not in reasons)
        notes.extend(f"{allowed.camera_name}: {note}" for note in verdict.notes)
    if reasons:
        raise InvalidArgumentError(" ".join(reasons), details={"reasons": reasons})
    return list(dict.fromkeys(notes))


def add_checklist_items(session: ObservationSession, items: Iterable[ChecklistItem]) -> list[PlanChangeItem]:
    """Add checklist items to a session, skipping ones already on the list.

    An item already on the list has the same rule and the same capture
    setting. It is not added again.

    Parameters
    ----------
    session : `ObservationSession`
        The session to change.
    items : `Iterable` [`ChecklistItem`]
        The items to add.

    Returns
    -------
    changes : `list` [`PlanChangeItem`]
        One record for each item added or skipped.
    """
    changes: list[PlanChangeItem] = []
    for item in items:
        duplicate = next(
            (
                existing
                for existing in session.checklist
                if existing.rule == item.rule and existing.capture_setting == item.capture_setting
            ),
            None,
        )
        if duplicate is not None:
            changes.append(
                PlanChangeItem(
                    kind="checklist_already_present",
                    detail=f"{item.rule} is already on the list.",
                    entry_id=duplicate.item_id,
                )
            )
            continue
        session.checklist.append(item)
        changes.append(PlanChangeItem(kind="checklist_added", detail=item.rule, entry_id=item.item_id))
    return changes


def find_checklist_item(session: ObservationSession, item_id: str) -> ChecklistItem | None:
    """Find one checklist item by id.

    Parameters
    ----------
    session : `ObservationSession`
        The session whose checklist to search.
    item_id : `str`
        The item id.

    Returns
    -------
    item : `ChecklistItem` or `None`
        The item, or `None` when the session has no such item.
    """
    return next((candidate for candidate in session.checklist if candidate.item_id == item_id), None)


def tick_item(item: ChecklistItem, ticked_at: datetime | None, note: str | None) -> PlanChangeItem:
    """Record that the user did one checklist item.

    A repeat tick (the item came back and the user fixed it again) adds a
    new time and keeps the old ones in `tick_history`.

    Parameters
    ----------
    item : `ChecklistItem`
        The item to tick. It is changed in place.
    ticked_at : `datetime` or `None`
        When the user did it. `None` means now. A time without a time zone
        is taken as UTC.
    note : `str` or `None`
        A note to keep with the item. `None` leaves the note alone.

    Returns
    -------
    change : `PlanChangeItem`
        The record of the tick.
    """
    moment = ticked_at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    item.ticked_at = moment
    item.tick_history.append(moment)
    if note is not None:
        item.note = note
    return PlanChangeItem(kind="checklist_ticked", detail=item.rule, entry_id=item.item_id)


def store_session_change(
    butler: DiskButler,
    session: ObservationSession,
    loaded_revision: int,
    expected_revision: int | None,
    changes: list[PlanChangeItem],
    setting_notes: list[str],
) -> PlanChange | PlanRevisionConflictError:
    """Store a changed session with a revision check, and describe the change.

    Parameters
    ----------
    butler : `DiskButler`
        The storage.
    session : `ObservationSession`
        The changed session, still carrying the revision it was loaded with.
    loaded_revision : `int`
        The revision the session had when this call read it.
    expected_revision : `int` or `None`
        The revision the caller last saw. `None` accepts whatever was read.
    changes : `list` [`PlanChangeItem`]
        What the write changed.
    setting_notes : `list` [`str`]
        Notes on accepted capture settings.

    Returns
    -------
    result : `PlanChange` or `PlanRevisionConflictError`
        The new revision and what changed. When every change is an
        "already present" skip, nothing is written and the revision is
        unchanged (`revision == previous_revision`). When the stored revision
        is not the one the caller expected, nothing is written and the error
        is returned (not raised) for the caller to raise.
    """
    wanted = loaded_revision if expected_revision is None else expected_revision
    if wanted != loaded_revision:
        return PlanRevisionConflictError(
            f"The plan is at revision {loaded_revision}, not {wanted}. Read it again and redo the edit.",
            details={"current_revision": loaded_revision, "expected_revision": wanted},
        )
    if all(change.kind.endswith("_already_present") for change in changes):
        # Nothing was changed, so nothing is stored and the revision stays.
        return PlanChange(
            session_id=session.id,
            previous_revision=loaded_revision,
            revision=loaded_revision,
            changes=changes,
            setting_notes=setting_notes,
            session=session,
        )
    session.revision = loaded_revision + 1
    if not butler.put_session_if_revision(session, loaded_revision):
        stored = butler.get("observation_session", {"session_id": session.id})
        current = stored.revision if stored is not None else None
        return PlanRevisionConflictError(
            f"The plan changed while this edit was being made; it is now at revision {current}. "
            "Read it again and redo the edit.",
            details={"current_revision": current, "expected_revision": wanted},
        )
    return PlanChange(
        session_id=session.id,
        previous_revision=loaded_revision,
        revision=session.revision,
        changes=changes,
        setting_notes=setting_notes,
        session=session,
    )
