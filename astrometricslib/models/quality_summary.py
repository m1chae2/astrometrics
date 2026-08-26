"""Data structures for tracking the quality and results of our pipelines.

This module defines classes that record how well a processing job (like
stacking
images or finding asteroids) performed. It includes a common base class for
information every pipeline shares (like which target was processed), and
specific
classes for each pipeline's unique metrics (like how many stars were found).
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ExcludedFrame(BaseModel):
    """A record of a single picture that was skipped, and the reason why."""

    path: str
    reason: str


class TargetSessionContribution(BaseModel):
    """Tracks how many pictures from a single observing session were used."""

    session_id: str
    frames_contributed: int
    frames_clipped: int


class StarIdentificationMetrics(BaseModel):
    """How many of the stars found in an image could be named.

    Some pipelines look up each star's position against a known catalog.
    A star can end up in one of three buckets: matched to a name we
    already know, seen but with no matching catalog entry, or not
    resolved at all. Astrometry, photometry, and spectroscopy all record
    this the same way, so it lives here once instead of three times.
    """

    catalog_matched_star_count: int = 0
    position_only_star_count: int = 0
    unresolved_star_count: int = 0


class PipelineQualitySummaryBase(BaseModel):
    """Basic information recorded by every processing pipeline.

    This includes things like the pipeline's name, the target being processed,
    and any flags indicating potential problems.
    """

    pipeline_name: str
    pipeline_version: str
    target_id: str
    target_session_ids: list[str] = Field(default_factory=list)
    target_session_breakdown: list[TargetSessionContribution] = Field(default_factory=list)
    upstream_quality_summary_reference: str | None = None
    resolved_parameters: dict[str, Any] = Field(default_factory=dict)
    quality_processing_applied: bool = True
    flagged: bool = False
    flag_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------

# Bumped whenever StackingPipelineQualityMetrics's shape changes meaningfully.
STACKING_PIPELINE_VERSION = "1.1.0"


class StackingPipelineQualityMetrics(BaseModel):
    """Measurements recorded when combining (stacking) multiple images.

    This tracks how many images were successfully combined and records details
    like the final image sharpness (FWHM) or if the background was uneven.
    """

    is_spectral: bool
    frames_submitted: int
    frames_stacked: int
    excluded_frames: list[ExcludedFrame] = Field(default_factory=list)

    rejected_pixel_fraction: float | None = None
    rejected_fraction_flagged: bool = False

    background_split_detected: bool = False
    background_split_detail: str | None = None

    calibration_mismatch_flags: list[str] = Field(default_factory=list)

    saturated_pixel_fraction: float | None = None
    saturation_flagged: bool = False

    # Standard-imaging-only.
    stacked_fwhm_px: float | None = None
    median_input_fwhm_px: float | None = None
    fwhm_degraded: bool = False

    # Spectral-only.
    spectral_registration_flags: list[ExcludedFrame] = Field(default_factory=list)

    # Technical details about the stacking run itself, such as whether
    # the process timed out or if color-conversion (debayering) was applied.
    stacking_duration_seconds: float | None = None
    timed_out: bool = False
    debayer_applied: bool | None = None
    # To align images, one picture is chosen as the "reference" that
    # all others are matched against. We record which picture was chosen.
    registration_reference_frame: str | None = None
    registration_reference_star_count: int | None = None


class StackQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an image stacking job.

    It combines the basic pipeline information with the specific stacking
    metrics.
    """

    pipeline_name: str = "stacking"
    pipeline_version: str = STACKING_PIPELINE_VERSION
    stacking_metrics: StackingPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Astrometry
# ---------------------------------------------------------------------------

# Bumped whenever AstrometryPipelineQualityMetrics's shape changes
# meaningfully.
ASTROMETRY_PIPELINE_VERSION = "1.2.0"


class AstrometryPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when figuring out where an image is pointing.

    This tracks how many stars were found and whether the image's coordinates
    could be successfully calculated (plate solving).
    """

    sources_detected: int
    solve_attempted: bool
    plate_solve_succeeded: bool
    simbad_matched_count: int
    astrometric_residual_rms_arcsec: float | None = None

    # Tracks whether we had connection issues when trying to look up
    # star names in online databases (like SIMBAD or Gaia).
    remote_catalog_queries_attempted: int = 0
    remote_catalog_queries_failed: int = 0
    remote_catalog_circuit_breaker_tripped: bool = False
    # The number of times we tried to calculate coordinates for this image.
    plate_solve_attempts: int = 0


class AstrometryQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an astrometry (coordinate-finding) job."""

    pipeline_name: str = "astrometry"
    pipeline_version: str = ASTROMETRY_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "stacking"
    astrometry_metrics: AstrometryPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Photometry
# ---------------------------------------------------------------------------

# Bumped whenever PhotometryPipelineQualityMetrics's shape changes
# meaningfully.
PHOTOMETRY_PIPELINE_VERSION = "1.1.0"


class FrameEnsembleComposition(BaseModel):
    """Tracks which known stars were the brightness reference used."""

    frame_path: str
    ensemble_size: int
    excluded_comparison_star_ids: list[str] = Field(default_factory=list)


class PhotometryPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when measuring the brightness of stars.

    This tracks how many stars were processed and if any variable stars
    were found.
    """

    stars_processed: int
    stars_found: int
    frames_processed: int
    rejected_frames: list[ExcludedFrame] = Field(default_factory=list)
    frame_ensemble_composition: list[FrameEnsembleComposition] = Field(default_factory=list)
    variable_candidate_count: int
    light_curve_scatter_rms_mag: float | None = None
    cross_session_match_count: int = 0
    sessions_missing_wcs: list[str] = Field(default_factory=list)
    long_term_variable_candidate_count: int = 0
    astrometry_identified_star_count: int = 0
    sessions_with_reused_header_wcs: list[str] = Field(default_factory=list)
    # Lists observing sessions where the image coordinates saved in the file
    # were bad and had to be recalculated from scratch.
    sessions_with_replaced_header_wcs: list[str] = Field(default_factory=list)


class PhotometryQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for a photometry (brightness-measuring) job."""

    pipeline_name: str = "photometry"
    pipeline_version: str = PHOTOMETRY_PIPELINE_VERSION
    photometry_metrics: PhotometryPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Spectroscopy
# ---------------------------------------------------------------------------

# Bumped whenever SpectroscopyPipelineQualityMetrics's shape changes
# meaningfully.
SPECTROSCOPY_PIPELINE_VERSION = "1.1.0"


class SpectroscopyPipelineQualityMetrics(StarIdentificationMetrics):
    """Measurements recorded when analyzing a star's light spectrum.

    This tracks details about the spectral lines, like how wide they are
    and whether any parts of the spectrum were too bright (saturated).
    """

    zero_order_saturated_pixel_fraction: float | None = None
    zero_order_saturation_flagged: bool = False
    dispersion_angle_deg: float | None = None
    trail_width_profile_available: bool = False
    median_trail_width_px: float | None = None
    wavelength_calibration_rms_nm: float | None = None


class SpectroscopyQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for a spectroscopy (light-spectrum) job."""

    pipeline_name: str = "spectroscopy"
    pipeline_version: str = SPECTROSCOPY_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "stacking"
    spectroscopy_metrics: SpectroscopyPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Tracking
# ---------------------------------------------------------------------------

# Bumped whenever TrackingPipelineQualityMetrics's shape changes
# meaningfully.
TRACKING_PIPELINE_VERSION = "1.0.0"


class TrackingPipelineQualityMetrics(BaseModel):
    """Measurements that describe how well the telescope tracked the sky.

    This looks for problems with the telescope mount (like drifting) or
    changes in the sky conditions (like the background getting brighter).
    It records the worst-case values across all observing sessions.
    """

    sessions_found: int
    sessions_analyzed: int
    usable_frames: int
    span_hours: float | None = None

    drift_rate_x_px_per_hour: float | None = None
    drift_rate_y_px_per_hour: float | None = None
    max_excursion_px: float | None = None
    meridian_flips: int = 0

    # Records if the telescope had a repeating tracking error (like a
    # gear wobbling every 60 seconds).
    periodic_error_period_seconds: int | None = None
    periodic_error_strength: float = 0.0
    periodic_error_corroborated: bool = False

    trailed_frame_count: int = 0
    median_fwhm_px: float | None = None
    fwhm_spread_px: float | None = None
    median_roundness: float | None = None
    median_background: float | None = None
    background_spread: float | None = None


class TrackingQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for a telescope tracking analysis job."""

    pipeline_name: str = "tracking"
    pipeline_version: str = TRACKING_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "stacking"
    tracking_metrics: TrackingPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Asteroid recovery
# ---------------------------------------------------------------------------

# Bumped whenever AsteroidRecoveryPipelineQualityMetrics's shape
# changes meaningfully.
ASTEROID_RECOVERY_PIPELINE_VERSION = "1.0.0"


class AsteroidRecoveryPipelineQualityMetrics(BaseModel):
    """Measurements for the process that searches for moving asteroids.

    This tracks how many candidates were found and how many passed each
    successive check (e.g., did it move in a straight line? did it match a
    known asteroid?).
    """

    frames_with_wcs_estimate: int
    frames_excluded_missing_pointing_metadata: int
    candidates_detected: int
    candidates_persistence_confirmed: int
    candidates_rate_linearity_confirmed: int
    candidates_ephemeris_matched: int
    trajectory_fit_residual_rms_arcsec: float | None = None


class AsteroidRecoveryQualitySummary(PipelineQualitySummaryBase):
    """The final saved report for an asteroid-hunting job."""

    pipeline_name: str = "asteroid_recovery"
    pipeline_version: str = ASTEROID_RECOVERY_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "astrometry"
    asteroid_recovery_metrics: AsteroidRecoveryPipelineQualityMetrics
