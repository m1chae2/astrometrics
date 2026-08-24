"""Purpose: Shared quality-summary schemas for every analysis pipeline.

Description: Defines PipelineQualitySummaryBase, the common fields every
per-pipeline quality summary (stacking, astrometry, photometry,
spectroscopy, asteroid recovery) shares: pipeline identity, target/session
provenance, resolved parameters, and flag state. Each pipeline's own summary
class subclasses this and adds a single dedicated field for its
pipeline-specific metrics (e.g. StackQualitySummary.stacking_metrics) --
generic code operating on a PipelineQualitySummaryBase must never reach into
a subclass's pipeline-specific field; metrics needed generically belong on
this base class instead.

Merged from the former targetlib/{pipeline_quality_summary_base,
asteroid_recovery_quality, astrometry_quality, photometry_quality,
spectroscopy_quality, stack_quality}.py -- these were 6 small, tightly
coupled schema files with no reason to stay separate under the
Rubin-aligned layered architecture. stack_quality.py's pure decision
functions (resolve_filter_wfwhm_with_floor, is_stacked_fwhm_degraded,
is_rejected_fraction_significant) moved to
tasks/target_tasks/stack_quality_tasks.py instead -- they're algorithm,
not schema.
"""

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class ExcludedFrame(BaseModel):
    """A single frame excluded from a pipeline run, and why."""

    path: str
    reason: str


class TargetSessionContribution(BaseModel):
    """One TargetSession's contribution of frames to a single pipeline run."""

    session_id: str
    frames_contributed: int
    frames_clipped: int


class PipelineQualitySummaryBase(BaseModel):
    """Fields every per-pipeline quality summary has in common.

    Pipeline-specific metrics live only in each subclass's own dedicated
    field (e.g. StackQualitySummary.stacking_metrics) -- generic code
    operating on a PipelineQualitySummaryBase must never reach into a
    subclass's pipeline-specific field.
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
    """Quality metrics specific to the stacking pipeline.

    Not shared with other pipelines. Standard and spectral stacks populate
    different subsets of the optional fields:
    stacked_fwhm_px/median_input_fwhm_px/fwhm_degraded for standard stacks
    (whole-field FWHM is meaningful there); spectral stacks populate
    spectral_registration_flags instead (zero-order-star-tracking based, see
    tasks/target_tasks/spectral_registration_quality.py) -- a whole-field
    FWHM doesn't capture what matters for a spectral trace.
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

    # Run facts. A stack that timed out or silently debayered a
    # monochrome sensor is a quality event, but both were previously
    # discoverable only by reading Siril's own logs -- on the 2026-08-23
    # run, "[Sun] Stacking timed out after 600 seconds" and an incorrect
    # mono debayer each took a log dig to find. Recorded here so they are
    # queryable alongside every other verdict.
    stacking_duration_seconds: float | None = None
    timed_out: bool = False
    debayer_applied: bool | None = None
    # Registration picks one frame as its reference and aligns the rest
    # to it; if that frame is poor, the whole stack fails. M 42's
    # registration aborted with "Found 0 stars in reference" and nothing
    # recorded which frame that was or how poor it looked.
    registration_reference_frame: str | None = None
    registration_reference_star_count: int | None = None


class StackQualitySummary(PipelineQualitySummaryBase):
    """Persisted per-stack quality record for the stacking pipeline.

    Subclasses PipelineQualitySummaryBase. rejection_sigma_low/high/mode and
    filter_wfwhm_requested/effective/loosened live in the inherited
    resolved_parameters dict rather than as dedicated fields here -- they're
    resolved-parameter values in the sense the shared base defines, not
    stacking-specific quality metrics. upstream_quality_summary_reference is
    always None for stacking: it's the only pipeline with primary,
    non-inherited input quality (see the architecture discussion this
    implements).
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


class AstrometryPipelineQualityMetrics(BaseModel):
    """Astrometry-pipeline-specific quality metrics.

    Not shared with other pipelines.

    catalog_matched_star_count/position_only_star_count/unresolved_star_count
    are a strict breakdown of every star this run identified against:
    matched to SIMBAD or Gaia (a real name), matched to neither but
    still given a stable position-derived id (`FIELD_J...`), or
    matched to neither and never even given a sky position (dropped
    before persistence -- see `pipeline_tasks._drop_unresolved_stars`).
    A high position_only/unresolved rate relative to
    catalog_matched_star_count is worth investigating, though it is
    not proof by itself of spurious detections -- SIMBAD and Gaia are
    both incomplete at the faint end.
    """

    sources_detected: int
    solve_attempted: bool
    plate_solve_succeeded: bool
    simbad_matched_count: int
    astrometric_residual_rms_arcsec: float | None = None
    catalog_matched_star_count: int = 0
    position_only_star_count: int = 0
    unresolved_star_count: int = 0

    # Remote-service health for this run. Without these, a run where
    # every catalog query failed is indistinguishable from one where the
    # field genuinely held no catalog stars -- the difference between a
    # broken service and a real result. On the 2026-08-23 run, Gaia was
    # down for the whole batch and the only evidence was 67 timeout lines
    # in the logs.
    remote_catalog_queries_attempted: int = 0
    remote_catalog_queries_failed: int = 0
    remote_catalog_circuit_breaker_tripped: bool = False
    # Counts every upload, so a solve that needed retries after dropped
    # connections is distinguishable from one that succeeded first try.
    plate_solve_attempts: int = 0


class AstrometryQualitySummary(PipelineQualitySummaryBase):
    """Persisted per-solve quality record.

    Astrometry pipeline's subclass of `PipelineQualitySummaryBase`.

    upstream_quality_summary_reference is always "stacking": astrometry
    always solves Target.stacked_image, never individual frames, so
    stacking's StackQualitySummary is its upstream input quality.
    target_session_ids/target_session_breakdown stay empty: astrometry
    consumes one already-stacked image rather than individual frames, so a
    per-session breakdown isn't meaningful here -- session provenance for
    the underlying stack is reachable via upstream_quality_summary_reference
    instead.
    """

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
    """Per-frame comparison-star ensemble composition for normalization.

    ensemble_size is deliberately per-frame rather than a single run-level
    constant: a saturated comparison star is excluded from the ensemble
    median only in the frames where it's actually saturated, remaining
    eligible in every other frame.
    """

    frame_path: str
    ensemble_size: int
    excluded_comparison_star_ids: list[str] = Field(default_factory=list)


class PhotometryPipelineQualityMetrics(BaseModel):
    """Photometry-pipeline-specific quality metrics.

    Not shared with other pipelines.
    """

    stars_processed: int
    stars_found: int
    frames_processed: int
    rejected_frames: list[ExcludedFrame] = Field(default_factory=list)
    frame_ensemble_composition: list[FrameEnsembleComposition] = Field(default_factory=list)
    # Frames whose comparison ensemble collapsed below
    # `variability_analyzer.MINIMUM_FRAME_ENSEMBLE_SIZE` and were
    # rejected rather than normalized against one or two stars. A high
    # count means the field is too sparse for reliable differential
    # photometry, not that the frames were bad.
    frames_rejected_for_small_ensemble: int = 0
    variable_candidate_count: int
    light_curve_scatter_rms_mag: float | None = None
    cross_session_match_count: int = 0
    sessions_missing_wcs: list[str] = Field(default_factory=list)
    long_term_variable_candidate_count: int = 0
    astrometry_identified_star_count: int = 0
    sessions_with_reused_header_wcs: list[str] = Field(default_factory=list)
    # Sessions whose reused header WCS matched too few catalog stars to be
    # trusted and was replaced by a fresh plate solve; see
    # `session_identification.MIN_CATALOG_MATCH_FRACTION_FOR_REUSED_WCS`.
    # A session listed here was salvaged -- its stars would otherwise have
    # been persisted with sky positions tens of arcsec off.
    sessions_with_replaced_header_wcs: list[str] = Field(default_factory=list)
    # See AstrometryPipelineQualityMetrics's docstring for what these
    # three counts mean; same breakdown, summed across every session.
    catalog_matched_star_count: int = 0
    position_only_star_count: int = 0
    unresolved_star_count: int = 0


class PhotometryQualitySummary(PipelineQualitySummaryBase):
    """Persisted per-run quality record.

    Photometry pipeline's subclass of `PipelineQualitySummaryBase`.

    upstream_quality_summary_reference is always None: photometry runs on
    raw per-session frames rather than a stacked image, so it has no single
    upstream pipeline run to reference (unlike astrometry, which always
    solves one stack).
    """

    pipeline_name: str = "photometry"
    pipeline_version: str = PHOTOMETRY_PIPELINE_VERSION
    photometry_metrics: PhotometryPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Spectroscopy
# ---------------------------------------------------------------------------

# Bumped whenever SpectroscopyPipelineQualityMetrics's shape changes
# meaningfully.
SPECTROSCOPY_PIPELINE_VERSION = "1.1.0"


class SpectroscopyPipelineQualityMetrics(BaseModel):
    """Spectroscopy-pipeline-specific quality metrics.

    Not shared with other pipelines.

    trail_width_profile_available/median_trail_width_px are populated once
    the rung-3 traced-extraction path (SpectrumExtractor.extract_line_traced/
    extract_with_flare_mask_traced) actually runs for at least one star that
    run; they stay at their defaults (False/None) when extraction_method is
    "fixed" (rung 1), since there's no per-position width data to summarize.
    """

    zero_order_saturated_pixel_fraction: float | None = None
    zero_order_saturation_flagged: bool = False
    dispersion_angle_deg: float | None = None
    trail_width_profile_available: bool = False
    median_trail_width_px: float | None = None
    wavelength_calibration_rms_nm: float | None = None
    # See AstrometryPipelineQualityMetrics's docstring for what these
    # three counts mean. Usually a small population here: most of a
    # spectral stack's blind-detected stars only ever get identified
    # via geometric registration against a reference field (when one
    # is available), not independently solved.
    catalog_matched_star_count: int = 0
    position_only_star_count: int = 0
    unresolved_star_count: int = 0


class SpectroscopyQualitySummary(PipelineQualitySummaryBase):
    """Persisted per-extraction quality record.

    Spectroscopy pipeline's subclass of `PipelineQualitySummaryBase`.

    Two distinct pipelines populate this model. The single-stacked-
    frame path (`pipeline_tasks.analyze_target(pipeline_type="spectroscopy")`,
    no `frames=` argument) always extracts from
    `Target.stacked_spectral_target` and defaults
    `upstream_quality_summary_reference` to "stacking", since
    stacking's spectral_registration_flags is the directly relevant
    upstream signal there. The session-grouped, per-frame interactive
    "Analyze Target" path (`AnalysisOrchestrator._run_spectroscopy_analysis`
    via `process_spectroscopy_frames_by_session`) instead extracts
    directly from a target's raw, unstacked frames and sets
    `upstream_quality_summary_reference` to "raw_frames" -- it also
    populates `target_session_ids`/`target_session_breakdown`, which
    the single-stacked-frame path never does (it has no session
    concept by construction).
    """

    pipeline_name: str = "spectroscopy"
    pipeline_version: str = SPECTROSCOPY_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "stacking"
    spectroscopy_metrics: SpectroscopyPipelineQualityMetrics


# ---------------------------------------------------------------------------
# Asteroid recovery
# ---------------------------------------------------------------------------

# Bumped whenever AsteroidRecoveryPipelineQualityMetrics's shape
# changes meaningfully.
ASTEROID_RECOVERY_PIPELINE_VERSION = "1.0.0"


class AsteroidRecoveryPipelineQualityMetrics(BaseModel):
    """Asteroid-recovery-specific quality metrics; not pipeline-shared.

    Each count is a strict funnel: frames_with_wcs_estimate <= total
    light frames; candidates_detected >=
    candidates_persistence_confirmed >=
    candidates_rate_linearity_confirmed >= candidates_ephemeris_matched.
    """

    frames_with_wcs_estimate: int
    frames_excluded_missing_pointing_metadata: int
    candidates_detected: int
    candidates_persistence_confirmed: int
    candidates_rate_linearity_confirmed: int
    candidates_ephemeris_matched: int
    trajectory_fit_residual_rms_arcsec: float | None = None


class AsteroidRecoveryQualitySummary(PipelineQualitySummaryBase):
    """Persisted per-run quality record for the asteroid-recovery pipeline.

    upstream_quality_summary_reference defaults to "astrometry": this
    pipeline's per-frame WCS estimates depend on the stack's
    plate-solve WCS having already succeeded
    (`analyze_target(pipeline_type="astrometry")` must run first).
    """

    pipeline_name: str = "asteroid_recovery"
    pipeline_version: str = ASTEROID_RECOVERY_PIPELINE_VERSION
    upstream_quality_summary_reference: str | None = "astrometry"
    asteroid_recovery_metrics: AsteroidRecoveryPipelineQualityMetrics
