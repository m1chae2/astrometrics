"""Data structures for tracking moving objects (like asteroids).

These classes store information about things that move across multiple
images. Unlike regular stars which stay put, these are temporary events
tracked across a specific sequence of pictures.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrameDetection(BaseModel):
    """A dot of light in one picture, which might be an asteroid."""

    model_config = ConfigDict(populate_by_name=True)

    frame_path: str = Field(alias="framePath")
    timestamp: float = Field(alias="timestamp")
    pixel_x: float = Field(alias="pixelX")
    pixel_y: float = Field(alias="pixelY")
    right_ascension_deg: float = Field(alias="rightAscensionDeg")
    declination_deg: float = Field(alias="declinationDeg")
    flux: float = Field(alias="flux")
    sharpness: float = Field(alias="sharpness")
    photutils_roundness1: float = Field(alias="photutilsRoundness1")


class MovingObjectTrack(BaseModel):
    """The calculated path (speed, direction) of an object across pictures."""

    model_config = ConfigDict(populate_by_name=True)

    right_ascension_rate_arcsec_per_hour: float = Field(alias="rightAscensionRateArcsecPerHour")
    declination_rate_arcsec_per_hour: float = Field(alias="declinationRateArcsecPerHour")
    total_rate_arcsec_per_hour: float = Field(alias="totalRateArcsecPerHour")
    linear_fit_r_squared: float = Field(alias="linearFitRSquared")
    fit_start_timestamp: float = Field(alias="fitStartTimestamp")
    fit_end_timestamp: float = Field(alias="fitEndTimestamp")


class CascadeStage(StrEnum):
    """Tracks how far a possible asteroid made it through our checking process.

    We run several tests to see if a moving dot is really an asteroid.
    This shows if it passed all tests, or at which step it was rejected
    (e.g., it was just a dead pixel).
    """

    MORPHOLOGY_DETECTED = "morphology_detected"
    PERSISTENCE_CONFIRMED = "persistence_confirmed"
    REFERENCE_FRAME_CONFIRMED = "reference_frame_confirmed"
    RATE_LINEARITY_CONFIRMED = "rate_linearity_confirmed"
    EPHEMERIS_MATCHED = "ephemeris_matched"
    REJECTED_SINGLE_FRAME = "rejected_single_frame"
    REJECTED_STATIONARY_SKY = "rejected_stationary_sky"
    REJECTED_STATIONARY_PIXEL = "rejected_stationary_pixel"
    REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE = "rejected_nonlinear_or_out_of_range_rate"


class EphemerisMatch(BaseModel):
    """A match between our detected object and a real, known asteroid.

    We compare our object's speed and location against databases (like SkyBoT)
    that predict where known asteroids should be.
    """

    model_config = ConfigDict(populate_by_name=True)

    provider: str = Field(default="skybot", alias="provider")
    designation: str = Field(alias="designation")
    mpc_number: int | None = Field(default=None, alias="mpcNumber")
    predicted_visual_magnitude: float | None = Field(default=None, alias="predictedVisualMagnitude")
    predicted_right_ascension_rate_arcsec_per_hour: float | None = Field(
        default=None, alias="predictedRightAscensionRateArcsecPerHour"
    )
    predicted_declination_rate_arcsec_per_hour: float | None = Field(
        default=None, alias="predictedDeclinationRateArcsecPerHour"
    )
    angular_separation_arcsec: float = Field(alias="angularSeparationArcsec")


class AsteroidRecoveryCandidate(BaseModel):
    """A potential asteroid we tracked across several pictures.

    It holds all the individual detections, its calculated path, and
    whether it matched any known asteroids.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    target_id: str = Field(alias="targetId")
    frame_detections: list[FrameDetection] = Field(default_factory=list, alias="frameDetections")
    track: MovingObjectTrack | None = Field(default=None, alias="track")
    cascade_stage: CascadeStage = Field(alias="cascadeStage")
    ephemeris_match: EphemerisMatch | None = Field(default=None, alias="ephemerisMatch")
