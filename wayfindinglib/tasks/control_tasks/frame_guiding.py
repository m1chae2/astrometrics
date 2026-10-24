"""Purpose: Match each light frame's exposure window to the guiding records.

Description: To say whether wind or a bad dither spoiled a frame, one needs
the guide error during that frame's exposure. A frame carries the time it
began and its exposure length. The guiding records carry one error
measurement every few seconds. This module cuts the guiding records to each
frame's window and reports the error inside it. It reads only; nothing is
stored.

Only guide-log samples are used. The pulse-based estimates the app makes
while guiding live are not measurements of the star, so they are left out.
"""

from __future__ import annotations

import os
import statistics
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import FrameSelection, NotFoundError, select_library_frames
from wayfindinglib.analytics.result_quality.guiding_join import (
    FrameWindow,
    guiding_read_range,
    join_frames_to_guiding,
)

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

GUIDE_LOG_SOURCE = "ekos_guide_log"
"""The guiding samples that are real star measurements."""

SLOW_FRAME_RATIO = 2.0
"""A frame whose guide error is above this multiple of the group's median
is listed as worse than its neighbours. The ratio is relative, so it adapts
to the equipment: it is a flag for a person to look at, not a pass mark.
Checked on the 2026-10-02 session, where ordinary frames stayed within
about 1.5 times the median and frames hit by a dither or drift were above 3."""

MAXIMUM_FRAMES = 200
"""Most frames one answer covers."""

MAXIMUM_QUALITY_FRAMES = 60
"""Most frames whose image quality is measured in one answer. A frame takes
about a second to measure, so a longer table would make the call slow."""

QUALITY_COLUMNS = (
    "star_count",
    "fwhm_px",
    "roundness",
    "longest_trail_px",
    "sky_median_adu",
    "saturated_pixels",
)
"""The per-frame image measurements joined onto each guiding row."""


def _iso(seconds: float) -> str:
    """Write a Unix time as an ISO 8601 UTC string.

    Parameters
    ----------
    seconds : `float`
        Seconds since the Unix epoch.

    Returns
    -------
    text : `str`
        The time, to the second.
    """
    return datetime.fromtimestamp(seconds, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _add_image_quality(
    astrometrics: Any, target: Any, selection: FrameSelection, frames: list[dict[str, Any]]
) -> str:
    """Measure the frames' images and put the numbers on the guiding rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library, for the frame quality check.
    target : `Target`
        The target the frames belong to.
    selection : `FrameSelection`
        The same selection used for the guiding rows.
    frames : `list` [`dict`]
        The guiding rows, changed in place.

    Returns
    -------
    note : `str`
        What was done, including any frames left unmeasured.
    """
    wanted = [row["file"] for row in frames][:MAXIMUM_QUALITY_FRAMES]
    from astrometricslib import InvalidArgumentError, NotFoundError

    try:
        report = astrometrics.quality.measure_frames(
            target=target,
            kind="raw_check",
            include=["spectra"] if selection.include_spectra else None,
            filter_name=selection.filter_name,
            first_file=wanted[0],
            last_file=wanted[-1],
            limit=len(wanted),
            register_job=False,
        )
    except (InvalidArgumentError, NotFoundError) as error:
        return f"Image quality could not be measured: {error}"
    by_file = {os.path.basename(row["path"]): row for row in report.frames}
    for row in frames:
        measured = by_file.get(row["file"])
        if measured is None:
            continue
        for column in QUALITY_COLUMNS:
            row[column] = measured.get(column)
        row["quality_flags"] = measured.get("flags", [])
    unmeasured = len(frames) - sum(1 for row in frames if "star_count" in row)
    note = f"Image quality measured for {len(frames) - unmeasured} frame(s)."
    if unmeasured:
        note += f" {unmeasured} frame(s) have no image measurement (limit {MAXIMUM_QUALITY_FRAMES} frames)."
    return note


def link_frames_to_guiding(
    context: ControlContext,
    target_id: str,
    selection: FrameSelection,
    limit: int,
    include_quality: bool = False,
) -> dict[str, Any]:
    """Report the guide error during each selected frame's exposure.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the guiding records.
    target_id : `str`
        The library target whose light frames to match.
    selection : `FrameSelection`
        Which frames: filter, file range, time range.
    limit : `int`
        How many frames to cover, from 1 to `MAXIMUM_FRAMES`. With a range
        or time bound these are the first frames in it, otherwise the newest.
    include_quality : `bool`, optional
        Also measure each frame's image (star count, star width, roundness,
        longest trail, sky level, saturated pixels, and the quality flags)
        and put it on the same row. Slow: about a second a frame, so at
        most `MAXIMUM_QUALITY_FRAMES` of the chosen frames.

    Returns
    -------
    report : `dict` [`str`, `Any`]
        ``frames`` (one row per frame) and ``group`` (the median error and
        the frames well above it).

    Raises
    ------
    NotFoundError
        If no target has the id `target_id`.
    """
    limit = max(1, min(int(limit), MAXIMUM_FRAMES))
    target = context.astrometrics.targets.get(target_id, refresh=True)
    if target is None:
        raise NotFoundError(
            f"No target with id {target_id!r} in the library.", details={"target_id": target_id}
        )
    lights = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
    chosen = select_library_frames(lights, selection)
    chosen = [frame for frame in chosen if frame.timestamp is not None]
    matching = len(chosen)
    chosen = chosen[:limit] if selection.has_bounds else chosen[-limit:]
    if not chosen:
        return {"target_id": target.id, "frames_matching": matching, "frames": []}

    windows = []
    for frame in chosen:
        try:
            exposure_seconds = float(frame.exposure)
        except ValueError:
            exposure_seconds = 0.0
        windows.append((frame, frame.timestamp, frame.timestamp + exposure_seconds, exposure_seconds))
    frame_windows = [
        FrameWindow(
            frame_id=os.path.basename(frame.path),
            start_epoch_seconds=start,
            exposure_seconds=exposure_seconds,
        )
        for frame, start, _end, exposure_seconds in windows
    ]
    read_start, read_end = guiding_read_range(frame_windows) or (0.0, 0.0)
    rows = context.records.get_guiding_samples(
        start_time=read_start, limit=500_000, sources=[GUIDE_LOG_SOURCE]
    )
    rows = [row for row in rows if row["timestamp"] <= read_end]
    joined = join_frames_to_guiding(frame_windows, rows)

    frames = []
    for (frame, start, end, exposure_seconds), statistics_for_frame in zip(windows, joined, strict=True):
        frames.append({
            "file": os.path.basename(frame.path),
            "filter": str(getattr(frame.filter, "value", frame.filter)),
            "exposure_seconds": exposure_seconds,
            "starts_at": _iso(start),
            "ends_at": _iso(end),
            "pier_side": frame.pier_side,
            **statistics_for_frame,
        })

    quality_note = None
    if include_quality:
        quality_note = _add_image_quality(context.astrometrics, target, selection, frames)

    measured = [row["rms_total_arcsec"] for row in frames if row.get("rms_total_arcsec") is not None]
    median_error = statistics.median(measured) if measured else None
    worse = []
    if median_error:
        for row in frames:
            value = row.get("rms_total_arcsec")
            if value is not None and value > SLOW_FRAME_RATIO * median_error:
                row["rms_vs_group_median"] = round(value / median_error, 1)
                worse.append(row["file"])
    return {
        "target_id": target.id,
        "frames_matching": matching,
        "frames_reported": len(frames),
        "frames_without_guiding": sum(1 for row in frames if not row["guide_samples"]),
        "group": {"median_rms_total_arcsec": median_error, "worse_than_group": worse},
        "note": (
            "A frame's window runs from its recorded start time for its exposure length. Only guide-log "
            "samples count. Frames outside the ingested guide logs show guide_samples of 0."
        ),
        "image_quality": quality_note,
        "frames": frames,
    }
