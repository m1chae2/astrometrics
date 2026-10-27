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
import re
import statistics
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from astrometricslib import (
    UNKNOWN_CAPTURE_SETTING_CAMERA,
    AstrometricsError,
    CaptureSetting,
    MeasurementReference,
    QualityMeasurement,
    Target,
    fold_exposure_lengths,
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
from wayfindinglib.analytics.result_quality.evidence_order import order_statements_evidence
from wayfindinglib.analytics.result_quality.fixed_pattern_location import FixedPatternAccount
from wayfindinglib.analytics.result_quality.forecast import (
    StackRunRecord,
    camera_gain_for_setting,
    fixed_pattern_account_for_run,
    fixed_pattern_noise_for_runs,
    library_runs,
    noise_model_error_for_setting,
    odd_even_difference_noise_inputs,
    recommended_additional_frames_status_from_checks,
    single_stack_fixed_pattern_noise,
    stack_frames_from_observations,
    stack_groups_of_run,
)
from wayfindinglib.analytics.result_quality.guiding_exposure import (
    GuidingExposureOutcome,
    assess_guiding_exposure,
    imaging_spans,
)
from wayfindinglib.analytics.result_quality.guiding_join import FrameWindow
from wayfindinglib.analytics.result_quality.judge_results import (
    judge_results,
    needs_attention,
    order_statements,
)
from wayfindinglib.analytics.result_quality.library_inputs import (
    LibraryInputs,
    build_target_observations,
    dominant_setting,
    target_direction,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    APERTURE_NOISE_ERROR_FACTOR,
    MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH,
    TARGET_NOISE_RATIO,
    NoiseModelError,
    StackFrame,
    StackGroup,
    WeightingMode,
    frames_to_cut_noise,
    frames_to_cut_noise_in_groups,
    predict_group_frame_noise_adu,
    predict_weighting_mode,
)
from wayfindinglib.analytics.result_quality.report_rules import (
    FIXED_PATTERN_CALIBRATION_TEXT,
    FIXED_PATTERN_CAUSE_TEXT,
    FIXED_PATTERN_NOT_REMOVED_TEXT,
    PLAIN_FIXED_PATTERN_CALIBRATION_TEXT,
    PLAIN_FIXED_PATTERN_NOT_REMOVED_TEXT,
    AltitudeSample,
    LightFrameGroup,
    altitude_cost_statement,
    calibration_gap_statement,
    duration_text,
    find_altitude_cost,
    find_calibration_gaps,
    find_sharpness_tradeoff,
    find_sharpness_tradeoff_in_groups,
    fixed_pattern_limited_statement,
    fixed_pattern_shape_statements,
    frames_lost_statements,
    guiding_limits_statements,
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
from wayfindinglib.analytics.result_quality.science_goal import (
    group_by_goal,
    science_goal_for_setting,
    science_goal_for_target,
)
from wayfindinglib.analytics.result_quality.starting_point import (
    StartingPointInputs,
    build_starting_point,
    sky_dominated_exposure_seconds,
)
from wayfindinglib.models.planning.target_report import (
    ContextReading,
    ForecastBasis,
    ForecastCheck,
    ForecastOutcome,
    RecommendedAdditionalFramesStatus,
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
    FRAME_WEIGHT_METRIC,
    QUARANTINE_METRICS,
    TargetRows,
    assign_stacks_to_settings,
    frame_altitudes,
    frame_sensor_temperatures,
    median_or_none,
    read_calibration_rows,
    read_forecast_runs,
    read_noise_history,
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
EFFICIENCY_RULE_NAME = "efficiency_reference_missing"
"""The rule that says a capture setting has no efficiency reference (design
estimate, register row WAY-D81)."""
CALIBRATION_RULE_NAME = "calibration_gap"
FRAMES_LOST_RULE_NAME = "frames_lost"

GUIDE_SAMPLE_READ_LIMIT = 200_000
"""Most guide samples read for one night. A long night holds about 10,000, so
this never cuts one (the same limit the performance envelope uses)."""

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


def _measured_noise_checks(
    rows: Sequence[QualityMeasurement],
    stack_settings: Mapping[str, int],
    settings: Mapping[int, CaptureSetting],
    runs: Sequence[StackRunRecord] = (),
    noise_model_errors: Mapping[Any, NoiseModelError] | None = None,
) -> dict[CaptureSetting, MeasuredNoiseCheck]:
    """Compare each stack's measured noise with the noise it should have.

    A stack with a usable odd–even difference is compared with the random noise
    measured from its odd and even half stacks, read from its newest library
    run (`odd_even_difference_noise_inputs`, the record the fixed-pattern noise
    reads). Any other stack is compared with the noise its frames predict.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The target's newest rows.
    stack_settings : `Mapping` [`str`, `int`]
        The setting id of each stack.
    settings : `Mapping` [`int`, `CaptureSetting`]
        The target's settings.
    runs : `Sequence` [`StackRunRecord`], optional
        The target's library stack runs, with odd–even differences attached.
    noise_model_errors : `Mapping`, optional
        The noise model's own error by capture setting group
        (`read_noise_history`), for the stacks on the model basis. The
        stand-in value when omitted.

    Returns
    -------
    checks : `dict` [`CaptureSetting`, `MeasuredNoiseCheck`]
        For each setting with a stack, the variance ratio and its error. On
        the model basis the error is ``2 * 1.17 / sqrt(n)`` times the ratio
        for n apertures (unpaired). A stack missing the noise values its
        basis needs is left out.
    """
    newest_run_by_stack: dict[str, StackRunRecord] = {}
    for run in sorted(runs, key=lambda item: (item.measured_at, item.run_id)):
        newest_run_by_stack[run.stack_subject_id] = run
    checks: dict[CaptureSetting, MeasuredNoiseCheck] = {}
    for stack_id, setting_id in stack_settings.items():
        newest_run = newest_run_by_stack.get(stack_id)
        inputs = odd_even_difference_noise_inputs(newest_run) if newest_run is not None else None
        account = _fixed_pattern_account_of_stack(
            newest_run, noise_model_error_for_setting(noise_model_errors, settings[setting_id])
        )
        if inputs is not None:
            checks[settings[setting_id]] = MeasuredNoiseCheck.from_odd_even_difference(*inputs).model_copy(
                update={"fixed_pattern_account": account}
            )
            continue
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
        model_error = noise_model_error_for_setting(noise_model_errors, settings[setting_id])
        checks[settings[setting_id]] = MeasuredNoiseCheck(
            variance_ratio=ratio,
            variance_ratio_error=error,
            expected_noise_relative_error=model_error.relative_error,
            model_error_basis=model_error.basis_text,
            fixed_pattern_account=account,
        )
    return checks


def _fixed_pattern_account_of_stack(
    newest_run: StackRunRecord | None, model_error: NoiseModelError
) -> FixedPatternAccount | None:
    """Say where a stack's fixed pattern is and what the masters explain.

    Parameters
    ----------
    newest_run : `StackRunRecord` or `None`
        The stack's newest library run.
    model_error : `NoiseModelError`
        The noise model's error, for a stack on the model basis.

    Returns
    -------
    account : `FixedPatternAccount` or `None`
        The shape tests and the calibration breakdown of the stack's own
        fixed-pattern noise (`fixed_pattern_account_for_run`), for the loss
        account.
    """
    if newest_run is None:
        return None
    return fixed_pattern_account_for_run(
        newest_run, single_stack_fixed_pattern_noise(newest_run, model_error)
    )


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
        if observation is None or observation.capture_setting.camera == UNKNOWN_CAPTURE_SETTING_CAMERA:
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


MORE_FRAMES_NOT_CHECKED_REASON = (
    "The forecast of how much one more hour helps has not been checked on this target yet. Take more "
    "frames and stack them; then the report compares the forecast with what the stack did, and once "
    "one check agrees, it will say how many more frames to take."
)
"""Why "take N more" is hidden. The user decided on 2026-10-24 (issue #95,
decision U1) that the number of frames is shown for a target only after one of
THAT target's forecast checks came out as forecast."""


PROVISIONAL_COUNT_SENTENCE = (
    "It rests on a check made from frames already taken; the next real stack will confirm it or withdraw it."
)
"""What a "take N more" statement says when only an earlier-nights check
agreed. The user decided on 2026-10-26 (issue #134, decision D11) that such a
count is shown, marked provisional, and ranked below a count that a real later
stack confirmed."""


def _recommended_additional_frames_status_from_checks(
    forecast_checks: Sequence[ForecastCheck],
) -> RecommendedAdditionalFramesStatus:
    """Say whether "take N more" may be shown, and on what footing.

    Parameters
    ----------
    forecast_checks : `Sequence` [`ForecastCheck`]
        The target's own forecast checks, oldest first
        (`forecast.forecast_check`).

    Returns
    -------
    frames_status : `RecommendedAdditionalFramesStatus`
        ``confirmed`` once a check of two library stacks agreed;
        ``provisional`` when only a check made from frames already taken
        agreed (and no real check has disagreed since); else ``not_checked``.
        See `forecast.recommended_additional_frames_status_from_checks`.
    """
    return recommended_additional_frames_status_from_checks(forecast_checks)


def _recommended_additional_frames_status_open(forecast_checks: Sequence[ForecastCheck]) -> bool:
    """Say whether "take N more" may be shown for the target.

    Parameters
    ----------
    forecast_checks : `Sequence` [`ForecastCheck`]
        The target's own forecast checks.

    Returns
    -------
    open : `bool`
        `True` when the status is confirmed or provisional.
    """
    return (
        _recommended_additional_frames_status_from_checks(forecast_checks)
        != RecommendedAdditionalFramesStatus.NOT_CHECKED
    )


def _with_provisional_count(text: str) -> str:
    """Put "(provisional)" after the count in a recommended-frames text.

    Parameters
    ----------
    text : `str`
        A message that starts "Recommended additional frames: 44".

    Returns
    -------
    text : `str`
        The same text with " (provisional)" after the count; unchanged when it
        does not start that way.
    """
    return re.sub(r"^(Recommended additional frames: \d+)", r"\1 (provisional)", text)


def _mark_provisional(statements: Sequence[TargetStatement]) -> list[TargetStatement]:
    """Say on each recommended-frames count that it is provisional.

    The confidence stays ``design_estimate``, which is what the rule already
    carries, and no new confidence label exists. The word "(provisional)" goes
    after the count, and the sentence that says why goes on the message, the
    plain message and the reason. The statement's values name the basis
    (``count_basis`` is ``"provisional"``).

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements made for one setting.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The same statements; each ``more_frames`` recommendation carries the
        sentence.
    """
    marked = []
    for statement in statements:
        if statement.rule == "more_frames" and statement.kind == StatementKind.RECOMMENDATION:
            statement = statement.model_copy(
                update={
                    "message": f"{_with_provisional_count(statement.message)} {PROVISIONAL_COUNT_SENTENCE}",
                    "plain_message": (
                        f"{_with_provisional_count(statement.plain_message)} "
                        f"{PROVISIONAL_COUNT_SENTENCE}".strip()
                        if statement.plain_message
                        else ""
                    ),
                    "reason": f"{statement.reason} {PROVISIONAL_COUNT_SENTENCE}".strip(),
                    "values": {
                        **statement.values,
                        "count_basis": RecommendedAdditionalFramesStatus.PROVISIONAL.value,
                    },
                }
            )
        marked.append(statement)
    return marked


def _more_frames_not_checked_statement(
    setting: CaptureSetting, forecast_checks: Sequence[ForecastCheck], has_frame_weights: bool = True
) -> TargetStatement:
    """Make the statement that stands in for a hidden "take N more".

    Parameters
    ----------
    setting : `CaptureSetting`
        The setting the hidden statement was about.
    forecast_checks : `Sequence` [`ForecastCheck`]
        The target's checks, to say what has been tried.
    has_frame_weights : `bool`, optional
        Whether the target has any frame-level ``stacking.frame_weight`` rows.
        Without them (history-fill data) no check can run, and the reason says
        how to get them.

    Returns
    -------
    statement : `TargetStatement`
        An ``insufficient_data`` statement for the rule ``more_frames``.
    """
    if forecast_checks:
        tried = ", ".join(sorted({check.outcome.value for check in forecast_checks}))
        history = f" {len(forecast_checks)} check(s) so far came out as: {tried}."
        from_frames_taken = sum(
            1 for check in forecast_checks if check.basis == ForecastBasis.EARLIER_NIGHTS_SUBSET
        )
        if from_frames_taken:
            history += f" {from_frames_taken} of them were made from frames already taken (earlier nights)."
        not_checkable = [check for check in forecast_checks if check.outcome == ForecastOutcome.NOT_CHECKABLE]
        if not_checkable and not _recommended_additional_frames_status_open(forecast_checks):
            history += f" The latest one that could not be made: {not_checkable[-1].reason}"
    else:
        history = " No stack has followed a forecast yet, so there is no check."
    if not has_frame_weights:
        history += (
            " This target has no frame-level stacking.frame_weight rows (its stored history was filled in "
            "afterward), so no check can run yet. Restack this target to record frame weights and noise "
            "apertures."
        )
    return insufficient("more_frames", MORE_FRAMES_NOT_CHECKED_REASON + history, setting)


def _single_group_of(frames: Sequence[StackFrame], electrons_per_adu: float) -> StackGroup:
    """Make the one exposure group of a stack that has only one.

    A stack of one exposure length stores no ``s_g`` (the frame noise the
    combine measures), so it is predicted from the frames' sky and read noise.
    It is used only to plan a stack that a new exposure length would turn into
    a group stack.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The stack's frames.
    electrons_per_adu : `float`
        The camera gain.

    Returns
    -------
    group : `StackGroup`
        The group, with a predicted frame noise.
    """
    return StackGroup(
        frames=tuple(frames),
        frame_noise_adu=predict_group_frame_noise_adu(frames, electrons_per_adu),
        electrons_per_adu=electrons_per_adu,
        noise_predicted=True,
    )


def _camera_gain(config: Any, setting: CaptureSetting) -> float | None:
    """Look up a setting's camera gain, or give ``None`` when unknown.

    Parameters
    ----------
    config : `AppConfiguration`
        The configuration that holds the camera profiles.
    setting : `CaptureSetting`
        The setting.

    Returns
    -------
    gain : `float` or `None`
        Electrons per ADU.
    """
    try:
        return camera_gain_for_setting(config, setting)
    except AstrometricsError:
        logger.warning("The camera profile of '%s' could not be read.", setting.camera, exc_info=True)
        return None


def _stack_of_setting(
    observations: Sequence[FrameObservation],
    fallback_frames: Sequence[StackFrame],
    runs: Sequence[StackRunRecord],
    observations_by_id: Mapping[str, FrameObservation],
    electrons_per_adu: float | None,
) -> tuple[list[StackRunRecord], StackRunRecord | None, Any, list[StackFrame]]:
    """Find the newest stack of a setting's frames and rebuild its groups.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The setting's frames.
    fallback_frames : `Sequence` [`StackFrame`]
        The setting's usable frames, used as the stack's frames when no stack
        holds any of them.
    runs : `Sequence` [`StackRunRecord`]
        The target's library stack runs.
    observations_by_id : `Mapping` [`str`, `FrameObservation`]
        Every frame of the target by id.
    electrons_per_adu : `float` or `None`
        The camera gain, kept on the rebuilt groups.

    Returns
    -------
    setting_runs, newest_run, run_groups, stack_frames : `tuple`
        The runs that hold any of the setting's frames (oldest first), the
        newest of them (or ``None``), its `RunGroups` (or ``None`` when a
        member frame has no usable measurements) and the newest stack's
        frames (the fallback frames when there is none).
    """
    frame_ids = {observation.frame_id for observation in observations}
    setting_runs = [run for run in runs if frame_ids & set(run.member_frame_ids)]
    newest_run = setting_runs[-1] if setting_runs else None
    if newest_run is None:
        return setting_runs, None, None, list(fallback_frames)
    run_frames, run_problems = stack_frames_from_observations(newest_run.member_frame_ids, observations_by_id)
    if run_problems:
        return setting_runs, newest_run, None, list(fallback_frames)
    return (
        setting_runs,
        newest_run,
        stack_groups_of_run(newest_run, run_frames, electrons_per_adu=electrons_per_adu),
        run_frames,
    )


def _more_frames_for_setting(
    setting: CaptureSetting,
    observations: Sequence[FrameObservation],
    rows: Sequence[QualityMeasurement],
    configured_weight: str | None,
    overheads: Any,
    *,
    runs: Sequence[StackRunRecord] = (),
    observations_by_id: Mapping[str, FrameObservation] | None = None,
    electrons_per_adu: float | None = None,
    noise_model_errors: Mapping[Any, NoiseModelError] | None = None,
    fixed_pattern_history: Mapping[Any, Mapping[str, float]] | None = None,
    target_id: str | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
) -> list[TargetStatement]:
    """Make the "take N more" statement of one capture setting.

    The new frames copy the setting's past frames. When the past frames were
    limited by read noise, the new frames use the exposure at which the sky
    dominates instead (plan section 6.4), and the reason says so.

    The number comes from the model the forecast check used. When the newest
    stack that holds the setting's frames was combined from exposure groups,
    the groups are rebuilt from its stored rows and the take-more search places
    the new frames the way the stacker would (`frames_to_cut_noise_in_groups`:
    at least 5 frames of a new exposure length, a say when the heaviest group
    would change). When the new frames would turn a single-group stack into a
    group stack, the same search is used with the earlier group's frame noise
    predicted. The stack's noise fixed-pattern noise is applied. When the
    fixed-pattern noise makes up so much of the noise that more frames cannot
    reach the target, the statement says so in place of a count.

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
    runs : `Sequence` [`StackRunRecord`], optional
        The target's library stack runs, for the stack's groups and
        fixed_pattern.
    observations_by_id : `Mapping` [`str`, `FrameObservation`], optional
        Every frame of the target by id (the stack may hold frames of other
        settings).
    electrons_per_adu : `float`, optional
        The camera gain at the setting, for the frame noise of a new group.
    noise_model_errors : `Mapping`, optional
        The noise model's own error by capture setting group
        (`read_noise_history`).
    fixed_pattern_history : `Mapping`, optional
        Other targets' fixed-pattern shares by setup (`read_noise_history`).
        A target with no fixed-pattern noise of its own starts from their
        median, and the statement says so (decision D23).
    target_id : `str`, optional
        The target, left out of its own borrowed median.
    target_noise_ratio : `float`, optional
        The noise after the new frames over the noise now. The default is one
        doubling of the effective time; a depth goal passes the ratio that
        reaches its depth.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation, or one ``insufficient_data`` statement saying
        what is missing, or the fixed-pattern noise warning.
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
    setting_runs, newest_run, run_groups, stack_frames = _stack_of_setting(
        observations,
        frames,
        runs,
        observations_by_id or {observation.frame_id: observation for observation in observations},
        electrons_per_adu,
    )
    fixed_pattern = (
        fixed_pattern_noise_for_runs(
            setting_runs,
            noise_model_error_for_setting(noise_model_errors, setting),
            fixed_pattern_history,
            target_id,
        )
        if setting_runs
        else None
    )
    stack_is_groups = run_groups is not None and run_groups.is_group_stack
    if (
        mode == WeightingMode.WFWHM
        and any(frame.star_width is None for frame in frames)
        and not stack_is_groups
    ):
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
    # A single-group stack becomes a group stack when the new frames have an
    # exposure length that is not the stack's: the stacker then makes a new
    # group of them, and the flat model no longer describes the stack.
    stack_lengths = [frame.exposure_seconds for frame in stack_frames]
    becomes_group_stack = not stack_is_groups and (
        len(
            set(
                fold_exposure_lengths([
                    *stack_lengths,
                    *([new_exposure] * MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH),
                ])
            )
        )
        > 1
    )
    if stack_is_groups:
        if run_groups is not None and run_groups.reason is not None:
            return [insufficient("more_frames", run_groups.reason, setting)]
        existing_groups = list(run_groups.groups) if run_groups is not None else []
    elif becomes_group_stack:
        if electrons_per_adu is None:
            return [
                insufficient(
                    "more_frames",
                    f"The new {new_exposure:g} s frames would make a new exposure group, and the "
                    "camera's gain (electrons per ADU) is needed to predict its weight. The camera "
                    "profile has no gain for this setting.",
                    setting,
                )
            ]
        existing_groups = [_single_group_of(stack_frames, electrons_per_adu)]
    else:
        existing_groups = []
    if existing_groups:
        result = frames_to_cut_noise_in_groups(
            existing_groups,
            frames,
            new_exposure_seconds=new_exposure,
            electrons_per_adu=electrons_per_adu,
            fixed_pattern=fixed_pattern,
            target_noise_ratio=target_noise_ratio,
        )
    else:
        result = frames_to_cut_noise(
            frames,
            frames,
            mode,
            new_exposure_seconds=new_exposure,
            fixed_pattern=fixed_pattern,
            target_noise_ratio=target_noise_ratio,
        )
    used_ids = {frame.frame_id for frame in frames}
    evidence = _references([
        row
        for row in rows
        if row.subject.subject_id in used_ids and row.metric in (SKY_RATE_METRIC, READ_NOISE_METRIC)
    ])
    if (result.practical_limit_applied and not result.doubling_worthwhile) or (
        result.fixed_pattern_limited and not result.practical_limit_applied
    ):
        fixed_pattern_evidence = (
            [MeasurementReference(row_id=newest_run.noise_row_id)]
            if newest_run is not None and newest_run.noise_row_id is not None
            else []
        )
        return [fixed_pattern_limited_statement(result, setting, fixed_pattern_evidence)]
    statement = more_frames_statement(result, setting, overheads, evidence, exposure_note=note)
    frame_count = result.doubling_frames if result.practical_limit_applied else result.frames_needed
    if statement is None or frame_count is None or result.new_exposure_seconds is None:
        return [
            insufficient("more_frames", result.reason or "The number of frames could not be found.", setting)
        ]
    return [_with_full_wall_clock(statement, frame_count, result.new_exposure_seconds, overheads)]


def _point_fixed_pattern_at_calibration(statements: Sequence[TargetStatement]) -> list[TargetStatement]:
    """Tell a fixed-pattern statement the report has calibration advice.

    The fixed-pattern noise statements end with "More frames will not remove
    it." When the same report carries a ``calibration_gap`` statement for the
    same capture setting, that sentence is replaced by a pointer to it, because
    a missing flat or dark is a likely cause of fixed-pattern noise.

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        All the report's statements.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The same statements; those that carry the fixed-pattern noise sentence
        and have calibration advice for their setting say so.
    """
    calibration_settings = {
        statement.capture_setting for statement in statements if statement.rule == CALIBRATION_RULE_NAME
    }
    pointed = []
    for statement in statements:
        if (
            statement.capture_setting in calibration_settings
            and FIXED_PATTERN_NOT_REMOVED_TEXT in statement.message
        ):
            statement = statement.model_copy(
                update={
                    "message": statement.message.replace(
                        FIXED_PATTERN_NOT_REMOVED_TEXT, FIXED_PATTERN_CALIBRATION_TEXT
                    ),
                    "plain_message": statement.plain_message.replace(
                        PLAIN_FIXED_PATTERN_NOT_REMOVED_TEXT, PLAIN_FIXED_PATTERN_CALIBRATION_TEXT
                    ),
                }
            )
        pointed.append(statement)
    return pointed


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
    goal_for_setting: Callable[[CaptureSetting], ScienceGoal],
    target_rows: TargetRows,
    observations: Sequence[FrameObservation],
    summaries: Sequence[Any],
    runs: Sequence[Any],
    stack_settings: Mapping[str, int],
    context: LibraryInputs,
    config: Any,
    contexts_for_overheads: Sequence[Any],
    forecast_checks: Sequence[ForecastCheck] = (),
    has_frame_weights: bool = True,
    noise_model_errors: Mapping[Any, NoiseModelError] | None = None,
    fixed_pattern_history: Mapping[Any, Mapping[str, float]] | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
) -> list[TargetStatement]:
    """Make the statements that are about the target's own stack and frames.

    Covers the stop signal or "take N more", the sharpness trade-off and the
    cost of low altitude.

    Parameters
    ----------
    target : `Target`
        The target.
    goal_for_setting : `Callable`
        Gives the science goal of a capture setting, for the efficiency of
        its frames.
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
    forecast_checks : `Sequence` [`ForecastCheck`], optional
        The target's own forecast checks. The recommended additional frames are
        shown only when one came out as forecast (user decision U1,
        2026-10-24). When only a check made from frames already taken agreed,
        the count is marked provisional (decision D11, 2026-10-26).
    has_frame_weights : `bool`, optional
        Whether the target has any ``stacking.frame_weight`` rows; when it
        has none, the "take N more" reason says to restack.
    noise_model_errors : `Mapping`, optional
        The noise model's own error by capture setting group
        (`read_noise_history`).
    fixed_pattern_history : `Mapping`, optional
        Other targets' fixed-pattern shares by setup (`read_noise_history`).
    target_noise_ratio : `float`, optional
        The noise ratio the "take N more" search aims for (see
        `_more_frames_for_setting`).

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
    frames_status = _recommended_additional_frames_status_from_checks(forecast_checks)
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
    observations_by_id = {observation.frame_id: observation for observation in observations}
    accounts: dict[CaptureSetting, FixedPatternAccount] = {}
    for summary in summaries:
        setting = summary.capture_setting
        setting_observations = by_setting.get(setting, [])
        if not stopped:
            overheads = estimate_capture_overheads(
                contexts_for_overheads, setting.exposure_seconds, setting.filter_name
            )
            if frames_status != RecommendedAdditionalFramesStatus.NOT_CHECKED:
                more_frames = _more_frames_for_setting(
                    setting,
                    setting_observations,
                    target_rows.rows,
                    configured_weight,
                    overheads,
                    runs=runs,
                    observations_by_id=observations_by_id,
                    electrons_per_adu=_camera_gain(config, setting),
                    noise_model_errors=noise_model_errors,
                    fixed_pattern_history=fixed_pattern_history,
                    target_id=target.id,
                    target_noise_ratio=target_noise_ratio,
                )
                statements.extend(
                    _mark_provisional(more_frames)
                    if frames_status == RecommendedAdditionalFramesStatus.PROVISIONAL
                    else more_frames
                )
            else:
                statements.append(
                    _more_frames_not_checked_statement(setting, forecast_checks, has_frame_weights)
                )
        setting_ids = {observation.frame_id for observation in setting_observations}
        setting_runs = [run for run in runs if setting_ids & set(run.member_frame_ids)]
        if setting_runs:
            setting_fixed_pattern = fixed_pattern_noise_for_runs(
                setting_runs, noise_model_error_for_setting(noise_model_errors, setting)
            )
            account = fixed_pattern_account_for_run(setting_runs[-1], setting_fixed_pattern)
            if account is not None:
                accounts[setting] = account
                statements.extend(
                    fixed_pattern_shape_statements(
                        account,
                        setting,
                        [MeasurementReference(row_id=setting_runs[-1].noise_row_id)]
                        if setting_runs[-1].noise_row_id is not None
                        else [],
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
            width_values = [float(w) for w in widths if w is not None]
            tradeoff = find_sharpness_tradeoff(frames, width_values, mode)
            _, _, run_groups, _ = _stack_of_setting(
                setting_observations, frames, runs, observations_by_id, None
            )
            if run_groups is not None and run_groups.is_group_stack and run_groups.reason is None:
                # The stack was combined from exposure groups: the other
                # groups dilute the change, so it is worked out on the stack.
                setting_ids = {frame.frame_id for frame in frames}
                position = next(
                    (
                        index
                        for index, group in enumerate(run_groups.groups)
                        if setting_ids & {frame.frame_id for frame in group.frames}
                    ),
                    None,
                )
                tradeoff = None
                if position is not None:
                    group_widths = [
                        _width_row_value(target_rows.rows, frame.frame_id)
                        for frame in run_groups.groups[position].frames
                    ]
                    if all(width is not None for width in group_widths):
                        tradeoff = find_sharpness_tradeoff_in_groups(
                            run_groups.groups, position, [float(w) for w in group_widths if w is not None]
                        )
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
                [o for o in setting_observations if o.set_aside_reason is None],
                goal_for_setting(setting),
                reference,
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
    return _add_fixed_pattern_accounts(statements, accounts)


def _add_fixed_pattern_accounts(
    statements: Sequence[TargetStatement], accounts: Mapping[CaptureSetting, FixedPatternAccount]
) -> list[TargetStatement]:
    """Put where the fixed pattern is, and why, into its statements.

    The fixed-pattern noise statements ("take N more" with a diminishing
    return, and "more frames will not help") carry `FIXED_PATTERN_CAUSE_TEXT`.
    For a setting whose stack has a fixed-pattern account, its sentences follow
    that text in the message and are added to the reason, so the statement
    names the shape that was found and what the master flat and dark explain,
    in place of only saying the cause is probably the flats or calibration
    (decisions D21 and D22).

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements made so far.
    accounts : `Mapping`
        The fixed-pattern account of each setting that has one.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The same statements; those with the cause text and an account have the
        account's sentences added.
    """
    updated = []
    for statement in statements:
        account = accounts.get(statement.capture_setting) if statement.capture_setting is not None else None
        if account is not None and account.text and FIXED_PATTERN_CAUSE_TEXT in statement.message:
            statement = statement.model_copy(
                update={
                    "message": statement.message.replace(
                        FIXED_PATTERN_CAUSE_TEXT, f"{FIXED_PATTERN_CAUSE_TEXT} {account.text}"
                    ),
                    "reason": f"{statement.reason} {account.text}".strip(),
                }
            )
        updated.append(statement)
    return updated


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
    goals: set[ScienceGoal | None], rows: Sequence[QualityMeasurement], target_has_photometry: bool
) -> list[TargetStatement]:
    """Say what is missing for the natural finish lines.

    Parameters
    ----------
    goals : `set` [`ScienceGoal`]
        The science goals of the target's capture settings.
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
    if ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION in goals:
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


def _guiding_exposure_statements(
    summaries: Sequence[Any],
    observations: Sequence[FrameObservation],
    butler: Any,
    star_width_evidence: Mapping[str, MeasurementReference],
) -> list[TargetStatement]:
    """Make the "guiding limits exposure" statement of each capture setting.

    The guide samples are read from the stored control records (Foundation),
    only for the nights the setting was used.

    Parameters
    ----------
    summaries : `Sequence` [`CaptureSettingSummary`]
        The setting summaries.
    observations : `Sequence` [`FrameObservation`]
        The frames.
    butler : `DiskButler`
        The storage layer, which holds the guide samples.
    star_width_evidence : `Mapping` [`str`, `MeasurementReference`]
        The star-width row of each frame.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements of `guiding_limits_statements`.
    """
    from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES

    records = getattr(butler, "control_records", None)
    sources = [source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES]
    statements: list[TargetStatement] = []
    sample_cache: dict[str, list[dict[str, Any]]] = {}
    for summary in summaries:
        setting = summary.capture_setting
        kept = [
            o
            for o in observations
            if o.capture_setting == setting
            and o.set_aside_reason is None
            and o.start_epoch_seconds is not None
            and o.night_id is not None
        ]
        windows_by_night: dict[str, list[tuple[float, float]]] = defaultdict(list)
        for observation in kept:
            start = float(observation.start_epoch_seconds)
            windows_by_night[observation.night_id].append((
                start,
                start + float(setting.exposure_seconds),
            ))
        if records is None or not setting.exposure_seconds:
            reason = (
                "The setting has no exposure time."
                if not setting.exposure_seconds
                else "No stored guide samples are available to this report."
            )
            statements.extend(guiding_limits_statements(setting, GuidingExposureOutcome(reason=reason)))
            continue
        spans = {night: imaging_spans(windows) for night, windows in windows_by_night.items()}
        for night in spans:
            if night not in sample_cache:
                sample_cache[night] = records.get_guiding_samples(
                    session_id=night, limit=GUIDE_SAMPLE_READ_LIMIT, sources=sources
                )
        outcome = assess_guiding_exposure(
            float(setting.exposure_seconds),
            [o.star_width_effective_arcsec for o in kept if o.star_width_effective_arcsec is not None],
            spans,
            {night: sample_cache[night] for night in spans},
            [
                FrameWindow(
                    frame_id=o.frame_id,
                    start_epoch_seconds=o.start_epoch_seconds,
                    exposure_seconds=float(setting.exposure_seconds),
                )
                for o in kept
            ],
        )
        evidence = [star_width_evidence[o.frame_id] for o in kept if o.frame_id in star_width_evidence]
        statements.extend(guiding_limits_statements(setting, outcome, evidence))
    return statements


def _state_goals(statements: Sequence[TargetStatement], summaries: Sequence[Any]) -> list[TargetStatement]:
    """Put the science goal into each statement that is about one setting.

    The user decided on 2026-10-24 (issue #95, decision U2) that each
    statement states its goal. The goal goes in ``values["science_goal"]``. A
    statement about the whole target gets it only when every setting has the
    same goal.

    Parameters
    ----------
    statements : `Sequence` [`TargetStatement`]
        The statements.
    summaries : `Sequence` [`CaptureSettingSummary`]
        The setting summaries, which carry the goals.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        Copies with the goal added where it is known.
    """
    goal_by_setting = {summary.capture_setting: summary.science_goal for summary in summaries}
    distinct = {goal for goal in goal_by_setting.values() if goal is not None}
    result = []
    for statement in statements:
        goal = (
            goal_by_setting.get(statement.capture_setting)
            if statement.capture_setting is not None
            else (next(iter(distinct)) if len(distinct) == 1 else None)
        )
        if goal is None or "science_goal" in statement.values:
            result.append(statement)
        else:
            result.append(
                statement.model_copy(update={"values": {**statement.values, "science_goal": goal.value}})
            )
    return result


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
    clip, saturation = profile.clip_ceiling_adu, profile.pixel_saturation_level_adu
    note += (
        f" The camera's pixels top out at {clip.value:.0f} ADU ({_provenance_text(clip)}); a raw frame "
        f"counts a pixel as saturated at or above {saturation.value:.0f} ADU "
        f"({_provenance_text(saturation)})."
    )
    if not profile.saturation_threshold_can_be_reached:
        note += (
            " The profile's own saturation threshold is above the ceiling, so no pixel could reach it; "
            "the ceiling is used instead."
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
    target_noise_ratio: float = TARGET_NOISE_RATIO,
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
    target_noise_ratio : `float`, optional
        The noise after the recommended additional frames over the noise now.
        The default is one doubling of the effective time. The depth goal
        passes the ratio that reaches its depth.

    Returns
    -------
    report : `TargetDatasetReport`
        The summaries, the statements in priority order, how many warnings
        need attention, the starting point when there are no frames, the
        forecast checks and the science outcomes. Nothing is stored.
    """
    from wayfindinglib.analytics.result_quality.forecast import PINNED_STACK_METRICS, forecast_check

    now = at or datetime.now(UTC)
    target_rows = read_target_rows(astrometrics, target.id)
    rows = target_rows.rows
    # The goal is chosen for each capture setting (user decision U2).
    lights = [frame for frame in target.frames if str(frame.role).upper().endswith("LIGHT")]

    def goal_for_setting(setting: CaptureSetting) -> ScienceGoal:
        """Give the science goal of one capture setting of this target.

        Parameters
        ----------
        setting : `CaptureSetting`
            The setting.

        Returns
        -------
        goal : `ScienceGoal`
            Slitless spectral resolution for a grating setting; otherwise by
            the target's type and results.
        """
        return science_goal_for_setting(target, setting, lights)

    target_goal = science_goal_for_target(target)
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
    # The calibration of each run is not a measurement row; it is a record of
    # master file identities. A run with none counts as confounded.
    all_runs = read_forecast_runs(astrometrics, target.id, stack_rows)
    # The earlier-nights subset runs serve only the forecast check. The stop
    # signal, the fixed-pattern noise and the stack map read the library runs
    # alone.
    runs = library_runs(all_runs)
    newest_members: dict[str, list[str]] = {}
    for run in runs:
        newest_members[run.stack_subject_id] = run.member_frame_ids
    stack_settings, assignment_notes = assign_stacks_to_settings(rows, target_rows.settings, newest_members)
    target_rows.notes.extend(assignment_notes)
    stack_setting_by_subject = dict(stack_settings)

    # Set-aside frames (the rows that say so) and the frames' observations.
    reasons_by_frame = context.set_aside_reasons(target.id, rows)
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

    # Summaries, one group of settings for each science goal.
    moonless = context.moonless_sky_rates(observations)
    weight_seconds = _weight_effective_seconds(rows, stack_setting_by_subject, target_rows.settings)
    noise_history = read_noise_history(astrometrics)
    noise_model_errors = noise_history.model_errors
    checks = _measured_noise_checks(
        rows, stack_setting_by_subject, target_rows.settings, runs, noise_model_errors
    )
    summaries = []
    for group_goal, group in group_by_goal(
        observations, lambda observation: observation.capture_setting, goal_for_setting
    ).items():
        group_summaries = capture_setting_summaries(
            group,
            group_goal,
            context.reference_for,
            weight_effective_seconds_by_setting=weight_seconds,
            measured_noise_check_by_setting=checks,
        )
        for summary in group_summaries:
            reference = context.reference_for(summary.capture_setting)
            update: dict[str, Any] = {"science_goal": group_goal}
            if reference is not None:
                result = loss_account(
                    [o for o in group if o.capture_setting == summary.capture_setting],
                    [],
                    group_goal,
                    lambda _setting, reference=reference: reference,
                    measured_noise_check=checks.get(summary.capture_setting),
                    moonless_sky_rate_by_frame=moonless or None,
                )
                if result is not None:
                    update["loss_account"] = result.account
            summaries.append(summary.model_copy(update=update))
    notes.extend(
        context.reference_notes(
            [summary.capture_setting for summary in summaries], observations, goal_for_setting
        )
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
    statements.extend(
        seeing_limit_statements(observations, goal_for_setting, context.reference_for, star_width_evidence)
    )
    statements.extend(_guiding_exposure_statements(summaries, observations, butler, star_width_evidence))
    contexts = butler.get_all("ekos_session_context")
    # The checks come first: "take N more" waits for one that agreed (U1).
    forecast_checks: list[ForecastCheck] = (
        forecast_check(
            all_runs,
            observations,
            configured_stack_weight=configured_weight,
            electrons_per_adu=(
                _camera_gain(config, dominant_setting(observations))
                if dominant_setting(observations)
                else None
            ),
        )
        if len(runs) >= 2 or len(all_runs) > len(runs)
        else []
    )
    statements.extend(
        _quality_statements_of_settings(
            target,
            goal_for_setting,
            target_rows,
            observations,
            summaries,
            runs,
            stack_setting_by_subject,
            context,
            config,
            contexts,
            forecast_checks,
            has_frame_weights=any(row.metric == FRAME_WEIGHT_METRIC for row in stack_rows),
            noise_model_errors=noise_model_errors,
            fixed_pattern_history=noise_history.fixed_pattern_shares,
            target_noise_ratio=target_noise_ratio,
        )
    )
    statements.extend(_calibration_statements(target, summaries, observations, astrometrics, rows))
    statements = _point_fixed_pattern_at_calibration(statements)
    has_photometry = any(row.metric.startswith("photometry.") for row in rows)
    goals_in_use = {summary.science_goal for summary in summaries} or {target_goal}
    statements.extend(_natural_goal_statements(goals_in_use, rows, has_photometry))
    statements = _checklist_returns(statements, sessions_of_target(butler, target.id), rows)
    statements = _state_goals(statements, summaries)

    starting_point = None
    if not observations:
        starting_point = _starting_point(target, target_goal, context, config, now)

    metric_summaries = {summary.name: summary for summary in astrometrics.quality.metrics()}
    # Each statement lists its most extreme evidence first, so a shortened
    # reply (the MCP tools shorten it) keeps the references that matter most.
    statements = order_statements_evidence(
        statements, [*rows, *stack_rows], {name: summary.unit for name, summary in metric_summaries.items()}
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
        context=context_readings(rows, metric_summaries, stack_setting_by_subject),
        generatedAt=now,
    )
    return report


__all__ = ["build_target_dataset_report", "context_readings"]
