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
    # How bright this dot looked in this one picture (astronomers call
    # this "flux"). `None` if brightness wasn't measured. On its own
    # this isn't enough to notice a rotating asteroid's brightness
    # rising and falling -- passing clouds or haze changing between
    # pictures would look the same. See picture_brightness_level below.
    brightness: float | None = Field(default=None, alias="brightness")
    # A typical brightness level for this picture, found by looking at
    # every dot detected in it (not just this one). Dividing this dot's
    # own brightness by this number cancels out sky changes (clouds,
    # haze, moonlight) from one picture to the next, so what's left is
    # mostly just this object's own brightness changing.
    picture_brightness_level: float | None = Field(default=None, alias="pictureBrightnessLevel")
    # How far, in arcseconds, this picture's star positions scatter around
    # the reference stars after the pointing correction. It is the typical
    # error of one position in this picture. `None` if it could not be
    # measured (too few matched stars), in which case the straight-line
    # test falls back on an assumed value from the settings.
    astrometric_error_arcsec: float | None = Field(default=None, alias="astrometricErrorArcsec")
    # How long the shutter was open for this picture, in seconds (the
    # `EXPTIME` header). `timestamp` is the moment the exposure started, so
    # the middle of the exposure is `timestamp + exposure_seconds / 2`.
    # `None` if the header had no usable value.
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")


class MovingObjectTrack(BaseModel):
    """The calculated path (speed, direction) of an object across pictures."""

    model_config = ConfigDict(populate_by_name=True)

    right_ascension_rate_arcsec_per_hour: float = Field(alias="rightAscensionRateArcsecPerHour")
    declination_rate_arcsec_per_hour: float = Field(alias="declinationRateArcsecPerHour")
    total_rate_arcsec_per_hour: float = Field(alias="totalRateArcsecPerHour")
    # How well a straight line fits the object's positions over time,
    # from 0 (no fit at all) to 1 (a perfect straight line). A real
    # asteroid should fit close to 1.
    # This is reported for information only. Any chain with a large
    # displacement scores close to 1, so it no longer decides anything.
    linear_fit_r_squared: float = Field(alias="linearFitRSquared")
    # How far the positions miss the fitted line, as a root-mean-square
    # (RMS) in arcseconds, for each sky axis. These decide the straight-line
    # test. `None` on records saved before this check existed.
    residual_rms_right_ascension_arcsec: float | None = Field(
        default=None, alias="residualRmsRightAscensionArcsec"
    )
    residual_rms_declination_arcsec: float | None = Field(default=None, alias="residualRmsDeclinationArcsec")
    # The error of one position that the residuals were judged against, and
    # the largest RMS the chain was allowed. `astrometric_error_assumed` is
    # true when at least one picture had no measured error and the assumed
    # value from the settings was used for it.
    astrometric_error_arcsec: float | None = Field(default=None, alias="astrometricErrorArcsec")
    residual_limit_arcsec: float | None = Field(default=None, alias="residualLimitArcsec")
    astrometric_error_assumed: bool | None = Field(default=None, alias="astrometricErrorAssumed")
    fit_start_timestamp: float = Field(alias="fitStartTimestamp")
    fit_end_timestamp: float = Field(alias="fitEndTimestamp")


class CascadeStage(StrEnum):
    """Tracks how far a possible asteroid made it through the checking process.

    Several tests are run to see if a moving dot is really an asteroid.
    This shows if it passed all tests, or at which step it was rejected
    (e.g., it was just a dead pixel).
    """

    REFERENCE_FRAME_CONFIRMED = "reference_frame_confirmed"
    RATE_LINEARITY_CONFIRMED = "rate_linearity_confirmed"
    EPHEMERIS_MATCHED = "ephemeris_matched"
    REJECTED_SINGLE_FRAME = "rejected_single_frame"
    REJECTED_STATIONARY_SKY = "rejected_stationary_sky"
    REJECTED_STATIONARY_PIXEL = "rejected_stationary_pixel"
    REJECTED_NONLINEAR_OR_OUT_OF_RANGE_RATE = "rejected_nonlinear_or_out_of_range_rate"


class EphemerisMatch(BaseModel):
    """A match between the detected object and a real, known asteroid.

    The object's speed and location are compared against databases
    (like SkyBoT) that predict where known asteroids should be.
    """

    model_config = ConfigDict(populate_by_name=True)

    designation: str = Field(alias="designation")
    # How far apart, in arcseconds, the object we found appears from the
    # known asteroid's predicted position. A smaller number is a
    # closer, more confident match.
    angular_separation_arcsec: float = Field(alias="angularSeparationArcsec")
    # The same separation measured at the first and at the last detection of
    # the chain, each against the known asteroid's position at that
    # detection's own time. Both must be inside the match radius.
    # `angular_separation_arcsec` is the larger of the two. `None` on records
    # saved before the database was asked once per end of the chain.
    first_detection_separation_arcsec: float | None = Field(
        default=None, alias="firstDetectionSeparationArcsec"
    )
    last_detection_separation_arcsec: float | None = Field(
        default=None, alias="lastDetectionSeparationArcsec"
    )


class AsteroidDetectionCandidate(BaseModel):
    """A potential asteroid tracked across several pictures.

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
