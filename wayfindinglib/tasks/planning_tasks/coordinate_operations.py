"""Purpose: Coordinate sums for the planning code and the drivers.

Description: Astropy-backed RA/Dec to Alt/Az conversion (`compute_altaz`),
shared by the planning code and the INDI drivers, and the local sidereal
time at the site a `SkyEngine` holds.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time

if TYPE_CHECKING:
    from wayfindinglib.tasks.planning_tasks.sky_engine import SkyEngine


def compute_altaz(
    ra_deg: float,
    dec_deg: float,
    location: EarthLocation,
    obstime: Time,
) -> tuple[float, float]:
    """Transform equatorial ICRS coordinates (RA/Dec) to horizontal (Alt/Az).

    Pure coordinate math with no dependency on `SkyEngine`, so callers
    that track their own observer location (for example INDI drivers
    reading GEOGRAPHIC_COORD live off the mount) can share this transform
    without using the configured site.

    Parameters
    ----------
    ra_deg : float
        Right Ascension in degrees.
    dec_deg : float
        Declination in degrees.
    location : EarthLocation
        Observer location for the transform.
    obstime : Time
        The observation time.

    Returns
    -------
    Tuple[float, float]
        Altitude and Azimuth in degrees.
    """
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz_frame = AltAz(obstime=obstime, location=location)
    altaz_coord = coord.transform_to(altaz_frame)
    return altaz_coord.alt.deg, altaz_coord.az.deg


def get_local_sidereal_time(sky: SkyEngine, time_input: datetime | Time) -> float:
    """Calculate the local mean sidereal time (LST) in hours.

    Parameters
    ----------
    sky : `SkyEngine`
        Supplies the observer location.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    float
        Local mean sidereal time in hours (0.0 to 24.0).
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)
    lst_angle = observation_time.sidereal_time("mean", longitude=sky.location.lon)
    return lst_angle.hour
