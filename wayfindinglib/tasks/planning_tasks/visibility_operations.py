"""Purpose: Hour angle, meridian-flip status, and rise, set and transit times.

Description: Small sums used by `visibility_report`, which answers
`ObservationPlanning.get_visibility`.
Each takes values the caller already computed (a sidereal time, an
altitude), so a caller that handles many objects at once can compute
those once for the whole list.
"""

import logging
import math
from typing import Any

import astropy.units as u
from astropy.time import Time

logger = logging.getLogger(__name__)


def hour_angle_from_lst(lst_hours: float, ra_hours: float) -> float:
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


def meridian_from_hour_angle(sky: Any, hour_angle: float) -> dict[str, Any]:
    """Compute meridian flip status from an already-known Hour Angle.

    Parameters
    ----------
    sky : `SkyEngine`
        Supplies the configured meridian-flip delay.
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


def rise_set_transit(
    sky: Any,
    dec_deg: float,
    alt_deg: float,
    hour_angle: float,
    observation_time: Time,
) -> dict[str, Any]:
    """Compute rise/set/transit times from an already-known altitude/HA.

    Parameters
    ----------
    sky : `SkyEngine`
        Supplies the observer latitude.
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
