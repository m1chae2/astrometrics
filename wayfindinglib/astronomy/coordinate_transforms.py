"""Purpose: Equatorial-to-Horizontal Coordinate Transforms.

Description: Pure Astropy-backed RA/Dec <-> Alt/Az transforms, taking a
plain latitude/longitude/elevation triple rather than a `Sky` astrometrics or
a connected device -- so a placement calculation performed with no
hardware present and a safety check performed mid-slew agree by
construction, both resolving position from the same `SiteProfile`
(`Wayfinding_Library_Architecture.md` §2.2.2, "Single Source Of Observer
Position").

It also converts between the two equatorial frames the app meets. Star
catalogs, plate solutions and library targets use ICRS, which agrees
with J2000 to well under an arcsecond. A mount on INDI reports and
accepts positions in the "current epoch" frame (INDI's
``EQUATORIAL_EOD_COORD``, often called JNow): the true equator and
equinox of the date. The Earth's axis precesses, so the two frames drift
apart by about 50 arcseconds a year, or about 0.36 degrees by 2026.
"""

import astropy.units as u
import numpy as np
from astropy.coordinates import ICRS, TETE, AltAz, EarthLocation, SkyCoord
from astropy.time import Time


def earth_location(latitude_deg: float, longitude_deg: float, elevation_m: float) -> EarthLocation:
    """Build an `EarthLocation` from a `SiteProfile`'s coordinate triple.

    Parameters
    ----------
    latitude_deg : `float`
        Latitude in degrees.
    longitude_deg : `float`
        Longitude in degrees.
    elevation_m : `float`
        Elevation above sea level in meters.

    Returns
    -------
    location : `EarthLocation`
        The constructed observer location.
    """
    return EarthLocation(lat=latitude_deg * u.deg, lon=longitude_deg * u.deg, height=elevation_m * u.m)


def compute_altaz(
    ra_deg: float,
    dec_deg: float,
    location: EarthLocation,
    obstime: Time,
) -> tuple[float, float]:
    """Transform equatorial ICRS coordinates (RA/Dec) to horizontal (Alt/Az).

    Parameters
    ----------
    ra_deg : `float`
        Right Ascension in degrees.
    dec_deg : `float`
        Declination in degrees.
    location : `EarthLocation`
        Observer location for the transform.
    obstime : `Time`
        The observation time.

    Returns
    -------
    altitude_deg, azimuth_deg : `tuple` [`float`, `float`]
        Altitude and azimuth in degrees.
    """
    coord = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
    altaz_frame = AltAz(obstime=obstime, location=location)
    altaz_coord = coord.transform_to(altaz_frame)
    return altaz_coord.alt.deg, altaz_coord.az.deg


def local_sidereal_time_hours(location: EarthLocation, obstime: Time) -> float:
    """Return the local apparent sidereal time, in hours, at location/time.

    Parameters
    ----------
    location : `EarthLocation`
        The observer location.
    obstime : `Time`
        The observation time.

    Returns
    -------
    sidereal_time_hours : `float`
        The local apparent sidereal time, in hours.
    """
    return obstime.sidereal_time("apparent", longitude=location.lon).hour


def hour_angle_deg(ra_deg: float, location: EarthLocation, obstime: Time) -> float:
    """Return a target's hour angle in degrees, normalized to [-180, 180).

    Positive values are past transit (west of the meridian).

    Parameters
    ----------
    ra_deg : `float`
        The Right Ascension of the target, in degrees.
    location : `EarthLocation`
        The observer location.
    obstime : `Time`
        The observation time.

    Returns
    -------
    hour_angle_deg : `float`
        The target's hour angle in degrees, normalized to [-180, 180).
    """
    lst_hours = local_sidereal_time_hours(location, obstime)
    ha_hours = lst_hours - (ra_deg / 15.0)
    ha_hours = ((ha_hours + 12.0) % 24.0) - 12.0
    return ha_hours * 15.0


def angular_separation_arcsec(ra1_deg: float, dec1_deg: float, ra2_deg: float, dec2_deg: float) -> float:
    """Return the great-circle separation between two equatorial coordinates.

    Parameters
    ----------
    ra1_deg : `float`
        Right Ascension of the first coordinate in degrees.
    dec1_deg : `float`
        Declination of the first coordinate in degrees.
    ra2_deg : `float`
        Right Ascension of the second coordinate in degrees.
    dec2_deg : `float`
        Declination of the second coordinate in degrees.

    Returns
    -------
    separation_arcsec : `float`
        The angular separation, in arcseconds.
    """
    first = SkyCoord(ra=ra1_deg * u.deg, dec=dec1_deg * u.deg, frame="icrs")
    second = SkyCoord(ra=ra2_deg * u.deg, dec=dec2_deg * u.deg, frame="icrs")
    return first.separation(second).arcsec


def signed_offset_components_arcsec(
    from_ra_deg: float, from_dec_deg: float, to_ra_deg: float, to_dec_deg: float
) -> tuple[float, float]:
    """Return the signed per-axis (RA, Dec) offset between two coordinates.

    The RA component is scaled by cos(dec) so it represents true
    angular displacement on the sky rather than a raw coordinate
    difference, which would overstate RA offsets away from the
    celestial equator.

    Parameters
    ----------
    from_ra_deg : `float`
        Starting Right Ascension in degrees.
    from_dec_deg : `float`
        Starting Declination in degrees.
    to_ra_deg : `float`
        Target Right Ascension in degrees.
    to_dec_deg : `float`
        Target Declination in degrees.

    Returns
    -------
    ra_arcsec, dec_arcsec : `tuple` [`float`, `float`]
        The signed per-axis offset.
    """
    delta_ra_deg = (to_ra_deg - from_ra_deg + 180.0) % 360.0 - 180.0
    ra_arcsec = delta_ra_deg * 3600.0 * np.cos(np.radians(from_dec_deg))
    dec_arcsec = (to_dec_deg - from_dec_deg) * 3600.0
    return float(ra_arcsec), float(dec_arcsec)


def icrs_to_current_epoch(ra_deg: float, dec_deg: float, obstime: Time) -> tuple[float, float]:
    """Convert an ICRS (J2000) position to the current-epoch frame (JNow).

    The current-epoch frame is the true equator and equinox of `obstime`
    (Astropy's ``TETE`` frame, seen from the Earth's center). It applies
    precession, nutation and annual aberration. This is the frame an INDI
    mount expects in ``EQUATORIAL_EOD_COORD``.

    Parameters
    ----------
    ra_deg : `float`
        ICRS Right Ascension in degrees.
    dec_deg : `float`
        ICRS Declination in degrees.
    obstime : `Time`
        The date that defines the current epoch.

    Returns
    -------
    ra_deg, dec_deg : `tuple` [`float`, `float`]
        The position in the current-epoch frame, in degrees. Right
        Ascension is in [0, 360).
    """
    current = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs").transform_to(
        TETE(obstime=obstime)
    )
    return float(current.ra.wrap_at(360 * u.deg).deg), float(current.dec.deg)


def current_epoch_to_icrs(ra_deg: float, dec_deg: float, obstime: Time) -> tuple[float, float]:
    """Convert a current-epoch (JNow) position to ICRS (J2000).

    The inverse of `icrs_to_current_epoch`. Use it on the position an
    INDI mount reports, so the mount can be compared with catalog stars
    and plate solutions.

    Parameters
    ----------
    ra_deg : `float`
        Current-epoch Right Ascension in degrees.
    dec_deg : `float`
        Current-epoch Declination in degrees.
    obstime : `Time`
        The date that defines the current epoch.

    Returns
    -------
    ra_deg, dec_deg : `tuple` [`float`, `float`]
        The ICRS position in degrees. Right Ascension is in [0, 360).
    """
    current = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame=TETE(obstime=obstime))
    icrs = current.transform_to(ICRS())
    return float(icrs.ra.wrap_at(360 * u.deg).deg), float(icrs.dec.deg)
