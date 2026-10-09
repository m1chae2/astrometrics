"""Builds the gate record for one photometry run.

A photometry run produces light curves for many stars from one or more
sessions of frames. Whether the run as a whole can be trusted depends on a
few checks: were many frames thrown out as outliers, did every frame have a
capture time, could every session be used, did the comparison stars hold up
in every frame, did the telescope keep tracking, and are there enough stars
to say what "normal" scatter looks like.

Each check is returned as a `GateResult`. A check that could not look (for
example, no star recorded its alignment drift) is ``not_checked``; it is not
a pass. The failed gates' sentences are the run's flag reasons.
"""

from collections.abc import Sequence

from astrometricslib.models.gate_result import GateResult, failed_gate, passed_gate, unchecked_gate
from astrometricslib.pipelines.photometry.post_processing.variability_skill import (
    MINIMUM_KNOWN_VARIABLES,
    MINIMUM_UNLISTED_STARS,
    discrimination,
    minimum_detectable_amplitude_mag,
)
from astrometricslib.pipelines.photometry.pre_processing.assess_input_quality import (
    UNSTABLE_TRACKING_DRIFT_PX,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import MINIMUM_ENSEMBLE_SIZE

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
# to say what the field's normal scatter is. The variable-star cutoff is the
# median plus a multiple of the median absolute deviation (MAD) of every
# star's scatter; with only a handful of stars those two numbers describe
# the handful, not the field. The same minimum the comparison ensemble
# needs. A design estimate, not validated on real fields.
MINIMUM_STARS_FOR_SCATTER_POPULATION = MINIMUM_ENSEMBLE_SIZE

# The largest peak-to-peak amplitude, in magnitudes, a run's cutoff may demand
# of a sinusoidal variable before the run is called blind to most variables.
# A design estimate, not validated: pulsating, rotating and young-star
# variables are mostly below about 0.3 mag, while eclipsing binaries, Cepheids,
# RR Lyrae and Miras are larger.
MAXIMUM_DETECTABLE_AMPLITUDE_MAG = 0.3

ENSEMBLE_REJECTION_GATE_NAME = "ensemble_frame_rejection"
CAPTURE_TIMESTAMP_GATE_NAME = "capture_timestamps"
SESSION_CONTENT_GATE_NAME = "session_content"
SESSION_PLATE_SOLVE_GATE_NAME = "session_plate_solve"
PHOTOMETRY_WORK_GATE_NAME = "photometry_work"
COMPARISON_ENSEMBLE_GATE_NAME = "comparison_ensemble"
REGISTRATION_DRIFT_GATE_NAME = "registration_drift"
SCATTER_POPULATION_GATE_NAME = "scatter_population"
VARIABILITY_DISCRIMINATION_GATE_NAME = "variability_discrimination"
DETECTABLE_AMPLITUDE_GATE_NAME = "detectable_amplitude"


def photometry_run_gates(
    *,
    frames_contributed: int,
    rejected_frame_count: int,
    frames_without_timestamp: int,
    session_count: int,
    session_empty_reasons: Sequence[str],
    sessions_missing_wcs: Sequence[str],
    no_work_reason: str | None,
    ensemble_sizes: Sequence[int],
    registration_drifts_px: Sequence[float | None],
    stars_with_scatter: int,
    known_variable_cvs: Sequence[float] = (),
    unlisted_cvs: Sequence[float] = (),
    cutoff_cv: float | None = None,
) -> list[GateResult]:
    """Build the gates for one photometry run.

    Parameters
    ----------
    frames_contributed : `int`
        Frames that went into the run's sessions.
    rejected_frame_count : `int`
        Frames rejected as global ensemble outliers.
    frames_without_timestamp : `int`
        Frames left out for having no capture time.
    session_count : `int`
        Sessions the frames were grouped into.
    session_empty_reasons : `Sequence` [`str`]
        One sentence per session that produced no light curves.
    sessions_missing_wcs : `Sequence` [`str`]
        Sessions that could not be plate-solved for cross-session matching.
    no_work_reason : `str` or `None`
        Why the run found nothing to do, if it did not.
    ensemble_sizes : `Sequence` [`int`]
        The number of comparison stars measured in each frame.
    registration_drifts_px : `Sequence` [`float` or `None`]
        Each star's largest frame-to-frame alignment offset, or `None` when
        none was recorded for it.
    stars_with_scatter : `int`
        Stars with at least three usable points, so with a measured scatter.
    known_variable_cvs : `Sequence` [`float`], optional
        The scatter (CV) of each star with a light curve that a catalog lists
        as variable.
    unlisted_cvs : `Sequence` [`float`], optional
        The scatter of each star with a light curve that no catalog lists.
    cutoff_cv : `float` or `None`, optional
        The run's variable-star cutoff on the scatter, or `None` if it has
        none.

    Returns
    -------
    gates : `list` [`GateResult`]
        Ten gates, in a fixed order.
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
        gates.append(
            failed_gate(
                CAPTURE_TIMESTAMP_GATE_NAME,
                f"{frames_without_timestamp} frame(s) excluded for missing capture timestamp",
                float(frames_without_timestamp),
                0.0,
            )
        )
    else:
        gates.append(passed_gate(CAPTURE_TIMESTAMP_GATE_NAME, 0.0, 0.0))

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

    ensemble_source = f"at least {MINIMUM_ENSEMBLE_SIZE} comparison stars measured in every frame"
    if not ensemble_sizes:
        gates.append(
            unchecked_gate(
                COMPARISON_ENSEMBLE_GATE_NAME,
                "no frame was normalized against a comparison ensemble",
                ensemble_source,
            )
        )
    else:
        smallest = min(ensemble_sizes)
        thin = sum(1 for size in ensemble_sizes if size < MINIMUM_ENSEMBLE_SIZE)
        if thin:
            gates.append(
                failed_gate(
                    COMPARISON_ENSEMBLE_GATE_NAME,
                    f"{thin} of {len(ensemble_sizes)} frame(s) were normalized against fewer than "
                    f"{MINIMUM_ENSEMBLE_SIZE} comparison stars (the smallest had {smallest})",
                    float(smallest),
                    float(MINIMUM_ENSEMBLE_SIZE),
                    ensemble_source,
                )
            )
        else:
            gates.append(
                passed_gate(
                    COMPARISON_ENSEMBLE_GATE_NAME,
                    float(smallest),
                    float(MINIMUM_ENSEMBLE_SIZE),
                    ensemble_source,
                )
            )

    drift_source = (
        f"alignment offset of at most {UNSTABLE_TRACKING_DRIFT_PX:g} px (half of the centroid search box)"
    )
    recorded_drifts = [drift for drift in registration_drifts_px if drift is not None]
    if not recorded_drifts:
        gates.append(
            unchecked_gate(
                REGISTRATION_DRIFT_GATE_NAME,
                "no star recorded its frame-to-frame alignment drift",
                drift_source,
            )
        )
    else:
        worst = max(recorded_drifts)
        if worst > UNSTABLE_TRACKING_DRIFT_PX:
            gates.append(
                failed_gate(
                    REGISTRATION_DRIFT_GATE_NAME,
                    f"frame alignment drifted up to {worst:.0f} px, so tracking was probably lost "
                    "and apparent brightness changes may be alignment, not the star",
                    worst,
                    UNSTABLE_TRACKING_DRIFT_PX,
                    drift_source,
                )
            )
        else:
            gates.append(
                passed_gate(REGISTRATION_DRIFT_GATE_NAME, worst, UNSTABLE_TRACKING_DRIFT_PX, drift_source)
            )

    population_source = (
        f"at least {MINIMUM_STARS_FOR_SCATTER_POPULATION} stars with three or more points (design estimate)"
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
    else:
        gates.append(
            passed_gate(
                SCATTER_POPULATION_GATE_NAME,
                float(stars_with_scatter),
                float(MINIMUM_STARS_FOR_SCATTER_POPULATION),
                population_source,
            )
        )

    skill_source = (
        f"at least {MINIMUM_KNOWN_VARIABLES} catalogued variables and "
        f"{MINIMUM_UNLISTED_STARS} unlisted stars; AUC above chance at the 5% level"
    )
    skill = discrimination(known_variable_cvs, unlisted_cvs)
    if skill is None:
        gates.append(
            unchecked_gate(
                VARIABILITY_DISCRIMINATION_GATE_NAME,
                f"the field has {len(known_variable_cvs)} star(s) the catalogs list as variable and "
                f"{len(unlisted_cvs)} they do not, too few to check that the scatter "
                "statistic can see variables",
                skill_source,
            )
        )
    elif skill.sees_known_variables:
        gates.append(
            passed_gate(VARIABILITY_DISCRIMINATION_GATE_NAME, skill.auc, skill.required_auc, skill_source)
        )
    else:
        gates.append(
            failed_gate(
                VARIABILITY_DISCRIMINATION_GATE_NAME,
                f"the scatter statistic does not pick out the {skill.known_variables} stars "
                f"the catalogs list as variable (AUC {skill.auc:.2f}, needs "
                f"{skill.required_auc:.2f}), so variable candidates from this run are not reliable",
                skill.auc,
                skill.required_auc,
                skill_source,
            )
        )

    amplitude_source = f"at most {MAXIMUM_DETECTABLE_AMPLITUDE_MAG:g} mag peak to peak (design estimate)"
    if cutoff_cv is None:
        gates.append(
            unchecked_gate(
                DETECTABLE_AMPLITUDE_GATE_NAME, "the run has no variable-star cutoff", amplitude_source
            )
        )
    else:
        amplitude = minimum_detectable_amplitude_mag(cutoff_cv)
        if amplitude > MAXIMUM_DETECTABLE_AMPLITUDE_MAG:
            gates.append(
                failed_gate(
                    DETECTABLE_AMPLITUDE_GATE_NAME,
                    f"a variable would have to change by about {amplitude:.2f} mag peak to peak "
                    "to clear this run's cutoff, so smaller variables cannot be flagged",
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
    return gates
