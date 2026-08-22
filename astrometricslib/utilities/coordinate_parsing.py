"""Purpose: Astronomical Coordinate String Parsing.

Description: Parses sexagesimal and decimal RA/Dec angle string formats into
decimal degrees.
"""

import astropy.units as u


def parse_coordinate_string(angle_str: str, is_ra: bool = True) -> float:
    """Parse a single RA or Dec string into decimal degrees.

    Returns
    -------
    decimal_degrees : `float`
        The parsed angle, in decimal degrees.

    Raises
    ------
    ValueError
        If ``angle_str`` is empty or contains no parseable tokens.
    """
    if not angle_str:
        raise ValueError("Empty coordinate string.")

    normalized = (
        angle_str
        .replace("h", " ")
        .replace("m", " ")
        .replace("s", " ")
        .replace("d", " ")
        .replace("°", " ")
        .replace("′", " ")
        .replace("″", " ")
        .replace("'", " ")
        .replace('"', " ")
        .strip()
    )

    parts = normalized.split()
    if len(parts) == 0:
        raise ValueError(f"Invalid coordinate format: {angle_str}")

    from astropy.coordinates import Angle

    unit = u.hourangle if is_ra else u.deg
    angle = Angle(" ".join(parts), unit=unit)
    return angle.deg
