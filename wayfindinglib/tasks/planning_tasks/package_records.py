"""Purpose: Read and change the stored observation packages.

Description: The owner functions behind ``wayfinder.planning.packages``. A
package is a reusable imaging request for one target: its exposure rows,
dithering, altitude limit and priority.

* `get_package`, `get_package_for_target`, `query_packages` and
  `query_suggestion_log` read.
* `create_package` and `create_mosaic_packages` record new packages.
* `update_package` changes a package in one revision-checked write: plain
  field changes, a reopen (``status="PLANNED"`` on a complete package) and a
  dismissal of a suggestion or of some of its rows. `restore_package` takes a
  dismissal back and `delete_package` removes a package.
* `get_depth_goal` and `set_depth_goal` read and choose a target's depth level.

A new or changed exposure row starts counting frames from now. Reopening a
complete package stores, for each light row, the frames already captured, so
they do not complete it again.
"""

import uuid
from collections.abc import Sequence
from typing import Any

from pydantic import ValidationError

from astrometricslib import Target
from astrometricslib.arguments import Page, check_limit
from astrometricslib.exceptions import ConflictError, InvalidArgumentError, NotFoundError
from wayfindinglib.analytics.frame_counting import TargetFrameReader, count_request_frames
from wayfindinglib.models.planning.depth_goal import DepthGoalStatus, DepthLevel
from wayfindinglib.models.planning.mosaic import MosaicPanel, MosaicPlan
from wayfindinglib.models.planning.observation_package import (
    DismissedRow,
    DitherConfig,
    ExposureRequest,
    FrameType,
    ObservationPackage,
    PackageStatus,
)
from wayfindinglib.models.planning.suggestion_log import SuggestionEvent, SuggestionLogEntry
from wayfindinglib.models.session.observation_session import PlanRevisionConflictError
from wayfindinglib.tasks.planning_tasks import planning_context
from wayfindinglib.tasks.planning_tasks.planning_context import (
    PlanningContext,
    equipment_names,
    fold_target_id,
    load_package,
    log_suggestion_event,
)

__all__ = [
    "PACKAGE_FIELDS",
    "create_mosaic_packages",
    "create_package",
    "delete_package",
    "get_depth_goal",
    "get_package",
    "get_package_for_target",
    "list_packages",
    "query_packages",
    "query_suggestion_log",
    "restore_package",
    "set_depth_goal",
    "update_package",
]

PACKAGE_FIELDS = (
    "name",
    "exposure_requests",
    "dither_config",
    "minimum_altitude_deg",
    "priority",
    "quality_weighting_enabled",
    "notes",
    "framing",
)
"""The package fields `update_package` sets directly."""

UPDATE_KEYS = (*PACKAGE_FIELDS, "status", "dismissed", "dismissed_rows")
"""Every change `update_package` accepts."""


def _page(items: list[Any], limit: int | None, offset: int) -> Page[Any]:
    """Cut a page of matches after checking the paging arguments.

    Parameters
    ----------
    items : `list`
        Every match, in order.
    limit : `int` or `None`
        Most matches to return; `None` returns every match.
    offset : `int`
        How many matches to skip.

    Returns
    -------
    page : `Page`
        The page.
    """
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)
    return Page.of(items, offset=offset, limit=limit)


def get_package(
    context: PlanningContext, package_id: str, required: bool = False
) -> ObservationPackage | None:
    """Read one stored package by id.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package_id : `str`
        The package id.
    required : `bool`, optional
        Raise `NotFoundError` instead of returning `None`.

    Returns
    -------
    package : `ObservationPackage` or `None`
        The package, or `None` when no package has that id.
    """
    if required:
        return load_package(context, package_id)
    return context.butler.get("observation_package", {"id": package_id})


def list_packages(
    context: PlanningContext,
    status: PackageStatus | str | None = None,
    target: Target | None = None,
    dismissed: bool | None = False,
) -> list[ObservationPackage]:
    """List the stored packages that match, newest first.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    status : `PackageStatus` or `str`, optional
        Only this status.
    target : `Target`, optional
        Only this target's packages (matched ignoring case, ``_`` as space).
    dismissed : `bool` or `None`, optional
        `False` (default) leaves dismissed suggestions out, `True` keeps only
        them, `None` keeps both.

    Returns
    -------
    packages : `list` [`ObservationPackage`]
        The matching packages.

    Raises
    ------
    InvalidArgumentError
        If ``status`` is not a package status.
    """
    wanted_status = None
    if status is not None:
        try:
            wanted_status = PackageStatus(status)
        except ValueError as error:
            raise InvalidArgumentError(
                f"status must be one of: {', '.join(item.value for item in PackageStatus)}.",
                details={"status": str(status)},
            ) from error
    wanted_target = None if target is None else fold_target_id(target.id)
    packages = [
        package
        for package in context.butler.get_all("observation_package")
        if (wanted_status is None or package.status == wanted_status)
        and (wanted_target is None or fold_target_id(package.target_id) == wanted_target)
        and (dismissed is None or package.is_dismissed == dismissed)
    ]
    packages.sort(key=lambda package: package.created_at, reverse=True)
    return packages


def query_packages(
    context: PlanningContext,
    status: PackageStatus | str | None = None,
    target: Target | None = None,
    dismissed: bool | None = False,
    limit: int | None = None,
    offset: int = 0,
) -> Page[ObservationPackage]:
    """List the stored packages, newest first, as a page.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    status, target, dismissed
        The filters of `list_packages`.
    limit : `int`, optional
        Most packages to return; `None` returns every match.
    offset : `int`, optional
        How many matches to skip.

    Returns
    -------
    packages : `Page` [`ObservationPackage`]
        The matching packages.
    """
    return _page(list_packages(context, status, target, dismissed), limit, offset)


def get_package_for_target(context: PlanningContext, target: Target) -> ObservationPackage | None:
    """Pick the one package that stands for a target.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`
        The target.

    Returns
    -------
    package : `ObservationPackage` or `None`
        The newest planned package, else the newest suggested package that is
        not dismissed, else `None`. A complete package is never picked.
    """
    for status in (PackageStatus.PLANNED, PackageStatus.SUGGESTED):
        found = list_packages(context, status, target, False)
        if found:
            return found[0]
    return None


def query_suggestion_log(
    context: PlanningContext, target: Target | None = None, limit: int | None = None, offset: int = 0
) -> Page[SuggestionLogEntry]:
    """List the suggestion lifecycle log, newest first.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`, optional
        Only this target's lines.
    limit : `int`, optional
        Most lines to return; `None` returns every match.
    offset : `int`, optional
        How many matches to skip.

    Returns
    -------
    entries : `Page` [`SuggestionLogEntry`]
        The log lines.
    """
    wanted = None if target is None else fold_target_id(target.id)
    entries = [
        entry
        for entry in context.butler.get_all("package_suggestion_event")
        if wanted is None or fold_target_id(entry.target_id) == wanted
    ]
    entries.sort(key=lambda entry: entry.time, reverse=True)
    return _page(entries, limit, offset)


def create_package(
    context: PlanningContext,
    target: Target,
    exposures: Sequence[ExposureRequest] = (),
    dither_config: DitherConfig | None = None,
    minimum_altitude_deg: float | None = None,
    priority: int = 0,
    quality_weighting_enabled: bool = False,
    notes: str = "",
) -> ObservationPackage:
    """Record a reusable imaging request for a target.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`
        The library target.
    exposures : `Sequence` [`ExposureRequest`], optional
        The exposure rows. Empty is allowed. Each row starts counting frames
        now unless it carries its own ``counted_from``.
    dither_config : `DitherConfig`, optional
        How the package dithers.
    minimum_altitude_deg : `float`, optional
        The lowest altitude the package may be imaged at.
    priority : `int`, optional
        Higher is placed first.
    quality_weighting_enabled : `bool`, optional
        Let the target's dataset report raise the priority when placed.
    notes : `str`, optional
        Free text.

    Returns
    -------
    package : `ObservationPackage`
        The stored package.

    Raises
    ------
    InvalidArgumentError
        If ``target`` is not a `Target` or a value is not valid.
    """
    if not isinstance(target, Target):
        raise InvalidArgumentError(
            "create takes a Target; look a name up with astrometrics.targets.get first."
        )
    moment = planning_context.utc_now()
    try:
        rows = [ExposureRequest.model_validate(row) for row in exposures]
        package = ObservationPackage(
            id=str(uuid.uuid4()),
            name=target.id,
            target_id=target.id,
            exposure_requests=[
                row if row.counted_from is not None else row.model_copy(update={"counted_from": moment})
                for row in rows
            ],
            dither_config=dither_config,
            minimum_altitude_deg=minimum_altitude_deg,
            priority=priority,
            quality_weighting_enabled=quality_weighting_enabled,
            notes=notes,
        )
    except ValidationError as error:
        raise InvalidArgumentError("A package value is not valid.", details={"error": str(error)}) from error
    context.butler.put(package, "observation_package", {"id": package.id})
    return package


def create_mosaic_packages(
    context: PlanningContext,
    target: Target,
    panels: Sequence[MosaicPanel],
    exposures: Sequence[ExposureRequest] = (),
    dither_config: DitherConfig | None = None,
) -> MosaicPlan:
    """Add one library target per mosaic panel, and one package each.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`
        The target the mosaic covers.
    panels : `Sequence` [`MosaicPanel`]
        The panels, as ``sky.get_panels`` gives them.
    exposures : `Sequence` [`ExposureRequest`], optional
        The exposure rows every panel's package shares. With none, only the
        panel targets are added and no package is recorded.
    dither_config : `DitherConfig`, optional
        The dithering every panel's package shares.

    Returns
    -------
    plan : `MosaicPlan`
        The panel target ids and the recorded packages.

    Raises
    ------
    InvalidArgumentError
        If ``dither_config`` comes without ``exposures``.
    """
    from wayfindinglib.tasks.planning_tasks.mosaic_tasks import create_mosaic

    if not exposures and dither_config is not None:
        raise InvalidArgumentError("dither_config is used only with exposures, for the panels' packages.")
    plan = create_mosaic(
        context.astrometrics, target, list(panels), list(exposures), dither_config, bool(exposures)
    )
    for package in plan.packages:
        context.butler.put(package, "observation_package", {"id": package.id})
    return plan


def _stamped_rows(old_rows: list[ExposureRequest], new_rows: list[ExposureRequest]) -> list[ExposureRequest]:
    """Give each edited row its counting time and baseline.

    A row that matches an old row in frame type, filter, length, gain, offset
    and binning keeps the old row's counting time and baseline; a new or
    changed row starts counting now. A suggested row keeps its source key,
    reason and fingerprint.

    Parameters
    ----------
    old_rows : `list` [`ExposureRequest`]
        The rows before the edit.
    new_rows : `list` [`ExposureRequest`]
        The rows after the edit.

    Returns
    -------
    rows : `list` [`ExposureRequest`]
        The new rows with their counting fields set.
    """

    def match_key(row: ExposureRequest) -> tuple:
        """Name the fields that decide which frames a row counts.

        Parameters
        ----------
        row : `ExposureRequest`
            The row.

        Returns
        -------
        key : `tuple`
            Frame type, filter, length, gain, offset and binning.
        """
        return (row.frame_type, row.filter, row.exposure_s, row.gain, row.offset, row.binning)

    old_by_key = {match_key(row): row for row in old_rows}
    moment = planning_context.utc_now()
    stamped = []
    for row in new_rows:
        old = old_by_key.get(match_key(row))
        if old is None:
            stamped.append(row.model_copy(update={"counted_from": moment, "baseline_captured": 0}))
            continue
        stamped.append(
            row.model_copy(
                update={
                    "counted_from": old.counted_from,
                    "baseline_captured": old.baseline_captured,
                    "source_key": row.source_key or old.source_key,
                    "reason": row.reason or old.reason,
                    "evidence_fingerprint": row.evidence_fingerprint or old.evidence_fingerprint,
                }
            )
        )
    return stamped


def _rows_with_baseline(context: PlanningContext, package: ObservationPackage) -> list[ExposureRequest]:
    """Store how many frames are already captured for each light row.

    Used when a complete package is reopened. When the frames cannot be
    counted, the row counts from now instead.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package being reopened.

    Returns
    -------
    rows : `list` [`ExposureRequest`]
        The rows with their baselines (or new counting times) set.
    """
    from wayfindinglib.tasks.planning_tasks.suggestion_tasks import newest_session_with

    moment = planning_context.utc_now()
    session = newest_session_with(package.id, context.butler.get_all("observation_session"))
    target_frames = TargetFrameReader(context.astrometrics).read(package.target_id) if session else None
    rows = []
    for row in package.exposure_requests:
        if row.frame_type != FrameType.LIGHT:
            rows.append(row)
        elif session is None or target_frames is None or (target_frames.note and not target_frames.frames):
            rows.append(row.model_copy(update={"counted_from": moment, "baseline_captured": 0}))
        else:
            count = count_request_frames(
                row.model_copy(update={"baseline_captured": 0}),
                target_frames,
                equipment_names(context, session),
                package.created_at,
            )
            rows.append(row.model_copy(update={"baseline_captured": count.captured}))
    return rows


def _dismissed(
    package: ObservationPackage, dismiss: bool, source_keys: Sequence[str] | None
) -> ObservationPackage:
    """Dismiss or bring back a suggested package, or some of its rows.

    Parameters
    ----------
    package : `ObservationPackage`
        The package as stored. It is not changed.
    dismiss : `bool`
        `True` to dismiss, `False` to take a dismissal back.
    source_keys : `Sequence` [`str`] or `None`
        The rows to act on, or `None` for the whole package.

    Returns
    -------
    edited : `ObservationPackage`
        A copy with the dismissal changed.

    Raises
    ------
    NotFoundError
        If a source key names no row (or no dismissed row).
    """
    edited = package.model_copy(deep=True)
    suggestion = edited.suggestion
    if suggestion is None:
        return edited
    if source_keys is None:
        if dismiss and not suggestion.evidence_fingerprint:
            suggestion.evidence_fingerprint = package.id[:16]
        suggestion.dismissed_fingerprint = suggestion.evidence_fingerprint if dismiss else ""
        return edited
    for source_key in source_keys:
        if dismiss:
            row = next((row for row in edited.exposure_requests if row.source_key == source_key), None)
            if row is None:
                raise NotFoundError(
                    f"No row {source_key!r} in the package.",
                    details={"package_id": package.id, "source_key": source_key},
                )
            edited.exposure_requests = [item for item in edited.exposure_requests if item is not row]
            suggestion.dismissed_rows.append(
                DismissedRow(source_key=source_key, fingerprint=row.evidence_fingerprint, request=row)
            )
            continue
        held = next((item for item in suggestion.dismissed_rows if item.source_key == source_key), None)
        if held is None:
            raise NotFoundError(
                f"No dismissed row {source_key!r} in the package.",
                details={"package_id": package.id, "source_key": source_key},
            )
        suggestion.dismissed_rows = [item for item in suggestion.dismissed_rows if item is not held]
        edited.exposure_requests.append(held.request)
    return edited


def _store(context: PlanningContext, edited: ObservationPackage, loaded_revision: int) -> ObservationPackage:
    """Store an edited package if the stored revision did not move.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    edited : `ObservationPackage`
        The edited package. Its revision is set here.
    loaded_revision : `int`
        The revision the edit started from.

    Returns
    -------
    package : `ObservationPackage`
        The stored package.

    Raises
    ------
    PlanRevisionConflictError
        If the package changed while the edit was made.
    """
    edited.revision = loaded_revision + 1
    if not context.butler.put_package_if_revision(edited, loaded_revision):
        stored = context.butler.get("observation_package", {"id": edited.id})
        raise PlanRevisionConflictError(
            "The package changed while this edit was being made. Read it again and redo the edit.",
            details={
                "current_revision": stored.revision if stored is not None else None,
                "expected_revision": loaded_revision,
            },
        )
    return edited


def _loaded(context: PlanningContext, package: ObservationPackage) -> ObservationPackage:
    """Read the stored package and check the caller's revision against it.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package as the caller last read it.

    Returns
    -------
    stored : `ObservationPackage`
        The stored package.

    Raises
    ------
    PlanRevisionConflictError
        If the caller's revision is not the stored one.

    Notes
    -----
    Raises `NotFoundError` if the package is no longer stored.
    Raises `PlanRevisionConflictError` if the caller's revision is not the
    stored one.
    """
    stored = load_package(context, package.id)
    if package.revision != stored.revision:
        raise PlanRevisionConflictError(
            f"The package is at revision {stored.revision}, not {package.revision}. "
            "Read it again and redo the edit.",
            details={"current_revision": stored.revision, "expected_revision": package.revision},
        )
    return stored


def update_package(
    context: PlanningContext, package: ObservationPackage, changes: dict[str, Any]
) -> ObservationPackage:
    """Change a stored package in one revision-checked write.

    Plain changes set the fields in `PACKAGE_FIELDS`; a value of `None`
    clears a field that may be empty. ``status="PLANNED"`` reopens a complete
    package (it may come with plain changes). ``dismissed=True`` dismisses a
    suggestion until its evidence changes, and ``dismissed_rows=[keys]``
    dismisses those rows and takes them out; neither mixes with other changes.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package as the caller last read it.
    changes : `dict` [`str`, `Any`]
        Field name -> new value.

    Returns
    -------
    package : `ObservationPackage`
        The stored package.

    Raises
    ------
    InvalidArgumentError
        If nothing or an unknown name is given, or the changes clash.
    ConflictError
        If the package is in the wrong state for a reopen or a dismissal.

    Notes
    -----
    Raises `InvalidArgumentError` if nothing or an unknown name is given, a
    dismissal is mixed with other changes, a status change is not a reopen, or
    a value is not valid.
    Raises `ConflictError` if a reopen is asked of a package that is not
    complete, or a dismissal of one that is not a library suggestion.
    Raises `PlanRevisionConflictError` if the package's revision is not the
    stored one.
    """
    unknown = sorted(set(changes) - set(UPDATE_KEYS))
    if unknown or not changes:
        raise InvalidArgumentError(
            f"Give one or more of: {', '.join(UPDATE_KEYS)}.", details={"unknown": unknown}
        )
    dismissing = "dismissed" in changes or "dismissed_rows" in changes
    if dismissing and len(changes) > 1:
        raise InvalidArgumentError("dismissed and dismissed_rows cannot be combined with other changes.")
    if "dismissed" in changes and changes["dismissed"] is not True:
        raise InvalidArgumentError("dismissed takes True; restore brings a dismissed suggestion back.")
    reopen = "status" in changes
    if reopen and PackageStatus(changes["status"]) != PackageStatus.PLANNED:
        raise InvalidArgumentError(
            "Only status='PLANNED' can be set, to reopen a complete package.",
            details={"status": str(changes["status"])},
        )
    stored = _loaded(context, package)
    if dismissing:
        if stored.status != PackageStatus.SUGGESTED or stored.suggestion is None:
            raise ConflictError(
                "Only a suggested package can be dismissed.",
                details={"package_id": stored.id, "status": stored.status.value},
            )
        rows = changes.get("dismissed_rows")
        edited = _dismissed(stored, True, None if rows is None else list(rows))
        stored_package = _store(context, edited, stored.revision)
        log_suggestion_event(
            context, SuggestionEvent.DISMISSED, stored_package, "the user asked", ",".join(rows or [])
        )
        return stored_package
    if reopen and stored.status != PackageStatus.COMPLETE:
        raise ConflictError(
            "Only a complete package can be reopened.",
            details={"package_id": stored.id, "status": stored.status.value},
        )
    updates = {name: value for name, value in changes.items() if name in PACKAGE_FIELDS}
    if reopen:
        updates["status"] = PackageStatus.PLANNED
        updates["completed_at"] = None
    try:
        edited = ObservationPackage.model_validate({**stored.model_dump(), **updates})
    except ValidationError as error:
        raise InvalidArgumentError("A package value is not valid.", details={"error": str(error)}) from error
    if "exposure_requests" in updates:
        edited.exposure_requests = _stamped_rows(stored.exposure_requests, edited.exposure_requests)
    if reopen:
        edited.exposure_requests = _rows_with_baseline(context, edited)
    stored_package = _store(context, edited, stored.revision)
    if reopen:
        log_suggestion_event(context, SuggestionEvent.REOPENED, stored_package, "the user reopened it")
    return stored_package


def restore_package(
    context: PlanningContext, package: ObservationPackage, source_keys: Sequence[str] | None = None
) -> ObservationPackage:
    """Bring back a dismissed suggestion, or some of its dismissed rows.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package as the caller last read it.
    source_keys : `Sequence` [`str`], optional
        Bring back only these rows.

    Returns
    -------
    package : `ObservationPackage`
        The stored package.

    Raises
    ------
    ConflictError
        If the package is not a library suggestion.

    Notes
    -----
    Raises `ConflictError` if the package is not a library suggestion.
    Raises `NotFoundError` if a key names no dismissed row.
    Raises `PlanRevisionConflictError` if the package's revision is not the
    stored one.
    """
    stored = _loaded(context, package)
    if stored.status != PackageStatus.SUGGESTED or stored.suggestion is None:
        raise ConflictError(
            "Only a suggested package can be brought back.",
            details={"package_id": stored.id, "status": stored.status.value},
        )
    edited = _dismissed(stored, False, None if source_keys is None else list(source_keys))
    stored_package = _store(context, edited, stored.revision)
    log_suggestion_event(
        context, SuggestionEvent.UNDISMISSED, stored_package, "the user asked", ",".join(source_keys or [])
    )
    return stored_package


def delete_package(context: PlanningContext, package: ObservationPackage) -> None:
    """Remove a stored package.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    package : `ObservationPackage`
        The package as the caller last read it.

    Raises
    ------
    NotFoundError
        If the package is not stored.

    Notes
    -----
    Raises `NotFoundError` if the package is not stored.
    Raises `PlanRevisionConflictError` if the package's revision is not the
    stored one.
    """
    _loaded(context, package)
    if not context.butler.delete_package(package.id):
        raise NotFoundError(f"No observation package {package.id!r}.", details={"package_id": package.id})


def get_depth_goal(context: PlanningContext, target: Target) -> DepthGoalStatus:
    """Say how close a target's image is to its chosen depth level.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`
        The target.

    Returns
    -------
    status : `DepthGoalStatus`
        The chosen level, the newest stack's depth, the level it reaches, and
        the time to reach the chosen level.
    """
    from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import cached_depth_goal_status
    from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

    return cached_depth_goal_status(
        target.id,
        config=context.config,
        astrometrics=context.astrometrics,
        butler=context.butler,
        report_for_noise_ratio=lambda ratio: build_target_dataset_report(
            target,
            astrometrics=context.astrometrics,
            butler=context.butler,
            config=context.config,
            target_noise_ratio=ratio,
        ),
    )


def set_depth_goal(context: PlanningContext, target: Target, level: DepthLevel | str) -> DepthGoalStatus:
    """Choose a target's depth level and return the new status.

    Parameters
    ----------
    context : `PlanningContext`
        The shared planning state.
    target : `Target`
        The target.
    level : `DepthLevel` or `str`
        ``"quick_look"``, ``"good"`` or ``"deep"``.

    Returns
    -------
    status : `DepthGoalStatus`
        The status for the new level.
    """
    from wayfindinglib.tasks.planning_tasks.depth_goal_tasks import coerce_level, store_chosen_level

    store_chosen_level(context.butler, target.id, coerce_level(level))
    return get_depth_goal(context, target)
