"""Purpose: Build the target dataset report for one target.

Description: The report tells the user what is wrong with the data already
taken on one target, and what to take next (`QUALITY_METRICS_PLAN.md` section
6). It is recomputed on every read and never stored. It joins:

* a summary of each capture setting, with its loss account (``aggregates``);
* the statements of the rule table: the plain rules (``judge_results``) and
  the rules that need a join or a fit (``report_rules``);
* the stop signal and "take N more" (``noise_growth`` through ``forecast``);
* a starting point when the target has no frames;
* the checks of earlier forecasts (``forecast``);
* the checklist items the user ticked, returned when the measurements taken
  since show the fix did not work.

What the whole library knows (site, references, sky models, frame
observations) is read by the shared builder, ``library_inputs``.

Missing data is said, never skipped: a rule that could not be checked gives an
``insufficient_data`` statement with its reason. All reading is done in
``target_report_inputs``; this module only decides and assembles.

The checklist return rule
-------------------------
A ticked item of a stored night plan comes back as a ``checklist_return`` on
a statement when the statement is about the item's rule (and its capture
setting, when the item names one) and still fires. For a statement that cites
stored measurements, at least one cited row must have been measured after the
tick, on a night that began on or after the tick's night; rows measured before
the tick describe the world before the fix. A statement that cites no rows
(such as a missing calibration frame) comes back when it still fires, because
it is computed from the library as it is now.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from astrometricslib import (
    AstrometricsError,
    CaptureSetting,
    MeasurementReference,
    QualityMeasurement,
    Target,
    frame_is_spectral,
    observing_night_id,
    resolve_camera_profile,
)
from wayfindinglib.analytics.result_quality.aggregates import (
    MeasuredNoiseCheck,
    capture_setting_summaries,
    loss_account,
)
from wayfindinglib.analytics.result_quality.capture_overheads import (
    AUTOFOCUS_RUNS_PER_HOUR,
    DITHER_EVERY_FRAMES,
    estimate_capture_overheads,
    wall_clock_estimate,
)
from wayfindinglib.analytics.result_quality.efficiency import (
    READ_NOISE_METRIC,
    SKY_RATE_METRIC,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    FrameObservation,
    assess_efficiency,
    baseline_key,
)
from wayfindinglib.analytics.result_quality.judge_results import (
    judge_results,
    needs_attention,
    order_statements,
)
from wayfindinglib.analytics.result_quality.library_inputs import (
    UNKNOWN_SETTING_CAMERA,
    LibraryInputs,
    build_target_observations,
    dominant_setting,
    set_aside_reasons,
    target_direction,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    APERTURE_NOISE_ERROR_FACTOR,
    StackFrame,
    WeightingMode,
    frames_to_cut_noise,
    predict_weighting_mode,
)
from wayfindinglib.analytics.result_quality.reply_budget import (
    EVIDENCE_STEPS,
    fit_reply,
    limit_statement_evidence,
    reply_budget,
)
from wayfindinglib.analytics.result_quality.report_rules import (
    AltitudeSample,
    LightFrameGroup,
    altitude_cost_statement,
    calibration_gap_statement,
    duration_text,
    find_altitude_cost,
    find_calibration_gaps,
    find_sharpness_tradeoff,
    frames_lost_statements,
    insufficient,
    more_frames_statement,
    seeing_limit_statements,
    sharpness_statement,
    sky_cost_statements,
    stop_statement,
)
from wayfindinglib.analytics.result_quality.rules import (
    DEFERRED_RULES,
    MINIMUM_SKY_TO_READ_NOISE_RATIO,
    STAR_WIDTH_MOFFAT_ARCSEC_METRIC,
)
from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_target
from wayfindinglib.analytics.result_quality.starting_point import (
    StartingPointInputs,
    build_starting_point,
    sky_dominated_exposure_seconds,
)
from wayfindinglib.models.planning.target_report import (
    ContextReading,
    ForecastCheck,
    ScienceOutcomeSummary,
    StartingPoint,
    TargetDatasetReport,
)
from wayfindinglib.models.quality.efficiency import ScienceGoal
from wayfindinglib.models.quality.statements import (
    ChecklistReturn,
    Confidence,
    StatementKind,
    TargetStatement,
)
from wayfindinglib.tasks.planning_tasks.target_report_inputs import (
    QUARANTINE_METRICS,
    TargetRows,
    assign_stacks_to_settings,
    frame_altitudes,
    frame_sensor_temperatures,
    median_or_none,
    read_calibration_rows,
    read_target_rows,
    sessions_of_target,
)

logger = logging.getLogger(__name__)

MINIMUM_FRAMES_FOR_FORECAST = 10
"""Fewest usable frames of a setting for "take N more". The new frames copy the
setting's past frames, so ten frames are the least that give a spread of sky
rates to copy. Design estimate, register row WAY-D61."""

EXPOSURE_ROUNDING_SECONDS = 10.0
"""A proposed exposure that follows from the sky is rounded up to a multiple of
this, because nobody sets 47 s. Design estimate, register row WAY-D61."""

STARTING_POINT_SAMPLE_STEP_HOURS = 0.5
"""The sky for the starting point is sampled this often over the next day."""

STARTING_POINT_MINIMUM_ALTITUDE_DEG = 30.0
"""The starting point uses the moonless sky at times the target stands at least
this high (about an airmass of 2), because that is when it would be imaged.
Design estimate, register row WAY-D67."""

SPECTRUM_RULE_NAME = "spectrum_snr"
PHOTOMETRY_RULE_NAME = "photometry_precision"
EFFICIENCY_RULE_NAME = "efficiency_reference"
CALIBRATION_RULE_NAME = "calibration_gap"
FRAMES_LOST_RULE_NAME = "frames_lost"

VARIABLE_CANDIDATE_METRIC = "photometry.variable_candidate_count"
CATALOG_AMPLITUDE_METRIC = "photometry.target_catalog_amplitude_mag"
DETECTABLE_AMPLITUDE_METRIC = "photometry.gate_detectable_amplitude"
SENSOR_TEMPERATURE_METRIC = "frame.sensor_temperature_degc"
NOISE_METRIC = "stacking.empty_aperture_noise_stack_unit"
EXPECTED_NOISE_METRIC = "stacking.expected_empty_aperture_noise_stack_unit"
EFFECTIVE_TIME_METRIC = "stacking.effective_time_s"


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------


def _deferred_statement(name: str, reason: str) -> TargetStatement:
    """Say that a natural goal cannot be checked yet, and why.

    Parameters
    ----------
    name : `str`
        The rule or deferred-rule name of the goal.
    reason : `str`
        What is missing.

    Returns
    -------
    statement : `TargetStatement`
        An ``insufficient_data`` statement with a design-estimate confidence.
    """
    return TargetStatement(
        rule=name,
        kind=StatementKind.INSUFFICIENT_DATA,
        message=f"Cannot check {name.replace('_', ' ')}: data is missing.",
        reason=reason,
        confidence=Confidence.DESIGN_ESTIMATE,
    )


def _references(rows: Sequence[QualityMeasurement]) -> list[MeasurementReference]:
    """Cite rows by id.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        Rows read back from the store.

    Returns
    -------
    references : `list` [`MeasurementReference`]
        One reference for each row that has an id.
    """
    return [MeasurementReference(row_id=row.row_id) for row in rows if row.row_id is not None]


def _newest(rows: Sequence[QualityMeasurement], metric: str) -> QualityMeasurement | None:
    """Pick the newest row of a metric.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        Rows to search.
    metric : `str`
        The metric name.

    Returns
    -------
    row : `QualityMeasurement` or `None`
        The newest row, or ``None`` when there is none.
    """
    matches = [row for row in rows if row.metric == metric]
    return max(matches, key=lambda row: row.measured_at) if matches else None


# -----------------------------------------------------------------------------
# Report parts
# -----------------------------------------------------------------------------


def _science_outcomes(target: Target, rows: Sequence[QualityMeasurement]) -> ScienceOutcomeSummary:
    """Summarize the science results already made from the target.

    Parameters
    ----------
    target : `Target`
        The target, with its pipeline summaries.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.

    Returns
    -------
    outcomes : `ScienceOutcomeSummary`
        Flagged pipelines, asteroid candidate counts, and the number of
        variable star candidates from the stored photometry count (0 when the
        photometry run stored none).
    """
    flagged = [
        name
        for name, summary in (
            ("stacking", target.stacking.quality_summary),
            ("spectral_stacking", target.spectral_stacking.quality_summary),
            ("astrometry", target.quality.astrometry),
            ("photometry", target.quality.photometry),
            ("spectroscopy", target.quality.spectroscopy),
            ("asteroid_detection", target.asteroid_detection.quality_summary),
        )
        if summary is not None and summary.flagged
    ]
    candidates = target.asteroid_detection.candidates
    variable_row = _newest(rows, VARIABLE_CANDIDATE_METRIC)
    return ScienceOutcomeSummary(
        variable_star_candidate_count=(
            int(variable_row.value) if variable_row is not None and variable_row.value is not None else 0
        ),
        asteroid_candidate_count=len(candidates),
        confirmed_asteroid_candidate_count=sum(
            1 for candidate in candidates if candidate.cascade_stage.value == "ephemeris_matched"
        ),
        flagged_pipelines=flagged,
    )


def context_readings(
    rows: Sequence[QualityMeasurement],
    purposes: Mapping[str, Any],
    stack_setting_by_subject: Mapping[str, int] | None = None,
) -> list[ContextReading]:
    """Gather the target's context measurements for the report.

    Every registered context metric that the target has a stored row for is
    shown once for each capture setting, with the newest value only. No list
    of values per frame is kept, so the section stays small however many
    frames the target has.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.
    purposes : `Mapping` [`str`, `MetricSummary`]
        The registered metrics by name; only those with the purpose
        ``context`` are shown.
    stack_setting_by_subject : `Mapping` [`str`, `int`], optional
        The capture setting of each stack subject, so a stack metric is
        shown under its setting.

    Returns
    -------
    readings : `list` [`ContextReading`]
        The readings, in metric-name then setting order.
    """
    stack_setting_by_subject = stack_setting_by_subject or {}
    groups: dict[tuple[str, int | None], list[QualityMeasurement]] = defaultdict(list)
    for row in rows:
        summary = purposes.get(row.metric)
        if summary is None or summary.purpose.value != "context":
            continue
        setting_id = row.subject.capture_setting_id
        if setting_id is None:
            setting_id = stack_setting_by_subject.get(row.subject.subject_id)
        groups[row.metric, setting_id].append(row)
    readings: list[ContextReading] = []
    for metric, setting_id in sorted(groups, key=lambda key: (key[0], -1 if key[1] is None else key[1])):
        group = groups[metric, setting_id]
        newest = max(group, key=lambda row: (row.measured_at, row.row_id or 0))
        readings.append(
            ContextReading(
                metric=metric,
                level=newest.subject.level.value,
                value=newest.value,
                unit=purposes[metric].unit,
                captureSettingId=setting_id,
                rowCount=len(group),
                rowId=newest.row_id,
            )
        )
    return readings


def fit_report_to_budget(report: TargetDatasetReport, budget: int | None = None) -> TargetDatasetReport:
    """Shrink the report until its JSON fits the reply budget.

    Two things can go, in this order. First the context section, which is only
    a convenience: the statements cite the rows they used, so the report
    says why the section is empty. Then the long evidence lists, cut to
    ``EVIDENCE_STEPS`` references per statement in turn, each statement
    counting what it lost. The statements, their order and the counts of the
    warnings never change.

    Parameters
    ----------
    report : `TargetDatasetReport`
        The finished report.
    budget : `int`, optional
        The most characters its JSON may have, measured the way the MCP tool
        registry measures a reply. `reply_budget` when omitted.

    Returns
    -------
    report : `TargetDatasetReport`
        The same report when it fits. Otherwise a smaller copy; when even the
        smallest step does not fit, that smallest copy.
    """
    limit = reply_budget() if budget is None else budget

    def shrink(current: TargetDatasetReport, round_number: int) -> TargetDatasetReport | None:
        """Make the report one step smaller.

        Parameters
        ----------
        current : `TargetDatasetReport`
            The report so far.
        round_number : `int`
            0 drops the context; later rounds cut the evidence lists.

        Returns
        -------
        smaller : `TargetDatasetReport` or `None`
            The smaller report, or ``None`` when nothing is left to cut.
        """
        if round_number == 0:
            return current.model_copy(
                update={
                    "context": [],
                    "context_omitted_reason": (
                        "The context readings were left out so the report fits the reply budget of "
                        f"{limit:,} characters. The statements still cite the rows they used; read "
                        "any metric with QualityRecords.query."
                    ),
                }
            )
        if round_number > len(EVIDENCE_STEPS):
            return None
        keep = EVIDENCE_STEPS[round_number - 1]
        return current.model_copy(update={"statements": limit_statement_evidence(current.statements, keep)})

    return fit_reply(report, shrink, limit)


def _measured_noise_checks(
    rows: Sequence[QualityMeasurement],
    stack_settings: Mapping[str, int],
    settings: Mapping[int, CaptureSetting],
) -> dict[CaptureSetting, MeasuredNoiseCheck]:
    """Compare each stack's measured noise with the noise its frames predict.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The target's newest rows.
    stack_settings : `Mapping` [`str`, `int`]
        The setting id of each stack.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The target's settings.

    Returns
    -------
    checks : `dict` [`CaptureSetting`, `MeasuredNoiseCheck`]
        For each setting with a stack, the variance ratio and its error. The
        error is ``2 * 1.17 / sqrt(n)`` times the ratio for n apertures
        (unpaired). A stack missing either noise value is left out.
    """
    checks: dict[CaptureSetting, MeasuredNoiseCheck] = {}
    for stack_id, setting_id in stack_settings.items():
        stack_rows = [row for row in rows if row.subject.subject_id == stack_id]
        measured = _newest(stack_rows, NOISE_METRIC)
        expected = _newest(stack_rows, EXPECTED_NOISE_METRIC)
        if (
            measured is None
            or expected is None
            or measured.value is None
            or expected.value is None
            or measured.value <= 0.0
            or expected.value <= 0.0
            or not measured.sample_count
            or measured.sample_count < 2
        ):
            continue
        ratio = (measured.value / expected.value) ** 2
        error = 2.0 * APERTURE_NOISE_ERROR_FACTOR / math.sqrt(measured.sample_count) * ratio
        checks[settings[setting_id]] = MeasuredNoiseCheck(variance_ratio=ratio, variance_ratio_error=error)
    return checks


def _weight_effective_seconds(
    rows: Sequence[QualityMeasurement],
    stack_settings: Mapping[str, int],
    settings: Mapping[int, CaptureSetting],
) -> dict[CaptureSetting, float]:
    """Read the stacker's weighted effective time of each setting.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The target's newest rows.
    stack_settings : `Mapping` [`str`, `int`]
        The setting id of each stack.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The target's settings.

    Returns
    -------
    seconds : `dict` [`CaptureSetting`, `float`]
        The effective time (``stacking.effective_time_s``) of the combined
        stack tied to each setting. Exposure-group stacks are left out so
        the time is not counted twice.
    """
    seconds: dict[CaptureSetting, float] = {}
    for stack_id, setting_id in stack_settings.items():
        if "/groups/" in stack_id or "#group-" in stack_id:
            continue
        row = _newest([r for r in rows if r.subject.subject_id == stack_id], EFFECTIVE_TIME_METRIC)
        if row is not None and row.value is not None:
            seconds[settings[setting_id]] = float(row.value)
    return seconds


def _set_aside_parts(
    reasons_by_frame: Mapping[str, str],
    observations: Sequence[FrameObservation],
    rows: Sequence[QualityMeasurement],
) -> tuple[
    dict[CaptureSetting, Counter],
    dict[CaptureSetting, list[MeasurementReference]],
    Counter,
]:
    """Match the set-aside frames with the measured frames.

    Parameters
    ----------
    reasons_by_frame : `Mapping` [`str`, `str`]
        The reason of each set-aside frame, by frame subject id
        (`set_aside_reasons`).
    observations : `Sequence` [`FrameObservation`]
        The frames with stored measurements.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.

    Returns
    -------
    reasons_by_setting, evidence_by_setting, unassigned : `tuple`
        The reasons counted for each setting; the stored quarantine rows of
        the matched frames for each setting; and the reasons counted for
        frames no stored measurement could place.
    """
    by_id = {observation.frame_id: observation for observation in observations}
    quarantine_rows: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in rows:
        if row.metric in QUARANTINE_METRICS:
            quarantine_rows[row.subject.subject_id].append(row)
    reasons_by_setting: dict[CaptureSetting, Counter] = defaultdict(Counter)
    evidence_by_setting: dict[CaptureSetting, list[MeasurementReference]] = defaultdict(list)
    unassigned: Counter = Counter()
    for frame_id, reason in reasons_by_frame.items():
        observation = by_id.get(frame_id)
        if observation is None or observation.capture_setting.camera == UNKNOWN_SETTING_CAMERA:
            unassigned[reason] += 1
            continue
        reasons_by_setting[observation.capture_setting][reason] += 1
        evidence_by_setting[observation.capture_setting].extend(
            _references(quarantine_rows.get(frame_id, []))
        )
    return reasons_by_setting, evidence_by_setting, unassigned


def _stack_frames(
    observations: Sequence[FrameObservation],
) -> tuple[list[StackFrame], list[FrameObservation], list[str]]:
    """Build the forward model's frames from the kept frames of one setting.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The frames of one setting.

    Returns
    -------
    frames, used, problems : `tuple`
        The usable frames, the observations they came from, and one sentence
        for each frame left out. A frame needs an exposure, a sky rate and a
        read noise; a missing star width matters only for width weights.
    """
    frames: list[StackFrame] = []
    used: list[FrameObservation] = []
    problems: list[str] = []
    for observation in observations:
        if observation.set_aside_reason is not None:
            continue
        exposure = observation.effective_exposure_seconds
        if (
            exposure is None
            or observation.sky_rate_e_per_s_per_px is None
            or observation.read_noise_e is None
        ):
            problems.append(
                f"{observation.frame_id}: "
                + ("; ".join(observation.missing_reasons.values()) or "no exposure, sky rate or read noise.")
            )
            continue
        frames.append(
            StackFrame(
                frame_id=observation.frame_id,
                exposure_seconds=float(exposure),
                sky_rate_e_per_s_per_px=float(observation.sky_rate_e_per_s_per_px),
                read_noise_e=float(observation.read_noise_e),
                star_width=observation.star_width_effective_arcsec,
            )
        )
        used.append(observation)
    return frames, used, problems


def _more_frames_for_setting(
    setting: CaptureSetting,
    observations: Sequence[FrameObservation],
    rows: Sequence[QualityMeasurement],
    configured_weight: str | None,
    overheads: Any,
) -> list[TargetStatement]:
    """Make the "take N more" statement of one capture setting.

    The new frames copy the setting's past frames. When the past frames were
    limited by read noise, the new frames use the exposure at which the sky
    dominates instead (plan section 6.4), and the reason says so.

    Parameters
    ----------
    setting : `CaptureSetting`
        The setting.
    observations : `Sequence` [`FrameObservation`]
        The setting's frames.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows, to cite.
    configured_weight : `str` or `None`
        The stacker's configured weight mode.
    overheads : `CaptureOverheads`
        Download and settle times.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation, or one ``insufficient_data`` statement saying
        what is missing.
    """
    frames, used, problems = _stack_frames(observations)
    if len(frames) < MINIMUM_FRAMES_FOR_FORECAST:
        reason = (
            f"{len(frames)} frames have an exposure, a sky rate and a read noise; the forecast needs "
            f"{MINIMUM_FRAMES_FOR_FORECAST}. Measure the target's frames (QualityRecords.measure_frames)."
        )
        if problems:
            reason += " First problem: " + problems[0]
        return [insufficient("more_frames", reason, setting)]
    mode = predict_weighting_mode(
        configured_weight,
        [frame.exposure_seconds for frame in frames],
        is_spectral=frame_is_spectral({"filter": setting.filter_name}),
    )
    if mode == WeightingMode.WFWHM and any(frame.star_width is None for frame in frames):
        return [
            insufficient(
                "more_frames",
                "The configured weights use star width, and some frames have none. Measure star widths.",
                setting,
            )
        ]
    new_exposure = setting.exposure_seconds or statistics.median(frame.exposure_seconds for frame in frames)
    note = ""
    sky_noise = median_or_none(
        math.sqrt(o.sky_rate_e_per_s_per_px * (o.effective_exposure_seconds or 0.0))
        for o in used
        if o.sky_rate_e_per_s_per_px
    )
    read_noise = median_or_none(o.read_noise_e for o in used)
    if sky_noise is not None and read_noise and sky_noise / read_noise < MINIMUM_SKY_TO_READ_NOISE_RATIO:
        sky_rate = median_or_none(o.sky_rate_e_per_s_per_px for o in used)
        if sky_rate:
            wanted = sky_dominated_exposure_seconds(sky_rate, read_noise)
            rounded = math.ceil(wanted / EXPOSURE_ROUNDING_SECONDS) * EXPOSURE_ROUNDING_SECONDS
            if rounded > new_exposure:
                note = (
                    f"The earlier {new_exposure:g} s frames were limited by read noise, "
                    f"so the new frames use {rounded:g} s, where the sky dominates. "
                    "Check that the brightest star will not clip."
                )
                new_exposure = rounded
    result = frames_to_cut_noise(frames, frames, mode, new_exposure_seconds=new_exposure)
    used_ids = {frame.frame_id for frame in frames}
    evidence = _references([
        row
        for row in rows
        if row.subject.subject_id in used_ids and row.metric in (SKY_RATE_METRIC, READ_NOISE_METRIC)
    ])
    statement = more_frames_statement(result, setting, overheads, evidence, exposure_note=note)
    if statement is None or result.frames_needed is None or result.new_exposure_seconds is None:
        return [
            insufficient("more_frames", result.reason or "The number of frames could not be found.", setting)
        ]
    return [_with_full_wall_clock(statement, result.frames_needed, result.new_exposure_seconds, overheads)]


def _with_full_wall_clock(
    statement: TargetStatement, frame_count: int, exposure_seconds: float, overheads: Any
) -> TargetStatement:
    """Price the new frames with their dithers and autofocus runs as well.

    The rule that builds the statement prices downloads only. Tonight's
    ranking also prices dithers and autofocus runs, with the cadence of
    `DITHER_EVERY_FRAMES` and `AUTOFOCUS_RUNS_PER_HOUR`. The two must price an
    hour of imaging the same way, so the wall-clock time and the sentence that
    states it are redone here with the same cadence.

    Parameters
    ----------
    statement : `TargetStatement`
        The "take N more" recommendation.
    frame_count : `int`
        Frames to take.
    exposure_seconds : `float`
        Exposure of each.
    overheads : `CaptureOverheads`
        Download, settle and autofocus times.

    Returns
    -------
    statement : `TargetStatement`
        A copy with the full wall-clock time in its message and effect.
    """
    downloads_only = wall_clock_estimate(frame_count, exposure_seconds, overheads)
    without_focus = wall_clock_estimate(
        frame_count, exposure_seconds, overheads, dither_every_frames=DITHER_EVERY_FRAMES
    )
    # One autofocus run for each hour of imaging, counting a part hour as one.
    focus_runs = max(1, math.ceil(without_focus.total_seconds / 3600.0 * AUTOFOCUS_RUNS_PER_HOUR))
    full = wall_clock_estimate(
        frame_count,
        exposure_seconds,
        overheads,
        dither_every_frames=DITHER_EVERY_FRAMES,
        autofocus_runs=focus_runs,
    )
    old_text = duration_text(downloads_only.total_seconds) + " with downloads"
    new_text = duration_text(full.total_seconds) + " with downloads, dithers and refocusing"
    update: dict[str, Any] = {"message": statement.message.replace(old_text, new_text)}
    if statement.expected_effect is not None:
        update["expected_effect"] = statement.expected_effect.model_copy(
            update={"wall_clock_seconds": full.total_seconds}
        )
    return statement.model_copy(update=update)


def _quality_statements_of_settings(
    target: Target,
    goal: ScienceGoal,
    target_rows: TargetRows,
    observations: Sequence[FrameObservation],
    summaries: Sequence[Any],
    runs: Sequence[Any],
    stack_settings: Mapping[str, int],
    context: LibraryInputs,
    config: Any,
    contexts_for_overheads: Sequence[Any],
) -> list[TargetStatement]:
    """Make the statements that are about the target's own stack and frames.

    Covers the stop signal or "take N more", the sharpness trade-off and the
    cost of low altitude.

    Parameters
    ----------
    target : `Target`
        The target.
    goal : `ScienceGoal`
        The target's science goal, for the efficiency of each frame.
    target_rows : `TargetRows`
        The target's rows and settings.
    observations : `Sequence` [`FrameObservation`]
        The frames.
    summaries : `Sequence` [`CaptureSettingSummary`]
        The setting summaries.
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.
    stack_settings : `Mapping` [`str`, `int`]
        The setting id of each stack.
    context : `LibraryInputs`
        Library-wide references.
    config : `AppConfiguration`
        The configuration, for the stacker's weight mode.
    contexts_for_overheads : `Sequence` [`EkosSessionContext`]
        The stored Ekos session records, for the download times.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements.
    """
    from wayfindinglib.analytics.result_quality.forecast import target_stop_signal

    statements: list[TargetStatement] = []
    altitudes = frame_altitudes(target)
    configured_weight = config.get_stack_weight() if hasattr(config, "get_stack_weight") else None
    stopped = False
    signal = target_stop_signal(runs) if runs else None
    dominant = dominant_setting(observations)
    if signal is not None and signal.stopped:
        stopped = True
        evidence = [
            MeasurementReference(row_id=run.noise_row_id) for run in runs if run.noise_row_id is not None
        ]
        effective = None
        for stack_id in stack_settings:
            row = _newest(
                [r for r in target_rows.rows if r.subject.subject_id == stack_id], EFFECTIVE_TIME_METRIC
            )
            if row is not None and row.value is not None and "#group-" not in stack_id:
                effective = float(row.value)
        statements.append(stop_statement(signal, dominant, effective, evidence))
    elif runs and signal is not None and signal.stopped is None:
        statements.append(
            insufficient(
                "more_frames_will_not_help",
                f"The stop signal could not be read: {signal.reason}",
                dominant,
            )
        )
    by_setting: dict[CaptureSetting, list[FrameObservation]] = defaultdict(list)
    for observation in observations:
        by_setting[observation.capture_setting].append(observation)
    for summary in summaries:
        setting = summary.capture_setting
        setting_observations = by_setting.get(setting, [])
        if not stopped:
            overheads = estimate_capture_overheads(
                contexts_for_overheads, setting.exposure_seconds, setting.filter_name
            )
            statements.extend(
                _more_frames_for_setting(
                    setting, setting_observations, target_rows.rows, configured_weight, overheads
                )
            )
        frames, used, _problems = _stack_frames(setting_observations)
        widths = [_width_row_value(target_rows.rows, observation.frame_id) for observation in used]
        if frames and all(width is not None for width in widths):
            mode = predict_weighting_mode(
                configured_weight,
                [frame.exposure_seconds for frame in frames],
                is_spectral=frame_is_spectral({"filter": setting.filter_name}),
            )
            tradeoff = find_sharpness_tradeoff(frames, [float(w) for w in widths if w is not None], mode)
            if tradeoff is not None:
                statements.append(
                    sharpness_statement(
                        tradeoff,
                        setting,
                        _references([
                            row
                            for row in target_rows.rows
                            if row.metric == STAR_WIDTH_MOFFAT_ARCSEC_METRIC
                            and row.subject.subject_id in {f.frame_id for f in frames}
                        ]),
                    )
                )
        samples = []
        evidence_rows: list[QualityMeasurement] = []
        efficiency = None
        reference = context.reference_for(setting)
        if reference is not None:
            efficiency = assess_efficiency(
                [o for o in setting_observations if o.set_aside_reason is None], goal, reference
            )
        if efficiency is not None:
            by_frame_exposure = {o.frame_id: o for o in setting_observations}
            for result in efficiency.frame_results:
                altitude = altitudes.get(result.frame_id)
                if result.eta is not None and altitude is not None:
                    samples.append(AltitudeSample(altitude, result.eta, result.exposure_seconds))
                    observation = by_frame_exposure.get(result.frame_id)
                    if observation is not None:
                        evidence_rows.extend(
                            row
                            for row in target_rows.rows
                            if row.subject.subject_id == observation.frame_id
                            and row.metric in (SKY_RATE_METRIC, "frame.star_width_effective_arcsec")
                        )
        cost = find_altitude_cost(samples)
        if cost is not None:
            statements.append(altitude_cost_statement(cost, setting, _references(evidence_rows)))
    return statements


def _width_row_value(rows: Sequence[QualityMeasurement], frame_id: str) -> float | None:
    """Read a frame's Moffat star width in arcseconds.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.
    frame_id : `str`
        The frame's subject id.

    Returns
    -------
    width : `float` or `None`
        The value of the newest ``frame.star_width_moffat_arcsec`` row of
        the frame, or ``None`` when there is none or it was not measured.
    """
    row = _newest([r for r in rows if r.subject.subject_id == frame_id], STAR_WIDTH_MOFFAT_ARCSEC_METRIC)
    return float(row.value) if row is not None and row.value is not None and row.value > 0.0 else None


def _calibration_statements(
    target: Target,
    summaries: Sequence[Any],
    observations: Sequence[FrameObservation],
    astrometrics: Any,
    rows: Sequence[QualityMeasurement],
) -> list[TargetStatement]:
    """Make the calibration warnings, tied to the library's rows.

    Parameters
    ----------
    target : `Target`
        The target; its frame records give the sensor temperatures.
    summaries : `Sequence` [`CaptureSettingSummary`]
        The setting summaries.
    observations : `Sequence` [`FrameObservation`]
        The frames.
    astrometrics : `Astrometrics`
        The science library handle.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows (the sensor temperature rows are used when
        the frame record has none).

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation for each missing dark or flat, or one
        ``insufficient_data`` statement when the library cannot be read.
    """
    if not summaries:
        return []
    darks, flats, problem = read_calibration_rows(astrometrics)
    if problem is not None:
        return [insufficient(CALIBRATION_RULE_NAME, problem)]
    temperatures = frame_sensor_temperatures(target)
    for row in rows:
        if row.metric == SENSOR_TEMPERATURE_METRIC and row.value is not None:
            temperatures.setdefault(row.subject.subject_id, float(row.value))
    groups = []
    for summary in summaries:
        frame_ids = [o.frame_id for o in observations if o.capture_setting == summary.capture_setting]
        groups.append(
            LightFrameGroup(
                capture_setting=summary.capture_setting,
                sensor_temperature_c=median_or_none(temperatures.get(frame_id) for frame_id in frame_ids),
                frame_count=summary.frames_taken,
            )
        )
    return [calibration_gap_statement(finding) for finding in find_calibration_gaps(groups, darks, flats)]


def _natural_goal_statements(
    goal: ScienceGoal, rows: Sequence[QualityMeasurement], target_has_photometry: bool
) -> list[TargetStatement]:
    """Say what is missing for the natural finish lines.

    Parameters
    ----------
    goal : `ScienceGoal`
        The target's science goal.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows.
    target_has_photometry : `bool`
        Whether a photometry run stored any row for the target.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        An ``insufficient_data`` statement for each natural goal that has no
        calibration yet.
    """
    statements: list[TargetStatement] = []
    deferred = {entry.name: entry for entry in DEFERRED_RULES}
    if goal == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION:
        entry = deferred["spectrum_snr"]
        statements.append(
            _deferred_statement(SPECTRUM_RULE_NAME, f"{entry.reason} To unblock: {entry.unblocked_by}")
        )
    if target_has_photometry:
        catalog = _newest(rows, CATALOG_AMPLITUDE_METRIC)
        detectable = _newest(rows, DETECTABLE_AMPLITUDE_METRIC)
        if catalog is None or catalog.value is None:
            reason = (
                "The target's catalog amplitude (the magnitude range in its variable "
                "star record) is not stored, so there is no finish line for the "
                "photometry. Run the photometry again, or state an amplitude."
            )
        else:
            reason = (
                f"The catalog amplitude of {catalog.value:.2f} mag is stored"
                + (
                    f" and the run's detectable amplitude is {detectable.value:.2f} mag"
                    if detectable is not None and detectable.value is not None
                    else ""
                )
                + ", but no calibration yet turns that gap into a number of frames."
            )
        statements.append(_deferred_statement(PHOTOMETRY_RULE_NAME, reason))
    return statements


def _checklist_returns(
    statements: Sequence[TargetStatement], sessions: Sequence[Any], rows: Sequence[QualityMeasurement]
) -> list[TargetStatement]:
    """Attach a checklist return where a ticked fix did not hold.

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements of the report.
    sessions : `Sequence` [`ObservationSession`]
        The target's stored night plans.
    rows : `Sequence` [`QualityMeasurement`]
        The target's stored rows, to find when the cited rows were measured.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements, with ``checklist_return`` set where a ticked item
        came back. See the module description for the rule.
    """
    by_id = {row.row_id: row for row in rows if row.row_id is not None}
    ticked = [
        (item, item.ticked_at)
        for session in sessions
        for item in session.checklist
        if item.ticked_at is not None
    ]
    if not ticked:
        return list(statements)
    result: list[TargetStatement] = []
    for statement in statements:
        returned: ChecklistReturn | None = None
        if statement.kind in (StatementKind.WARNING, StatementKind.RECOMMENDATION):
            for item, ticked_at in ticked:
                if item.rule != statement.rule:
                    continue
                if item.capture_setting is not None and item.capture_setting != statement.capture_setting:
                    continue
                tick_night = observing_night_id(ticked_at.timestamp())
                cited = [by_id[ref.row_id] for ref in statement.evidence if ref.row_id in by_id]
                since = [
                    row
                    for row in cited
                    if row.measured_at > ticked_at
                    and (
                        row.subject.observing_night_id is None or row.subject.observing_night_id >= tick_night
                    )
                ]
                if cited and not since:
                    continue
                newest = max((row.measured_at for row in since), default=None)
                reason = f"Ticked {ticked_at:%b %d}; the rule still fires" + (
                    f" on measurements taken {newest:%b %d}."
                    if newest is not None
                    else " on the library as it is now."
                )
                returned = ChecklistReturn(item_id=item.item_id, ticked_at=ticked_at, reason=reason)
                break
        result.append(statement.model_copy(update={"checklist_return": returned}) if returned else statement)
    return result


# -----------------------------------------------------------------------------
# Starting point
# -----------------------------------------------------------------------------


def _camera_spellings(camera_name: str, config: Any) -> list[str]:
    """List the names a camera may be stored under in the measurement store.

    The catalog name, the FITS header name and the library record name can
    differ, and the store matches the camera name exactly.

    Parameters
    ----------
    camera_name : `str`
        The camera's name in the equipment catalog.
    config : `AppConfiguration`
        The configuration that holds the camera profiles.

    Returns
    -------
    spellings : `list` [`str`]
        The catalog name first, then the profile's names, without repeats.
    """
    from astrometricslib import ConfigurationError, resolve_camera_profile

    names = [camera_name]
    try:
        profile = resolve_camera_profile(camera_name, config)
    except ConfigurationError:
        return names
    names.extend([profile.camera_name, profile.record_name, *profile.name_aliases])
    return list(dict.fromkeys(name for name in names if name))


def _provenance_text(value: Any) -> str:
    """Say where a camera profile value came from.

    Parameters
    ----------
    value : `ProvenancedValue`
        A profile value that carries its provenance.

    Returns
    -------
    text : `str`
        The kind (measured, datasheet, assumed) and the source.
    """
    return f"{value.provenance.kind}: {value.provenance.source}"


def _clipping_note(camera_name: str, config: Any, goal: ScienceGoal) -> str:
    """Write what is known about the camera's limits and the star's peak.

    The numbers come from the camera's stored profile and are never typed in
    here: the clip ceiling (where a saturated pixel tops out), the saturation
    threshold (where a raw frame counts a pixel as saturated) and the
    photometric linearity limit (where brightness stops being proportional to
    light), each with its source.

    Parameters
    ----------
    camera_name : `str`
        The active camera.
    config : `AppConfiguration`
        The configuration that holds the camera profiles.
    goal : `ScienceGoal`
        The target's goal. A slitless spectrum is capped by its zero-order
        image, a different star peak, and says so.

    Returns
    -------
    note : `str`
        Why no cap is computed, and the camera's limits.
    """
    if goal == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION:
        note = (
            "Not computed. A slitless spectrum is capped by the zero-order image of the brightest star, "
            "which keeps a share of the star's light in one point. That share depends on the grating, "
            "and no stored measurement gives it, so the zero-order peak cannot be predicted."
        )
    else:
        note = (
            "Not computed. No photometric zero point that is not exploratory exists, so the star's peak "
            "cannot be predicted."
        )
    try:
        profile = resolve_camera_profile(camera_name, config)
    except AstrometricsError:
        return note + " The camera's saturation limits are not known."
    clip, saturation = profile.clip_ceiling_adu, profile.saturation_threshold_adu
    note += (
        f" The camera's pixels top out at {clip.value:.0f} ADU ({_provenance_text(clip)}); a raw frame "
        f"counts a pixel as saturated at or above {saturation.value:.0f} ADU "
        f"({_provenance_text(saturation)})."
    )
    if not profile.saturation_threshold_can_be_reached:
        note += (
            " The threshold is above the ceiling, so no pixel can reach it and saturation would go unseen."
        )
    linearity = profile.photometric_linearity_limit_adu
    if linearity is not None:
        note += (
            f" Brightness stops being proportional to light above {linearity.value:.0f} ADU "
            f"({_provenance_text(linearity)})."
        )
    return note


def _starting_point(
    target: Target, goal: ScienceGoal, context: LibraryInputs, config: Any, at: datetime
) -> StartingPoint:
    """Build the starting point of a target with no frames.

    The equipment is the active camera. The capture setting is the one the
    camera's library frames use most among those that suit the goal (the
    spectroscopy filter for a spectrum, any other filter otherwise). The sky
    rate is the sky model's moonless prediction at the times in the next day
    when the target stands above `STARTING_POINT_MINIMUM_ALTITUDE_DEG`;
    without a model it is the median of the library's own sky rates for that
    equipment, which includes the Moon. The read noise is the median measured
    read noise of the equipment's library frames.

    Parameters
    ----------
    target : `Target`
        The target.
    goal : `ScienceGoal`
        The target's goal, read from its type.
    context : `LibraryInputs`
        Library-wide frames and sky model.
    config : `AppConfiguration`
        The configuration, for the active equipment.
    at : `datetime`
        The time to start from.

    Returns
    -------
    starting_point : `StartingPoint`
        The starting point. It is labeled a design estimate.
    """
    from wayfindinglib.analytics.result_quality.observing_values import sky_geometry
    from wayfindinglib.analytics.result_quality.sky_model import predict_sky_rate
    from wayfindinglib.data_access.equipment_catalog_reader import get_equipment_catalog

    def missing(reason: str) -> StartingPoint:
        """Build a starting point that says why nothing was computed.

        Parameters
        ----------
        reason : `str`
            Why.

        Returns
        -------
        starting_point : `StartingPoint`
            One with no numbers.
        """
        return build_starting_point(
            StartingPointInputs(None, None, reason, None, reason, None, None, f"Not computed. {reason}")
        )

    catalog = get_equipment_catalog(config)
    camera = catalog.active_camera()
    if camera is None:
        return missing(
            "No camera is active in the equipment catalog, so there is no equipment to start from."
        )
    observations: list[FrameObservation] = []
    for spelling in _camera_spellings(camera.name, config):
        observations = [o for o in context.camera_observations(spelling) if o.set_aside_reason is None]
        if observations:
            break
    if not observations:
        return missing(
            f"The library holds no measured frames from the camera {camera.name}, so the sky and the "
            "read noise are not known for it."
        )
    wants_spectral = goal == ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION
    suited = [
        o
        for o in observations
        if frame_is_spectral({"filter": o.capture_setting.filter_name}) == wants_spectral
    ]
    goal_note = ""
    if not suited:
        suited = observations
        goal_note = (
            f" The camera has no {'spectroscopy' if wants_spectral else 'imaging'} frames in the library, "
            "so the setting does not suit the target's goal."
        )
    best_setting = Counter(o.capture_setting for o in suited).most_common(1)[0][0]
    key = baseline_key(best_setting)
    same_equipment = [o for o in observations if baseline_key(o.capture_setting) == key]
    read_noise = median_or_none(o.read_noise_e for o in same_equipment)
    read_note = (
        f"Read noise: the median of {len(same_equipment)} measured frames of {key}."
        if read_noise
        else f"No measured read noise exists for {key}."
    )
    direction = target_direction(target)
    model, model_reason = context.sky_model_for(best_setting)
    sky_rate: float | None = None
    sky_note = ""
    if model is not None and direction is not None:
        site = context.site
        step = STARTING_POINT_SAMPLE_STEP_HOURS * 3600.0
        epochs = [
            at.timestamp() + index * step for index in range(int(24 / STARTING_POINT_SAMPLE_STEP_HOURS))
        ]
        geometries = sky_geometry(
            epochs,
            [direction[0]] * len(epochs),
            [direction[1]] * len(epochs),
            site.latitude_deg,
            site.longitude_deg,
            site.elevation_m,
        )
        rates = [
            prediction.moonless_sky_rate_e_per_s_per_px
            for geometry in geometries
            if geometry.altitude_deg >= STARTING_POINT_MINIMUM_ALTITUDE_DEG
            for prediction in [predict_sky_rate(model, geometry)]
            if prediction.moonless_sky_rate_e_per_s_per_px
        ]
        if rates:
            sky_rate = float(statistics.median(rates))
            sky_note = (
                f"Sky: the sky model's moonless rate for {key} while the target is above "
                f"{STARTING_POINT_MINIMUM_ALTITUDE_DEG:.0f} degrees in the next day "
                "(a moonless sky gives the longest exposure the rule asks for)."
            )
        else:
            sky_note = (
                f"The target does not rise above {STARTING_POINT_MINIMUM_ALTITUDE_DEG:.0f} "
                "degrees in the next day."
            )
    if sky_rate is None:
        fallback = median_or_none(o.sky_rate_e_per_s_per_px for o in same_equipment)
        if fallback:
            sky_rate = fallback
            sky_note = (
                f"Sky: the median measured rate of {key}, Moon included ({model_reason or 'no direction'}); "
                "it is brighter than a moonless sky, so the exposure may be shorter than needed."
            )
        elif not sky_note:
            sky_note = f"No measured sky rate exists for {key}."
    return build_starting_point(
        StartingPointInputs(
            capture_setting=best_setting,
            sky_rate_e_per_s_per_px=sky_rate,
            sky_note=sky_note + goal_note,
            read_noise_e=read_noise,
            read_noise_note=read_note,
            peak_rate_adu_per_s=None,
            room_adu=None,
            clipping_note=_clipping_note(camera.name, config, goal),
        )
    )


# -----------------------------------------------------------------------------
# The report
# -----------------------------------------------------------------------------


def build_target_dataset_report(
    target: Target,
    *,
    astrometrics: Any,
    butler: Any,
    config: Any,
    at: datetime | None = None,
) -> TargetDatasetReport:
    """Build the report on one target's data.

    Parameters
    ----------
    target : `Target`
        The target.
    astrometrics : `Astrometrics`
        The science library handle. Its ``quality``, ``processing`` and
        ``targets`` parts are read.
    butler : `DiskButler`
        The storage layer, for the stored night plans, the Ekos session
        records and the site.
    config : `AppConfiguration`
        The configuration, for the equipment and the stacker's weight mode.
    at : `datetime`, optional
        The time the report is for. Used only by the starting point.
        Defaults to now.

    Returns
    -------
    report : `TargetDatasetReport`
        The summaries, the statements in priority order, how many warnings
        need attention, the starting point when there are no frames, the
        forecast checks and the science outcomes. Nothing is stored.
    """
    from wayfindinglib.analytics.result_quality.forecast import (
        PINNED_STACK_METRICS,
        forecast_check,
        stack_runs_from_measurements,
    )

    now = at or datetime.now(UTC)
    target_rows = read_target_rows(astrometrics, target.id)
    rows = target_rows.rows
    goal = science_goal_for_target(target)
    context = LibraryInputs(astrometrics, butler, config)
    statements: list[TargetStatement] = []
    notes: list[str] = []
    configured_weight = config.get_stack_weight() if hasattr(config, "get_stack_weight") else None

    # Stack history: the stop signal, the forecast checks and the stack map.
    stack_rows: list[QualityMeasurement] = []
    for metric in PINNED_STACK_METRICS:
        stack_rows.extend(
            astrometrics.quality.query(
                metric=metric, target_id=target.id, library_only=True, latest_only=False
            )
        )
    runs = stack_runs_from_measurements(stack_rows)
    newest_members: dict[str, list[str]] = {}
    for run in runs:
        newest_members[run.stack_subject_id] = run.member_frame_ids
    stack_settings, assignment_notes = assign_stacks_to_settings(rows, target_rows.settings, newest_members)
    target_rows.notes.extend(assignment_notes)
    stack_setting_by_subject = dict(stack_settings)

    # Set-aside frames (the rows that say so) and the frames' observations.
    reasons_by_frame = set_aside_reasons(rows)
    observations = build_target_observations(target, rows, target_rows.settings, reasons_by_frame)
    reasons_by_setting, evidence_by_setting, unassigned = _set_aside_parts(
        reasons_by_frame, observations, rows
    )
    if reasons_by_frame:
        notes.append(
            f"{len(reasons_by_frame)} frames are recorded as moved to the _excluded folder. The stored row "
            "says they were set aside but not why (clouded or trailed), so the reason shown is the row's own "
            "words."
        )
    else:
        notes.append(
            "No frame is recorded as set aside in the stored rows. The stacker does not store set-aside "
            "frames one by one, so the set-aside time may be higher than shown."
        )

    # Summaries.
    moonless = context.moonless_sky_rates(observations)
    summaries = capture_setting_summaries(
        observations,
        goal,
        context.reference_for,
        weight_effective_seconds_by_setting=_weight_effective_seconds(
            rows, stack_setting_by_subject, target_rows.settings
        ),
        measured_noise_check_by_setting=_measured_noise_checks(
            rows, stack_setting_by_subject, target_rows.settings
        ),
    )
    checks = _measured_noise_checks(rows, stack_setting_by_subject, target_rows.settings)
    refreshed = []
    for summary in summaries:
        reference = context.reference_for(summary.capture_setting)
        if reference is None:
            refreshed.append(summary)
            continue
        setting_observations = [o for o in observations if o.capture_setting == summary.capture_setting]
        result = loss_account(
            setting_observations,
            [],
            goal,
            lambda _setting, reference=reference: reference,
            measured_noise_check=checks.get(summary.capture_setting),
            moonless_sky_rate_by_frame=moonless or None,
        )
        refreshed.append(
            summary.model_copy(update={"loss_account": result.account}) if result is not None else summary
        )
    summaries = refreshed
    notes.extend(
        context.reference_notes([summary.capture_setting for summary in summaries], observations, goal)
    )
    for note in context.notes:
        if note.startswith("No efficiency reference"):
            statements.append(insufficient(EFFICIENCY_RULE_NAME, note))
        else:
            notes.append(note)

    # Plain rules, then the rules that need a join or a fit.
    statements.extend(
        judge_results(
            rows,
            capture_settings=target_rows.settings,
            subject_capture_settings=stack_setting_by_subject,
        )
    )
    statements.extend(
        frames_lost_statements(
            summaries,
            reasons_by_setting,
            evidence_by_setting,
            unassigned,
            sum(summary.frames_taken for summary in summaries),
        )
    )
    statements.extend(sky_cost_statements(summaries))
    star_width_evidence = {
        row.subject.subject_id: MeasurementReference(row_id=row.row_id)
        for row in rows
        if row.metric == STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC and row.row_id is not None
    }
    statements.extend(seeing_limit_statements(observations, goal, context.reference_for, star_width_evidence))
    contexts = butler.get_all("ekos_session_context")
    statements.extend(
        _quality_statements_of_settings(
            target,
            goal,
            target_rows,
            observations,
            summaries,
            runs,
            stack_setting_by_subject,
            context,
            config,
            contexts,
        )
    )
    statements.extend(_calibration_statements(target, summaries, observations, astrometrics, rows))
    has_photometry = any(row.metric.startswith("photometry.") for row in rows)
    statements.extend(_natural_goal_statements(goal, rows, has_photometry))
    statements = _checklist_returns(statements, sessions_of_target(butler, target.id), rows)

    starting_point = None
    if not observations:
        starting_point = _starting_point(target, goal, context, config, now)

    forecast_checks: list[ForecastCheck] = (
        forecast_check(runs, observations, configured_stack_weight=configured_weight)
        if len(runs) >= 2
        else []
    )
    ordered = order_statements(statements)
    report = TargetDatasetReport(
        targetId=target.id,
        captureSettings=summaries,
        statements=ordered,
        needsAttention=needs_attention(ordered),
        startingPoint=starting_point,
        notes=notes,
        forecastChecks=forecast_checks,
        scienceOutcomes=_science_outcomes(target, rows),
        context=context_readings(
            rows,
            {summary.name: summary for summary in astrometrics.quality.metrics()},
            stack_setting_by_subject,
        ),
        generatedAt=now,
    )
    return fit_report_to_budget(report)


__all__ = ["build_target_dataset_report", "context_readings", "fit_report_to_budget"]
