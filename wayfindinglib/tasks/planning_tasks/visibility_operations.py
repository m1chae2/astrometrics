"""Visibility, Meridian, and Rise/Set/Transit Operations.

Astronomical visibility, rise/set/transit, and meridian flip status
calculations for wayfindinglib.sky.Sky.
"""

import logging
import math
from datetime import datetime
from typing import Any

import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time

from astrometricslib import StellarObject, Target

logger = logging.getLogger(__name__)


def _hour_angle_from_lst(lst_hours: float, ra_hours: float) -> float:
    """Compute Hour Angle in hours, normalized to [-12.0, 12.0].

    HA = LST - RA. Shared by every caller in this module that derives Hour
    Angle from an already-computed Local Sidereal Time, so the normalization
    can't drift out of sync between them.

    Parameters
    ----------
    lst_hours : float
        Local Sidereal Time in hours.
    ra_hours : float
        Right Ascension in hours.

    Returns
    -------
    float
        Hour Angle in hours, normalized to [-12.0, 12.0].
    """
    return (lst_hours - ra_hours + 12.0) % 24.0 - 12.0


def _meridian_from_hour_angle(sky, hour_angle: float) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Compute meridian flip status from an already-known Hour Angle.

    Split out of `get_meridian_status` so `get_visibility` can reuse a
    single batch-computed Local Sidereal Time / Hour Angle per object
    instead of each helper recomputing it independently.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing the configured meridian flip delay.
    hour_angle : float
        Hour Angle in hours, normalized to [-12.0, 12.0].

    Returns
    -------
    Dict[str, Any]
        Status dictionary containing:
        - "hour_angle": Hour Angle in hours (-12.0 to 12.0).
        - "flip_required": Boolean indicating if a meridian flip is needed.
        - "time_to_flip_seconds": Float seconds remaining until
          meridian crossing.
    """
    # Flip is required if the object has transited the meridian and is past
    # our user-configurable delay limit.
    delay_hours = sky.meridian_flip_delay_min / 60.0
    flip_required = hour_angle > delay_hours

    # Seconds until meridian transit (hour_angle = 0)
    # If object is east (hour_angle < 0), time_to_flip is positive.
    # If west, negative.
    time_to_flip_seconds = -hour_angle * 3600.0

    return {
        "hour_angle": hour_angle,
        "flip_required": flip_required,
        "time_to_flip_seconds": time_to_flip_seconds,
    }


def get_meridian_status(sky, ra_deg: float, dec_deg: float, time_input: datetime | Time) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Compute the target Hour Angle and meridian flip status.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing sidereal time and meridian flip delay.
    ra_deg : float
        Right Ascension of the target in degrees.
    dec_deg : float
        Declination of the target in degrees.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    Dict[str, Any]
        Status dictionary containing:
        - "hour_angle": Hour Angle in hours (-12.0 to 12.0).
        - "flip_required": Boolean indicating if a meridian flip is needed.
        - "time_to_flip_seconds": Float seconds remaining until
          meridian crossing.
    """
    lst = sky.get_local_sidereal_time(time_input)
    ra_hours = ra_deg / 15.0

    hour_angle = _hour_angle_from_lst(lst, ra_hours)

    return _meridian_from_hour_angle(sky, hour_angle)


def _rise_set_transit(
    sky,  # ruff: ignore[missing-type-function-argument]
    dec_deg: float,
    alt_deg: float,
    hour_angle: float,
    observation_time: Time,
) -> dict[str, Any]:
    """Compute rise/set/transit times from an already-known altitude/HA.

    Split out of `get_object_visibility` so `get_visibility` can reuse a
    single batch-computed Alt/Az per object instead of each helper
    recomputing its own SkyCoord/AltAz transform.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing observer latitude.
    dec_deg : float
        Declination of the target in degrees.
    alt_deg : float
        Current altitude of the target in degrees.
    hour_angle : float
        Hour Angle in hours, normalized to [-12.0, 12.0].
    observation_time : Time
        The observation time.

    Returns
    -------
    Dict[str, Any]
        Visibility dictionary containing:
        - "above_horizon": Boolean.
        - "rise_time": Formatted rise time or "Circumpolar" / "Never Rises".
        - "set_time": Formatted set time or "Circumpolar" / "Never Rises".
        - "transit_time": Formatted transit time.
    """
    above_horizon = alt_deg > 0.0

    transit_diff_sec = -hour_angle * 3600.0
    transit_time = (observation_time + transit_diff_sec * u.s).datetime.strftime("%H:%M:%S")

    # Rise/Set Hour Angle using spherical trig:
    # cos(HA) = (sin(alt) - sin(lat)*sin(dec)) / (cos(lat)*cos(dec))
    # Standard astronomical rise/set altitude threshold including
    # refraction is -0.833 degrees
    alt_threshold = -0.833

    lat_rad = math.radians(sky.latitude)
    dec_rad = math.radians(dec_deg)
    alt_rad = math.radians(alt_threshold)

    denominator = math.cos(lat_rad) * math.cos(dec_rad)
    if denominator == 0:
        return {
            "above_horizon": above_horizon,
            "rise_time": "Unknown",
            "set_time": "Unknown",
            "transit_time": transit_time,
        }

    cos_hour_angle = (math.sin(alt_rad) - math.sin(lat_rad) * math.sin(dec_rad)) / denominator

    if cos_hour_angle < -1.0:
        # Circumpolar
        rise_time = "Circumpolar"
        set_time = "Circumpolar"
    elif cos_hour_angle > 1.0:
        # Never Rises
        rise_time = "Never Rises"
        set_time = "Never Rises"
    else:
        hour_angle_limit_deg = math.degrees(math.acos(cos_hour_angle))

        # Rise is at Hour Angle = -hour_angle_limit
        # Set is at Hour Angle = +hour_angle_limit
        # Calculate difference in seconds from current hour angle
        rise_diff_sec = (-hour_angle_limit_deg / 15.0 - hour_angle) * 3600.0
        set_diff_sec = (hour_angle_limit_deg / 15.0 - hour_angle) * 3600.0

        rise_time = (observation_time + rise_diff_sec * u.s).datetime.strftime("%H:%M:%S")
        set_time = (observation_time + set_diff_sec * u.s).datetime.strftime("%H:%M:%S")

    return {
        "above_horizon": above_horizon,
        "rise_time": rise_time,
        "set_time": set_time,
        "transit_time": transit_time,
    }


def get_object_visibility(sky, ra_deg: float, dec_deg: float, time_input: datetime | Time) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Calculate Rise, Set, Transit times, and current visibility status.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing coordinate transforms and observer location.
    ra_deg : float
        Right Ascension of the target in degrees.
    dec_deg : float
        Declination of the target in degrees.
    time_input : Union[datetime, Time]
        The observation time.

    Returns
    -------
    Dict[str, Any]
        Visibility dictionary containing:
        - "above_horizon": Boolean.
        - "rise_time": Formatted rise time or "Circumpolar" / "Never Rises".
        - "set_time": Formatted set time or "Circumpolar" / "Never Rises".
        - "transit_time": Formatted transit time.
    """
    observation_time = time_input if isinstance(time_input, Time) else Time(time_input)

    # Calculate current Alt/Az to check if above horizon
    alt, _ = sky.radec_to_altaz(ra_deg, dec_deg, observation_time)

    # Transit calculations: Local Sidereal Time = RA
    lst = sky.get_local_sidereal_time(observation_time)
    ra_hours = ra_deg / 15.0
    hour_angle = _hour_angle_from_lst(lst, ra_hours)

    return _rise_set_transit(sky, dec_deg, alt, hour_angle, observation_time)


def get_visibility(
    sky,  # ruff: ignore[missing-type-function-argument]
    objects: list[Target | StellarObject],
    time_input: datetime | Time | None = None,
) -> list[dict[str, Any]]:
    """Calculate visibility parameters for a list of objects at a given time.

    Parameters
    ----------
    sky : Sky
        The Sky instance providing coordinate transforms and observer location.
    objects : List[Union[Target, StellarObject]]
        The list of astronomical objects to check.
    time_input : Optional[Union[datetime, Time]]
        The observation time. Defaults to now.

    Returns
    -------
    List[Dict[str, Any]]
        List of visibility dictionaries corresponding to the objects.
    """
    observation_time = time_input or datetime.utcnow()
    obs_time = observation_time if isinstance(observation_time, Time) else Time(observation_time)

    # Parse RA/Dec for every object up front so the Alt/Az transform below
    # can run once as a single batched SkyCoord array instead of once per
    # object. A per-object SkyCoord/transform_to call takes ~3ms; for a
    # multi-thousand-object catalog that's several seconds, and this method
    # used to do it *twice* per object (once here, once inside
    # get_object_visibility). One batched transform takes milliseconds
    # total regardless of object count.
    valid_objects: list[Target | StellarObject] = []
    ra_values: list[float] = []
    dec_values: list[float] = []
    skipped_stellar_object_count = 0

    for astronomical_object in objects:
        # Parse RA/Dec from Target or StellarObject
        if isinstance(astronomical_object, StellarObject):
            try:
                ra_deg = float(astronomical_object.right_ascension)
                dec_deg = float(astronomical_object.declination)
            except TypeError, ValueError:
                # Photometry/spectroscopy-detected stellar objects that were
                # never plate-solved have empty-string ra/dec; skip them
                # rather than failing the whole visibility request. Counted
                # and logged once below rather than per-object, since a
                # single target's photometry run can produce thousands of
                # these and each is expected/benign, not actionable.
                skipped_stellar_object_count += 1
                continue
        else:  # Target
            try:
                from astrometricslib import parse_coordinate_string

                ra_deg = parse_coordinate_string(str(astronomical_object.ra), is_ra=True)
                dec_deg = parse_coordinate_string(str(astronomical_object.dec), is_ra=False)
            except Exception as parse_error:
                logger.warning(
                    "Failed to parse coordinates for target %s: %s", astronomical_object.id, parse_error
                )
                continue

        valid_objects.append(astronomical_object)
        ra_values.append(ra_deg)
        dec_values.append(dec_deg)

    if skipped_stellar_object_count:
        logger.info(
            "Skipped %d stellar object(s) without plate-solved coordinates.",
            skipped_stellar_object_count,
        )

    if not valid_objects:
        return []

    coords = SkyCoord(ra=ra_values, dec=dec_values, unit=(u.deg, u.deg), frame="icrs")
    altaz_frame = AltAz(obstime=obs_time, location=sky.location)
    altaz_coords = coords.transform_to(altaz_frame)
    altitudes = altaz_coords.alt.deg
    azimuths = altaz_coords.az.deg

    # Local Sidereal Time depends only on the observation time, not the
    # object, so it's computed once and reused for every object's Hour Angle.
    lst = sky.get_local_sidereal_time(obs_time)

    results = []
    for astronomical_object, ra_deg, dec_deg, alt, az in zip(
        valid_objects, ra_values, dec_values, altitudes, azimuths, strict=False
    ):
        ra_hours = ra_deg / 15.0
        hour_angle = _hour_angle_from_lst(lst, ra_hours)

        visibility = _rise_set_transit(sky, dec_deg, alt, hour_angle, obs_time)
        meridian = _meridian_from_hour_angle(sky, hour_angle)

        is_stellar_object = isinstance(astronomical_object, StellarObject)
        results.append({
            "id": astronomical_object.id,
            "name": astronomical_object.id if is_stellar_object else astronomical_object.common_name,
            "ra": (str(astronomical_object.right_ascension) if is_stellar_object else astronomical_object.ra),
            "dec": (str(astronomical_object.declination) if is_stellar_object else astronomical_object.dec),
            "altitude": float(alt),
            "azimuth": float(az),
            "hour_angle": meridian["hour_angle"],
            "flip_required": meridian["flip_required"],
            "time_to_flip_seconds": meridian["time_to_flip_seconds"],
            "rise_time": visibility["rise_time"],
            "set_time": visibility["set_time"],
            "transit_time": visibility["transit_time"],
            "above_horizon": visibility["above_horizon"],
        })
    return results
