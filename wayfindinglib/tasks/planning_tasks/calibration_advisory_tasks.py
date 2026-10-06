"""Purpose: Count the calibration frames already held for one request.

Description: `ObservationPlanning.get_advisory(kind="calibration")` uses
this to look up how many dark, bias or flat frames of a given exposure
and filter the inventory already holds for a camera. It is for
information only: unlike the quality advisory, it never changes where a
package is placed or its priority, because calibration exposures are
scheduled together with the science exposures of their package.
"""

from typing import Any

from astrometricslib import FilterType
from wayfindinglib.models.equipment_and_site.calibration import (
    CalibrationAdvisory,
    CalibrationEntry,
    CalibrationStats,
)
from wayfindinglib.models.planning.observation_package import FrameType


def build_calibration_advisory(
    butler: Any,
    camera_id: str,
    frame_type: FrameType,
    exposure_seconds: float | None = None,
    filter: FilterType | None = None,
) -> CalibrationAdvisory:
    """Look up the existing count for a requested calibration entry.

    Parameters
    ----------
    butler : `wayfindinglib.drivers.butler.DiskButler`
        The storage layer to read `CalibrationStats` from.
    camera_id : `str`
        The camera whose inventory is checked.
    frame_type : `FrameType`
        The requested calibration frame type (`DARK`, `FLAT`, or `BIAS`).
    exposure_seconds : `float`, optional
        The requested exposure time, when it distinguishes matching
        entries (e.g. darks).
    filter : `FilterType`, optional
        The requested filter, when it distinguishes matching entries
        (e.g. flats).

    Returns
    -------
    advisory : `CalibrationAdvisory`
        The matching existing count, or zero if none exists.
    """
    stats: CalibrationStats | None = butler.get("calibration_stats", {"camera_id": camera_id})
    entries: list[CalibrationEntry] = []
    if stats is not None:
        if frame_type == FrameType.DARK:
            entries = stats.darks
        elif frame_type == FrameType.BIAS:
            entries = stats.biases
        elif frame_type == FrameType.FLAT:
            entries = stats.flats

    existing_count = 0
    for entry in entries:
        if (
            entry.exposure_sec is not None
            and exposure_seconds is not None
            and entry.exposure_sec != exposure_seconds
        ):
            continue
        if entry.filter is not None and filter is not None and entry.filter != filter:
            continue
        existing_count += entry.count

    return CalibrationAdvisory(
        camera_id=camera_id,
        frame_type=frame_type,
        exposure_sec=exposure_seconds,
        filter=filter,
        existing_count=existing_count,
    )
