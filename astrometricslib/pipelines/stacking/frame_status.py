"""Purpose: Say what each frame of a target is doing in its stack.

Description: A frame is in one of four states:

* ``included``: it is in the library and was taken before the latest stack
  was made, so that stack used it (unless the stacker leaves it out for a
  reason it records elsewhere).
* ``flagged``: an included frame whose stars are stretched. Its stored star
  elongation (long axis over short axis) is at least the warning level
  (`STRETCHED_STAR_WARNING_ELONGATION`), but the quarantine kept it, because
  it is not above the set-aside threshold of its session
  (`set_aside_elongation_limit`). The reason gives the three numbers. A
  stacked frame above the threshold (put back by hand, or in a session too
  small for the quarantine to judge) is flagged too, and its reason says so.
* ``not_stacked``: it was taken after the latest stack was made, or the
  target has no stack yet.
* ``set_aside``: the stacker moved it into an `_excluded` folder. Such a frame
  is no longer part of the target's frame list, so it is listed from the
  folder, with the reason the stacker recorded.

The set-aside threshold is found again here the way the quarantine found it:
the stacked light frames are split into the quarantine's sessions
(`split_into_batches`), and each session's threshold comes from its frames'
stored elongations. The frames already set aside are no longer in the list,
so the session median can sit a little lower than when the stack was made.
"""

import os
from collections.abc import Callable, Iterable, Mapping, Sequence

from astrometricslib._internal.drivers.fits_access import frame_uses_color_filter_array
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.frame_status import FrameStatus, FrameStatusReport
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    MINIMUM_FRAMES_TO_JUDGE,
    STRETCHED_STAR_WARNING_ELONGATION,
    set_aside_elongation_limit,
    split_into_batches,
)

__all__ = ["build_frame_statuses", "set_aside_limits_by_path", "stretched_star_reason"]


def set_aside_limits_by_path(
    frames: Sequence[FrameRecord],
    elongations: Mapping[str, float],
    colour_camera: Callable[[str], bool | None] = frame_uses_color_filter_array,
) -> dict[str, float | None]:
    """Find the quarantine's set-aside threshold for each stacked light frame.

    Parameters
    ----------
    frames : `Sequence` [`FrameRecord`]
        The frames that are in the stack. Spectroscopy frames and frames that
        are not lights are left out, as the quarantine leaves them out.
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
        which the quarantine does not judge, or with no stored elongation.
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
        quarantine does not judge the session.

    Returns
    -------
    reason : `str`
        For example "Stacked, but its stars are stretched: elongation 1.25,
        warning level 1.20, set-aside threshold 1.32."
    """
    words = f"Stacked, but its stars are stretched: elongation {elongation:.2f}, warning level {warning:.2f}"
    if limit is None:
        return f"{words}; its session is too small for the quarantine to judge."
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
        capture setting the quarantine groups by).
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
        exposure = record.exposure_seconds
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
                exposure="" if exposure is None else f"{float(exposure):g}",
                original_path=record.original_path,
                restorable=bool(record.restorable),
                restore_note=record.restore_note or "",
            )
        )
    return FrameStatusReport(frames=statuses, stacked_at=stacked_at)
