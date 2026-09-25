"""Data structures for tracking the quality and results of the pipelines.

This module defines classes that record how well a processing job (like
stacking images or finding asteroids) performed. It includes a common
base class for information every pipeline shares (like which target was
processed), and specific classes for each pipeline's unique metrics
(like how many stars were found).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ExcludedFrame(BaseModel):
    """A record of a single picture that was skipped, and the reason why."""

    model_config = ConfigDict(populate_by_name=True)

    path: str = Field(alias="path")
    reason: str = Field(alias="reason")


class ExposureGroupSummary(BaseModel):
    """What happened to the frames of one exposure length in a stack.

    Frames taken with different exposure lengths are stacked one length at a
    time (each with the dark frames of its own length) and the results are
    combined. This records, for each length, how it went. A group left out of
    the combined image says why in `left_out_reason`.
    """

    model_config = ConfigDict(populate_by_name=True)

    exposure_seconds: float = Field(alias="exposureSeconds")
    frames_submitted: int = Field(alias="framesSubmitted")
    frames_stacked: int = Field(alias="framesStacked")
    # False when no dark frames of this exposure length exist: the group was
    # stacked without a dark, and its frames are not mixed with darked ones.
    dark_applied: bool = Field(alias="darkApplied")
    # Whether a star is clipped at the camera's ceiling at this exposure
    # length (see `pipelines/stacking/exposure_saturation.py`).
    saturated: bool = Field(default=False, alias="saturated")
    # Whether this group's raw frames are clipped at zero (the sky sits below
    # the camera's read noise), so their faint pixels read too high.
    clipped_at_zero: bool = Field(default=False, alias="clippedAtZero")
    # Where this group's own stack is kept (the ``groups`` folder next to the
    # combined stack), or `None` when the group could not be stacked.
    stack_path: str | None = Field(default=None, alias="stackPath")
    # How far this group's stack was moved to line up with the reference
    # group, as [rows, columns] in pixels; `None` for the reference group.
    alignment_shift_pixels: list[float] | None = Field(default=None, alias="alignmentShiftPixels")
    # Why the group is not in the combined image, or `None` if it is.
    left_out_reason: str | None = Field(default=None, alias="leftOutReason")


class TargetSessionContribution(BaseModel):
    """Tracks how many pictures from a single observing session were used."""

    model_config = ConfigDict(populate_by_name=True)

    session_id: str = Field(alias="sessionId")
    frames_contributed: int = Field(alias="framesContributed")
    frames_clipped: int = Field(alias="framesClipped")


class StarIdentificationMetrics(BaseModel):
    """How many of the stars found in an image could be named.

    Some pipelines look up each star's position against a known catalog
    (a database of stars and their real positions). Each star ends up in
    one of three buckets: matched to a known name, seen but with no
    matching catalog entry, or not resolved at all. Astrometry,
    photometry, and spectroscopy all record this the same way, so it
    lives here once instead of three times.
    """

    model_config = ConfigDict(populate_by_name=True)

    catalog_matched_star_count: int = Field(default=0, alias="catalogMatchedStarCount")
    position_only_star_count: int = Field(default=0, alias="positionOnlyStarCount")
    unresolved_star_count: int = Field(default=0, alias="unresolvedStarCount")


class AppliedCameraProfile(BaseModel):
    """Which camera profile a pipeline run used, and the numbers from it.

    A camera profile holds facts about one camera model (see
    `astrometricslib.models.camera_profile`). Recording it on each summary
    lets a reader see which assumptions a result rests on, in particular
    whether the camera was recognised at all.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The camera name as the frames spell it, or `None` when the run could not
    # tell which camera took its frames.
    camera_name: str | None = Field(default=None, alias="cameraName")
    # The profile that was used. For a camera with no profile of its own this
    # is the generic stand-in.
    profile_name: str = Field(alias="profileName")
    # `True` when the camera has no profile and the generic stand-in was used.
    # Every number below is then an assumption about a made-up camera.
    is_generic_fallback: bool = Field(alias="isGenericFallback")
    # The pixel value at which this camera's frames clip, and where that
    # number came from: "datasheet", "measured" or "assumed".
    clip_ceiling_adu: float = Field(alias="clipCeilingAdu")
    clip_ceiling_source: str = Field(alias="clipCeilingSource")
    # A pixel at or above this value counts as saturated in a raw frame.
    saturation_threshold_adu: float = Field(alias="saturationThresholdAdu")
    saturation_threshold_source: str = Field(alias="saturationThresholdSource")
    # `False` when the threshold is above the ceiling, so a saturated raw frame
    # could never be counted as saturated.
    saturation_threshold_can_be_reached: bool = Field(alias="saturationThresholdCanBeReached")
    # Whether the profile has a sensitivity curve, so spectra can be corrected
    # for the sensor's sensitivity.
    has_quantum_efficiency_curve: bool = Field(alias="hasQuantumEfficiencyCurve")


class PipelineQualitySummaryBase(BaseModel):
    """Basic information recorded by every processing pipeline.

    This includes things like the pipeline's name, the target being processed,
    and any flags indicating potential problems.
    """

    model_config = ConfigDict(populate_by_name=True)

    pipeline_name: str = Field(alias="pipelineName")
    pipeline_version: str = Field(alias="pipelineVersion")
    target_id: str = Field(alias="targetId")
    target_session_ids: list[str] = Field(default_factory=list, alias="targetSessionIds")
    target_session_breakdown: list[TargetSessionContribution] = Field(
        default_factory=list, alias="targetSessionBreakdown"
    )
    # The name of an earlier pipeline this one builds on, if any (for
    # example, astrometry runs after stacking, so its reports point back
    # to "stacking").
    upstream_quality_summary_reference: str | None = Field(
        default=None, alias="upstreamQualitySummaryReference"
    )
    # The actual settings used for this run, after filling in any
    # defaults -- kept so a confusing result can later be traced back to
    # exactly what was configured.
    resolved_parameters: dict[str, Any] = Field(default_factory=dict, alias="resolvedParameters")
    # The camera profile this run used. `None` in summaries saved before
    # camera profiles existed, or when the run has no frames to name a camera.
    camera_profile: AppliedCameraProfile | None = Field(default=None, alias="cameraProfile")
    quality_processing_applied: bool = Field(default=True, alias="qualityProcessingApplied")
    flagged: bool = Field(default=False, alias="flagged")
    flag_reasons: list[str] = Field(default_factory=list, alias="flagReasons")
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------

# Bumped whenever StackingPipelineQualityMetrics's shape changes meaningfully.
STACKING_PIPELINE_VERSION = "1.3.0"


class StackingPipelineQualityMetrics(BaseModel):
    """Measurements recorded when combining (stacking) multiple images.

    This tracks how many images were successfully combined and records details
    like the final image sharpness (FWHM) or if the background was uneven.
    """

    model_config = ConfigDict(populate_by_name=True)

    is_spectral: bool = Field(alias="isSpectral")
    frames_submitted: int = Field(alias="framesSubmitted")
    frames_stacked: int = Field(alias="framesStacked")
    excluded_frames: list[ExcludedFrame] = Field(default_factory=list, alias="excludedFrames")

    rejected_pixel_fraction: float | None = Field(default=None, alias="rejectedPixelFraction")
    rejected_fraction_flagged: bool = Field(default=False, alias="rejectedFractionFlagged")

    background_split_detected: bool = Field(default=False, alias="backgroundSplitDetected")
    background_split_detail: str | None = Field(default=None, alias="backgroundSplitDetail")

    calibration_mismatch_flags: list[str] = Field(default_factory=list, alias="calibrationMismatchFlags")

    saturated_pixel_fraction: float | None = Field(default=None, alias="saturatedPixelFraction")
    saturation_flagged: bool = Field(default=False, alias="saturationFlagged")

    # One entry per exposure length in the stack (a single entry when every
    # frame has the same length).
    exposure_groups: list[ExposureGroupSummary] = Field(default_factory=list, alias="exposureGroups")
    # The exposure length, in seconds, that would keep the brightest star
    # below the camera's ceiling; `None` when it cannot be worked out (for
    # example a star clipped too heavily to estimate).
    recommended_exposure_seconds: float | None = Field(default=None, alias="recommendedExposureSeconds")

    # The share of the stack's pixels that are exactly zero. A stack that is
    # mostly zero has been over-subtracted (its calibration removed more than
    # the sky), and is blank.
    zero_pixel_fraction: float | None = Field(default=None, alias="zeroPixelFraction")
    zero_fraction_flagged: bool = Field(default=False, alias="zeroFractionFlagged")
    # Siril warns when calibration leaves a large share of a frame below zero;
    # this is the worst percentage it reported, or `None` if it did not warn.
    negative_pixel_max_percent: int | None = Field(default=None, alias="negativePixelMaxPercent")
    negative_pixels_flagged: bool = Field(default=False, alias="negativePixelsFlagged")

    # Standard-imaging-only.
    stacked_fwhm_px: float | None = Field(default=None, alias="stackedFwhmPx")
    median_input_fwhm_px: float | None = Field(default=None, alias="medianInputFwhmPx")
    fwhm_degraded: bool = Field(default=False, alias="fwhmDegraded")

    # Spectral-only.
    spectral_registration_flags: list[ExcludedFrame] = Field(
        default_factory=list, alias="spectralRegistrationFlags"
    )

    # Technical details about the stacking run itself, such as whether
    # the process timed out or if color-conversion (debayering) was applied.
    stacking_duration_seconds: float | None = Field(default=None, alias="stackingDurationSeconds")
    timed_out: bool = Field(default=False, alias="timedOut")
    debayer_applied: bool | None = Field(default=None, alias="debayerApplied")
    # To align images, one picture is chosen as the "reference" that
    # all others are matched against. Which picture was chosen is recorded.
    registration_reference_frame: str | None = Field(default=None, alias="registrationReferenceFrame")
    registration_reference_star_count: int | None = Field(
        default=None, alias="registrationReferenceStarCount"
    )


class StackQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an image stacking job.

    It combines the basic pipeline information with the specific stacking
    metrics.
    """

    pipeline_name: str = Field(default="stacking", alias="pipelineName")
    pipeline_version: str = Field(default=STACKING_PIPELINE_VERSION, alias="pipelineVersion")
    stacking_metrics: StackingPipelineQualityMetrics = Field(alias="stackingMetrics")


# ---------------------------------------------------------------------------
# Astrometry
# ---------------------------------------------------------------------------

# Bumped whenever AstrometryPipelineQualityMetrics's shape changes
# meaningfully.
ASTROMETRY_PIPELINE_VERSION = "1.3.0"


class AstrometryPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when figuring out where an image is pointing.

    This tracks how many stars were found and whether the image's
    coordinates could be successfully calculated ("plate solving" --
    matching the stars in the picture to a star map to figure out
    exactly where the telescope was pointed).
    """

    sources_detected: int = Field(alias="sourcesDetected")
    solve_attempted: bool = Field(alias="solveAttempted")
    plate_solve_succeeded: bool = Field(alias="plateSolveSucceeded")
    # SIMBAD and Gaia are online databases of stars and their real
    # positions, used to double-check what's in the picture.
    simbad_matched_count: int = Field(alias="simbadMatchedCount")
    # How far off, on average, the calculated coordinates were from the
    # true star positions, in arcseconds ("RMS" is a standard way to
    # average errors so they don't cancel out). Lower is better.
    astrometric_residual_rms_arcsec: float | None = Field(default=None, alias="astrometricResidualRmsArcsec")

    # Tracks whether there were connection issues when trying to look up
    # star names in online databases (like SIMBAD or Gaia).
    remote_catalog_queries_attempted: int = Field(default=0, alias="remoteCatalogQueriesAttempted")
    remote_catalog_queries_failed: int = Field(default=0, alias="remoteCatalogQueriesFailed")
    # True if too many of those lookups failed in a row, so the code
    # stopped trying for a while instead of repeatedly waiting on a
    # database that seems to be down.
    remote_catalog_circuit_breaker_tripped: bool = Field(
        default=False, alias="remoteCatalogCircuitBreakerTripped"
    )
    # The number of times coordinate calculation was attempted for this image.
    plate_solve_attempts: int = Field(default=0, alias="plateSolveAttempts")


class AstrometryQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an astrometry (coordinate-finding) job."""

    pipeline_name: str = Field(default="astrometry", alias="pipelineName")
    pipeline_version: str = Field(default=ASTROMETRY_PIPELINE_VERSION, alias="pipelineVersion")
    upstream_quality_summary_reference: str | None = Field(
        default="stacking", alias="upstreamQualitySummaryReference"
    )
    astrometry_metrics: AstrometryPipelineQualityMetrics = Field(alias="astrometryMetrics")


# ---------------------------------------------------------------------------
# Photometry
# ---------------------------------------------------------------------------

# Bumped whenever PhotometryPipelineQualityMetrics's shape changes
# meaningfully.
PHOTOMETRY_PIPELINE_VERSION = "1.2.0"


class FrameEnsembleComposition(BaseModel):
    """Tracks which comparison stars a picture's brightness was measured with.

    To tell if a star got brighter or dimmer, its light is compared
    against a group of other, steady stars in the same picture (called
    the "ensemble"). This records which stars were in that group.
    """

    model_config = ConfigDict(populate_by_name=True)

    frame_path: str = Field(alias="framePath")
    ensemble_size: int = Field(alias="ensembleSize")
    excluded_comparison_star_ids: list[str] = Field(default_factory=list, alias="excludedComparisonStarIds")


class PhotometryPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when measuring the brightness of stars.

    This tracks how many stars were processed and if any variable stars
    were found.
    """

    stars_processed: int = Field(alias="starsProcessed")
    stars_found: int = Field(alias="starsFound")
    frames_processed: int = Field(alias="framesProcessed")
    rejected_frames: list[ExcludedFrame] = Field(default_factory=list, alias="rejectedFrames")
    frame_ensemble_composition: list[FrameEnsembleComposition] = Field(
        default_factory=list, alias="frameEnsembleComposition"
    )
    variable_candidate_count: int = Field(alias="variableCandidateCount")
    # How steady a star's measured brightness was, night to night, for
    # stars that turned out NOT to be variable ("RMS" averages the
    # ups and downs into one number). A high number here means the
    # measurements themselves are noisy, not that the star is
    # actually changing.
    light_curve_scatter_rms_mag: float | None = Field(default=None, alias="lightCurveScatterRmsMag")
    cross_session_match_count: int = Field(default=0, alias="crossSessionMatchCount")
    # "WCS" (World Coordinate System) is the map, stored in a picture's
    # file, from its pixels to real sky coordinates. These three lists
    # track sessions where that map was missing, already correct and
    # reused as-is, or wrong and had to be recalculated.
    sessions_missing_wcs: list[str] = Field(default_factory=list, alias="sessionsMissingWcs")
    long_term_variable_candidate_count: int = Field(default=0, alias="longTermVariableCandidateCount")
    astrometry_identified_star_count: int = Field(default=0, alias="astrometryIdentifiedStarCount")
    sessions_with_reused_header_wcs: list[str] = Field(
        default_factory=list, alias="sessionsWithReusedHeaderWcs"
    )
    sessions_with_replaced_header_wcs: list[str] = Field(
        default_factory=list, alias="sessionsWithReplacedHeaderWcs"
    )


class PhotometryQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for a photometry (brightness-measuring) job."""

    pipeline_name: str = Field(default="photometry", alias="pipelineName")
    pipeline_version: str = Field(default=PHOTOMETRY_PIPELINE_VERSION, alias="pipelineVersion")
    photometry_metrics: PhotometryPipelineQualityMetrics = Field(alias="photometryMetrics")


# ---------------------------------------------------------------------------
# Spectroscopy
# ---------------------------------------------------------------------------

# Bumped whenever SpectroscopyPipelineQualityMetrics's shape changes
# meaningfully.
SPECTROSCOPY_PIPELINE_VERSION = "1.3.0"


class SpectralClassificationConcern(BaseModel):
    """One star whose self-determined spectral type shouldn't be trusted as-is.

    Names exactly which stars a spectroscopy run's own classification is
    shaky for, and why, rather than only reporting how many -- so a user
    building a personal catalog from self-determined types knows which
    entries to double-check instead of taking every one at face value.
    """

    model_config = ConfigDict(populate_by_name=True)

    star_id: str = Field(alias="starId")
    # "low_confidence", "ambiguous", or both joined by a comma -- see
    # spectral_classifier.is_classification_low_confidence and
    # .is_classification_ambiguous for what each means.
    reason: str = Field(alias="reason")
    spectral_type: str = Field(alias="spectralType")
    confidence: float | None = Field(default=None, alias="confidence")


class SpectroscopyPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when analyzing a star's light spectrum.

    A spectroscope splits a star's light into a rainbow-like streak (the
    "trail") so its colors can be measured. This class tracks details
    about that streak, like how wide it is and whether any part of it
    was too bright (saturated).
    """

    # The "zero order" is the star's plain, undispersed image that shows
    # up alongside the rainbow streak -- it's much brighter, so it's
    # checked separately for overexposure.
    zero_order_saturated_pixel_fraction: float | None = Field(
        default=None, alias="zeroOrderSaturatedPixelFraction"
    )
    zero_order_saturation_flagged: bool = Field(default=False, alias="zeroOrderSaturationFlagged")
    # The angle, in degrees, that the rainbow streak is tilted at in
    # the picture.
    dispersion_angle_deg: float | None = Field(default=None, alias="dispersionAngleDeg")
    trail_width_profile_available: bool = Field(default=False, alias="trailWidthProfileAvailable")
    median_trail_width_px: float | None = Field(default=None, alias="medianTrailWidthPx")
    # How many classified stars had a winning correlation too weak to
    # trust, or a top-two-type near-tie -- see SpectralClassificationConcern.
    low_confidence_classification_count: int = Field(default=0, alias="lowConfidenceClassificationCount")
    ambiguous_classification_count: int = Field(default=0, alias="ambiguousClassificationCount")
    flagged_spectral_classifications: list[SpectralClassificationConcern] = Field(
        default_factory=list, alias="flaggedSpectralClassifications"
    )


class SpectroscopyQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for a spectroscopy (light-spectrum) job."""

    pipeline_name: str = Field(default="spectroscopy", alias="pipelineName")
    pipeline_version: str = Field(default=SPECTROSCOPY_PIPELINE_VERSION, alias="pipelineVersion")
    upstream_quality_summary_reference: str | None = Field(
        default="stacking", alias="upstreamQualitySummaryReference"
    )
    spectroscopy_metrics: SpectroscopyPipelineQualityMetrics = Field(alias="spectroscopyMetrics")


# ---------------------------------------------------------------------------
# Asteroid detection
# ---------------------------------------------------------------------------

# Bumped whenever AsteroidDetectionPipelineQualityMetrics's shape
# changes meaningfully.
ASTEROID_DETECTION_PIPELINE_VERSION = "1.1.0"


class AsteroidDetectionPipelineQualityMetrics(BaseModel):
    """Measurements for the process that searches for moving asteroids.

    This tracks how many candidates were found and how many passed each
    successive check (e.g., did it move in a straight line? did it match a
    known asteroid?).
    """

    model_config = ConfigDict(populate_by_name=True)

    # "WCS" (World Coordinate System) is the map from a picture's pixels
    # to real sky coordinates. A frame needs one before it can be
    # searched for moving objects.
    frames_with_wcs_estimate: int = Field(alias="framesWithWcsEstimate")
    frames_excluded_missing_pointing_metadata: int = Field(alias="framesExcludedMissingPointingMetadata")
    candidates_detected: int = Field(alias="candidatesDetected")
    candidates_persistence_confirmed: int = Field(alias="candidatesPersistenceConfirmed")
    # How many candidates moved at a steady speed in a straight line
    # across the pictures, the way a real asteroid would (as opposed to
    # a camera glitch or a cosmic ray hit).
    candidates_rate_linearity_confirmed: int = Field(alias="candidatesRateLinearityConfirmed")
    # How many candidates were matched to a real, already-known asteroid
    # by checking a database of predicted asteroid positions
    # ("ephemeris" means a table of where something will be over time).
    candidates_ephemeris_matched: int = Field(alias="candidatesEphemerisMatched")


class AsteroidDetectionQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an asteroid-hunting job."""

    pipeline_name: str = Field(default="asteroid_detection", alias="pipelineName")
    pipeline_version: str = Field(default=ASTEROID_DETECTION_PIPELINE_VERSION, alias="pipelineVersion")
    upstream_quality_summary_reference: str | None = Field(
        default="astrometry", alias="upstreamQualitySummaryReference"
    )
    asteroid_detection_metrics: AsteroidDetectionPipelineQualityMetrics = Field(
        alias="asteroidDetectionMetrics"
    )
