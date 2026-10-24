"""Purpose: Review the quality of one observing night.

Description: `build_night_quality` is the work behind
`control.history.query(kind="night_quality", session_id=...)`
(`QUALITY_METRICS_PLAN.md` Section 6.1, use case UC1). For each target imaged
that night it states where the time went: frames set aside, and the efficiency
lost in the frames kept, against the user's own best nights with the same
equipment (the baseline) or, when there are too few nights, against the
telescope's physical limit. It also judges the night's measurements with the
same rules as the target report, counts the warnings with the same
`needs_attention` function, and summarizes the night's guiding.

The review reads only what the last sync copied: the quality measurements in
the science library, the recorded guiding runs and Ekos sessions, and the
configuration. It asks the mount for nothing, starts no job and copies no log.
Nothing is stored.

The inputs (site, references, sky models, frame observations) come from the
shared builder, ``library_inputs``, the same one the target report and
tonight's ranking use. The reviewed night is left out of its own efficiency
baseline. The focus drift (luminance frames, with the autofocus runs of the
stored Ekos records) and an exploratory polar-alignment estimate (from the
field rotation, grouped by guiding run) are worked out for the whole night.

Three honest limits are written into the result:

* A frame the stacker set aside is counted only when a stored row says it was
  moved to the ``_excluded`` folder. The stacker does not store set-aside
  frames one by one today, so a night may show no set-aside time that it had.
* Stack-level rules (flat noise, clipping of the brightest star) read the
  newest stack of the target, which may be newer than the night.
* The Moon's share of the sky loss comes from the sky model and is labeled
  model-derived. It needs five nights of the user's own frames.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from functools import partial
from typing import TYPE_CHECKING, Any

from astrometricslib import (
    CaptureSetting,
    FilterType,
    MeasurementReference,
    MeasurementStatus,
    NotFoundError,
    QualityMeasurement,
    SubjectLevel,
    Target,
)
from wayfindinglib.analytics.result_quality.aggregates import DataSummary, SetAsideInput, night_summary
from wayfindinglib.analytics.result_quality.allowed_capture_values import filter_names_match
from wayfindinglib.analytics.result_quality.efficiency import (
    FrameObservation,
    frame_observations_from_measurements,
)
from wayfindinglib.analytics.result_quality.evidence_order import order_statements_evidence
from wayfindinglib.analytics.result_quality.focus_drift import (
    FocusFrameSample,
    fit_focus_drift,
    focus_drift_statement,
)
from wayfindinglib.analytics.result_quality.judge_results import (
    judge_results,
    needs_attention,
    order_statements,
)
from wayfindinglib.analytics.result_quality.library_inputs import (
    LibraryInputs,
    times_and_directions,
)
from wayfindinglib.analytics.result_quality.observing_values import (
    FrameObservingInputs,
    PolarAlignmentEstimate,
    RotationSample,
    observing_values,
    polar_alignment_from_rotation,
)
from wayfindinglib.analytics.result_quality.rules import FOCUSER_TEMPERATURE_METRIC, STAR_WIDTH_PIXELS_METRIC
from wayfindinglib.analytics.result_quality.science_goal import (
    goal_of_setting,
    science_goal_for_setting,
    science_goal_for_target,
)
from wayfindinglib.models.quality.efficiency import ScienceGoal
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.ekos_session import EkosSessionContext
from wayfindinglib.models.session.guiding_run import GuidingRunSummary
from wayfindinglib.models.session.night_quality import (
    GuidingNightSummary,
    NightFocusDrift,
    NightPolarAlignment,
    NightQuality,
    NightTargetQuality,
)
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

NIGHT_QUALITY_ENTRY = "WAY-74"
"""Register entry behind the night review."""

ROTATION_METRIC = "stacking.frame_registration_rotation_deg"
"""The exploratory frame metric that the polar-alignment estimate reads."""

FOCUS_FILTER = FilterType.L
"""The filter whose frames the focus drift is fitted to. Each filter has its
own best focus, so frames of different filters must not share one fit."""


def _guiding_summary(context: ControlContext, night_id: str) -> GuidingNightSummary:
    """Summarize the night's guiding from the recorded runs.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded guiding runs and Ekos sessions.
    night_id : `str`
        The night.

    Returns
    -------
    summary : `GuidingNightSummary`
        The summary, or one that says there is no guiding data.
    """
    analysis = night_analysis.guiding_night_analysis(context, night_id)
    if analysis is None:
        return GuidingNightSummary(
            available=False, reason="No guiding run or sample is recorded for this night."
        )
    quality = analysis.input_quality
    performance = analysis.performance
    return GuidingNightSummary(
        available=True,
        flagged=analysis.flagged,
        flag_reasons=list(analysis.flag_reasons),
        rms_per_axis_arcsec=performance.rms_per_axis_arcsec,
        expected_star_widening_fraction=performance.expected_star_widening_fraction,
        lost_fraction=quality.lost_fraction,
        excursion_count=quality.excursion_count,
        runs=list(performance.runs),
    )


def _target_statements(
    night_rows: Sequence[QualityMeasurement],
    stack_rows: Sequence[QualityMeasurement],
    capture_settings: Mapping[int, CaptureSetting],
    main_setting_id: int | None,
) -> list[TargetStatement]:
    """Judge one target's measurements of the night with the plain rules.

    Parameters
    ----------
    night_rows : `Sequence` [`QualityMeasurement`]
        The target's rows of the night.
    stack_rows : `Sequence` [`QualityMeasurement`]
        The target's newest stack rows (they belong to no night).
    capture_settings : `Mapping` [`int`, `CaptureSetting`]
        Every capture setting by id.
    main_setting_id : `int` or `None`
        The setting most used that night; stack rows are assigned to it.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements in priority order.
    """
    rows = [*night_rows, *stack_rows]
    used = {
        row.subject.capture_setting_id for row in night_rows if row.subject.capture_setting_id is not None
    }
    if main_setting_id is not None:
        used.add(main_setting_id)
    settings = {
        setting_id: capture_settings[setting_id] for setting_id in used if setting_id in capture_settings
    }
    stack_to_setting = (
        {row.subject.subject_id: main_setting_id for row in stack_rows}
        if main_setting_id is not None
        else None
    )
    return order_statements(
        judge_results(rows, capture_settings=settings, subject_capture_settings=stack_to_setting)
    )


def _main_setting_id(rows: Iterable[QualityMeasurement]) -> int | None:
    """Find the capture setting with the most frames in some rows.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Frame rows of one target and night.

    Returns
    -------
    setting_id : `int` or `None`
        The most common setting id, or ``None`` when no row has one.
    """
    counts: dict[int, set[str]] = defaultdict(set)
    for row in rows:
        if row.subject.level == SubjectLevel.FRAME and row.subject.capture_setting_id is not None:
            counts[row.subject.capture_setting_id].add(row.subject.subject_id)
    return max(counts, key=lambda setting_id: len(counts[setting_id])) if counts else None


def _target_quality(
    target_id: str,
    summary: DataSummary,
    statements: list[TargetStatement],
    goal_target: Target | None,
    goal: Any,
) -> NightTargetQuality:
    """Assemble one target's entry from its summary and statements.

    Parameters
    ----------
    target_id : `str`
        The target.
    summary : `DataSummary`
        The night's summary for the target.
    statements : `list` [`TargetStatement`]
        Its statements.
    goal_target : `Target` or `None`
        The library target, if it was found.
    goal : `ScienceGoal`
        The goal used.

    Returns
    -------
    quality : `NightTargetQuality`
        The entry.
    """
    notes = list(summary.notes)
    if goal_target is None:
        notes.append("The target is not in the library, so its goal is the default for a faint star.")
    return NightTargetQuality(
        target_id=target_id,
        goal=goal,
        frames_taken=summary.frames_taken,
        frames_set_aside=summary.frames_set_aside,
        captured_seconds=summary.captured_seconds,
        efficiency_effective_seconds=summary.efficiency_effective_seconds,
        capture_settings=summary.setting_summaries,
        loss_account=summary.loss_account,
        statements=statements,
        needs_attention=needs_attention(statements),
        notes=notes,
    )


def _latest_values(rows: Iterable[QualityMeasurement], metric: str) -> dict[str, QualityMeasurement]:
    """Pick the newest measured row of a metric for each frame.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Frame rows.
    metric : `str`
        The metric.

    Returns
    -------
    latest : `dict` [`str`, `QualityMeasurement`]
        The newest row with a value, by frame subject id.
    """
    latest: dict[str, QualityMeasurement] = {}
    for row in rows:
        if (
            row.metric != metric
            or row.subject.level != SubjectLevel.FRAME
            or row.status != MeasurementStatus.MEASURED
            or row.value is None
        ):
            continue
        current = latest.get(row.subject.subject_id)
        if current is None or (row.measured_at, row.row_id or 0) >= (
            current.measured_at,
            current.row_id or 0,
        ):
            latest[row.subject.subject_id] = row
    return latest


def _focus_drift_for_night(
    night_rows: Sequence[QualityMeasurement],
    settings: Mapping[int, CaptureSetting],
    epochs: Mapping[str, float],
    directions: Mapping[str, tuple[float, float]],
    contexts: Sequence[EkosSessionContext],
    inputs: LibraryInputs,
) -> tuple[Any, list[TargetStatement]]:
    """Fit the focus drift of the night's luminance frames.

    The frames are those with a star width in pixels, a focuser temperature
    and a time. The autofocus runs are the successful runs of the stored Ekos
    records of the night (those through the luminance filter, else those that
    name no filter).

    Parameters
    ----------
    night_rows : `Sequence` [`QualityMeasurement`]
        The night's rows.
    settings : `Mapping` [`int`, `CaptureSetting`]
        Every capture setting by id.
    epochs, directions : `Mapping`
        The middle of each frame's exposure and where it points, by frame id.
    contexts : `Sequence` [`EkosSessionContext`]
        The night's stored Ekos records.
    inputs : `LibraryInputs`
        Gives the observing site (for the airmass).

    Returns
    -------
    result, statements : `tuple`
        The fit result and the statement it makes (empty when the data show
        no drift).
    """
    widths = _latest_values(night_rows, STAR_WIDTH_PIXELS_METRIC)
    temperatures = _latest_values(night_rows, FOCUSER_TEMPERATURE_METRIC)
    frame_ids = [
        frame_id
        for frame_id, row in widths.items()
        if frame_id in temperatures
        and frame_id in epochs
        and row.subject.capture_setting_id in settings
        and settings[row.subject.capture_setting_id].filter_name
        and filter_names_match(settings[row.subject.capture_setting_id].filter_name, FOCUS_FILTER)
    ]
    airmass_by_frame: dict[str, float] = {}
    if frame_ids and all(frame_id in directions for frame_id in frame_ids):
        site = inputs.site
        values = observing_values(
            [
                FrameObservingInputs(
                    frame_id=frame_id,
                    start_epoch_seconds=epochs[frame_id],
                    requested_ra_deg=directions[frame_id][0],
                    requested_dec_deg=directions[frame_id][1],
                )
                for frame_id in frame_ids
            ],
            site.latitude_deg,
            site.longitude_deg,
            site.elevation_m,
        )
        airmass_by_frame = {
            value.frame_id: value.geometry.airmass
            for value in values
            if value.geometry is not None and value.geometry.airmass is not None
        }
    samples = [
        FocusFrameSample(
            captured_at=datetime.fromtimestamp(epochs[frame_id], tz=UTC),
            focuser_temperature_degc=temperatures[frame_id].value,
            star_width_px=widths[frame_id].value,
            airmass=airmass_by_frame.get(frame_id),
            evidence=MeasurementReference(row_id=widths[frame_id].row_id)
            if widths[frame_id].row_id is not None
            else None,
        )
        for frame_id in frame_ids
        if widths[frame_id].value and widths[frame_id].value > 0.0
    ]
    runs = [run for record in contexts for run in record.autofocus_runs if run.succeeded]
    through_filter = [
        run for run in runs if run.filter_name and filter_names_match(run.filter_name, FOCUS_FILTER)
    ]
    chosen = through_filter or [run for run in runs if not run.filter_name]
    times = [datetime.fromtimestamp(run.timestamp, tz=UTC) for run in chosen]
    result = fit_focus_drift(samples, times)
    statement = focus_drift_statement(result)
    return result, [statement] if statement is not None else []


def _polar_alignment_for_night(
    night_rows: Sequence[QualityMeasurement],
    epochs: Mapping[str, float],
    directions: Mapping[str, tuple[float, float]],
    runs: Sequence[GuidingRunSummary],
    inputs: LibraryInputs,
    target_by_frame: Mapping[str, str],
) -> tuple[PolarAlignmentEstimate | None, list[str]]:
    """Estimate the polar-alignment error from the field rotation of the night.

    Each frame belongs to the guiding run that was running at its time, and
    each run (of each target) has its own rotation zero point. The estimate is
    exploratory and no rule uses it.

    Parameters
    ----------
    night_rows : `Sequence` [`QualityMeasurement`]
        The night's rows.
    epochs, directions : `Mapping`
        The middle of each frame's exposure and where it points, by frame id.
    runs : `Sequence` [`GuidingRunSummary`]
        The night's stored guiding runs.
    inputs : `LibraryInputs`
        Gives the observing site.
    target_by_frame : `Mapping` [`str`, `str`]
        The target of each frame.

    Returns
    -------
    estimate, notes : `tuple`
        The estimate, or ``None`` when no frame has a rotation row, and the
        notes about frames that could not be used.
    """
    rotations = _latest_values(night_rows, ROTATION_METRIC)
    if not rotations:
        return None, ["No frame of the night has a stored field rotation, so no polar-alignment estimate."]
    ordered = sorted(runs, key=lambda run: run.started_at)
    starts = [run.started_at for run in ordered]
    samples: list[RotationSample] = []
    unplaced = 0
    for frame_id, row in rotations.items():
        if frame_id not in epochs or frame_id not in directions:
            unplaced += 1
            continue
        index = bisect.bisect_right(starts, epochs[frame_id]) - 1
        run = ordered[index] if index >= 0 else None
        if run is None or (run.ended_at is not None and epochs[frame_id] > run.ended_at):
            unplaced += 1
            continue
        samples.append(
            RotationSample(
                guiding_run_id=f"{run.id}|{target_by_frame.get(frame_id, '')}",
                epoch_seconds=epochs[frame_id],
                rotation_deg=row.value,
                ra_deg=directions[frame_id][0],
                dec_deg=directions[frame_id][1],
            )
        )
    notes = (
        [f"{unplaced} frames with a field rotation have no guiding run, time or position and were left out."]
        if unplaced
        else []
    )
    if not samples:
        return None, [*notes, "No frame with a field rotation falls inside a recorded guiding run."]
    site = inputs.site
    return polar_alignment_from_rotation(
        samples, site.latitude_deg, site.longitude_deg, site.elevation_m
    ), notes


def build_night_quality(context: ControlContext, night_id: str) -> NightQuality:
    """Review one observing night.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library handle (``astrometrics``), the
        configuration and the recorded sessions. Nothing is written and no
        device is asked.
    night_id : `str`
        The night, named for the local date on which it began, such as
        ``"2026-09-24"``.

    Returns
    -------
    quality : `NightQuality`
        The targets imaged (those that need attention first), the count of
        warnings over all of them, the guiding summary, the night's focus drift
        and polar-alignment estimate, and notes.

    Raises
    ------
    NotFoundError
        If the night has neither frame measurements nor guiding data.
    """
    astrometrics = context.astrometrics
    capture_settings = astrometrics.quality.capture_settings()
    night_rows = astrometrics.quality.query(observing_night_id=night_id, library_only=True)
    guiding = _guiding_summary(context, night_id)
    if not night_rows and not guiding.available:
        raise NotFoundError(
            f"Night {night_id} has no frame measurements and no guiding data.", details={"night_id": night_id}
        )

    inputs = LibraryInputs(astrometrics, context.butler, context.config)
    rows_by_target: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in night_rows:
        if row.subject.target_id:
            rows_by_target[row.subject.target_id].append(row)
    targets: dict[str, Target] = inputs.targets()

    night_frame_rows = [row for row in night_rows if row.subject.level == SubjectLevel.FRAME]
    reasons: dict[str, str] = {}
    for listed_target_id in sorted(rows_by_target):
        reasons.update(
            inputs.set_aside_reasons(
                listed_target_id,
                [row for row in night_frame_rows if row.subject.target_id == listed_target_id],
            )
        )
    epochs, directions = times_and_directions(targets[t] for t in rows_by_target if t in targets)
    night_observations = frame_observations_from_measurements(
        night_frame_rows,
        capture_settings,
        start_epoch_by_frame=epochs,
        direction_by_frame=directions,
        set_aside_reason_by_frame=reasons,
    )
    # The reviewed night is not part of its own baseline.
    reference_for = inputs.reference_provider(exclude_night_id=night_id)
    moonless = inputs.moonless_sky_rates(night_observations)
    notes: list[str] = []
    for setting in {o.capture_setting for o in night_observations}:
        model, reason = inputs.sky_model_for(setting)
        if model is None:
            notes.append(f"Sky model not made, so no Moon share for {reason}")
    observed_ids = {o.frame_id for o in night_observations}

    entries: list[NightTargetQuality] = []
    all_statements: list[TargetStatement] = []
    for target_id in sorted(rows_by_target):
        target = targets.get(target_id)
        goal_target = target if target else Target(id=target_id)
        target_goal = science_goal_for_target(goal_target)

        goal_for_setting = partial(science_goal_for_setting, goal_target)  # user decision U2
        target_observations = [o for o in night_observations if o.target_id == target_id]
        set_aside = _unobserved_set_aside(
            night_frame_rows, target_id, night_id, reasons, observed_ids, capture_settings
        )
        summary, goal = _summary_by_goal(
            night_id,
            target_observations,
            set_aside,
            goal_for_setting,
            target_goal,
            reference_for,
            moonless or None,
        )
        target_night_rows = rows_by_target[target_id]
        stack_rows = astrometrics.quality.query(target_id=target_id, level="stack", library_only=True)
        statements = _target_statements(
            target_night_rows, stack_rows, capture_settings, _main_setting_id(target_night_rows)
        )
        # The most extreme evidence first, so a shortened reply keeps it.
        statements = order_statements_evidence(statements, [*target_night_rows, *stack_rows])
        all_statements.extend(statements)
        entry = _target_quality(target_id, summary, statements, target, goal)
        reference_notes = inputs.reference_notes(
            {o.capture_setting for o in target_observations},
            target_observations,
            goal_for_setting,
            night_id,
        )
        entries.append(entry.model_copy(update={"notes": [*entry.notes, *reference_notes]}))

    night_contexts = [
        record for record in context.butler.get_all("ekos_session_context") if record.session_id == night_id
    ]
    night_runs = [run for run in context.butler.get_all("guiding_run") if run.session_id == night_id]
    target_by_frame = {
        row.subject.subject_id: row.subject.target_id for row in night_frame_rows if row.subject.target_id
    }
    focus_result, focus_statements = _focus_drift_for_night(
        night_rows, capture_settings, epochs, directions, night_contexts, inputs
    )
    polar, polar_notes = _polar_alignment_for_night(
        night_rows, epochs, directions, night_runs, inputs, target_by_frame
    )
    notes.extend(polar_notes)
    focus_statements = order_statements_evidence(focus_statements, night_rows)
    all_statements.extend(focus_statements)

    if not reasons:
        notes.append(
            "No frame is recorded as set aside. The stacker does not store set-aside frames one by one, so "
            "the set-aside time may be higher than shown."
        )
    notes.extend(inputs.notes)
    entries.sort(key=lambda entry: (-entry.needs_attention.warning_count, entry.target_id))
    return NightQuality(
        night_id=night_id,
        targets=entries,
        needs_attention=needs_attention(all_statements),
        guiding=guiding,
        night_statements=order_statements(focus_statements),
        focus_drift=NightFocusDrift.model_validate(focus_result, from_attributes=True),
        polar_alignment=NightPolarAlignment.model_validate(polar, from_attributes=True)
        if polar is not None
        else None,
        notes=notes,
    )


def _summary_by_goal(
    night_id: str,
    observations: Sequence[FrameObservation],
    set_aside: Sequence[SetAsideInput],
    goal_for_setting: Callable[[CaptureSetting], ScienceGoal],
    default_goal: ScienceGoal,
    reference_for: Any,
    moonless_sky_rate_by_frame: Mapping[str, float] | None,
) -> tuple[DataSummary, ScienceGoal]:
    """Summarize a target's night, each capture setting for its own goal.

    The user decided on 2026-10-24 (issue #95, decision U2) that the goal is
    chosen for each capture setting. The settings are split into groups that
    share a goal, each group is summarized, and the groups are added up. When
    the goals differ, the whole-target loss account is left out (it would have
    to state one goal); each setting's own account stays in its summary.

    Parameters
    ----------
    night_id : `str`
        The night.
    observations : `Sequence` [`FrameObservation`]
        The target's frames.
    set_aside : `Sequence` [`SetAsideInput`]
        The target's set-aside frames that were not measured.
    goal_for_setting : `Callable`
        Gives the goal of a capture setting.
    default_goal : `ScienceGoal`
        The target-level goal, used when there is no frame to read settings
        from.
    reference_for : `Callable`
        Gives the efficiency reference of a capture setting.
    moonless_sky_rate_by_frame : `Mapping` [`str`, `float`] or `None`
        The sky model's no-Moon rates.

    Returns
    -------
    summary, goal : `tuple`
        The summary, and the goal that holds most of the captured time (the
        target's goal when the goals agree).
    """
    settings = {o.capture_setting for o in observations} | {f.capture_setting for f in set_aside}
    goals = {setting: goal_of_setting(goal_for_setting, setting) for setting in settings}
    distinct = set(goals.values())
    if len(distinct) <= 1:
        goal = next(iter(distinct), default_goal)
        summary = night_summary(
            night_id,
            observations,
            goal,
            reference_for,
            set_aside=set_aside,
            moonless_sky_rate_by_frame=moonless_sky_rate_by_frame,
        )
        summary = summary.model_copy(
            update={
                "setting_summaries": [
                    item.model_copy(update={"science_goal": goal}) for item in summary.setting_summaries
                ]
            }
        )
        return summary, goal
    parts: list[tuple[ScienceGoal, DataSummary]] = []
    for group_goal in distinct:
        parts.append((
            group_goal,
            night_summary(
                night_id,
                [o for o in observations if goals[o.capture_setting] == group_goal],
                group_goal,
                reference_for,
                set_aside=[f for f in set_aside if goals[f.capture_setting] == group_goal],
                moonless_sky_rate_by_frame=moonless_sky_rate_by_frame,
            ),
        ))
    parts.sort(key=lambda part: (-part[1].captured_seconds, part[0].value))
    effective = [
        p.efficiency_effective_seconds for _g, p in parts if p.efficiency_effective_seconds is not None
    ]
    summaries = [
        item.model_copy(update={"science_goal": group_goal})
        for group_goal, part in parts
        for item in part.setting_summaries
    ]
    notes = [note for _g, part in parts for note in part.notes]
    notes.append(
        "The target's capture settings have different science goals ("
        + ", ".join(sorted(g.value for g in distinct))
        + "), so there is no loss account for the whole target. Each setting states its own goal and account."
    )
    merged = DataSummary(
        scope="night",
        scope_id=night_id,
        nights=sorted({n for _g, part in parts for n in part.nights}),
        frames_taken=sum(p.frames_taken for _g, p in parts),
        frames_set_aside=sum(p.frames_set_aside for _g, p in parts),
        captured_seconds=sum(p.captured_seconds for _g, p in parts),
        efficiency_effective_seconds=sum(effective) if effective else None,
        setting_summaries=summaries,
        loss_account=None,
        notes=notes,
    )
    return merged, parts[0][0]


def _unobserved_set_aside(
    night_frame_rows: Sequence[QualityMeasurement],
    target_id: str,
    night_id: str,
    reasons: Mapping[str, str],
    observed_ids: set[str],
    capture_settings: Mapping[int, CaptureSetting],
) -> list[SetAsideInput]:
    """List set-aside frames of a target that have no frame measurements.

    A set-aside frame that was measured is counted from its measurements. One
    that was not measured is counted here, from its capture setting.

    Parameters
    ----------
    night_frame_rows : `Sequence` [`QualityMeasurement`]
        The frame rows of the night.
    target_id : `str`
        The target.
    night_id : `str`
        The night.
    reasons : `Mapping` [`str`, `str`]
        The reason by set-aside frame id (`set_aside_reasons`).
    observed_ids : `set` [`str`]
        Frames that have measurements.
    capture_settings : `Mapping` [`int`, `CaptureSetting`]
        Every capture setting by id.

    Returns
    -------
    set_aside : `list` [`SetAsideInput`]
        One input for each such frame.
    """
    found: dict[str, SetAsideInput] = {}
    for row in night_frame_rows:
        frame_id = row.subject.subject_id
        setting_id = row.subject.capture_setting_id
        if (
            row.subject.target_id != target_id
            or frame_id not in reasons
            or frame_id in observed_ids
            or frame_id in found
            or setting_id not in capture_settings
        ):
            continue
        setting = capture_settings[setting_id]
        found[frame_id] = SetAsideInput(
            frame_id=frame_id,
            exposure_seconds=setting.exposure_seconds or 0.0,
            reason=reasons[frame_id],
            capture_setting=setting,
            night_id=night_id,
        )
    return list(found.values())


__all__ = [
    "NIGHT_QUALITY_ENTRY",
    "build_night_quality",
]
