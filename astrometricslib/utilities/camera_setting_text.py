"""Writes a camera's gain, offset and binning as text that compares equal.

A camera profile stores facts, such as the gain in electrons per ADU (ADU is
analog-to-digital unit, one step of the stored pixel value), for each camera
setting it was measured at. A frame states its setting in its header, and the
same setting can be written several ways: a gain of ``0`` or ``0.0``, an
offset of ``10`` or ``10.0``, a binning of ``2x2`` or the number ``2``. The
helpers here write each part one way, so a stored setting and a frame's
setting match exactly when they are the same setting.

The binning rules agree with `astrometricslib.drivers.calibration_library`
(``format_binning`` and ``normalize_binning``), which files calibration
frames by setting. This module repeats the few lines instead of importing
them because the camera-profile model sits below the drivers and cannot import
from them. A test keeps the two in step.
"""

import math
import re
from typing import Any

from astrometricslib.utilities.iso_text import canonical_iso_text

# The binning of a frame whose header names none: no binning at all. Same
# default as `astrometricslib.drivers.calibration_library.UNBINNED`.
UNBINNED_TEXT = "1x1"

_BINNING_TEXT_PATTERN = re.compile(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$")


def _as_finite_number(value: Any) -> float | None:
    """Read a value as a finite number.

    Parameters
    ----------
    value : `Any`
        A number, or text that may hold one.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when the value is missing or not a finite
        number.
    """
    if value is None:
        return None
    try:
        number = float(str(value).strip())
    except ValueError:
        return None
    return number if math.isfinite(number) else None


def canonical_gain_setting(value: Any) -> str:
    """Write a gain or ISO setting in its standard form.

    Parameters
    ----------
    value : `Any`
        The gain (or the ISO of a DSLR) as the header or record gives it,
        for example ``0``, ``0.0`` or ``"800"``.

    Returns
    -------
    gain_setting : `str`
        A whole number without a decimal point (``"0"``). Anything else
        keeps its own text.
    """
    return canonical_iso_text(value)


def canonical_offset_setting(value: Any) -> str:
    """Write a camera offset in its standard form.

    Parameters
    ----------
    value : `Any`
        The offset (the constant the camera adds to every pixel). A missing
        offset counts as 0, the same way the calibration library files a
        frame with no ``OFFSET`` card.

    Returns
    -------
    offset_setting : `str`
        The number written without a needless decimal point (``"10"``), or
        the text itself when it is not a number.
    """
    if value is None or str(value).strip() == "":
        return "0"
    number = _as_finite_number(value)
    if number is None:
        return str(value).strip()
    return f"{number:g}"


def canonical_binning_setting(value: Any) -> str:
    """Write a binning in its standard form, such as ``"2x2"``.

    Parameters
    ----------
    value : `Any`
        Text such as ``"2x2"`` or ``"2X2"``, or a number such as ``2`` (the
        same factor along both axes). Anything else counts as unbinned.

    Returns
    -------
    binning_setting : `str`
        ``"<x>x<y>"``, or ``"1x1"`` when the value names no binning.
    """
    match = _BINNING_TEXT_PATTERN.match(str(value)) if isinstance(value, str) else None
    if match:
        return f"{max(int(match.group(1)), 1)}x{max(int(match.group(2)), 1)}"
    number = _as_finite_number(value)
    if number is None or number < 1:
        return UNBINNED_TEXT
    factor = int(number)
    return f"{factor}x{factor}"
