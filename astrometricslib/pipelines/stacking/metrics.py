"""The quality metrics that the stacking pipeline declares for the registry.

Each `MetricSpec` describes one measurement that existing code already makes.
The `computed_by` field points at the function that makes it.

The stacking stage records these values and its self-checks as measurements
next to the quality summary, which stays as it is
(`post_processing/record_quality.py`). Values read from the stack are about the
stack (level ``stack``). A value about one exposure group is about that group's
own stack file (see `exposure_group_subject` in `record_quality.py`). The
per-frame weights are about one frame each.

Two of them are frame metrics (`frame.quarantine_roundness` and
`frame.quarantine_sky_median_adu`). The frame quarantine step measures them
for every light frame it checks, so the stacking stage records them for kept
frames as well as for the frames it moves aside.
"""

import datetime

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)
from astrometricslib.pipelines.stacking.post_processing.exposure_saturation import (
    CHECKED_ESTIMATE_ERROR_FRACTION,
    CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA,
)

STACKING_REJECTED_PIXEL_FRACTION = "stacking.rejected_pixel_fraction"
FRAME_QUARANTINE_ROUNDNESS = "frame.quarantine_roundness"
FRAME_QUARANTINE_SKY_MEDIAN = "frame.quarantine_sky_median_adu"

_DECLARED_BEFORE_PHASE_3: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=STACKING_REJECTED_PIXEL_FRACTION,
        definition_version=1,
        unit="fraction",
        checkpoint=Checkpoint.STACKING,
        levels=(SubjectLevel.STACK,),
        better=Direction.LOWER,
        meaning="The share of pixel values thrown out while combining the frames.",
        decisions=("rejected pixel fraction gate",),
        estimator=(
            "The mean over all pixels of Siril's rejection map (`_RejMap.fits`), where each pixel "
            "holds the fraction of the frames that were rejected at that position."
        ),
        sample_definition=(
            "Every pixel position of the stack; each holds the share of frames rejected there. "
            "The register entry STK-24 is Partly measured: the normal range is measured, "
            "the detection side is not."
        ),
        band="stack filter or channel",
        computed_by="drivers.siril_output_parsing.measure_rejected_fraction",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-24",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A high share means the frames disagree, often from poor alignment or cloud, "
            "and the stack gate flags it."
        ),
    ),
    MetricSpec(
        name=FRAME_QUARANTINE_ROUNDNESS,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.FRAME,
        levels=(SubjectLevel.FRAME,),
        better=Direction.HIGHER,
        meaning=(
            "How round the brightest stars of one raw frame are, as the frame quarantine step "
            "measures it (1 is round, smaller is more stretched). It is the step's own number, "
            "kept so a frame that was kept can be compared with the frames that were moved."
        ),
        decisions=("frame quarantine",),
        # `frame.star_elongation` is a different estimator (Moffat fits and
        # second moments of up to 196 stars). The two must never share a name.
        estimator=(
            "Median over the brightest detected stars (at most `MAXIMUM_STARS_MEASURED`) of the "
            "smaller flux-weighted second-moment width divided by the larger one, measured in a "
            "small box on the background-subtracted frame by `detect_stars`. The widths are taken "
            "along the image x and y axes, not along the star's own axes, so a trail at 45 degrees "
            "to the axes reads round (a roundness near 1); the metric catches trails along the axes "
            "and is blind to diagonal ones. `frame.star_elongation` has no such blind spot."
        ),
        sample_definition=(
            "The brightest stars with a measurable shape, away from the frame edge. A frame with "
            "no such star gets no value."
        ),
        band="The frame's own filter; a color image is read as stored, with its color pixels mixed.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-01",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame whose stars are much less round than the rest of its session has trailed, and "
            "the quarantine step moves it out of the stack."
        ),
    ),
    MetricSpec(
        name=FRAME_QUARANTINE_SKY_MEDIAN,
        definition_version=1,
        unit="adu",
        checkpoint=Checkpoint.FRAME,
        levels=(SubjectLevel.FRAME,),
        better=Direction.NONE,
        meaning=(
            "The median pixel value of one raw frame, as the frame quarantine step measures it. "
            "It includes the bias level and every star, so it is not the sky brightness."
        ),
        decisions=("frame quarantine",),
        # `frame.background_level_adu` is the sigma-clipped median, a different
        # estimator. The two must never share a name.
        estimator="The plain median of every pixel of the frame, `numpy.median`, with no clipping.",
        sample_definition="Every pixel of the frame as stored.",
        band="The frame's own filter; a color image is read as stored, with its color pixels mixed.",
        computed_by="pipelines.shared.quality.raw_frame_check.measure_raw_frame",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="STK-01",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "A frame much brighter than its session, together with few stars, is clouded, and the "
            "quarantine step moves it out of the stack."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Phase 3: the stack summary's numbers, the stack's self-checks, the noise of
# empty sky, the frames that went in and their weights.
# ---------------------------------------------------------------------------

STACKING_FRAMES_SUBMITTED = "stacking.frames_submitted_count"
STACKING_FRAMES_STACKED = "stacking.frames_stacked_count"
STACKING_FRAMES_ACCEPTED = "stacking.frames_accepted_count"
STACKING_FRAMES_EXCLUDED_FOR_GAIN = "stacking.frames_excluded_for_gain_count"
STACKING_FRAMES_EXCLUDED_FOR_BACKGROUND = "stacking.frames_excluded_for_background_count"
STACKING_FRAMES_QUARANTINED = "stacking.frames_quarantined_count"
STACKING_FLAT_FRAME_COUNT = "stacking.flat_frame_count"
STACKING_FLAT_NOISE_FRACTION = "stacking.flat_noise_fraction"
STACKING_FLAT_SMOOTHING_SIGMA_PX = "stacking.flat_smoothing_sigma_px"
STACKING_SATURATED_PIXEL_FRACTION = "stacking.saturated_pixel_fraction"
STACKING_ZERO_PIXEL_FRACTION = "stacking.zero_pixel_fraction"
STACKING_NEGATIVE_PIXEL_MAX_FRACTION = "stacking.negative_pixel_max_fraction"
STACKING_STACKED_FWHM_PX = "stacking.stacked_fwhm_px"
STACKING_MEDIAN_INPUT_FWHM_PX = "stacking.median_input_fwhm_px"
STACKING_EXPECTED_STACK_FWHM_PX = "stacking.expected_stack_fwhm_px"
STACKING_DURATION_S = "stacking.duration_s"
STACKING_REGISTRATION_REFERENCE_STAR_COUNT = "stacking.registration_reference_star_count"
STACKING_GROUP_EXPOSURE_S = "stacking.group_exposure_s"
STACKING_GROUP_FRAMES_SUBMITTED = "stacking.group_frames_submitted_count"
STACKING_GROUP_FRAMES_STACKED = "stacking.group_frames_stacked_count"
STACKING_GROUP_ALIGNMENT_SHIFT_PX = "stacking.group_alignment_shift_px"
STACKING_GROUP_GAIN_MID_RANGE = "stacking.group_gain_mid_range"
STACKING_GROUP_GAIN_BRIGHT_END_RATIO = "stacking.group_gain_bright_end_ratio"
STACKING_GROUP_GAIN_DISAGREEMENT = "stacking.group_gain_disagreement_fraction"
STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT = "stacking.empty_aperture_noise_stack_unit"
STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT = "stacking.noise_reference_flux_stack_unit"
STACKING_EMPTY_APERTURE_NOISE_NORMALIZED = "stacking.empty_aperture_noise_normalized"
STACKING_EFFECTIVE_FRAME_COUNT = "stacking.effective_frame_count"
STACKING_EFFECTIVE_TIME_S = "stacking.effective_time_s"
STACKING_EFFECTIVE_TIME_READ_NOISE_S = "stacking.effective_time_read_noise_s"
STACKING_FRAME_WEIGHT = "stacking.frame_weight"
STACKING_NIGHT_WEIGHT_SHARE = "stacking.night_weight_fraction"
STACKING_NOISE_WITHOUT_NIGHT = "stacking.noise_without_night"
STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT = "stacking.noise_difference_without_night"
STACKING_SUBSET_EXPECTED_NOISE_RATIO = "stacking.subset_expected_noise_ratio"
STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT = "stacking.half_difference_noise_stack_unit"
STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED = "stacking.half_difference_noise_normalized"
STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT = "stacking.half_difference_reference_flux_stack_unit"
STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR = "stacking.half_difference_variance_factor"
STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO = "stacking.half_difference_model_variance_ratio"
STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT = "stacking.half_difference_odd_effective_frame_count"
STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT = "stacking.half_difference_even_effective_frame_count"
STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION = (
    "stacking.half_difference_shared_frame_odd_weight_fraction"
)
STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION = (
    "stacking.half_difference_shared_frame_even_weight_fraction"
)

# The expected empty-aperture noise and its parts, the stack's sky level, the
# master flat's noise, and the brightest star's peak of each exposure group.
STACKING_EXPECTED_SKY_NOISE_STACK_UNIT = "stacking.expected_sky_noise_stack_unit"
STACKING_EXPECTED_READ_NOISE_STACK_UNIT = "stacking.expected_read_noise_stack_unit"
STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT = "stacking.expected_empty_aperture_noise_stack_unit"
STACKING_EXPECTED_NOISE_APERTURE_AREA_PX = "stacking.expected_noise_aperture_area_px"
STACKING_EXPECTED_NOISE_SCALE_STACK_UNIT_PER_ADU = "stacking.expected_noise_scale_stack_unit_per_adu"
STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT = "stacking.expected_sky_level_stack_unit"
STACKING_STACK_SKY_LEVEL_STACK_UNIT = "stacking.stack_sky_level_stack_unit"
STACKING_FLAT_NOISE_AFTER_SMOOTHING_FRACTION = "stacking.flat_noise_after_smoothing_fraction"
STACKING_FLAT_NOISE_IN_APERTURE_STACK_UNIT = "stacking.flat_noise_in_aperture_stack_unit"
STACKING_GROUP_PEAK_FRACTION = "stacking.group_brightest_star_peak_fraction"
STACKING_GROUP_PEAK_ABOVE_SKY_ADU = "stacking.group_brightest_star_peak_above_sky_adu"
STACKING_GROUP_RAW_SKY_LEVEL_ADU = "stacking.group_raw_sky_level_adu"
STACKING_GROUP_CLIP_CEILING_ADU = "stacking.group_clip_ceiling_adu"
STACKING_GROUP_LINEARITY_LIMIT_ADU = "stacking.group_linearity_limit_adu"
STACKING_GROUP_PEAK_ESTIMATED_FRAME_COUNT = "stacking.group_peak_estimated_frame_count"
STACKING_GROUP_CLIPPED_FRAME_COUNT = "stacking.group_clipped_frame_count"

# The rotation of each frame about the registration reference (per frame, from
# the stacking run).
STACKING_FRAME_REGISTRATION_ROTATION_DEG = "stacking.frame_registration_rotation_deg"

# Whether the flats' dust shadows still match the dust in the raw lights.
STACKING_FLAT_DUST_SHADOW_COUNT = "stacking.flat_dust_shadow_count"
STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET = "stacking.flat_light_dust_correlation_at_zero_offset"
STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS = "stacking.flat_light_correlation_outside_shadows"
STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS = "stacking.flat_light_search_excess_sigmas"
STACKING_FLAT_LIGHT_BEST_CORRELATION = "stacking.flat_light_best_correlation"
STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX = "stacking.flat_light_best_shift_x_px"
STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX = "stacking.flat_light_best_shift_y_px"
STACKING_FLAT_LIGHT_BEST_ROTATION_DEG = "stacking.flat_light_best_rotation_deg"
STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD = "stacking.flat_light_displaced_correlation_std"
STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION = "stacking.light_dust_split_half_correlation"
# How far the pointing stepped between exposures and how it drifted (per stack,
# from Siril's registration of the run).
STACKING_REGISTRATION_STEP_MEDIAN_PX = "stacking.registration_step_median_px"
STACKING_REGISTRATION_STEP_MAD_PX = "stacking.registration_step_mad_px"
STACKING_REGISTRATION_STEP_JUMP_COUNT = "stacking.registration_step_jump_count"
STACKING_REGISTRATION_STEP_DEGENERATE_COUNT = "stacking.registration_step_degenerate_count"
STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX = "stacking.registration_step_after_drift_median_px"
STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR = "stacking.registration_drift_rate_px_per_hour"
STACKING_REGISTRATION_DRIFT_DIRECTION_DEG = "stacking.registration_drift_direction_deg"
STACKING_REGISTRATION_DRIFT_EXTENT_PX = "stacking.registration_drift_extent_px"

# What the two half stacks share at the scale of a few pixels, and how that
# differs along and across the drift (per library stack, from the half-stack
# difference job).
STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT = "stacking.half_shared_fine_scale_rms_stack_unit"
STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO = "stacking.half_shared_fine_scale_variance_ratio"
STACKING_HALF_SHARED_FINE_SCALE_COVARIANCE_STACK_UNIT_SQUARED = (
    "stacking.half_shared_fine_scale_covariance_stack_unit_squared"
)
STACKING_HALF_FINE_SCALE_DIFFERENCE_VARIANCE_STACK_UNIT_SQUARED = (
    "stacking.half_fine_scale_difference_variance_stack_unit_squared"
)
STACKING_HALF_SHARED_FINE_SCALE_REFERENCE_CORRECTION_STACK_UNIT_SQUARED = (
    "stacking.half_shared_fine_scale_reference_correction_stack_unit_squared"
)
STACKING_WALKING_NOISE_ALONG_CORRELATION = "stacking.walking_noise_along_drift_correlation"
STACKING_WALKING_NOISE_ACROSS_CORRELATION = "stacking.walking_noise_across_drift_correlation"
STACKING_WALKING_NOISE_CORRELATION_EXCESS = "stacking.walking_noise_correlation_excess"

# The self-checks. The key is the gate's name in the stack summary; the value
# is the metric that stores it. Each is named ``stacking.gate_<gate name>``.
STACKING_GATE_GAIN_HOMOGENEITY = "stacking.gate_gain_homogeneity"
STACKING_GATE_FRAME_QUARANTINE = "stacking.gate_frame_quarantine"
STACKING_GATE_BACKGROUND_HOMOGENEITY = "stacking.gate_background_homogeneity"
STACKING_GATE_FLAT_LEVEL = "stacking.gate_flat_level"
STACKING_GATE_FLAT_NOISE = "stacking.gate_flat_noise"
STACKING_GATE_CALIBRATION_METADATA = "stacking.gate_calibration_metadata"
STACKING_GATE_CALIBRATION_FRAME_COUNT = "stacking.gate_calibration_frame_count"
STACKING_GATE_EXPOSURE_GROUP_LINEARITY = "stacking.gate_exposure_group_linearity"
STACKING_GATE_EXPOSURE_GROUPS_COMBINED = "stacking.gate_exposure_groups_combined"
STACKING_GATE_RAW_FRAME_CLIPPING = "stacking.gate_raw_frame_clipping"
STACKING_GATE_REJECTED_PIXEL_FRACTION = "stacking.gate_rejected_pixel_fraction"
STACKING_GATE_STACK_SHARPNESS = "stacking.gate_stack_sharpness"
STACKING_GATE_SPECTRAL_REGISTRATION = "stacking.gate_spectral_registration"
STACKING_GATE_SATURATED_PIXEL_FRACTION = "stacking.gate_saturated_pixel_fraction"
STACKING_GATE_ZERO_PIXEL_FRACTION = "stacking.gate_zero_pixel_fraction"
STACKING_GATE_NEGATIVE_PIXELS = "stacking.gate_negative_pixels"

_STACK_ONLY = (SubjectLevel.STACK,)
_BAND = (
    "The stack's own filter or color channels as stored; a color stack is read as stored, "
    "with no channel picked out."
)
_NO_UNCERTAINTY = "No uncertainty is reported. The value is a single count or summary."
_STAGE = "pipelines.stacking.stage"
_CONTEXT_REASON = (
    "It is kept so a reader can see how the stack was made and compare stacks of the same target. "
    "No decision uses it directly."
)


def _context_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    computed_by: str,
    *,
    better: Direction = Direction.NONE,
    levels: tuple[SubjectLevel, ...] = _STACK_ONLY,
    register_entry: str | None = None,
    purpose: MetricPurpose = MetricPurpose.CONTEXT,
    purpose_reason: str = _CONTEXT_REASON,
    uncertainty_kind: UncertaintyKind = UncertaintyKind.NONE,
    uncertainty_method: str = _NO_UNCERTAINTY,
    band: str = _BAND,
    definition_version: int = 1,
    decisions: tuple[str, ...] = (),
    exploratory_question: str = "",
    review_date: datetime.date | None = None,
) -> MetricSpec:
    """Declare one value that the stacking stage already makes.

    Parameters
    ----------
    name : `str`
        The metric name.
    unit : `str`
        The unit of every measurement.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the value is found.
    sample_definition : `str`
        What is counted or sampled.
    computed_by : `str`
        Dotted path of the code that makes the value.
    better : `Direction`, optional
        Which direction is better.
    levels : `tuple` [`SubjectLevel`], optional
        What the value is about. A stack unless the caller says otherwise.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the value, if one does.
    purpose : `MetricPurpose`, optional
        Why the value exists. Context unless the caller says otherwise.
    purpose_reason : `str`, optional
        The reason for a decision or context purpose.
    uncertainty_kind : `UncertaintyKind`, optional
        The kind of uncertainty reported. None unless the caller says so.
    uncertainty_method : `str`, optional
        How the uncertainty is found.
    band : `str`, optional
        The filter the value applies to.
    definition_version : `int`, optional
        The version of the metric's estimator, sample and constants. Raise it
        when any of them changes (plan section 3.4).
    decisions : `tuple` [`str`], optional
        The decisions the value informs (documentation only).
    exploratory_question : `str`, optional
        The question an exploratory value answers. Needed for the
        exploratory purpose.
    review_date : `datetime.date` or `None`, optional
        When an exploratory value must be promoted or removed. Needed for
        the exploratory purpose.

    Returns
    -------
    spec : `MetricSpec`
        The declaration, at the given definition version (1 unless raised).
    """
    return MetricSpec(
        name=name,
        definition_version=definition_version,
        unit=unit,
        checkpoint=Checkpoint.STACKING,
        levels=levels,
        better=better,
        meaning=meaning,
        decisions=decisions,
        estimator=estimator,
        sample_definition=sample_definition,
        band=band,
        computed_by=computed_by,
        uncertainty_kind=uncertainty_kind,
        uncertainty_method=uncertainty_method,
        register_entry=register_entry,
        purpose=purpose,
        purpose_reason=purpose_reason,
        exploratory_question=exploratory_question,
        review_date=review_date,
    )


def _gate_metric(
    name: str,
    unit: str,
    meaning: str,
    value_description: str,
    computed_by: str,
    purpose_reason: str,
    *,
    register_entry: str | None = None,
) -> MetricSpec:
    """Declare one of the stack's self-checks.

    Parameters
    ----------
    name : `str`
        The metric name, ``stacking.gate_<gate name>``.
    unit : `str`
        The unit of the number the check measures and of its limit.
    meaning : `str`
        One line in plain words.
    value_description : `str`
        What the stored number is, and when there is none.
    computed_by : `str`
        Dotted path of the function that builds the gate.
    purpose_reason : `str`
        Why the check exists.
    register_entry : `str` or `None`, optional
        The validation register entry behind the check's limit, if one does.

    Returns
    -------
    spec : `MetricSpec`
        A decision-purpose declaration whose measurements carry the check's
        outcome and limit.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.STACKING,
        levels=_STACK_ONLY,
        better=Direction.LOWER,
        meaning=meaning,
        decisions=("stack flagged for a look",),
        estimator=(
            f"{value_description} It is stored with the check's outcome (passed, failed or not "
            "checked) and its limit. The check is a self-check: it flags the stack for a manual "
            "look and says nothing about the sky."
        ),
        sample_definition="The one stack this run made.",
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry=register_entry,
        purpose=MetricPurpose.DECISION,
        purpose_reason=purpose_reason,
    )


_GROUP_NOTE = (
    "Stored on the exposure group's own stack, so a stack of several exposure lengths has one value "
    "for each length."
)

_VALUE_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_FRAMES_SUBMITTED,
        "count",
        "How many frames were given to the stacking stage, before any were set aside.",
        "A count of the frames the caller listed for the stack.",
        "Every frame handed to the stage.",
        f"{_STAGE}.stack_frames",
    ),
    _context_metric(
        STACKING_FRAMES_STACKED,
        "count",
        "How many frames the stage counts as stacked.",
        (
            "The frames of the exposure groups that were combined, minus the frames whose files "
            "could not be read. It is the stage's count, not the number Siril printed, and it does "
            "not subtract the frames Siril dropped while registering."
        ),
        "The frames left after the stage's own checks.",
        f"{_STAGE}._base_stack_quality_summary",
        better=Direction.HIGHER,
    ),
    _context_metric(
        STACKING_FRAMES_ACCEPTED,
        "count",
        "How many frames were left after the gain, quarantine and background checks.",
        "A count of the frames given to the stacking program.",
        "The frames that passed the stage's pre-checks.",
        "pipelines.stacking.pre_processing.assess_input_quality.assess_input_quality",
        better=Direction.HIGHER,
    ),
    _context_metric(
        STACKING_FRAMES_EXCLUDED_FOR_GAIN,
        "count",
        "How many frames were set aside because their camera gain differed from the rest.",
        "A count of the frames with a minority gain setting.",
        "The frames the gain check set aside.",
        "pipelines.stacking.pre_processing.assess_input_quality.assess_input_quality",
    ),
    _context_metric(
        STACKING_FRAMES_EXCLUDED_FOR_BACKGROUND,
        "count",
        "How many frames were set aside because their sky background differed from the rest.",
        "A count of the frames the background-homogeneity check set aside.",
        "The frames the background check set aside.",
        "pipelines.stacking.pre_processing.assess_input_quality.assess_input_quality",
    ),
    _context_metric(
        STACKING_FRAMES_QUARANTINED,
        "count",
        "How many frames the quarantine step moved out of the stack.",
        "A count of the frames moved to the excluded folder for clouds or trailed stars.",
        "The frames the quarantine step moved.",
        "pipelines.stacking.pre_processing.assess_input_quality.assess_input_quality",
        register_entry="STK-01",
    ),
    _context_metric(
        STACKING_FLAT_FRAME_COUNT,
        "count",
        "How many flat frames the master flat was built from.",
        "A count of the flat frames Siril combined. Not stored when the stack used no flats.",
        "The flat frames used for this stack.",
        "pipelines.stacking.pre_processing.flat_calibration.assess_flats",
        better=Direction.HIGHER,
    ),
    _context_metric(
        STACKING_FLAT_NOISE_FRACTION,
        "fraction",
        "The relative noise of the master flat (0.005 is 0.5%).",
        "The flat assessment's noise estimate of the master flat, as a fraction of its level.",
        "The master flat used for this stack.",
        "pipelines.stacking.pre_processing.flat_calibration.assess_flats",
        better=Direction.LOWER,
        register_entry="STK-05",
    ),
    _context_metric(
        STACKING_FLAT_SMOOTHING_SIGMA_PX,
        "px",
        "How much the master flat was blurred (a Gaussian width) to calm its noise.",
        "The blur width the flat assessment chose; 0 when the flat was left alone.",
        "The master flat used for this stack.",
        "pipelines.stacking.pre_processing.flat_calibration.assess_flats",
        register_entry="STK-05",
    ),
    _context_metric(
        STACKING_SATURATED_PIXEL_FRACTION,
        "fraction",
        "The share of the stack's pixels that sit at its saturation ceiling.",
        (
            "The share of pixels piled up at the stack's highest value, as "
            "`compute_stack_saturated_pixel_fraction` counts them."
        ),
        "Every pixel of the stack.",
        "pipelines.shared.quality.quality_metrics.measure_saturated_pixel_fraction",
        better=Direction.LOWER,
    ),
    _context_metric(
        STACKING_ZERO_PIXEL_FRACTION,
        "fraction",
        "The share of the stack's pixels that are exactly zero.",
        "The count of pixels equal to zero divided by the pixel count; for a color stack, the worst channel.",
        "Every pixel of the stack.",
        f"{_STAGE}._measure_calibration_health",
        better=Direction.LOWER,
        register_entry="STK-26",
    ),
    _context_metric(
        STACKING_NEGATIVE_PIXEL_MAX_FRACTION,
        "fraction",
        "The worst share of negative pixels Siril warned about after subtracting the dark.",
        (
            "Siril's own warning, read from its output as a whole percent and stored as a fraction "
            "(12% is 0.12). No value when Siril printed no warning."
        ),
        "The worst frame Siril warned about.",
        "drivers.siril_output_parsing.parse_negative_pixel_percentage",
        better=Direction.LOWER,
        register_entry="STK-26",
    ),
    _context_metric(
        STACKING_STACKED_FWHM_PX,
        "px",
        "The median star width (full width at half maximum) of the finished stack.",
        (
            "The median star width from `measure_image_fwhm`; for a stack made of exposure groups, "
            "the same measure taken away from the cores the combine patched."
        ),
        "The brightest unsaturated stars of the stack.",
        f"{_STAGE}._measure_fwhm_degradation",
        better=Direction.LOWER,
        register_entry="STK-25",
    ),
    _context_metric(
        STACKING_MEDIAN_INPUT_FWHM_PX,
        "px",
        "The median star width of a sample of the input frames.",
        "The median of `measure_image_fwhm` over at most 15 frames spaced evenly over the set.",
        "At most 15 input frames.",
        f"{_STAGE}._measure_fwhm_degradation",
        better=Direction.LOWER,
        register_entry="STK-25",
    ),
    _context_metric(
        STACKING_EXPECTED_STACK_FWHM_PX,
        "px",
        "The star width the stack should have, given the widths of its input frames.",
        "The root mean square of the sampled input widths (`expected_stack_fwhm`).",
        "The same sample of at most 15 input frames.",
        "pipelines.stacking.post_processing.stack_quality.expected_stack_fwhm",
        better=Direction.LOWER,
        register_entry="STK-25",
    ),
    _context_metric(
        STACKING_DURATION_S,
        "s",
        "How long the stacking program ran.",
        "The wall-clock seconds the stacking program took, summed over exposure groups.",
        "One run of the stacking program.",
        "drivers.siril_interface.ImageProcessing",
        better=Direction.LOWER,
    ),
    _context_metric(
        STACKING_REGISTRATION_REFERENCE_STAR_COUNT,
        "count",
        "How many stars Siril found in the frame it aligned all the others to.",
        "Siril's star count for the registration reference frame.",
        "The one reference frame.",
        f"{_STAGE}._update_frame_registration_results",
        better=Direction.HIGHER,
    ),
    _context_metric(
        STACKING_GROUP_EXPOSURE_S,
        "s",
        "The exposure length of one exposure group.",
        "The exposure time that all the frames of the group share.",
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.post_processing.exposure_group_report.build_group_summary",
    ),
    _context_metric(
        STACKING_GROUP_FRAMES_SUBMITTED,
        "count",
        "How many frames were given to one exposure group.",
        "A count of the group's frames.",
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.post_processing.exposure_group_report.build_group_summary",
    ),
    _context_metric(
        STACKING_GROUP_FRAMES_STACKED,
        "count",
        "How many frames of one exposure group Siril stacked.",
        "Siril's count of the images stacked for the group.",
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.post_processing.exposure_group_report.build_group_summary",
        better=Direction.HIGHER,
    ),
    _context_metric(
        STACKING_GROUP_ALIGNMENT_SHIFT_PX,
        "px",
        "How far one group's stack was moved to line up with the reference group.",
        (
            "The length of the (rows, columns) shift vector, sqrt(rows^2 + columns^2). "
            "Not stored for the reference group."
        ),
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.post_processing.exposure_group_report.build_group_summary",
        register_entry="STK-13",
    ),
    _context_metric(
        STACKING_GROUP_GAIN_MID_RANGE,
        "",
        "How many times brighter per second one group reads than the reference group.",
        (
            "The median flux ratio of isolated stars, or of mid-range pixels when the field has too "
            "few stars. It is the scale applied to the group."
        ),
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.processing.exposure_groups.measure_exposure_group_gains",
        register_entry="STK-19",
    ),
    _context_metric(
        STACKING_GROUP_GAIN_BRIGHT_END_RATIO,
        "",
        "The same brightness ratio, measured only on the brightest stars.",
        "The median flux ratio of the brightest quarter of the stars. It is never applied.",
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.processing.exposure_groups.measure_exposure_group_gains",
        register_entry="STK-20",
    ),
    _context_metric(
        STACKING_GROUP_GAIN_DISAGREEMENT,
        "fraction",
        "How far the faint-star and bright-star brightness ratios of one group disagree.",
        (
            "The larger distance of the faint-quarter and bright-quarter ratios from the gain, divided "
            "by the gain; for the pixel fallback, abs(bright end ratio - gain) divided by the gain."
        ),
        f"One exposure group. {_GROUP_NOTE}",
        "pipelines.stacking.processing.exposure_groups.measure_exposure_group_gains",
        better=Direction.LOWER,
        register_entry="STK-20",
    ),
)

_NOISE_PURPOSE_REASON = (
    "The forecast check compares how the stack's noise falls as frames are added with what the frames "
    "predict. That needs the same sky apertures every time and one flux scale, which these three "
    "numbers (the noise, the flux it is divided by, and their ratio) supply."
)
_NOISE_SAMPLE = (
    "Up to 100 circular apertures (radius 4 px) placed at sky positions away from "
    "the 400 brightest stars (24 px) and away from faint sources (13 px from any pixel that, after "
    "removing the large-scale background, stands more than 3.5 robust sigma above zero at a smoothing "
    "of 1.5, 3 or 6 px; the robust sigma is taken over the pixels that hold data). The apertures are "
    "chosen by this pixel mask, made before they are placed, not by their own sums; its bias, "
    "measured, is under 1% on noise alone (validation entry STK-36). Only a library stack chooses "
    "them; a stack with at least twice the effective frame count of the stack that chose the set "
    "replaces the set by a new one with a new identity, and rows of different sets are never "
    "compared. Every stored row names the set's identity and the depth of the stack that chose it. "
    "The apertures are reused by every later stack of the target, and by its subset stacks, which "
    "never choose any. Apertures that fall off the "
    "stack or on empty (zero) edge pixels are dropped. No two apertures are closer than 24 px, so no two "
    "sky rings share a pixel. A stack needs at least 20 usable apertures; with fewer clean apertures the "
    "noise is not measured. Sky structure too faint to flag (unresolved stars, a faint halo) stays in the "
    "apertures. The target keeps one set of "
    "apertures for each camera and optic (see `empty_apertures.reset_apertures`)."
)
_NOISE_UNCERTAINTY = (
    "Statistical, 1 sigma: 1.1664 times the spread divided by sqrt(n) for n usable apertures. This is the "
    "standard error of a spread taken as 1.4826 times the median absolute deviation (its variance is that of "
    "a standard deviation divided by the 0.3675 efficiency of the median absolute deviation), for samples "
    "from a bell curve. The apertures do not overlap, so the samples are independent."
)
_WEIGHT_PURPOSE_REASON = (
    "Membership and weights say which frames the stack really holds and how much each counted. The "
    "forecast check needs them to predict the stack's noise from the frames' own noise."
)

_NOISE_AND_WEIGHT_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_EMPTY_APERTURE_NOISE_STACK_UNIT,
        "stack_unit",
        "The noise of the stack in an empty patch of sky, in the stack's own units.",
        (
            "The robust spread of the sky-subtracted sums of the empty apertures: 1.4826 times the "
            "median absolute deviation of the sums. Each sum is the aperture's pixel total (pixels "
            "weighted by the exact overlap) minus the median of its sky ring (7 to 12 px) times the "
            "aperture area. Signed, so the spread is not cut at zero. The apertures sit in sky free of "
            "bright stars and of faint sources found in the stack's pixels (see the sample), so the spread "
            "is the noise of empty sky plus whatever structure is too faint to flag. The stack's pixel "
            "scale is Siril's, so compare stacks only through the normalized value."
        ),
        _NOISE_SAMPLE,
        "pipelines.stacking.post_processing.stack_noise.measure_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_NOISE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=_NOISE_UNCERTAINTY,
        register_entry="STK-36",
        definition_version=3,
    ),
    _context_metric(
        STACKING_NOISE_REFERENCE_FLUX_STACK_UNIT,
        "stack_unit",
        "The brightness of the fixed reference stars in this stack, in the stack's own units.",
        (
            "The zero point times the median brightness the reference stars had on the stack that chose "
            "them. A star's brightness is its sky-subtracted sum in a large aperture (3 times the first "
            "stack's star width, never under 8 px, with a 5 px sky ring starting 3 px beyond it), so it "
            "hardly changes with the seeing. The zero point is the median, over the stars, of the ratio of "
            "the star's brightness here to its brightness on the first stack. The 10 stars are the brightest "
            "isolated stars of the first stack. A star whose peak in the registration reference frame is at "
            "or above the camera profile's saturation threshold (or its linearity limit, if lower) is "
            "never used, when the stars are chosen and on every later stack. A stack needs at least 5 usable "
            "stars."
        ),
        "The fixed reference stars of the target, those that fall on this stack and are not saturated.",
        "pipelines.stacking.post_processing.stack_noise.measure_stack_noise",
        better=Direction.NONE,
        purpose_reason=_NOISE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: 1.2533 times 1.4826 times the median absolute deviation of the stars' "
            "brightness ratios, divided by sqrt(k) for k stars (the standard error of a median), times the "
            "first stack's median star brightness. It is 0 on the stack that chose the stars, because that "
            "stack defines the scale. A star that widens past the large aperture would lower its ratio and "
            "widen the scatter; no change in the stars themselves is in the error."
        ),
        register_entry="STK-36",
        definition_version=3,
    ),
    _context_metric(
        STACKING_EMPTY_APERTURE_NOISE_NORMALIZED,
        "",
        "The empty-aperture noise as a fraction of the reference stars' brightness.",
        (
            "The empty-aperture noise divided by the reference flux. The stack's scale cancels, so "
            "stacks of the same target are comparable. It is stored with the two numbers it comes from."
        ),
        _NOISE_SAMPLE + " The reference stars as for the reference flux.",
        "pipelines.stacking.post_processing.stack_noise.measure_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_NOISE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: the relative error of the noise (1.1664 / sqrt(n)) and the relative error "
            "of the zero point added in quadrature, times the ratio. The scatter of the stars' brightness "
            "ratios is included; any real change in a star's brightness between stacks is not."
        ),
        register_entry="STK-36",
        definition_version=3,
    ),
    _context_metric(
        STACKING_EFFECTIVE_FRAME_COUNT,
        "count",
        "How many equally weighted frames would give the same noise as this weighted stack.",
        (
            "N_eff = (sum of w)^2 / (sum of w^2), where w is each frame's weight (Kish's effective "
            "sample size). It equals the frame count for equal weights. Not stored when the stacker "
            "chose the weights itself and they cannot be rebuilt (noise and star-count weighting)."
        ),
        "The frames that are members of the stack (see `stacking.frame_weight`).",
        "pipelines.stacking.post_processing.stack_membership.effective_frame_count",
        better=Direction.HIGHER,
        purpose_reason=_WEIGHT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_EFFECTIVE_TIME_S,
        "s",
        "How many seconds of equally weighted light would give the same noise as this stack.",
        (
            "T_eff = (sum of w)^2 / (sum of w^2 / t), where t is each frame's exposure in seconds. "
            "Each frame gives a rate estimate whose variance is proportional to 1/t (photon-limited "
            "light); a weighted mean of those rates has variance proportional to sum(w^2/t) / "
            "(sum w)^2, and an equal-weight stack of T seconds has variance proportional to 1/T. For "
            "equal weights and exposures it is the total exposure. Not stored when the weights are "
            "not reported."
        ),
        "The frames that are members of the stack.",
        "pipelines.stacking.post_processing.stack_membership.effective_exposure_seconds",
        better=Direction.HIGHER,
        purpose_reason=_WEIGHT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_EFFECTIVE_TIME_READ_NOISE_S,
        "s",
        (
            "The length of one frame that would have the same read noise as this stack, "
            "in the rate (counts per second)."
        ),
        (
            "tau = (sum of w) / sqrt(sum of w^2 / t^2), where t is each frame's exposure in seconds. Read "
            "noise RN adds a variance RN^2 to the counts of every frame whatever its length, so in the rate "
            "it is RN^2 / t^2. A weighted mean of the rates has a read-noise variance RN^2 x sum(w^2 / t^2) "
            "/ (sum w)^2, and one frame of tau seconds has RN^2 / tau^2. For N equal frames of t seconds it "
            "is t x sqrt(N). With `stacking.effective_time_s` (T_eff, the photon-noise part) a rate "
            "variance is R / T_eff + RN^2 / tau^2 for a sky rate R, which is what the forecast fit needs "
            "when the frames differ in length. Not stored when the weights are not reported."
        ),
        "The frames that are members of the stack.",
        "pipelines.stacking.post_processing.stack_membership.effective_read_noise_time_seconds",
        better=Direction.HIGHER,
        purpose_reason=_WEIGHT_PURPOSE_REASON,
    ),
    MetricSpec(
        name=STACKING_FRAME_WEIGHT,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.STACKING,
        levels=(SubjectLevel.FRAME,),
        better=Direction.NONE,
        meaning=(
            "How much one frame counted in a stack, relative to the average frame (1 is average). "
            "A row exists for each frame that went into the stack."
        ),
        decisions=("stack membership",),
        estimator=(
            "Equal stacking gives every frame 1. With several exposure groups, a frame's weight is its "
            "group's weight (frames x exposure^2 / frame noise^2) divided by the group's frame count, "
            "so it is proportional to exposure^2 / frame noise^2; the weights are then divided by "
            "their mean over the members. With Siril's star-width weighting (`-weight=wfwhm`, set by "
            "`Processing.Siril.stack_weight`) the weights are rebuilt with Siril's own formula "
            "from the weighted star width of each stacked frame in the sequence file: "
            "(1/w^2 - 1/w_max^2) / (1/w_min^2 - 1/w_max^2), so the blurriest frame gets 0, then "
            "divided by the mean. When Siril weights by noise or by number of stars, it does not "
            "report the weights and the scale it fits is not saved: the row is then not measured, "
            "but it still records that the frame is a member. A frame Siril dropped while registering is "
            "not a member and gets no row. When Siril's stack filters dropped frames that cannot be "
            "told apart, every candidate row is marked assumed."
        ),
        sample_definition="Each frame that the run stacked. The run id ties the rows to the stack.",
        band=_BAND,
        computed_by="pipelines.stacking.post_processing.stack_membership.determine_stack_members",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry="STK-07",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=_WEIGHT_PURPOSE_REASON,
    ),
)

_NIGHT_PURPOSE_REASON = (
    "Each night's contribution shows which nights helped a target and which did not (plan decision "
    "17). Nothing decides from it yet: the forecast check and the planner read it as context."
)
_NIGHT_SAMPLE = (
    "One row for each observing night of the frames in the target's library stack. The subject is "
    "the leave-one-night-out subset stack (a stack of the library stack's frames without that "
    "night, kept in the target's `_subsets` folder), and its observing night id names the night "
    "left out."
)

_NIGHT_REVIEW_DATE = datetime.date(2027, 4, 30)
_NIGHT_QUESTION = (
    "Does leaving one observing night out of a real target's library stack change the stack's noise "
    "by the amount that the night's share of the summed frame weights predicts? The register entry is "
    "a design estimate, because leaving a night out has been run on synthetic frames only."
)

_NIGHT_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_NIGHT_WEIGHT_SHARE,
        "fraction",
        "The share of the library stack's total frame weight that one observing night supplied.",
        (
            "The sum of the night's frame weights divided by the sum of all the stack's frame "
            "weights. The weights are the `stacking.frame_weight` rows the library stack recorded "
            "(each relative to the average frame). The shares of all the nights add up to 1. Not "
            "stored when the stack's weights are unknown, because equal weights are never assumed. "
            "The row is marked assumed, with the reason, when any weight it uses is assumed (the stack's "
            "member list may include frames that Siril's filters dropped)."
        ),
        _NIGHT_SAMPLE,
        "pipelines.stacking.subset_noise_run.night_weight_shares",
        better=Direction.NONE,
        register_entry="STK-37",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=_NIGHT_QUESTION,
        review_date=_NIGHT_REVIEW_DATE,
    ),
    _context_metric(
        STACKING_NOISE_WITHOUT_NIGHT,
        "",
        "The stack's noise, as a fraction of the reference stars' brightness, with one night left out.",
        (
            "The normalized empty-aperture noise (see `stacking.empty_aperture_noise_normalized`) of "
            "a stack of the library stack's frames without the night's frames, measured in the same "
            "sky apertures as the library stack. The library stack's value is "
            "`stacking.empty_aperture_noise_normalized`; `stacking.noise_difference_without_night` gives "
            "the difference with an error that accounts for the two stacks sharing frames and apertures. "
            "The leave-out stack uses the target's normal stacking settings but skips the frame "
            "quarantine step, which the library stack's own members have already passed."
        ),
        _NIGHT_SAMPLE + " Up to 100 empty apertures, as for the stack noise.",
        "pipelines.stacking.subset_noise_run.run_night_contributions",
        better=Direction.LOWER,
        register_entry="STK-37",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=_NIGHT_QUESTION,
        review_date=_NIGHT_REVIEW_DATE,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: as for `stacking.empty_aperture_noise_normalized`. This is the "
            "error of this stack's value on its own; it is not the error of a comparison with the "
            "library stack, which the two stacks' shared frames make smaller (see "
            "`stacking.noise_difference_without_night`). The stacker's own run-to-run differences are "
            "not included."
        ),
        definition_version=3,
    ),
    _context_metric(
        STACKING_NOISE_DIFFERENCE_WITHOUT_NIGHT,
        "",
        (
            "The noise of the stack without one night minus the noise of the library stack, "
            "each as a fraction of its reference stars' brightness."
        ),
        (
            "The normalized empty-aperture noise of the leave-out stack minus that of the library stack, "
            "both measured in the same sky apertures. Each stack's per-aperture sums are divided by its "
            "own reference flux first. The noise is 1.4826 times the median absolute deviation of the "
            "sums. Only apertures usable on both stacks count. The library stack's sums are read from the "
            "file kept beside it when it was stacked; the row is not measured when that file is missing "
            "or belongs to other apertures."
        ),
        _NIGHT_SAMPLE + " Only the apertures usable on both stacks.",
        "pipelines.stacking.post_processing.difference_noise.paired_noise_difference",
        better=Direction.NONE,
        register_entry="STK-37",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=_NIGHT_QUESTION,
        review_date=_NIGHT_REVIEW_DATE,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma, paired: the apertures are resampled with replacement 1000 times (a fixed "
            "seed), the same draw for both stacks, the difference of the two noises is found for each "
            "draw, and the uncertainty is the standard deviation of those differences. Because the "
            "stacks share frames and apertures, this is smaller than the two independent errors added in "
            "quadrature. The stacker's run-to-run differences are not included."
        ),
        definition_version=3,
    ),
    _context_metric(
        STACKING_SUBSET_EXPECTED_NOISE_RATIO,
        "",
        (
            "How many times the library stack's noise a subset stack's noise would be if all frames "
            "were alike: the square root of the library stack's effective frame count over the "
            "subset's."
        ),
        (
            "sqrt(N_eff,library / N_eff,subset), where N_eff = (sum of w)^2 / (sum of w^2) is the "
            "effective frame count (see `stacking.effective_frame_count`). The subset's weights are its "
            "own, because Siril rebuilds the star-width weights for each set of frames. If frames were "
            "equally noisy, the subset's noise divided by the library stack's would be this number, so "
            "it is the prediction to read the measured noise against. It says nothing about whether "
            "frames or a night were worth keeping. Not stored when either count is unknown; stored as "
            "assumed when either member list is."
        ),
        "One row for each subset stack (a growth step or a leave-one-night-out stack).",
        "pipelines.stacking.subset_noise_run.expected_noise_ratio",
        better=Direction.NONE,
        register_entry="STK-36",
        purpose_reason=_NIGHT_PURPOSE_REASON,
    ),
)

_HALF_DIFFERENCE_PURPOSE_REASON = (
    "The empty-aperture noise of a deep or crowded field still holds structure that every frame "
    "shares and that averaging never removes (STK-38). The difference of two disjoint halves "
    "cancels that structure, so it is the measure of the random noise that more frames do remove. "
    "The forecast check reads it next to the empty-aperture noise and the noise the frames predict. "
    "Nothing decides from it yet."
)
_HALF_DIFFERENCE_SAMPLE = (
    "The same empty apertures as the library stack's noise (see `stacking.empty_aperture_noise_stack_unit`), "
    "never chosen here; the job refuses, with a reason, when the halves were measured in other apertures "
    "than the library stack. The subject is the library stack, because the numbers describe its frames. "
    "The library stack's frames are put in order of capture time and split into two disjoint halves, the "
    "1st, 3rd, 5th, ... frame (odd) and the 2nd, 4th, 6th, ... (even), so both halves span the whole "
    "night. The library stack's registration reference frame goes into both halves and is handed to the "
    "stacker as the frame to line up with, so a half cannot choose its own and the pixels of the two "
    "halves coincide; the job still refuses when either half reports another reference frame. Each "
    "half is stacked with the target's normal settings except "
    "that the edges are not trimmed and no frame is quarantined. At least 8 frames per half, counting the "
    "shared one, are needed (a design choice, not a measurement); with fewer the rows are not measured. "
    "A Siril failure or a missing registration reference is stored as not measured, with the reason, and "
    "no other number is put in its place."
)
_HALF_DIFFERENCE_CANCELS = (
    "The difference A - B of the two half stacks cancels what both halves hold: sky structure, faint "
    "stars, nebula, a fixed pattern from the calibration frames (the master dark, bias and flat are "
    "the same in both halves, so their noise cancels; the master flat's random noise is 4.8% of the "
    "measured noise on M 57, STK-39), and the one shared frame, whose share differs a little between "
    "the halves. It keeps the "
    "random noise of both halves: photon noise, read noise, and the correlation between neighboring "
    "pixels that registration adds, because each frame is resampled on its own and the two halves "
    "hold different frames. It does NOT include structure common to both halves, so it is a lower "
    "limit of the empty-aperture noise, not a replacement for it."
)

_HALF_DIFFERENCE_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_HALF_DIFFERENCE_NOISE_STACK_UNIT,
        "stack_unit",
        "The noise of the difference of two half stacks in an empty patch of sky, in the stack's own units.",
        (
            "The robust spread of the sky-subtracted sums of the empty apertures taken on the image A - B: "
            "1.4826 times the median absolute deviation of the sums, each sum formed as for "
            "`stacking.empty_aperture_noise_stack_unit`. "
            + _HALF_DIFFERENCE_CANCELS
            + " If every frame had the same noise sigma, a half with normalized weights a (adding "
            "up to 1) would have the variance sigma^2 x sum(a^2) = sigma^2 / N_eff, the difference "
            "sigma^2 x F, with F = sum((a - b)^2) over the frames of both halves "
            "(`stacking.half_difference_variance_factor`), and the library stack "
            "sigma^2 / N_eff,library. The library stack's own random noise would then be the difference "
            "divided by sqrt(N_eff,library x F), which for two disjoint halves of N equal frames is the "
            "difference divided by 2. Frames differ in noise, and the star-width weights are not noise "
            "weights, so this is an estimate and is not stored as a value; the job's result reports it "
            "and every part of it is stored."
        ),
        _HALF_DIFFERENCE_SAMPLE,
        "pipelines.stacking.post_processing.difference_noise.measure_difference_noise",
        better=Direction.LOWER,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=_NOISE_UNCERTAINTY,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_NOISE_NORMALIZED,
        "",
        "The noise of the difference of two half stacks as a fraction of the reference stars' brightness.",
        (
            "`stacking.half_difference_noise_stack_unit` divided by the reference flux of the first half "
            "(the 10 fixed reference stars, as for `stacking.noise_reference_flux_stack_unit`; the "
            "denominator is stored as `stacking.half_difference_reference_flux_stack_unit`. Siril "
            "normalizes every frame of both halves to the same reference frame, so they share a flux "
            "scale, which STK-42 checks on real halves). The stack's scale "
            "cancels, so it can be read against `stacking.empty_aperture_noise_normalized`. "
            + _HALF_DIFFERENCE_CANCELS
        ),
        _HALF_DIFFERENCE_SAMPLE,
        "pipelines.stacking.post_processing.difference_noise.measure_difference_noise",
        better=Direction.LOWER,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: as for `stacking.empty_aperture_noise_normalized` (the relative error of "
            "the spread, 1.1664 / sqrt(n), and of the zero point, added in quadrature). The stacker's "
            "run-to-run differences and the choice of which frames fall in which half are not included."
        ),
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_VARIANCE_FACTOR,
        "",
        (
            "How many times a single frame's variance the difference of the two half stacks would have "
            "if every frame had the same noise."
        ),
        (
            "F = sum over all frames of (a - b)^2, where a and b are the frame's normalized weights in "
            "the odd and the even half (each half's weights add up to 1; a frame in one half only has 0 "
            "in the other). For disjoint halves it is sum(a^2) + sum(b^2) = 1 / N_eff,odd + 1 / N_eff,even. "
            "The one frame in both halves, the registration reference frame, has the largest star-width "
            "weight and lowers F by twice the product of its two shares, because its noise cancels in "
            "the difference. Not stored when a half's weights "
            "cannot be rebuilt."
        ),
        "The frames of both halves.",
        "pipelines.stacking.subset_noise_run.difference_variance_factor",
        better=Direction.NONE,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_REFERENCE_FLUX_STACK_UNIT,
        "stack_unit",
        "The brightness of the fixed reference stars on the first half stack, in the stack's own units.",
        (
            "The number `stacking.half_difference_noise_normalized` divides by: the zero point times the "
            "median brightness the 10 fixed reference stars had on the stack that chose them, read on the "
            "odd half stack (see `stacking.noise_reference_flux_stack_unit`). It is stored so the "
            "normalized value can be turned back into stack units, and so a reader can check that it "
            "is near the library stack's own reference flux, as it should be when Siril puts both "
            "halves on the library stack's scale."
        ),
        "The fixed reference stars of the target that fall on the odd half stack and are not saturated.",
        "pipelines.stacking.post_processing.difference_noise.measure_difference_noise",
        better=Direction.NONE,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: as for `stacking.noise_reference_flux_stack_unit` (the standard error "
            "of the median of the stars' brightness ratios, times the first stack's median star "
            "brightness)."
        ),
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_MODEL_VARIANCE_RATIO,
        "",
        (
            "How many times the library stack's variance the difference of the two half stacks should have, "
            "when each frame has the noise the sky and the camera predict for it."
        ),
        (
            "R = sum over all frames of (a - b)^2 v, divided by sum over all frames of s^2 v. Here a and b "
            "are the frame's normalized weights in the odd and the even half, s its normalized weight in "
            "the library stack, and v = (sky electrons + read noise^2) / gain^2 the frame's own variance in "
            "ADU squared, from its stored sky rate, exposure and camera profile (the model of "
            "`stacking.expected_sky_noise_stack_unit`). Unlike `stacking.half_difference_variance_factor` it "
            "lets frames differ in noise (the sky changes through a night). The library stack's own random "
            "noise is then the difference divided by sqrt(R). Not stored when a frame has no stored sky "
            "rate, gain or read noise, or a weight is unknown."
        ),
        "The frames of both halves, with the library stack's weights.",
        "pipelines.stacking.subset_noise_run.model_variance_ratio",
        better=Direction.NONE,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_SHARED_ODD_WEIGHT_FRACTION,
        "fraction",
        "The share of the odd half stack's weight that the one shared frame has.",
        (
            "The shared frame's weight in the odd half divided by the half's total weight. The shared "
            "frame is the library stack's registration reference. With the even half's share and the two "
            "effective frame counts it lets a reader rebuild the variance factor: "
            "F = 1 / N_eff,odd + 1 / N_eff,even - 2 a b, where a and b are the two shares."
        ),
        "The one frame that is in both halves.",
        "pipelines.stacking.subset_noise_run.half_difference_measurements",
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_SHARED_EVEN_WEIGHT_FRACTION,
        "fraction",
        "The share of the even half stack's weight that the one shared frame has.",
        (
            "The shared frame's weight in the even half divided by the half's total weight (see "
            "`stacking.half_difference_shared_frame_odd_weight_fraction`)."
        ),
        "The one frame that is in both halves.",
        "pipelines.stacking.subset_noise_run.half_difference_measurements",
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_ODD_EFFECTIVE_FRAME_COUNT,
        "count",
        "How many equally weighted frames would give the same noise as the odd half stack.",
        (
            "N_eff = (sum of w)^2 / (sum of w^2) for the frames of the odd half (see "
            "`stacking.effective_frame_count`), with the weights Siril's star-width weighting gives that "
            "half alone. Not stored when the weights cannot be rebuilt. It is a part of the half-difference "
            "noise: with `stacking.half_difference_even_effective_frame_count` it says how much "
            "random noise each half holds."
        ),
        "The frames of the odd half: the 1st, 3rd, ... by capture time, and the shared reference frame.",
        "pipelines.stacking.post_processing.stack_membership.effective_frame_count",
        better=Direction.HIGHER,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
    _context_metric(
        STACKING_HALF_DIFFERENCE_EVEN_EFFECTIVE_FRAME_COUNT,
        "count",
        "How many equally weighted frames would give the same noise as the even half stack.",
        (
            "N_eff = (sum of w)^2 / (sum of w^2) for the frames of the even half (see "
            "`stacking.effective_frame_count`), with the weights Siril's star-width weighting gives that "
            "half alone. Not stored when the weights cannot be rebuilt."
        ),
        "The frames of the even half: the 2nd, 4th, ... by capture time, and the shared reference frame.",
        "pipelines.stacking.post_processing.stack_membership.effective_frame_count",
        better=Direction.HIGHER,
        purpose_reason=_HALF_DIFFERENCE_PURPOSE_REASON,
        register_entry="STK-42",
    ),
)

_ASSESS = "pipelines.stacking.pre_processing.assess_input_quality.calibration_gates"
_OUTPUT = "pipelines.stacking.post_processing.assess_output_quality.output_quality_gates"
_GROUP_GATES = "pipelines.stacking.post_processing.exposure_group_report"

_GATE_METRICS: tuple[MetricSpec, ...] = (
    _gate_metric(
        STACKING_GATE_GAIN_HOMOGENEITY,
        "count",
        "Whether the frames' gain settings could be compared, with the number of frames set aside.",
        (
            "The number of frames set aside for a minority gain. No limit: setting them aside "
            "corrects the stack."
        ),
        "pipelines.stacking.pre_processing.frame_homogeneity.gain_homogeneity_gate",
        "Frames of different gains have different noise, so the minority is set aside and the count shown.",
    ),
    _gate_metric(
        STACKING_GATE_FRAME_QUARANTINE,
        "fraction",
        "Whether the quarantine step could judge each session and stay within its cap.",
        "No number: the check reports its outcome and the cap (the largest share of a session it may move).",
        "pipelines.stacking.pre_processing.frame_quarantine.quarantine_gate",
        (
            "A session with too many apparent bad frames is probably a bad night, so none are moved "
            "and the stack is flagged."
        ),
        register_entry="STK-03",
    ),
    _gate_metric(
        STACKING_GATE_BACKGROUND_HOMOGENEITY,
        "",
        "Whether the sky background of the frames split into two levels.",
        "No number: the check reports its outcome and the gap ratio it compares with.",
        "pipelines.stacking.pre_processing.background_homogeneity.background_homogeneity_gate",
        "A split in sky level (clouds, twilight, a lamp) means some frames do not belong with the rest.",
        register_entry="STK-04",
    ),
    _gate_metric(
        STACKING_GATE_FLAT_LEVEL,
        "fraction",
        "Whether the flat frames were neither too faint nor too bright.",
        "The flat frames' level as a fraction of full scale.",
        f"{_ASSESS}",
        "Faint flats are noisy and bright flats may be nonlinear, so either flags the stack.",
    ),
    _gate_metric(
        STACKING_GATE_FLAT_NOISE,
        "fraction",
        "Whether the master flat's noise was low enough.",
        "The master flat's relative noise.",
        f"{_ASSESS}",
        "The master flat's noise is copied into every frame, so a noisy flat leaves a fixed pattern.",
        register_entry="STK-05",
    ),
    _gate_metric(
        STACKING_GATE_CALIBRATION_METADATA,
        "count",
        "Whether the calibration frames matched the lights in gain, offset, temperature and binning.",
        "The number of mismatch flags. The limit is 0.",
        f"{_ASSESS}",
        "A calibration frame taken with other settings does not remove the lights' fixed pattern correctly.",
    ),
    _gate_metric(
        STACKING_GATE_CALIBRATION_FRAME_COUNT,
        "count",
        "Whether every master calibration frame was built from enough frames.",
        "The number of masters built from too few frames. The limit is 0.",
        f"{_ASSESS}",
        "A master built from a few frames adds its own noise to every light frame.",
    ),
    _gate_metric(
        STACKING_GATE_EXPOSURE_GROUP_LINEARITY,
        "fraction",
        "Whether the exposure groups of a stack agreed on one brightness scale.",
        "The largest brightness-ratio disagreement among the compared groups.",
        f"{_GROUP_GATES}.exposure_group_linearity_gate",
        "A group whose faint and bright stars scale differently is nonlinear or clipped and is left out.",
        register_entry="STK-20",
    ),
    _gate_metric(
        STACKING_GATE_EXPOSURE_GROUPS_COMBINED,
        "fraction",
        "Whether every exposure group went into the combined stack.",
        "The share of submitted frames that were lost with a dropped group. The limit is 0.",
        f"{_GROUP_GATES}.exposure_groups_combined_gate",
        "A dropped group leaves a full-looking stack that holds only part of the data.",
    ),
    _gate_metric(
        STACKING_GATE_RAW_FRAME_CLIPPING,
        "fraction",
        "Whether the raw frames were clipped at zero.",
        "The largest share of exactly-zero raw pixels in any exposure group.",
        f"{_GROUP_GATES}.raw_frame_clipping_gate",
        "A frame clipped at zero reads its faint sky too high.",
        register_entry="STK-22",
    ),
    _gate_metric(
        STACKING_GATE_REJECTED_PIXEL_FRACTION,
        "fraction",
        "Whether the stacker threw out too large a share of pixel values.",
        "The rejected pixel fraction (see `stacking.rejected_pixel_fraction`).",
        f"{_OUTPUT}",
        "A high rejected share means the frames disagree, often from poor alignment or cloud.",
        register_entry="STK-24",
    ),
    _gate_metric(
        STACKING_GATE_STACK_SHARPNESS,
        "",
        "Whether the stack is wider than its input frames predict.",
        "The stack's star width divided by the width its input frames predict.",
        f"{_OUTPUT}",
        "A stack wider than its inputs predict has been blurred by poor alignment.",
        register_entry="STK-25",
    ),
    _gate_metric(
        STACKING_GATE_SPECTRAL_REGISTRATION,
        "count",
        "Whether the spectral frames' zero-order star tracked steadily.",
        "The number of frames with a registration concern. The limit is 0.",
        f"{_OUTPUT}",
        "A zero-order star that jumps between frames smears the spectrum.",
    ),
    _gate_metric(
        STACKING_GATE_SATURATED_PIXEL_FRACTION,
        "fraction",
        "Whether too large a share of the stack's pixels is saturated.",
        "The saturated pixel fraction (see `stacking.saturated_pixel_fraction`).",
        f"{_OUTPUT}",
        "Saturated pixels hold no brightness information.",
    ),
    _gate_metric(
        STACKING_GATE_ZERO_PIXEL_FRACTION,
        "fraction",
        "Whether the stack is mostly zero (blank or over-subtracted).",
        "The zero pixel fraction (see `stacking.zero_pixel_fraction`).",
        f"{_OUTPUT}",
        "A mostly-zero stack has had more removed than the sky.",
        register_entry="STK-26",
    ),
    _gate_metric(
        STACKING_GATE_NEGATIVE_PIXELS,
        "fraction",
        "Whether Siril warned about many negative pixels after dark subtraction.",
        (
            "The worst warning as a fraction (the summary quotes whole percent; the stored number and "
            "limit are divided by 100)."
        ),
        f"{_OUTPUT}",
        "Many negative pixels after dark subtraction mean the dark frames are probably wrong.",
        register_entry="STK-26",
    ),
)


_EXPECTED_NOISE_PURPOSE_REASON = (
    "A stack whose measured noise sits far above what its frames' light predicts has a cause the "
    "model leaves out (correlated noise from registration, the master flat, a fixed pattern). "
    "Reading the measured noise against the expected parts is how a reader finds that out."
)
_EXPECTED_NOISE_SAMPLE = (
    "The member frames of the stack (see `stacking.frame_weight`), each with a stored sky "
    "measurement from the frame measurement job and a gain and read noise in the camera profile "
    "for its exact setting. A frame with a missing input is never skipped or replaced: the whole "
    "stack's expected noise is then not measured, with the reason."
)
_EXPECTED_NOISE_MODEL = (
    "The model: each frame has a sky of S electrons per pixel (the stored sky rate times the "
    "exposure; the rate includes dark current and amp glow, because no dark is subtracted when it is "
    "measured), a read noise of R electrons and a gain of g electrons per ADU, so one pixel has a "
    "variance of (S + R^2) / g^2 ADU squared. The stack is the weighted mean sum(w x) / sum(w) of "
    "the frames with the weights of `stacking.frame_weight`, so one stack pixel has the variance "
    "sum(w^2 v) / (sum w)^2. The sky and read parts are summed apart. An aperture sum multiplies "
    "that variance by `stacking.expected_noise_aperture_area_px` (about 46 px for the squared "
    "overlaps of the radius 4 px circle plus about 13 px for the variance of the median of the 7 to "
    "12 px sky ring that is subtracted), and the square root is multiplied by "
    "`stacking.expected_noise_scale_stack_unit_per_adu` to give the stack's units. Left out: Siril's "
    "normalization of each frame, the debayering of a color stack (interpolated, correlated pixels, "
    "planes averaged), the noise of the master dark, bias and flat, fixed patterns, the data thrown "
    "away by pixel rejection, and faint stars in the apertures; a measured noise above the expected "
    "one is what these may explain. Registration is not one of the causes: a resampling kernel whose "
    "weights add to 1 keeps an aperture sum. A stack combined from several exposure lengths, a stack "
    "whose member list is uncertain, and a stack whose weights are unknown get no value or an assumed "
    "one (see the reason on the row). A row built only from measured inputs says so: it is a model "
    "prediction from measured inputs, not a measurement of the stack."
)
_NO_PROPAGATED_UNCERTAINTY = (
    "No uncertainty is reported. The model has no random error of its own; the errors of its inputs "
    "(the gain, the sky) are not propagated."
)

_SCIENCE_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_EXPECTED_SKY_NOISE_STACK_UNIT,
        "stack_unit",
        "The photon-noise part of the noise the stack's frames predict in an empty aperture.",
        "The square root of the sky variance part of the model below, in the stack's units. "
        + _EXPECTED_NOISE_MODEL,
        _EXPECTED_NOISE_SAMPLE,
        "pipelines.stacking.post_processing.expected_noise.expected_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_EXPECTED_NOISE_PURPOSE_REASON,
        uncertainty_method=_NO_PROPAGATED_UNCERTAINTY,
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_EXPECTED_READ_NOISE_STACK_UNIT,
        "stack_unit",
        "The read-noise part of the noise the stack's frames predict in an empty aperture.",
        "The square root of the read variance part of the model below, in the stack's units. "
        + _EXPECTED_NOISE_MODEL,
        _EXPECTED_NOISE_SAMPLE,
        "pipelines.stacking.post_processing.expected_noise.expected_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_EXPECTED_NOISE_PURPOSE_REASON,
        uncertainty_method=_NO_PROPAGATED_UNCERTAINTY,
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_EXPECTED_EMPTY_APERTURE_NOISE_STACK_UNIT,
        "stack_unit",
        "The noise the stack's frames predict in an empty aperture, in the units of the measured noise.",
        "The sky part and the read part added in quadrature. " + _EXPECTED_NOISE_MODEL,
        _EXPECTED_NOISE_SAMPLE,
        "pipelines.stacking.post_processing.expected_noise.expected_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_EXPECTED_NOISE_PURPOSE_REASON,
        uncertainty_method=_NO_PROPAGATED_UNCERTAINTY,
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_EXPECTED_NOISE_APERTURE_AREA_PX,
        "px",
        "How many pixels of variance one aperture sum holds in the expected-noise model.",
        (
            "The sum of the squared pixel overlaps of the radius 4 px circle (about 46 px), plus (pi / 2) "
            "times the squared sum of the overlaps divided by the number of ring pixels (about 13 px), "
            "averaged over 16 places of the circle's centre inside a pixel: about 59 px for the stack's "
            "aperture. The pi / 2 assumes Gaussian noise in the ring."
        ),
        "The one aperture shape of `stack_image.py` (radius 4 px, ring 7 to 12 px).",
        "pipelines.stacking.post_processing.expected_noise.effective_aperture_area_px",
        purpose_reason=_EXPECTED_NOISE_PURPOSE_REASON,
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_EXPECTED_NOISE_SCALE_STACK_UNIT_PER_ADU,
        "stack_unit/adu",
        "How many of the stack's units one camera ADU is, in the expected-noise model.",
        (
            "1 / 65535 (the full-scale count of the exposure-group combine): Siril divides every integer "
            "frame by 65535 when it reads it, a 14-bit raw frame included (the Nikon's frames are not "
            "scaled up first), and writes the stack on that scale. A frame stored as floating-point "
            "numbers would break the scale, and nothing checks for one. It was checked against the "
            "stack's own sky level on one stack; every stack now stores both skies (see "
            "`stacking.expected_sky_level_stack_unit`) so the check can be repeated."
        ),
        "One constant for the stack.",
        "pipelines.stacking.post_processing.expected_noise.expected_stack_noise",
        purpose_reason=_EXPECTED_NOISE_PURPOSE_REASON,
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_EXPECTED_SKY_LEVEL_STACK_UNIT,
        "stack_unit",
        "The sky level the stack's frames predict, in the stack's units.",
        (
            "The weighted mean, with the weights of `stacking.frame_weight`, of each frame's sky in ADU "
            "(the stored sky rate times the exposure, divided by the gain) times the unit scale. Read "
            "next to `stacking.stack_sky_level_stack_unit` it checks the unit scale on every stack."
        ),
        _EXPECTED_NOISE_SAMPLE,
        "pipelines.stacking.post_processing.expected_noise.expected_stack_noise",
        purpose_reason=(
            "It is the other half of the unit check: the sky the frames predict against the sky the stack "
            "shows."
        ),
        register_entry="STK-38",
    ),
    _context_metric(
        STACKING_STACK_SKY_LEVEL_STACK_UNIT,
        "stack_unit",
        "The stack's own sky level, in the stack's units.",
        (
            "The median of the stack's pixels that are finite and not exactly zero (the empty edge of "
            "a registered stack is zero), in the stack's own units. The stars and nebula cover a small "
            "share of the stack, so the median is the sky. It is the signal level the master flat's "
            "noise is multiplied by, and it checks the unit scale against the sky the frames predict."
        ),
        "Every finite, nonzero pixel of the stack (a color stack has its channels averaged).",
        "pipelines.stacking.post_processing.stack_image.sky_level_of_plane",
        purpose_reason=(
            "A part of `stacking.flat_noise_in_aperture_stack_unit` (the flat's noise is multiplied by the "
            "signal) and of the unit check; no decision uses the level itself."
        ),
        register_entry="STK-39",
    ),
    _context_metric(
        STACKING_FLAT_NOISE_AFTER_SMOOTHING_FRACTION,
        "fraction",
        "The relative noise of the master flat in use, after any blur (0.005 is 0.5%).",
        (
            "The flat assessment's noise of the averaged flat frames before any blur "
            "(`stacking.flat_noise_fraction`) times the factor by which the Gaussian blur of width "
            "`stacking.flat_smoothing_sigma_px` lowers white noise: the square root of the sum of the "
            "squared weights of the sampled, normalized kernel (about 1 / (2 sigma sqrt(pi))). The "
            "master itself is not kept, so this is derived, not measured on the master."
        ),
        "The master flat used for this stack.",
        "pipelines.stacking.post_processing.flat_noise_contribution.flat_noise_contribution",
        better=Direction.LOWER,
        purpose_reason=(
            "A part of `stacking.flat_noise_in_aperture_stack_unit`, stored so the fraction is never "
            "hidden behind the aperture figure; the decision reads the aperture figure."
        ),
        uncertainty_method=_NO_PROPAGATED_UNCERTAINTY,
        register_entry="STK-39",
    ),
    _context_metric(
        STACKING_FLAT_NOISE_IN_APERTURE_STACK_UNIT,
        "stack_unit",
        "The noise the master flat adds to an empty-aperture sum, in the units of the stack's noise.",
        (
            "The stack's sky level times the master's relative noise before the blur times the standard "
            "deviation of a circle's sum of white noise after the same blur (the circle's overlap weights "
            "are blurred with the kernel and their squares summed, averaged over 16 places of the "
            "centre). It is the noise of an aperture sum if the whole pattern stays (no dithering). "
            "The sky ring that the stack's apertures subtract is left out: in a simulation it put the "
            "subtracted sum 11% above the figure for a flat that is not blurred and 7% above for a blur of "
            "1.5 px. A dithered stack averages the pattern, so for it the figure is an upper bound."
        ),
        "The master flat used for this stack and the stack's aperture shape (radius 4 px).",
        "pipelines.stacking.post_processing.flat_noise_contribution.flat_noise_contribution",
        better=Direction.LOWER,
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "Compared with `stacking.empty_aperture_noise_stack_unit` it tells whether more flat frames "
            "would lower the stack's noise; the comparison itself is a rule outside the library."
        ),
        decisions=("take more flat frames",),
        uncertainty_method=_NO_PROPAGATED_UNCERTAINTY,
        register_entry="STK-39",
    ),
    _context_metric(
        STACKING_GROUP_PEAK_FRACTION,
        "fraction",
        "How close the brightest star's peak is to the ceiling at this exposure (1 is the ceiling).",
        (
            "For each sampled raw frame of the group: the brightest star's peak above the sky divided by "
            "the room between the sky (the frame's median) and the ceiling. The peak is the highest "
            "pixel whose four neighbors are at least 20% of the way up (so a hot pixel is not a "
            "star). When the frame is clipped (4 or more connected pixels at its highest value), the "
            "ceiling is that value and the peak is estimated from the pixels just outside the clipped "
            "core with a Moffat profile (beta 3). An estimate is checked, for the camera, against two "
            "exposure lengths of the same field: it is stored as assumed, with the error of that check "
            "in the reason, when the largest estimate of the group is within the largest size checked for "
            "the camera ("
            + ", ".join(
                f"the ZWO ASI 533MM Pro: {size:.0f} times the room"
                for size in CHECKED_ESTIMATE_RANGE_ROOM_MULTIPLES_BY_CAMERA.values()
            )
            + "; no other camera has a checked range), where the estimated peak grew within "
            f"{CHECKED_ESTIMATE_ERROR_FRACTION:.0%} of the exposure ratio in at least 90% of the cases; "
            "beyond it the row is not measured, with the size of the estimate. The group's "
            "value is the median of the frames' fractions; the range test uses the largest estimate. For "
            "an unclipped frame the ceiling is the camera profile's clip ceiling."
        ),
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose=MetricPurpose.DECISION,
        purpose_reason=(
            "It replaces the recommended exposure: the planner reads how near the ceiling the brightest "
            "star is at the exposure that was used. The peak grows in proportion to the exposure."
        ),
        decisions=("exposure length",),
        uncertainty_method=(
            "No uncertainty is reported. The count of frames behind the median is the sample count. "
            "The error of an estimated peak is stated in the row's reason, from register entry STK-41."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_PEAK_ABOVE_SKY_ADU,
        "adu",
        "The brightest star's peak above the sky in a group's raw frames.",
        "The median, over the sampled frames, of the peak above the sky (see the fraction's estimator).",
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose_reason=(
            "A part of `stacking.group_brightest_star_peak_fraction`, stored so the fraction is never the "
            "only number."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_RAW_SKY_LEVEL_ADU,
        "adu",
        "The sky level, in ADU, of a group's raw frames.",
        "The median, over the sampled frames, of each frame's median pixel value.",
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose_reason=(
            "A part of `stacking.group_brightest_star_peak_fraction`: the room is the ceiling less this."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_CLIP_CEILING_ADU,
        "adu",
        "The ceiling, in ADU, that the brightest star's peak was compared with.",
        (
            "The median, over the sampled frames, of the camera profile's clip ceiling (a frame whose "
            "star is not clipped) or of the highest value the frame itself reached (a clipped frame). "
            "The row is assumed when a frame took the profile's ceiling and the profile's number is a "
            "datasheet or generic value, not a measured one."
        ),
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose_reason=(
            "A part of `stacking.group_brightest_star_peak_fraction`: the ceiling it is taken against."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_LINEARITY_LIMIT_ADU,
        "adu",
        "The value above which the camera stops responding in proportion to the light.",
        (
            "The camera profile's photometric linearity limit. Not stored when the profile has none. "
            "The row is assumed when the profile's number is a datasheet or generic value."
        ),
        "One value per camera profile. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.group_peak_fields",
        purpose_reason=(
            "A star whose peak sits above this limit gives a brightness that no longer grows in proportion "
            "to the light, even though it is not clipped; a reader can compare the peak with it."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_PEAK_ESTIMATED_FRAME_COUNT,
        "count",
        "How many of the sampled frames had a clipped star, whose peak was estimated.",
        "A count of the frames whose peak came from the wings of a clipped star, not from a pixel.",
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose_reason=(
            "Says how much of `stacking.group_brightest_star_peak_fraction` is an estimate and how much a "
            "measurement."
        ),
        register_entry="STK-40",
    ),
    _context_metric(
        STACKING_GROUP_CLIPPED_FRAME_COUNT,
        "count",
        "How many of the sampled frames had a clipped brightest star.",
        (
            "A count of the sampled raw frames with at least 4 connected pixels at the frame's highest "
            "value, whether or not a peak could be estimated for them. The row's sample count is the "
            "number of frames read."
        ),
        "Up to 3 raw frames spread through the group. " + _GROUP_NOTE,
        "pipelines.stacking.post_processing.exposure_group_report.summarize_group_peak",
        purpose_reason=(
            "Stored for every group, even when the peak estimate is not believed, so a reader always "
            "knows whether the brightest star clipped."
        ),
        register_entry="STK-40",
    ),
)

# Six months after the metrics below were first measured (2026-10-23): the
# science plan's default review date for an exploratory metric.
_FRAME_SHAPE_REVIEW_DATE = datetime.date(2027, 4, 30)

_FRAME_ROW_SAMPLE = (
    "One row for each frame the run gave to the stacking program, in the frame's own subject (the same "
    "subject as `stacking.frame_weight`). The frame's time is the timestamp of its frame record, joined "
    "by the subject id: the frame header's DATE-OBS (DATE when there is none), which by the FITS "
    "convention is the start of the exposure. The angle depends on which frame is the registration "
    "reference, so values compare only within one `run_id`: a restack that picks another reference "
    "changes them. The reason of a measured row names the reference frame and the image parity (the "
    "sign of det(CD) of the stack's plate solution, or unknown). A frame Siril could not register or "
    "never read, and every frame of a spectral stack, gets a row that is not measured, with the reason."
)

_FRAME_SHAPE_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=STACKING_FRAME_REGISTRATION_ROTATION_DEG,
        definition_version=1,
        unit="deg",
        checkpoint=Checkpoint.STACKING,
        levels=(SubjectLevel.FRAME,),
        better=Direction.NONE,
        meaning=(
            "The rotation of one frame about the registration reference, from the transform Siril "
            "fitted to line the frame's stars up with the reference frame's."
        ),
        decisions=("polar alignment",),
        estimator=(
            "The rotation of the polar decomposition of the 2 x 2 part of the 3 x 3 homography Siril "
            "saves for the frame in its registration table (the transform that moves the frame onto the "
            "reference): atan2(h10 - h01, h00 + h11) in degrees, which a small shear in the fit does not "
            "change. It is given only when the determinant of the 2 x 2 part is positive. Siril's image "
            "coordinates have x along the columns and y counted up from the last row of the array "
            "(y = height - 1 - row), so a positive angle turns the frame's x axis toward +y "
            "(counterclockwise when the array is drawn with its first row at the top). It is the "
            "rotation of the transform, so the frame's own orientation relative to the reference is "
            "the opposite sign. The angle lies in (-180, 180] degrees: a meridian flip turns the field "
            "by about 180 degrees, so the stored series jumps by about 360 degrees at the wrap and a "
            "reader unwraps it. It is not a polar-alignment error: the conversion belongs to the "
            "`wayfindinglib` library. The reference frame reads 0 by construction. A sequence whose "
            "frames are all the identity transform (already registered) is not measured. A spectral "
            "stack is registered by shift only, so it has no rotation."
        ),
        sample_definition=_FRAME_ROW_SAMPLE,
        band=_BAND,
        computed_by="pipelines.stacking.post_processing.field_rotation.field_rotation_measurements",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=(
            "No uncertainty is reported. Siril does not save the error of its fit. The register entry "
            "gives the spread of the angle over a night, which is an upper bound on the precision."
        ),
        register_entry="STK-43",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=(
            "Does the rotation of the frames about the reference drift through a night by an amount "
            "that tracks the polar-alignment error, once it is converted in `wayfindinglib`, and is "
            "Siril's angle precise enough to see it?"
        ),
        review_date=_FRAME_SHAPE_REVIEW_DATE,
    ),
)

_FLAT_MATCH_REVIEW_DATE = datetime.date(2027, 4, 30)
_FLAT_MATCH_QUESTION = (
    "Do the flats' dust shadows still match the lights (has dust moved, been added or been cleaned "
    "since the flats were taken)? Does the correlation of the dust shadows between the mean of the "
    "raw flats and the raw lights fall when the dust has changed, and does the best shift and "
    "rotation of the shadow pattern recover the change? The shadows come from dust on the camera's "
    "window, which turns with the sensor, so this cannot show a rotation of the camera relative to "
    "the optics."
)
_FLAT_MATCH_MAP = (
    "Both maps are built from raw frames, because the stacking step keeps neither the master flat nor "
    "the calibrated lights. Each frame is averaged into cells of 8 x 8 raw pixels (a color camera's 2 x 2 "
    "groups are merged first); a light frame has the bias level (from the stack's bias frames) subtracted, "
    "and every frame is divided by its median. A disc of 25 cells (200 pixels) around the frame centre, "
    "where the target sits, is set aside in every frame. The flat map is the mean of up to 16 raw flat "
    "frames (not the master flat the stack used, which is not kept). "
    "The light map is the pixelwise median of up to 24 raw, unregistered light frames that went into the "
    "stack, each with a sky of at least 10 times the read noise above the bias (from the camera profile), "
    "after the cells "
    "more than 4 robust standard deviations above the local sky (with their wings, and 2 cells around "
    "them) are set aside in each frame, and the cells more than 3 deviations above their neighbors on "
    "the median map are set aside in every frame (faint stars sit at the same pixels in every frame). "
    "Each map is then band-passed: a polynomial of degree 4 (the vignetting) is divided out, then the "
    "map smoothed with a Gaussian of 2 cells (16 pixels) is divided by the map smoothed with 30 cells "
    "(240 pixels), minus 1, so the dust shadows stay. Cells within 16 cells (128 pixels) of the edge "
    "are left out."
)
_FLAT_MATCH_SAMPLE = (
    "Up to 16 of the flat frames the stack was calibrated with and up to 24 of the frames that went into "
    "the stack (not the frames skipped as corrupt or left out at registration; when the member list "
    "could not be worked out, the submitted frames without the corrupt ones), sorted by time and path "
    "and spread evenly through the set (the first and last included). Light frames of another size "
    "than the first readable one are skipped, and so are those with a sky under 10 times the read "
    "noise above the bias (counted in the not-measured reason). At least 8 usable light frames are "
    "needed. The stack's bias frames and the camera profile's gain and read noise for the frames' "
    "setting are needed; without them nothing is measured. "
    "A spectral stack is not measured (its lights carry the grating's spectra). Reading is "
    "limited by a time limit from the configuration (default 300 s); when it is reached nothing is "
    "measured."
)
_FLAT_MATCH_UNCERTAINTY = (
    "No uncertainty is reported. `stacking.light_dust_split_half_correlation` says how repeatable "
    "the light map is, which limits the correlation any flat can reach."
)
_FLAT_MATCH_BASE = {
    "checkpoint": Checkpoint.STACKING,
    "levels": (SubjectLevel.STACK,),
    "decisions": ("take or retake flat frames",),
    "band": _BAND,
    "uncertainty_kind": UncertaintyKind.NONE,
    "uncertainty_method": _FLAT_MATCH_UNCERTAINTY,
    "register_entry": "STK-46",
    "purpose": MetricPurpose.EXPLORATORY,
    "exploratory_question": _FLAT_MATCH_QUESTION,
    "review_date": _FLAT_MATCH_REVIEW_DATE,
    "sample_definition": _FLAT_MATCH_SAMPLE,
    "definition_version": 1,
}
_FLAT_MATCH_SEARCH = (
    "The shift and rotation of the shadow pattern (not of the camera) are found by cross-correlating the "
    "maps by fast Fourier transform at every rotation from -3 to +3 degrees in steps of 0.25 degree, then "
    "refining the angle in steps of 0.05 degree, with a parabola fit for the sub-step shift and angle. "
    "The shift is searched over plus or minus 160 pixels. A pattern point of the flat appears in the "
    "lights moved by the shift first and then turned about the sensor centre (positive angles turn "
    "clockwise with the array drawn row 0 at the top)."
)
_FLAT_MATCH_ONLY_IF_FOUND = (
    " It is given only when the best score stands at least 3.1 null spreads (see "
    "`stacking.flat_light_search_excess_sigmas`; the limit is the 99th percentile of that statistic on "
    "unrelated dust, register entry STK-46) above the best score of the same search at 8 displaced "
    "placements, and the peak is not at the edge of the search; otherwise it is not measured, with the "
    "reason."
)
_FLAT_MATCH_PLACEMENT_UNCERTAINTY = (
    "Not a statistical 1 sigma. The square root of the sum of two squares: half the absolute difference "
    "between the value found with the odd frames' light map and with the even frames' (one split pair, "
    "so noise only and a rough size; each half has twice the noise variance of the whole sample), and a "
    "constant method error (1.1 pixel, 0.2 degree) from injecting known displacements at real shadow "
    "depths (0.15 to 0.4%) and real noise (register entry STK-46). Not given when either half found "
    "no placement or its peak was "
    "at the edge of the search. The dust shadows are broad, so the correlation changes slowly with the "
    "placement and the uncertainty can be large."
)
_FLAT_MATCH_PLACEMENT = {
    **_FLAT_MATCH_BASE,
    "uncertainty_kind": UncertaintyKind.SYSTEMATIC_BOUND,
    "uncertainty_method": _FLAT_MATCH_PLACEMENT_UNCERTAINTY,
}

_FLAT_MATCH_METRICS: tuple[MetricSpec, ...] = (
    MetricSpec(
        name=STACKING_FLAT_DUST_SHADOW_COUNT,
        unit="count",
        better=Direction.NONE,
        meaning="How many dust shadows (donuts) the flat shows (a diagnostic).",
        estimator=(
            _FLAT_MATCH_MAP + " Dust shadows are the connected groups (neighbors on a side or a corner) of "
            "at least 25 cells (40 x 40 pixels) of the flat's band-passed map that dip below the larger of "
            "5 standard deviations of the map's noise (half the standard deviation of the difference "
            "between the maps of the odd and the even flats; with one flat, the robust spread of the map "
            "itself) and 0.15% of the level. The count depends on these three limits, so it counts the "
            "shadows above them, not all the dust. It is a diagnostic: no other row waits on it. The "
            "control correlation uses the shadows it finds."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.count_dust_shadows",
        **{
            **_FLAT_MATCH_BASE,
            "sample_definition": "Up to 16 of the flat frames the stack was calibrated with.",
        },
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET,
        unit="",
        better=Direction.HIGHER,
        meaning="Correlation of the flat's dust pattern with the lights', with no shift or rotation.",
        estimator=(
            _FLAT_MATCH_MAP + " The value is the Pearson correlation coefficient of the two band-passed maps "
            "over the cells that both can use (at least 16 cells, 128 pixels, from the edge), at zero shift "
            "and rotation. 1 is the same pattern in the same place, 0 is no relation."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.match_dust_maps",
        **_FLAT_MATCH_BASE,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS,
        unit="",
        better=Direction.NONE,
        meaning="The same correlation with the flat's shadows masked out: a control for non-dust structure.",
        estimator=(
            _FLAT_MATCH_MAP + " The value is the Pearson correlation coefficient at zero shift and rotation "
            "over the cells that both maps can use, after the flat's dust shadows (see "
            "`stacking.flat_dust_shadow_count`) are enlarged by 4 cells (32 pixels) and left out. "
            "If it stays near the value with the shadows, the correlation does not come from the shadows' "
            "cores: either something other than dust is shared by the flat and the lights (a leftover of "
            "the vignetting, a fixed pattern), or the dust is deep enough that the faint halo the band "
            "pass leaves around each shadow (which the mask does not cover) correlates by itself. "
            "Not measured when fewer than 100 cells remain."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.measure_flat_match",
        **_FLAT_MATCH_BASE,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS,
        unit="",
        better=Direction.HIGHER,
        meaning="How far the best match stands above what the same search finds at displaced placements.",
        estimator=(
            _FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + " The search takes the best of 25 angles and "
            "41 x 41 shifts, so a high score happens by chance. The same search is run with the lights' map "
            "moved by 60 cells along and across the axes and diagonally (8 placements, where the two "
            "patterns cannot be the same dust). The value is the best coarse score of the real search minus "
            "the mean of those 8 best scores, over their sample standard deviation. It gates the shift and "
            "rotation rows (limit 3.1, the 99th percentile on unrelated dust, register entry STK-46)."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.match_dust_maps",
        **_FLAT_MATCH_BASE,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_BEST_CORRELATION,
        unit="",
        better=Direction.HIGHER,
        meaning="Correlation of the two dust patterns after the best shift and rotation of the lights.",
        estimator=(
            _FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + " The value is the Pearson correlation coefficient "
            "of the flat map and the light map sampled at the best placement (bilinear interpolation)."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.match_dust_maps",
        **_FLAT_MATCH_BASE,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
        unit="px",
        better=Direction.NONE,
        meaning="How far along the columns the lights' dust sits from the flat's, in sensor pixels.",
        estimator=_FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + _FLAT_MATCH_ONLY_IF_FOUND,
        computed_by="pipelines.stacking.post_processing.flat_match.measure_flat_match",
        **_FLAT_MATCH_PLACEMENT,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
        unit="px",
        better=Direction.NONE,
        meaning="How far along the rows the lights' dust sits from the flat's, in sensor pixels.",
        estimator=_FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + _FLAT_MATCH_ONLY_IF_FOUND,
        computed_by="pipelines.stacking.post_processing.flat_match.measure_flat_match",
        **_FLAT_MATCH_PLACEMENT,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
        unit="deg",
        better=Direction.NONE,
        meaning="How far the lights' dust pattern is turned about the sensor centre from the flat's.",
        estimator=_FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + _FLAT_MATCH_ONLY_IF_FOUND,
        computed_by="pipelines.stacking.post_processing.flat_match.measure_flat_match",
        **_FLAT_MATCH_PLACEMENT,
    ),
    MetricSpec(
        name=STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD,
        unit="",
        better=Direction.NONE,
        meaning="The chance spread of the correlation: its variation when the lights' map is moved far off.",
        estimator=(
            _FLAT_MATCH_MAP + " " + _FLAT_MATCH_SEARCH + " The value is the standard deviation of the "
            "normalized correlation (the search score: sum of the products over the sum of squares, which "
            "is the Pearson coefficient when the maps' means are 0) at every shift with the larger axis "
            "between 40 and 100 cells (320 to 800 pixels) and no rotation, where the two patterns cannot be "
            "the same dust (the shadows are about 40 cells across). Two maps of a few broad shadows "
            "correlate "
            "by chance with a spread of 0.1 to 0.3, so a correlation means little until it is read against "
            "this. Not measured when the maps are too small for shifts that far."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.match_dust_maps",
        **_FLAT_MATCH_BASE,
    ),
    MetricSpec(
        name=STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION,
        unit="",
        better=Direction.HIGHER,
        meaning="How repeatable the lights' dust map is: the odd frames' map against the even frames'.",
        estimator=(
            _FLAT_MATCH_MAP + " The light map is built twice, from the 1st, 3rd, ... and from the 2nd, "
            "4th, ... frames of the sample. The value is the Pearson correlation coefficient of the two "
            "band-passed maps. The light map holds the dust and whatever else repeats from frame to frame "
            "(the sensor's fixed pattern, stars too faint to find), so the value limits how much of the "
            "light map any flat could explain: it is not a measure of dust. The correlation with the flat "
            "(`stacking.flat_light_best_correlation`) uses the median of all the frames, which is a little "
            "more repeatable than a half."
        ),
        computed_by="pipelines.stacking.post_processing.flat_match.measure_flat_match",
        **_FLAT_MATCH_BASE,
    ),
)

_SHIFT_SAMPLE = (
    "The frames Siril registered, put in order of capture time (the frame header's DATE-OBS, DATE when "
    "there is none). Each frame's pointing is where Siril's transform puts the middle of the image, "
    "compared with the registration reference frame, in pixels; a frame without a capture time, and a "
    "frame Siril dropped, are left out. A stack whose frames were already registered (every transform is "
    "the identity) is not measured, and neither is a stack that joins several exposure groups (each "
    "group has its own reference). Siril registers a spectral stack by shift only; its shifts are "
    "used the same way."
)
_SHIFT_PURPOSE_REASON = (
    "It tells a reader whether the run was dithered, which decides how far a pattern fixed on the "
    "sensor averages out in the stack. No decision uses it directly."
)
_SHIFT_SUSPECT = (
    "The step rows are not measured when more than 10% of the pairs of one night have a step longer than "
    "50 px, or when more than half of the frames have exactly the identity transform: the transforms "
    "of such a run (some DSLR groups) are not pointings."
)
_DITHER_CAVEAT = (
    "A median step of 0.3 to 0.6 pixel is a slow drift plus the noise of the fit and does not show that "
    "the run was dithered (`stacking.registration_step_after_drift_median_px` takes the drift out). "
    "The measurement is a distance moved, whatever moved it. The number of pairs is the sample count "
    "of the row."
)

_SHIFT_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        STACKING_REGISTRATION_STEP_MEDIAN_PX,
        "px",
        "The median distance the pointing moved between two exposures taken one after the other.",
        (
            "For each pair of neighboring exposures of one observing night, the distance between their "
            "pointings, sqrt(dx^2 + dy^2) in pixels; the median over the pairs used. A pair across two "
            "observing nights is left out. A pair whose step is longer than 50 px is left out and counted "
            "(`stacking.registration_step_degenerate_count`): the stored shifts of some DSLR groups are "
            "thousands of pixels and are not pointing. At least 5 pairs are needed. "
            + _SHIFT_SUSPECT
            + " "
            + _DITHER_CAVEAT
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.step_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=(
            "No uncertainty is reported. The spread of the steps is stored beside it "
            "(`stacking.registration_step_mad_px`)."
        ),
    ),
    _context_metric(
        STACKING_REGISTRATION_STEP_MAD_PX,
        "px",
        "How much the steps between neighboring exposures vary: the median absolute deviation.",
        (
            "The median of the absolute differences between each used step and the median step "
            "(`stacking.registration_step_median_px`). It is not scaled to a standard deviation. A random "
            "dither has a wide spread and a pure drift has a narrow one. A dither that happens every few "
            "exposures changes fewer than half of the steps and leaves this value small; "
            "`stacking.registration_step_jump_count` counts those steps."
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.step_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_REGISTRATION_STEP_JUMP_COUNT,
        "count",
        "How many steps between neighboring exposures were far larger than the usual step: the dithers.",
        (
            "The number of used steps larger than the median step plus 5 robust standard deviations of the "
            "steps (1.4826 times the median absolute deviation, but not less than 0.1 px, so a pure drift "
            "with a spread near zero does not count every step above its median). It is the row to read "
            "for a run that dithers once every N exposures: the median step stays at the drift step and "
            "the median absolute deviation stays small, while this count is the number of dithers (plus "
            "any re-center or guiding upset) and the number of used pairs (the row's sample count) "
            "divided by it is about N. For steps of independent jitter alone the threshold is about 5.5 "
            "standard deviations of one coordinate; a simulation of 200 runs of 60 frames of jitter "
            "alone gave 2 false jumps in 11,800 steps (about one run in a hundred, because the spread "
            "is estimated from the run's own steps). At least 5 pairs are needed. " + _SHIFT_SUSPECT
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.step_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_REGISTRATION_STEP_DEGENERATE_COUNT,
        "count",
        "How many pairs of neighboring exposures had a step too long to be a dither and were left out.",
        (
            "The number of pairs of one night whose step was longer than 50 px. The limit sits between "
            "the largest real dither or drift step found in the stored shifts (a few pixels) and the "
            "5,000 to 7,400 px 'shifts' of DSLR groups (STK-44). Pairs across two observing nights are "
            "not counted here. When this count is more than 10% of the pairs of one night, the step "
            "rows are not measured."
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.step_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX,
        "px",
        "The median step between neighboring exposures after each night's steady drift is taken out.",
        (
            "For each observing night with at least 5 registered frames, a straight line of the pointing "
            "against time is fitted to each coordinate (frames more than 50 px off the line are dropped "
            "and the line is fitted again). The step is the distance between the neighboring frames' "
            "residuals from the line; the median over all pairs of all such nights, at least 5 pairs. "
            "Only the dither and the noise of Siril's fit are left. For an undithered run it is the "
            "fit noise times about 1.4. " + _SHIFT_SUSPECT
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.step_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR,
        "px/h",
        "How fast the pointing walks through a night, in pixels per hour.",
        (
            "For each observing night with at least 5 registered frames, the length of the slope of a "
            "straight line fitted to the pointing against time (the two coordinates fitted apart; frames "
            "more than 50 px off the line are dropped and the line is fitted again). The mean of the "
            "nights' lengths, weighted by the number of frames in each fit. A drift lost in the "
            "scatter of the fit still has a length, which the noise makes positive: the length of a "
            "slope with errors s in both coordinates averages 1.25 s when the true drift is zero. So "
            "the rate is biased upward when it is not several times its error, and the row says so in "
            "its reason."
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.drift_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: for each night, the standard error of the slope of each coordinate "
            "(from the scatter about the line and the spread of the frame times), combined along the "
            "drift; the nights' errors are added in quadrature with their frame-count weights. The "
            "straight-line model itself is not tested, and the error does not remove the upward bias "
            "of a length near zero."
        ),
    ),
    _context_metric(
        STACKING_REGISTRATION_DRIFT_DIRECTION_DEG,
        "deg",
        "The direction along which the pointing walks through a night, as an axis in the array.",
        (
            "The angle of the drift axis from the column axis toward the row axis (rows counted downward "
            "in the array), from 0 up to but not including 180 degrees, because the sign of a drift does "
            "not matter for the smear it makes. Each night whose drift is at least 3 times the "
            "standard error of its slope gives an axis; the nights' axes are averaged at twice their "
            "angle, weighted by frame count. Not measured when no night drifts by 3 standard errors "
            "(the axis would be set by noise) or when the nights' axes agree less than 0.5."
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.drift_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
    _context_metric(
        STACKING_REGISTRATION_DRIFT_EXTENT_PX,
        "px",
        "How far the pointing ranges along the drift axis within a night.",
        (
            "For the nights that give the drift axis, the largest minus the smallest pointing projected "
            "onto the axis; the largest over those nights. It includes a dither, which widens the "
            "range. A pattern fixed on the sensor is smeared over about this length in the stack."
        ),
        _SHIFT_SAMPLE,
        "pipelines.stacking.post_processing.registration_shifts.drift_summary",
        register_entry="STK-44",
        purpose_reason=_SHIFT_PURPOSE_REASON,
    ),
)

_PATTERN_REVIEW_DATE = datetime.date(2027, 4, 30)

_PATTERN_SAMPLE = (
    "The library stack's two half stacks (the job of `stacking.half_difference_noise_stack_unit`: the frames "
    "in capture-time order, alternately in each half, the registration reference frame in both). Used "
    "are the pixels at least 10 px from every faint source the stack's faint-source mask flags, and at "
    "least 9 px from the empty edge, in tiles of 64 x 64 px; at least 100,000 pixels and 30 tiles. A half "
    "pair whose weights are unknown is not measured."
)
_PATTERN_ESTIMATOR = (
    "Each half is put through a Gaussian high-pass filter (sigma 3 px). The covariance c of the two "
    "residual images over the used pixels is the variance of what the halves share at the scale of a "
    "few pixels, because the random noise of different exposures is independent. The registration "
    "reference frame is in both halves, and its random noise is shared too; it is taken out as "
    "sigma_f^2 x a x b, where a and b are its weight shares in the halves and sigma_f^2 is one "
    "frame's variance, the variance D of the difference of the residuals divided by the variance factor "
    "F (`stacking.half_difference_variance_factor`, which assumes equal frame noise). That correction "
    "is a model, not a measurement: the reference is the frame with the heaviest weight, so its noise "
    "is the least likely to equal the average frame's. The three parts are stored beside the result "
    "(`stacking.half_shared_fine_scale_covariance_stack_unit_squared`, "
    "`stacking.half_fine_scale_difference_variance_stack_unit_squared` and "
    "`stacking.half_shared_fine_scale_reference_correction_stack_unit_squared`). What the halves share "
    "is the part of any fixed pattern (a defect or flat-field grain that calibration left) that stays "
    "coherent in both halves, plus any sky structure at this scale that the faint-source mask did not "
    "flag (very faint stars, fine nebula). The halves alternate in time, so a pointing "
    "that is held for several exposures (a drift, or a dither made every N exposures) gives frames to both "
    "halves and a fixed pattern lands alike in both and is shared; only a dither that gives every exposure "
    "its own pointing makes the halves sample different pointings and share less. So the value is not "
    "a measurement of the sensor pattern. A sensor pattern is smeared along the pointing drift and "
    "sky structure is not: the walking-noise rows tell them apart."
)
_PATTERN_UNCERTAINTY = (
    "Statistical, 1 sigma: the standard deviation over 400 bootstrap resamples of the 64 x 64 px tiles "
    "(a fixed seed). Neighboring tiles are treated as independent, which is nearly right for a "
    "pattern a few pixels wide and understates the error for a smear longer than a tile. The choice "
    "of which frames fall in which half, the assumption of equal frame noise, and the uncertainty of "
    "the variance factor F and of the library stack's effective frame count N_eff (which set the "
    "reference-frame correction and the ratio's denominator) are not included."
)
_PATTERN_QUESTION = (
    "How much of the fine-scale structure that two halves of a stack share is a pattern fixed on the "
    "sensor, and how much is faint sky? Does the sensor part grow when the run is not dithered? The "
    "walking-noise rows (a smear along the drift) are the check, and the register entry STK-45 "
    "says how large a smeared pattern they could see (it is far above the grain a flat is expected "
    "to leave)."
)


def _pattern_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    *,
    uncertainty: bool = True,
    question: str = _PATTERN_QUESTION,
) -> MetricSpec:
    """Declare one value of the half-stack pattern measurement.

    Parameters
    ----------
    name : `str`
        The metric name.
    unit : `str`
        The unit.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the value is found.
    uncertainty : `bool`, optional
        Whether the value has the bootstrap uncertainty.
    question : `str`, optional
        The question the exploratory metric answers.

    Returns
    -------
    spec : `MetricSpec`
        An exploratory declaration for the half-stack difference job.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.STACKING,
        levels=_STACK_ONLY,
        better=Direction.NONE,
        meaning=meaning,
        decisions=(),
        estimator=estimator,
        sample_definition=_PATTERN_SAMPLE,
        band=_BAND,
        computed_by="pipelines.stacking.post_processing.half_pattern.measure_half_pattern",
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA if uncertainty else UncertaintyKind.NONE,
        uncertainty_method=_PATTERN_UNCERTAINTY if uncertainty else _NO_UNCERTAINTY,
        register_entry="STK-45",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=question,
        review_date=_PATTERN_REVIEW_DATE,
    )


_PATTERN_METRICS: tuple[MetricSpec, ...] = (
    _pattern_metric(
        STACKING_HALF_SHARED_FINE_SCALE_RMS_STACK_UNIT,
        "stack_unit",
        (
            "The size, per pixel, of the fine-scale structure that the two half stacks share: the part of "
            "any fixed sensor pattern that stays coherent in both halves, plus faint sky."
        ),
        (
            "The square root of the shared variance of the two halves' high-pass residuals. "
            + _PATTERN_ESTIMATOR
            + " Not measured when the shared variance is not above zero: the value would be the root of "
            "a negative number, and the ratio row keeps the sign."
        ),
    ),
    _pattern_metric(
        STACKING_HALF_SHARED_FINE_SCALE_VARIANCE_RATIO,
        "",
        (
            "The variance of the structure the half stacks share at a few pixels, as a multiple of the "
            "library stack's random variance."
        ),
        (
            "The shared variance (see `stacking.half_shared_fine_scale_rms_stack_unit`) divided by the "
            "random variance of the whole library stack at the same scale. The latter is the variance of the "
            "difference of the residuals, divided by the variance factor of the difference "
            "(`stacking.half_difference_variance_factor`) and by the library stack's effective frame "
            "count (`stacking.effective_frame_count`), so the ratio is (c - sigma_f^2 a b) / "
            "(D / (F x N_eff)) from the stored parts. It may be below zero, within its error. The "
            "reference-frame correction is N_eff x a x b in ratio units, a fifth of the uncorrected "
            "value on the one stack checked (STK-45). " + _PATTERN_ESTIMATOR
        ),
    ),
    _pattern_metric(
        STACKING_HALF_SHARED_FINE_SCALE_COVARIANCE_STACK_UNIT_SQUARED,
        "stack_unit^2",
        "The covariance of the two half stacks' fine-scale residuals, before the reference frame's noise "
        "is taken out.",
        (
            "The mean over the used pixels of odd x even, after each half is high-pass filtered and its "
            "mean taken out: c in the ratio's formula. It includes the random noise of the registration "
            "reference frame, which both halves hold. Stored so the ratio can be rebuilt and the size "
            "of the correction seen. " + _PATTERN_ESTIMATOR
        ),
    ),
    _pattern_metric(
        STACKING_HALF_FINE_SCALE_DIFFERENCE_VARIANCE_STACK_UNIT_SQUARED,
        "stack_unit^2",
        "The variance of the difference of the two half stacks' fine-scale residuals.",
        (
            "The mean over the used pixels of (odd - even)^2 after the high-pass filter: D in the ratio's "
            "formula. Divided by the variance factor F it is one frame's variance at this scale "
            "(sigma_f^2), and divided again by the effective frame count it is the random variance of "
            "the library stack, the ratio's denominator. It holds the random noise of both halves and "
            "none of what they share. " + _PATTERN_ESTIMATOR
        ),
    ),
    _pattern_metric(
        STACKING_HALF_SHARED_FINE_SCALE_REFERENCE_CORRECTION_STACK_UNIT_SQUARED,
        "stack_unit^2",
        "The part of the covariance that is the random noise of the shared registration reference frame.",
        (
            "sigma_f^2 x a x b, with sigma_f^2 = D / F and a and b the reference frame's weight shares in "
            "the odd and even halves "
            "(`stacking.half_difference_shared_frame_odd_weight_fraction` and the even one). It is "
            "subtracted from the covariance. It assumes every frame has the noise of the average "
            "frame, which is a model and not a measurement, and the reference is the sharpest frame, "
            "the one with the heaviest weight. " + _PATTERN_ESTIMATOR
        ),
    ),
    _pattern_metric(
        STACKING_WALKING_NOISE_ALONG_CORRELATION,
        "",
        "The correlation of the two half stacks' fine-scale residuals at a lag of 6 px along the drift.",
        (
            "The mean of odd x even residual products over pixel pairs 6 px apart along the drift axis "
            "(`stacking.registration_drift_direction_deg`), in both directions, divided by the geometric "
            "mean of the halves' variances. Moving the second half by a fractional lag uses bilinear "
            "interpolation, and a pair counts only when all four interpolation neighbors are in the "
            "sample. A pattern smeared along the drift keeps its correlation at this lag; sky "
            "structure does not depend on the direction. The smear's signal at the lag falls as "
            "(1 - lag / extent) of the pointing range along the drift, so a short range sees "
            "less of it. The reference frame's shared noise is not taken "
            "out, because at 6 px it is correlated only through the filter and the interpolation. "
            "Not measured when the drift axis is unknown, or when the pointings cover less than 12 px "
            "along it (twice the lag): a smear cannot reach the lag."
        ),
    ),
    _pattern_metric(
        STACKING_WALKING_NOISE_ACROSS_CORRELATION,
        "",
        "The same correlation at a lag of 6 px across the drift.",
        (
            "As `stacking.walking_noise_along_drift_correlation`, with the lag turned a quarter circle, "
            "across the drift axis."
        ),
    ),
    _pattern_metric(
        STACKING_WALKING_NOISE_CORRELATION_EXCESS,
        "",
        (
            "How much more the halves' fine-scale residuals correlate along the drift than across it: "
            "the smear of a sensor pattern."
        ),
        (
            "The along-drift correlation minus the across-drift correlation, both at 6 px. A positive "
            "excess beyond its error is a pattern smeared by the drift (walking noise) or stars "
            "elongated along the drift (a trail in the sky structure that moves with the pointing); zero "
            "means the shared structure is the same in both directions, as sky structure is. Both "
            "correlations are normalized with the same variances, so their difference is not changed "
            "by the shared reference frame's noise or by the choice of the filter width to first order."
        ),
    ),
)

STACKING_METRICS: tuple[MetricSpec, ...] = (
    *_DECLARED_BEFORE_PHASE_3,
    *_VALUE_METRICS,
    *_NOISE_AND_WEIGHT_METRICS,
    *_NIGHT_METRICS,
    *_HALF_DIFFERENCE_METRICS,
    *_SCIENCE_METRICS,
    *_FRAME_SHAPE_METRICS,
    *_FLAT_MATCH_METRICS,
    *_SHIFT_METRICS,
    *_PATTERN_METRICS,
    *_GATE_METRICS,
)

# The gate name (as in the stack summary) mapped to the metric that stores it.
GATE_METRIC_BY_NAME: dict[str, str] = {
    "gain_homogeneity": STACKING_GATE_GAIN_HOMOGENEITY,
    "frame_quarantine": STACKING_GATE_FRAME_QUARANTINE,
    "background_homogeneity": STACKING_GATE_BACKGROUND_HOMOGENEITY,
    "flat_level": STACKING_GATE_FLAT_LEVEL,
    "flat_noise": STACKING_GATE_FLAT_NOISE,
    "calibration_metadata": STACKING_GATE_CALIBRATION_METADATA,
    "calibration_frame_count": STACKING_GATE_CALIBRATION_FRAME_COUNT,
    "exposure_group_linearity": STACKING_GATE_EXPOSURE_GROUP_LINEARITY,
    "exposure_groups_combined": STACKING_GATE_EXPOSURE_GROUPS_COMBINED,
    "raw_frame_clipping": STACKING_GATE_RAW_FRAME_CLIPPING,
    "rejected_pixel_fraction": STACKING_GATE_REJECTED_PIXEL_FRACTION,
    "stack_sharpness": STACKING_GATE_STACK_SHARPNESS,
    "spectral_registration": STACKING_GATE_SPECTRAL_REGISTRATION,
    "saturated_pixel_fraction": STACKING_GATE_SATURATED_PIXEL_FRACTION,
    "zero_pixel_fraction": STACKING_GATE_ZERO_PIXEL_FRACTION,
    "negative_pixels": STACKING_GATE_NEGATIVE_PIXELS,
}
