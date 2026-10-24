"""Purpose: Judge stored measurements with the rule table.

Description: `judge_results` takes the quality measurements of a target (read
from the quality store by the caller), groups them by capture setting, and
makes one statement for each plain rule of `RESULT_QUALITY_RULES` that fires.
A plain rule compares one reading with a limit. Rules made by other modules
(``computed_by`` is set) are not judged here.

Three promises hold for every statement:

* **Evidence is exact.** A statement lists the stored rows it used, by row
  id, and the limits it compared them with.
* **Missing data is said, never skipped.** When a metric has no usable
  measurement for a capture setting, the rule makes an ``insufficient_data``
  statement that names the metric, says why it is missing and how to get it.
  A row measured under another definition version of the metric is not used
  (decision S13), and the statement says to measure again.
* **Nothing is stored.** Statements are recomputed on each read.

`order_statements` sorts statements for display, and `needs_attention` counts
the warnings. Every view uses these two functions, so the app and the AI
tools show the same order and the same count.
"""

import math
import statistics
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from astrometricslib import (
    CaptureSetting,
    MeasurementReference,
    MeasurementStatus,
    QualityLimit,
    QualityMeasurement,
    SubjectLevel,
)
from wayfindinglib.analytics.result_quality.evidence_order import order_evidence_most_extreme_first
from wayfindinglib.analytics.result_quality.rules import (
    ELONGATION_METRIC,
    EMPTY_APERTURE_NOISE_METRIC,
    FLAT_FRAME_COUNT_METRIC,
    FLAT_NOISE_FRACTION_METRIC,
    FLAT_NOISE_IN_APERTURE_METRIC,
    PEAK_FRACTION_METRIC,
    RAW_FRAME_CLIPPING_METRIC,
    READ_NOISE_METRIC,
    RESULT_QUALITY_RULES,
    SKY_NOISE_METRIC,
)
from wayfindinglib.models.quality.rule import QualityRule
from wayfindinglib.models.quality.statements import (
    ExpectedEffect,
    NeedsAttention,
    ProposedAction,
    Severity,
    StatementKind,
    TargetStatement,
)

# The value a check outcome has when a stored self-check failed. The outcome
# is a string enum in astrometricslib that is not part of its public API, so
# it is compared by value.
_FAILED_CHECK_OUTCOME = "failed"

# The order statements are shown in: warnings first (they are about data
# already taken), then recommendations, constraints and missing data.
_KIND_ORDER = {
    StatementKind.WARNING: 0,
    StatementKind.RECOMMENDATION: 1,
    StatementKind.CONSTRAINT: 2,
    StatementKind.INSUFFICIENT_DATA: 3,
}

# Within warnings, the most severe comes first.
_SEVERITY_ORDER = {Severity.WARNING: 0, Severity.ADVICE: 1, Severity.INFO: 2}


@dataclass(frozen=True)
class _Reading:
    """One metric's value for one capture setting.

    Attributes
    ----------
    metric : `str`
        The metric name.
    value : `float`
        For a frame metric, the median over the frames. For a stack or run
        metric, the value of the newest row.
    rows : `list` [`QualityMeasurement`]
        The rows the value came from.
    assumed_reasons : `list` [`str`]
        The reasons of the rows whose status is ``assumed``.
    """

    metric: str
    value: float
    rows: list[QualityMeasurement]
    assumed_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Problem:
    """Why a metric could not be read for a capture setting.

    Attributes
    ----------
    metric : `str`
        The metric name.
    text : `str`
        What is wrong and how to get it, in plain words.
    rows : `list` [`QualityMeasurement`]
        Rows that exist but could not be used, cited as evidence.
    """

    metric: str
    text: str
    rows: list[QualityMeasurement]


@dataclass(frozen=True)
class _Case:
    """Everything a plain rule needs to make its statement.

    Attributes
    ----------
    rule : `QualityRule`
        The rule being judged.
    limits : `list` [`QualityLimit`]
        The limits to use (the rule's own, or the caller's override).
    capture_setting : `CaptureSetting` or `None`
        The setting being judged.
    readings : `dict` [`str`, `_Reading`]
        The reading of each required metric.
    """

    rule: QualityRule
    limits: list[QualityLimit]
    capture_setting: CaptureSetting | None
    readings: dict[str, _Reading]


def _how_to_get(metric: str) -> str:
    """Say how to get a missing measurement of a metric.

    Parameters
    ----------
    metric : `str`
        The metric name.

    Returns
    -------
    hint : `str`
        One plain sentence.
    """
    if metric.startswith("frame."):
        return "Measure the target's frames again (QualityRecords.measure_frames)."
    if metric.startswith("stacking."):
        return "Stack the target again, so the stacker records it."
    return "Run the pipeline that records this metric."


def _reference(row: QualityMeasurement) -> MeasurementReference:
    """Build the reference that cites a stored row.

    Parameters
    ----------
    row : `QualityMeasurement`
        A row read back from the quality store.

    Returns
    -------
    reference : `MeasurementReference`
        A reference by row id.

    Raises
    ------
    ValueError
        If the row has no row id, which means it was not read back from the
        store, so it cannot be cited.
    """
    if row.row_id is None:
        raise ValueError(
            f"Measurement of {row.metric} for {row.subject.subject_id} has no row_id; "
            "read it back from the quality store so a statement can cite it."
        )
    return MeasurementReference(row_id=row.row_id)


def _read_metric(
    metric: str, definition_version: int, rows: Sequence[QualityMeasurement]
) -> _Reading | _Problem:
    """Read one metric from the rows of a capture setting.

    Only rows of the pinned definition version that hold a value are used. Of
    the rows of one subject, only the newest is used, because the store keeps
    every re-measurement. Rows of a subset stack are ignored.

    Parameters
    ----------
    metric : `str`
        The metric name.
    definition_version : `int`
        The definition version the rule was written against.
    rows : `Sequence` [`QualityMeasurement`]
        The rows of one capture setting.

    Returns
    -------
    result : `_Reading` or `_Problem`
        The reading, or the reason there is none.
    """
    of_metric = [row for row in rows if row.metric == metric and row.subject.variant is None]
    if not of_metric:
        return _Problem(
            metric, f"{metric} has no measurement for this capture setting. {_how_to_get(metric)}", []
        )
    current = [row for row in of_metric if row.definition_version == definition_version]
    if not current:
        versions = sorted({row.definition_version for row in of_metric})
        return _Problem(
            metric,
            f"{metric} was measured under definition version {versions}, but this rule needs version "
            f"{definition_version}. Remeasure it. {_how_to_get(metric)}",
            of_metric,
        )
    newest_by_subject: dict[str, QualityMeasurement] = {}
    for row in current:
        held = newest_by_subject.get(row.subject.subject_id)
        if held is None or row.measured_at > held.measured_at:
            newest_by_subject[row.subject.subject_id] = row
    usable = [row for row in newest_by_subject.values() if row.status != MeasurementStatus.NOT_MEASURED]
    if not usable:
        newest = max(newest_by_subject.values(), key=lambda row: row.measured_at)
        return _Problem(
            metric,
            f"{metric} was not measured: {newest.reason or 'no reason was stored'}. {_how_to_get(metric)}",
            list(newest_by_subject.values()),
        )
    if usable[0].subject.level == SubjectLevel.FRAME:
        chosen = sorted(usable, key=lambda row: row.measured_at)
        value = statistics.median(row.value for row in chosen if row.value is not None)
    else:
        chosen = [max(usable, key=lambda row: row.measured_at)]
        value = chosen[0].value if chosen[0].value is not None else math.nan
    assumed = [row.reason for row in chosen if row.status == MeasurementStatus.ASSUMED]
    return _Reading(metric, float(value), chosen, sorted(set(assumed)))


def _exposure_text(capture_setting: CaptureSetting | None) -> str:
    """Name the exposure of a setting for a message.

    Parameters
    ----------
    capture_setting : `CaptureSetting` or `None`
        The setting.

    Returns
    -------
    text : `str`
        For example ``"60 s"``, or ``"These"`` when the exposure is unknown.
    """
    if capture_setting is None or capture_setting.exposure_seconds is None:
        return "These"
    return f"{capture_setting.exposure_seconds:g} s"


def _limit_value(limits: Sequence[QualityLimit], index: int, rule_name: str) -> float:
    """Read the numeric value of one of a rule's limits.

    Parameters
    ----------
    limits : `Sequence` [`QualityLimit`]
        The limits in use for the rule.
    index : `int`
        Which limit; the order is the order in the rule's row of the table.
    rule_name : `str`
        The rule's name, for the error message.

    Returns
    -------
    value : `float`
        The limit's value.

    Raises
    ------
    ValueError
        If the limit is missing or has no value.
    """
    if index >= len(limits) or limits[index].value is None:
        raise ValueError(f"Rule {rule_name!r} needs a limit with a value at position {index}.")
    return float(limits[index].value)


def _evidence(readings: Iterable[_Reading]) -> list[MeasurementReference]:
    """List the rows of some readings as references, each once.

    Parameters
    ----------
    readings : `Iterable` [`_Reading`]
        The readings a statement used.

    Returns
    -------
    references : `list` [`MeasurementReference`]
        References by row id, in the order of the readings.
    """
    seen: set[int] = set()
    references: list[MeasurementReference] = []
    for reading in readings:
        for row in reading.rows:
            reference = _reference(row)
            if reference.row_id not in seen:
                seen.add(reference.row_id)
                references.append(reference)
    return references


def _assumed_text(readings: Iterable[_Reading]) -> str:
    """Say which readings rest on an assumed value.

    Parameters
    ----------
    readings : `Iterable` [`_Reading`]
        The readings a statement used.

    Returns
    -------
    text : `str`
        A sentence for each assumed reading, or an empty string.
    """
    parts = [
        f"{reading.metric} is assumed, not measured ({'; '.join(reading.assumed_reasons)})."
        for reading in readings
        if reading.assumed_reasons
    ]
    return " ".join(parts)


def _statement(
    case: _Case,
    message: str,
    reason: str,
    *,
    proposed_action: ProposedAction | None = None,
    expected_effect: ExpectedEffect | None = None,
    extra_limits: Sequence[QualityLimit] = (),
) -> TargetStatement:
    """Build the statement of a plain rule that fired.

    Parameters
    ----------
    case : `_Case`
        The rule, its limits, the setting and the readings.
    message : `str`
        One plain sentence.
    reason : `str`
        Why.
    proposed_action : `ProposedAction`, optional
        The values proposed, for a recommendation.
    expected_effect : `ExpectedEffect`, optional
        What the action is expected to do.
    extra_limits : `Sequence` [`QualityLimit`], optional
        Limits cited beside the rule's own, such as the limit a stored
        self-check used.

    Returns
    -------
    statement : `TargetStatement`
        The statement, with the rule's kind, severity, action class and
        confidence.
    """
    rule = case.rule
    assumed = _assumed_text(case.readings.values())
    limits = [*case.limits, *extra_limits]
    # The rows a reading used, so the evidence can be put most extreme first.
    rows_by_id = {
        row.row_id: row
        for reading in case.readings.values()
        for row in reading.rows
        if row.row_id is not None
    }
    evidence = order_evidence_most_extreme_first(_evidence(case.readings.values()), limits, rows_by_id)
    return TargetStatement(
        rule=rule.name,
        kind=rule.statement_kind,
        severity=rule.severity,
        action_class=rule.action_class,
        capture_setting=case.capture_setting,
        message=message,
        reason=f"{reason} {assumed}".strip(),
        proposed_action=proposed_action,
        expected_effect=expected_effect,
        evidence=evidence,
        limits=limits,
        confidence=rule.confidence,
    )


def _insufficient_statement(
    rule: QualityRule,
    capture_setting: CaptureSetting | None,
    problems: Sequence[_Problem],
) -> TargetStatement:
    """Build the statement that says a rule could not be checked.

    Parameters
    ----------
    rule : `QualityRule`
        The rule that could not be checked.
    capture_setting : `CaptureSetting` or `None`
        The setting.
    problems : `Sequence` [`_Problem`]
        What was missing.

    Returns
    -------
    statement : `TargetStatement`
        An ``insufficient_data`` statement with a reason that names every
        missing metric.
    """
    cited: list[MeasurementReference] = []
    seen: set[int] = set()
    for problem in problems:
        for row in problem.rows:
            if row.row_id is not None and row.row_id not in seen:
                seen.add(row.row_id)
                cited.append(MeasurementReference(row_id=row.row_id))
    return TargetStatement(
        rule=rule.name,
        kind=StatementKind.INSUFFICIENT_DATA,
        capture_setting=capture_setting,
        message=f"Cannot check the rule {rule.name.replace('_', ' ')}: data is missing.",
        reason=" ".join(problem.text for problem in problems),
        evidence=cited,
        confidence=rule.confidence,
    )


def _insufficient_from_values(case: _Case, reason: str) -> TargetStatement:
    """Build an ``insufficient_data`` statement for values that cannot be used.

    Parameters
    ----------
    case : `_Case`
        The rule and its readings.
    reason : `str`
        Why the values cannot be used, such as a read noise of zero.

    Returns
    -------
    statement : `TargetStatement`
        The statement, citing the rows that were read.
    """
    return TargetStatement(
        rule=case.rule.name,
        kind=StatementKind.INSUFFICIENT_DATA,
        capture_setting=case.capture_setting,
        message=f"Cannot check the rule {case.rule.name.replace('_', ' ')}: the values cannot be used.",
        reason=reason,
        evidence=_evidence(case.readings.values()),
        confidence=case.rule.confidence,
    )


def _judge_overexposed_setting(case: _Case) -> list[TargetStatement]:
    """Warn when the brightest star clips at this exposure.

    Limits, in order of the rule's row: the fraction at which a star clips,
    and the fraction a proposed exposure aims for.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One warning, or an empty list.
    """
    reading = case.readings[PEAK_FRACTION_METRIC]
    clip_fraction = _limit_value(case.limits, 0, case.rule.name)
    target_fraction = _limit_value(case.limits, 1, case.rule.name)
    if reading.value < clip_fraction:
        return []
    exposure = case.capture_setting.exposure_seconds if case.capture_setting else None
    if exposure is not None and reading.value > 0:
        proposed_seconds = exposure * target_fraction / reading.value
        advice = f"Use {proposed_seconds:.3g} s or less."
        action = ProposedAction(exposure_seconds=proposed_seconds)
        effect = ExpectedEffect(
            description=f"The brightest star would reach {target_fraction:.0%} of the room below the ceiling."
        )
    else:
        advice = "Shorten the exposure."
        action = ProposedAction(
            note=(
                f"Shorten the exposure until the brightest star stays below "
                f"{target_fraction:.0%} of the room."
            )
        )
        effect = None
    message = case.rule.wording.format(
        exposure_text=_exposure_text(case.capture_setting), peak_fraction=reading.value, advice_text=advice
    )
    reason = (
        f"The peak is {reading.value:.2f} of the room between the sky and the ceiling; "
        f"a star clips at {clip_fraction:g}."
    )
    return [_statement(case, message, reason, proposed_action=action, expected_effect=effect)]


def _judge_underexposed_setting(case: _Case) -> list[TargetStatement]:
    """Recommend a longer exposure when read noise matters too much.

    The sky's photon noise grows as the square root of the exposure, so the
    exposure that reaches the least ratio is the present one times the
    square of the missing factor. The proposal does not check the longer
    exposure against the clipping limit; the rule ``overexposed_setting``
    does that for the exposures already taken.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation, or an empty list.
    """
    sky_noise = case.readings[SKY_NOISE_METRIC].value
    read_noise = case.readings[READ_NOISE_METRIC].value
    if read_noise <= 0.0:
        return [
            _insufficient_from_values(
                case, "The read noise is zero or negative, so the ratio is not defined."
            )
        ]
    ratio = sky_noise / read_noise
    least_ratio = _limit_value(case.limits, 0, case.rule.name)
    if ratio >= least_ratio:
        return []
    exposure = case.capture_setting.exposure_seconds if case.capture_setting else None
    if exposure is not None:
        needed_seconds = exposure * (least_ratio / max(ratio, 1e-9)) ** 2
        advice = f"Use {needed_seconds:.3g} s or longer, if the brightest star stays below the ceiling."
        action = ProposedAction(exposure_seconds=needed_seconds)
    else:
        advice = "Use a longer exposure, if the brightest star stays below the ceiling."
        action = ProposedAction(note="Lengthen the exposure until the sky noise is 3 times the read noise.")
    effect = ExpectedEffect(
        description=f"The sky noise would be {least_ratio:g} times the read noise, so the camera adds little."
    )
    message = case.rule.wording.format(
        exposure_text=_exposure_text(case.capture_setting), ratio=ratio, advice_text=advice
    )
    reason = (
        f"Sky noise {sky_noise:.2f} e- against read noise {read_noise:.2f} e- in one pixel; "
        f"the least ratio is {least_ratio:g}."
    )
    return [_statement(case, message, reason, proposed_action=action, expected_effect=effect)]


def _judge_offset_too_low(case: _Case) -> list[TargetStatement]:
    """Warn when the stacker's self-check found frames clipped at zero.

    The limit is the one the stored self-check used, so this rule does not
    repeat astrometricslib's number.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One warning, or an empty list.
    """
    reading = case.readings[RAW_FRAME_CLIPPING_METRIC]
    row = reading.rows[0]
    if str(row.check_outcome) != _FAILED_CHECK_OUTCOME:
        return []
    message = case.rule.wording.format(zero_percent=reading.value * 100.0)
    reason = (
        f"The stacker's own check found up to {reading.value:.0%} of raw pixels at zero. "
        "Allowed offsets come from the camera profile's measured settings."
    )
    action = ProposedAction(note="Raise the camera offset until the dark pixels no longer read zero.")
    extra = [row.check_limit] if row.check_limit is not None else []
    return [_statement(case, message, reason, proposed_action=action, extra_limits=extra)]


def _judge_trailed_stars(case: _Case) -> list[TargetStatement]:
    """Warn when stars are stretched more than the limit.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One warning, or an empty list.
    """
    reading = case.readings[ELONGATION_METRIC]
    limit = _limit_value(case.limits, 0, case.rule.name)
    if reading.value <= limit:
        return []
    message = case.rule.wording.format(
        elongation=reading.value, exposure_text=_exposure_text(case.capture_setting)
    )
    reason = f"The median axis ratio is {reading.value:.2f}; the limit is {limit:g}."
    return [_statement(case, message, reason)]


def _flat_count(case: _Case) -> int:
    """Read the number of flat frames in the master flat.

    Parameters
    ----------
    case : `_Case`
        The case holding the reading of the flat frame count.

    Returns
    -------
    count : `int`
        The count, at least 1.
    """
    return max(1, round(case.readings[FLAT_FRAME_COUNT_METRIC].value))


def _judge_flat_noise_limits_stack(case: _Case) -> list[TargetStatement]:
    """Warn when the master flat adds too much to the stack's noise.

    Limits, in order of the rule's row: the share of the stack's noise
    variance the flat may add, and the share to aim for. The flat's noise
    falls as one over the square root of the number of flats, so the count
    that reaches the aim is the present count times the ratio of the
    present variance share to the aimed one.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One warning, or an empty list.
    """
    flat_noise = case.readings[FLAT_NOISE_IN_APERTURE_METRIC].value
    stack_noise = case.readings[EMPTY_APERTURE_NOISE_METRIC].value
    if stack_noise <= 0.0:
        return [
            _insufficient_from_values(
                case, "The stack's noise is zero or negative, so the flat's share is not defined."
            )
        ]
    ratio = flat_noise / stack_noise
    variance_share = ratio**2
    limit_share = _limit_value(case.limits, 0, case.rule.name)
    if variance_share < limit_share:
        return []
    target_share = _limit_value(case.limits, 1, case.rule.name)
    present_count = _flat_count(case)
    new_count = math.ceil(present_count * variance_share / target_share)
    extra_flats = max(1, new_count - present_count)
    new_flat_share = variance_share * present_count / (present_count + extra_flats)
    new_noise_ratio = math.sqrt(max(0.0, 1.0 - variance_share + new_flat_share))
    advice = f"Take {extra_flats} more flats."
    message = case.rule.wording.format(
        noise_percent=ratio * 100.0, variance_percent=variance_share * 100.0, advice_text=advice
    )
    reason = (
        f"The flat's noise is {ratio:.0%} of the stack's noise in the same empty apertures, from "
        f"{present_count} flats. Flat noise falls as one over the square root of the number of flats."
    )
    action = ProposedAction(calibration_frame_type="flat", frame_count=extra_flats)
    effect = ExpectedEffect(
        noise_change_fraction=new_noise_ratio - 1.0,
        description=f"The stack's noise would fall by about {1.0 - new_noise_ratio:.0%}.",
    )
    return [_statement(case, message, reason, proposed_action=action, expected_effect=effect)]


def _judge_master_flat_noisy(case: _Case) -> list[TargetStatement]:
    """Recommend more flats when the master flat is noisier than the limit.

    Parameters
    ----------
    case : `_Case`
        The rule, limits, setting and readings.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation, or an empty list.
    """
    fraction = case.readings[FLAT_NOISE_FRACTION_METRIC].value
    limit = _limit_value(case.limits, 0, case.rule.name)
    if fraction <= limit:
        return []
    present_count = _flat_count(case)
    new_count = math.ceil(present_count * (fraction / limit) ** 2)
    extra_flats = max(1, new_count - present_count)
    advice = f"Take {extra_flats} more flats."
    message = case.rule.wording.format(
        noise_percent=fraction * 100.0, limit_percent=limit * 100.0, advice_text=advice
    )
    reason = (
        f"The master flat's relative noise is {fraction:.2%} from {present_count} flats, "
        f"above the {limit:.1%} limit. Flat noise falls as one over the square root of the number of flats."
    )
    action = ProposedAction(calibration_frame_type="flat", frame_count=extra_flats)
    effect = ExpectedEffect(description=f"The master flat's noise would reach the {limit:.1%} limit.")
    return [_statement(case, message, reason, proposed_action=action, expected_effect=effect)]


_PLAIN_RULE_JUDGES: dict[str, Callable[[_Case], list[TargetStatement]]] = {
    "overexposed_setting": _judge_overexposed_setting,
    "underexposed_setting": _judge_underexposed_setting,
    "offset_too_low": _judge_offset_too_low,
    "trailed_stars": _judge_trailed_stars,
    "flat_noise_limits_stack": _judge_flat_noise_limits_stack,
    "master_flat_noisy": _judge_master_flat_noisy,
}

PLAIN_RULE_NAMES: frozenset[str] = frozenset(_PLAIN_RULE_JUDGES)
"""The names of the rules that `judge_results` evaluates itself.

A rule of the table with no ``computed_by`` must be in this set, and the
conformance test checks it.
"""


def _group_rows(
    measurements: Iterable[QualityMeasurement], subject_capture_settings: Mapping[str, int] | None
) -> dict[int | None, list[QualityMeasurement]]:
    """Group measurements by the capture setting they belong to.

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        The rows.
    subject_capture_settings : `Mapping` [`str`, `int`] or `None`
        Capture setting ids for subjects whose rows carry none, such as
        stacks.

    Returns
    -------
    grouped : `dict` [`int` or `None`, `list` [`QualityMeasurement`]]
        The rows of each setting id. ``None`` holds rows with no setting.
    """
    grouped: dict[int | None, list[QualityMeasurement]] = defaultdict(list)
    for row in measurements:
        setting_id = row.subject.capture_setting_id
        if setting_id is None and subject_capture_settings is not None:
            setting_id = subject_capture_settings.get(row.subject.subject_id)
        grouped[setting_id].append(row)
    return grouped


def _setting_sort_key(capture_setting: CaptureSetting | None) -> tuple:
    """Make a sort key for a capture setting.

    Parameters
    ----------
    capture_setting : `CaptureSetting` or `None`
        The setting.

    Returns
    -------
    key : `tuple`
        A key that sorts settings by camera, filter, exposure, gain, offset
        and binning, with ``None`` first.
    """
    if capture_setting is None:
        return (0, "", "", 0.0, 0, 0, 0)
    return (
        1,
        capture_setting.camera,
        capture_setting.filter_name or "",
        capture_setting.exposure_seconds or 0.0,
        capture_setting.gain_setting or 0,
        capture_setting.offset or 0,
        capture_setting.binning or 0,
    )


def _statement_sort_key(statement: TargetStatement) -> tuple:
    """Build the sort key of one statement.

    Parameters
    ----------
    statement : `TargetStatement`
        The statement.

    Returns
    -------
    key : `tuple`
        Kind, then severity, then capture setting, then rule name.
    """
    severity_rank = _SEVERITY_ORDER[statement.severity] if statement.severity is not None else 3
    return (
        _KIND_ORDER[statement.kind],
        severity_rank,
        _setting_sort_key(statement.capture_setting),
        statement.rule,
    )


def order_statements(statements: Iterable[TargetStatement]) -> list[TargetStatement]:
    """Sort statements for display.

    Parameters
    ----------
    statements : `Iterable` [`TargetStatement`]
        The statements.

    Returns
    -------
    ordered : `list` [`TargetStatement`]
        Warnings first (the most severe first), then recommendations,
        constraints and missing data. Ties go by capture setting and rule
        name, so the order never changes between reads.
    """
    return sorted(statements, key=_statement_sort_key)


def needs_attention(statements: Iterable[TargetStatement]) -> NeedsAttention:
    """Count the warnings and find the worst severity.

    This is the one function every view uses to decide whether a target or a
    night needs attention, so the app and the AI tools agree.

    Parameters
    ----------
    statements : `Iterable` [`TargetStatement`]
        The statements of a target or a night.

    Returns
    -------
    attention : `NeedsAttention`
        The number of warnings and the most severe of them (``None`` when
        there are none).
    """
    severities = [
        statement.severity
        for statement in statements
        if statement.kind == StatementKind.WARNING and statement.severity is not None
    ]
    if not severities:
        return NeedsAttention(warning_count=0, worst_severity=None)
    worst = min(severities, key=lambda severity: _SEVERITY_ORDER[severity])
    return NeedsAttention(warning_count=len(severities), worst_severity=worst)


def judge_results(
    measurements: Iterable[QualityMeasurement],
    *,
    capture_settings: Mapping[int, CaptureSetting],
    rules: Sequence[QualityRule] = RESULT_QUALITY_RULES,
    limits_override: Mapping[str, Sequence[QualityLimit]] | None = None,
    subject_capture_settings: Mapping[str, int] | None = None,
) -> list[TargetStatement]:
    """Judge a target's stored measurements with the plain rules.

    Each capture setting in `capture_settings` is judged, and so is any
    setting id found in the measurements. A rule that needs a metric with no
    usable row for a setting makes an ``insufficient_data`` statement. Rows
    that belong to no setting are judged together, and a rule none of whose
    metrics appear among them is not asked (they are not a real setting).

    Parameters
    ----------
    measurements : `Iterable` [`QualityMeasurement`]
        Rows read back from the quality store, each with its row id.
    capture_settings : `Mapping` [`int`, `CaptureSetting`]
        The capture settings by id (``QualityRecords.capture_settings``).
    rules : `Sequence` [`QualityRule`], optional
        The rules to judge. Rules with ``computed_by`` set are skipped,
        because another module makes their statements.
    limits_override : `Mapping` [`str`, `Sequence` [`QualityLimit`]], optional
        Replacement limits by rule name, in the order of the rule's row.
    subject_capture_settings : `Mapping` [`str`, `int`], optional
        Capture setting ids by subject id, for rows that carry none. Stack
        rows carry none today, so the caller that knows which stack belongs
        to which setting passes the map.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        The statements, in the order of `order_statements`.

    Raises
    ------
    ValueError
        If a plain rule has no judging function, a row has no row id, or an
        overriding limit list is too short.
    """
    grouped = _group_rows(measurements, subject_capture_settings)
    setting_ids: list[int | None] = [
        *capture_settings,
        *(key for key in grouped if key not in capture_settings),
    ]
    overrides = limits_override or {}
    statements: list[TargetStatement] = []
    for setting_id in setting_ids:
        capture_setting = capture_settings.get(setting_id) if setting_id is not None else None
        rows = grouped.get(setting_id, [])
        for rule in rules:
            if rule.computed_by is not None:
                continue
            judge = _PLAIN_RULE_JUDGES.get(rule.name)
            if judge is None:
                raise ValueError(f"Plain rule {rule.name!r} has no judging function in judge_results.")
            if setting_id is None and not any(row.metric in {r.name for r in rule.requires} for row in rows):
                # Rows with no capture setting are not a real setting. A
                # rule none of whose metrics appear among them has nothing
                # to say about them, so it is not asked.
                continue
            readings: dict[str, _Reading] = {}
            problems: list[_Problem] = []
            for requirement in rule.requires:
                result = _read_metric(requirement.name, requirement.definition_version, rows)
                if isinstance(result, _Problem):
                    problems.append(result)
                else:
                    readings[requirement.name] = result
            if problems:
                statements.append(_insufficient_statement(rule, capture_setting, problems))
                continue
            case = _Case(rule, list(overrides.get(rule.name, rule.limits)), capture_setting, readings)
            statements.extend(judge(case))
    return order_statements(statements)
