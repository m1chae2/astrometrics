"""Purpose: Read a typed or displayed sky coordinate into degrees.

Description: The backend receives coordinates as text, such as
``"12h 00m 00s"`` or ``"+45d 00m 00s"``, from the UI and from the mount's
status. The libraries take degrees, so this module turns the text into a
number. `astropy` does the reading; this function only checks the input
and the declination range.
"""

from astropy import units
from astropy.coordinates import Angle

from astrometricslib.exceptions import InvalidArgumentError


def parse_coordinate_text(text: str, is_right_ascension: bool) -> float:
    """Read one right ascension or declination text into degrees.

    Parameters
    ----------
    text : `str`
        The coordinate, for example ``"12h 30m 00s"``, ``"12:30:00"`` or
        ``"-45d 10m 00s"``. A right ascension without a unit marker is read
        as hours, a declination as degrees.
    is_right_ascension : `bool`
        `True` for a right ascension, `False` for a declination.

    Returns
    -------
    degrees : `float`
        The angle in decimal degrees.

    Raises
    ------
    InvalidArgumentError
        If `text` is empty or cannot be read, or a declination is outside
        -90 to 90 degrees.
    """
    if not isinstance(text, str) or not text.strip():
        raise InvalidArgumentError(f"Invalid coordinate string: {text!r}")
    # Some placeholder coordinates end in two prime marks.
    # Astropy wants one double prime.
    cleaned = text.replace("′′", "″")
    unit = units.hourangle if is_right_ascension else units.deg
    try:
        angle = Angle(cleaned, unit=unit)
    except (ValueError, units.UnitsError) as error:
        raise InvalidArgumentError(f"Invalid coordinate string: {cleaned!r}") from error
    if not is_right_ascension and not -90.0 <= angle.deg <= 90.0:
        raise InvalidArgumentError(
            f"Declination {angle.deg:.6f} degrees is out of range; must be between -90 and 90."
        )
    return float(angle.deg)
