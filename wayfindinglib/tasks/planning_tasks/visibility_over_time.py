"""Purpose: Plan a night: where objects are hour by hour, and when to image.

Description: `get_visibility` answers for one moment. Planning a night needs
the whole span: when each object clears the trees and the horizon, when it
crosses the meridian, when the Sun and Moon are up, and how close the Moon
comes. This module computes a table over a time span for several objects at
once, with an optional minimum altitude and an optional list of blocked
sky ranges (a tree line, a roof) given by the caller.

A time is "usable" for an object when the object is above the horizon limit
at its azimuth and the Sun is below -18 degrees (astronomical night). The
Moon is reported next to it and not subtracted, because whether moonlight
matters depends on the filter and the target.

Nothing here reads or writes the library. For exact behavior, read the code.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_body, get_sun
from astropy.time import Time

from wayfindinglib.tasks.planning_tasks.visibility_operations import (
    _hour_angle_from_lst,
    _meridian_from_hour_angle,
    _rise_set_transit,
)

ASTRONOMICAL_NIGHT_SUN_ALTITUDE_DEG = -18.0
"""The Sun's altitude below which the sky is fully dark."""

SIDEREAL_DAY_HOURS = 23.9344696
"""Hours between two meridian crossings of the same star."""

DEFAULT_WINDOW_HOURS = 12.0
"""Length of the span when no end time is given."""

MAXIMUM_TIME_STEPS = 150
"""Most time samples per object. A longer span needs a larger step."""

MAXIMUM_OBJECTS = 30
"""Most objects one table covers."""

MINIMUM_ZONE_FIELDS = ("azimuth_start_deg", "azimuth_end_deg", "min_clear_altitude_deg")
"""The keys every blocked sky range must have."""


def _format_time(epoch_seconds: float, offset_hours: float) -> str:
    """Write a Unix time as ISO 8601 in the chosen UTC offset.

    Parameters
    ----------
    epoch_seconds : `float`
        Seconds since the Unix epoch.
    offset_hours : `float`
        The offset from UTC to write the time in, such as -6 for Montana
        in summer.

    Returns
    -------
    text : `str`
        The time to the second, with its offset (``Z`` for none).
    """
    moment = datetime.fromtimestamp(epoch_seconds, tz=timezone(timedelta(hours=offset_hours)))
    text = moment.strftime("%Y-%m-%dT%H:%M:%S%z")
    if offset_hours == 0:
        return text.replace("+0000", "Z")
    return f"{text[:-2]}:{text[-2:]}"


def _required_altitudes(
    azimuths_deg: np.ndarray, minimum_altitude_deg: float, zones: list[dict[str, Any]]
) -> np.ndarray:
    """Find the altitude an object must exceed at each azimuth.

    Parameters
    ----------
    azimuths_deg : `numpy.ndarray`
        The azimuths, in degrees.
    minimum_altitude_deg : `float`
        The lowest altitude allowed anywhere.
    zones : `list` [`dict`]
        Blocked sky ranges. A range may wrap past north (start above end).

    Returns
    -------
    required : `numpy.ndarray`
        The limit at each azimuth: the highest of the minimum and any zone
        that covers the azimuth.
    """
    required = np.full(azimuths_deg.shape, float(minimum_altitude_deg))
    wrapped = azimuths_deg % 360.0
    for zone in zones:
        start, end = float(zone["azimuth_start_deg"]) % 360.0, float(zone["azimuth_end_deg"]) % 360.0
        inside = (
            (wrapped >= start) & (wrapped <= end) if start <= end else (wrapped >= start) | (wrapped <= end)
        )
        required = np.where(inside, np.maximum(required, float(zone["min_clear_altitude_deg"])), required)
    return required


def _intervals_where_positive(times: np.ndarray, margin: np.ndarray) -> list[tuple[float, float]]:
    """Find the spans where a margin is zero or above.

    Parameters
    ----------
    times : `numpy.ndarray`
        Sample times, increasing.
    margin : `numpy.ndarray`
        A value that is at least zero while the condition holds.

    Returns
    -------
    spans : `list` [`tuple` [`float`, `float`]]
        Start and end times of each span, found by straight-line
        interpolation between samples. A span that is already running at
        the first sample, or still running at the last, ends at that sample.
    """
    spans: list[tuple[float, float]] = []
    start: float | None = None
    for index, value in enumerate(margin):
        if value >= 0 and start is None:
            if index == 0:
                start = float(times[0])
            else:
                fraction = -margin[index - 1] / (value - margin[index - 1])
                start = float(times[index - 1] + fraction * (times[index] - times[index - 1]))
        elif value < 0 and start is not None:
            fraction = margin[index - 1] / (margin[index - 1] - value)
            spans.append((start, float(times[index - 1] + fraction * (times[index] - times[index - 1]))))
            start = None
    if start is not None:
        spans.append((start, float(times[-1])))
    return spans


def _span_texts(spans: list[tuple[float, float]], offset_hours: float) -> list[dict[str, Any]]:
    """Write spans as start, end and length.

    Parameters
    ----------
    spans : `list` [`tuple` [`float`, `float`]]
        Start and end times in Unix seconds.
    offset_hours : `float`
        The UTC offset to write times in.

    Returns
    -------
    texts : `list` [`dict`]
        One entry per span with ``from``, ``to`` and ``hours``.
    """
    return [
        {
            "from": _format_time(start, offset_hours),
            "to": _format_time(end, offset_hours),
            "hours": round((end - start) / 3600.0, 2),
        }
        for start, end in spans
    ]


def build_visibility_over_time(
    sky: Any,
    objects: list[Any],
    start_epoch: float,
    end_epoch: float | None,
    step_minutes: float,
    minimum_altitude_deg: float,
    horizon_zones: list[dict[str, Any]] | None,
    timezone_offset_hours: float,
    include_samples: bool,
) -> dict[str, Any]:
    """Build the night-planning table for a list of objects.

    Parameters
    ----------
    sky : `Sky`
        Supplies the observing location, sidereal time and flip delay.
    objects : `list`
        Sky objects with ``id`` and coordinates, as `get_visibility` takes.
    start_epoch : `float`
        Start of the span, in Unix seconds.
    end_epoch : `float` or `None`
        End of the span; `None` means 12 hours after the start.
    step_minutes : `float`
        Time between samples. At least 1.
    minimum_altitude_deg : `float`
        The lowest altitude that counts as clear, in degrees.
    horizon_zones : `list` [`dict`] or `None`
        Blocked sky ranges, each with ``azimuth_start_deg``,
        ``azimuth_end_deg`` and ``min_clear_altitude_deg``.
    timezone_offset_hours : `float`
        The UTC offset to write times in.
    include_samples : `bool`
        Whether to include the hour-by-hour rows for each object.

    Returns
    -------
    table : `dict` [`str`, `Any`]
        The site, the Sun and Moon, and one entry per object; or
        ``{"error": ...}`` for a request that cannot be answered.
    """
    zones = list(horizon_zones or [])
    for zone in zones:
        missing = [name for name in MINIMUM_ZONE_FIELDS if name not in zone]
        if missing:
            return {"error": f"A horizon zone needs {', '.join(MINIMUM_ZONE_FIELDS)}; missing {missing}."}
    if not objects:
        return {"error": "Give at least one object."}
    if len(objects) > MAXIMUM_OBJECTS:
        return {"error": f"At most {MAXIMUM_OBJECTS} objects per table; got {len(objects)}."}
    step_seconds = max(1.0, float(step_minutes)) * 60.0
    end_epoch = end_epoch if end_epoch is not None else start_epoch + DEFAULT_WINDOW_HOURS * 3600.0
    if end_epoch < start_epoch:
        return {"error": "The end time must not be before the start time."}
    count = int((end_epoch - start_epoch) // step_seconds) + 1
    if count > MAXIMUM_TIME_STEPS:
        return {
            "error": f"{count} time steps is more than {MAXIMUM_TIME_STEPS}. Use a larger "
            "step_minutes or a shorter span."
        }

    epochs = start_epoch + step_seconds * np.arange(count)
    times = Time(epochs, format="unix")
    frame = AltAz(obstime=times, location=sky.location)
    sun_altitude = get_sun(times).transform_to(frame).alt.deg
    moon_altaz = get_body("moon", times, sky.location).transform_to(frame)
    moon_altitude = moon_altaz.alt.deg
    elongation = get_sun(times).separation(get_body("moon", times))
    illumination = 0.5 * (1.0 - np.cos(elongation.rad))

    dark_margin = ASTRONOMICAL_NIGHT_SUN_ALTITUDE_DEG - sun_altitude
    result: dict[str, Any] = {
        "site": {
            "latitude_deg": round(float(sky.latitude), 4),
            "longitude_deg": round(float(sky.longitude), 4),
            "elevation_m": round(float(sky.elevation), 1),
        },
        "window": {
            "from": _format_time(float(epochs[0]), timezone_offset_hours),
            "to": _format_time(float(epochs[-1]), timezone_offset_hours),
            "step_minutes": step_seconds / 60.0,
            "time_zone_offset_hours": timezone_offset_hours,
        },
        "horizon": {"minimum_altitude_deg": minimum_altitude_deg, "blocked_ranges": zones},
        "astronomical_night": _span_texts(
            _intervals_where_positive(epochs, dark_margin), timezone_offset_hours
        ),
        "moon": {
            "illumination_fraction": round(float(np.mean(illumination)), 2),
            "above_horizon": _span_texts(
                _intervals_where_positive(epochs, moon_altitude), timezone_offset_hours
            ),
        },
        "objects": [],
    }

    start_time = Time(float(epochs[0]), format="unix")
    sidereal_hours_at_start = sky.get_local_sidereal_time(start_time)
    flip_delay_hours = float(sky.meridian_flip_delay_min) / 60.0
    for sky_object in objects:
        ra_deg, dec_deg = _object_coordinates(sky_object)
        coordinates = SkyCoord(ra=ra_deg * u.deg, dec=dec_deg * u.deg, frame="icrs")
        altaz = coordinates.transform_to(frame)
        altitude, azimuth = altaz.alt.deg, altaz.az.deg
        required = _required_altitudes(azimuth, minimum_altitude_deg, zones)
        clear_margin = altitude - required
        separation = altaz.separation(moon_altaz).deg

        transits = []
        hours_to_first = ((ra_deg / 15.0) - sidereal_hours_at_start) % 24.0
        crossing_hours = hours_to_first / 1.0027379093
        while crossing_hours * 3600.0 <= end_epoch - start_epoch:
            transit_epoch = start_epoch + crossing_hours * 3600.0
            transits.append({
                "meridian_crossing": _format_time(transit_epoch, timezone_offset_hours),
                "flip_due": _format_time(transit_epoch + flip_delay_hours * 3600.0, timezone_offset_hours),
            })
            crossing_hours += SIDEREAL_DAY_HOURS

        highest = int(np.argmax(altitude))
        hour_angle = _hour_angle_from_lst(sidereal_hours_at_start, ra_deg / 15.0)
        meridian_now = _meridian_from_hour_angle(sky, hour_angle)
        rise_set = _rise_set_transit(sky, dec_deg, float(altitude[0]), hour_angle, start_time)
        entry: dict[str, Any] = {
            "at_start": {
                "altitude_deg": round(float(altitude[0]), 2),
                "azimuth_deg": round(float(azimuth[0]), 2),
                "hour_angle_hours": round(meridian_now["hour_angle"], 4),
                "flip_required": meridian_now["flip_required"],
                "time_to_flip_seconds": round(meridian_now["time_to_flip_seconds"], 0),
                "rise_utc": rise_set["rise_time"],
                "set_utc": rise_set["set_time"],
                "transit_utc": rise_set["transit_time"],
            },
            "id": sky_object.id,
            "ra_deg": round(ra_deg, 4),
            "dec_deg": round(dec_deg, 4),
            "highest_altitude_deg": round(float(altitude[highest]), 1),
            "highest_altitude_at": _format_time(float(epochs[highest]), timezone_offset_hours),
            "meridian": transits,
            "clear_of_horizon": _span_texts(
                _intervals_where_positive(epochs, clear_margin), timezone_offset_hours
            ),
            "usable": _span_texts(
                _intervals_where_positive(epochs, np.minimum(clear_margin, dark_margin)),
                timezone_offset_hours,
            ),
            "moon_separation_deg": {
                "minimum": round(float(np.min(separation)), 0),
                "maximum": round(float(np.max(separation)), 0),
            },
        }
        if include_samples:
            entry["samples"] = [
                {
                    "time": _format_time(float(epochs[index]), timezone_offset_hours),
                    "altitude_deg": round(float(altitude[index]), 1),
                    "azimuth_deg": round(float(azimuth[index]), 0),
                    "clear": bool(clear_margin[index] >= 0),
                    "sun_altitude_deg": round(float(sun_altitude[index]), 0),
                    "moon_altitude_deg": round(float(moon_altitude[index]), 0),
                    "moon_separation_deg": round(float(separation[index]), 0),
                }
                for index in range(count)
            ]
        result["objects"].append(entry)
    return result


def _object_coordinates(sky_object: Any) -> tuple[float, float]:
    """Read a sky object's right ascension and declination in degrees.

    Parameters
    ----------
    sky_object : `Any`
        A library target (coordinates as text) or a stellar object (numbers).

    Returns
    -------
    coordinates : `tuple` [`float`, `float`]
        Right ascension and declination, in degrees.
    """
    from astrometricslib import StellarObject, parse_coordinate_string

    if isinstance(sky_object, StellarObject):
        return float(sky_object.right_ascension), float(sky_object.declination)
    return (
        parse_coordinate_string(str(sky_object.ra), is_ra=True),
        parse_coordinate_string(str(sky_object.dec), is_ra=False),
    )
