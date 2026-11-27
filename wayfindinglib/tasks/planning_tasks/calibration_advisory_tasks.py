"""Purpose: Count the calibration frames the library holds for one request.

Description: `ObservationPlanning.get_calibration_advice` uses
this to look up how many dark, bias or flat frames of a given exposure,
filter, gain setting, offset, binning and sensor temperature the calibration
library holds for a camera. The count comes from the library's own index of
calibration files (`astrometricslib` ``calibration.query`` with a
`CaptureSetting`), the same source the Calibration Library view shows, so
the two always agree. It is an inventory view only: the advice about which
calibration frames to take lives in the target dataset report (rule
``calibration_gap``, `report_rules.find_calibration_gaps`). This count never
changes where a package is placed or its priority.

The library does the matching: camera (ignoring case), gain, offset,
binning, and for darks the temperature and the exposure, and for flats the
filter. A setting the request leaves out is not compared.
"""

from typing import Any

from astrometricslib import CaptureSetting, FilterType
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.planning.observation_package import FrameType

_KIND_BY_FRAME_TYPE = {FrameType.DARK: "dark", FrameType.BIAS: "bias", FrameType.FLAT: "flat"}


def build_calibration_advisory(
    calibration: Any,
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
    calibration : `CalibrationCatalog`
        The library calibration catalog (``astrometrics.calibration``).
    camera_id : `str`
        The camera whose frames are counted.
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
        are matched on it, by the library's dark temperature slot.

    Returns
    -------
    advisory : `CalibrationAdvisory`
        The matching existing count, or zero if none exists.
    """
    kind = _KIND_BY_FRAME_TYPE.get(frame_type)
    existing_count = 0
    if kind is not None:
        setting = CaptureSetting(
            camera=camera_id,
            exposure_s=exposure_seconds if kind == "dark" else None,
            filter_name=None if filter is None or kind != "flat" else FilterType(filter).value,
            gain=gain_setting,
            offset=offset,
            binning=binning,
            temperature_c=sensor_temperature_c if kind == "dark" else None,
        )
        existing_count = sum(row.count for row in calibration.query(kind, setting=setting))

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
