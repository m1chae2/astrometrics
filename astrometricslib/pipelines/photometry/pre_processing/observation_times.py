"""Turns a frame's exposure start into a mid-exposure BJD_TDB time.

A light curve needs times that mean the same thing on every night, at every
observatory. The FITS ``DATE-OBS`` card gives the moment the shutter opened,
in UTC (Coordinated Universal Time, the civil time scale). That is not the
best time to attach to a brightness measurement, for two reasons:

1. A brightness measurement is an average over the whole exposure, so its
   best time is the middle of the exposure, not the start. A 300 s exposure
   makes the start-time error 150 s.
2. Earth moves around the Sun, so light from a star reaches the telescope
   up to about 8.3 minutes earlier or later than it would reach the Sun. A
   brightness change that really happens at one moment would show up at
   different times at different times of the year, which smears out the
   period of anything that repeats.

BJD_TDB removes both. The steps are:

1. Add half the exposure time to ``DATE-OBS`` (the middle of the exposure).
2. Convert from UTC to TDB (Barycentric Dynamical Time, a uniform time scale
   that does not follow Earth's rotation or leap seconds). When the
   observatory's position is known, the conversion includes the small
   correction for the observer's position on Earth (at most about 2 ms).
3. Add the light-travel time to the solar system barycenter (the center of
   mass of the solar system) for the direction of the target. It ranges
   from -499 s to +499 s depending on the target's position on the sky and
   the date.
4. Express the result as a Julian Date (days since noon on 4713 BCE
   January 1), which is the BJD_TDB value.

The correction uses one sky position for the whole field. Across a field of
view the barycentric delay changes by about 8.7 s per degree of angle on the
sky at most, so the result is accurate to a few seconds for a field of a
degree or less. The stars in one field therefore all share the same time
stamps.

The barycentric positions come from astropy's built-in ephemeris (the
tabulated positions of the planets), which needs no download. Its Earth
position is good to about 2 ms in light-travel time.
"""

from collections.abc import Sequence
from datetime import datetime

import numpy as np
from astropy import units as astropy_units
from astropy.coordinates import EarthLocation, SkyCoord
from astropy.time import Time, TimeDelta

from astrometricslib.foundation.observatory_site import ObservatorySite

# The three ways a light curve's times can be recorded. The
# `capture_timestamps` gate quotes them, so they are plain sentences.
TIME_BASIS_BJD_TDB = "BJD_TDB, mid-exposure"
TIME_BASIS_BJD_TDB_GEOCENTRIC = "BJD_TDB, mid-exposure, observer taken at Earth's center (no site configured)"
TIME_BASIS_UTC_START = "UTC start, no BJD_TDB (target position unknown)"

# The ephemeris used for the barycentric delay. "builtin" is astropy's own
# tabulated positions, which work offline.
_EPHEMERIS = "builtin"


def time_basis_for(site: ObservatorySite | None) -> str:
    """Name the time basis of BJD_TDB values computed for a site.

    Parameters
    ----------
    site : `ObservatorySite` or `None`
        The observatory used for the calculation, or `None` when none was.

    Returns
    -------
    basis : `str`
        `TIME_BASIS_BJD_TDB` with a site, `TIME_BASIS_BJD_TDB_GEOCENTRIC`
        without one.
    """
    return TIME_BASIS_BJD_TDB if site is not None else TIME_BASIS_BJD_TDB_GEOCENTRIC


def mid_exposure_times(
    starts: Sequence[datetime], exposure_seconds: Sequence[float], site: ObservatorySite | None = None
) -> Time:
    """Find the middle of each exposure.

    Parameters
    ----------
    starts : `Sequence` [`datetime.datetime`]
        The exposure start times, in UTC, with no time zone attached (as
        read from ``DATE-OBS``).
    exposure_seconds : `Sequence` [`float`]
        The exposure time of each frame, in seconds.
    site : `ObservatorySite`, optional
        The observatory. It is attached to the result so the UTC to TDB
        conversion includes the observer's position. Without one, Earth's
        center is attached.

    Returns
    -------
    middles : `astropy.time.Time`
        The middle of each exposure, on the UTC scale.
    """
    if site is not None:
        location = EarthLocation.from_geodetic(
            lon=site.longitude_deg * astropy_units.deg,
            lat=site.latitude_deg * astropy_units.deg,
            height=site.elevation_m * astropy_units.m,
        )
    else:
        # astropy needs some location for the barycentric delay. The origin
        # of Earth-fixed coordinates is Earth's center.
        location = EarthLocation.from_geocentric(0.0, 0.0, 0.0, astropy_units.m)
    start_times = Time(list(starts), scale="utc", location=location)
    half_exposures = TimeDelta(np.asarray(exposure_seconds, dtype=float) / 2.0, format="sec")
    return start_times + half_exposures


def barycentric_julian_dates(
    starts: Sequence[datetime],
    exposure_seconds: Sequence[float],
    right_ascension_deg: float,
    declination_deg: float,
    site: ObservatorySite | None = None,
) -> np.ndarray:
    """Convert exposure starts to mid-exposure BJD_TDB values.

    Parameters
    ----------
    starts : `Sequence` [`datetime.datetime`]
        The exposure start times, in UTC, with no time zone attached.
    exposure_seconds : `Sequence` [`float`]
        The exposure time of each frame, in seconds. Must match `starts`
        in length.
    right_ascension_deg, declination_deg : `float`
        The sky position of the target (ICRS, the standard sky coordinate
        system), in degrees.
    site : `ObservatorySite`, optional
        The observatory. Without one, the observer is taken at Earth's
        center, which changes each value by at most about 25 ms.

    Returns
    -------
    bjd_tdb : `numpy.ndarray`
        One value per frame: the Julian Date of the middle of the exposure
        in BJD_TDB, in days.
    """
    middles = mid_exposure_times(starts, exposure_seconds, site)
    target = SkyCoord(ra=right_ascension_deg * astropy_units.deg, dec=declination_deg * astropy_units.deg)
    delay = middles.light_travel_time(target, kind="barycentric", ephemeris=_EPHEMERIS)
    return np.asarray((middles.tdb + delay).jd, dtype=float)
