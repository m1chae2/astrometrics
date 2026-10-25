"""Purpose: Turn a "take more frames" statement into a package exposure row.

Description: A rule whose action becomes a row of an observation package
declares that with ``produces="package_row"`` (`QualityRule.produces`). Today
only ``more_frames`` does. This module holds the one documented way to fill
the fields of an `ExposureRequest` from such a statement, so the code that
makes suggested packages and any other reader fill them the same way:

* ``frame_type`` is ``LIGHT``. A statement that names a calibration frame
  type makes a checklist item, never a package row.
* ``filter`` is the capture setting's filter, a free-text name such as
  ``"Ha"`` or ``"Luminance"``, normalized to a `FilterType`. A setting with no
  filter gives ``FilterType.NONE``. A name that is not recognized gives no
  row, because a row with the wrong filter would waste a night.
* ``count`` is the proposed ``frame_count``.
* ``exposure_sec`` is the proposed ``exposure_seconds``, or the capture
  setting's exposure when the proposal has none.
* ``gain_setting``, ``offset`` and ``binning`` come from the proposal when it
  has them, otherwise from the capture setting, so new frames match the
  frames already taken.

The module imports the rule table, so a statement from a rule that does not
declare ``package_row`` gives no row.
"""

from astrometricslib import CaptureSetting, FilterType
from wayfindinglib.analytics.result_quality.all_rules import ALL_RULES
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.quality.statements import TargetStatement

__all__ = ["exposure_request_from_statement", "normalize_filter_name"]

_FILTER_NAMES: dict[str, FilterType] = {
    "l": FilterType.L,
    "lum": FilterType.L,
    "luminance": FilterType.L,
    "r": FilterType.R,
    "red": FilterType.R,
    "g": FilterType.G,
    "green": FilterType.G,
    "b": FilterType.B,
    "blue": FilterType.B,
    "ha": FilterType.Ha,
    "h-alpha": FilterType.Ha,
    "halpha": FilterType.Ha,
    "oiii": FilterType.OIII,
    "o3": FilterType.OIII,
    "sii": FilterType.SII,
    "s2": FilterType.SII,
    "spec": FilterType.SPEC,
    "spectroscopy": FilterType.SPEC,
    "star analyzer 200": FilterType.SPEC,
    "none": FilterType.NONE,
    "": FilterType.NONE,
}
"""Lower-case spellings of each filter and the `FilterType` they mean."""

_PACKAGE_ROW_RULES = frozenset(rule.name for rule in ALL_RULES if rule.produces == "package_row")
"""The names of the rules that declare ``produces="package_row"``."""


def normalize_filter_name(filter_name: str | None) -> FilterType | None:
    """Read a free-text filter name as a `FilterType`.

    Parameters
    ----------
    filter_name : `str` or `None`
        The name as the capture setting holds it, such as ``"Ha"`` or
        ``"Luminance"``. ``None`` means no filter.

    Returns
    -------
    filter_type : `FilterType` or `None`
        The filter, ``FilterType.NONE`` for no filter, or ``None`` when the
        name is not recognized.
    """
    return _FILTER_NAMES.get((filter_name or "").strip().lower())


def exposure_request_from_statement(statement: TargetStatement) -> ExposureRequest | None:
    """Fill an exposure row from a statement that asks for more light frames.

    Parameters
    ----------
    statement : `TargetStatement`
        A statement from the quality rules.

    Returns
    -------
    request : `ExposureRequest` or `None`
        The row, or ``None`` when the statement does not make one: its rule
        does not declare ``package_row``, it proposes no frame count or names
        a calibration frame type, no exposure is known, or the filter is not
        recognized.
    """
    if statement.rule not in _PACKAGE_ROW_RULES:
        return None
    action = statement.proposed_action
    if action is None or action.frame_count is None or action.calibration_frame_type is not None:
        return None
    setting = statement.capture_setting
    exposure_seconds = action.exposure_seconds
    if exposure_seconds is None and setting is not None:
        exposure_seconds = setting.exposure_seconds
    if exposure_seconds is None:
        return None
    filter_type = normalize_filter_name(setting.filter_name if setting is not None else None)
    if filter_type is None:
        return None
    return ExposureRequest(
        frame_type=FrameType.LIGHT,
        filter=filter_type,
        exposure_sec=exposure_seconds,
        count=action.frame_count,
        gain_setting=action.gain_setting
        if action.gain_setting is not None
        else _setting_value(setting, "gain_setting"),
        offset=action.offset if action.offset is not None else _setting_value(setting, "offset"),
        binning=action.binning if action.binning is not None else _setting_value(setting, "binning"),
    )


def _setting_value(setting: CaptureSetting | None, name: str) -> int | None:
    """Read one integer field of a capture setting, if there is a setting.

    Parameters
    ----------
    setting : `CaptureSetting` or `None`
        The capture setting.
    name : `str`
        ``"gain_setting"``, ``"offset"`` or ``"binning"``.

    Returns
    -------
    value : `int` or `None`
        The field, or ``None`` when there is no setting or the field is unset.
    """
    return None if setting is None else getattr(setting, name)
