"""Purpose: Read a typed sky coordinate into degrees, and write one as text.

Description: The backend receives coordinates as text, such as
``"12h 00m 00s"`` or ``"+45d 00m 00s"``, from the UI. The libraries take
and give degrees, so this module turns the text into a number
(`parse_coordinate_text`, which `astropy` reads) and a number back into the
text the screens show (`format_right_ascension`, `format_declination` and
`format_angle`).
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


def _split_sexagesimal(value: float, decimals: int) -> tuple[int, int, float]:
    """Split a positive value into whole units, minutes and rounded seconds.

    The seconds are rounded first, so 59.999 seconds carries into the next
    minute instead of printing ``60``.

    Parameters
    ----------
    value : `float`
        A positive value in hours or degrees.
    decimals : `int`
        Decimal places kept on the seconds.

    Returns
    -------
    units, minutes, seconds : `tuple` [`int`, `int`, `float`]
        The value split into its three parts.
    """
    total_seconds = round(value * 3600.0, decimals)
    units, remainder = divmod(total_seconds, 3600.0)
    minutes, seconds = divmod(remainder, 60.0)
    return int(units), int(minutes), round(seconds, decimals)


def format_right_ascension(ra_deg: float | None) -> str:
    """Write a right ascension as ``"5h 35m 17.30s"``.

    Parameters
    ----------
    ra_deg : `float` or `None`
        Right ascension in degrees, or `None` when it is not known.

    Returns
    -------
    text : `str`
        The hours, minutes and seconds, or ``"Unknown"``.
    """
    if ra_deg is None:
        return "Unknown"
    hours, minutes, seconds = _split_sexagesimal((ra_deg / 15.0) % 24.0, 2)
    return f"{hours % 24}h {minutes:02d}m {seconds:05.2f}s"


def format_declination(dec_deg: float | None) -> str:
    """Write a declination as ``"-5° 23′ 28.0″"``, always with a sign.

    Parameters
    ----------
    dec_deg : `float` or `None`
        Declination in degrees, or `None` when it is not known.

    Returns
    -------
    text : `str`
        The degrees, minutes and seconds, or ``"Unknown"``.
    """
    if dec_deg is None:
        return "Unknown"
    sign = "-" if dec_deg < 0 else "+"
    degrees, minutes, seconds = _split_sexagesimal(abs(dec_deg), 1)
    return f"{sign}{degrees}° {minutes:02d}′ {seconds:04.1f}″"


def format_angle(angle_deg: float | None) -> str:
    """Write an altitude or azimuth as ``"45° 12′ 30.0″"``.

    The same text the INDI session used to report, so the status reply
    keeps its shape.

    Parameters
    ----------
    angle_deg : `float` or `None`
        The angle in degrees, or `None` when it is not known.

    Returns
    -------
    text : `str`
        The degrees, minutes and seconds (to 0.01), or ``"Unknown"``.
    """
    if angle_deg is None:
        return "Unknown"
    sign = "-" if angle_deg < 0 else ""
    magnitude = abs(angle_deg)
    degrees = int(magnitude)
    remainder = (magnitude - degrees) * 60
    minutes = int(remainder)
    seconds = round((remainder - minutes) * 60, 2)
    return f"{sign}{degrees}° {minutes}′ {seconds}″"
