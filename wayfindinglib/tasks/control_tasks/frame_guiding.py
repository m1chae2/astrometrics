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

import os
import statistics
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any

from astrometricslib import FrameSelection, select_library_frames

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


def _window_statistics(samples: list[dict[str, Any]], exposure_seconds: float) -> dict[str, Any]:
    """Summarise the guide error of the samples inside one window.

    Parameters
    ----------
    samples : `list` [`dict`]
        The guiding rows inside the window, in time order. ``dra`` and
        ``ddec`` are arcseconds.
    exposure_seconds : `float`
        The window length, used to say how much of it the samples cover.

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        The sample count, the coverage, and the RMS and peak error.
    """
    if not samples:
        return {"guide_samples": 0}
    ra_errors = [float(row["dra"]) for row in samples]
    dec_errors = [float(row["ddec"]) for row in samples]
    total_errors = [(ra**2 + dec**2) ** 0.5 for ra, dec in zip(ra_errors, dec_errors, strict=True)]
    gaps = [later["timestamp"] - earlier["timestamp"] for earlier, later in pairwise(samples)]
    cadence = statistics.median(gaps) if gaps else None
    coverage = min(1.0, len(samples) * cadence / exposure_seconds) if cadence and exposure_seconds else None
    return {
        "guide_samples": len(samples),
        "coverage_fraction": round(coverage, 2) if coverage is not None else None,
        "rms_ra_arcsec": round((sum(value**2 for value in ra_errors) / len(ra_errors)) ** 0.5, 2),
        "rms_dec_arcsec": round((sum(value**2 for value in dec_errors) / len(dec_errors)) ** 0.5, 2),
        "rms_total_arcsec": round((sum(value**2 for value in total_errors) / len(total_errors)) ** 0.5, 2),
        "peak_total_arcsec": round(max(total_errors), 2),
    }


def link_frames_to_guiding(
    control: Any,
    target_id: str,
    selection: FrameSelection,
    limit: int,
) -> dict[str, Any]:
    """Report the guide error during each selected frame's exposure.

    Parameters
    ----------
    control : `ObservatoryControl`
        Supplies the library configuration and the guiding records.
    target_id : `str`
        The library target whose light frames to match.
    selection : `FrameSelection`
        Which frames: filter, file range, time range.
    limit : `int`
        How many frames to cover, from 1 to `MAXIMUM_FRAMES`. With a range
        or time bound these are the first frames in it, otherwise the newest.

    Returns
    -------
    report : `dict` [`str`, `Any`]
        ``frames`` (one row per frame) and ``group`` (the median error and
        the frames well above it), or ``{"error": ...}``.
    """
    from astrometricslib import Astrometrics

    limit = max(1, min(int(limit), MAXIMUM_FRAMES))
    target = Astrometrics(control._config).targets.get(target_id)
    if target is None:
        return {"error": f"No target with id {target_id!r} in the library."}
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
    first_start = min(window[1] for window in windows)
    last_end = max(window[2] for window in windows)
    rows = control._logger_interface.get_guiding_logs(
        start_time=first_start - 5.0, limit=500_000, sources=[GUIDE_LOG_SOURCE]
    )
    rows = sorted(
        (row for row in rows if row["timestamp"] <= last_end + 5.0), key=lambda row: row["timestamp"]
    )

    frames = []
    for frame, start, end, exposure_seconds in windows:
        inside = [row for row in rows if start <= row["timestamp"] < end]
        frames.append({
            "file": os.path.basename(frame.path),
            "filter": str(getattr(frame.filter, "value", frame.filter)),
            "exposure_seconds": exposure_seconds,
            "starts_at": _iso(start),
            "ends_at": _iso(end),
            "pier_side": frame.pier_side,
            **_window_statistics(inside, exposure_seconds),
        })

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
        "frames": frames,
    }
