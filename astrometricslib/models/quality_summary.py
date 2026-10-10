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

from astrometricslib.models.gaia_xp_comparison import GaiaXpRunSummary
from astrometricslib.models.gate_result import GateResult, GateStatus
from astrometricslib.models.spectroscopy_quality import StageQualityRollup
from astrometricslib.models.stacking_quality import StackingInputQuality, StackingOutputQuality
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary


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
    # length (see `pipelines/stacking/post_processing/exposure_saturation.py`).
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
    # How many times brighter (per second) this group reads than the reference
    # group, measured on the reference's mid-range pixels (20th to 80th
    # percentile of brightness). This is the scale applied to the group. It is
    # a ratio of two counts-per-second values, so it has no unit. `None` for
    # the reference group's own entry and when the ratio could not be measured.
    gain_mid_range: float | None = Field(default=None, alias="gainMidRange")
    # The same ratio measured on the brightest 1% of the shared pixels. It is
    # never applied. It differs from `gain_mid_range` when the bright end of
    # the group is compressed (near full well) or clipped. No unit.
    gain_bright_end_ratio: float | None = Field(default=None, alias="gainBrightEndRatio")
    # abs(bright end ratio - mid-range gain) / mid-range gain, as a fraction
    # (0.05 is 5%). `None` when either ratio could not be measured.
    gain_disagreement: float | None = Field(default=None, alias="gainDisagreement")
    # True when `gain_disagreement` is above the configured tolerance
    # (`exposure_group_gain_tolerance`). The group is then left out of the
    # combined image and `left_out_reason` names both ratios.
    gain_nonlinear: bool = Field(default=False, alias="gainNonlinear")
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
    # One record per quality check that this pipeline has moved onto the gate
    # record (see `gate_result.py`). Unlike `flag_reasons`, it also shows
    # the checks that passed and the ones that could not run. Empty on a
    # summary saved before gates were recorded, and for checks a pipeline
    # has not yet moved over, which still report only through `flag_reasons`.
    gates: list[GateResult] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="createdAt")
    # The id of the IVOA provenance Activity (see
    # astrometricslib.models.provenance) this run was recorded as --
    # always the same id as the run's job_id. `None` when no job was
    # open for this run (register_job=False), matching the job log's
    # own behavior of recording nothing in that case.
    provenance_activity_id: str | None = Field(default=None, alias="provenanceActivityId")
    # The id of the upstream provenance Entity this run actually
    # consumed (for example the specific stacked image astrometry
    # solved), superseding upstream_quality_summary_reference's plain
    # category name with a real, resolvable reference. `None` when this
    # run has no upstream entity (for example stacking itself, or a run
    # with no provenance recorded).
    upstream_entity_id: str | None = Field(default=None, alias="upstreamEntityId")

    def record_gate(self, result: GateResult) -> None:
        """Add one gate's result, keeping `flagged` and `flag_reasons` in step.

        A failed gate flags the run and adds its sentence to `flag_reasons`
        (once, even if the same sentence is already there). A passed gate
        and a gate that was not checked are only recorded; they never flag
        the run. A second result with the same name replaces the first, so
        re-running a check does not leave two answers.

        Parameters
        ----------
        result : `GateResult`
            The gate's result.
        """
        self.gates = [existing for existing in self.gates if existing.name != result.name]
        self.gates.append(result)
        if result.status is GateStatus.FAILED:
            self.flagged = True
            if result.detail and result.detail not in self.flag_reasons:
                self.flag_reasons.append(result.detail)

    def gate(self, name: str) -> GateResult | None:
        """Look up a recorded gate by name.

        Parameters
        ----------
        name : `str`
            The gate's name.

        Returns
        -------
        result : `GateResult` or `None`
            The gate's result, or `None` if no gate of that name was recorded.
        """
        return next((result for result in self.gates if result.name == name), None)


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

    # The flat frames the stack was calibrated with. The master flat's noise
    # is copied into every light frame, so a set with few or faint frames
    # leaves a fixed noise pattern in the stack. `flat_noise_fraction` is the
    # master flat's relative noise (0.005 is 0.5%). When it is above the
    # limit, the master flat is smoothed by `flat_smoothing_sigma_px` pixels
    # (a Gaussian width). `flat_calibration_issues` states each problem found
    # in one sentence. All are empty or `None` when the stack used no flats.
    flat_frame_count: int | None = Field(default=None, alias="flatFrameCount")
    flat_noise_fraction: float | None = Field(default=None, alias="flatNoiseFraction")
    flat_smoothing_sigma_px: float | None = Field(default=None, alias="flatSmoothingSigmaPx")
    flat_calibration_issues: list[str] = Field(default_factory=list, alias="flatCalibrationIssues")

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
    # The star width the stack should have given its input frames: the root
    # mean square of a sample of their widths. The degradation flag compares
    # the stack with this, not with the median.
    expected_stack_fwhm_px: float | None = Field(default=None, alias="expectedStackFwhmPx")
    fwhm_degraded: bool = Field(default=False, alias="fwhmDegraded")

    # Spectral-only.
    spectral_registration_flags: list[ExcludedFrame] = Field(
        default_factory=list, alias="spectralRegistrationFlags"
    )
    # Whether the spectral registration check actually ran. An empty flag
    # list means "all clear" only when this is true: the check is skipped
    # when its three per-frame lists do not line up.
    spectral_registration_checked: bool = Field(default=False, alias="spectralRegistrationChecked")

    # Technical details about the stacking run itself, such as whether
    # the process timed out or if color-conversion (debayering) was applied.
    stacking_duration_seconds: float | None = Field(default=None, alias="stackingDurationSeconds")
    timed_out: bool = Field(default=False, alias="timedOut")
    debayer_applied: bool | None = Field(default=None, alias="debayerApplied")
    # The program that did the pixel work (calibration, registration and
    # combination) and its version, so a stack can be traced to the tool that
    # made it. `None` on a summary saved before they were recorded.
    stacking_engine: str | None = Field(default=None, alias="stackingEngine")
    stacking_engine_version: str | None = Field(default=None, alias="stackingEngineVersion")
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
    # The same judgement split in two: was the input sound, and did the
    # stack come out well. Both are `None` on a summary saved before they
    # existed.
    input_quality: StackingInputQuality | None = Field(default=None, alias="inputQuality")
    output_quality: StackingOutputQuality | None = Field(default=None, alias="outputQuality")


# ---------------------------------------------------------------------------
# Astrometry
# ---------------------------------------------------------------------------

# Bumped whenever AstrometryPipelineQualityMetrics's shape changes
# meaningfully. 1.4.0: the residual gate judges the plate solver's own fit
# when it reports one, Gaia positions carry proper motion to the observation
# epoch, and the scale hint uses binning.
ASTROMETRY_PIPELINE_VERSION = "1.4.0"


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
    # The root mean square (RMS) of the distance, in arcseconds, between each
    # detected star and the nearest SIMBAD or Gaia star within the match
    # radius. This is the catalog match separation, not the plate-solve fit
    # residual: it ignores any star farther than the radius and includes
    # wrong matches. The field keeps its older name for saved summaries and
    # always holds the same value as `catalog_match_separation_rms_arcsec`.
    astrometric_residual_rms_arcsec: float | None = Field(default=None, alias="astrometricResidualRmsArcsec")
    # The same catalog match separation RMS, under a name that says what it
    # is. The residual gate uses it only when the plate solver gave no fit
    # residual of its own. `None` when no star was matched to a catalog.
    catalog_match_separation_rms_arcsec: float | None = Field(
        default=None, alias="catalogMatchSeparationRmsArcsec"
    )
    # The plate solver's own fit residual, in arcseconds: the RMS distance
    # between each matched field star's fitted position and its reference
    # star's position. This is the number the residual gate judges. `None`
    # when the solver reported none (an online solve, or a missing match
    # table).
    plate_solve_fit_residual_rms_arcsec: float | None = Field(
        default=None, alias="plateSolveFitResidualRmsArcsec"
    )
    # How many stars the plate solver matched to its reference stars when it
    # fitted the solution. `None` when the solver reported none.
    plate_solve_matched_star_count: int | None = Field(default=None, alias="plateSolveMatchedStarCount")
    # Short stable keys for conditions that limit how far the results can be
    # trusted: ``gaia_row_limit_reached`` (the Gaia search returned as many
    # rows as it may, so faint stars may be missing),
    # ``gaia_proper_motion_unknown`` (cached Gaia rows had no proper motion,
    # so positions stay at epoch 2016.0), ``gaia_epoch_unknown`` (the frame has
    # no observation date, so positions stay at epoch 2016.0) and
    # ``scale_hint_binning_mismatch`` (the header's X and Y pixel sizes, or X
    # and Y binning, differ). They do not flag the run.
    astrometry_flags: list[str] = Field(default_factory=list, alias="astrometryFlags")

    # The size of one pixel on the sky, in arcseconds, from the solved
    # coordinates, and the width (FWHM) of the stars in the image, in pixels.
    # They let the residual above be judged against the equipment: a fit
    # should be much better than a star is wide. `None` when the image was
    # not solved or the stars could not be measured.
    plate_scale_arcsec_per_pixel: float | None = Field(default=None, alias="plateScaleArcsecPerPixel")
    star_fwhm_px: float | None = Field(default=None, alias="starFwhmPx")

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
PHOTOMETRY_PIPELINE_VERSION = "1.3.0"


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


class NoiseModelPoint(BaseModel):
    """One point of a photometry run's noise-model curve.

    The curve says how much scatter a constant star of each brightness shows
    in the run's field. A plot of these points, with the stars' own
    `instrumentalMag` and `rmsMag`, shows which stars sit above the curve.
    """

    model_config = ConfigDict(populate_by_name=True)

    # The median instrumental magnitude (-2.5 log10 of the flux in ADU per
    # second) of the stars in this bin of the fit.
    instrumental_mag: float = Field(alias="instrumentalMag")
    # The median scatter of those stars, in magnitudes.
    rms_mag: float = Field(alias="rmsMag")
    # How many stars were in the bin.
    star_count: int = Field(alias="starCount")


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
    # The field's noise-model curve, brightest bin first: how much scatter a
    # constant star of each brightness shows (see
    # `pipelines.photometry.processing.variability_indices`). Empty when the
    # field had too few stars to fit one.
    noise_model_curve: list[NoiseModelPoint] = Field(default_factory=list, alias="noiseModelCurve")
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
SPECTROSCOPY_PIPELINE_VERSION = "1.5.0"


class SpectralClassificationConcern(BaseModel):
    """One star whose self-determined spectral type shouldn't be trusted as-is.

    Names exactly which stars a spectroscopy run's own classification is
    shaky for, and why, rather than only reporting how many -- so a user
    building a personal catalog from self-determined types knows which
    entries to double-check instead of taking every one at face value.
    """

    model_config = ConfigDict(populate_by_name=True)

    star_id: str = Field(alias="starId")
    # "poor_match", "class_ambiguous", or both joined by a comma -- see
    # assess_output_quality.is_classification_poor_match and
    # .is_classification_class_ambiguous for what each means. A star that is
    # ambiguous only between neighbouring subtypes is not listed.
    reason: str = Field(alias="reason")
    spectral_type: str = Field(alias="spectralType")
    # The best reference's relative RMS (lower is closer). Not a probability.
    classification_rms: float | None = Field(default=None, alias="classificationRms")
    # The RMS of the best reference of a different spectral class letter
    # minus the best RMS, in relative RMS units; `None` when no other class
    # was compared. Not a probability.
    rms_gap_to_next_class: float | None = Field(default=None, alias="rmsGapToNextClass")


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
    # How many classified stars had a best match above `NO_GOOD_MATCH_RMS`, or
    # a gap to the best reference of another spectral class below
    # `AMBIGUOUS_RMS_GAP` -- see SpectralClassificationConcern.
    poor_match_classification_count: int = Field(default=0, alias="poorMatchClassificationCount")
    ambiguous_classification_count: int = Field(default=0, alias="ambiguousClassificationCount")
    flagged_spectral_classifications: list[SpectralClassificationConcern] = Field(
        default_factory=list, alias="flaggedSpectralClassifications"
    )
    # How each quality checkpoint went across the run's spectra, in stage
    # order: for each of the four stages, how many spectra were checked, how
    # many had a failed metric, and the median of each numeric metric (see
    # `StageQualityRollup`). `None` for a summary saved before this was
    # recorded, and for a run with no spectra.
    stage_quality_summary: list[StageQualityRollup] | None = Field(default=None, alias="stageQualitySummary")
    # How the run's spectra compare with their Gaia DR3 XP spectra: in each of
    # four bands, the median ratio observed / XP over the compared stars (the
    # measured residual instrument response) and its scatter, and the median
    # tilt (see `GaiaXpRunSummary`). `None` for a summary saved before this was
    # recorded, and for a run in which no star was compared.
    gaia_xp_summary: GaiaXpRunSummary | None = Field(default=None, alias="gaiaXpSummary")
    # How much the spectra's wavelength zero points scatter across the run,
    # before and after the pipeline's correction, and how the offsets relate
    # to zero-order saturation (see `WavelengthScaleSummary`). `None` for a
    # summary saved before this was recorded, and for a run in which no
    # spectrum had a measured offset.
    wavelength_scale_summary: WavelengthScaleSummary | None = Field(
        default=None, alias="wavelengthScaleSummary"
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
    # How many times the known-asteroid database (SkyBoT) was asked about the
    # field, and how many of those questions failed. A failed question looks
    # the same as an empty field, so without these a network error would make
    # every real mover seem unknown.
    ephemeris_queries_attempted: int = Field(default=0, alias="ephemerisQueriesAttempted")
    ephemeris_queries_failed: int = Field(default=0, alias="ephemerisQueriesFailed")


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
