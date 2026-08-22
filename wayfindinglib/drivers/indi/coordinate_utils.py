"""Utility functions for celestial coordinate conversions in INDI drivers."""


def coordinate_dms_to_decimal(coordinate: str) -> float:
    """Convert a degrees-minutes-seconds string (D-M-S) to decimal degrees.

    Example: "12-30-00" -> 12.5

    Returns
    -------
    decimal_degrees : `float`
        The decimal-degree value, or ``0.0`` if the string cannot be
        parsed.
    """
    try:
        # Support both '-' and ':' as separators
        sep = ":" if ":" in coordinate else "-"
        parts = coordinate.strip().split(sep)
        if len(parts) < 3:
            # Maybe it's just D-M
            d = float(parts[0])
            m = float(parts[1]) if len(parts) > 1 else 0.0
            s = 0.0
        else:
            d, m, s = map(float, parts[:3])

        sign = -1 if d < 0 or coordinate.startswith("-") else 1
        return sign * (abs(d) + m / 60.0 + s / 3600.0)
    except ValueError, IndexError:
        return 0.0


def coordinate_decimal_to_dms(coordinate: float) -> str:
    """Convert decimal degrees to a human-readable D-M-S string.

    Example: 12.5 -> 12° 30′ 00″

    Returns
    -------
    dms_string : `str`
        The value formatted as ``"D° M′ S″"``.
    """
    sign = "-" if coordinate < 0 else ""
    abs_coord = abs(coordinate)

    degrees = int(abs_coord)
    remainder = (abs_coord - degrees) * 60
    minutes = int(remainder)
    seconds = round((remainder - minutes) * 60, 2)

    return f"{sign}{degrees}° {minutes}′ {seconds}″"
