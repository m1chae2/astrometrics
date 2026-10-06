"""Purpose: Say where sky objects are, at one moment or over a night.

Description: This module does the work behind
`ObservationPlanning.get_visibility`. It first turns the caller's inputs
into what the sums need: names into library targets or stars (or SIMBAD
objects), and a time given as text, a `datetime` or an astropy `Time`
into one astropy `Time`.

For one moment it computes, for every object at once, the altitude and
azimuth, the hour angle, and the rise, set and transit times. One
coordinate transform covers the whole list, so the whole library takes
milliseconds.

For a span of time it also builds a time table for each object: when it
clears the horizon limit (an altitude, plus blocked ranges such as a tree
line given by the caller), when it crosses the meridian, and when it is
*usable*, meaning clear while the Sun is below -18 degrees. It also
describes the night: twilight, the fully dark span, and the Moon. The
Moon is reported next to each object and not subtracted, because whether
moonlight matters depends on the filter and the target.

Nothing here reads or writes stored records except the target and star
lookups.
"""

import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import astropy.units as u
import numpy as np
from astropy.coordinates import AltAz, SkyCoord, get_body, get_sun
from astropy.time import Time

from astrometricslib import (
    InvalidArgumentError,
    StellarObject,
    Target,
    parse_coordinate_string,
    to_epoch_seconds,
)
from wayfindinglib.models.planning.visibility import (
    HorizonZone,
    MeridianCrossing,
    MeridianStatus,
    MoonConditions,
    NightConditions,
    ObjectVisibility,
    ObservingSite,
    SeparationRange,
    SunEvent,
    TimeSpan,
    VisibilityReport,
    VisibilitySample,
    VisibilitySpan,
)
from wayfindinglib.tasks.planning_tasks.visibility_operations import (
    hour_angle_from_lst,
    meridian_from_hour_angle,
    rise_set_transit,
)

logger = logging.getLogger(__name__)

INCLUDE_SECTIONS = ("meridian", "samples")
"""The optional sections `get_visibility` can add to each object."""

ASTRONOMICAL_NIGHT_SUN_ALTITUDE_DEG = -18.0
"""The Sun's altitude below which the sky is fully dark."""

SIDEREAL_DAY_HOURS = 23.9344696
"""Hours between two meridian crossings of the same star."""

SIDEREAL_RATE = 1.0027379093
"""Sidereal hours that pass in one ordinary (solar) hour."""

DEFAULT_STEP_MINUTES = 30.0
"""Time between table rows when the caller gives none."""

MAXIMUM_TIME_STEPS = 150
"""Most table rows per object. A longer span needs a larger step."""

MAXIMUM_SPAN_OBJECTS = 30
"""Most objects one time table covers. One moment has no limit."""

SUN_ALTITUDE_EVENTS = (
    (-0.833, "sunset or sunrise"),
    (-6.0, "civil twilight"),
    (-12.0, "nautical twilight"),
    (-18.0, "astronomical twilight"),
)
"""The Sun altitudes, in degrees, at which each twilight stage begins or
ends. The first is the usual rim-on-the-horizon value that allows for
refraction."""


def to_astropy_time(time: str | datetime | Time | None, name: str = "time") -> Time:
    """Turn any accepted time into an astropy `Time` in UTC.

    Parameters
    ----------
    time : `str`, `datetime`, `astropy.time.Time` or `None`
        ``None`` or ``"now"`` mean the current time. A string is ISO 8601;
        an offset such as ``-06:00`` is honored and no offset means UTC.
        A `datetime` with no time zone is taken as UTC.
    name : `str`, optional
        The argument's name, for the error message.

    Returns
    -------
    moment : `astropy.time.Time`
        The same instant.
    """
    if isinstance(time, Time):
        return time
    if time is None or (isinstance(time, str) and time.strip().lower() in ("", "now")):
        return Time(datetime.now(UTC).timestamp(), format="unix")
    return Time(to_epoch_seconds(time, name), format="unix")


def resolve_sky_objects(sky: Any, objects: Sequence[Any] | None) -> list[Target | StellarObject]:
    """Turn the caller's object list into targets and stars.

    Parameters
    ----------
    sky : `Sky`
        Looks names up in the library, then in SIMBAD.
    objects : `Sequence` or `None`
        Each item is a name or id, a `Target` or `StellarObject`, or a
        dictionary ``{"id", "ra_deg", "dec_deg"}`` (``"name"`` optional)
        for a point with known coordinates. `None` means every library
        target.

    Returns
    -------
    resolved : `list` [`Target` or `StellarObject`]
        The objects, in the order given.

    Raises
    ------
    InvalidArgumentError
        If ``objects`` is not a list, or an item has the wrong form.
    """
    from wayfindinglib.tasks.planning_tasks import catalog_operations, resolution_operations

    if objects is None:
        return catalog_operations.astrometrics_catalog(sky, 0.0, 0.0, 180.0, False)
    if isinstance(objects, str) or not isinstance(objects, Sequence):
        raise InvalidArgumentError(
            "objects must be a list of names, targets, stars or coordinate dictionaries."
        )
    resolved: list[Target | StellarObject] = []
    for item in objects:
        if isinstance(item, Target | StellarObject):
            resolved.append(item)
        elif isinstance(item, str):
            resolved.append(resolution_operations.resolve_target_coordinates(sky, item))
        elif isinstance(item, dict):
            missing = [key for key in ("id", "ra_deg", "dec_deg") if key not in item]
            if missing:
                raise InvalidArgumentError(
                    f"Coordinate dictionary {item!r} needs the key(s) {missing}: "
                    'use {"id": ..., "ra_deg": ..., "dec_deg": ...}.'
                )
            resolved.append(
                StellarObject(
                    id=str(item["id"]),
                    name=str(item.get("name", item["id"])),
                    ra=float(item["ra_deg"]),
                    dec=float(item["dec_deg"]),
                )
            )
        else:
            raise InvalidArgumentError(f"Cannot use {item!r} as a sky object.")
    return resolved


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
    azimuths_deg: np.ndarray, minimum_altitude_deg: float, zones: list[HorizonZone]
) -> np.ndarray:
    """Find the altitude an object must exceed at each azimuth.

    Parameters
    ----------
    azimuths_deg : `numpy.ndarray`
        The azimuths, in degrees.
    minimum_altitude_deg : `float`
        The lowest altitude allowed anywhere.
    zones : `list` [`HorizonZone`]
        Blocked sky ranges. A range may wrap past north (start above end).

    Returns
    -------
    required : `numpy.ndarray`
        The limit at each azimuth: the highest of the minimum and any zone
        that covers the azimuth.
    """
    required = np.full(np.shape(azimuths_deg), float(minimum_altitude_deg))
    wrapped = np.asarray(azimuths_deg) % 360.0
    for zone in zones:
        start, end = zone.azimuth_start_deg % 360.0, zone.azimuth_end_deg % 360.0
        inside = (
            (wrapped >= start) & (wrapped <= end) if start <= end else (wrapped >= start) | (wrapped <= end)
        )
        required = np.where(inside, np.maximum(required, zone.min_clear_altitude_deg), required)
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


def _sun_events(epochs: np.ndarray, sun_altitude: np.ndarray, offset_hours: float) -> list[SunEvent]:
    """List when the Sun crosses each twilight altitude in the window.

    The crossing time is interpolated between the two time steps either
    side of it, so it is better than the step size.

    Parameters
    ----------
    epochs : `numpy.ndarray`
        The time steps, in Unix seconds.
    sun_altitude : `numpy.ndarray`
        The Sun's altitude at each step, in degrees.
    offset_hours : `float`
        The UTC offset to write times in.

    Returns
    -------
    events : `list` [`SunEvent`]
        Each crossing and whether the Sun was going down or up, in time
        order.
    """
    events: list[tuple[float, SunEvent]] = []
    for threshold, label in SUN_ALTITUDE_EVENTS:
        above = sun_altitude - threshold
        for index in range(len(above) - 1):
            if above[index] == 0 or above[index] * above[index + 1] >= 0:
                continue
            fraction = above[index] / (above[index] - above[index + 1])
            moment = float(epochs[index] + fraction * (epochs[index + 1] - epochs[index]))
            events.append((
                moment,
                SunEvent(
                    time=_format_time(moment, offset_hours),
                    event=f"Sun reaches {threshold:g} deg ({label})",
                    going="down" if above[index + 1] < above[index] else "up",
                ),
            ))
    events.sort(key=lambda pair: pair[0])
    return [event for _moment, event in events]


def _time_spans(spans: list[tuple[float, float]], offset_hours: float) -> list[TimeSpan]:
    """Write spans as start, end and length.

    Parameters
    ----------
    spans : `list` [`tuple` [`float`, `float`]]
        Start and end times in Unix seconds.
    offset_hours : `float`
        The UTC offset to write times in.

    Returns
    -------
    spans : `list` [`TimeSpan`]
        One entry per span.
    """
    return [
        TimeSpan(
            start=_format_time(start, offset_hours),
            end=_format_time(end, offset_hours),
            hours=round((end - start) / 3600.0, 2),
        )
        for start, end in spans
    ]


def _object_coordinates(sky_object: Target | StellarObject) -> tuple[float, float] | None:
    """Read a sky object's right ascension and declination in degrees.

    Parameters
    ----------
    sky_object : `Target` or `StellarObject`
        A library target (coordinates as text) or a star (numbers).

    Returns
    -------
    coordinates : `tuple` [`float`, `float`] or `None`
        Right ascension and declination, in degrees, or `None` when the
        object has no usable position (a star that was never plate
        solved, or a target whose text cannot be read).
    """
    try:
        if isinstance(sky_object, StellarObject):
            return float(sky_object.right_ascension), float(sky_object.declination)
        return (
            parse_coordinate_string(str(sky_object.ra), is_ra=True),
            parse_coordinate_string(str(sky_object.dec), is_ra=False),
        )
    except (TypeError, ValueError, InvalidArgumentError) as error:  # float() or the coordinate parser
        logger.debug("Skipping %s, which has no usable position: %s", sky_object.id, error)
        return None


def _object_name(sky_object: Target | StellarObject) -> str | None:
    """Give an object's common name, when it has one.

    Parameters
    ----------
    sky_object : `Target` or `StellarObject`
        The object.

    Returns
    -------
    name : `str` or `None`
        The target's common name or the star's name.
    """
    if isinstance(sky_object, StellarObject):
        return getattr(sky_object, "name", None) or None
    return sky_object.common_name or None


def _check_span_arguments(
    start_epoch: float,
    end_epoch: float | None,
    step_minutes: float | None,
    object_count: int,
    sections: frozenset[str],
) -> float | None:
    """Refuse arguments that do not fit together, and pick the step.

    Parameters
    ----------
    start_epoch : `float`
        Start of the span, in Unix seconds.
    end_epoch : `float` or `None`
        End of the span; `None` for one moment.
    step_minutes : `float` or `None`
        Time between rows, or `None` for the default.
    object_count : `int`
        How many objects were asked about.
    sections : `frozenset` [`str`]
        The sections asked for with ``include``.

    Returns
    -------
    step_seconds : `float` or `None`
        Time between rows, or `None` for one moment.

    Raises
    ------
    InvalidArgumentError
        If a span-only argument comes without an end time, the span runs
        backwards, or it has too many rows or objects.
    """
    if end_epoch is None:
        unused = ["step_minutes"] if step_minutes is not None else []
        unused += ['include=["samples"]'] if "samples" in sections else []
        if unused:
            raise InvalidArgumentError(
                f"{' and '.join(unused)} need an end_time: they describe a span, not one moment."
            )
        return None
    if end_epoch < start_epoch:
        raise InvalidArgumentError("end_time must not be before time.")
    if object_count > MAXIMUM_SPAN_OBJECTS:
        raise InvalidArgumentError(
            f"At most {MAXIMUM_SPAN_OBJECTS} objects per time table; got {object_count}."
        )
    step_seconds = max(1.0, float(step_minutes if step_minutes is not None else DEFAULT_STEP_MINUTES)) * 60.0
    count = int((end_epoch - start_epoch) // step_seconds) + 1
    if count > MAXIMUM_TIME_STEPS:
        raise InvalidArgumentError(
            f"{count} time steps is more than {MAXIMUM_TIME_STEPS}. "
            "Use a larger step_minutes or a shorter span."
        )
    return step_seconds


def build_visibility_report(
    sky: Any,
    objects: list[Target | StellarObject],
    start: Time,
    end: Time | None,
    step_minutes: float | None,
    sections: frozenset[str],
    minimum_altitude_deg: float,
    horizon_zones: list[HorizonZone],
    timezone_offset_hours: float,
) -> VisibilityReport:
    """Say where each object is at `start`, and over the span to `end`.

    Parameters
    ----------
    sky : `Sky`
        Supplies the observing location, sidereal time and flip delay.
    objects : `list` [`Target` or `StellarObject`]
        The objects. One with no usable position is left out.
    start : `astropy.time.Time`
        The moment, or the start of the span.
    end : `astropy.time.Time` or `None`
        End of the span; `None` for one moment.
    step_minutes : `float` or `None`
        Time between rows of the span table. Defaults to 30.
    sections : `frozenset` [`str`]
        Optional sections: ``"meridian"`` and ``"samples"``.
    minimum_altitude_deg : `float`
        The lowest altitude that counts as clear, in degrees.
    horizon_zones : `list` [`HorizonZone`]
        Blocked sky ranges.
    timezone_offset_hours : `float`
        The UTC offset to write times in.

    Returns
    -------
    report : `VisibilityReport`
        The site, one entry per object, and for a span the night itself.
    """
    start_epoch = float(start.unix)
    end_epoch = float(end.unix) if end is not None else None
    step_seconds = _check_span_arguments(start_epoch, end_epoch, step_minutes, len(objects), sections)

    positioned = [(item, position) for item in objects if (position := _object_coordinates(item))]
    report = VisibilityReport(
        time=_format_time(start_epoch, timezone_offset_hours),
        time_zone_offset_hours=timezone_offset_hours,
        site=ObservingSite(
            latitude_deg=round(float(sky.latitude), 4),
            longitude_deg=round(float(sky.longitude), 4),
            elevation_m=round(float(sky.elevation), 1),
        ),
        minimum_altitude_deg=minimum_altitude_deg,
        horizon_zones=horizon_zones,
    )
    if not positioned:
        return report

    ra_values = [coordinates[0] for _item, coordinates in positioned]
    dec_values = [coordinates[1] for _item, coordinates in positioned]
    coordinates = SkyCoord(ra=ra_values, dec=dec_values, unit=(u.deg, u.deg), frame="icrs")
    altaz_now = coordinates.transform_to(AltAz(obstime=start, location=sky.location))
    altitudes, azimuths = altaz_now.alt.deg, altaz_now.az.deg
    clear_now = altitudes >= _required_altitudes(azimuths, minimum_altitude_deg, horizon_zones)
    sidereal_hours = sky.get_local_sidereal_time(start)

    for index, (item, (ra_deg, dec_deg)) in enumerate(positioned):
        hour_angle = hour_angle_from_lst(sidereal_hours, ra_deg / 15.0)
        rise_set = rise_set_transit(sky, dec_deg, float(altitudes[index]), hour_angle, start)
        entry = ObjectVisibility(
            id=str(item.id),
            name=_object_name(item),
            ra_deg=round(ra_deg, 6),
            dec_deg=round(dec_deg, 6),
            altitude_deg=round(float(altitudes[index]), 3),
            azimuth_deg=round(float(azimuths[index]), 3),
            above_horizon=bool(rise_set["above_horizon"]),
            clear=bool(clear_now[index]),
            rise_utc=rise_set["rise_time"],
            set_utc=rise_set["set_time"],
            transit_utc=rise_set["transit_time"],
        )
        if "meridian" in sections:
            meridian = meridian_from_hour_angle(sky, hour_angle)
            entry.meridian = MeridianStatus(
                hour_angle_hours=round(float(meridian["hour_angle"]), 4),
                flip_required=bool(meridian["flip_required"]),
                time_to_flip_seconds=round(float(meridian["time_to_flip_seconds"]), 0),
            )
        report.objects.append(entry)

    if end_epoch is not None and step_seconds is not None:
        _add_span(sky, report, coordinates, start_epoch, end_epoch, step_seconds, sections)
    return report


def _add_span(
    sky: Any,
    report: VisibilityReport,
    coordinates: SkyCoord,
    start_epoch: float,
    end_epoch: float,
    step_seconds: float,
    sections: frozenset[str],
) -> None:
    """Fill the night and each object's movement over the span.

    Parameters
    ----------
    sky : `Sky`
        Supplies the observing location, sidereal time and flip delay.
    report : `VisibilityReport`
        The report whose objects already hold the start-moment values.
        Changed in place.
    coordinates : `astropy.coordinates.SkyCoord`
        The objects' positions, in the same order as ``report.objects``.
    start_epoch, end_epoch : `float`
        The span, in Unix seconds.
    step_seconds : `float`
        Time between rows.
    sections : `frozenset` [`str`]
        Optional sections; ``"samples"`` adds the time table.
    """
    offset_hours = report.time_zone_offset_hours
    count = int((end_epoch - start_epoch) // step_seconds) + 1
    epochs = start_epoch + step_seconds * np.arange(count)
    times = Time(epochs, format="unix")
    frame = AltAz(obstime=times, location=sky.location)
    sun_altitude = get_sun(times).transform_to(frame).alt.deg
    moon_altaz = get_body("moon", times, sky.location).transform_to(frame)
    moon_altitude = moon_altaz.alt.deg
    elongation = get_sun(times).separation(get_body("moon", times))
    illumination = 0.5 * (1.0 - np.cos(elongation.rad))
    dark_margin = ASTRONOMICAL_NIGHT_SUN_ALTITUDE_DEG - sun_altitude

    report.night = NightConditions(
        start=_format_time(float(epochs[0]), offset_hours),
        end=_format_time(float(epochs[-1]), offset_hours),
        step_minutes=step_seconds / 60.0,
        sun_events=_sun_events(epochs, sun_altitude, offset_hours),
        astronomical_night=_time_spans(_intervals_where_positive(epochs, dark_margin), offset_hours),
        moon=MoonConditions(
            illumination_fraction=round(float(np.mean(illumination)), 2),
            above_horizon=_time_spans(_intervals_where_positive(epochs, moon_altitude), offset_hours),
        ),
    )

    sidereal_hours_at_start = sky.get_local_sidereal_time(Time(start_epoch, format="unix"))
    flip_delay_hours = float(sky.meridian_flip_delay_min) / 60.0
    for index, entry in enumerate(report.objects):
        altaz = coordinates[index].transform_to(frame)
        altitude, azimuth = altaz.alt.deg, altaz.az.deg
        clear_margin = altitude - _required_altitudes(
            azimuth, report.minimum_altitude_deg, report.horizon_zones
        )
        separation = altaz.separation(moon_altaz).deg

        crossings = []
        crossing_hours = (((entry.ra_deg / 15.0) - sidereal_hours_at_start) % 24.0) / SIDEREAL_RATE
        while crossing_hours * 3600.0 <= end_epoch - start_epoch:
            crossing_epoch = start_epoch + crossing_hours * 3600.0
            crossings.append(
                MeridianCrossing(
                    crossing=_format_time(crossing_epoch, offset_hours),
                    flip_due=_format_time(crossing_epoch + flip_delay_hours * 3600.0, offset_hours),
                )
            )
            crossing_hours += SIDEREAL_DAY_HOURS

        highest = int(np.argmax(altitude))
        entry.span = VisibilitySpan(
            highest_altitude_deg=round(float(altitude[highest]), 1),
            highest_altitude_at=_format_time(float(epochs[highest]), offset_hours),
            meridian_crossings=crossings,
            clear_of_horizon=_time_spans(_intervals_where_positive(epochs, clear_margin), offset_hours),
            usable=_time_spans(
                _intervals_where_positive(epochs, np.minimum(clear_margin, dark_margin)), offset_hours
            ),
            moon_separation=SeparationRange(
                minimum_deg=round(float(np.min(separation)), 0),
                maximum_deg=round(float(np.max(separation)), 0),
            ),
        )
        if "samples" in sections:
            entry.span.samples = [
                VisibilitySample(
                    time=_format_time(float(epochs[row]), offset_hours),
                    altitude_deg=round(float(altitude[row]), 1),
                    azimuth_deg=round(float(azimuth[row]), 0),
                    clear=bool(clear_margin[row] >= 0),
                    sun_altitude_deg=round(float(sun_altitude[row]), 0),
                    moon_altitude_deg=round(float(moon_altitude[row]), 0),
                    moon_separation_deg=round(float(separation[row]), 0),
                )
                for row in range(count)
            ]
