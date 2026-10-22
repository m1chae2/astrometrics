"""Purpose: Build the reports that `QualityDiagnostics.frame_quality` returns.

Description: `frame_quality` offers three checks of raw light frames, chosen
with ``kind``. The API method only checks its arguments; the work for two of
the kinds is here:

* `raw_check_report` runs the batch check (`raw_frame_check`) on a folder or
  on a target's frames and flags the frames that stand out.
* `input_quality_report` measures the sky level, the saturated pixels and,
  if asked, the star width of a target's newest frames. It measures a copy of
  the target, so the saved catalog never changes.

The third kind, ``quarantine_preview``, runs the check the stacker uses to
set frames aside, so its report lives with that check, in
`pipelines/stacking/pre_processing/frame_quarantine.py`.

Each report here also finds slow drifts across the newest frames
(`find_trends`). None of them saves anything.
"""

import os
from collections.abc import Callable
from typing import Any

from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.quality_reports import InputQualityReport, RawFrameCheckReport
from astrometricslib.models.target import Target
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.quality import frame_selection, frame_statistics
from astrometricslib.pipelines.shared.quality.frame_trends import find_trends

__all__ = ["input_quality_report", "raw_check_report"]

INPUT_QUALITY_METRICS = ("background_level", "saturated_pixel_fraction", "measured_fwhm_px")
"""The numbers `input_quality_report` gives for each frame."""


def _newest_or_first(items: list[Any], selection: frame_selection.FrameSelection, limit: int) -> list[Any]:
    """Keep the first frames of a bounded selection, or the newest otherwise.

    Returns
    -------
    kept : `list`
        At most ``limit`` items: the first ones when the selection has a
        file range or a time bound, the last (newest) ones when it has none.
    """
    return items[:limit] if selection.has_bounds else items[-limit:]


def raw_check_report(
    target: Target | None,
    folder_path: str | None,
    selection: frame_selection.FrameSelection,
    limit: int,
    trend_frames: int,
    trend_threshold_percent: float,
) -> RawFrameCheckReport:
    """Run the batch check on a folder of frames or on a target's frames.

    Parameters
    ----------
    target : `Target` or `None`
        The target whose light frames to check, when no folder is given.
    folder_path : `str` or `None`
        A folder of ``*.fits`` frames, read in file-name order.
    selection : `FrameSelection`
        The filter, file range and time window to keep.
    limit : `int`
        How many frames to check.
    trend_frames : `int`
        How many of the newest frames to look at for a drift.
    trend_threshold_percent : `float`
        How far a number must move, as a percentage, to count as a drift.

    Returns
    -------
    report : `RawFrameCheckReport`
        The rows, the batch medians and the drifts.

    Raises
    ------
    InvalidArgumentError
        If neither a folder nor a target is given.
    """
    from astrometricslib.pipelines.shared.quality.raw_frame_check import check_raw_frames

    if folder_path:
        paths = frame_selection.select_folder_paths(folder_path, selection)
    elif target is not None:
        lights = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
        paths = [frame.path for frame in frame_selection.select_library_frames(lights, selection)]
    else:
        raise InvalidArgumentError("kind='raw_check' needs a folder_path or a target.")
    matching = len(paths)
    paths = _newest_or_first(paths, selection, limit)
    report = RawFrameCheckReport(
        target_id=target.id if target is not None else None,
        folder_path=folder_path,
        frames_matching=matching,
        frames_checked=len(paths),
    )
    if not paths:
        return report.model_copy(update={"batch": {"frame_count": 0}})
    checked = check_raw_frames(paths=paths)
    rows = checked.get("frames", [])
    return report.model_copy(
        update={
            "frames": rows,
            "batch": checked.get("batch", {}),
            "trends": find_trends(
                rows,
                {"fwhm_px": "rising", "star_count": "falling", "sky_median_adu": "either"},
                trend_frames,
                trend_threshold_percent,
            ),
        }
    )


def input_quality_report(
    target: Target,
    selection: frame_selection.FrameSelection,
    camera_id: str | None,
    limit: int,
    measure_fwhm: Callable[[Any], float | None] | None,
    remeasure: bool,
    trend_frames: int,
    trend_threshold_percent: float,
) -> InputQualityReport:
    """Measure the newest light frames of a target, on a copy of it.

    Parameters
    ----------
    target : `Target`
        The target whose frames to measure. It is not changed.
    selection : `FrameSelection`
        The filter, file range and time window to keep.
    camera_id : `str` or `None`
        Only frames whose camera name contains this text, ignoring case.
    limit : `int`
        How many frames to measure.
    measure_fwhm : `Callable` or `None`
        A function that measures the star width from a frame's pixels, such
        as the astrometry pipeline's `measure_fwhm_from_data`, or `None` to
        skip it. Measuring it is about 50 times slower per frame.
    remeasure : `bool`
        Measure again frames that already have stored values.
    trend_frames : `int`
        How many of the newest frames to look at for a drift.
    trend_threshold_percent : `float`
        How far a number must move, as a percentage, to count as a drift.

    Returns
    -------
    report : `InputQualityReport`
        One row per frame, a summary per number, and the drifts.
    """
    wanted = (camera_id or "").lower()
    every_light = [frame for frame in target.frames if str(frame.role).upper() == "LIGHT"]
    chosen = [
        frame
        for frame in frame_selection.select_library_frames(every_light, selection)
        if wanted in (frame.camera or "").lower()
    ]
    frames_matching = len(chosen)
    chosen = _newest_or_first(chosen, selection, limit)
    working = target.model_copy(deep=True)
    working.frames = [frame.model_copy(deep=True) for frame in chosen]
    counts = frame_statistics.measure_frame_input_quality(working, measure_fwhm, remeasure, None)
    rows = [
        {
            "file": os.path.basename(frame.path),
            "camera": frame.camera,
            "exposure": frame.exposure,
            "filter": str(frame.filter),
            **{name: getattr(frame.measurements, name) for name in INPUT_QUALITY_METRICS},
        }
        for frame in working.frames
    ]
    summary = {}
    for name in INPUT_QUALITY_METRICS:
        values = sorted(row[name] for row in rows if row[name] is not None)
        summary[name] = (
            {
                "frames": len(values),
                "minimum": values[0],
                "median": values[len(values) // 2],
                "maximum": values[-1],
            }
            if values
            else {"frames": 0}
        )
    return InputQualityReport(
        target_id=target.id,
        light_frames_in_target=sum(1 for frame in every_light if not frame_is_spectral(frame)),
        spectral_light_frames_in_target=sum(1 for frame in every_light if frame_is_spectral(frame)),
        frames_matching=frames_matching,
        frames_measured=len(rows),
        counts=counts,
        summary=summary,
        trends=find_trends(
            rows,
            {"measured_fwhm_px": "rising", "background_level": "either"},
            trend_frames,
            trend_threshold_percent,
        ),
        frames=rows,
        note="Nothing was saved. Frames that were already measured keep their stored values.",
    )
