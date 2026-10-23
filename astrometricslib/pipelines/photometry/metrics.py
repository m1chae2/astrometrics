"""The quality metrics the photometry pipeline declares.

Each `MetricSpec` describes one number the pipeline already counted or
measured, or one self-check it already made. Nothing here measures anything:
the `computed_by` field points at the code that does, and
`post_processing/record_quality.py` emits the values.

There are three kinds.

- The run counts (stars, frames, candidates, identifications) come from the
  run's quality summary.
- The run measurements are numbers about the whole run: the median scatter of
  the light curves, the comparison stars' scatter against what their errors
  predict, the worst corner displacement of the aligned frames and the share
  of star measurements that kept the aligned position.
- The ``gate_*`` metrics are the run's eleven self-checks (see
  `record_quality.photometry_run_gates`), stored with their outcome (passed,
  failed or not checked) and the limit each used. They stay self-checks: they
  flag the run for a look and say nothing about the sky.

Five more come from the catalog zero point work (see
`processing/catalog_zero_point.py`). They are about one frame each: its zero
point in one catalog band, the error of the zero point's absolute level from
the star sample, and its color slope. They are exploratory or context: they
measure and do not judge. A night's summary of them is made when it is read,
on the observatory side.

All the others are about one run, so their subject level is ``run``. Per-star
results (a star's CV, its variability score, its periods and its two period
checks) stay on the star records. The noise-model curve stays in the quality
summary because it is a list of points, not a number.

Names follow ``<checkpoint>.<snake_case>``, so a gate's name is joined to the
prefix ``gate_`` (for example ``photometry.gate_registration_drift``).
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

PHOTOMETRY_STARS_PROCESSED = "photometry.stars_processed_count"
PHOTOMETRY_STARS_FOUND = "photometry.stars_found_count"
PHOTOMETRY_FRAMES_PROCESSED = "photometry.frames_processed_count"
PHOTOMETRY_VARIABLE_CANDIDATE_COUNT = "photometry.variable_candidate_count"
PHOTOMETRY_CROSS_SESSION_MATCH_COUNT = "photometry.cross_session_match_count"
PHOTOMETRY_LONG_TERM_VARIABLE_CANDIDATE_COUNT = "photometry.long_term_variable_candidate_count"
PHOTOMETRY_ASTROMETRY_IDENTIFIED_STAR_COUNT = "photometry.astrometry_identified_star_count"
PHOTOMETRY_CATALOG_MATCHED_STAR_COUNT = "photometry.catalog_matched_star_count"
PHOTOMETRY_POSITION_ONLY_STAR_COUNT = "photometry.position_only_star_count"
PHOTOMETRY_UNRESOLVED_STAR_COUNT = "photometry.unresolved_star_count"
PHOTOMETRY_STARS_WITH_SCATTER = "photometry.stars_with_light_curve_scatter_count"
PHOTOMETRY_NOISE_MODEL_STAR_COUNT = "photometry.noise_model_star_count"
PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG = "photometry.light_curve_scatter_rms_median_mag"
PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED = "photometry.comparison_scatter_over_predicted_worst"
PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX = "photometry.corner_displacement_p95_worst_px"
PHOTOMETRY_CENTROID_FALLBACK_FRACTION = "photometry.centroid_fallback_worst_fraction"

PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG = "photometry.catalog_zero_point_gaia_g_mag"
PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG = "photometry.catalog_zero_point_simbad_v_mag"
PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE = "photometry.catalog_zero_point_color_slope"
PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG = (
    "photometry.catalog_zero_point_star_sample_error_gaia_g_mag"
)
PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG = (
    "photometry.catalog_zero_point_star_sample_error_simbad_v_mag"
)

# The metrics that come from the catalog zero point work. They are about
# frames, and a record step with no zero points emits none.
PHOTOMETRY_ZERO_POINT_METRIC_NAMES: frozenset[str] = frozenset({
    PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG,
    PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG,
})

PHOTOMETRY_GATE_ENSEMBLE_FRAME_REJECTION = "photometry.gate_ensemble_frame_rejection"
PHOTOMETRY_GATE_CAPTURE_TIMESTAMPS = "photometry.gate_capture_timestamps"
PHOTOMETRY_GATE_SESSION_CONTENT = "photometry.gate_session_content"
PHOTOMETRY_GATE_SESSION_PLATE_SOLVE = "photometry.gate_session_plate_solve"
PHOTOMETRY_GATE_PHOTOMETRY_WORK = "photometry.gate_photometry_work"
PHOTOMETRY_GATE_COMPARISON_ENSEMBLE = "photometry.gate_comparison_ensemble"
PHOTOMETRY_GATE_REGISTRATION_DRIFT = "photometry.gate_registration_drift"
PHOTOMETRY_GATE_SCATTER_POPULATION = "photometry.gate_scatter_population"
PHOTOMETRY_GATE_VARIABILITY_DISCRIMINATION = "photometry.gate_variability_discrimination"
PHOTOMETRY_GATE_DETECTABLE_AMPLITUDE = "photometry.gate_detectable_amplitude"
PHOTOMETRY_GATE_FLUX_UNCERTAINTY = "photometry.gate_flux_uncertainty"

_RUN_ONLY = (SubjectLevel.RUN,)
_NO_UNCERTAINTY = "No uncertainty is reported. The value is an exact count."
_NO_UNCERTAINTY_MEASURED = (
    "No uncertainty is reported. The value is a summary of per-star or per-session numbers, "
    "and the pipeline does not propagate their errors into it."
)
_BAND = "The filter the run was asked for. Every light frame of the target if no filter was named."
_ADAPTER_CLASS = "pipelines.photometry.runner.PhotometryPipelineAdapter"
_GATE_BUILDER = "pipelines.photometry.post_processing.record_quality.photometry_run_gates"
_GATE_PURPOSE_REASON = (
    "The run flags itself for a manual look when this check fails, and a check that could not look "
    "is told apart from one that passed."
)


def _count_metric(
    name: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    *,
    better: Direction = Direction.NONE,
    computed_by: str = _ADAPTER_CLASS,
) -> MetricSpec:
    """Declare one per-run count of the photometry pipeline.

    Parameters
    ----------
    name : `str`
        The metric name.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the count is made.
    sample_definition : `str`
        What is counted.
    purpose_reason : `str`
        Why the count is kept.
    better : `Direction`, optional
        Which direction is better. Most counts have none.
    computed_by : `str`, optional
        Dotted path of the code that counts it.

    Returns
    -------
    spec : `MetricSpec`
        A context metric on the run level, unit ``count``, with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit="count",
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_RUN_ONLY,
        better=better,
        meaning=meaning,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=purpose_reason,
    )


def _measurement_metric(
    name: str,
    unit: str,
    meaning: str,
    estimator: str,
    sample_definition: str,
    purpose_reason: str,
    computed_by: str,
    *,
    better: Direction = Direction.LOWER,
    register_entry: str | None = None,
) -> MetricSpec:
    """Declare one per-run measurement that is not a count.

    Parameters
    ----------
    name : `str`
        The metric name.
    unit : `str`
        The unit of the value.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        How the value is found.
    sample_definition : `str`
        What the value summarizes.
    purpose_reason : `str`
        Why the value is kept.
    computed_by : `str`
        Dotted path of the code that finds it.
    better : `Direction`, optional
        Which direction is better. Lower by default.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the value, if one does.

    Returns
    -------
    spec : `MetricSpec`
        A context metric on the run level with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_RUN_ONLY,
        better=better,
        meaning=meaning,
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=computed_by,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY_MEASURED,
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
    register_entry: str | None = None,
) -> MetricSpec:
    """Declare one self-check gate of the photometry pipeline as a metric.

    Parameters
    ----------
    name : `str`
        The metric name, ``photometry.gate_<gate name>``.
    unit : `str`
        The unit of the value and of the limit.
    meaning : `str`
        One line in plain words.
    estimator : `str`
        What the stored value is and how the outcome is decided.
    sample_definition : `str`
        What the check looks at.
    register_entry : `str` or `None`, optional
        The validation register entry that backs the limit, if one does.

    Returns
    -------
    spec : `MetricSpec`
        A decision metric on the run level with no uncertainty.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit=unit,
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_RUN_ONLY,
        better=Direction.NONE,
        meaning=meaning,
        decisions=("run flag",),
        estimator=estimator,
        sample_definition=sample_definition,
        band=_BAND,
        computed_by=_GATE_BUILDER,
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY,
        register_entry=register_entry,
        purpose=MetricPurpose.DECISION,
        purpose_reason=_GATE_PURPOSE_REASON,
    )


_ZERO_POINT_REVIEW_DATE = datetime.date(2027, 4, 30)
_ZERO_POINT_FRAME_ONLY = (SubjectLevel.FRAME,)
_ZERO_POINT_ESTIMATOR = (
    "A differential zero point from a fixed star set. The session's stars of the chosen band that lie in "
    "the central region (within 0.5 of the half-diagonal of the frame from its center) and pass the cuts "
    "(positive flux, not saturated, peak pixel not above the camera profile's photometric linearity "
    "limit, flux at least 20 times its own error) in at least 80 percent of the frames form the set; "
    "fewer than 10 give no value. A star's offset in a frame is its catalog magnitude plus 2.5 log10 of "
    "its flux in ADU per second. Its reference is the median of its offsets over the session. The session "
    "level is the median of the references. The value is the level plus the median, over the stars that "
    "pass in this frame (at least 10), of the offset minus the star's reference. Gaia G is used when its "
    "set has at least 10 stars, else SIMBAD V. The value is for a star of the field's median color, with "
    "no color, extinction or aperture correction. The aperture is 4 px in radius with no aperture "
    "correction, so the value falls as the stars widen: about 0.1 to 0.2 mag at a star width of 3.3 px. "
    "The frame is not flat-fielded, so the central region carries whatever vignetting is left. The flux "
    "errors behind the signal-to-noise cut use the camera profile's gain; when no gain is known they "
    "assume 1 e-/ADU and the row is marked assumed."
)
_ZERO_POINT_SAMPLE = (
    "The session's catalog stars of one band, in the central region of the frame, with an unambiguous "
    "catalog match, that pass the cuts in at least 80 percent of the session's frames."
)
_ZERO_POINT_UNCERTAINTY = (
    "The standard error of the median of the stars' offset changes in this frame: 1.2533 times the "
    "robust sigma (1.4826 times the median absolute deviation) over the square root of the star count. "
    "It is the frame-to-frame precision, because the same stars are used each time. It does not include "
    "the error of the absolute level from the star sample (see the star sample error metric of the "
    "same band) or any systematic effect (color, L filter against the catalog band, aperture loss, "
    "unflattened frame, mixed SIMBAD sources)."
)
_ZERO_POINT_QUESTION = (
    "Does the differential zero point of a frame stay steady over a night, and does it fall when cloud "
    "or haze passes, once the change of star width with focus and seeing is accounted for? The "
    "observatory has no night labeled photometric, so the value has not been checked against a truth."
)
_ZERO_POINT_COMPUTED_BY = "pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates"


def _zero_point_metric(name: str, band_name: str, filter_text: str) -> MetricSpec:
    """Declare the frame zero point against one catalog band.

    Parameters
    ----------
    name : `str`
        The metric name.
    band_name : `str`
        The catalog band, as the meaning line words it.
    filter_text : `str`
        The band entry: which catalog magnitude it is tied to.

    Returns
    -------
    spec : `MetricSpec`
        An exploratory frame metric in magnitudes with a standard error.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit="mag",
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_ZERO_POINT_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            f"The magnitude that one ADU per second has in one frame, tied to {band_name} magnitudes, for a "
            "star of the field's median color, with no color, extinction or aperture correction."
        ),
        estimator=_ZERO_POINT_ESTIMATOR,
        sample_definition=_ZERO_POINT_SAMPLE,
        band=filter_text,
        computed_by=_ZERO_POINT_COMPUTED_BY,
        uncertainty_kind=UncertaintyKind.STATISTICAL_1_SIGMA,
        uncertainty_method=_ZERO_POINT_UNCERTAINTY,
        register_entry="PHO-15",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=_ZERO_POINT_QUESTION,
        review_date=_ZERO_POINT_REVIEW_DATE,
    )


def _star_sample_error_metric(name: str, band_name: str, zero_point_name: str) -> MetricSpec:
    """Declare the star-sampling error of one band's absolute zero point level.

    Parameters
    ----------
    name : `str`
        The metric name.
    band_name : `str`
        The catalog band, as the meaning line words it.
    zero_point_name : `str`
        The name of the zero point metric of the same band.

    Returns
    -------
    spec : `MetricSpec`
        A context frame metric in magnitudes.
    """
    return MetricSpec(
        name=name,
        definition_version=1,
        unit="mag",
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_ZERO_POINT_FRAME_ONLY,
        better=Direction.NONE,
        meaning=(
            f"The statistical error of the absolute level of the {band_name} zero point from the star "
            "sample; systematics (color, L against the catalog band, aperture loss, unflattened frame, "
            "mixed SIMBAD sources) are not included."
        ),
        estimator=(
            "1.2533 times the robust sigma (1.4826 times the median absolute deviation) of the fixed "
            "stars' reference offsets over the square root of the star count. It is the same for every "
            f"frame of a session and is stored with each frame that has a value of {zero_point_name}."
        ),
        sample_definition="The session's fixed star set of the band (see the zero point metric).",
        band=f"{band_name} against the frame's own filter.",
        computed_by="pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method=_NO_UNCERTAINTY_MEASURED,
        register_entry="PHO-15",
        purpose=MetricPurpose.CONTEXT,
        purpose_reason=(
            "A reader comparing the absolute zero point of two sessions or two fields needs the error of "
            "the star sample beside it, because that error is much larger than the frame-to-frame "
            "precision stored with the zero point."
        ),
    )


PHOTOMETRY_METRICS: tuple[MetricSpec, ...] = (
    _count_metric(
        PHOTOMETRY_STARS_PROCESSED,
        "How many stars the run measured a light curve for.",
        "Count of the stars in the run's result after sessions were merged. It equals the stars-found "
        "metric; the quality summary stores the same number under both names.",
        "Every star the run kept, merged across sessions.",
        "It says how large the field's sample was; every other count is read against it.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_STARS_FOUND,
        "How many stars the run found.",
        "Count of the stars in the run's result after sessions were merged. It equals the "
        "stars-processed metric; the quality summary stores the same number under both names.",
        "Every star the run kept, merged across sessions.",
        "It is kept next to the summary field of the same name.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_FRAMES_PROCESSED,
        "How many frames contributed to the run's light curves.",
        "Frames in all sessions, minus the frames rejected as global ensemble outliers, minus the "
        "frames left out for an unreadable DATE-OBS header.",
        "Every frame of every session of the run.",
        "A run with few frames says little about variability, and the rejection gate is read against it.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_VARIABLE_CANDIDATE_COUNT,
        "How many stars the variable-star rule flagged, before sessions were merged.",
        "Count of the candidates the per-session variability analysis returned, summed over sessions. "
        "A star flagged in two sessions counts twice.",
        "The stars of every session that passed the candidate rule.",
        "A count is only a count: it must be read with the detectable-amplitude and discrimination checks.",
    ),
    _count_metric(
        PHOTOMETRY_CROSS_SESSION_MATCH_COUNT,
        "How many stars were matched between observing sessions by sky position.",
        "Count of the stars that appear in more than one session after the cross-session match.",
        "The stars of every session of a run that has more than one.",
        "A match count of zero for a multi-session run means the sessions were not joined.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_LONG_TERM_VARIABLE_CANDIDATE_COUNT,
        "How many stars varied between sessions.",
        "Count of the candidates found by comparing each star's mean brightness across sessions.",
        "The stars matched across sessions.",
        "These are the variables a single night cannot show, kept next to the single-night count.",
    ),
    _count_metric(
        PHOTOMETRY_ASTROMETRY_IDENTIFIED_STAR_COUNT,
        "How many stars were named by solving the field's coordinates.",
        "Sum, over the sessions the run plate-solved, of the stars the solve matched to SIMBAD.",
        "The stars of the sessions that were plate-solved by the run.",
        "It shows how much of the naming rests on the run's own plate solution.",
    ),
    _count_metric(
        PHOTOMETRY_CATALOG_MATCHED_STAR_COUNT,
        "How many stars matched a catalog entry.",
        "Count of the stars in the catalog-matched bucket of the star identification step.",
        "The stars of the run.",
        "It is the first of three identification buckets and is read with the other two.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_POSITION_ONLY_STAR_COUNT,
        "How many stars were seen but matched no catalog entry.",
        "Count of the stars in the position-only bucket of the star identification step.",
        "The stars of the run.",
        "It is the second of three identification buckets and is read with the other two.",
    ),
    _count_metric(
        PHOTOMETRY_UNRESOLVED_STAR_COUNT,
        "How many stars could not be placed on the sky.",
        "Count of the stars in the unresolved bucket of the star identification step.",
        "The stars of the run.",
        "It is the third of three identification buckets and is read with the other two.",
        better=Direction.LOWER,
    ),
    _count_metric(
        PHOTOMETRY_STARS_WITH_SCATTER,
        "How many stars had a measured scatter.",
        "Count of the stars whose coefficient of variation was found, which needs a light curve with "
        "three or more usable points.",
        "The stars of the run.",
        "It is the number the scatter-population check is read against.",
        better=Direction.HIGHER,
    ),
    _count_metric(
        PHOTOMETRY_NOISE_MODEL_STAR_COUNT,
        "How many stars the run's noise model could be fitted from.",
        "Count of the stars that kept an instrumental magnitude and a nonzero scatter from the "
        "per-session analysis, which the noise-model fit takes as input.",
        "The stars of the run.",
        "A thin sample makes the noise model unreliable, and the scatter-population check reads it.",
        better=Direction.HIGHER,
    ),
    _measurement_metric(
        PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG,
        "mag",
        "The typical RMS scatter of a star's light curve in this run, in magnitudes.",
        "For each star with three or more usable points, the sample standard deviation (RMS about the "
        "mean) of its detrended (else normalized, else raw) flux divided by the mean flux, turned into "
        "magnitudes as 2.5 log10(1 + ratio). The value is the median of those per-star numbers over "
        "the stars, rounded to 4 decimals. The summary field `light_curve_scatter_rms_mag` holds this "
        "same median, despite the shorter name. The star records were not screened for variables "
        "first, so a few variables lift it only a little.",
        "The stars with three or more positive flux points.",
        "It sets what variability the run could ever detect: a 0.3 mag scatter hides a 0.05 mag variable.",
        "pipelines.photometry.processing.variability_analyzer.median_light_curve_scatter_mag",
    ),
    _measurement_metric(
        PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED,
        "",
        "How many times more the comparison stars scatter than their errors predict, in the worst session.",
        "For each session, the median scatter of its comparison stars' normalized light curves "
        "(2.5 log10(1 + CV)) divided by the error the propagated uncertainties predict for the same points. "
        "The value is the largest ratio over the sessions. A session with no predicted error is left out.",
        "The comparison set of every session that was normalized.",
        "A clear night gives 1.3 to 1.7, patchy cloud far more, and the comparison-ensemble check "
        "fails above 5.",
        "pipelines.photometry.post_processing.record_quality.worst_comparison_scatter_ratio",
        register_entry="PHO-02",
    ),
    _measurement_metric(
        PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX,
        "px",
        "How far rotation and scale change moved a corner of the field, in the worst session.",
        "For each session, the 95th percentile over its frames of the displacement of a frame corner by "
        "the fitted rotation and scale change (a pure shift gives zero). The value is the largest over "
        "the sessions. Sessions with no rotation and scale fitted are left out.",
        "The aligned frames of every session.",
        "A field that turned lands each star on different pixels, whose flat-field error differs from "
        "frame to frame, and the registration check fails above 1 px.",
        "pipelines.photometry.pre_processing.frame_photometry.summarize_centroid_shifts",
        register_entry="PHO-04",
    ),
    _measurement_metric(
        PHOTOMETRY_CENTROID_FALLBACK_FRACTION,
        "fraction",
        "The share of star measurements that kept the aligned position, in the worst session.",
        "For each session, the number of star measurements whose own centroid was refused divided by "
        "the number of star measurements. The value is the largest share over the sessions.",
        "Every star in every aligned frame of every session.",
        "Above one in five, the global shift does not fit the stars well enough to trust the rest, and the "
        "registration check fails there.",
        "pipelines.photometry.pre_processing.frame_photometry.summarize_centroid_shifts",
        register_entry="PHO-04",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_ENSEMBLE_FRAME_REJECTION,
        "fraction",
        "Whether many frames were thrown out as global ensemble outliers.",
        "The value is the rejected frames divided by the frames that contributed. The limit is 0.25. The "
        "check fails when at least 5 frames and at least that share were rejected, and is not checked "
        "with fewer than 5 frames.",
        "The frames of all sessions.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_CAPTURE_TIMESTAMPS,
        "count",
        "Whether every frame had a usable capture time.",
        "The value is the number of frames left out for a missing or unreadable capture time. The "
        "limit is 0. The check fails above 0.",
        "The frames of all sessions.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_SESSION_CONTENT,
        "count",
        "Whether every session produced light curves.",
        "The gate stores no number. It fails when any session produced no light curves and is not checked "
        "when the run had no sessions, so the stored value is always not measured.",
        "The sessions of the run.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_SESSION_PLATE_SOLVE,
        "count",
        "Whether every session could be plate-solved for cross-session matching.",
        "The value is the number of sessions that could not be plate-solved. The limit is 0. The check "
        "fails above 0 and is not checked for a single session.",
        "The sessions of the run.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_PHOTOMETRY_WORK,
        "",
        "Whether the run found any frames to measure.",
        "The gate stores no number. It fails when stage 1 found nothing to measure (no frame in the "
        "filter, or no frame with a capture time), so the stored value is always not measured.",
        "The frames the run was given.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_COMPARISON_ENSEMBLE,
        "count",
        "Whether each session's comparison set was large enough, fixed and well behaved.",
        "The value is the number of stars in the smallest session's comparison set. The limit is 5. The "
        "check fails when a set is below the limit, when a set changes size from frame to frame, or "
        "when the comparison stars scatter more than 5 times what their errors predict; the ratio "
        "is stored by the comparison-scatter metric. It is not checked when no session was normalized.",
        "The comparison set of every session that was normalized.",
        register_entry="PHO-02",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_REGISTRATION_DRIFT,
        "px",
        "Whether the frames stayed lined up with each other.",
        "The value is the largest frame-to-frame alignment offset of any star, in pixels. The limit is "
        "20 px. The check fails when that offset passes the limit, when the corner displacement or the "
        "centroid fallback share passes its own limit (stored by their own metrics), so the outcome can "
        "be failed with the value under the limit. It is not checked when no star recorded a drift and no "
        "session has a per-star summary.",
        "Every star of the run and the aligned frames of every session.",
        register_entry="PHO-04",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_SCATTER_POPULATION,
        "count",
        "Whether enough stars have a measured scatter to say what normal scatter is.",
        "The value is the number of stars counted by the test that applied: the stars with a scatter "
        "against the limit 10, or, once a noise model was attempted, the stars it was fitted from "
        "against its own limit. The limit is stored next to the value, so the two are told apart. The "
        "check is not checked below the limit and never fails.",
        "The stars with three or more points, or the stars the noise model can use.",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_VARIABILITY_DISCRIMINATION,
        "",
        "Whether the variability score ranks cataloged variables above other stars.",
        "The value is the area under the ROC curve (AUC) of the score against the catalog labels. The "
        "limit is 0.7. The check fails when the AUC is under the limit or not above chance at the 5% "
        "level, and is not checked with fewer than 10 cataloged variables or 30 unlisted stars.",
        "The stars with a light curve that a catalog lists as variable, against those no catalog lists.",
        register_entry="PHO-08",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_DETECTABLE_AMPLITUDE,
        "mag",
        "Whether the run's noise level would let a small variable be flagged.",
        "The value is the smallest peak-to-peak amplitude of a sinusoid the run's noise model (else its "
        "CV cutoff) could flag. The limit is 0.3 mag. The check fails above the limit and is not checked "
        "when the run has neither a noise model nor a cutoff.",
        "The stars of the run, through the noise model or the cutoff.",
        register_entry="PHO-09",
    ),
    _gate_metric(
        PHOTOMETRY_GATE_FLUX_UNCERTAINTY,
        "mag",
        "Whether the per-point errors can be trusted, and how large they are.",
        "The value is the median over all points of 1.0857 x sigma / flux, in magnitudes. No limit "
        "applies. The check passes with a known gain and is not checked when no light curve has errors "
        "or when the gain was assumed to be 1 e-/ADU; the value is then stored as an assumed value, "
        "so it is only a guide.",
        "Every point of every normalized light curve that carries an error.",
        register_entry="PHO-02",
    ),
    _zero_point_metric(
        PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
        "Gaia G",
        "Gaia G magnitudes (330 to 1050 nm), against the frame's own filter. The L filter and the sensor "
        "response differ from G, so the offset is not a standard-system zero point.",
    ),
    _zero_point_metric(
        PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
        "SIMBAD V",
        "Johnson V magnitudes from SIMBAD, against the frame's own filter. The L filter and the sensor "
        "response differ from V, so the offset depends on star color and is not a standard-system "
        "zero point.",
    ),
    MetricSpec(
        name=PHOTOMETRY_CATALOG_ZERO_POINT_COLOR_SLOPE,
        definition_version=1,
        unit="",
        checkpoint=Checkpoint.PHOTOMETRY,
        levels=_ZERO_POINT_FRAME_ONLY,
        better=Direction.NONE,
        meaning="How much a star's offset changes for each magnitude of its B-V color, in one frame.",
        estimator=(
            "The Theil-Sen slope of the offset (catalog magnitude plus 2.5 log10 of the flux, not the "
            "change from the star's reference) of the fixed set's stars that pass the cuts in this "
            "frame, against the star's B-V color, in magnitudes per magnitude. It is stored only for "
            "SIMBAD V sessions, and fitted only when at least 20 used stars have a B-V color spanning at "
            "least 0.4 mag (5th to 95th percentile). Gaia G stars carry no color in this library, so a "
            "Gaia G session stores no slope. The slope is not applied to the zero point."
        ),
        sample_definition="The used stars of the session's SIMBAD V fixed set that have a B-V color.",
        band="SIMBAD V against the frame's own filter.",
        computed_by="pipelines.photometry.processing.catalog_zero_point.differential_frame_estimates",
        uncertainty_kind=UncertaintyKind.NONE,
        uncertainty_method="No uncertainty is reported.",
        register_entry="PHO-15",
        purpose=MetricPurpose.EXPLORATORY,
        exploratory_question=(
            "Does a star's color move its offset enough to matter for the zero point, and is the slope "
            "steady from frame to frame? No transmission curve of the L filter is held, so the size to "
            "expect is not known."
        ),
        review_date=_ZERO_POINT_REVIEW_DATE,
    ),
    _star_sample_error_metric(
        PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_GAIA_G_MAG,
        "Gaia G",
        PHOTOMETRY_CATALOG_ZERO_POINT_GAIA_G_MAG,
    ),
    _star_sample_error_metric(
        PHOTOMETRY_CATALOG_ZERO_POINT_STAR_SAMPLE_ERROR_SIMBAD_V_MAG,
        "SIMBAD V",
        PHOTOMETRY_CATALOG_ZERO_POINT_SIMBAD_V_MAG,
    ),
)
