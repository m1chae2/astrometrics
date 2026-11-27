"""Purpose: `Resolution`, where a named object is on the sky.

Description: `TargetCatalog.resolve` turns a name into one `Resolution`. The
name is looked up in the library's targets first, then in its stars, and
last in the SIMBAD catalog. The answer says which of the three found it, and
holds the library's own object when the library knew the name.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.models.target import Target

__all__ = ["Resolution"]


class Resolution(BaseModel):
    """Where a named object is, and who knew the name.

    Parameters
    ----------
    name : `str`
        The name as the library or SIMBAD gives it: the target's id, the
        star's name, or the name that was asked for.
    ra_deg : `float`
        Right ascension, ICRS, in degrees.
    dec_deg : `float`
        Declination, ICRS, in degrees.
    source : ``"target"``, ``"star"`` or ``"simbad"``
        Who knew the name: one of the library's targets, one of its stars,
        or the SIMBAD catalog.
    target : `Target` or `None`, optional
        The library's target, when ``source`` is ``"target"``.
    star : `StellarObject` or `None`, optional
        The library's star, when ``source`` is ``"star"``.
    object_type : `str` or `None`, optional
        SIMBAD's main object type code (such as ``"G"`` for a galaxy), when
        SIMBAD gave one.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str
    ra_deg: float
    dec_deg: float
    source: Literal["target", "star", "simbad"]
    target: Target | None = None
    star: StellarObject | None = None
    object_type: str | None = None
