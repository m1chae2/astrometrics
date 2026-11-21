"""What each frame of a target is doing in the stack, for the Status column.

A frame is in one of three states:

* ``included``: it is in the library and was taken before the latest stack
  was made, so that stack used it (unless the stacker leaves it out for a
  reason it records elsewhere).
* ``not_stacked``: it was taken after the latest stack was made, or the
  target has no stack yet.
* ``set_aside``: the stacker moved it into an `_excluded` folder. Such a
  frame is no longer part of the target's frame list, so it is listed here
  from the folder, with the reason the stacker recorded.

The "flagged" state, a frame in the stack whose stars are stretched far more
than the others, is not decided here. It comes from the star-shape review
(`advisory:star_shapes`), which the screen already reads.

This module only builds the answer. `TargetService.get_frame_statuses` reads
the target and the stack files and calls it.
"""

import os
import re
from collections.abc import Iterable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["FrameStatus", "FrameStatusReport", "build_frame_statuses"]

_FILE_NAME_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})")
"""The date and time a capture program writes into a frame's file name."""


class FrameStatus(BaseModel):
    """The status of one frame, with the reason when there is one.

    Attributes
    ----------
    path : `str`
        The frame's file. For a set-aside frame, its place in `_excluded`.
    name : `str`
        The file name.
    state : `str`
        ``included``, ``not_stacked`` or ``set_aside``.
    kind : `str` or `None`
        The kind the stacker recorded for a set-aside frame, such as
        ``clouded``, ``trailed`` or ``off_field``. `None` when none is
        recorded.
    reason : `str`
        The stacker's own words for a set-aside frame; empty otherwise.
    date : `str`
        When the frame was taken, as ``YYYY-MM-DD HH:MM:SS``. Empty when the
        file name has no date (set-aside frames only).
    camera : `str`
        The camera name from the frame's header (set-aside frames only).
    filter : `str`
        The filter name from the frame's header (set-aside frames only).
    exposure : `str`
        The exposure in seconds, for a set-aside frame; empty otherwise.
    original_path : `str` or `None`
        Where a set-aside frame was before it was moved.
    restorable : `bool`
        Whether the app can put a set-aside frame back.
    restore_note : `str`
        Why a set-aside frame cannot be put back, when it cannot.
    """

    model_config = ConfigDict(populate_by_name=True)

    path: str
    name: str
    state: str
    kind: str | None = None
    reason: str = ""
    date: str = ""
    camera: str = ""
    filter: str = ""
    exposure: str = ""
    original_path: str | None = Field(default=None, alias="originalPath")
    restorable: bool = False
    restore_note: str = Field(default="", alias="restoreNote")


class FrameStatusReport(BaseModel):
    """The statuses of every frame of a target.

    Attributes
    ----------
    frames : `list` [`FrameStatus`]
        One entry per frame in the target, then one per set-aside frame.
    stacked_at : `float` or `None`
        When the latest stack file was written, in seconds since the Unix
        epoch. `None` when the target has no stack.
    """

    model_config = ConfigDict(populate_by_name=True)

    frames: list[FrameStatus] = Field(default_factory=list)
    stacked_at: float | None = Field(default=None, alias="stackedAt")


def _date_from_file_name(file_name: str) -> str:
    """Read the capture time out of a frame's file name.

    Parameters
    ----------
    file_name : `str`
        The frame's file name.

    Returns
    -------
    date : `str`
        ``YYYY-MM-DD HH:MM:SS``, or an empty string when the name has none.
    """
    match = _FILE_NAME_DATE.search(file_name)
    if match is None:
        return ""
    day, hour, minute, second = match.groups()
    return f"{day} {hour}:{minute}:{second}"


def build_frame_statuses(
    frames: Iterable[Any],
    set_aside: Sequence[Any],
    stacked_at: float | None,
) -> FrameStatusReport:
    """Say what state each frame is in.

    Parameters
    ----------
    frames : `Iterable`
        The target's frame records (each with ``path`` and ``timestamp``).
    set_aside : `Sequence`
        The records `QualityRecords.set_aside_frames` returns.
    stacked_at : `float` or `None`
        When the latest stack was written, or `None` when there is none.

    Returns
    -------
    report : `FrameStatusReport`
        A frame with no recorded time counts as taken before the stack.
    """
    statuses: list[FrameStatus] = []
    for frame in frames:
        taken_after_stack = stacked_at is None or (
            frame.timestamp is not None and float(frame.timestamp) > stacked_at
        )
        statuses.append(
            FrameStatus(
                path=frame.path,
                name=os.path.basename(frame.path),
                state="not_stacked" if taken_after_stack else "included",
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
                date=record.date or _date_from_file_name(record.file),
                exposure="" if exposure is None else f"{float(exposure):g}",
                original_path=record.original_path,
                restorable=bool(record.restorable),
                restore_note=record.restore_note or "",
            )
        )
    return FrameStatusReport(frames=statuses, stacked_at=stacked_at)
