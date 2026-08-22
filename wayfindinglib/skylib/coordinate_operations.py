"""Coordinate Transform Operations.

Astropy-backed sidereal time, RA/Dec <-> Alt/Az transforms, and tracking rate
calculations for wayfindinglib.sky.Sky.
"""

from datetime import datetime

import astropy.units as u
from astropy.coordinates import AltAz, EarthLocation, SkyCoord
from astropy.time import Time


def compute_altaz(
    ra_deg: float,
    dec_deg: float,
    location: EarthLocation,
    obstime: Time,
) -> tuple[float, float]:
    """Transform equatorial ICRS coordinates (RA/Dec) to horizontal (Alt/Az).

    Pure coordinate math with no dependency on the Sky astrometrics, so callers
    that track their own observer location independently of Sky's configured
    location (e.g. INDI drivers reading GEOGRAPHIC_COORD live off the mount)
    can share this transform without being coupled to Sky's location.

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


def get_local_sidereal_time(sky, time_input: datetime | Time) -> float:  # ruff: ignore[missing-type-function-argument]
    """Calculate the local mean sidereal time (LST) in hours.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing observer location.
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


def radec_to_altaz(sky, ra_deg: float, dec_deg: float, time_input: datetime | Time) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Convert equatorial ICRS coordinates (RA/Dec) to horizontal (Alt/Az).

    Parameters
    ----------
    sky : Sky
        The Sky instance providing observer location.
    ra_deg : float
        Right Ascension in degrees.
    dec_deg : float
        Declination in degrees.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    Tuple[float, float]
        Altitude and Azimuth in degrees.
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)
    return compute_altaz(ra_deg, dec_deg, sky.location, observation_time)


def altaz_to_radec(sky, alt_deg: float, az_deg: float, time_input: datetime | Time) -> tuple[float, float]:  # ruff: ignore[missing-type-function-argument]
    """Convert horizontal (Alt/Az) coordinates to equatorial ICRS (RA/Dec).

    Parameters
    ----------
    sky : Sky
        The Sky instance providing observer location.
    alt_deg : float
        Altitude in degrees.
    az_deg : float
        Azimuth in degrees.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    Tuple[float, float]
        Right Ascension and Declination in degrees.
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)
    altaz_coord = SkyCoord(
        alt=alt_deg * u.deg, az=az_deg * u.deg, frame="altaz", obstime=observation_time, location=sky.location
    )
    icrs_coord = altaz_coord.transform_to("icrs")
    return icrs_coord.ra.deg, icrs_coord.dec.deg


def get_tracking_rates(sky, ra_deg: float, dec_deg: float, time_input: datetime | Time) -> dict[str, float]:  # ruff: ignore[missing-type-function-argument]
    """Compute coordinate tracking rates in Alt/Az and RA/Dec.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing observer location.
    ra_deg : float
        Right Ascension in degrees.
    dec_deg : float
        Declination in degrees.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    Dict[str, float]
        Dictionary containing:
        - "alt_rate": rate of change in Altitude (arcsec/sec)
        - "az_rate": rate of change in Azimuth (arcsec/sec)
        - "ra_rate": rate of change in Right Ascension (arcsec/sec,
          usually sidereal)
        - "dec_rate": rate of change in Declination (arcsec/sec)
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)
    time_step = 1.0 * u.s

    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")

    # Calculate Alt/Az at observation_time and observation_time + time_step
    altaz_now = coord.transform_to(AltAz(obstime=observation_time, location=sky.location))
    altaz_next = coord.transform_to(AltAz(obstime=observation_time + time_step, location=sky.location))

    alt_rate = (altaz_next.alt.deg - altaz_now.alt.deg) * 3600.0
    az_rate = (altaz_next.az.deg - altaz_now.az.deg) * 3600.0

    # Sidereal rate is 15.041 arcseconds per second
    return {
        "alt_rate": alt_rate,
        "az_rate": az_rate,
        "ra_rate": 15.041,
        "dec_rate": 0.0,
    }
