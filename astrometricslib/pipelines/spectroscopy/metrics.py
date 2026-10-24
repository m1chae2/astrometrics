"""The quality metrics the spectroscopy pipeline declares.

Each `MetricSpec` describes one number the pipeline already worked out or one
self-check it already made. Nothing here measures anything: the `computed_by`
field points at the code that does, and `post_processing/record_quality.py`
emits the values.

There are three kinds, all about one run (subject level ``run``):

- Run totals from the summary: the worst zero-order saturation, the
  dispersion angle, the median trail width, the star identification counts and
  the classification concern counts. Each name says which estimator it keeps
  (``_max``, ``_median``, ``_first_star``) and ends with its unit.
- Roll-ups of the per-spectrum checkpoints. Every spectrum passes four
  checkpoints (raw frame, pre-processing, processing, post-processing). The
  summary keeps, for each numeric checkpoint metric, the median over the
  run's spectra. Each of those medians is declared as
  ``spectroscopy.stage_<stage>_<metric>``. The checkpoint keeps its own
  metric names; the declared name adds the unit suffix the checkpoint name
  lacks (see `stage_metric_name`). The wavelength-scale scatter and the Gaia
  XP comparison are rolled up in the same way, and declared too.
- The run's self-checks (gates), stored with their outcome and limit as
  ``spectroscopy.gate_<gate name>``. They stay self-checks: they flag the run
  for a look and say nothing about the sky.

These values are run-level and not stack-level, unlike astrometry's, for two
reasons found in the code. The session batch (`batch.py`) extracts spectra
from every frame of every session of the target and rolls them up into one
summary, so no single stack is the source of its numbers. The single-image
runner (`runner.py`) may read a stack, but it does not tell the record step
which one, and its numbers are medians over the many stars of that image, not
a property of the image file. If the pipeline later records which stack a run
read, the numbers that describe that stack alone (for example the trail
width) could move to the stack level.

Yes/no verdicts are not measurements and are not declared here (for example
``poor_match``, ``trustworthy`` and ``zero_order_saturation_flagged``). The
per-star results stay in the star records.

Names follow ``<checkpoint>.<snake_case>`` with exactly one dot, so the stage
and the metric name are joined with underscores. Units outside the registry's
list (relative RMS, subtype steps, percent per 1000 A) are declared as the
unitless ``""`` and the meaning says what the number is in.
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import (
    Checkpoint,
    Direction,
    MetricPurpose,
    MetricSpec,
    UncertaintyKind,
)

# ---- Run totals from the summary -----------------------------------------

SPECTROSCOPY_ZERO_ORDER_SATURATED_FRACTION_MAX = "spectroscopy.zero_order_saturated_max_fraction"
SPECTROSCOPY_DISPERSION_ANGLE_FIRST_STAR = "spectroscopy.dispersion_angle_first_star_deg"
SPECTROSCOPY_TRAIL_WIDTH_MEDIAN = "spectroscopy.trail_width_median_px"
SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT = "spectroscopy.catalog_matched_star_count"
SPECTROSCOPY_POSITION_ONLY_STAR_COUNT = "spectroscopy.position_only_star_count"
SPECTROSCOPY_UNRESOLVED_STAR_COUNT = "spectroscopy.unresolved_star_count"
SPECTROSCOPY_POOR_MATCH_CLASSIFICATION_COUNT = "spectroscopy.poor_match_classification_count"
SPECTROSCOPY_AMBIGUOUS_CLASSIFICATION_COUNT = "spectroscopy.ambiguous_classification_count"

# ---- Wavelength-scale roll-up --------------------------------------------

SPECTROSCOPY_WAVELENGTH_SCALE_SPECTRUM_COUNT = "spectroscopy.wavelength_scale_spectrum_count"
SPECTROSCOPY_WAVELENGTH_SCALE_APPLIED_COUNT = "spectroscopy.wavelength_scale_applied_count"
SPECTROSCOPY_WAVELENGTH_SCALE_RMS_BEFORE = "spectroscopy.wavelength_scale_rms_before_correction_angstrom"
SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER = "spectroscopy.wavelength_scale_rms_after_correction_angstrom"
SPECTROSCOPY_WAVELENGTH_SCALE_SATURATION_CORRELATION = "spectroscopy.wavelength_scale_saturation_correlation"
SPECTROSCOPY_WAVELENGTH_SCALE_CORRELATION_SPECTRUM_COUNT = (
    "spectroscopy.wavelength_scale_correlation_spectrum_count"
)

# ---- Gaia XP roll-up ------------------------------------------------------

SPECTROSCOPY_GAIA_XP_COMPARED_STAR_COUNT = "spectroscopy.gaia_xp_compared_star_count"
SPECTROSCOPY_GAIA_XP_MEDIAN_SLOPE = "spectroscopy.gaia_xp_median_slope_percent_per_1000_angstrom"
SPECTROSCOPY_GAIA_XP_MEDIAN_ABSOLUTE_SLOPE = (
    "spectroscopy.gaia_xp_median_absolute_slope_percent_per_1000_angstrom"
)

# The four comparison bands, in Angstroms, in the order the run summary keeps
# them. They are the same four as `COMPARISON_BANDS_ANGSTROM` in
# `post_processing/compare_to_gaia_xp.py`; a test checks that the two agree.
GAIA_XP_BAND_EDGES_ANGSTROM: tuple[tuple[int, int], ...] = (
    (4200, 5000),
    (5000, 6000),
    (6000, 7000),
    (7000, 8000),
)

# ---- Gates ----------------------------------------------------------------

SPECTROSCOPY_GATE_SPECTRA_EXTRACTED = "spectroscopy.gate_spectra_extracted"
SPECTROSCOPY_GATE_ZERO_ORDER_SATURATION = "spectroscopy.gate_zero_order_saturation"
SPECTROSCOPY_GATE_SPECTRAL_CLASSIFICATION = "spectroscopy.gate_spectral_classification"
SPECTROSCOPY_GATE_CATALOG_AGREEMENT = "spectroscopy.gate_catalog_agreement"
SPECTROSCOPY_GATE_FEATURE_SIGNIFICANCE = "spectroscopy.gate_feature_significance"
SPECTROSCOPY_GATE_RESOLUTION_MEASURED = "spectroscopy.gate_resolution_measured"
SPECTROSCOPY_GATE_PROCESSING_QUALITY = "spectroscopy.gate_processing_quality"
SPECTROSCOPY_GATE_WAVELENGTH_SCALE = "spectroscopy.gate_wavelength_scale"
SPECTROSCOPY_GATE_GAIA_XP_AGREEMENT = "spectroscopy.gate_gaia_xp_agreement"

_RUN_ONLY = (SubjectLevel.RUN,)
_NO_UNCERTAINTY = "No uncertainty is reported."
_EXACT_COUNT = "No uncertainty is reported. The value is an exact count."
_BAND = "Not applicable: a spectrum is not taken through a filter."
_RUNNER_CLASS = "pipelines.spectroscopy.runner.SpectroscopyPipelineAdapter"
_BATCH_SUMMARY_FUNCTION = "pipelines.spectroscopy.batch._attach_spectroscopy_quality_summary"
_RECORD_QUALITY = "pipelines.spectroscopy.post_processing.record_quality"
_STAGE_ROLLUP_FUNCTION = f"{_RECORD_QUALITY}.summarize_stage_quality"
_GATE_BUILDER = f"{_RECORD_QUALITY}.spectroscopy_run_gates"
_WAVELENGTH_SCALE_MODULE = "pipelines.spectroscopy.post_processing.wavelength_scale"
_GAIA_XP_MODULE = "pipelines.spectroscopy.post_processing.compare_to_gaia_xp"
_BOTH_PATHS = (
    " The single-image runner and the session batch (`batch.py`) both build it, "
    "from the same per-star results."
)
_GATE_PURPOSE_REASON = (
    "The run flags itself for a manual look when this check fails, so a run whose spectra "
    "cannot be trusted is told apart from one whose spectra can."
)


def _context_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    *,
    better: Direction = Direction.NONE,
    computed_by: str = _RUNNER_CLASS,
    uncertainty_method: str = _NO_UNCERTAINTY,
    register_entry: str | None = None,
) -> MetricSpec:
    """Declare one run-level context metric of the spectroscopy pipeline.

    Parameters
    ----------
    name : `str`
        The metric name.
    unit : `str`
        The unit of the value (see the module description for ``""``).
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the value is made.
    sample_definition : `str`
        What the value is made from.
    purpose_reason : `str`
        Why the value is kept.
    better : `Direction`, optional
        Which direction is better. Most have none.
    computed_by : `str`, optional
        Dotted path of the code that computes it.
    uncertainty_method : `str`, optional
        The sentence that says why there is no uncertainty.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the metric, if one does.

    Returns
    -------
    spec : `MetricSpec`
        A context metric on the run level, with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.SPECTROSCOPY,
        levels=_RUN_ONLY,
        better=better,
        meaning=meaning,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=uncertainty_method,
        register_entry=register_entry,
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=purpose_reason,
    )


def _gate_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    *,
    computed_by: str = _GATE_BUILDER,
) -> MetricSpec:
    """Declare one self-check gate of the spectroscopy pipeline as a metric.

    Parameters
    ----------
    name : `str`
        The metric name, ``spectroscopy.gate_<gate name>``.
    unit : `str`
        The unit of the value and of the limit.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        What the stored value is and how the outcome is decided.
    sample_definition : `str`
        What the check looks at.
    computed_by : `str`, optional
        Dotted path of the function that builds the gate.

    Returns
    -------
    spec : `MetricSpec`
        A decision metric on the run level with no uncertainty. No register
        entry is cited: every limit of these gates is a design estimate, listed
        in the design-estimates table of the spectroscopy register.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.SPECTROSCOPY,
        levels=_RUN_ONLY,
        better=Direction.NONE,
        meaning=meaning,
        decisions=("run flag",),
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        purpose=MetricPurpose.DECISION,
        purpose_reason=_GATE_PURPOSE_REASON,
    )


# ---- Checkpoint medians ---------------------------------------------------
#
# One row per numeric metric of a per-spectrum checkpoint:
# (stage, metric name in the checkpoint, unit, better, meaning, how one
# spectrum's value is made, validation register entry or None).
# The summary keeps only the median over the run's spectra, so each declared
# value is that median. A metric that is a yes/no verdict (unit "flag" in the
# checkpoint) is left out on purpose.

_StageRow = tuple[str, str, str, Direction, str, str, str | None]

_STAGE_ROWS: tuple[_StageRow, ...] = (
    # ---- raw frame (checkpoint 0): pre_processing/assess_raw_frame_quality.py
    (
        "raw_frame",
        "dar_along_dispersion_angstrom",
        "angstrom",
        Direction.LOWER,
        "How far atmospheric refraction stretches the wavelengths along the trail.",
        "Span of the refraction-induced wavelength error between the blue and red report wavelengths, "
        "from the plane-parallel refraction model, before any correction.",
        None,
    ),
    (
        "raw_frame",
        "target_altitude_degrees",
        "deg",
        Direction.HIGHER,
        "How high the target stood above the horizon.",
        "The altitude the refraction model used, from the site, the pointing and the exposure time.",
        None,
    ),
    (
        "raw_frame",
        "dar_across_dispersion_px",
        "px",
        Direction.NONE,
        "How much refraction widens the trail between the blue and red ends.",
        "Span of the refraction shift across the dispersion, from the refraction model.",
        None,
    ),
    (
        "raw_frame",
        "parallactic_to_dispersion_angle_degrees",
        "deg",
        Direction.NONE,
        "The angle between the direction to the zenith and the dispersion direction.",
        "Angle from the refraction model; 0 means the red end of the spectrum points at the zenith.",
        None,
    ),
    (
        "raw_frame",
        "zero_order_saturated_fraction",
        "fraction",
        Direction.LOWER,
        "The share of the star's zero-order image at the camera's saturation level.",
        "Saturated pixels divided by pixels in the zero-order box, for one star.",
        None,
    ),
    (
        "raw_frame",
        "valid_fraction",
        "fraction",
        Direction.HIGHER,
        "The share of the spectrum's samples that lie on the image and in the camera's wavelength range.",
        "Usable samples divided by all samples along the trail, for one star.",
        None,
    ),
    (
        "raw_frame",
        "median_trail_width",
        "px",
        Direction.NONE,
        "How wide the star's trail is across the dispersion.",
        "Median of the star's fitted trail widths along the trail.",
        None,
    ),
    (
        "raw_frame",
        "contaminated_sky_fraction",
        "fraction",
        Direction.LOWER,
        "The share of sky readings in which a sky strip beside the trail was contaminated.",
        "Readings where the lower or upper sky band was contaminated and dropped, divided by all "
        "sky readings.",
        "SPE-03",
    ),
    (
        "raw_frame",
        "streak_tilt",
        "deg",
        Direction.NONE,
        "How far the spectrum's streak is tilted from the pixel rows.",
        "Tilt angle from the raw frame check; present only when that check ran for the star.",
        None,
    ),
    (
        "raw_frame",
        "peak_above_sky",
        "adu",
        Direction.NONE,
        "How far the spectrum's brightest sample stands above the sky.",
        "Peak sample brightness minus the sky level, from the raw frame check; present only when it ran.",
        None,
    ),
    # ---- pre-processing (checkpoint 1): assess_input_quality.py
    (
        "pre_processing",
        "resolution_element",
        "angstrom",
        Direction.LOWER,
        "How much the instrument blurred the spectrum.",
        "The resolution element from the star's own trail width, or the 45 A fallback when the width "
        "could not be measured.",
        "SPE-05",
    ),
    (
        "pre_processing",
        "zero_order_saturated_fraction",
        "fraction",
        Direction.LOWER,
        "The share of the star's zero-order image at the saturation level.",
        "Saturated pixels divided by pixels in the zero-order box, for one star.",
        None,
    ),
    (
        "pre_processing",
        "valid_fraction",
        "fraction",
        Direction.HIGHER,
        "The share of the spectrum's samples that lie on the image and in the camera's wavelength range.",
        "Usable samples divided by all samples along the trail, for one star; the same number as at "
        "the raw frame.",
        None,
    ),
    (
        "pre_processing",
        "signal_to_noise",
        "",
        Direction.HIGHER,
        "The spectrum's signal-to-noise ratio per resolution element, from its own scatter.",
        "The post-hoc estimate: the spectrum is averaged into resolution elements, the noise is the robust "
        "spread of each element's second difference, and the signal is the middle element.",
        "SPE-09",
    ),
    (
        "pre_processing",
        "median_snr_per_resolution_element",
        "",
        Direction.HIGHER,
        "The spectrum's signal-to-noise ratio per resolution element, from the per-sample errors.",
        "Median over the signal-to-noise window of the brightness summed over one resolution element "
        "divided by the error of that sum, from the per-sample variance.",
        "SPE-09",
    ),
    (
        "pre_processing",
        "snr_at_classifying_lines",
        "",
        Direction.HIGHER,
        "The spectrum's signal-to-noise ratio per resolution element at the classifying lines.",
        "Median over the line-index cores that can be measured (H-beta, H-alpha, Mg b, Na D and the two TiO "
        "bands, those clear of the atmospheric bands) of the signal-to-noise per resolution element of the "
        "stretch within 6 resolution elements of the line, on the scatter scale: the median element over "
        "the robust scatter of the second difference of the elements, the same estimator as the class-gap "
        "table (not the extractor's per-sample errors, which read about five times higher), using the "
        "measured line spread.",
        "SPE-30",
    ),
    (
        "pre_processing",
        "fraction_samples_snr_below_5",
        "fraction",
        Direction.LOWER,
        "The share of the spectrum's samples whose brightness is under 5 times its own error.",
        "Samples below 5 times their error divided by samples in the signal-to-noise window.",
        "SPE-09",
    ),
    (
        "pre_processing",
        "snr_estimate_ratio",
        "",
        Direction.NONE,
        "How well the two signal-to-noise estimates agree.",
        "The variance-based signal-to-noise divided by the post-hoc estimate; 1 means they agree.",
        "SPE-09",
    ),
    (
        "pre_processing",
        "wavelength_zero_point_offset_angstrom",
        "angstrom",
        Direction.LOWER,
        "How far the spectrum's wavelength scale was off at the zero point.",
        "Absolute offset of the wavelength scale, measured from known lines.",
        "SPE-11",
    ),
    (
        "pre_processing",
        "wavelength_zero_point_uncertainty_angstrom",
        "angstrom",
        Direction.LOWER,
        "The error of the wavelength zero-point offset.",
        "The fit errors and the uncertainty of the lines' rest positions, combined.",
        "SPE-11",
    ),
    (
        "pre_processing",
        "wavelength_zero_point_line_count",
        "count",
        Direction.HIGHER,
        "How many lines gave the wavelength zero point.",
        "Lines that passed the significance test.",
        "SPE-11",
    ),
    (
        "pre_processing",
        "chromatic_defocus_ratio",
        "",
        Direction.NONE,
        "How much wider the trail is in the red than in the blue.",
        "Median trail FWHM at 6200 to 7000 A divided by the median trail FWHM at 4200 to 5000 A.",
        None,
    ),
    (
        "pre_processing",
        "trail_fwhm_blue_px",
        "px",
        Direction.NONE,
        "The trail's width across the dispersion in the blue.",
        "Median trail FWHM across the dispersion between 4200 and 5000 A.",
        None,
    ),
    (
        "pre_processing",
        "trail_fwhm_red_px",
        "px",
        Direction.NONE,
        "The trail's width across the dispersion in the red.",
        "Median trail FWHM across the dispersion between 6200 and 7000 A.",
        None,
    ),
    (
        "pre_processing",
        "measured_vs_stored_line_spread_ratio_halpha",
        "",
        Direction.NONE,
        "How the spectrum's own line width at H-alpha compares with the stored line-spread profile.",
        "The measured FWHM at 6563 A divided by the stored profile's FWHM there.",
        "SPE-06",
    ),
    # ---- processing (checkpoint 2): processing/assess_processing_quality.py
    (
        "processing",
        "significant_feature_count",
        "count",
        Direction.NONE,
        "How many absorption features the feature test judged significant.",
        "Features with a detected or possible verdict, among the features that were tested.",
        "SPE-24",
    ),
    (
        "processing",
        "best_feature_p_value",
        "",
        Direction.LOWER,
        "How strong the spectrum's strongest feature is, as a chance probability.",
        "The smallest feature p-value: the chance that noise like this spectrum's gives a feature "
        "this strong.",
        "SPE-24",
    ),
    (
        "processing",
        "uncalibrated_feature_count",
        "count",
        Direction.LOWER,
        "How many feature p-values assumed Gaussian noise.",
        "Features whose p-value could not be calibrated on the spectrum's own control windows.",
        "SPE-24",
    ),
    (
        "processing",
        "ebv_used",
        "mag",
        Direction.NONE,
        "The reddening removed from the spectrum before it was classified.",
        "The catalog E(B-V) of the star; empty when none was available.",
        "SPE-21",
    ),
    (
        "processing",
        "dereddening_type_shift_steps",
        "",
        Direction.NONE,
        "How much dereddening moved the best spectral type, in subtype steps.",
        "Absolute steps between the best type before and after dereddening; ten steps make one letter class.",
        "SPE-21",
    ),
    (
        "processing",
        "index_vs_template_type_steps",
        "",
        Direction.LOWER,
        "How far the line-index type is from the template-fit type, in subtype steps.",
        "Steps between the type the line indices point to and the template-fit type; ten steps make one "
        "letter class.",
        "SPE-22",
    ),
    (
        "processing",
        "median_equivalent_width_relative_error",
        "fraction",
        Direction.LOWER,
        "How well the equivalent widths of the detected features are known.",
        "Median of error divided by the absolute equivalent width, over features with a detected or "
        "possible verdict.",
        "SPE-23",
    ),
    (
        "processing",
        "hbeta_equivalent_width_angstrom",
        "angstrom",
        Direction.NONE,
        "The equivalent width of the H-beta feature; positive means a dip below the continuum.",
        "The equivalent width the feature detector measured for H-beta.",
        "SPE-23",
    ),
    (
        "processing",
        "halpha_equivalent_width_angstrom",
        "angstrom",
        Direction.NONE,
        "The equivalent width of the H-alpha feature; positive means a dip below the continuum.",
        "The equivalent width the feature detector measured for H-alpha.",
        "SPE-23",
    ),
    (
        "processing",
        "classification_rms",
        "",
        Direction.LOWER,
        "How close the best reference spectrum is to the star's spectrum, in relative RMS.",
        "The relative RMS between the spectrum and the best reference; not a probability.",
        "SPE-16",
    ),
    (
        "processing",
        "rms_gap_to_second_best",
        "",
        Direction.NONE,
        "How much closer the best reference is than the second best, in relative RMS.",
        "The second-best reference's relative RMS minus the best one's.",
        "SPE-18",
    ),
    (
        "processing",
        "rms_gap_to_next_class",
        "",
        Direction.HIGHER,
        "How much closer the best reference is than the best one of another letter class, in relative RMS.",
        "The relative RMS of the best reference of another letter class minus the best RMS.",
        "SPE-18",
    ),
    (
        "processing",
        "synthetic_b_minus_v",
        "mag",
        Direction.NONE,
        "The star's B-V color measured from its own spectrum.",
        "B-V from synthetic photometry on the calibrated spectrum.",
        "SPE-27",
    ),
    (
        "processing",
        "best_template_reduced_chi_square",
        "",
        Direction.NONE,
        "How well the best reference fits the spectrum, as a reduced chi-square.",
        "The reduced chi-square of the best reference, from the per-sample errors.",
        None,
    ),
    (
        "processing",
        "second_order_risky_fraction",
        "fraction",
        Direction.LOWER,
        "The share of the spectrum where second-order light could add a tenth of the signal.",
        "Samples where second-order light could add a tenth of the signal, divided by all samples.",
        "SPE-26",
    ),
    (
        "processing",
        "second_order_max_blue_to_red_ratio",
        "",
        Direction.LOWER,
        "The largest ratio of the brightness at half a wavelength to the brightness at that wavelength.",
        "The largest value over the spectrum of the brightness at half a wavelength divided by the "
        "brightness at that wavelength; the stored ratio is capped.",
        "SPE-26",
    ),
    (
        "processing",
        "emission_lines_detected",
        "count",
        Direction.NONE,
        "How many named emission lines or blends were detected.",
        "Lines or blends with a detected verdict from the emission-line detector.",
        "SPE-25",
    ),
    # ---- post-processing (checkpoint 3): assess_output_quality.py
    (
        "post_processing",
        "catalog_type_steps_apart",
        "",
        Direction.LOWER,
        "How far the measured spectral type is from the catalog type, in subtype steps.",
        "Absolute steps between the two types; ten steps make one letter class.",
        "SPE-19",
    ),
    (
        "post_processing",
        "gaia_xp_residual_rms_fraction",
        "fraction",
        Direction.LOWER,
        "How far the spectrum is from the star's Gaia XP spectrum.",
        "RMS of observed divided by Gaia XP, minus 1, over 4200 to 8000 A.",
        "SPE-28",
    ),
    (
        "post_processing",
        "gaia_xp_slope_percent_per_1000_angstrom",
        "",
        Direction.LOWER,
        "How much the observed over Gaia XP ratio tilts, in percent of the flux at 5500 A per 1000 A.",
        "The absolute slope of a straight-line fit to the ratio.",
        "SPE-28",
    ),
    (
        "post_processing",
        "gaia_xp_wavelength_shift_angstrom",
        "angstrom",
        Direction.LOWER,
        "How far the spectrum's features are shifted from Gaia XP's.",
        "The absolute feature shift that best matches the two spectra.",
        "SPE-28",
    ),
)

# The first column of `_STAGE_ROWS`, spelled out, with the code that makes
# the per-spectrum number for each stage.
_STAGE_DESCRIPTION: dict[str, tuple[str, str]] = {
    "raw_frame": ("raw frame (checkpoint 0)", "pre_processing/assess_raw_frame_quality.py"),
    "pre_processing": ("calibrated spectrum (checkpoint 1)", "pre_processing/assess_input_quality.py"),
    "processing": ("processing result (checkpoint 2)", "processing/assess_processing_quality.py"),
    "post_processing": ("final result (checkpoint 3)", "post_processing/assess_output_quality.py"),
}


# The checkpoints, in the order a spectrum passes through them.
STAGE_NAMES: tuple[str, ...] = tuple(_STAGE_DESCRIPTION)

# Every (checkpoint, metric name) pair that has a declared run median.
STAGE_METRIC_KEYS: tuple[tuple[str, str], ...] = tuple((row[0], row[1]) for row in _STAGE_ROWS)


# A checkpoint's metric keeps its own name inside the checkpoint (the run
# summary and the per-star records store it under that name). A registered
# name must end with its unit (see `KNOWN_UNITS` and the metric conformance
# test), so these checkpoint names get the unit suffix they lack. Each entry
# maps a checkpoint metric name to the name used after ``stage_<stage>_``.
_REGISTERED_NAME_BY_CHECKPOINT_METRIC: dict[str, str] = {
    "target_altitude_degrees": "target_altitude_deg",
    "parallactic_to_dispersion_angle_degrees": "parallactic_to_dispersion_angle_deg",
    "median_trail_width": "median_trail_width_px",
    "streak_tilt": "streak_tilt_deg",
    "peak_above_sky": "peak_above_sky_adu",
    "resolution_element": "resolution_element_angstrom",
    "fraction_samples_snr_below_5": "snr_below_5_fraction",
    "ebv_used": "ebv_used_mag",
    "median_equivalent_width_relative_error": "median_equivalent_width_relative_error_fraction",
    "synthetic_b_minus_v": "synthetic_b_minus_v_mag",
    "emission_lines_detected": "emission_lines_detected_count",
}


def stage_metric_name(stage: str, checkpoint_metric: str) -> str:
    """Give the registered name of a checkpoint metric's run median.

    Parameters
    ----------
    stage : `str`
        The checkpoint, one of ``raw_frame``, ``pre_processing``,
        ``processing`` or ``post_processing``.
    checkpoint_metric : `str`
        The metric's name inside the checkpoint (the name the summary and
        the per-star records store it under).

    Returns
    -------
    name : `str`
        ``spectroscopy.stage_<stage>_<metric>``, where ``<metric>`` is the
        checkpoint name, ending with the metric's unit.
    """
    registered = _REGISTERED_NAME_BY_CHECKPOINT_METRIC.get(checkpoint_metric, checkpoint_metric)
    return f"spectroscopy.stage_{stage}_{registered}"


def stage_spectrum_count_name(stage: str) -> str:
    """Give the registered name of a checkpoint's spectrum count.

    Parameters
    ----------
    stage : `str`
        The checkpoint.

    Returns
    -------
    name : `str`
        ``spectroscopy.stage_<stage>_spectrum_count``.
    """
    return f"spectroscopy.stage_{stage}_spectrum_count"


def gaia_xp_band_metric_name(band_index: int, quantity: str) -> str:
    """Give the registered name of one Gaia XP band quantity.

    Parameters
    ----------
    band_index : `int`
        The band, 0 to 3, in increasing wavelength.
    quantity : `str`
        ``median_ratio``, ``scatter`` or ``star_count``.

    Returns
    -------
    name : `str`
        ``spectroscopy.gaia_xp_band_<start>_<end>_<quantity>``.
    """
    start, end = GAIA_XP_BAND_EDGES_ANGSTROM[band_index]
    return f"spectroscopy.gaia_xp_band_{start}_{end}_{quantity}"


def _stage_metrics() -> tuple[MetricSpec, ...]:
    """Declare the roll-up of every numeric checkpoint metric.

    Returns
    -------
    specs : `tuple` of `MetricSpec`
        For each checkpoint, one spectrum-count metric and one median metric
        for each row of the table of checkpoint metrics.
    """
    specs: list[MetricSpec] = []
    for stage, (stage_label, _source_file) in _STAGE_DESCRIPTION.items():
        specs.append(
            _context_metric(
                stage_spectrum_count_name(stage),
                "count",
                f"How many spectra have a {stage_label} quality checkpoint.",
                f"Count of the run's spectra that carry the {stage} checkpoint.",
                "Every spectrum extracted in the run.",
                "It is the number the stage medians are read against.",
                computed_by=_STAGE_ROLLUP_FUNCTION,
                uncertainty_method=_EXACT_COUNT,
            )
        )
    for stage, key, unit, better, meaning, per_spectrum, register_entry in _STAGE_ROWS:
        stage_label, source_file = _STAGE_DESCRIPTION[stage]
        specs.append(
            _context_metric(
                stage_metric_name(stage, key),
                unit,
                meaning,
                "Median over the run's spectra of the per-spectrum value at the "
                f"{stage_label}, which `{source_file}` makes. One spectrum's value: {per_spectrum} "
                "A spectrum with no value is left out of the median, and the run has no value "
                "when no spectrum has one." + _BOTH_PATHS,
                f"The spectra of the run that have a value for this metric at the {stage} checkpoint.",
                "It shows what a typical spectrum measured at this checkpoint, so a reader can see where "
                "in the pipeline quality was lost.",
                better=better,
                computed_by=_STAGE_ROLLUP_FUNCTION,
                register_entry=register_entry,
            )
        )
    return tuple(specs)


def _gaia_xp_metrics() -> tuple[MetricSpec, ...]:
    """Declare the run roll-up of the Gaia XP comparison.

    Returns
    -------
    specs : `tuple` of `MetricSpec`
        The compared-star count, the two median tilts, and for each of the
        four bands the median ratio, its scatter and the star count.
    """
    builder = f"{_GAIA_XP_MODULE}.summarize_gaia_xp"
    specs = [
        _context_metric(
            SPECTROSCOPY_GAIA_XP_COMPARED_STAR_COUNT,
            "count",
            "How many stars were compared with a Gaia XP spectrum.",
            "Count of stars with a compared Gaia XP record. A star counts once, however many spectra it has.",
            "The stars of the run that have a Gaia DR3 XP spectrum.",
            "It is the sample size behind every Gaia XP roll-up number.",
            better=Direction.HIGHER,
            computed_by=builder,
            uncertainty_method=_EXACT_COUNT,
            register_entry="SPE-28",
        ),
        _context_metric(
            SPECTROSCOPY_GAIA_XP_MEDIAN_SLOPE,
            "",
            "The typical tilt of observed over Gaia XP, in percent of the flux at 5500 A per 1000 A.",
            "Median over the compared stars of the signed slope of a straight-line fit to the ratio. A "
            "star's slope is the median over its own spectra.",
            "The stars with a compared Gaia XP record.",
            "A tilt that is the same for most stars points at the instrument response or the airmass "
            "correction rather than at one star.",
            computed_by=builder,
            register_entry="SPE-28",
        ),
        _context_metric(
            SPECTROSCOPY_GAIA_XP_MEDIAN_ABSOLUTE_SLOPE,
            "",
            "The typical size of the tilt of observed over Gaia XP, in percent per 1000 A.",
            "Median over the compared stars of the absolute slope. The Gaia XP agreement gate judges this "
            "number.",
            "The stars with a compared Gaia XP record.",
            "It is the value the Gaia XP agreement gate is read against.",
            better=Direction.LOWER,
            computed_by=builder,
            register_entry="SPE-28",
        ),
    ]
    for band_index, (start, end) in enumerate(GAIA_XP_BAND_EDGES_ANGSTROM):
        band_text = f"{start} to {end} A"
        specs.append(
            _context_metric(
                gaia_xp_band_metric_name(band_index, "median_ratio"),
                "",
                f"The typical ratio of observed to Gaia XP flux from {band_text}.",
                "Median over the compared stars of each star's median ratio in the band, after both spectra "
                "are scaled to the same median near 5500 A. It is the residual instrument response in the "
                "band.",
                f"The stars with a value in the {band_text} band.",
                "Dividing the response-corrected spectra by this ratio would make them agree with Gaia.",
                computed_by=builder,
                register_entry="SPE-28",
            )
        )
        specs.append(
            _context_metric(
                gaia_xp_band_metric_name(band_index, "scatter"),
                "",
                f"How much the stars' ratios of observed to Gaia XP spread from {band_text}.",
                "1.4826 times the median absolute deviation of the stars' ratios about their median, which "
                "equals the standard deviation for a normal distribution.",
                f"The stars with a value in the {band_text} band; at least two are needed.",
                "A large spread means the instrument response differs from star to star.",
                better=Direction.LOWER,
                computed_by=builder,
                register_entry="SPE-28",
            )
        )
        specs.append(
            _context_metric(
                gaia_xp_band_metric_name(band_index, "star_count"),
                "count",
                f"How many stars had a Gaia XP ratio from {band_text}.",
                "Count of compared stars with a ratio in the band.",
                "The stars with a compared Gaia XP record.",
                "It is the sample size behind the band's median ratio and scatter.",
                better=Direction.HIGHER,
                computed_by=builder,
                uncertainty_method=_EXACT_COUNT,
            )
        )
    return tuple(specs)


SPECTROSCOPY_METRICS: tuple[MetricSpec, ...] = (
    _context_metric(
        SPECTROSCOPY_ZERO_ORDER_SATURATED_FRACTION_MAX,
        "fraction",
        "The worst zero-order saturation among the run's stars.",
        "Maximum over the processed stars of the share of the zero-order image's pixels at the camera's "
        "saturation level." + _BOTH_PATHS,
        "Every processed star that has a zero-order measurement.",
        "A saturated zero order may bend the wavelength zero point, so the worst star sets the run's flag.",
        better=Direction.LOWER,
    ),
    _context_metric(
        SPECTROSCOPY_DISPERSION_ANGLE_FIRST_STAR,
        "deg",
        "The tilt of the spectrum streak in the picture, taken from the first star that has one.",
        "The dispersion angle of the first star in the result list that has one. It is not an average. "
        "In the single-image runner the list is the image's stars, which share one angle fitted once "
        "for the image, so the first star stands for the image. In the session batch the list joins "
        "the frames in the order their results were stored, which follows the order the workers "
        "finished, so the star that is first can change from run to run." + _BOTH_PATHS,
        "The first star with a measured dispersion angle.",
        "A streak tilted differently from run to run points at a change in the camera or grating setup.",
    ),
    _context_metric(
        SPECTROSCOPY_TRAIL_WIDTH_MEDIAN,
        "px",
        "The typical width of the stars' trails across the dispersion.",
        "Median of every fitted trail width of every star, at every position along the trail. A width of "
        "0.0, which marks a fixed-box fallback, is left out." + _BOTH_PATHS,
        "All fitted trail widths of the run's stars.",
        "The trail width sets the extraction box and the resolution element, so it is the first number to "
        "check when a focus change is suspected.",
        computed_by=_RUNNER_CLASS,
    ),
    _context_metric(
        SPECTROSCOPY_CATALOG_MATCHED_STAR_COUNT,
        "count",
        "How many stars were matched to a known catalog name.",
        "Count of the run's stars whose identification found a catalog name.",
        "Every star of the single-image run. The session batch does not count this and gives no value.",
        "It shows how much of the run can be compared with catalog spectral types.",
        better=Direction.HIGHER,
        uncertainty_method=_EXACT_COUNT,
    ),
    _context_metric(
        SPECTROSCOPY_POSITION_ONLY_STAR_COUNT,
        "count",
        "How many stars were seen but have only a sky position.",
        "Count of the run's stars with a position but no catalog name.",
        "Every star of the single-image run. The session batch does not count this and gives no value.",
        "A star without a name cannot be compared with a catalog spectral type.",
        uncertainty_method=_EXACT_COUNT,
    ),
    _context_metric(
        SPECTROSCOPY_UNRESOLVED_STAR_COUNT,
        "count",
        "How many stars could not be identified at all.",
        "Count of the run's stars that identification could place neither by name nor by position.",
        "Every star of the single-image run. The session batch does not count this and gives no value.",
        "It shows how much of the run could not be identified.",
        better=Direction.LOWER,
        uncertainty_method=_EXACT_COUNT,
    ),
    _context_metric(
        SPECTROSCOPY_POOR_MATCH_CLASSIFICATION_COUNT,
        "count",
        "How many classified stars have a poor best match.",
        "Count of the flagged classifications whose reason names a poor match: the best reference is "
        "farther than the no-good-match limit in relative RMS." + _BOTH_PATHS,
        "The classified stars of the run.",
        "It says how many spectral types in the run should be double-checked.",
        better=Direction.LOWER,
        computed_by="pipelines.spectroscopy.post_processing.assess_output_quality.is_classification_poor_match",
        uncertainty_method=_EXACT_COUNT,
        register_entry="SPE-16",
    ),
    _context_metric(
        SPECTROSCOPY_AMBIGUOUS_CLASSIFICATION_COUNT,
        "count",
        "How many classified stars are ambiguous between spectral classes.",
        "Count of the flagged classifications whose reason names class ambiguity: the best reference of "
        "another letter class is within the ambiguity gap in relative RMS." + _BOTH_PATHS,
        "The classified stars of the run.",
        "It says how many spectral types in the run could be of another letter class.",
        better=Direction.LOWER,
        computed_by=(
            "pipelines.spectroscopy.post_processing.assess_output_quality.is_classification_class_ambiguous"
        ),
        uncertainty_method=_EXACT_COUNT,
        register_entry="SPE-18",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_SPECTRUM_COUNT,
        "count",
        "How many spectra had a measured wavelength zero-point offset.",
        "Count of the spectra whose pre-processing checkpoint holds an offset.",
        "The spectra of the run with a measured offset.",
        "It is the sample size behind the wavelength-scale numbers.",
        better=Direction.HIGHER,
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        uncertainty_method=_EXACT_COUNT,
        register_entry="SPE-11",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_APPLIED_COUNT,
        "count",
        "How many of those spectra had the offset removed from their wavelengths.",
        "Count of the spectra with a measured offset whose offset the pipeline applied.",
        "The spectra of the run with a measured offset.",
        "A spectrum whose offset was not applied keeps its full offset in the after-correction scatter.",
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        uncertainty_method=_EXACT_COUNT,
        register_entry="SPE-11",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_RMS_BEFORE,
        "angstrom",
        "How much the spectra's wavelength zero points scatter before any correction.",
        "Root mean square of the absolute offsets of the spectra with a measured offset.",
        "The spectra of the run with a measured offset.",
        "It shows how far off the wavelength scales were before the pipeline's correction.",
        better=Direction.LOWER,
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        register_entry="SPE-11",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_RMS_AFTER,
        "angstrom",
        "How much the spectra's wavelength zero points scatter after the pipeline's correction.",
        "Root mean square, over the spectra with a measured offset, of what is left after correction: the "
        "offset's uncertainty for a spectrum whose offset was applied, and the whole offset for any other.",
        "The spectra of the run with a measured offset.",
        "It is the number the wavelength-scale gate is judged on.",
        better=Direction.LOWER,
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        register_entry="SPE-11",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_SATURATION_CORRELATION,
        "",
        "How closely a spectrum's wavelength offset follows its zero-order saturation.",
        "Pearson correlation, from -1 to 1, between a spectrum's absolute offset and its zero-order "
        "saturated fraction. The absolute offset is used because saturation can pull the zero order "
        "either way.",
        "The spectra with both an offset and a saturation value; at least three, and neither list constant.",
        "A strong positive value says saturation is bending the wavelength scale.",
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        register_entry="SPE-11",
    ),
    _context_metric(
        SPECTROSCOPY_WAVELENGTH_SCALE_CORRELATION_SPECTRUM_COUNT,
        "count",
        "How many spectra went into the offset and saturation correlation.",
        "Count of the spectra with a saturation value next to a measured offset.",
        "The spectra of the run with a measured offset.",
        "It is the sample size behind the correlation.",
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.summarize_wavelength_scale",
        uncertainty_method=_EXACT_COUNT,
        register_entry="SPE-11",
    ),
    *_gaia_xp_metrics(),
    *_stage_metrics(),
    _gate_metric(
        SPECTROSCOPY_GATE_SPECTRA_EXTRACTED,
        "count",
        "Whether any star produced a spectrum.",
        "The value is the number of spectra. The limit is 1. The check passes at or above 1 and fails "
        "with none.",
        "The stars the run processed.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_ZERO_ORDER_SATURATION,
        "fraction",
        "Whether the zero-order image of any star was saturated.",
        "The value is the worst saturated share among the stars' zero-order images. The limit is the "
        "saturation flag level (a guess, not validated). The check fails above the limit and is not "
        "checked when no star had a zero-order measurement.",
        "Every processed star that has a zero-order measurement.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_SPECTRAL_CLASSIFICATION,
        "count",
        "Whether any spectral type is a poor match or ambiguous between letter classes.",
        "The value is the number of stars whose type is a poor match or class-ambiguous. The limit is 0. "
        "The check fails above 0 and is not checked when no star was given a type. Stars ambiguous only "
        "between neighboring subtypes are named in the detail, not counted.",
        "The classified stars of the run.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_CATALOG_AGREEMENT,
        "count",
        "Whether any measured spectral type disagrees with the catalog.",
        "The value is the number of stars whose measured type is too many subtype steps from the catalog "
        "type. The limit is 0. The check fails above 0 and is not checked when no star had a catalog type.",
        "The stars that have both a measured and a catalog spectral type.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_FEATURE_SIGNIFICANCE,
        "count",
        "Whether the feature p-values were calibrated on each spectrum's own noise.",
        "The value is the number of stars with a feature p-value that fell back to Gaussian noise. The "
        "limit is 0. The check fails above 0 and is not checked when no star had its features tested.",
        "The stars whose absorption features were tested.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_RESOLUTION_MEASURED,
        "count",
        "Whether the instrument resolution was measured for any spectrum.",
        "The value is the number of spectra whose resolution was measured from their own trail. The limit "
        "quoted is the number of spectra, for context. The check passes when at least one was measured, "
        "and is not checked when none was or no spectrum exists.",
        "The spectra of the run.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_PROCESSING_QUALITY,
        "fraction",
        "Whether too many classified spectra failed a processing-checkpoint metric.",
        "The value is the share of classified spectra with a failed processing metric. The limit is 0.5 "
        "(a designed share, not validated). The check fails above the limit and is not checked when no "
        "classified spectrum had a processing checkpoint.",
        "The classified spectra of the run.",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_WAVELENGTH_SCALE,
        "angstrom",
        "Whether the wavelength scale scatters between spectra.",
        "The value is the RMS of the wavelength offsets left after correction. The limit is 11 A, one "
        "pixel of dispersion (designed, not measured). The check fails above the limit and is not "
        "checked when fewer than three spectra had a measured offset.",
        "The spectra of the run with a measured wavelength offset.",
        computed_by=f"{_WAVELENGTH_SCALE_MODULE}.wavelength_scale_gate",
    ),
    _gate_metric(
        SPECTROSCOPY_GATE_GAIA_XP_AGREEMENT,
        "",
        "Whether the spectra agree with Gaia XP, in percent of the flux at 5500 A per 1000 A.",
        "The value is the median absolute tilt of observed over Gaia XP across the compared stars. The "
        "limit is 3 percent per 1000 A (designed, not yet checked against real spectra). The check fails "
        "above the limit and is not checked with fewer than three compared stars.",
        "The stars compared with a Gaia XP spectrum.",
        computed_by=f"{_GAIA_XP_MODULE}.gaia_xp_gate",
    ),
)
