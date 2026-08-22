"""Purpose: Solar Altitude Calculation.

Description: Pure Astropy-backed solar altitude at a given location and
time -- the raw input `night_window.py`'s twilight-threshold policy
brackets a usable night against
(`Wayfinding_Library_Architecture.md` §2.3.3).
"""

from astropy.coordinates import AltAz, EarthLocation, get_sun
from astropy.time import Time


def solar_altitude_deg(location: EarthLocation, obstime: Time) -> float:
    """Return the sun's altitude in degrees at `location`/`obstime`.

    Returns
    -------
    altitude_deg : `float`
        The sun's altitude, in degrees.
    """
    sun_coord = get_sun(obstime)
    altaz_frame = AltAz(obstime=obstime, location=location)
    return sun_coord.transform_to(altaz_frame).alt.deg
