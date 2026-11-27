"""Purpose: Choose a target's frames, for ``targets.frames.query``.

Description: `query_frames` is the owner of ``TargetFrames.query``. It keeps
a target's timed light frames that pass every filter given: the observing
night, a time range, the filter, a file name range, the observatory
configuration, the gain, the exposure length and the frame's state in the
newest stack (`frame_status`). The frames in the target's `_excluded` folders
are chosen with ``status="set_aside"``; the target's own frame list no longer
holds them, so their records are read from their headers.
"""

import os
from datetime import date
from typing import Any, Literal

from astrometricslib.foundation.arguments import TimeInput, check_choice, check_limit
from astrometricslib.models.observatory_configuration import ObservatoryConfiguration
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.target_frames import night_text, stored_number

__all__ = ["FRAME_ORDERS", "FRAME_STATUSES", "query_frames"]

FRAME_STATUSES = ("included", "flagged", "not_stacked", "set_aside")
"""The states a frame can be in, as `query_frames` filters them."""

FRAME_ORDERS = ("time", "newest", "name")
"""The orders `query_frames` sorts by: oldest first, newest first, or name."""


def _set_aside_records(target: Target, config: Any) -> list[FrameRecord]:
    """Give the frames in a target's `_excluded` folders as frame records.

    Parameters
    ----------
    target : `Target`
        The target.
    config : `AppConfiguration`
        The settings, for the frames folder.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        One record per set-aside file, with the camera, filter, time and
        exposure its header gives.
    """
    from astrometricslib.pipelines.shared.frame_scanning import create_frame_record_from_fits
    from astrometricslib.pipelines.stacking.frame_status import list_set_aside_frames_with_reasons

    records = []
    for entry in list_set_aside_frames_with_reasons(target, config):
        record = create_frame_record_from_fits(entry.path, config=config)
        if entry.exposure_seconds is not None:
            record.exposure = str(entry.exposure_seconds)
        records.append(record)
    return records


def query_frames(
    target: Target,
    config: Any,
    *,
    night: str | date | None = None,
    since: TimeInput | None = None,
    until: TimeInput | None = None,
    filter_name: str | None = None,
    first_file: str | None = None,
    last_file: str | None = None,
    configuration: ObservatoryConfiguration | None = None,
    gain: float | None = None,
    exposure_s: float | None = None,
    status: Literal["included", "flagged", "not_stacked", "set_aside"] | None = None,
    order_by: Literal["time", "newest", "name"] | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> Page[FrameRecord]:
    """Pick a target's light frames.

    Only light frames with a start time are kept. Spectroscopy frames are
    left out unless ``filter_name`` asks for one. Frames with status
    ``"set_aside"`` are the files in the target's `_excluded` folders, which
    the target's own frame list no longer holds.

    Parameters
    ----------
    target : `Target`
        The target.
    config : `AppConfiguration`
        The settings: the frames folder and the camera profiles.
    night : `str` or `date`, optional
        Only frames of this observing night, ``YYYY-MM-DD``.
    since, until : `TimeInput`, optional
        Only frames that began in this time range (no zone means UTC).
    filter_name : `str`, optional
        Only frames whose filter matches this text, ignoring case.
    first_file, last_file : `str`, optional
        Only frames whose file name sorts from ``first_file`` to
        ``last_file``. A short bound such as ``"013"`` is matched against the
        frame number at the end of the name.
    configuration : `ObservatoryConfiguration`, optional
        Only frames taken with this equipment.
    gain : `float`, optional
        Only frames with this gain (or ISO) setting.
    exposure_s : `float`, optional
        Only frames of this exposure length, in seconds.
    status : `str`, optional
        Only frames in this state of the newest stack: ``"included"``,
        ``"flagged"``, ``"not_stacked"`` or ``"set_aside"``.
    order_by : `str`, optional
        ``"time"`` (oldest first, the default), ``"newest"`` or ``"name"``.
    limit : `int` or `None`, optional
        The most frames to return. `None` returns every match.
    offset : `int`, optional
        How many matches to skip first.

    Returns
    -------
    frames : `Page` [`FrameRecord`]
        The matching frames.

    Notes
    -----
    Raises `InvalidArgumentError` if a time cannot be read, a choice is
    unknown, or ``limit`` or ``offset`` is out of range.
    """
    from astrometricslib.pipelines.shared.equipment import frame_matches
    from astrometricslib.pipelines.shared.quality import frame_selection
    from astrometricslib.utilities.observing_night import observing_night_id

    if status is not None:
        check_choice("status", status, FRAME_STATUSES)
    if order_by is not None:
        check_choice("order_by", order_by, FRAME_ORDERS)
    check_limit(limit, 1, None)
    check_limit(offset, 0, None, name="offset", allow_none=False)

    source = _set_aside_records(target, config) if status == "set_aside" else list(target.frames)
    frames = frame_selection.select_target_light_frames(
        source, filter_name=filter_name, first_file=first_file, last_file=last_file, since=since, until=until
    )
    wanted_night = night_text(night)
    if wanted_night is not None:
        frames = [
            frame
            for frame in frames
            if frame.timestamp is not None and observing_night_id(frame.timestamp) == wanted_night
        ]
    if configuration is not None:
        frames = [frame for frame in frames if frame_matches(frame, configuration, config)]
    if gain is not None:
        frames = [
            frame
            for frame in frames
            if (value := stored_number(frame.iso)) is not None and abs(value - float(gain)) < 0.1
        ]
    if exposure_s is not None:
        frames = [
            frame
            for frame in frames
            if (value := stored_number(frame.exposure)) is not None and abs(value - float(exposure_s)) < 0.001
        ]
    if status in ("included", "flagged", "not_stacked"):
        from astrometricslib.pipelines.stacking.frame_status import report_frame_statuses

        states = {entry.path: entry.state for entry in report_frame_statuses(target, config).frames}
        frames = [frame for frame in frames if states.get(frame.path) == status]
    if order_by == "newest":
        frames = sorted(frames, key=lambda frame: frame.timestamp or 0.0, reverse=True)
    elif order_by == "name":
        frames = sorted(frames, key=lambda frame: os.path.basename(frame.path).lower())
    return Page.of(frames, offset=offset, limit=limit)
