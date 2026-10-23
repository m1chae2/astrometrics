"""Builds the gate record for one photometry run.

A photometry run produces light curves for many stars from one or more
sessions of frames. Whether the run as a whole can be trusted depends on a
few checks: were many frames thrown out as outliers, did every frame have a
capture time (and which time scale do the times use), could every session be
used, was the comparison set large enough and the same in every frame, did
the telescope keep tracking, are there enough stars to say what "normal"
scatter looks like, and how large does the measurement say the error of each
point is.

Each check is returned as a `GateResult`. A check that could not look (for
example, no star recorded its alignment drift) is ``not_checked``; it is not
a pass. The failed gates' sentences are the run's flag reasons.

The same module turns the run's counts, measurements and gates into quality
measurements (`photometry_run_measurements`) and stores them
(`record_photometry_quality`). The gates and the summary stay as they are;
the measurements are written next to them.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from astrometricslib.models.quality.gate_result import (
    GateResult,
    GateStatus,
    failed_gate,
    passed_gate,
    unchecked_gate,
)
from astrometricslib.models.quality.limit import LimitTier
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.models.quality.quality_summary import PhotometryPipelineQualityMetrics
from astrometricslib.pipelines.photometry.metrics import (
    PHOTOMETRY_ASTROMETRY_IDENTIFIED_STAR_COUNT,
    PHOTOMETRY_CATALOG_MATCHED_STAR_COUNT,
    PHOTOMETRY_CENTROID_FALLBACK_FRACTION,
    PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED,
    PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX,
    PHOTOMETRY_CROSS_SESSION_MATCH_COUNT,
    PHOTOMETRY_FRAMES_PROCESSED,
    PHOTOMETRY_GATE_CAPTURE_TIMESTAMPS,
    PHOTOMETRY_GATE_COMPARISON_ENSEMBLE,
    PHOTOMETRY_GATE_DETECTABLE_AMPLITUDE,
    PHOTOMETRY_GATE_ENSEMBLE_FRAME_REJECTION,
    PHOTOMETRY_GATE_FLUX_UNCERTAINTY,
    PHOTOMETRY_GATE_PHOTOMETRY_WORK,
    PHOTOMETRY_GATE_REGISTRATION_DRIFT,
    PHOTOMETRY_GATE_SCATTER_POPULATION,
    PHOTOMETRY_GATE_SESSION_CONTENT,
    PHOTOMETRY_GATE_SESSION_PLATE_SOLVE,
    PHOTOMETRY_GATE_VARIABILITY_DISCRIMINATION,
    PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG,
    PHOTOMETRY_LONG_TERM_VARIABLE_CANDIDATE_COUNT,
    PHOTOMETRY_METRICS,
    PHOTOMETRY_NOISE_MODEL_STAR_COUNT,
    PHOTOMETRY_POSITION_ONLY_STAR_COUNT,
    PHOTOMETRY_STARS_FOUND,
    PHOTOMETRY_STARS_PROCESSED,
    PHOTOMETRY_STARS_WITH_SCATTER,
    PHOTOMETRY_UNRESOLVED_STAR_COUNT,
    PHOTOMETRY_VARIABLE_CANDIDATE_COUNT,
)
from astrometricslib.pipelines.photometry.post_processing.variability_skill import (
    MINIMUM_DISCRIMINATION_AUC,
    MINIMUM_KNOWN_VARIABLES,
    MINIMUM_UNLISTED_STARS,
    discrimination,
    minimum_detectable_amplitude_from_noise_mag,
    minimum_detectable_amplitude_mag,
)
from astrometricslib.pipelines.photometry.pre_processing.assess_input_quality import (
    UNSTABLE_TRACKING_DRIFT_PX,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    ANCHOR_RULE_RANK_BAND,
    CentroidShiftSummary,
)
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    MINIMUM_COMPARISON_STARS,
    ComparisonSetResult,
)
from astrometricslib.pipelines.photometry.processing.variability_indices import (
    MINIMUM_STARS_FOR_NOISE_MODEL,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    GateLimitSource,
    PipelineRunContext,
    build_and_record_measurements,
    build_measurement,
    gate_measurement,
)

# The percentage of rejected frames needed to trigger a quality warning flag.
# Normal processing naturally rejects a small number of frames (around 7.2%
# based on past runs). If we trigger a warning for anything less, we get
# too many false alarms. Setting the limit to 0.25 (25%) helps us catch
# real issues (like passing clouds) that need a human to check.
MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG = 0.25

# The minimum number of rejected frames needed to trigger a quality warning.
# This prevents false alarms when dealing with a small number of frames
# (under 20), where a single rejected frame could cause a high percentage.
MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG = 5

# The fewest stars with a usable light curve (three or more points) needed
# to say what the field's normal scatter is. The CV cutoff is the median
# plus a multiple of the median absolute deviation (MAD) of every star's
# scatter; with only a handful of stars those two numbers describe the
# handful, not the field. A design estimate, not validated on real fields.
# The noise model that replaces the CV cutoff needs more stars
# (`MINIMUM_STARS_FOR_NOISE_MODEL`).
MINIMUM_STARS_FOR_SCATTER_POPULATION = 10

# The largest peak-to-peak amplitude, in magnitudes, a run's noise level (or,
# without a noise model, its CV cutoff) may demand of a sinusoidal variable
# before the run is called blind to most variables.
# A design estimate, not validated: pulsating, rotating and young-star
# variables are mostly below about 0.3 mag, while eclipsing binaries, Cepheids,
# RR Lyrae and Miras are larger.
MAXIMUM_DETECTABLE_AMPLITUDE_MAG = 0.3

# The largest 95th-percentile (over the frames) displacement of a frame's
# corner, in pixels, that the `registration_drift` gate accepts. Each frame
# is lined up with the first by a shift, a rotation and a scale change (see
# `fit_frame_alignment`). The corner displacement is how far the rotation
# and scale alone move a corner of the field. A translation leaves it at
# zero. The apertures follow the stars, so a rotation does not by itself
# spoil the photometry. Past this limit, though, a star lands on pixels
# whose flat-field error differs by a different amount in every frame, which
# adds scatter no comparison star can remove. 1 pixel is two thirds of
# `MAX_CENTROID_SHIFT_PX` (1.5 px), where a centroid is refused. Measured on
# 2026-10-11 (95th percentile over the frames): M 13 0.05 px (rotation) and
# 0.2 px (scale) over a night, M 81 0.9 px, NGC 2403 1.8 px (a real rotation
# of 0.05 degrees over the night; 1.9 px in the worst frame).
# A designed limit, not a measured false-alarm rate.
MAXIMUM_CORNER_DISPLACEMENT_PX = 1.0

# The largest share of star measurements that may keep the shifted reference
# position because the star's own centroid was refused. Above one in five,
# the global shift does not fit the stars well enough to trust the rest. A
# designed limit, not a measured false-alarm rate.
MAXIMUM_CENTROID_FALLBACK_FRACTION = 0.20

# The most per-frame reasons the `capture_timestamps` gate quotes in its
# detail. The full list stays in the run's excluded frames.
MAXIMUM_TIMESTAMP_REASONS_SHOWN = 3

ENSEMBLE_REJECTION_GATE_NAME = "ensemble_frame_rejection"
CAPTURE_TIMESTAMP_GATE_NAME = "capture_timestamps"
SESSION_CONTENT_GATE_NAME = "session_content"
SESSION_PLATE_SOLVE_GATE_NAME = "session_plate_solve"
PHOTOMETRY_WORK_GATE_NAME = "photometry_work"
COMPARISON_ENSEMBLE_GATE_NAME = "comparison_ensemble"

# The comparison stars' own light curves may scatter at most this many times
# what their errors predict. Photon, read and sky noise leave out flat-field
# residuals and centroid error, so some excess is normal: the ratio was 1.27
# on M 13 and 1.66 on M 81 (2026-10-11). On NGC 2403 it was 18 (0.0655 mag
# against 0.0036), on a night when the comparison stars' brightness fell by
# 30 percent and recovered: patchy cloud that no ratio to the ensemble can
# remove. 5 sits between those cases. A design value, set on three fields.
MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED = 5.0
REGISTRATION_DRIFT_GATE_NAME = "registration_drift"
SCATTER_POPULATION_GATE_NAME = "scatter_population"
VARIABILITY_DISCRIMINATION_GATE_NAME = "variability_discrimination"
DETECTABLE_AMPLITUDE_GATE_NAME = "detectable_amplitude"
FLUX_UNCERTAINTY_GATE_NAME = "flux_uncertainty"

# How the comparison_ensemble gate words the limit on the size of a set. The
# stored measurement of that gate always uses it, whichever test failed.
COMPARISON_SET_SIZE_LIMIT_SOURCE = (
    f"at least {MINIMUM_COMPARISON_STARS} comparison stars, the same set in every frame of a session"
)


def photometry_run_gates(
    *,
    frames_contributed: int,
    rejected_frame_count: int,
    frames_without_timestamp: int,
    session_count: int,
    session_empty_reasons: Sequence[str],
    sessions_missing_wcs: Sequence[str],
    no_work_reason: str | None,
    comparison_sets: Sequence[ComparisonSetResult],
    registration_drifts_px: Sequence[float | None],
    stars_with_scatter: int,
    centroid_shift_summaries: Sequence[CentroidShiftSummary] = (),
    known_variable_scores: Sequence[float] = (),
    unlisted_scores: Sequence[float] = (),
    cutoff_cv: float | None = None,
    noise_model_stars: int | None = None,
    noise_floor_mag: float | None = None,
    timestamp_exclusion_reasons: Sequence[str] = (),
    time_basis: str | None = None,
    median_flux_error_mag: float | None = None,
    errors_assume_unit_gain: bool = False,
    errors_assume_zero_read_noise: bool = False,
) -> list[GateResult]:
    """Build the gates for one photometry run.

    Parameters
    ----------
    frames_contributed : `int`
        Frames that went into the run's sessions.
    rejected_frame_count : `int`
        Frames rejected as global ensemble outliers.
    frames_without_timestamp : `int`
        Frames left out for having no usable capture time. This counts both
        frames the database holds no time for and frames whose ``DATE-OBS``
        header was missing or unreadable.
    session_count : `int`
        Sessions the frames were grouped into.
    session_empty_reasons : `Sequence` [`str`]
        One sentence per session that produced no light curves.
    sessions_missing_wcs : `Sequence` [`str`]
        Sessions that could not be plate-solved for cross-session matching.
    no_work_reason : `str` or `None`
        Why the run found nothing to do, if it did not.
    comparison_sets : `Sequence` [`ComparisonSetResult`]
        One record for each session that had stars to normalize: the
        comparison stars it used, the number of them used in each frame,
        and the scatter of their normalized light curves.
    registration_drifts_px : `Sequence` [`float` or `None`]
        Each star's largest frame-to-frame alignment offset, or `None` when
        none was recorded for it.
    centroid_shift_summaries : `Sequence` [`CentroidShiftSummary`], optional
        One per session: how far each star's own centroid sat from the
        shifted reference position, and how often it fell back.
    stars_with_scatter : `int`
        Stars with at least three usable points, so with a measured scatter.
    known_variable_scores : `Sequence` [`float`], optional
        The variability score (see `variability_indices`) of each star with a
        light curve that a catalog lists as variable.
    unlisted_scores : `Sequence` [`float`], optional
        The variability score of each star with a light curve that no catalog
        lists.
    cutoff_cv : `float` or `None`, optional
        The run's variable-star cutoff on the CV, or `None` if it has none.
        The `detectable_amplitude` gate uses it only when `noise_floor_mag`
        is `None`.
    noise_model_stars : `int` or `None`, optional
        How many stars the run's noise model was fitted from (stars with a
        stored brightness and scatter). The `scatter_population` gate reads
        it. `None` leaves the gate on the count of stars with a CV.
    noise_floor_mag : `float` or `None`, optional
        The scatter the run's noise model expects of a star of typical
        brightness, in magnitudes. The `detectable_amplitude` gate uses it
        when it is given.
    timestamp_exclusion_reasons : `Sequence` [`str`], optional
        One sentence per frame left out because its ``DATE-OBS`` header was
        missing or unreadable, naming the frame and the problem. The gate's
        detail quotes the first few.
    time_basis : `str` or `None`, optional
        The time scale of the run's light curves, for example ``"BJD_TDB,
        mid-exposure"`` (see `observation_times`). The `capture_timestamps`
        gate quotes it in its detail, whether it passes or fails. `None`
        leaves it out.
    median_flux_error_mag : `float` or `None`, optional
        The median per-point error of the run's normalized light curves, in
        magnitudes (see `median_flux_error_mag`), or `None` when no light
        curve has errors.
    errors_assume_unit_gain : `bool`, optional
        Whether those errors assumed 1 electron per ADU because the camera's
        gain was unknown.
    errors_assume_zero_read_noise : `bool`, optional
        Whether those errors assumed no read noise because the camera's read
        noise was unknown.

    Returns
    -------
    gates : `list` [`GateResult`]
        Eleven gates, in a fixed order.
    """
    gates: list[GateResult] = []

    rejection_source = (
        f"at least {MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG} frames and "
        f"{MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG:.0%} of the run's frames"
    )
    if frames_contributed < MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG:
        gates.append(
            unchecked_gate(
                ENSEMBLE_REJECTION_GATE_NAME,
                f"fewer than {MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG} frames: too few to find outliers",
                rejection_source,
            )
        )
    else:
        fraction = rejected_frame_count / frames_contributed
        if (
            rejected_frame_count >= MINIMUM_ENSEMBLE_REJECTION_COUNT_TO_FLAG
            and fraction >= MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG
        ):
            gates.append(
                failed_gate(
                    ENSEMBLE_REJECTION_GATE_NAME,
                    f"{rejected_frame_count} of {frames_contributed} frame(s) ({fraction:.0%}) rejected "
                    "as global ensemble outliers, which is high enough to suspect the comparison "
                    "ensemble or the observing conditions",
                    fraction,
                    MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG,
                    rejection_source,
                )
            )
        else:
            gates.append(
                passed_gate(
                    ENSEMBLE_REJECTION_GATE_NAME,
                    fraction,
                    MINIMUM_ENSEMBLE_REJECTION_FRACTION_TO_FLAG,
                    rejection_source,
                )
            )

    if frames_without_timestamp:
        timestamp_detail = f"{frames_without_timestamp} frame(s) excluded for missing capture timestamp"
        if timestamp_exclusion_reasons:
            shown = list(timestamp_exclusion_reasons[:MAXIMUM_TIMESTAMP_REASONS_SHOWN])
            hidden_count = len(timestamp_exclusion_reasons) - len(shown)
            timestamp_detail += ": " + "; ".join(shown)
            if hidden_count > 0:
                timestamp_detail += f"; and {hidden_count} more"
        if time_basis:
            timestamp_detail += f". Times of the other frames: {time_basis}"
        gates.append(
            failed_gate(
                CAPTURE_TIMESTAMP_GATE_NAME,
                timestamp_detail,
                float(frames_without_timestamp),
                0.0,
            )
        )
    else:
        gates.append(
            passed_gate(
                CAPTURE_TIMESTAMP_GATE_NAME, 0.0, 0.0, detail=f"times: {time_basis}" if time_basis else ""
            )
        )

    if session_empty_reasons:
        gates.append(failed_gate(SESSION_CONTENT_GATE_NAME, "; ".join(session_empty_reasons)))
    elif session_count == 0:
        gates.append(unchecked_gate(SESSION_CONTENT_GATE_NAME, "the run had no sessions to check"))
    else:
        gates.append(
            passed_gate(SESSION_CONTENT_GATE_NAME, detail=f"{session_count} session(s) produced light curves")
        )

    if sessions_missing_wcs:
        gates.append(
            failed_gate(
                SESSION_PLATE_SOLVE_GATE_NAME,
                f"{len(sessions_missing_wcs)} session(s) could not be plate-solved for "
                f"cross-session star matching: {', '.join(sessions_missing_wcs)}",
                float(len(sessions_missing_wcs)),
                0.0,
            )
        )
    elif session_count < 2:
        gates.append(
            unchecked_gate(
                SESSION_PLATE_SOLVE_GATE_NAME, "a single session needs no cross-session star matching"
            )
        )
    else:
        gates.append(passed_gate(SESSION_PLATE_SOLVE_GATE_NAME, 0.0, 0.0))

    if no_work_reason:
        gates.append(failed_gate(PHOTOMETRY_WORK_GATE_NAME, no_work_reason))
    else:
        gates.append(passed_gate(PHOTOMETRY_WORK_GATE_NAME, detail="the run had frames to measure"))

    gates.append(_comparison_ensemble_gate(comparison_sets))

    gates.append(_registration_drift_gate(registration_drifts_px, centroid_shift_summaries))

    population_source = (
        f"at least {MINIMUM_STARS_FOR_SCATTER_POPULATION} stars with three or more points (design estimate)"
    )
    model_source = (
        f"at least {MINIMUM_STARS_FOR_NOISE_MODEL} stars with five or more points and a raw flux to fit the "
        "noise model (design estimate)"
    )
    if stars_with_scatter < MINIMUM_STARS_FOR_SCATTER_POPULATION:
        gates.append(
            unchecked_gate(
                SCATTER_POPULATION_GATE_NAME,
                f"only {stars_with_scatter} star(s) have a measured scatter, too few to say what normal "
                "scatter is, so the variable-star cutoff is not reliable",
                population_source,
            )
        )
    elif noise_model_stars is None:
        gates.append(
            passed_gate(
                SCATTER_POPULATION_GATE_NAME,
                float(stars_with_scatter),
                float(MINIMUM_STARS_FOR_SCATTER_POPULATION),
                population_source,
            )
        )
    elif noise_model_stars < MINIMUM_STARS_FOR_NOISE_MODEL:
        gates.append(
            unchecked_gate(
                SCATTER_POPULATION_GATE_NAME,
                f"only {noise_model_stars} star(s) can be used to fit the noise model, too few to say how "
                "much scatter a constant star of each brightness shows, so candidates were picked by the "
                "CV cutoff alone",
                model_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                SCATTER_POPULATION_GATE_NAME,
                float(noise_model_stars),
                float(MINIMUM_STARS_FOR_NOISE_MODEL),
                model_source,
                f"noise model fitted from {noise_model_stars} stars",
            )
        )

    skill_source = (
        f"at least {MINIMUM_KNOWN_VARIABLES} catalogued variables and "
        f"{MINIMUM_UNLISTED_STARS} unlisted stars; AUC above chance at the 5% level and at least "
        f"{MINIMUM_DISCRIMINATION_AUC:g} (design choice)"
    )
    skill = discrimination(known_variable_scores, unlisted_scores)
    if skill is None:
        gates.append(
            unchecked_gate(
                VARIABILITY_DISCRIMINATION_GATE_NAME,
                f"the field has {len(known_variable_scores)} star(s) the catalogs list as variable and "
                f"{len(unlisted_scores)} they do not, too few to check that the variability "
                "score can see variables",
                skill_source,
            )
        )
    elif skill.sees_known_variables:
        gates.append(
            passed_gate(VARIABILITY_DISCRIMINATION_GATE_NAME, skill.auc, skill.needed_auc, skill_source)
        )
    else:
        gates.append(
            failed_gate(
                VARIABILITY_DISCRIMINATION_GATE_NAME,
                f"the variability score does not pick out the {skill.known_variables} stars "
                f"the catalogs list as variable (AUC {skill.auc:.2f}, needs "
                f"{skill.needed_auc:.2f}), so variable candidates from this run are not reliable",
                skill.auc,
                skill.needed_auc,
                skill_source,
            )
        )

    amplitude_source = f"at most {MAXIMUM_DETECTABLE_AMPLITUDE_MAG:g} mag peak to peak (design estimate)"
    if noise_floor_mag is not None:
        amplitude = minimum_detectable_amplitude_from_noise_mag(noise_floor_mag)
        amplitude_source += "; from the noise model"
    elif cutoff_cv is not None:
        amplitude = minimum_detectable_amplitude_mag(cutoff_cv)
        amplitude_source += "; from the CV cutoff"
    else:
        amplitude = None
    if amplitude is None:
        gates.append(
            unchecked_gate(
                DETECTABLE_AMPLITUDE_GATE_NAME,
                "the run has no noise model and no variable-star cutoff",
                amplitude_source,
            )
        )
    elif amplitude > MAXIMUM_DETECTABLE_AMPLITUDE_MAG:
        gates.append(
            failed_gate(
                DETECTABLE_AMPLITUDE_GATE_NAME,
                f"a variable would have to change by about {amplitude:.2f} mag peak to peak "
                "to clear this run's noise level, so smaller variables cannot be flagged",
                amplitude,
                MAXIMUM_DETECTABLE_AMPLITUDE_MAG,
                amplitude_source,
            )
        )
    else:
        gates.append(
            passed_gate(
                DETECTABLE_AMPLITUDE_GATE_NAME,
                amplitude,
                MAXIMUM_DETECTABLE_AMPLITUDE_MAG,
                amplitude_source,
            )
        )

    gates.append(
        _flux_uncertainty_gate(median_flux_error_mag, errors_assume_unit_gain, errors_assume_zero_read_noise)
    )
    return gates


def _centroid_distribution_text(summaries: Sequence[CentroidShiftSummary]) -> str:
    """Describe the per-star centroid offsets of the worst session.

    Parameters
    ----------
    summaries : `Sequence` [`CentroidShiftSummary`]
        One summary per session. At least one is required.

    Returns
    -------
    text : `str`
        The median and 95th-percentile offset, the fallback share, the worst
        frame, and the anchor rule of the session with the largest corner
        displacement (or, without one, the largest 95th-percentile offset).
        The corner displacement itself is quoted for the session that has
        the largest. With more than one session, the text says
        so and names the largest fallback share of any session.
    """
    worst = max(
        summaries,
        key=lambda summary: (
            summary.corner_displacement_p95_px if summary.corner_displacement_p95_px is not None else -1.0,
            summary.p95_offset_px if summary.p95_offset_px is not None else -1.0,
        ),
    )
    parts = []
    if worst.p95_offset_px is None or worst.median_offset_px is None:
        parts.append("no star kept its own centroid")
    else:
        parts.append(
            f"per-star centroid offset from the shifted position: median {worst.median_offset_px:.2f} px, "
            f"95th percentile {worst.p95_offset_px:.2f} px"
        )
    parts.append(
        f"{100.0 * max(summary.fallback_fraction for summary in summaries):.1f}% of star measurements "
        "kept the aligned position"
    )
    corner_summaries = [summary for summary in summaries if summary.corner_displacement_p95_px is not None]
    if corner_summaries:
        corner = max(corner_summaries, key=lambda summary: summary.corner_displacement_p95_px or 0.0)
        parts.append(
            f"rotation and scale change move a corner {corner.corner_displacement_p95_px:.2f} px "
            f"(95th percentile over {corner.fitted_frame_count} frames)"
        )
    if worst.worst_frame is not None and worst.worst_frame_p95_offset_px is not None:
        parts.append(
            f"worst frame {worst.worst_frame:%Y-%m-%d %H:%M:%S} (95th percentile "
            f"{worst.worst_frame_p95_offset_px:.2f} px)"
        )
    if worst.anchor_rule == ANCHOR_RULE_RANK_BAND:
        parts.append(f"alignment used the {worst.anchor_rule} rule on {worst.anchor_count} stars")
    scope = f" (worst of {len(summaries)} sessions)" if len(summaries) > 1 else ""
    return "; ".join(parts) + scope


def _registration_drift_gate(
    registration_drifts_px: Sequence[float | None], summaries: Sequence[CentroidShiftSummary]
) -> GateResult:
    """Build the ``registration_drift`` gate.

    The gate looks at three things. The first is the largest frame-to-frame
    shift of the whole field, which shows lost tracking. The second is how
    far the fitted rotation and scale change move a corner of the field,
    which shows a field that turned or changed scale during the session. The
    third is the share of star measurements whose own centroid was refused,
    which shows an alignment that does not fit the stars. The per-star
    centroid offsets are reported in the detail but not judged: they are
    dominated by centroid noise on faint and crowded stars, which does not
    mean the frames are misaligned (on 2026-10-11, M 13 and M 81 failed on
    them with no rotation, while NGC 2403, which did rotate, was the only one
    where the rotation mattered).

    The gate fails when the global shift exceeds `UNSTABLE_TRACKING_DRIFT_PX`,
    when the 95th percentile of the corner displacement over the frames
    exceeds `MAXIMUM_CORNER_DISPLACEMENT_PX`, or when more than
    `MAXIMUM_CENTROID_FALLBACK_FRACTION` of the star measurements fell back.
    It is not checked when none of these was recorded.

    Parameters
    ----------
    registration_drifts_px : `Sequence` [`float` or `None`]
        Each star's largest frame-to-frame alignment offset, or `None`.
    summaries : `Sequence` [`CentroidShiftSummary`]
        One per session, possibly empty.

    Returns
    -------
    gate : `GateResult`
        The gate.
    """
    source = (
        f"global alignment offset of at most {UNSTABLE_TRACKING_DRIFT_PX:g} px (half of the centroid search "
        f"box), 95th-percentile corner displacement from rotation and scale change of at most "
        f"{MAXIMUM_CORNER_DISPLACEMENT_PX:g} px, and at most {100 * MAXIMUM_CENTROID_FALLBACK_FRACTION:.0f}% "
        "of star measurements falling back (designed limits)"
    )
    recorded_drifts = [drift for drift in registration_drifts_px if drift is not None]
    if not recorded_drifts and not summaries:
        return unchecked_gate(
            REGISTRATION_DRIFT_GATE_NAME, "no star recorded its frame-to-frame alignment drift", source
        )

    worst_drift = max(recorded_drifts) if recorded_drifts else None
    corner_values = [
        summary.corner_displacement_p95_px
        for summary in summaries
        if summary.corner_displacement_p95_px is not None
    ]
    worst_corner = max(corner_values) if corner_values else None
    worst_fallback = max((summary.fallback_fraction for summary in summaries), default=None)

    problems: list[str] = []
    measured, limit = worst_drift, UNSTABLE_TRACKING_DRIFT_PX
    if worst_drift is not None and worst_drift > UNSTABLE_TRACKING_DRIFT_PX:
        problems.append(
            f"frame alignment drifted up to {worst_drift:.0f} px, so tracking was probably lost "
            "and apparent brightness changes may be alignment, not the star"
        )
    if worst_corner is not None and worst_corner > MAXIMUM_CORNER_DISPLACEMENT_PX:
        problems.append(
            f"the field rotated or changed scale during the session: a corner moves {worst_corner:.2f} px "
            f"(limit {MAXIMUM_CORNER_DISPLACEMENT_PX:g} px). The frames were lined up with that rotation, "
            "so the apertures follow the stars, but a star lands on different pixels in each frame and the "
            "flat-field error adds scatter"
        )
        if not problems[:-1]:
            measured, limit = worst_corner, MAXIMUM_CORNER_DISPLACEMENT_PX
    if worst_fallback is not None and worst_fallback > MAXIMUM_CENTROID_FALLBACK_FRACTION:
        problems.append(
            f"{100.0 * worst_fallback:.1f}% of star measurements kept the aligned position because the "
            f"star's own centroid was refused (limit {100 * MAXIMUM_CENTROID_FALLBACK_FRACTION:.0f}%)"
        )
        if not problems[:-1]:
            measured, limit = worst_fallback, MAXIMUM_CENTROID_FALLBACK_FRACTION
    if measured is None:
        measured = worst_corner if worst_corner is not None else worst_fallback

    distribution = _centroid_distribution_text(summaries) if summaries else ""
    drift_text = f"global alignment drift up to {worst_drift:.1f} px" if worst_drift is not None else ""
    if problems:
        suffix = f" ({'; '.join(part for part in (drift_text, distribution) if part)})"
        return failed_gate(
            REGISTRATION_DRIFT_GATE_NAME, "; ".join(problems) + suffix, measured, limit, source
        )
    if worst_drift is not None:
        passed_limit = UNSTABLE_TRACKING_DRIFT_PX
    elif worst_corner is not None:
        passed_limit = MAXIMUM_CORNER_DISPLACEMENT_PX
    else:
        passed_limit = MAXIMUM_CENTROID_FALLBACK_FRACTION
    return passed_gate(
        REGISTRATION_DRIFT_GATE_NAME,
        measured,
        passed_limit,
        source,
        "; ".join(part for part in (drift_text, distribution) if part),
    )


def _comparison_ensemble_gate(comparison_sets: Sequence[ComparisonSetResult]) -> GateResult:
    """Report the comparison set of each session, and whether it held still.

    The gate fails when a session's comparison set has fewer than
    `MINIMUM_COMPARISON_STARS` stars, when the number of comparison stars
    differs from one frame of a session to another (a set that should be
    fixed for the session is not), or when the comparison stars' own light
    curves scatter more than `MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED` times
    what their errors predict. Otherwise it passes. Its detail gives the
    size of each set, says that each set is fixed across its session, and
    gives the scatter of the comparison stars' own normalized light curves
    with the scatter their errors predict, in magnitudes.

    Parameters
    ----------
    comparison_sets : `Sequence` [`ComparisonSetResult`]
        One record for each session that had stars to normalize.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` when no session was normalized. Otherwise ``failed``
        or ``passed``, with the size of the smallest set as its measured
        value.
    """
    source = COMPARISON_SET_SIZE_LIMIT_SOURCE
    if not comparison_sets:
        return unchecked_gate(
            COMPARISON_ENSEMBLE_GATE_NAME, "no session was normalized against a comparison set", source
        )
    sizes = [len(record.star_ids) for record in comparison_sets]
    smallest = min(sizes)
    thin = sum(1 for size in sizes if size < MINIMUM_COMPARISON_STARS)
    moving = sum(1 for record in comparison_sets if not record.is_fixed)
    if thin or moving:
        problems = []
        if thin:
            problems.append(
                f"{thin} of {len(sizes)} session(s) had fewer than {MINIMUM_COMPARISON_STARS} "
                f"comparison stars (the smallest had {smallest})"
            )
        if moving:
            problems.append(
                f"{moving} session(s) used a different number of comparison stars in different frames"
            )
        return failed_gate(
            COMPARISON_ENSEMBLE_GATE_NAME,
            "; ".join(problems),
            float(smallest),
            float(MINIMUM_COMPARISON_STARS),
            source,
        )
    scatters = [record.scatter_mag for record in comparison_sets if record.scatter_mag is not None]
    expected = [
        record.expected_error_mag for record in comparison_sets if record.expected_error_mag is not None
    ]
    ratios = [
        record.scatter_mag / record.expected_error_mag
        for record in comparison_sets
        if record.scatter_mag is not None and record.expected_error_mag
    ]
    if ratios and max(ratios) > MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED:
        return failed_gate(
            COMPARISON_ENSEMBLE_GATE_NAME,
            f"the comparison stars scatter {max(ratios):.1f} times what their errors predict "
            f"({max(scatters):.4f} mag against {max(expected):.4f} mag in the worst session). Something "
            "that changes across the field is not removed by dividing by the ensemble (patchy cloud, "
            "tracking loss or a changing focus), so the light curves cannot be trusted at the level of "
            "their error bars",
            float(max(ratios)),
            MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED,
            f"at most {MAXIMUM_ENSEMBLE_SCATTER_OVER_PREDICTED:g} times the scatter the errors predict "
            "(design value)",
        )
    detail = (
        f"comparison set sizes {', '.join(str(size) for size in sizes)}; each set is the same in every "
        "frame of its session"
    )
    if scatters:
        detail += f"; ensemble scatter {max(scatters):.4f} mag (worst session)"
        if expected:
            detail += f", errors predict {max(expected):.4f} mag"
    rejected = sum(record.rejected_count for record in comparison_sets)
    detail += f"; {rejected} star(s) turned away"
    return passed_gate(
        COMPARISON_ENSEMBLE_GATE_NAME, float(smallest), float(MINIMUM_COMPARISON_STARS), source, detail
    )


def _flux_uncertainty_gate(
    median_error_mag: float | None, assumes_unit_gain: bool, assumes_zero_read_noise: bool
) -> GateResult:
    """Report the size of the per-point errors and what they assumed.

    The gate cannot fail: the errors are the measurement's own estimate, and
    this repository holds no limit for what a good error is. It answers
    whether the estimate can be trusted. With a known gain it passes and
    reports the median error. With an assumed gain the error scale is only a
    guide, so the gate reads "not checked" and still quotes the median in
    its detail.

    Parameters
    ----------
    median_error_mag : `float` or `None`
        The median per-point error of the run's light curves, in magnitudes.
    assumes_unit_gain : `bool`
        Whether the errors assumed 1 electron per ADU.
    assumes_zero_read_noise : `bool`
        Whether the errors assumed no read noise.

    Returns
    -------
    gate : `GateResult`
        ``not_checked`` without errors or with an assumed gain, otherwise
        ``passed`` with the median error as its measured value.
    """
    source = "median of 1.0857 x sigma / flux over all points; no limit applies"
    if median_error_mag is None:
        return unchecked_gate(FLUX_UNCERTAINTY_GATE_NAME, "no light curve carries measurement errors", source)
    if assumes_unit_gain:
        return unchecked_gate(
            FLUX_UNCERTAINTY_GATE_NAME,
            f"errors assume unit gain (1 e-/ADU) because the camera's gain is not known, so the median "
            f"error of {median_error_mag:.4f} mag is only a guide; add a sensor_noise entry for the frames' "
            "gain, offset and binning to the camera profile to make it a measurement",
            source,
        )
    detail = f"median per-point error {median_error_mag:.4f} mag"
    if assumes_zero_read_noise:
        detail += "; read noise assumed to be zero, so faint stars' errors are slightly small"
    return passed_gate(FLUX_UNCERTAINTY_GATE_NAME, median_error_mag, None, source, detail)


def worst_comparison_scatter_ratio(comparison_sets: Sequence[ComparisonSetResult]) -> float | None:
    """Find how far above its predicted error the comparison scatter is.

    Parameters
    ----------
    comparison_sets : `Sequence` [`ComparisonSetResult`]
        One record for each session that had stars to normalize.

    Returns
    -------
    ratio : `float` or `None`
        The largest, over the sessions, of the comparison stars' scatter
        divided by the error their uncertainties predict. `None` when no
        session has both numbers.
    """
    ratios = [
        record.scatter_mag / record.expected_error_mag
        for record in comparison_sets
        if record.scatter_mag is not None and record.expected_error_mag
    ]
    return float(max(ratios)) if ratios else None


@dataclass(frozen=True)
class PhotometryRunInputs:
    """The run facts the measurements need besides the summary and the gates.

    Parameters
    ----------
    comparison_sets : `Sequence` [`ComparisonSetResult`]
        One record for each session that had stars to normalize.
    registration_drifts_px : `Sequence` [`float` or `None`]
        Each star's largest frame-to-frame alignment offset, or `None`.
    centroid_shift_summaries : `Sequence` [`CentroidShiftSummary`]
        One per session.
    stars_with_scatter : `int` or `None`
        Stars with a measured scatter, or `None` when it was not counted.
    noise_model_star_count : `int` or `None`
        Stars the noise model could be fitted from, or `None` when it was
        not counted.
    median_flux_error_mag : `float` or `None`
        The median per-point error of the run's light curves, in magnitudes.
    errors_assume_unit_gain : `bool`
        Whether those errors assumed 1 electron per ADU.
    """

    comparison_sets: Sequence[ComparisonSetResult] = field(default_factory=tuple)
    registration_drifts_px: Sequence[float | None] = field(default_factory=tuple)
    centroid_shift_summaries: Sequence[CentroidShiftSummary] = field(default_factory=tuple)
    stars_with_scatter: int | None = None
    noise_model_star_count: int | None = None
    median_flux_error_mag: float | None = None
    errors_assume_unit_gain: bool = False


# The metric that stands for each gate, and how its limit was obtained. The
# history fill (`scripts/backfill_quality_measurements.py`) reads this table
# too, so a limit is described in one place.
#
# A count that must be zero, a missing time or a missing plate solve follows
# from the geometry of the run (a frame without a time cannot be placed in a
# session). Every other limit is a designed value that the validation register
# lists in its design-estimates table, so each is a design estimate and cites
# its row (PHO-D06, PHO-D09, PHO-D13 to PHO-D16). The frame-rejection share
# was set above the 7 percent that earlier runs showed, but it is a chosen
# threshold, not a measured baseline. The flux-uncertainty gate has no limit
# (it reports the size of the errors); its source line is the error formula,
# which is physics, so it keeps the physical-budget tier with no value.
GATE_LIMIT_SOURCES: dict[str, GateLimitSource] = {
    ENSEMBLE_REJECTION_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_ENSEMBLE_FRAME_REJECTION, LimitTier.DESIGN_ESTIMATE, "PHO-D09"
    ),
    CAPTURE_TIMESTAMP_GATE_NAME: GateLimitSource(PHOTOMETRY_GATE_CAPTURE_TIMESTAMPS, LimitTier.GEOMETRY),
    SESSION_CONTENT_GATE_NAME: GateLimitSource(PHOTOMETRY_GATE_SESSION_CONTENT, LimitTier.GEOMETRY),
    SESSION_PLATE_SOLVE_GATE_NAME: GateLimitSource(PHOTOMETRY_GATE_SESSION_PLATE_SOLVE, LimitTier.GEOMETRY),
    PHOTOMETRY_WORK_GATE_NAME: GateLimitSource(PHOTOMETRY_GATE_PHOTOMETRY_WORK, LimitTier.GEOMETRY),
    COMPARISON_ENSEMBLE_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_COMPARISON_ENSEMBLE, LimitTier.DESIGN_ESTIMATE, "PHO-D06"
    ),
    REGISTRATION_DRIFT_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_REGISTRATION_DRIFT, LimitTier.DESIGN_ESTIMATE, "PHO-D16"
    ),
    SCATTER_POPULATION_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_SCATTER_POPULATION, LimitTier.DESIGN_ESTIMATE, "PHO-D13"
    ),
    VARIABILITY_DISCRIMINATION_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_VARIABILITY_DISCRIMINATION, LimitTier.DESIGN_ESTIMATE, "PHO-D14"
    ),
    DETECTABLE_AMPLITUDE_GATE_NAME: GateLimitSource(
        PHOTOMETRY_GATE_DETECTABLE_AMPLITUDE, LimitTier.DESIGN_ESTIMATE, "PHO-D15"
    ),
    FLUX_UNCERTAINTY_GATE_NAME: GateLimitSource(PHOTOMETRY_GATE_FLUX_UNCERTAINTY, LimitTier.PHYSICAL_BUDGET),
}

_SPEC_BY_NAME: dict[str, MetricSpec] = {spec.name: spec for spec in PHOTOMETRY_METRICS}


def gate_for_measurement(gate: GateResult, inputs: PhotometryRunInputs) -> GateResult:
    """Adjust a gate so its number means one thing in every outcome.

    Two gates quote a different number depending on how they ended. The
    comparison-ensemble gate gives the smallest set size, or the scatter ratio
    when that test fails; the registration gate gives the global drift in
    pixels, the corner displacement in pixels or the fallback share. A stored
    metric has one unit and one meaning, so the measurement uses a steady
    number (the other numbers have metrics of their own). The gate itself is
    not changed.

    The run's record step calls this function, and so does the history fill
    (with empty inputs, for gates whose steady number does not need them; see
    `steady_number_needs_run_data`).

    Parameters
    ----------
    gate : `GateResult`
        The gate as recorded on the summary.
    inputs : `PhotometryRunInputs`
        The run facts the steady numbers come from.

    Returns
    -------
    gate : `GateResult`
        A copy for the measurement. The comparison-ensemble gate carries the
        size of the smallest set and the limit on a set's size; the
        registration gate carries the largest global drift and its limit; the
        flux-uncertainty gate carries the median error even when it was not
        checked. Other gates, and any gate that was not checked, are returned
        as they are.
    """
    if (
        gate.name == COMPARISON_ENSEMBLE_GATE_NAME
        and gate.status is not GateStatus.NOT_CHECKED
        and inputs.comparison_sets
    ):
        smallest = min(len(record.star_ids) for record in inputs.comparison_sets)
        return gate.model_copy(
            update={
                "measured_value": float(smallest),
                "limit": float(MINIMUM_COMPARISON_STARS),
                "limit_source": COMPARISON_SET_SIZE_LIMIT_SOURCE,
            }
        )
    if gate.name == REGISTRATION_DRIFT_GATE_NAME and gate.status is not GateStatus.NOT_CHECKED:
        recorded = [drift for drift in inputs.registration_drifts_px if drift is not None]
        return gate.model_copy(
            update={
                "measured_value": float(max(recorded)) if recorded else None,
                "limit": float(UNSTABLE_TRACKING_DRIFT_PX),
            }
        )
    if (
        gate.name == FLUX_UNCERTAINTY_GATE_NAME
        and gate.status is GateStatus.NOT_CHECKED
        and inputs.errors_assume_unit_gain
    ):
        return gate.model_copy(update={"measured_value": inputs.median_flux_error_mag})
    return gate


def steady_number_needs_run_data(gate: GateResult) -> bool:
    """Say whether a gate's steady number needs run data.

    `gate_for_measurement` gives each gate a steady number. The
    comparison-ensemble and registration-drift gates quote a steady number
    (the smallest comparison set, the largest drift) that comes from the
    run's own lists. A stored summary does not hold those lists, so the
    history fill cannot rebuild the number for a gate that ran. A gate that
    was not checked keeps what it recorded.

    Parameters
    ----------
    gate : `GateResult`
        The gate as recorded on the summary.

    Returns
    -------
    needs_run_data : `bool`
        `True` when the gate ran and its steady number comes from run data.
    """
    return (
        gate.name in (COMPARISON_ENSEMBLE_GATE_NAME, REGISTRATION_DRIFT_GATE_NAME)
        and gate.status is not GateStatus.NOT_CHECKED
    )


def _worst_centroid_values(
    summaries: Sequence[CentroidShiftSummary],
) -> tuple[float | None, float | None]:
    """Find the worst corner displacement and fallback share of the sessions.

    Parameters
    ----------
    summaries : `Sequence` [`CentroidShiftSummary`]
        One per session.

    Returns
    -------
    corner_px : `float` or `None`
        The largest 95th-percentile corner displacement, or `None` when no
        session fitted a rotation and scale.
    fallback_fraction : `float` or `None`
        The largest share of star measurements that kept the aligned
        position, or `None` when there was no session summary.
    """
    corners = [
        summary.corner_displacement_p95_px
        for summary in summaries
        if summary.corner_displacement_p95_px is not None
    ]
    fallbacks = [summary.fallback_fraction for summary in summaries]
    return (
        float(max(corners)) if corners else None,
        float(max(fallbacks)) if fallbacks else None,
    )


# The summary's count fields, as the field name on
# `PhotometryPipelineQualityMetrics` and the metric that holds each one. The
# run's other numbers come from `PhotometryRunInputs` or are made from lists.
# A test checks that every summary field is here, is the light-curve scatter,
# or is named there as not a measurement.
SUMMARY_COUNT_METRICS: dict[str, str] = {
    "stars_processed": PHOTOMETRY_STARS_PROCESSED,
    "stars_found": PHOTOMETRY_STARS_FOUND,
    "frames_processed": PHOTOMETRY_FRAMES_PROCESSED,
    "variable_candidate_count": PHOTOMETRY_VARIABLE_CANDIDATE_COUNT,
    "cross_session_match_count": PHOTOMETRY_CROSS_SESSION_MATCH_COUNT,
    "long_term_variable_candidate_count": PHOTOMETRY_LONG_TERM_VARIABLE_CANDIDATE_COUNT,
    "astrometry_identified_star_count": PHOTOMETRY_ASTROMETRY_IDENTIFIED_STAR_COUNT,
    "catalog_matched_star_count": PHOTOMETRY_CATALOG_MATCHED_STAR_COUNT,
    "position_only_star_count": PHOTOMETRY_POSITION_ONLY_STAR_COUNT,
    "unresolved_star_count": PHOTOMETRY_UNRESOLVED_STAR_COUNT,
}


def photometry_run_measurements(
    metrics: PhotometryPipelineQualityMetrics,
    gates: Sequence[GateResult],
    inputs: PhotometryRunInputs,
    context: PipelineRunContext,
) -> list[QualityMeasurement]:
    """Build every measurement the photometry pipeline declares for one run.

    Parameters
    ----------
    metrics : `PhotometryPipelineQualityMetrics`
        The run's summary metrics.
    gates : `Sequence` [`GateResult`]
        The run's gates from `photometry_run_gates`.
    inputs : `PhotometryRunInputs`
        The run facts the summary does not hold.
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One measurement for each declared metric, all about the run. A value
        the run could not find (no light-curve scatter, no comparison error,
        no fitted rotation, a count that was not made) is ``not_measured``
        with a reason. The gate metrics carry the gate's outcome and limit.
        The flux-uncertainty value is ``assumed`` when the errors assumed a
        gain of 1 e-/ADU.
    """
    subject = context.run_subject()
    measurements: list[QualityMeasurement] = []

    counts: dict[str, int | None] = {
        metric_name: getattr(metrics, field_name) for field_name, metric_name in SUMMARY_COUNT_METRICS.items()
    }
    counts[PHOTOMETRY_STARS_WITH_SCATTER] = inputs.stars_with_scatter
    counts[PHOTOMETRY_NOISE_MODEL_STAR_COUNT] = inputs.noise_model_star_count
    for metric_name, count in counts.items():
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[metric_name],
                None if count is None else float(count),
                context,
                subject,
                reason="The run did not count this for the summary.",
            )
        )

    corner_px, fallback_fraction = _worst_centroid_values(inputs.centroid_shift_summaries)
    values: list[tuple[str, float | None, str]] = [
        (
            PHOTOMETRY_LIGHT_CURVE_SCATTER_RMS_MEDIAN_MAG,
            metrics.light_curve_scatter_rms_mag,
            "No star had three or more usable points, so no scatter could be measured.",
        ),
        (
            PHOTOMETRY_COMPARISON_SCATTER_OVER_PREDICTED,
            worst_comparison_scatter_ratio(inputs.comparison_sets),
            "No session had both a comparison-star scatter and a predicted error.",
        ),
        (
            PHOTOMETRY_CORNER_DISPLACEMENT_P95_PX,
            corner_px,
            "No session had a rotation and scale change fitted to its frames.",
        ),
        (
            PHOTOMETRY_CENTROID_FALLBACK_FRACTION,
            fallback_fraction,
            "No session recorded a per-star centroid summary.",
        ),
    ]
    for metric_name, value, missing_reason in values:
        measurements.append(
            build_measurement(
                _SPEC_BY_NAME[metric_name],
                None if value is None else float(value),
                context,
                subject,
                reason=missing_reason,
            )
        )

    gates_by_name = {gate.name: gate for gate in gates}
    for gate_name, limit_source in GATE_LIMIT_SOURCES.items():
        gate = gates_by_name.get(gate_name)
        if gate is None:
            continue
        spec = _SPEC_BY_NAME[limit_source.metric_name]
        measurement = gate_measurement(
            gate_for_measurement(gate, inputs),
            spec,
            context,
            subject,
            limit_tier=limit_source.tier,
            register_entry=limit_source.register_entry,
        )
        if (
            gate_name == FLUX_UNCERTAINTY_GATE_NAME
            and gate.status is GateStatus.NOT_CHECKED
            and measurement.value is not None
        ):
            # The gate's own detail names the assumed gain as the source.
            measurement = measurement.model_copy(update={"status": MeasurementStatus.ASSUMED})
        measurements.append(measurement)
    return measurements


def record_photometry_quality(
    metrics: PhotometryPipelineQualityMetrics,
    gates: Sequence[GateResult],
    inputs: PhotometryRunInputs,
    context: PipelineRunContext,
    *,
    config: object = None,
) -> int:
    """Store the run's measurements; no failure here ever fails the run.

    Parameters
    ----------
    metrics : `PhotometryPipelineQualityMetrics`
        The run's summary metrics.
    gates : `Sequence` [`GateResult`]
        The run's gates.
    inputs : `PhotometryRunInputs`
        The run facts the summary does not hold.
    context : `PipelineRunContext`
        The run's shared facts.
    config : `AppConfiguration`, optional
        Where the quality store lives. The process-wide one if omitted.

    Returns
    -------
    new_count : `int`
        How many rows were new. It is 0 when anything went wrong, either in
        building the rows or in storing them (both are logged).
    """
    return build_and_record_measurements(
        lambda: photometry_run_measurements(metrics, gates, inputs, context),
        f"photometry run on {context.target_id}",
        config=config,
    )
