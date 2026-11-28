"""Purpose: Leave out of photometry the frames the set-aside check sets aside.

Description: A cloudy, trailed or off-field frame has stars that are missing,
smeared or in the wrong place. If it stays in a session, the comparison-star
choice sees stars that are missing from some frames and drops them. On M 27
on 2026-09-24, 24 of 33 frames were clouded, no star was present in every
frame, and the whole night was left without comparison stars.

This module asks the set-aside check (`frame_set_aside`) which frames it
would set aside, using its own judging function, so photometry and stacking
never disagree about what a bad frame is. It also leaves out frames an earlier
stack already moved into an `_excluded` folder. It moves no file. Each
skipped frame is logged with its reason, so the photometry job lists it among
its warnings (see `run_warnings`).
"""

import logging
import os
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

__all__ = ["SkippedFrame", "skip_set_aside_frames"]

logger = logging.getLogger(__name__)

ALREADY_SET_ASIDE_REASON = (
    "already set aside by the cloud and target alignment check (in an _excluded folder)"
)
"""The reason given for a frame that is already in an `_excluded` folder."""

SkippedFrame = tuple[Any, str]
"""A skipped frame record and the plain-language reason it was skipped."""


def _night_of(frame: Any) -> str:
    """Name the UTC date a frame was taken, for grouping warnings.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    night : `str`
        ``YYYY-MM-DD``, or ``"unknown date"`` when the frame has no usable
        timestamp.
    """
    timestamp = getattr(frame, "timestamp", None)
    if isinstance(timestamp, datetime):
        return timestamp.astimezone(UTC).strftime("%Y-%m-%d")
    try:
        return datetime.fromtimestamp(float(timestamp), UTC).strftime("%Y-%m-%d")
    except TypeError, ValueError, OverflowError, OSError:
        return "unknown date"


def skip_set_aside_frames(
    target_id: str, frames: Sequence[Any], frames_path: str | Path
) -> tuple[list[Any], list[SkippedFrame]]:
    """Split frames into the ones photometry measures and the ones it skips.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    frames : `Sequence`
        The frames photometry would measure.
    frames_path : `str` or `Path`
        The library's frames folder, where the `_excluded` folders are found.

    Returns
    -------
    kept_frames : `list`
        The frames the set-aside check keeps, in their order.
    skipped_frames : `list` [`SkippedFrame`]
        Each skipped frame with its reason: either it is already in
        `_excluded`, or the set-aside check's judgement says it would be set
        aside. One warning per night lists them, so the photometry job
        reports them.
    """
    from astrometricslib.pipelines.stacking.pre_processing.frame_set_aside import (
        find_frames_already_set_aside,
        find_frames_to_set_aside,
    )

    skipped: dict[str, SkippedFrame] = {}
    for frame in find_frames_already_set_aside(target_id, frames, str(frames_path)):
        skipped[frame.path] = (frame, ALREADY_SET_ASIDE_REASON)
    to_judge = [frame for frame in frames if frame.path not in skipped]
    if to_judge:
        report = find_frames_to_set_aside(to_judge, target_id=target_id)
        by_path = {frame.path: frame for frame in to_judge}
        for decision in report.moved:
            frame = by_path.get(decision.path)
            if frame is not None:
                skipped[frame.path] = (frame, f"{decision.kind}: {decision.reason}")
        for note in report.notes:
            logger.info("[%s] Set-aside check check for photometry: %s", target_id, note)

    skipped_frames = [skipped[frame.path] for frame in frames if frame.path in skipped]
    kept_frames = [frame for frame in frames if frame.path not in skipped]
    _log_skipped_frames(target_id, skipped_frames, len(frames))
    return kept_frames, skipped_frames


def _log_skipped_frames(target_id: str, skipped_frames: Sequence[SkippedFrame], frame_count: int) -> None:
    """Log one warning for each night that lost frames, naming every frame.

    The warning is finished text with no arguments, so the run's warning
    collector keeps it whole and counts each night as its own warning.

    Parameters
    ----------
    target_id : `str`
        The target's id.
    skipped_frames : `Sequence` [`SkippedFrame`]
        The skipped frames and reasons.
    frame_count : `int`
        How many frames were checked, for the sentence.
    """
    by_night: dict[str, list[SkippedFrame]] = defaultdict(list)
    for skipped in skipped_frames:
        by_night[_night_of(skipped[0])].append(skipped)
    for night, night_frames in sorted(by_night.items()):
        listing = "; ".join(f"{os.path.basename(frame.path)} ({reason})" for frame, reason in night_frames)
        text = (
            f"[{target_id}] Photometry skipped {len(night_frames)} frame(s) of the night {night} "
            f"({frame_count} frames checked in all) that the set-aside check sets aside: {listing}"
        )
        # Finished text with no arguments, so the collector keeps each
        # night's warning whole instead of merging the nights into one.
        logger.warning(text)
