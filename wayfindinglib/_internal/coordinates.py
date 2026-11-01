"""Purpose: Read typed sky coordinates for the `wayfindinglib` calls.

Description: A copy of the science library's reader of a right ascension or
declination typed as text, so this library does not reach into the science
library's private package. The reading itself is done by
`astropy.coordinates.Angle`. Only `wayfindinglib` itself, and the tests of
either library, may import this module.
"""

import astropy.units as u
from astropy.coordinates import Angle

from astrometricslib.exceptions import InvalidArgumentError

__all__ = ["parse_coordinate_string"]


def parse_coordinate_string(angle_str: str, is_ra: bool = True) -> float:
    """Parse a single RA or Dec string into decimal degrees.

    All the real parsing (sexagesimal math, sign handling, unit-marker
    characters, colon-separated formats) is handled by
    `astropy.coordinates.Angle`; this function only validates the input
    and, for declinations, checks the result is a physically valid angle.

    Parameters
    ----------
    angle_str : `str`
        The angle as text, such as ``"16h 41m 41s"`` or ``"+36 27 37"``.
    is_ra : `bool`, optional
        `True` when the text is a right ascension, read in hours unless it
        says otherwise. `False` for a declination, read in degrees.

    Returns
    -------
    decimal_degrees : `float`
        The parsed angle, in decimal degrees.

    Raises
    ------
    InvalidArgumentError
        If ``angle_str`` is not a non-empty string, cannot be parsed, or
        (for a declination) falls outside the range -90 to 90 degrees.
    """
    if not isinstance(angle_str, str) or not angle_str.strip():
        raise InvalidArgumentError(f"Invalid coordinate string: {angle_str!r}")

    # astropy's Angle parser only accepts a single arcsecond-mark character
    # (the proper U+2033 "double prime", or a plain ASCII quote); it rejects
    # two U+2032 "prime" characters typed in a row. The placeholder coordinate
    # of a never-plate-solved target is written with that two-prime typo, so
    # normalize it here.
    angle_str = angle_str.replace("′′", "″")

    unit = u.hourangle if is_ra else u.deg
    try:
        angle = Angle(angle_str, unit=unit)
    except (ValueError, u.UnitsError) as exc:  # astropy could not parse the text
        raise InvalidArgumentError(f"Invalid coordinate string: {angle_str!r}") from exc

    if not is_ra and not (-90.0 <= angle.deg <= 90.0):
        raise InvalidArgumentError(
            f"Declination {angle.deg:.6f} degrees is out of range; must be between -90 and 90."
        )

    return float(angle.deg)
