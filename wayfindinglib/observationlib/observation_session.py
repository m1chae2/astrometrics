"""Purpose: Observation Session Domain Model.

Description: Defines ObservationSession, wayfindinglib's observatory-side
counterpart to astrometricslib's TargetSession. Composition, not
subclassing: references a TargetSession by ID rather than inheriting from
it, since the two live in different libraries with different lifecycles --
TargetSession is regenerable from frames, ObservationSession is irreplaceable
live-capture data (guiding telemetry, weather) recorded in its own library.
"""

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.session.telemetry import GuidingSample


class WeatherSample(BaseModel):
    """A single weather observation.

    No populator exists yet -- schema-ready, empty until an actual
    weather-station integration is built (only raw, unmodeled INDI
    temperature/humidity property reads exist today).
    """

    model_config = ConfigDict(populate_by_name=True)
    time: float = Field(..., ge=0.0)
    ambient_temperature_c: float | None = Field(None, alias="ambientTemperatureC")
    humidity_percent: float | None = Field(None, alias="humidityPercent")
    dew_point_c: float | None = Field(None, alias="dewPointC")


class ObservationSession(BaseModel):
    """Observatory-side context for one observing night.

    Composition over TargetSession (astrometricslib) by ID reference --
    target_session_id is nullable and linked post-hoc, since
    ObservationSession is recorded live during the night while TargetSession
    is only derivable afterward, once frames exist. The ID reference also
    serves as the quality-data conduit: session_operations.py's
    find_quality_contributions_for_session follows it to read
    astrometricslib's quality records directly, with no separate API.
    """

    model_config = ConfigDict(populate_by_name=True)
    id: str
    target_session_id: str | None = Field(None, alias="targetSessionId")
    sequence_plan_id: str | None = Field(None, alias="sequencePlanId")
    guiding_samples: list[GuidingSample] = Field(default_factory=list, alias="guidingSamples")
    weather_samples: list[WeatherSample] = Field(default_factory=list, alias="weatherSamples")
    created_at: str = Field(..., alias="createdAt")
