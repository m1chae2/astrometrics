"""Purpose: Sort a target into a kind of sky object by its name.

Description: Targets carry no object type of their own, so the kind is
read from the name, in this order:

1. A body of the solar system (a planet, the Sun, the Moon, Pluto or
   Ceres), by `is_solar_system_target`.
2. A Messier number ("M 31"), then an NGC number ("NGC 7000"), then an IC
   number ("IC 434").
3. A comet or asteroid designation, a letter C, P, D, X, A or I followed
   by a four-digit year ("C 2022 E3 ZTF", "P/2019 LD2").
4. A calibration folder ("bias", "dark", "flat", "calibration"), which is
   not a sky target at all.
5. Anything else, which in this library is almost always a named star.

Underscores and non-breaking spaces in a name count as spaces, so
"M_31" is a Messier number.
"""

import re

from astrometricslib.models.target import TargetObjectType
from astrometricslib.pipelines.shared.solar_system_targets import is_solar_system_target

__all__ = ["classify_target_name"]

_MESSIER = re.compile(r"^M(?=[\s\d]|$)", re.IGNORECASE)
_NGC = re.compile(r"^NGC(?=[\s\d]|$)", re.IGNORECASE)
_IC = re.compile(r"^IC(?=[\s\d]|$)", re.IGNORECASE)
_COMET = re.compile(r"^[CPDXAI]\s?/?\s?\d{4}\b", re.IGNORECASE)
_CALIBRATION_NAMES = frozenset({"bias", "dark", "flat", "calibration"})


def classify_target_name(name: str | None) -> TargetObjectType:
    """Return the kind of sky object a target name stands for.

    Parameters
    ----------
    name : `str` or `None`
        The target's id or name as stored.

    Returns
    -------
    object_type : `TargetObjectType`
        The first kind in the module's list that the name fits. An empty
        name is a `STAR` (the "anything else" kind).
    """
    cleaned = (name or "").replace(" ", " ").replace("_", " ").strip()
    if is_solar_system_target(cleaned):
        return TargetObjectType.SOLAR_SYSTEM
    if _MESSIER.match(cleaned):
        return TargetObjectType.MESSIER
    if _NGC.match(cleaned):
        return TargetObjectType.NGC
    if _IC.match(cleaned):
        return TargetObjectType.IC
    if _COMET.match(cleaned):
        return TargetObjectType.COMET
    if cleaned.lower() in _CALIBRATION_NAMES:
        return TargetObjectType.CALIBRATION
    return TargetObjectType.STAR
