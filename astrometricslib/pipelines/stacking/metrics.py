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
    "Up to 100 circular apertures (radius 4 px) placed once per target at sky positions away from "
    "every detected star, and reused by every later stack of the target. Apertures that fall off the "
    "stack or on empty (zero) edge pixels are dropped. No two apertures are closer than 24 px, so no two "
    "sky rings share a pixel. A stack needs at least 20 usable apertures. The target keeps one set of "
    "apertures for each camera and optic (see `stack_noise.reset_apertures`)."
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
            "aperture area. Signed, so the spread is not cut at zero. The stack's pixel scale is "
            "Siril's, so compare stacks only through the normalized value."
        ),
        _NOISE_SAMPLE,
        "pipelines.stacking.post_processing.stack_noise.measure_stack_noise",
        better=Direction.LOWER,
        purpose_reason=_NOISE_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=_NOISE_UNCERTAINTY,
        definition_version=2,
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
        definition_version=2,
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
        definition_version=2,
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
        purpose_reason=_NIGHT_PURPOSE_REASON,
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
        purpose_reason=_NIGHT_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma: as for `stacking.empty_aperture_noise_normalized`. This is the "
            "error of this stack's value on its own; it is not the error of a comparison with the "
            "library stack, which the two stacks' shared frames make smaller (see "
            "`stacking.noise_difference_without_night`). The stacker's own run-to-run differences are "
            "not included."
        ),
        definition_version=2,
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
        "pipelines.stacking.post_processing.stack_noise.paired_noise_difference",
        better=Direction.NONE,
        purpose_reason=_NIGHT_PURPOSE_REASON,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=(
            "Statistical, 1 sigma, paired: the apertures are resampled with replacement 1000 times (a fixed "
            "seed), the same draw for both stacks, the difference of the two noises is found for each "
            "draw, and the uncertainty is the standard deviation of those differences. Because the "
            "stacks share frames and apertures, this is smaller than the two independent errors added in "
            "quadrature. The stacker's run-to-run differences are not included."
        ),
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
        purpose_reason=_NIGHT_PURPOSE_REASON,
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
        "The one aperture shape of `stack_noise.py` (radius 4 px, ring 7 to 12 px).",
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
        "pipelines.stacking.post_processing.stack_noise.sky_level_of_plane",
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

STACKING_METRICS: tuple[MetricSpec, ...] = (
    *_DECLARED_BEFORE_PHASE_3,
    *_VALUE_METRICS,
    *_NOISE_AND_WEIGHT_METRICS,
    *_NIGHT_METRICS,
    *_SCIENCE_METRICS,
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
