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

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import TYPE_CHECKING, Any

from astrometricslib import (
    CaptureSetting,
    NotFoundError,
    QualityMeasurement,
    SubjectLevel,
    Target,
)
from wayfindinglib.analytics.result_quality.aggregates import DataSummary, SetAsideInput, night_summary
from wayfindinglib.analytics.result_quality.efficiency import (
    PINNED_METRIC_VERSIONS,
    SKY_RATE_METRIC,
    FrameObservation,
    ReferenceOutcome,
    baseline_key,
    baseline_reference,
    build_baseline_nights,
    frame_observations_from_measurements,
    physical_reference,
)
from wayfindinglib.analytics.result_quality.judge_results import (
    judge_results,
    needs_attention,
    order_statements,
)
from wayfindinglib.analytics.result_quality.reply_budget import (
    EVIDENCE_STEPS,
    MAXIMUM_REPLY_CHARACTERS,
    limit_statement_evidence,
    reply_characters,
)
from wayfindinglib.analytics.result_quality.science_goal import science_goal_for_target
from wayfindinglib.analytics.result_quality.sky_samples import (
    collect_sky_samples,
    fit_sky_models,
    frame_times,
    moonless_sky_rates,
    target_direction,
)
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.models.quality.efficiency import EfficiencyReference
from wayfindinglib.models.quality.statements import TargetStatement
from wayfindinglib.models.session.night_quality import GuidingNightSummary, NightQuality, NightTargetQuality
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

SET_ASIDE_MARKER = "_excluded"
"""Text in a row's ``action_taken`` that says the stacker moved the frame to
its ``_excluded`` folder (``"frame moved to _excluded/"``). Design estimate,
register row WAY-D75."""

NIGHT_QUALITY_ENTRY = "WAY-74"
"""Register entry behind the night review."""


def set_aside_reasons(rows: Iterable[QualityMeasurement]) -> dict[str, str]:
    """Find the frames that stored rows say were set aside.

    Parameters
    ----------
    rows : `Iterable` [`QualityMeasurement`]
        Frame-level rows of the night.

    Returns
    -------
    reasons : `dict` [`str`, `str`]
        The reason by frame id: the row's own words.
    """
    reasons: dict[str, str] = {}
    for row in rows:
        if (
            row.subject.level == SubjectLevel.FRAME
            and row.action_taken
            and SET_ASIDE_MARKER in row.action_taken
        ):
            reasons.setdefault(row.subject.subject_id, row.action_taken)
    return reasons


def make_reference_provider(
    observations: Sequence[FrameObservation],
    aperture_mm: float | None,
    typical_seeing_fwhm_arcsec: float | None,
) -> Callable[[CaptureSetting], EfficiencyReference | None]:
    """Build the function that gives the efficiency reference for a setting.

    The reference is the user's own baseline for the equipment (the 10th
    percentile of the per-night medians over all measured nights, needing at
    least 3 nights). When there are too few nights, it is the physical
    reference of the telescope, when the telescope's aperture and the pixel
    scale are known. Otherwise there is no reference, and the setting is named
    in the summary's notes. This choice is a design estimate (register row
    WAY-D76).

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The measured frames of every night.
    aperture_mm : `float` or `None`
        The imaging telescope's clear aperture, in millimeters.
    typical_seeing_fwhm_arcsec : `float` or `None`
        The site's typical good-night star width, if the site profile has it.

    Returns
    -------
    reference_for : `Callable`
        Gives the reference of a capture setting, or ``None``.
    """
    by_key: dict[str, list[FrameObservation]] = defaultdict(list)
    for observation in observations:
        by_key[baseline_key(observation.capture_setting)].append(observation)
    cache: dict[str, EfficiencyReference | None] = {}

    def reference_for(setting: CaptureSetting) -> EfficiencyReference | None:
        """Give the reference for a capture setting.

        Parameters
        ----------
        setting : `CaptureSetting`
            The setting.

        Returns
        -------
        reference : `EfficiencyReference` or `None`
            The baseline, else the physical reference, else ``None``.
        """
        key = baseline_key(setting)
        if key not in cache:
            outcome: ReferenceOutcome = baseline_reference(build_baseline_nights(by_key.get(key, [])), key)
            if outcome.reference is None:
                outcome = physical_reference(
                    aperture_mm, setting.pixel_scale_arcsec, typical_seeing_fwhm_arcsec
                )
            cache[key] = outcome.reference
        return cache[key]

    return reference_for


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


def fit_night_quality_to_budget(
    quality: NightQuality, budget: int = MAXIMUM_REPLY_CHARACTERS
) -> NightQuality:
    """Shorten the evidence lists until the review fits the reply budget.

    Parameters
    ----------
    quality : `NightQuality`
        The finished review.
    budget : `int`, optional
        The most characters its compact JSON may have.

    Returns
    -------
    quality : `NightQuality`
        The same review when it fits. Otherwise a copy whose statements keep
        fewer references each (``EVIDENCE_STEPS``, in turn) and count what
        they lost; a note says it was done. No statement is removed.
    """
    if reply_characters(quality) <= budget:
        return quality
    fitted = quality
    for keep in EVIDENCE_STEPS:
        targets = [
            entry.model_copy(update={"statements": limit_statement_evidence(entry.statements, keep)})
            for entry in quality.targets
        ]
        fitted = quality.model_copy(
            update={
                "targets": targets,
                "notes": [
                    *quality.notes,
                    "The evidence lists of the statements were cut to fit the reply budget of "
                    f"{budget:,} characters. Read the rows with QualityRecords.query.",
                ],
            }
        )
        if reply_characters(fitted) <= budget:
            break
    return fitted


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
        warnings over all of them, the guiding summary and notes.

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

    rows_by_target: dict[str, list[QualityMeasurement]] = defaultdict(list)
    for row in night_rows:
        if row.subject.target_id:
            rows_by_target[row.subject.target_id].append(row)
    targets: dict[str, Target] = {target.id: target for target in astrometrics.targets.list()}

    frame_rows: list[QualityMeasurement] = []
    for metric in PINNED_METRIC_VERSIONS:
        frame_rows.extend(astrometrics.quality.query(metric=metric, level="frame", library_only=True))
    night_frame_rows = [row for row in night_rows if row.subject.level == SubjectLevel.FRAME]
    reasons = set_aside_reasons(night_frame_rows)
    epochs: dict[str, float] = {}
    directions: dict[str, tuple[float, float]] = {}
    for target_id in rows_by_target:
        target = targets.get(target_id)
        direction = target_direction(target) if target else None
        if target is None or direction is None:
            continue
        for frame_id, epoch in frame_times(target).items():
            epochs[frame_id] = epoch
            directions[frame_id] = direction
    observations = frame_observations_from_measurements(
        frame_rows,
        capture_settings,
        start_epoch_by_frame=epochs,
        direction_by_frame=directions,
        set_aside_reason_by_frame=reasons,
    )

    location = configured_observer_location(context.config)
    notes: list[str] = []
    models: dict[str, Any] = {}
    if location is not None:
        sky_rows = [row for row in frame_rows if row.metric == SKY_RATE_METRIC]
        samples = collect_sky_samples(targets.values(), sky_rows, capture_settings)
        models, unavailable = fit_sky_models(
            samples, location["latitude"], location["longitude"], location["elevation"]
        )
        notes.extend(f"Sky model not made, so no Moon share for {line}" for line in unavailable)
    else:
        notes.append(
            "The observer's location is not configured, so the Moon's share of the sky loss is not given."
        )

    telescope = context.active_telescope()
    site_profiles = context.butler.get_all("site_profile")
    seeing = next(
        (site.typical_seeing_fwhm_arcsec for site in site_profiles if site.typical_seeing_fwhm_arcsec), None
    )
    reference_for = make_reference_provider(
        observations, telescope.aperture_mm if telescope else None, seeing
    )

    night_observations = [o for o in observations if o.night_id == night_id]
    observed_ids = {o.frame_id for o in night_observations}
    moonless: dict[str, float] = (
        moonless_sky_rates(
            night_observations, models, location["latitude"], location["longitude"], location["elevation"]
        )
        if location is not None and models
        else {}
    )

    entries: list[NightTargetQuality] = []
    all_statements: list[TargetStatement] = []
    for target_id in sorted(rows_by_target):
        target = targets.get(target_id)
        goal = science_goal_for_target(target) if target else science_goal_for_target(Target(id=target_id))
        target_observations = [o for o in night_observations if o.target_id == target_id]
        set_aside = _unobserved_set_aside(
            night_frame_rows, target_id, night_id, reasons, observed_ids, capture_settings
        )
        summary = night_summary(
            night_id,
            target_observations,
            goal,
            reference_for,
            set_aside=set_aside,
            moonless_sky_rate_by_frame=moonless or None,
        )
        target_night_rows = rows_by_target[target_id]
        stack_rows = astrometrics.quality.query(target_id=target_id, level="stack", library_only=True)
        statements = _target_statements(
            target_night_rows, stack_rows, capture_settings, _main_setting_id(target_night_rows)
        )
        all_statements.extend(statements)
        entries.append(_target_quality(target_id, summary, statements, target, goal))

    if not reasons:
        notes.append(
            "No frame is recorded as set aside. The stacker does not store set-aside frames one by one, so "
            "the set-aside time may be higher than shown."
        )
    entries.sort(key=lambda entry: (-entry.needs_attention.warning_count, entry.target_id))
    return fit_night_quality_to_budget(
        NightQuality(
            night_id=night_id,
            targets=entries,
            needs_attention=needs_attention(all_statements),
            guiding=guiding,
            notes=notes,
        )
    )


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
    "fit_night_quality_to_budget",
    "make_reference_provider",
    "set_aside_reasons",
]
