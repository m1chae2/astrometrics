"""Purpose: Astronomical Coordinate String Parsing.

Description: Parses sexagesimal and decimal RA/Dec angle string formats into
decimal degrees.
"""

import astropy.units as u
from astropy.coordinates import Angle

from astrometricslib.foundation.errors import InvalidArgumentError


def parse_coordinate_string(angle_str: str, is_ra: bool = True) -> float:
    """Parse a single RA or Dec string into decimal degrees.

    All the real parsing (sexagesimal math, sign handling, unit-marker
    characters, colon-separated formats) is handled by
    `astropy.coordinates.Angle`; this function only validates the input
    and, for declinations, checks the result is a physically valid angle.

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
    # two U+2032 "prime" characters typed in a row. The pydantic placeholder
    # coordinate for a never-plate-solved target ("0h 0m 0s" / "0deg 0' 0''")
    # is written with that two-prime typo, so normalize it here rather than
    # in every place that constructs or compares against the placeholder.
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
