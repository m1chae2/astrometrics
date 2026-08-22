"""Description: Asteroid-recovery pipeline domain schema definitions.

Models: FrameDetection, MovingObjectTrack, CascadeStage, EphemerisMatch,
AsteroidRecoveryCandidate. A moving-object candidate is a transient,
per-target-per-epoch event, not a stable catalog entity like
`StellarObject` -- kept as a small, tightly-coupled Pydantic cluster in
its own module rather than folded into `stellar_source.py`.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class FrameDetection(BaseModel):
    """A single point-source detection on one individual frame.

    En route to being chained into a moving-object candidate track.
    """

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
    """A fitted linear sky-motion track across a chain of frame detections."""

    model_config = ConfigDict(populate_by_name=True)

    right_ascension_rate_arcsec_per_hour: float = Field(alias="rightAscensionRateArcsecPerHour")
    declination_rate_arcsec_per_hour: float = Field(alias="declinationRateArcsecPerHour")
    total_rate_arcsec_per_hour: float = Field(alias="totalRateArcsecPerHour")
    linear_fit_r_squared: float = Field(alias="linearFitRSquared")
    fit_start_timestamp: float = Field(alias="fitStartTimestamp")
    fit_end_timestamp: float = Field(alias="fitEndTimestamp")


class CascadeStage(StrEnum):
    """The discrimination-cascade stage an `AsteroidRecoveryCandidate` reached.

    Ordered stages (a real mover progresses through these in sequence)
    are followed by the rejection stages (a candidate exits the
    cascade at exactly one of these).
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
    """A cross-match against a known solar-system body's predicted motion.

    From a SkyBoT (or similar) ephemeris query, matched against an
    `AsteroidRecoveryCandidate`.
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
    """A candidate moving object tracked across a target's frames.

    Also records how far it progressed through the discrimination
    cascade.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str = Field(alias="id")
    target_id: str = Field(alias="targetId")
    frame_detections: list[FrameDetection] = Field(default_factory=list, alias="frameDetections")
    track: MovingObjectTrack | None = Field(default=None, alias="track")
    cascade_stage: CascadeStage = Field(alias="cascadeStage")
    ephemeris_match: EphemerisMatch | None = Field(default=None, alias="ephemerisMatch")
