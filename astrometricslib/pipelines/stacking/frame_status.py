"""Purpose: Say what each frame of a target is doing in its stack.

Description: A frame is in one of four states:

* ``included``: it is in the library and was taken before the latest stack
  was made, so that stack used it (unless the stacker leaves it out for a
  reason it records elsewhere).
* ``flagged``: an included frame whose stars are stretched. Its stored star
  elongation (long axis over short axis) is at least the warning level
  (`STRETCHED_STAR_WARNING_ELONGATION`), but the set-aside check kept it,
  because it is not above the set-aside threshold of its session
  (`set_aside_elongation_limit`). The reason gives the three numbers. A
  stacked frame above the threshold (put back by hand, or in a session too
  small for the set-aside check to judge) is flagged too, and its reason says
  so.
* ``not_stacked``: it was taken after the latest stack was made, or the
  target has no stack yet.
* ``set_aside``: the stacker moved it into an `_excluded` folder. Such a frame
  is no longer part of the target's frame list, so it is listed from the
  folder, with the reason the stacker recorded.

The set-aside threshold is found again here the way the set-aside check found
it: the stacked light frames are split into the set-aside check's sessions
(`split_into_batches`), and each session's threshold comes from its frames'
stored elongations. The frames already set aside are no longer in the list,
so the session median can sit a little lower than when the stack was made.

`latest_stack_time` finds when the target's newest stack file was written,
the time a frame must be older than to be ``included``.
"""

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from astrometricslib._internal.drivers.fits_access import frame_uses_color_filter_array
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import existing_stack_path
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.frame_status import FrameStatus, FrameStatusReport
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
    MINIMUM_FRAMES_TO_JUDGE,
    STRETCHED_STAR_WARNING_ELONGATION,
    set_aside_elongation_limit,
    split_into_batches,
)

__all__ = ["build_frame_statuses", "latest_stack_time", "set_aside_limits_by_path", "stretched_star_reason"]


def latest_stack_time(target: Target, stacks_path: str | None) -> float | None:
    """Find when the newest of a target's stack files was written.

    The imaging stack, the spectral stack and the stack of every saved stack
    configuration count. A stack whose file is on disk nowhere is a status
    (there is no stack), not an error.

    Parameters
    ----------
    target : `Target`
        The target.
    stacks_path : `str` or `None`
        The library's stacks folder, where an old record's stack is looked for.

    Returns
    -------
    stacked_at : `float` or `None`
        The newest stack file's modification time, in seconds since the Unix
        epoch, or `None` when the target has no stack on disk.
    """
    recorded = [target.stacking.stacked_image, target.spectral_stacking.stacked_image]
    recorded.extend(
        configuration.stacked_image for configuration in target.stacking.stacks_by_configuration.values()
    )
    paths = {existing_stack_path(path, stacks_path) for path in recorded}
    times = [os.path.getmtime(path) for path in paths if path]
    return max(times) if times else None


def set_aside_limits_by_path(
    frames: Sequence[FrameRecord],
    elongations: Mapping[str, float],
    colour_camera: Callable[[str], bool | None] = frame_uses_color_filter_array,
) -> dict[str, float | None]:
    """Find the set-aside threshold of each stacked light frame.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames that are in the stack. Spectroscopy frames and frames that
        are not lights are left out, as the set-aside check leaves them out.
    elongations : `Mapping` [`str`, `float`]
        The stored star elongation of each frame, by path.
    colour_camera : `Callable`, optional
        Tells from a frame's file whether a colour camera took it (`None`
        when the header cannot be read). The first frame of each session is
        asked. Tests pass a stand-in.

    Returns
    -------
    limits : `dict` [`str`, `float` or `None`]
        The threshold of each light frame's session, by path. `None` for a
        frame in a session with fewer than `MINIMUM_FRAMES_TO_JUDGE` frames,
        which the set-aside check does not judge, or with no stored elongation.
    """
    lights = [
        frame for frame in frames if str(frame.role).upper() == "LIGHT" and not frame_is_spectral(frame)
    ]
    if not lights:
        return {}
    minimum_frames = resolve(MINIMUM_FRAMES_TO_JUDGE).value
    limits: dict[str, float | None] = {}
    for batch in split_into_batches(lights):
        limit = None
        if len(batch) >= minimum_frames:
            known = [elongations[frame.path] for frame in batch if frame.path in elongations]
            limit = set_aside_elongation_limit(known, bool(colour_camera(batch[0].path)))
        limits.update(dict.fromkeys((frame.path for frame in batch), limit))
    return limits


def stretched_star_reason(elongation: float, warning: float, limit: float | None) -> str:
    """Say why a stacked frame is flagged, with its numbers.

    Parameters
    ----------
    elongation : `float`
        The frame's stored star elongation.
    warning : `float`
        The warning level (`STRETCHED_STAR_WARNING_ELONGATION`).
    limit : `float` or `None`
        The set-aside threshold of the frame's session, or `None` when the
        set-aside check does not judge the session.

    Returns
    -------
    reason : `str`
        For example "Stacked, but its stars are stretched: elongation 1.25,
        warning level 1.20, set-aside threshold 1.32."
    """
    words = f"Stacked, but its stars are stretched: elongation {elongation:.2f}, warning level {warning:.2f}"
    if limit is None:
        return f"{words}; its session is too small for the set-aside check to judge."
    if elongation > limit:
        return (
            f"{words}, above the set-aside threshold {limit:.2f} "
            "(put back by hand, or stacked before the check)."
        )
    return f"{words}, set-aside threshold {limit:.2f}."


def build_frame_statuses(
    frames: Iterable[FrameRecord],
    set_aside: Sequence[SetAsideFrameRecord],
    stacked_at: float | None,
    elongations: Mapping[str, float] | None = None,
    colour_camera: Callable[[str], bool | None] = frame_uses_color_filter_array,
) -> FrameStatusReport:
    """Say what state each frame is in.

    Parameters
    ----------
    frames : `Iterable` [`FrameRecord`]
        The target's frame records (each with ``path``, ``timestamp`` and the
        capture setting the set-aside check groups by).
    set_aside : `Sequence` [`SetAsideFrameRecord`]
        The frames in the target's `_excluded` folders.
    stacked_at : `float` or `None`
        When the latest stack was written, in seconds since the Unix epoch,
        or `None` when there is none.
    elongations : `Mapping` [`str`, `float`], optional
        The stored star elongation of each frame, by path. A frame with none
        is never flagged.
    colour_camera : `Callable`, optional
        See `set_aside_limits_by_path`.

    Returns
    -------
    report : `FrameStatusReport`
        One status per frame, then one per set-aside frame. A frame with no
        recorded time counts as taken before the stack.
    """
    elongations = elongations or {}
    warning = resolve(STRETCHED_STAR_WARNING_ELONGATION).value
    frame_list = list(frames)
    stacked = [
        frame
        for frame in frame_list
        if stacked_at is not None and (frame.timestamp is None or float(frame.timestamp) <= stacked_at)
    ]
    stacked_paths = {frame.path for frame in stacked}
    stretched = any(elongations.get(frame.path, 0.0) >= warning for frame in stacked)
    limits = set_aside_limits_by_path(stacked, elongations, colour_camera) if stretched else {}
    statuses: list[FrameStatus] = []
    for frame in frame_list:
        state, elongation, reason = "included", None, ""
        if frame.path not in stacked_paths:
            state = "not_stacked"
        elif frame.path in limits and elongations.get(frame.path, 0.0) >= warning:
            state, elongation = "flagged", float(elongations[frame.path])
            reason = stretched_star_reason(elongation, warning, limits[frame.path])
        statuses.append(
            FrameStatus(
                path=frame.path,
                name=os.path.basename(frame.path),
                state=state,
                reason=reason,
                elongation=elongation,
            )
        )
    for record in set_aside:
        exposure = record.exposure_s
        statuses.append(
            FrameStatus(
                path=record.path,
                name=record.file,
                state="set_aside",
                kind=record.kind,
                reason=record.reason or "",
                camera=record.camera,
                filter=record.filter,
                date=record.date or "",
                exposure_s=None if exposure is None else float(exposure),
                original_path=record.original_path,
                restorable=bool(record.restorable),
                restore_note=record.restore_note or "",
            )
        )
    return FrameStatusReport(frames=statuses, stacked_at=stacked_at)


def list_set_aside_frames_with_reasons(target: Target, config: Any) -> list[SetAsideFrameRecord]:
    """List the frames in a target's `_excluded` folders, with reasons.

    The stacker moves frames with clouds or trailed stars into an `_excluded`
    folder and stores the move with the frame's quality rows. For each frame
    in those folders the reason comes from the stored rows if they carry the
    action taken, else from the folder's `excluded_frames.json` file, else it
    is empty; ``reason_source`` says which. Nothing is moved, measured or
    written, and a library with no quality database gives the folder-based
    answer without creating one.

    Parameters
    ----------
    target : `Target`
        The target.
    config : `AppConfiguration`
        The settings: the frames folder and the quality database.

    Returns
    -------
    frames : `list` [`SetAsideFrameRecord`]
        One entry per frame file in an `_excluded` folder. Empty when none.
    """
    from astrometricslib._internal.drivers.quality_store import QualityStore, quality_database_path
    from astrometricslib.pipelines.stacking.metrics import (
        FRAME_SET_ASIDE_ROUNDNESS,
        FRAME_SET_ASIDE_SKY_MEDIAN,
    )
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import target_folder_names
    from astrometricslib.pipelines.stacking.pre_processing.set_aside_listing import (
        build_set_aside_records,
        find_excluded_folders,
    )

    folders = find_excluded_folders(str(config.get_frames_path()), target_folder_names(target.id))
    if not folders:
        return []
    store = QualityStore(quality_database_path(config))
    rows = [
        row
        for metric in (FRAME_SET_ASIDE_ROUNDNESS, FRAME_SET_ASIDE_SKY_MEDIAN)
        for row in store.query(metric=metric, target_id=target.id, latest_only=True)
    ]
    setting_ids = sorted({
        row.subject.capture_setting_id for row in rows if row.subject.capture_setting_id is not None
    })
    settings = store.get_capture_settings(setting_ids) if setting_ids else {}
    return build_set_aside_records(
        target.id, folders, rows, {key: value.exposure_s for key, value in settings.items()}
    )


def report_frame_statuses(target: Target, config: Any) -> FrameStatusReport:
    """Say what state each frame of a target is in, for a status column.

    Each frame of the target is ``not_stacked`` when it was taken after the
    newest stack file was written (or there is no stack), ``flagged`` when it
    is in the stack and its stored star elongation is at least the warning
    level but the set-aside check kept it, and ``included`` otherwise. The
    frames in the target's `_excluded` folders follow as ``set_aside``, with
    the stacker's kind and reason (`list_set_aside_frames_with_reasons`). The
    newest stack file is looked for among the imaging stack, the spectral
    stack and each saved stack configuration. Nothing is written.

    Parameters
    ----------
    target : `Target`
        The target.
    config : `AppConfiguration`
        The settings: the frames and stacks folders.

    Returns
    -------
    report : `FrameStatusReport`
        ``frames`` and ``stacked_at``.
    """
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import stored_star_elongations
    from astrometricslib.utilities.frame_identity import frame_subject_id

    stacked_at = latest_stack_time(target, str(config.get_stacks_path()))
    stored = stored_star_elongations(target.id)
    elongations = {
        frame.path: stored[subject_id]
        for frame in target.frames
        if (subject_id := frame_subject_id(frame.path, target.id)) in stored
    }
    return build_frame_statuses(
        target.frames, list_set_aside_frames_with_reasons(target, config), stacked_at, elongations
    )


def restore_set_aside_frames(catalog: Any, target: Target, frames: Sequence[FrameRecord | str]) -> Any:
    """Move chosen set-aside frames back into their target, and scan it.

    Each frame must lie in one of the target's `_excluded` folders. A frame
    whose original place is taken, or that has no `excluded_frames.json`
    entry saying where it came from, stays where it is. The target is read
    again afterwards, so it lists the moved frames, and saved; the next
    stack may set them aside again.

    Parameters
    ----------
    catalog : `TargetCatalog`
        The target catalog's state.
    target : `Target`
        The target.
    frames : `Sequence` [`FrameRecord` or `str`]
        The set-aside frames, as ``frames.query(target, status="set_aside")``
        gives them, or their paths.

    Returns
    -------
    restored : `Page` [`FrameRecord`]
        The target's records of the frames that went back, at their original
        paths.

    Raises
    ------
    InvalidArgumentError
        If a frame is not in one of the target's `_excluded` folders.
    """
    from astrometricslib.foundation.errors import InvalidArgumentError
    from astrometricslib.models.page import Page
    from astrometricslib.pipelines.shared.target_frames import scan_frames
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
        restore_set_aside_frames,
        target_folder_names,
    )
    from astrometricslib.pipelines.stacking.pre_processing.set_aside_listing import find_excluded_folders

    paths = [item if isinstance(item, str) else item.path for item in frames]
    folders = {
        os.path.realpath(folder): folder
        for folder in find_excluded_folders(
            str(catalog._config.get_frames_path()), target_folder_names(target.id)
        )
    }
    files_by_folder: dict[str, set[str]] = {}
    for path in paths:
        folder = os.path.realpath(os.path.dirname(path))
        if folder not in folders:
            raise InvalidArgumentError(
                f"'{path}' is not in an _excluded folder of {target.id}.", details={"path": path}
            )
        files_by_folder.setdefault(folders[folder], set()).add(os.path.basename(path))
    restored: list[str] = []
    for folder, file_names in files_by_folder.items():
        restored.extend(restore_set_aside_frames(folder, file_names))
    if restored:
        scan_frames(catalog, target)
    by_path = {os.path.realpath(frame.path): frame for frame in target.frames}
    return Page.of([by_path[real] for path in restored if (real := os.path.realpath(path)) in by_path])
