"""Helper for reading SIMBAD coordinates.

The Planetarium no longer queries SIMBAD for stars (it draws from catalogs
downloaded to disk), but looking up a target by name still uses SIMBAD, and
that lookup reads coordinates with the helper below.
"""

import astropy.units as u
from astropy.coordinates import SkyCoord


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
