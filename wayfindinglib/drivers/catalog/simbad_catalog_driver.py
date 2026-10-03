"""Helper for reading SIMBAD coordinates.

The Planetarium no longer queries SIMBAD for stars (it draws from catalogs
downloaded to disk), but looking up a target by name still uses SIMBAD, and
that lookup reads coordinates with the helper below.
"""

import astropy.units as u
from astropy.coordinates import Angle, SkyCoord


def resolve_simbad_radec(raw_ra, raw_dec) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Resolve a SIMBAD row's RA/Dec value into decimal degrees.

    Tries the fast numeric-degree path first — recent astroquery versions
    return 'ra'/'dec' as decimal-degree floats — falling back to sexagesimal
    parsing (older versions return 'RA'/'DEC' as hourangle-RA/degree-Dec
    strings). Shared by every caller that resolves SIMBAD coordinates so they
    can't independently drift out of sync with astroquery's version behavior.

    Parameters
    ----------
    raw_ra : Any
        The raw value from a SIMBAD result row's RA column.
    raw_dec : Any
        The raw value from a SIMBAD result row's Dec column.

    Returns
    -------
    Tuple[float, float]
        (ra_deg, dec_deg).

    Raises
    ------
    ValueError
        If neither the numeric nor sexagesimal parse succeeds:
        propagated from `astropy.coordinates.SkyCoord` when it
        cannot parse `raw_ra`/`raw_dec` as hourangle/degree strings.
    """  # ruff: ignore[docstring-extraneous-exception] -- ValueError is genuinely raised by SkyCoord() on malformed input; not visible to static analysis
    try:
        return float(raw_ra), float(raw_dec)
    except TypeError, ValueError:
        resolved_coordinate = SkyCoord(str(raw_ra), str(raw_dec), unit=(u.hourangle, u.deg), frame="icrs")
        return resolved_coordinate.ra.deg, resolved_coordinate.dec.deg


def read_simbad_field(row, name: str, default=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Read one column from a SIMBAD result row, whatever its letter case.

    astroquery before 0.4.8 named columns in upper case (``MAIN_ID``) and
    later versions use lower case (``main_id``). Reading both keeps the
    callers working across versions. A masked (empty) value counts as
    missing.

    Parameters
    ----------
    row : `astropy.table.Row`
        One row of a SIMBAD result table.
    name : `str`
        The column name, in any letter case.
    default : `Any`, optional
        Returned when the column is absent or its value is masked.

    Returns
    -------
    value : `Any`
        The cell value, or `default`.
    """
    for column_name in row.colnames:
        if column_name.lower() == name.lower():
            value = row[column_name]
            if getattr(value, "mask", False) is True or str(value) == "--":
                return default
            return value
    return default


def format_target_coordinates(ra_degrees: float, dec_degrees: float) -> tuple[str, str]:
    """Write decimal degrees in the text form a `Target` stores.

    A `Target` keeps right ascension as ``"23h 24m 48.00s"`` and
    declination as ``"+61° 35′ 39.0′′"``. SIMBAD returns plain degrees in
    current astroquery versions, and a bare number such as ``351.2`` would
    later be read as hours.

    Parameters
    ----------
    ra_degrees : `float`
        Right ascension in degrees.
    dec_degrees : `float`
        Declination in degrees.

    Returns
    -------
    ra_text : `str`
        Right ascension as hours, minutes, seconds.
    dec_text : `str`
        Declination as signed degrees, arcminutes, arcseconds.
    """
    right_ascension = Angle(ra_degrees, u.deg).hms
    declination = Angle(abs(dec_degrees), u.deg).dms
    sign = "-" if dec_degrees < 0 else "+"
    ra_text = f"{int(right_ascension.h)}h {int(right_ascension.m)}m {right_ascension.s:.2f}s"
    dec_text = f"{sign}{int(declination.d)}° {int(declination.m)}′ {declination.s:.1f}′′"
    return ra_text, dec_text
