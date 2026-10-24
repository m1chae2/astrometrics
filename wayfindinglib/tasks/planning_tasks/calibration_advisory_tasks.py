"""Purpose: Count the calibration frames already held for one request.

Description: `ObservationPlanning.get_advisory(kind="calibration")` uses
this to look up how many dark, bias or flat frames of a given exposure,
filter, gain setting, offset, binning and sensor temperature the inventory
already holds for a camera. It is an inventory view only: the advice about
which calibration frames to take lives in the target dataset report
(rule ``calibration_gap``, `report_rules.find_calibration_gaps`). This
count never changes where a package is placed or its priority.

An entry matches when every setting that both the request and the entry
state agrees. A setting that one side leaves out is not compared, so an
inventory written before the gain setting, offset, binning and temperature
were recorded still counts. Darks match a temperature within
`DEFAULT_DARK_TEMPERATURE_TOLERANCE_C`.
"""

from typing import Any

from astrometricslib import DEFAULT_DARK_TEMPERATURE_TOLERANCE_C, FilterType
from wayfindinglib.models.equipment_and_site.calibration import (
    CalibrationAdvisory,
    CalibrationEntry,
    CalibrationStats,
)
from wayfindinglib.models.planning.observation_package import FrameType


def _differs(held: int | None, wanted: int | None) -> bool:
    """Say whether an entry and a request state different values.

    Parameters
    ----------
    held : `int` or `None`
        The entry's value.
    wanted : `int` or `None`
        The request's value.

    Returns
    -------
    differs : `bool`
        True only when both are stated and not equal.
    """
    return held is not None and wanted is not None and held != wanted


def build_calibration_advisory(
    butler: Any,
    camera_id: str,
    frame_type: FrameType,
    exposure_seconds: float | None = None,
    filter: FilterType | None = None,
    *,
    gain_setting: int | None = None,
    offset: int | None = None,
    binning: int | None = None,
    sensor_temperature_c: float | None = None,
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
    gain_setting : `int`, optional
        The requested gain setting (the ISO for a one-shot-color camera).
    offset : `int`, optional
        The requested camera offset.
    binning : `int`, optional
        The requested binning factor.
    sensor_temperature_c : `float`, optional
        The requested sensor temperature, in degrees Celsius. Only darks
        are matched on it.

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
        if _differs(entry.gain_setting, gain_setting) or _differs(entry.offset, offset):
            continue
        if _differs(entry.binning, binning):
            continue
        if (
            frame_type == FrameType.DARK
            and entry.sensor_temperature_c is not None
            and sensor_temperature_c is not None
            and abs(entry.sensor_temperature_c - sensor_temperature_c) > DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
        ):
            continue
        existing_count += entry.count

    return CalibrationAdvisory(
        camera_id=camera_id,
        frame_type=frame_type,
        exposure_sec=exposure_seconds,
        filter=filter,
        gain_setting=gain_setting,
        offset=offset,
        binning=binning,
        sensor_temperature_c=sensor_temperature_c,
        existing_count=existing_count,
    )
