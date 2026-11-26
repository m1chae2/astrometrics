"""Purpose: Say what each frame of a target is doing in its stack.

Description: A frame is in one of four states:

* ``included``: it is in the library and was taken before the latest stack
  was made, so that stack used it (unless the stacker leaves it out for a
  reason it records elsewhere).
* ``flagged``: an included frame whose stars are stretched far more than the
  other frames' stars. The caller names these frames, with their elongation
  (long axis over short axis); the star-shape review finds them.
* ``not_stacked``: it was taken after the latest stack was made, or the
  target has no stack yet.
* ``set_aside``: the stacker moved it into an `_excluded` folder. Such a frame
  is no longer part of the target's frame list, so it is listed from the
  folder, with the reason the stacker recorded.
"""

import os
from collections.abc import Iterable, Mapping, Sequence

from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.quality.frame_status import FrameStatus, FrameStatusReport
from astrometricslib.models.target import FrameRecord

__all__ = ["build_frame_statuses"]


def build_frame_statuses(
    frames: Iterable[FrameRecord],
    set_aside: Sequence[SetAsideFrameRecord],
    stacked_at: float | None,
    flagged: Mapping[str, float] | None = None,
    typical_elongation: float | None = None,
) -> FrameStatusReport:
    """Say what state each frame is in.

    Parameters
    ----------
    frames : `Iterable` [`FrameRecord`]
        The target's frame records (each with ``path`` and ``timestamp``).
    set_aside : `Sequence` [`SetAsideFrameRecord`]
        The frames in the target's `_excluded` folders.
    stacked_at : `float` or `None`
        When the latest stack was written, in seconds since the Unix epoch,
        or `None` when there is none.
    flagged : `Mapping` [`str`, `float`], optional
        The frames whose stars stand out as stretched, by path, with their
        elongation.
    typical_elongation : `float`, optional
        The typical elongation they stand out from, passed on in the report.

    Returns
    -------
    report : `FrameStatusReport`
        One status per frame, then one per set-aside frame. A frame with no
        recorded time counts as taken before the stack.
    """
    flagged = flagged or {}
    statuses: list[FrameStatus] = []
    for frame in frames:
        taken_after_stack = stacked_at is None or (
            frame.timestamp is not None and float(frame.timestamp) > stacked_at
        )
        if taken_after_stack:
            state, elongation = "not_stacked", None
        elif frame.path in flagged:
            state, elongation = "flagged", float(flagged[frame.path])
        else:
            state, elongation = "included", None
        statuses.append(
            FrameStatus(
                path=frame.path, name=os.path.basename(frame.path), state=state, elongation=elongation
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
    return FrameStatusReport(frames=statuses, stacked_at=stacked_at, typical_elongation=typical_elongation)
