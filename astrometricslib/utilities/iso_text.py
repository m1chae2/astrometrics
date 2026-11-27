"""Helpers for the ISO and gain text stored on frames.

A camera header can write the same setting two ways: an ISO of 800 as ``800``
or as ``800.0``. Left alone, the two spellings are treated as different
settings, which splits frames that belong together. The helpers here write an
ISO the same way every time and compare settings by their number.

`iso_or_gain_text` rewrites only an ISO (the header's ``ISOSPEED``) and keeps
a ``GAIN`` as the header spells it. A frame record keeps its gain as a whole
number (`setting_number`, `SettingNumber`), so a frame whose header writes
``GAIN = 0.0`` has gain 0, and its session id (see
`astrometricslib.pipelines.shared.target_sessions`) names gain 0.
"""

import math
from typing import Annotated, Any

from pydantic import BeforeValidator


def _as_number(value: Any) -> float | None:
    """Read a value as a finite number.

    Parameters
    ----------
    value : `Any`
        A number or text that may hold one.

    Returns
    -------
    number : `float` or `None`
        The number, or `None` when the value is not a finite number.
    """
    try:
        number = float(str(value).strip())
    except TypeError, ValueError:
        return None
    return number if math.isfinite(number) else None


def canonical_iso_text(value: Any) -> str:
    """Write an ISO as text, without a needless decimal point.

    Parameters
    ----------
    value : `Any`
        An ISO as read from a header, for example ``800``, ``800.0`` or
        ``"800.0"``.

    Returns
    -------
    iso_text : `str`
        A whole number is written without a decimal (``"800"``). Anything
        else, such as ``12.5`` or ``"Auto"``, keeps its own text.
    """
    text = str(value).strip()
    number = _as_number(text)
    if number is not None and number == int(number):
        return str(int(number))
    return text


def setting_number(value: Any) -> int | None:
    """Read a gain, ISO or offset setting as a whole number.

    Parameters
    ----------
    value : `Any`
        The setting as a header or an older record gives it, for example
        ``800``, ``800.0`` or ``"800"``.

    Returns
    -------
    number : `int` or `None`
        The setting rounded to a whole number, or `None` when `value` holds
        no finite number (missing, empty, or text such as ``"Auto"``).
    """
    if value is None or isinstance(value, bool):
        return None
    number = _as_number(value)
    return None if number is None else round(number)


def iso_or_gain_text(header: Any) -> str | None:
    """Read the ISO or gain from an image header, as text.

    Parameters
    ----------
    header : `Any`
        The image header.

    Returns
    -------
    iso_or_gain : `str` or `None`
        The header's ``ISOSPEED`` written by `canonical_iso_text`, else its
        ``GAIN`` exactly as the header has it, else `None` when it has neither.
    """
    iso = header.get("ISOSPEED")
    if iso is not None:
        return canonical_iso_text(iso)
    gain = header.get("GAIN")
    if gain is not None:
        return str(gain)
    return None


def iso_or_gain_values_match(first: Any, second: Any) -> bool:
    """Tell whether two ISO or gain settings are the same setting.

    Parameters
    ----------
    first, second : `Any`
        The settings to compare, as numbers or text.

    Returns
    -------
    matches : `bool`
        `True` when both are numbers and equal (``"800"`` matches
        ``"800.0"``), or when they are not numbers and their text is equal.
    """
    first_number, second_number = _as_number(first), _as_number(second)
    if first_number is not None and second_number is not None:
        return first_number == second_number
    return str(first).strip() == str(second).strip()


SettingNumber = Annotated[int | None, BeforeValidator(setting_number)]
"""A model field type for a gain, ISO or offset setting.

It holds a whole number, or `None` when the setting is not known. Text such as
``"800"`` or ``"800.0"``, which older records stored, is read as the number.
"""
