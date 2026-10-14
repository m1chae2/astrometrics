"""Purpose: The reply of `ObservationPlanning.get_visibility`.

Description: `get_visibility` says where sky objects are seen from the
observatory. Asked about one moment, each object gets its altitude,
azimuth, rise, set and transit times, and (with ``include=["meridian"]``)
its hour angle and meridian-flip status. Asked about a span of time, each
object also gets a `VisibilitySpan`: its highest point, its meridian
crossings, and when it is clear of the horizon limit and the sky is
fully dark. The span reply also describes the night itself: twilight,
the dark hours and the Moon.

All angles are in degrees. Times are ISO 8601 text in the offset the
caller chose; rise, set and transit at the start moment are UTC times of
day (``HH:MM:SS``), or ``"Circumpolar"`` / ``"Never Rises"``.
"""

from pydantic import BaseModel, ConfigDict, Field


class TimeSpan(BaseModel):
    """One stretch of time, such as a span when an object is usable."""

    model_config = ConfigDict(populate_by_name=True)

    start: str = Field(description="When the stretch begins, as ISO 8601 text.")
    end: str = Field(description="When the stretch ends, as ISO 8601 text.")
    hours: float = Field(description="Length of the stretch, in hours.")


class MeridianStatus(BaseModel):
    """Where an object stands relative to the meridian at one moment."""

    model_config = ConfigDict(populate_by_name=True)

    hour_angle_hours: float = Field(
        description="Hour angle, from -12 to 12 hours. Negative means east of the meridian."
    )
    flip_required: bool = Field(
        description="True once the object is past the meridian by more than the flip delay."
    )
    time_to_flip_seconds: float = Field(
        description="Seconds until the object crosses the meridian. Negative once it has crossed."
    )


class MeridianCrossing(BaseModel):
    """One meridian crossing inside the span, and when the flip is due."""

    model_config = ConfigDict(populate_by_name=True)

    crossing: str = Field(description="When the object crosses the meridian, as ISO 8601 text.")
    flip_due: str = Field(description="When the meridian flip is due, after the configured delay.")


class SeparationRange(BaseModel):
    """The smallest and largest angle between an object and the Moon."""

    model_config = ConfigDict(populate_by_name=True)

    minimum_deg: float = Field(description="Smallest separation over the span, in degrees.")
    maximum_deg: float = Field(description="Largest separation over the span, in degrees.")


class VisibilitySample(BaseModel):
    """One row of the time table for one object."""

    model_config = ConfigDict(populate_by_name=True)

    time: str = Field(description="The moment, as ISO 8601 text.")
    altitude_deg: float = Field(description="Altitude of the object.")
    azimuth_deg: float = Field(description="Azimuth of the object, from north through east.")
    clear: bool = Field(description="True when the object is above the horizon limit at its azimuth.")
    sun_altitude_deg: float = Field(description="Altitude of the Sun.")
    moon_altitude_deg: float = Field(description="Altitude of the Moon.")
    moon_separation_deg: float = Field(description="Angle between the object and the Moon.")


class VisibilitySpan(BaseModel):
    """How one object moves across the sky over the requested span."""

    model_config = ConfigDict(populate_by_name=True)

    highest_altitude_deg: float = Field(description="The highest altitude the object reaches in the span.")
    highest_altitude_at: str = Field(description="When it is highest, as ISO 8601 text.")
    meridian_crossings: list[MeridianCrossing] = Field(
        default_factory=list, description="Each meridian crossing inside the span."
    )
    clear_of_horizon: list[TimeSpan] = Field(
        default_factory=list, description="When the object is above the horizon limit."
    )
    usable: list[TimeSpan] = Field(
        default_factory=list,
        description="When the object is above the horizon limit and the Sun is below -18 degrees.",
    )
    moon_separation: SeparationRange = Field(description="How close the Moon comes.")
    samples: list[VisibilitySample] | None = Field(
        default=None, description='The time table, one row per step. Filled with include=["samples"].'
    )


class ObjectVisibility(BaseModel):
    """Where one object is at the start moment, and over the span if asked."""

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(description="The object's id.")
    name: str | None = Field(default=None, description="The object's common name, when it has one.")
    ra_deg: float = Field(description="Right ascension (J2000), in degrees.")
    dec_deg: float = Field(description="Declination (J2000), in degrees.")
    altitude_deg: float = Field(description="Altitude at the start moment.")
    azimuth_deg: float = Field(description="Azimuth at the start moment, from north through east.")
    above_horizon: bool = Field(description="True when the altitude is above 0 degrees.")
    clear: bool = Field(description="True when the object is above the horizon limit at its azimuth.")
    rise_utc: str = Field(description='UTC time of day it rises, or "Circumpolar" or "Never Rises".')
    set_utc: str = Field(description='UTC time of day it sets, or "Circumpolar" or "Never Rises".')
    transit_utc: str = Field(description="UTC time of day it crosses the meridian.")
    meridian: MeridianStatus | None = Field(
        default=None, description='Hour angle and flip status. Filled with include=["meridian"].'
    )
    span: VisibilitySpan | None = Field(
        default=None, description="Movement over the span. Filled when an end time is given."
    )


class ObservingSite(BaseModel):
    """The observatory position the answer was computed for."""

    model_config = ConfigDict(populate_by_name=True)

    latitude_deg: float = Field(description="Latitude, north positive.")
    longitude_deg: float = Field(description="Longitude, east positive.")
    elevation_m: float = Field(description="Height above sea level, in meters.")


class HorizonZone(BaseModel):
    """A blocked part of the sky, such as a tree line or a roof."""

    model_config = ConfigDict(populate_by_name=True)

    azimuth_start_deg: float = Field(description="Where the zone starts. A zone may wrap past north.")
    azimuth_end_deg: float = Field(description="Where the zone ends.")
    min_clear_altitude_deg: float = Field(description="An object in the zone must be above this altitude.")


class SunEvent(BaseModel):
    """The Sun crossing one twilight altitude."""

    model_config = ConfigDict(populate_by_name=True)

    time: str = Field(description="When it happens, as ISO 8601 text.")
    event: str = Field(description="What happens, such as 'Sun reaches -18 deg (astronomical twilight)'.")
    going: str = Field(description='"down" in the evening, "up" in the morning.')


class MoonConditions(BaseModel):
    """How bright the Moon is and when it is up."""

    model_config = ConfigDict(populate_by_name=True)

    illumination_fraction: float = Field(description="Average lit fraction over the span, from 0 to 1.")
    above_horizon: list[TimeSpan] = Field(default_factory=list, description="When the Moon is up.")


class NightConditions(BaseModel):
    """The span asked about, and the Sun and Moon during it."""

    model_config = ConfigDict(populate_by_name=True)

    start: str = Field(description="First moment of the table, as ISO 8601 text.")
    end: str = Field(description="Last moment of the table, as ISO 8601 text.")
    step_minutes: float = Field(description="Time between rows.")
    sun_events: list[SunEvent] = Field(default_factory=list, description="Sunset, twilight and sunrise.")
    astronomical_night: list[TimeSpan] = Field(
        default_factory=list, description="When the Sun is below -18 degrees."
    )
    moon: MoonConditions = Field(description="The Moon during the span.")


class VisibilityReport(BaseModel):
    """Where the asked-about objects are, at one moment or over a span."""

    model_config = ConfigDict(populate_by_name=True)

    time: str = Field(description="The start moment, as ISO 8601 text.")
    time_zone_offset_hours: float = Field(description="The UTC offset the times are written in.")
    site: ObservingSite = Field(description="The observatory position.")
    minimum_altitude_deg: float = Field(description="The lowest altitude that counts as clear.")
    horizon_zones: list[HorizonZone] = Field(
        default_factory=list, description="Blocked parts of the sky the caller gave."
    )
    objects: list[ObjectVisibility] = Field(
        default_factory=list, description="One entry per object, in the order asked for."
    )
    night: NightConditions | None = Field(
        default=None, description="The span, twilight and Moon. Filled when an end time is given."
    )
