"""Purpose: Count the calibration frames the library holds for one request.

Description: `ObservationPlanning.get_calibration_advice` uses
this to look up how many dark, bias or flat frames of a given exposure,
filter, gain setting, offset, binning and sensor temperature the calibration
library holds for a camera. The count comes from the library's own index of
calibration files (`astrometricslib` ``processing.calibration.query``), the
same source the Calibration Library view shows, so the two always agree.
It is an inventory view only: the advice about which calibration frames to
take lives in the target dataset report (rule ``calibration_gap``,
`report_rules.find_calibration_gaps`). This count never changes where a
package is placed or its priority.

The library does the matching on camera (ignoring case), gain, offset,
binning and, for darks, temperature. A setting the request leaves out is not
compared. This module then narrows the rows by exposure and filter.
"""

import math
from typing import Any

from astrometricslib import FilterType
from wayfindinglib.models.equipment_and_site.calibration import CalibrationAdvisory
from wayfindinglib.models.planning.observation_package import FrameType

# Two exposures closer than this, in seconds, are the same exposure. It only
# absorbs float round-off from folder names such as ``60.0``.
_EXPOSURE_MATCH_TOLERANCE_SECONDS = 1e-6

_KIND_BY_FRAME_TYPE = {FrameType.DARK: "dark", FrameType.BIAS: "bias", FrameType.FLAT: "flat"}
_ROWS_ATTRIBUTE_BY_KIND = {"dark": "darks", "bias": "biases", "flat": "flats"}


def _filter_text(value: Any) -> str:
    """Write a filter in its one standard spelling.

    Parameters
    ----------
    value : `Any`
        A `FilterType`, or text such as ``"L"`` or ``"Luminance"``.

    Returns
    -------
    text : `str`
        The `FilterType` value when the text names a filter, else the text.
    """
    try:
        return FilterType(value).value
    except ValueError:
        return str(value).strip()


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
    calibration : `astrometricslib.api.processing.CalibrationCatalog`
        The library calibration catalog
        (``astrometrics.processing.calibration``).
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
        are matched on it, within the library's dark temperature tolerance.

    Returns
    -------
    advisory : `CalibrationAdvisory`
        The matching existing count, or zero if none exists.
    """
    kind = _KIND_BY_FRAME_TYPE.get(frame_type)
    existing_count = 0
    if kind is not None:
        matched = calibration.query(
            kind=kind,
            detail="match",
            camera_id=camera_id,
            gain=gain_setting,
            offset=offset,
            binning=binning,
            temperature_c=sensor_temperature_c if kind == "dark" else None,
        )
        matched_keys = set(matched.matched_row_keys or [])
        rows = getattr(calibration.query(kind=kind, detail="counts"), _ROWS_ATTRIBUTE_BY_KIND[kind]) or []
        for row in rows:
            if row.get("row_key") not in matched_keys:
                continue
            if exposure_seconds is not None and row.get("exposure") is not None:
                if not math.isclose(
                    float(row["exposure"]), exposure_seconds, abs_tol=_EXPOSURE_MATCH_TOLERANCE_SECONDS
                ):
                    continue
            if filter is not None and row.get("filter") is not None:
                if _filter_text(row["filter"]) != _filter_text(filter):
                    continue
            existing_count += int(row.get("count") or 0)

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
