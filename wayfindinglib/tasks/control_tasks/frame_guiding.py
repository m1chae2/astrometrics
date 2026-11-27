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

from astrometricslib import Target
from astrometricslib.arguments import FrameChoice, Page, check_include
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.parameters import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from wayfindinglib.analytics.result_quality.guiding_join import (
    FrameWindow,
    guiding_read_range,
    join_frames_to_guiding,
)
from wayfindinglib.models.session.control_results import FrameGuidingReport

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext


FRAME_GUIDING_PARTS = ("quality",)
"""The heavy parts `link_frames_to_guiding` can add."""


GUIDE_LOG_SOURCE = "ekos_guide_log"
"""The guiding samples that are real star measurements."""

SLOW_FRAME_RATIO = TunedParameter(
    name="wayfinding.slow_frame_ratio",
    default=2.0,
    unit="× group median",
    description="A frame whose guide error exceeds this multiple of the group median is listed as worse.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.TUNED_PARAMETER,
    equipment=frozenset({EquipmentKind.MOUNT}),
    register_entry="WAY-D142",
    allowed_range=(1.2, 10.0),
    minimum_sample=100,  # a sample is one frame with guide samples inside its exposure
)
"""A frame whose guide error is above this multiple of the group's median
is listed as worse than its neighbours. The ratio is relative, so it adapts
to the equipment: it is a flag for a person to look at, not a pass mark.
Checked on the 2026-10-02 session, where ordinary frames stayed within
about 1.5 times the median and frames hit by a dither or drift were above 3."""

MAXIMUM_FRAMES = 200
"""Most frames one answer covers: the newest of the chosen frames."""

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


def _add_image_quality(astrometrics: Any, target: Any, frames: list[dict[str, Any]]) -> str:
    """Measure the frames' images and put the numbers on the guiding rows.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library, for the frame quality check.
    target : `Target`
        The target the frames belong to.
    frames : `list` [`dict`]
        The guiding rows, changed in place.

    Returns
    -------
    note : `str`
        What was done, including any frames left unmeasured.
    """
    wanted = [row["file"] for row in frames][:MAXIMUM_QUALITY_FRAMES]
    from astrometricslib.exceptions import InvalidArgumentError, NotFoundError

    wanted_files = set(wanted)
    chosen = [
        frame
        for frame in target.frames
        if os.path.basename(frame.path) in wanted_files
        and str(frame.role).upper() == "LIGHT"
        and not frame.is_spectral
    ]
    if not chosen:
        return "Image quality could not be measured: none of the frames is an imaging light frame."
    try:
        checks = astrometrics.quality.frames.measure(chosen).checks
    except (InvalidArgumentError, NotFoundError) as error:
        return f"Image quality could not be measured: {error}"
    by_file = {os.path.basename(row["path"]): row for row in (checks.frames if checks is not None else [])}
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


def _chosen_frames(target: Target, frames: FrameChoice | None) -> list[Any]:
    """Turn a frame choice into the target's frame records, oldest first.

    Parameters
    ----------
    target : `Target`
        The target the frames belong to.
    frames : `FrameChoice` or `None`
        A page or list of frame records, a list of paths, or `None` for
        every light frame of the target.

    Returns
    -------
    records : `list` [`FrameRecord`]
        The chosen imaging and spectral light frames, by start time.

    Raises
    ------
    InvalidArgumentError
        If `frames` is a page cut short by its ``limit``.
    NotFoundError
        If a path is not one of the target's frames.
    """
    if isinstance(frames, Page) and not frames.is_complete:
        raise InvalidArgumentError("frames is a page cut short by limit; ask for every matching frame.")
    lights = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
    if frames is None:
        chosen = lights
    else:
        by_path = {os.path.normpath(frame.path): frame for frame in target.frames}
        chosen = []
        for item in frames:
            path = os.path.normpath(str(getattr(item, "path", item)))
            if path not in by_path:
                raise NotFoundError(f"{path} is not a frame of target {target.id!r}.", details={"path": path})
            chosen.append(by_path[path])
    return sorted(chosen, key=lambda frame: frame.timestamp)


def link_frames_to_guiding(
    context: ControlContext,
    target: Target,
    frames: FrameChoice | None = None,
    include: list[str] | None = None,
) -> FrameGuidingReport:
    """Report the guide error during each chosen frame's exposure.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the guiding records.
    target : `Target`
        The library target whose light frames to match.
    frames : `FrameChoice`, optional
        The frames, from ``astrometrics.targets.frames.query``, or a list of
        paths. Every light frame of the target when omitted. At most the
        newest `MAXIMUM_FRAMES` of them are reported.
    include : `list` [`str`], optional
        ``["quality"]`` also measures each frame's image (star count, star
        width, roundness, longest trail, sky level, saturated pixels, and
        the quality flags) and puts it on the same row. Slow: about a
        second a frame, so at most `MAXIMUM_QUALITY_FRAMES` frames.

    Returns
    -------
    report : `FrameGuidingReport`
        ``frames`` (one row per frame) and ``group`` (the median error and
        the frames well above it).

    Notes
    -----
    Raises `InvalidArgumentError` if `include` names an unknown part or
    `frames` is a page cut short, and `NotFoundError` if a path is not one
    of the target's frames.
    """
    include_quality = "quality" in check_include(include, FRAME_GUIDING_PARTS)
    every = _chosen_frames(target, frames)
    matching = len(every)
    chosen = every[-MAXIMUM_FRAMES:]
    if not chosen:
        return FrameGuidingReport(target_id=target.id, frames_matching=matching, frames=[])

    windows = []
    for frame in chosen:
        try:
            exposure_s = float(frame.exposure_s)
        except ValueError:
            exposure_s = 0.0
        windows.append((frame, frame.timestamp, frame.timestamp + exposure_s, exposure_s))
    frame_windows = [
        FrameWindow(
            frame_id=os.path.basename(frame.path),
            start_epoch_seconds=start,
            exposure_s=exposure_s,
        )
        for frame, start, _end, exposure_s in windows
    ]
    read_start, read_end = guiding_read_range(frame_windows) or (0.0, 0.0)
    rows = context.records.get_guiding_samples(
        start_time=read_start, limit=500_000, sources=[GUIDE_LOG_SOURCE]
    )
    rows = [row for row in rows if row["timestamp"] <= read_end]
    joined = join_frames_to_guiding(frame_windows, rows)

    frames = []
    for (frame, start, end, exposure_s), statistics_for_frame in zip(windows, joined, strict=True):
        frames.append({
            "file": os.path.basename(frame.path),
            "filter": str(getattr(frame.filter, "value", frame.filter)),
            "exposure_s": exposure_s,
            "starts_at": _iso(start),
            "ends_at": _iso(end),
            "pier_side": frame.pier_side,
            **statistics_for_frame,
        })

    quality_note = None
    if include_quality:
        quality_note = _add_image_quality(context.astrometrics, target, frames)

    measured = [row["rms_total_arcsec"] for row in frames if row.get("rms_total_arcsec") is not None]
    median_error = statistics.median(measured) if measured else None
    worse = []
    if median_error:
        slow_frame_ratio = resolve_parameter(SLOW_FRAME_RATIO).value
        for row in frames:
            value = row.get("rms_total_arcsec")
            if value is not None and value > slow_frame_ratio * median_error:
                row["rms_vs_group_median"] = round(value / median_error, 1)
                worse.append(row["file"])
    return FrameGuidingReport.model_validate({
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
    })
