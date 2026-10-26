"""Purpose: Statements of the target report that need a join or a fit.

Description: `judge_results` makes the statements that compare one reading
with one limit. The rules in this module need a join, a fit or a split first,
so each is a small pure function: the caller reads the data and passes it in,
and the function returns statements (or nothing, when the rule does not fire).
Every statement takes its kind, severity, action class, confidence and
wording from the rule's row in `RESULT_QUALITY_RULES`, so the table stays the
one place to read what the library says.

The rules built here are:

* ``frames_lost``: frames the stacker set aside, from the set-aside records.
* ``sky_cost``: the share of captured time lost to a bright sky (the Moon's
  share comes from the sky model, so it is model-derived).
* ``altitude_cost``: how much less efficient the frames taken low in the sky
  were, from each frame's efficiency and altitude.
* ``sharpness_tradeoff``: what keeping only the sharpest frames would do to
  the star width and to the noise.
* ``calibration_gap``: a dark or flat that the library does not hold for a
  light frame setting.
* ``more_frames`` and ``more_frames_will_not_help``: "take N more" and the
  stop signal, from `noise_growth`.

Nothing here reads a database or the sky. Missing inputs are the caller's to
report as ``insufficient_data``; a function here never invents a value.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from astrometricslib import (
    DEFAULT_DARK_TEMPERATURE_TOLERANCE_C,
    CaptureSetting,
    LimitTier,
    MeasurementReference,
    QualityLimit,
)
from wayfindinglib.analytics.capture_history import normalize_name
from wayfindinglib.analytics.result_quality.capture_overheads import (
    EXPOSURE_MATCH_SECONDS,
    CaptureOverheads,
    wall_clock_estimate,
)
from wayfindinglib.analytics.result_quality.efficiency import (
    CAUSE_SKY,
    CAUSE_SKY_MOON,
    STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC,
    STAR_WIDTH_NIGHTLY_SPREAD_KEY,
    FrameObservation,
)
from wayfindinglib.analytics.result_quality.guiding_exposure import (
    STAR_WIDTH_BUDGET_FRACTION,
    TARGET_SHARP_FRACTION,
    GuidingExposureOutcome,
)
from wayfindinglib.analytics.result_quality.noise_growth import (
    FLOOR_BASIS_TEXT,
    PRACTICAL_TIME_FACTOR,
    StackFrame,
    StackGroup,
    StopSignal,
    TakeMoreResult,
    WeightingMode,
    forecast_group_noise,
    forecast_stack_noise,
    noise_ratio_with_floor,
)
from wayfindinglib.analytics.result_quality.plain_text import (
    exposure_phrases,
    format_plain,
    plain_missing_data,
)
from wayfindinglib.analytics.result_quality.rules import rule_by_name
from wayfindinglib.analytics.result_quality.science_goal import GoalSource, goal_of_setting
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import EfficiencyReference, EfficiencyReferenceKind, ScienceGoal
from wayfindinglib.models.quality.statements import (
    ExpectedEffect,
    ProposedAction,
    StatementKind,
    TargetStatement,
)

FRAME_LOSS_WARNING_FRACTION = 0.05
"""``frames_lost`` fires when at least this share of a setting's frames was set
aside. One frame in twenty is the point where the loss shows in the stack's
total time; a single trailed frame in a long run is not worth a warning. Design
estimate, register row WAY-D60."""

SKY_COST_MINIMUM_SHARE = 0.10
"""``sky_cost`` fires when the Moon (or, without a sky model, a bright sky)
cost at least this share of the captured time. Ten percent is the same size the
stop signal treats as too small to act on (`MINIMUM_GAIN_FRACTION` in
``noise_growth.py``). Design estimate, register row WAY-D64."""

ALTITUDE_CUTS_DEG = (30.0, 40.0, 50.0)
"""Altitudes, in degrees, at which the frames are split into low and high. 30
degrees is about an airmass of 2, where the atmosphere starts to cost visibly;
the others test whether the loss reaches higher. Design estimate, register row
WAY-D65."""

ALTITUDE_MINIMUM_FRAMES_PER_SIDE = 5
"""Fewest frames on each side of a cut for the cut to be judged. With
fewer, one bad frame sets the average. Design estimate, register row
WAY-D65."""

ALTITUDE_MINIMUM_RELATIVE_LOSS = 0.15
"""``altitude_cost`` fires when the low frames were at least this much less
efficient than the high ones. The efficiency of a single frame is known to
within about 5 percent (the seeing width bias of AST-18), so 15 percent is
three times that. Design estimate, register row WAY-D65."""

SHARPNESS_KEEP_FRACTION = 0.7
"""The share of frames kept when the sharpness trade-off is worked out. The
example of plan section 6.4 keeps the sharpest 70 percent. Design estimate,
register row WAY-D66."""

SHARPNESS_MINIMUM_GAIN = 0.05
"""``sharpness_tradeoff`` fires when keeping the sharpest frames narrows the
median star by at least this fraction. Below 5 percent the stars look the same
to the eye. Design estimate, register row WAY-D66."""

SHARPNESS_MINIMUM_FRAMES = 20
"""Fewest frames of one setting for the trade-off. A subset of 70 percent then
still holds 14 frames, enough for a median. Design estimate, register row
WAY-D66."""

SEEING_AT_REFERENCE_TOLERANCE = 0.10
"""For a point-source goal, ``seeing_limits_resolution`` fires when the median
star width is within this fraction above the physical reference width (or
below it). The efficiency of a single frame is known to within about 5 percent
(the seeing width bias of AST-18), so 10 percent is twice that: closer than
this, the stars are as sharp as the telescope and a typical sky allow. Design
estimate, register row WAY-D68."""

CALIBRATION_FLAT_FRAME_TYPE = "flat"
CALIBRATION_DARK_FRAME_TYPE = "dark"


# -----------------------------------------------------------------------------
# Small text helpers
# -----------------------------------------------------------------------------


def exposure_text(exposure_seconds: float | None) -> str:
    """Write an exposure for a message.

    Parameters
    ----------
    exposure_seconds : `float` or `None`
        The exposure, in seconds.

    Returns
    -------
    text : `str`
        For example ``"90 s"``, or ``"These"`` when the exposure is unknown.
    """
    return "These" if exposure_seconds is None else f"{exposure_seconds:g} s"


def duration_text(seconds: float) -> str:
    """Write a duration for a message.

    Parameters
    ----------
    seconds : `float`
        The duration, in seconds.

    Returns
    -------
    text : `str`
        Minutes below two hours (``"35 min"``), else hours (``"3.5 h"``).
    """
    if seconds < 7200.0:
        return f"{seconds / 60.0:.0f} min"
    return f"{seconds / 3600.0:.1f} h"


def _from_rule(
    rule_name: str,
    message: str,
    reason: str,
    *,
    capture_setting: CaptureSetting | None,
    plain_message: str,
    evidence: Iterable[MeasurementReference] = (),
    proposed_action: ProposedAction | None = None,
    expected_effect: ExpectedEffect | None = None,
) -> TargetStatement:
    """Build a statement that takes its shape from a rule.

    Parameters
    ----------
    rule_name : `str`
        The rule's name in `RESULT_QUALITY_RULES`.
    message : `str`
        One plain sentence.
    reason : `str`
        Why.
    capture_setting : `CaptureSetting` or `None`
        The setting it is about.
    plain_message : `str`
        The same finding in everyday words (the rule's plain template, filled).
    evidence : `Iterable` [`MeasurementReference`], optional
        The rows it used. Repeats are dropped.
    proposed_action : `ProposedAction`, optional
        The values proposed.
    expected_effect : `ExpectedEffect`, optional
        What the action should do.

    Returns
    -------
    statement : `TargetStatement`
        The statement.
    """
    rule = rule_by_name(rule_name)
    seen: set[int] = set()
    unique: list[MeasurementReference] = []
    for reference in evidence:
        if reference.row_id is not None:
            if reference.row_id in seen:
                continue
            seen.add(reference.row_id)
        unique.append(reference)
    return TargetStatement(
        rule=rule.name,
        kind=rule.statement_kind,
        severity=rule.severity,
        action_class=rule.action_class,
        capture_setting=capture_setting,
        message=message,
        plain_message=plain_message,
        reason=reason,
        proposed_action=proposed_action,
        expected_effect=expected_effect,
        evidence=unique,
        limits=list(rule.limits),
        confidence=rule.confidence,
    )


def insufficient(
    rule_name: str, reason: str, capture_setting: CaptureSetting | None = None
) -> TargetStatement:
    """Say that a rule could not be checked, and why.

    Parameters
    ----------
    rule_name : `str`
        The rule that could not be checked.
    reason : `str`
        What is missing and how to get it.
    capture_setting : `CaptureSetting`, optional
        The setting, when the gap is about one.

    Returns
    -------
    statement : `TargetStatement`
        An ``insufficient_data`` statement.
    """
    rule = rule_by_name(rule_name)
    return TargetStatement(
        rule=rule.name,
        kind=StatementKind.INSUFFICIENT_DATA,
        capture_setting=capture_setting,
        message=f"Cannot check the rule {rule.name.replace('_', ' ')}: data is missing.",
        plain_message=plain_missing_data(rule.name),
        reason=reason,
        confidence=rule.confidence,
    )


# -----------------------------------------------------------------------------
# Frames lost
# -----------------------------------------------------------------------------


def frames_lost_statements(
    summaries: Sequence[CaptureSettingSummary],
    reasons_by_setting: Mapping[CaptureSetting, Counter[str]],
    evidence_by_setting: Mapping[CaptureSetting, Sequence[MeasurementReference]],
    unassigned_reasons: Counter[str],
    unassigned_frame_count: int,
) -> list[TargetStatement]:
    """Warn about the share of frames the stacker set aside.

    Parameters
    ----------
    summaries : `Sequence` [`CaptureSettingSummary`]
        The summary of each setting. The frames taken and the frames set
        aside come from here, so the warning and the summary agree.
    reasons_by_setting : `Mapping` [`CaptureSetting`, `Counter`]
        How many frames of each setting were set aside, by reason.
    evidence_by_setting : `Mapping`
        Maps `CaptureSetting` to a `Sequence` of `MeasurementReference`.
        The stored quarantine rows of the set-aside frames of each setting.
    unassigned_reasons : `Counter`
        Frames set aside that no stored measurement could place in a setting,
        by reason.
    unassigned_frame_count : `int`
        Frames taken whose setting is unknown, for the share.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One warning for each setting with enough frames set aside, and one
        about frames that could not be placed in a setting.
    """
    statements: list[TargetStatement] = []
    rule = rule_by_name("frames_lost")
    for summary in summaries:
        if summary.frames_taken == 0:
            continue
        share = summary.frames_set_aside / summary.frames_taken
        if share < FRAME_LOSS_WARNING_FRACTION:
            continue
        reasons = reasons_by_setting.get(summary.capture_setting, Counter())
        reason_text = _reason_text(reasons)
        fields = {
            "lost_count": summary.frames_set_aside,
            "frame_count": summary.frames_taken,
            "reason_text": reason_text,
        }
        message = rule.wording.format_map(fields)
        statements.append(
            _from_rule(
                "frames_lost",
                message,
                (
                    f"{share * 100:.0f}% of this setting's frames were moved out of the stack, at or above "
                    f"the {FRAME_LOSS_WARNING_FRACTION * 100:.0f}% that the rule treats as a loss."
                ),
                capture_setting=summary.capture_setting,
                plain_message=format_plain(rule, fields),
                evidence=evidence_by_setting.get(summary.capture_setting, ()),
            )
        )
    if unassigned_reasons and sum(unassigned_reasons.values()) > 0:
        count = sum(unassigned_reasons.values())
        total = unassigned_frame_count + count
        unassigned_fields = {
            "lost_count": count,
            "frame_count": total,
            "reason_text": _reason_text(unassigned_reasons),
        }
        statements.append(
            _from_rule(
                "frames_lost",
                rule.wording.format_map(unassigned_fields),
                (
                    "No stored measurement of these frames names their capture setting, so the loss is "
                    "not in any setting's summary. Measure the target's frames again to place them."
                ),
                capture_setting=None,
                plain_message=format_plain(rule, unassigned_fields),
            )
        )
    return statements


def _reason_text(reasons: Counter[str]) -> str:
    """Write the reasons frames were set aside.

    Parameters
    ----------
    reasons : `Counter` [`str`]
        Frame counts by reason.

    Returns
    -------
    text : `str`
        For example ``"clouded 5, trailed 7"``, or ``"no reason recorded"``.
    """
    if not reasons:
        return "no reason recorded"
    return ", ".join(f"{reason} {count}" for reason, count in sorted(reasons.items()))


# -----------------------------------------------------------------------------
# Sky cost
# -----------------------------------------------------------------------------


def sky_cost_statements(summaries: Sequence[CaptureSettingSummary]) -> list[TargetStatement]:
    """Recommend moonless (or darker) nights when the sky cost much time.

    The loss account splits the sky-brightness loss into the Moon's share and
    the rest when the sky model could be fitted. The Moon's share is
    model-derived and is said so. Without the split, the whole sky loss is
    used and is called a bright sky.

    Parameters
    ----------
    summaries : `Sequence` [`CaptureSettingSummary`]
        The summaries, with their loss accounts.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One recommendation for each setting whose sky loss reaches
        `SKY_COST_MINIMUM_SHARE` of its captured time.
    """
    rule = rule_by_name("sky_cost")
    statements: list[TargetStatement] = []
    for summary in summaries:
        account = summary.loss_account
        if account is None or summary.captured_seconds <= 0.0:
            continue
        moon_seconds = sum(s.seconds for s in account.kept_frame_deficit if s.cause == CAUSE_SKY_MOON)
        sky_seconds = sum(s.seconds for s in account.kept_frame_deficit if s.cause == CAUSE_SKY)
        has_split = any(s.cause == CAUSE_SKY_MOON for s in account.kept_frame_deficit)
        lost_seconds = moon_seconds if has_split else sky_seconds
        share = lost_seconds / summary.captured_seconds
        if share < SKY_COST_MINIMUM_SHARE:
            continue
        condition = "Moon" if has_split else "Moon, twilight or light pollution"
        advice = "Schedule moonless nights." if has_split else "Schedule darker nights, away from the Moon."
        fields = {"condition_text": condition, "loss_percent": share * 100.0, "advice_text": advice}
        statements.append(
            _from_rule(
                "sky_cost",
                rule.wording.format_map(fields),
                (
                    f"The loss account against the {account.reference_name} reference puts "
                    f"{lost_seconds / 60.0:.0f} min of {summary.captured_seconds / 60.0:.0f} min of captured "
                    "time into the sky. "
                    + (
                        "The Moon's part comes from the sky model fitted to your own frames."
                        if has_split
                        else "No sky model could be fitted, so the Moon's part is not split out."
                    )
                ),
                capture_setting=summary.capture_setting,
                plain_message=format_plain(rule, fields),
                proposed_action=ProposedAction(note=advice),
                expected_effect=ExpectedEffect(
                    efficiency_change=share, description=f"About {share * 100:.0f}% more usable time."
                ),
            )
        )
    return statements


# -----------------------------------------------------------------------------
# Seeing limits resolution
# -----------------------------------------------------------------------------


def seeing_limit_statements(
    observations: Iterable[FrameObservation],
    goal: GoalSource,
    reference_for: Callable[[CaptureSetting], EfficiencyReference | None],
    star_width_evidence: Mapping[str, MeasurementReference],
) -> list[TargetStatement]:
    """Say where seeing, not exposure time, limits the detail.

    This is a constraint: it has no action, because only better nights would
    help and a night cannot be ordered. It is made for a setting in two cases:

    * The goal is an extended object or a slitless spectrum, where the
      efficiency model leaves seeing out of the time loss because more time
      does not repair it, and the median star width exceeds the reference
      width by more than the margin. The margin is the one-standard-deviation
      spread of the baseline's nightly median widths
      (`STAR_WIDTH_NIGHTLY_SPREAD_KEY`), so ordinary night-to-night change is
      not called a limit; the physical reference has no such spread, so it
      uses `SEEING_AT_REFERENCE_TOLERANCE` of the reference width. A physical
      reference whose site seeing is not set has no width, so no statement
      is made.
    * The goal is a point source, and the median star width is within
      `SEEING_AT_REFERENCE_TOLERANCE` of the physical reference (diffraction
      plus typical seeing), so only better seeing would sharpen the stars.

    Nothing is emitted when the case cannot be evaluated (another goal, no
    reference, no star width, or a point-source goal measured against the
    equipment baseline instead of the physical reference). Not being able to
    tell is not a finding about the data, so it is not an
    ``insufficient_data`` statement: the report already says when the
    reference or the widths are missing, in the efficiency notes.

    Parameters
    ----------
    observations : `Iterable` [`FrameObservation`]
        The frames of the target. Frames the stacker set aside are skipped.
    goal : `ScienceGoal` or `Callable`
        The science goal, or a function that gives the goal of a capture
        setting (user decision U2: the goal is chosen for each setting).
    reference_for : `Callable`
        Gives the efficiency reference of a capture setting, or ``None``.
    star_width_evidence : `Mapping` [`str`, `MeasurementReference`]
        The star-width row of each frame, by frame id.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        One constraint for each setting where seeing limits the detail.
    """
    detail_goals = {ScienceGoal.EXTENDED_OBJECT, ScienceGoal.SLITLESS_SPECTRAL_RESOLUTION}
    point_goals = {ScienceGoal.FAINT_POINT_SOURCE, ScienceGoal.ASTROMETRIC_PRECISION}
    rule = rule_by_name("seeing_limits_resolution")
    widths_by_setting: dict[CaptureSetting, list[tuple[str, float]]] = {}
    for observation in observations:
        if observation.set_aside_reason is None and observation.star_width_effective_arcsec is not None:
            widths_by_setting.setdefault(observation.capture_setting, []).append((
                observation.frame_id,
                observation.star_width_effective_arcsec,
            ))
    statements: list[TargetStatement] = []
    for setting, widths in widths_by_setting.items():
        setting_goal = goal_of_setting(goal, setting)
        if setting_goal not in detail_goals | point_goals:
            continue
        reference = reference_for(setting)
        reference_width = (
            None if reference is None else reference.factors.get(STAR_WIDTH_EFFECTIVE_ARCSEC_METRIC)
        )
        if reference is None or reference_width is None:
            continue
        median_width = statistics.median(width for _frame_id, width in widths)
        if setting_goal in detail_goals:
            spread = reference.factors.get(STAR_WIDTH_NIGHTLY_SPREAD_KEY)
            margin = spread if spread is not None else reference_width * SEEING_AT_REFERENCE_TOLERANCE
            fires = median_width > reference_width + margin
        else:
            fires = reference.kind == EfficiencyReferenceKind.PHYSICAL and median_width <= reference_width * (
                1.0 + SEEING_AT_REFERENCE_TOLERANCE
            )
        if not fires:
            continue
        limit = QualityLimit(
            value=reference_width,
            unit="arcsec",
            tier=LimitTier.DESIGN_ESTIMATE,
            derivation=(
                f"The {reference.name} reference star width: "
                + (
                    "diffraction limit, typical seeing and pixel blur added as squared widths."
                    if reference.kind == EfficiencyReferenceKind.PHYSICAL
                    else "the equipment's own good nights (10th percentile of nightly medians)."
                )
            ),
            registerEntry=reference.register_entry,
        )
        statement = _from_rule(
            "seeing_limits_resolution",
            rule.wording,
            (
                f"The median star width is {median_width:.2f} arcsec against a {reference.name} reference "
                f"of {reference_width:.2f} arcsec"
                + (
                    ", and for this goal more exposure time does not repair seeing."
                    if setting_goal in detail_goals
                    else ", so the stars are already as sharp as the telescope and a typical sky allow."
                )
            ),
            capture_setting=setting,
            plain_message=rule.plain_wording,
            evidence=[star_width_evidence[f] for f, _w in widths if f in star_width_evidence],
        )
        statements.append(statement.model_copy(update={"limits": [limit]}))
    return statements


# -----------------------------------------------------------------------------
# Guiding limits exposure
# -----------------------------------------------------------------------------


def guiding_limits_statements(
    setting: CaptureSetting,
    outcome: GuidingExposureOutcome,
    star_width_evidence: Sequence[MeasurementReference] = (),
) -> list[TargetStatement]:
    """Say when the guiding keeps too few frames sharp at the exposure used.

    The user decided on 2026-10-24 (issue #95, decision U3) that this rule is
    built. It fires when the share of sharp windows at the exposure the
    setting used is below `TARGET_SHARP_FRACTION`. It proposes the longest
    exposure whose share reaches the target, when there is one. The numbers
    come from the guide log, not from measurement rows, so they are in the
    statement's ``values``.

    Parameters
    ----------
    setting : `CaptureSetting`
        The setting.
    outcome : `GuidingExposureOutcome`
        What `assess_guiding_exposure` found.
    star_width_evidence : `Sequence` [`MeasurementReference`], optional
        The star-width rows the allowed error was worked out from.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        An ``insufficient_data`` statement when the guide data is missing;
        one recommendation when the guiding limits the exposure; else none.
    """
    result = outcome.result
    if result is None:
        return [insufficient("guiding_limits_exposure", outcome.reason, setting)]
    used = result.used
    if used.sharp_fraction is None or used.sharp_fraction >= TARGET_SHARP_FRACTION:
        return []
    rule = rule_by_name("guiding_limits_exposure")
    supported = result.supported_exposure_seconds
    supported_fraction = next(
        (item.sharp_fraction for item in result.ladder if item.exposure_seconds == supported), None
    )
    clause = (
        f"; at {supported:g} s, {supported_fraction * 100:.0f}%"
        if supported is not None and supported_fraction is not None
        else ""
    )
    values: dict[str, float | str | None] = {
        "allowed_guide_rms_arcsec": round(result.allowed_guide_rms_arcsec, 3),
        "star_width_arcsec": round(result.star_width_arcsec, 3),
        "target_sharp_fraction": TARGET_SHARP_FRACTION,
        "frames_sharp_fraction": result.frames_sharp_fraction,
        "nights": ", ".join(result.nights),
    }
    for item in result.ladder:
        if item.sharp_fraction is not None:
            values[f"sharp_fraction_at_{item.exposure_seconds:g}s"] = round(item.sharp_fraction, 3)
    if supported is not None:
        action = ProposedAction(exposure_seconds=supported)
        reason_tail = (
            f" The longest exposure that reaches {TARGET_SHARP_FRACTION * 100:.0f}% is {supported:g} s."
        )
    else:
        action = ProposedAction(
            note="No tested exposure reaches the target share. Check the guiding record first."
        )
        reason_tail = f" No tested exposure reaches {TARGET_SHARP_FRACTION * 100:.0f}%."
    if supported is not None and supported_fraction is not None:
        supported_sentence = (
            f" Try {supported:g} s frames: we expect about {supported_fraction * 100:.0f}% to stay sharp."
        )
    else:
        supported_sentence = " Check the guiding first."
    fields = {
        "used_text": f"{result.used_exposure_seconds:g} s",
        "used_percent": used.sharp_fraction * 100.0,
        "supported_clause": clause,
        "supported_sentence": supported_sentence,
    }
    statement = _from_rule(
        "guiding_limits_exposure",
        rule.wording.format_map(fields),
        (
            f"A window of {result.used_exposure_seconds:g} s is sharp when its guide error on the worse "
            f"axis is at most {result.allowed_guide_rms_arcsec:.2f} arcsec, which widens a "
            f"{result.star_width_arcsec:.2f} arcsec star by at most {STAR_WIDTH_BUDGET_FRACTION * 100:.0f}%. "
            f"{used.windows_sharp} of {used.windows_judged} windows on {len(result.nights)} night(s) were."
            + reason_tail
        ),
        capture_setting=setting,
        plain_message=format_plain(rule, fields),
        evidence=star_width_evidence,
        proposed_action=action,
    )
    return [statement.model_copy(update={"values": values})]


# -----------------------------------------------------------------------------
# Altitude cost
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AltitudeSample:
    """One frame's altitude, efficiency and exposure.

    Attributes
    ----------
    altitude_deg : `float`
        The altitude of the pointing, in degrees.
    eta : `float`
        The frame's efficiency, above 0 and at most 1.
    exposure_seconds : `float`
        The frame's exposure, in seconds.
    """

    altitude_deg: float
    eta: float
    exposure_seconds: float


@dataclass(frozen=True)
class AltitudeCost:
    """How much less efficient the low frames of a setting were.

    Attributes
    ----------
    cut_deg : `float`
        The altitude that splits the frames.
    relative_loss : `float`
        One minus the low frames' efficiency over the high frames'.
    low_frame_count, high_frame_count : `int`
        Frames on each side of the cut.
    """

    cut_deg: float
    relative_loss: float
    low_frame_count: int
    high_frame_count: int


def find_altitude_cost(samples: Sequence[AltitudeSample]) -> AltitudeCost | None:
    """Find the altitude cut with the largest loss.

    Each cut in `ALTITUDE_CUTS_DEG` splits the frames into low and high. The
    efficiency of each side is the exposure-weighted mean of its frames'
    efficiencies. A cut counts only with `ALTITUDE_MINIMUM_FRAMES_PER_SIDE`
    frames on both sides.

    Parameters
    ----------
    samples : `Sequence` [`AltitudeSample`]
        The kept frames of one capture setting.

    Returns
    -------
    cost : `AltitudeCost` or `None`
        The cut with the largest relative loss, when it reaches
        `ALTITUDE_MINIMUM_RELATIVE_LOSS`; else ``None``.
    """
    best: AltitudeCost | None = None
    for cut in ALTITUDE_CUTS_DEG:
        low = [s for s in samples if s.altitude_deg < cut]
        high = [s for s in samples if s.altitude_deg >= cut]
        if len(low) < ALTITUDE_MINIMUM_FRAMES_PER_SIDE or len(high) < ALTITUDE_MINIMUM_FRAMES_PER_SIDE:
            continue
        low_eta = sum(s.eta * s.exposure_seconds for s in low) / sum(s.exposure_seconds for s in low)
        high_eta = sum(s.eta * s.exposure_seconds for s in high) / sum(s.exposure_seconds for s in high)
        if high_eta <= 0.0:
            continue
        loss = 1.0 - low_eta / high_eta
        if best is None or loss > best.relative_loss:
            best = AltitudeCost(cut, loss, len(low), len(high))
    if best is None or best.relative_loss < ALTITUDE_MINIMUM_RELATIVE_LOSS:
        return None
    return best


def altitude_cost_statement(
    cost: AltitudeCost, capture_setting: CaptureSetting | None, evidence: Iterable[MeasurementReference]
) -> TargetStatement:
    """Recommend imaging above the altitude where the loss starts.

    Parameters
    ----------
    cost : `AltitudeCost`
        The cut and its loss.
    capture_setting : `CaptureSetting` or `None`
        The setting.
    evidence : `Iterable` [`MeasurementReference`]
        The rows the frames' efficiencies came from.

    Returns
    -------
    statement : `TargetStatement`
        The recommendation.
    """
    rule = rule_by_name("altitude_cost")
    fields = {
        "altitude_degrees": cost.cut_deg,
        "loss_percent": cost.relative_loss * 100.0,
        "advice_altitude_degrees": cost.cut_deg,
    }
    return _from_rule(
        "altitude_cost",
        rule.wording.format_map(fields),
        (
            f"{cost.low_frame_count} frames below {cost.cut_deg:.0f} degrees were "
            f"{cost.relative_loss * 100:.0f}% less efficient than {cost.high_frame_count} frames above it. "
            "Altitude also moves with time of night, so part of the loss may be the sky or the seeing "
            "of that hour."
        ),
        capture_setting=capture_setting,
        plain_message=format_plain(rule, fields),
        evidence=evidence,
        proposed_action=ProposedAction(note=f"Image above {cost.cut_deg:.0f} degrees."),
        expected_effect=ExpectedEffect(
            efficiency_change=cost.relative_loss,
            description=f"About {cost.relative_loss * 100:.0f}% more usable time per frame.",
        ),
    )


# -----------------------------------------------------------------------------
# Sharpness trade-off
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class SharpnessTradeoff:
    """What keeping the sharpest frames does to the stars and the noise.

    Attributes
    ----------
    keep_fraction : `float`
        The share of frames kept.
    kept_count, total_count : `int`
        Frames kept and frames in all.
    star_width_change : `float`
        Change of the median star width, as a fraction (-0.12 is 12 percent
        sharper).
    noise_change : `float`
        Change of the forecast stack noise, as a fraction (0.15 is 15 percent
        noisier).
    """

    keep_fraction: float
    kept_count: int
    total_count: int
    star_width_change: float
    noise_change: float


def find_sharpness_tradeoff(
    frames: Sequence[StackFrame], widths_arcsec: Sequence[float], mode: WeightingMode
) -> SharpnessTradeoff | None:
    """Work out the trade-off of keeping the sharpest frames.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The kept frames of one capture setting.
    widths_arcsec : `Sequence` [`float`]
        Each frame's star width, in arcseconds, in the order of `frames`.
    mode : `WeightingMode`
        The weighting mode of the stack, for the noise forecast.

    Returns
    -------
    tradeoff : `SharpnessTradeoff` or `None`
        The trade-off, when there are `SHARPNESS_MINIMUM_FRAMES` frames, the
        noise can be forecast and the stars narrow by at least
        `SHARPNESS_MINIMUM_GAIN`; else ``None``. Always ``None`` for the
        ``group`` mode: the noise of a stack of exposure groups depends on the
        other groups, so `find_sharpness_tradeoff_in_groups` is used instead.
    """
    if mode == WeightingMode.GROUP:
        return None
    if len(frames) != len(widths_arcsec) or len(frames) < SHARPNESS_MINIMUM_FRAMES:
        return None
    order = sorted(range(len(frames)), key=lambda index: widths_arcsec[index])
    kept_count = math.ceil(SHARPNESS_KEEP_FRACTION * len(frames))
    kept_indices = order[:kept_count]
    all_noise = forecast_stack_noise(frames, mode).noise
    kept_noise = forecast_stack_noise([frames[index] for index in kept_indices], mode).noise
    if not all_noise or kept_noise is None:
        return None
    width_change = (
        statistics.median(widths_arcsec[index] for index in kept_indices) / statistics.median(widths_arcsec)
        - 1.0
    )
    if -width_change < SHARPNESS_MINIMUM_GAIN:
        return None
    return SharpnessTradeoff(
        keep_fraction=SHARPNESS_KEEP_FRACTION,
        kept_count=kept_count,
        total_count=len(frames),
        star_width_change=width_change,
        noise_change=kept_noise / all_noise - 1.0,
    )


def find_sharpness_tradeoff_in_groups(
    groups: Sequence[StackGroup], group_position: int, widths_arcsec: Sequence[float]
) -> SharpnessTradeoff | None:
    """Work out the sharpness trade-off for one group of a group stack.

    Keeping only the sharpest frames of one exposure group changes that
    group's weight (``N t^2 / s^2``, with fewer frames) and so its share of
    the combined stack. The other groups dilute the change, so the noise
    change is found on the whole stack (`forecast_group_noise`), not on the
    group alone. The two stacks are compared as a fraction of the stars'
    flux (`noise_ratio_with_floor`), which leaves the combine's scale out.

    Parameters
    ----------
    groups : `Sequence` [`StackGroup`]
        The groups of the stack, with their frame noise and gain.
    group_position : `int`
        The group whose sharpest frames are kept.
    widths_arcsec : `Sequence` [`float`]
        Each of that group's frames' star width, in arcseconds, in the order
        of its frames.

    Returns
    -------
    tradeoff : `SharpnessTradeoff` or `None`
        The trade-off, under the same limits as `find_sharpness_tradeoff`;
        ``None`` when the stack cannot be forecast.
    """
    group = groups[group_position]
    if len(group.frames) != len(widths_arcsec) or len(group.frames) < SHARPNESS_MINIMUM_FRAMES:
        return None
    order = sorted(range(len(group.frames)), key=lambda index: widths_arcsec[index])
    kept_count = math.ceil(SHARPNESS_KEEP_FRACTION * len(group.frames))
    kept_indices = sorted(order[:kept_count])
    kept_group = group.model_copy(
        update={"frames": tuple(group.frames[index] for index in kept_indices), "in_group_weights": None}
    )
    kept_groups = [
        kept_group if position == group_position else other for position, other in enumerate(groups)
    ]
    all_forecast = forecast_group_noise(groups)
    kept_forecast = forecast_group_noise(kept_groups)
    ratio = noise_ratio_with_floor(all_forecast, kept_forecast, None)
    if ratio is None:
        return None
    width_change = (
        statistics.median(widths_arcsec[index] for index in kept_indices) / statistics.median(widths_arcsec)
        - 1.0
    )
    if -width_change < SHARPNESS_MINIMUM_GAIN:
        return None
    return SharpnessTradeoff(
        keep_fraction=SHARPNESS_KEEP_FRACTION,
        kept_count=kept_count,
        total_count=len(group.frames),
        star_width_change=width_change,
        noise_change=ratio - 1.0,
    )


def sharpness_statement(
    tradeoff: SharpnessTradeoff,
    capture_setting: CaptureSetting | None,
    evidence: Iterable[MeasurementReference],
) -> TargetStatement:
    """Describe the sharpness trade-off.

    Parameters
    ----------
    tradeoff : `SharpnessTradeoff`
        The trade-off.
    capture_setting : `CaptureSetting` or `None`
        The setting.
    evidence : `Iterable` [`MeasurementReference`]
        The star width rows.

    Returns
    -------
    statement : `TargetStatement`
        The recommendation (a data selection; the user decides).
    """
    rule = rule_by_name("sharpness_tradeoff")
    fields = {
        "keep_percent": tradeoff.keep_fraction * 100.0,
        "sharper_percent": -tradeoff.star_width_change * 100.0,
        "noisier_percent": tradeoff.noise_change * 100.0,
    }
    return _from_rule(
        "sharpness_tradeoff",
        rule.wording.format_map(fields),
        (
            f"Of {tradeoff.total_count} frames, the {tradeoff.kept_count} sharpest were compared with all. "
            "The noise change is a forecast from the frames' sky and read noise, not a measurement."
        ),
        capture_setting=capture_setting,
        plain_message=format_plain(rule, fields),
        evidence=evidence,
        proposed_action=ProposedAction(
            note=f"Stack only the sharpest {tradeoff.keep_fraction * 100:.0f}% of the frames."
        ),
        expected_effect=ExpectedEffect(
            noise_change_fraction=tradeoff.noise_change,
            description=(
                f"Stars {-tradeoff.star_width_change * 100:.0f}% narrower, "
                f"noise {tradeoff.noise_change * 100:+.0f}%."
            ),
        ),
    )


# -----------------------------------------------------------------------------
# Calibration gap
# -----------------------------------------------------------------------------


def _leading_integer(value: object) -> int | None:
    """Read the first integer in a value such as ``"2x2"`` or ``2``.

    Parameters
    ----------
    value : `object`
        A binning written as a number or as text.

    Returns
    -------
    number : `int` or `None`
        The first integer, or ``None`` when there is none.
    """
    digits = ""
    for character in str(value):
        if character.isdigit():
            digits += character
        elif digits:
            break
    return int(digits) if digits else None


def _as_float(value: object) -> float | None:
    """Read a value as a float, or ``None`` when it is not a number.

    Parameters
    ----------
    value : `object`
        The value.

    Returns
    -------
    number : `float` or `None`
        The number.
    """
    if not isinstance(value, int | float | str):
        return None
    try:
        return float(value)
    except ValueError:
        return None


@dataclass(frozen=True)
class LightFrameGroup:
    """Light frames of one setting and their sensor temperature.

    Attributes
    ----------
    capture_setting : `CaptureSetting`
        The light frames' setting.
    sensor_temperature_c : `float` or `None`
        The median sensor temperature of the frames, in degrees Celsius.
    frame_count : `int`
        How many frames.
    """

    capture_setting: CaptureSetting
    sensor_temperature_c: float | None
    frame_count: int


@dataclass(frozen=True)
class CalibrationFinding:
    """A calibration frame type the library lacks for a light setting.

    Attributes
    ----------
    frame_type : `str`
        ``"dark"`` or ``"flat"``.
    group : `LightFrameGroup`
        The light frames that need it.
    setting_text : `str`
        The settings to take it at, in words.
    nearest_row_text : `str`
        The nearest row the library holds and what differs, or a sentence
        saying the library has none for the camera.
    """

    frame_type: str
    group: LightFrameGroup
    setting_text: str
    nearest_row_text: str


def _row_text(kind: str, row: Mapping[str, object]) -> str:
    """Describe one calibration library row.

    Parameters
    ----------
    kind : `str`
        ``"dark"``, ``"bias"`` or ``"flat"``.
    row : `Mapping` [`str`, `object`]
        The row from ``CalibrationCatalog.query(detail="counts")``.

    Returns
    -------
    text : `str`
        For example ``"library dark row: ASI533MM, gain 100, offset 50, ..."``.
    """
    parts = [str(row.get("camera", "?"))]
    if kind == "flat":
        parts.append(f"filter {row.get('filter', '?')}")
    parts.append(f"gain {row.get('iso', '?')}")
    parts.append(f"offset {row.get('offset', '?')}")
    parts.append(f"bin {row.get('binning', '?')}")
    if kind == "dark":
        exposure = _as_float(row.get("exposure"))
        parts.append("exposure unknown" if exposure is None else f"{exposure:g} s")
        temperature = _as_float(row.get("temperature_c"))
        if temperature is not None:
            parts.append(f"{temperature:g} C")
    parts.append(f"{row.get('count', 0)} frames")
    return f"library {kind} row: " + ", ".join(parts)


def _camera_matches(setting: CaptureSetting, row: Mapping[str, object]) -> bool:
    """Say whether a library row is for the setting's camera.

    Parameters
    ----------
    setting : `CaptureSetting`
        The light frames' setting.
    row : `Mapping` [`str`, `object`]
        A library row.

    Returns
    -------
    matches : `bool`
        True when the names agree, ignoring case and punctuation.
    """
    return normalize_name(setting.camera) == normalize_name(str(row.get("camera", "")))


def _shared_setting_matches(setting: CaptureSetting, row: Mapping[str, object]) -> list[str]:
    """List the shared settings (gain, offset, binning) that differ.

    A setting whose value is unknown (``None``) is not compared.

    Parameters
    ----------
    setting : `CaptureSetting`
        The light frames' setting.
    row : `Mapping` [`str`, `object`]
        A library row.

    Returns
    -------
    differences : `list` [`str`]
        Names of the settings that differ; empty when the row matches.
    """
    differences: list[str] = []
    gain = _as_float(row.get("iso"))
    if setting.gain_setting is not None and (gain is None or abs(gain - setting.gain_setting) > 1e-6):
        differences.append("gain")
    offset = _as_float(row.get("offset"))
    if setting.offset is not None and (offset is None or abs(offset - setting.offset) > 1e-6):
        differences.append("offset")
    binning = _leading_integer(row.get("binning", ""))
    if setting.binning is not None and binning != setting.binning:
        differences.append("binning")
    return differences


def _setting_words(setting: CaptureSetting, temperature_c: float | None, with_exposure: bool) -> str:
    """Write the settings a calibration frame must be taken at.

    Parameters
    ----------
    setting : `CaptureSetting`
        The light frames' setting.
    temperature_c : `float` or `None`
        The light frames' sensor temperature, for darks.
    with_exposure : `bool`
        Whether to include the exposure (darks) or the filter (flats).

    Returns
    -------
    text : `str`
        For example ``"gain 100, offset 50, bin 1, 60 s, -10 C"``.
    """
    parts = []
    if setting.gain_setting is not None:
        parts.append(f"gain {setting.gain_setting}")
    if setting.offset is not None:
        parts.append(f"offset {setting.offset}")
    if setting.binning is not None:
        parts.append(f"bin {setting.binning}")
    if with_exposure:
        if setting.exposure_seconds is not None:
            parts.append(f"{setting.exposure_seconds:g} s")
        if temperature_c is not None:
            parts.append(f"{temperature_c:.0f} C")
    elif setting.filter_name:
        parts.append(f"filter {setting.filter_name}")
    return ", ".join(parts) if parts else "the light frames' settings"


def find_calibration_gaps(
    groups: Sequence[LightFrameGroup],
    dark_rows: Sequence[Mapping[str, object]],
    flat_rows: Sequence[Mapping[str, object]],
) -> list[CalibrationFinding]:
    """Find the darks and flats the library lacks for the light frames.

    A dark matches when its camera, gain, offset, binning and exposure agree
    (the exposure to within `EXPOSURE_MATCH_SECONDS`) and its sensor
    temperature is within `DEFAULT_DARK_TEMPERATURE_TOLERANCE_C` of the light
    frames' when both are known. A flat matches when its camera, filter, gain,
    offset and binning agree. Biases are not asked for: darks of the same
    exposure carry the bias.

    Parameters
    ----------
    groups : `Sequence` [`LightFrameGroup`]
        The light frame settings of the target.
    dark_rows, flat_rows : `Sequence` [`Mapping`]
        The rows of ``CalibrationCatalog.query(detail="counts")`` for darks
        and flats.

    Returns
    -------
    findings : `list` [`CalibrationFinding`]
        One for each (setting, frame type) with no matching row, with the
        nearest row of the camera so the user sees what differs.
    """
    findings: list[CalibrationFinding] = []
    for group in groups:
        setting = group.capture_setting
        # Darks
        camera_darks = [row for row in dark_rows if _camera_matches(setting, row) and row.get("count", 0)]
        matching_darks = []
        for row in camera_darks:
            if _shared_setting_matches(setting, row):
                continue
            exposure = _as_float(row.get("exposure"))
            if setting.exposure_seconds is not None and (
                exposure is None or abs(exposure - setting.exposure_seconds) > EXPOSURE_MATCH_SECONDS
            ):
                continue
            temperature = _as_float(row.get("temperature_c"))
            if (
                group.sensor_temperature_c is not None
                and temperature is not None
                and abs(temperature - group.sensor_temperature_c) > DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
            ):
                continue
            matching_darks.append(row)
        if not matching_darks:
            findings.append(
                CalibrationFinding(
                    CALIBRATION_DARK_FRAME_TYPE,
                    group,
                    _setting_words(setting, group.sensor_temperature_c, True),
                    _nearest_text("dark", setting, group.sensor_temperature_c, camera_darks),
                )
            )
        # Flats
        camera_flats = [row for row in flat_rows if _camera_matches(setting, row) and row.get("count", 0)]
        matching_flats = [
            row
            for row in camera_flats
            if not _shared_setting_matches(setting, row)
            and (
                setting.filter_name is None
                or str(row.get("filter", "")).casefold() == setting.filter_name.casefold()
            )
        ]
        if not matching_flats:
            findings.append(
                CalibrationFinding(
                    CALIBRATION_FLAT_FRAME_TYPE,
                    group,
                    _setting_words(setting, None, False),
                    _nearest_text("flat", setting, None, camera_flats),
                )
            )
    return findings


def _nearest_text(
    kind: str,
    setting: CaptureSetting,
    temperature_c: float | None,
    camera_rows: Sequence[Mapping[str, object]],
) -> str:
    """Name the library row closest to what is needed, or say there is none.

    Parameters
    ----------
    kind : `str`
        ``"dark"`` or ``"flat"``.
    setting : `CaptureSetting`
        The light frames' setting.
    temperature_c : `float` or `None`
        The light frames' sensor temperature.
    camera_rows : `Sequence` [`Mapping`]
        The library rows of the same camera that hold frames.

    Returns
    -------
    text : `str`
        The row that differs in the fewest settings, with the settings that
        differ; or a sentence saying the camera has none.
    """
    if not camera_rows:
        return f"The library holds no {kind}s for the camera {setting.camera}."

    def differences(row: Mapping[str, object]) -> list[str]:
        """List the settings in which a row differs from what is needed.

        Parameters
        ----------
        row : `Mapping` [`str`, `object`]
            A library row.

        Returns
        -------
        names : `list` [`str`]
            The differing settings.
        """
        found = _shared_setting_matches(setting, row)
        if kind == "dark":
            exposure = _as_float(row.get("exposure"))
            if setting.exposure_seconds is not None and (
                exposure is None or abs(exposure - setting.exposure_seconds) > EXPOSURE_MATCH_SECONDS
            ):
                found.append("exposure")
            temperature = _as_float(row.get("temperature_c"))
            if (
                temperature_c is not None
                and temperature is not None
                and abs(temperature - temperature_c) > DEFAULT_DARK_TEMPERATURE_TOLERANCE_C
            ):
                found.append("temperature")
        elif (
            setting.filter_name is not None
            and str(row.get("filter", "")).casefold() != setting.filter_name.casefold()
        ):
            found.append("filter")
        return found

    closest = min(camera_rows, key=lambda row: len(differences(row)))
    return (
        f"Nearest {_row_text(kind, closest)}; it differs in {', '.join(differences(closest)) or 'nothing'}."
    )


def calibration_gap_statement(finding: CalibrationFinding) -> TargetStatement:
    """Build the recommendation to take a missing calibration frame.

    Parameters
    ----------
    finding : `CalibrationFinding`
        The gap.

    Returns
    -------
    statement : `TargetStatement`
        The recommendation. Its reason cites the nearest library row.
    """
    rule = rule_by_name("calibration_gap")
    setting = finding.group.capture_setting
    plural = "darks" if finding.frame_type == CALIBRATION_DARK_FRAME_TYPE else "flats"
    fields = {"frame_type_text": plural, "setting_text": finding.setting_text}
    return _from_rule(
        "calibration_gap",
        rule.wording.format_map(fields),
        (
            f"{finding.group.frame_count} light frames need them. {finding.nearest_row_text} "
            f"The match uses the camera, gain, offset, binning and "
            + (
                f"exposure and sensor temperature (within {DEFAULT_DARK_TEMPERATURE_TOLERANCE_C:g} C)."
                if finding.frame_type == CALIBRATION_DARK_FRAME_TYPE
                else "filter."
            )
        ),
        capture_setting=setting,
        plain_message=format_plain(rule, fields),
        proposed_action=ProposedAction(
            calibration_frame_type=finding.frame_type, note=f"Take {plural} at {finding.setting_text}."
        ),
    )


# -----------------------------------------------------------------------------
# More frames, and the stop signal
# -----------------------------------------------------------------------------


FLOOR_CAUSE_TEXT = "The floor is a pattern fixed on the sky (faint stars, the flat field, calibration)."
"""The sentence that says what a noise floor probably is."""

FLOOR_NOT_REMOVED_TEXT = "More frames will not remove it."
"""Ends the floor's cause sentences when the report has no calibration advice
for the setting. The report builder swaps it for `FLOOR_CALIBRATION_TEXT` when
the same report carries a ``calibration_gap`` statement for the setting."""

FLOOR_CALIBRATION_TEXT = "The calibration recommendations in this report may lower it."
"""What replaces `FLOOR_NOT_REMOVED_TEXT` when calibration advice exists."""

PLAIN_FLOOR_NOT_REMOVED_TEXT = "and more frames will not remove it."
"""The plain-message ending that says the floor stays. The report builder swaps
it for `PLAIN_FLOOR_CALIBRATION_TEXT` when calibration advice exists."""

PLAIN_FLOOR_CALIBRATION_TEXT = "and the calibration recommendations in this report may lower it."
"""What replaces `PLAIN_FLOOR_NOT_REMOVED_TEXT` when advice exists."""


def _floor_basis_sentence(result: TakeMoreResult) -> str:
    """Say which measurement decided the floor.

    Parameters
    ----------
    result : `TakeMoreResult`
        An answer that may carry a floor.

    Returns
    -------
    text : `str`
        For example "The floor was measured from the odd and even half
        stacks.", or an empty string when the answer names no basis.
    """
    text = FLOOR_BASIS_TEXT.get(result.floor_basis or "")
    return f"The floor was {text}." if text else ""


def _floor_share_sentence(result: TakeMoreResult) -> str:
    """Say how much frames can give at most, and why.

    Parameters
    ----------
    result : `TakeMoreResult`
        An answer with ``practical_limit_applied`` true.

    Returns
    -------
    text : `str`
        For example "More frames give less each time: at most about 30% in
        all, because a noise floor of 0.000583 (stack units) makes up about
        49% of the noise."
    """
    most_cut = 100.0 * (result.most_reachable_cut or 0.0)
    share = 100.0 * (result.floor_variance_share or 0.0)
    floor_text = f" of {result.floor_noise:.3g} (stack units)" if result.floor_noise is not None else ""
    return (
        f"More frames give less each time: at most about {most_cut:.0f}% in all, because a noise "
        f"floor{floor_text} makes up about {share:.0f}% of the noise. {_floor_basis_sentence(result)}"
    ).strip()


def _diminishing_returns_statement(
    result: TakeMoreResult,
    capture_setting: CaptureSetting | None,
    overheads: CaptureOverheads,
    evidence: Iterable[MeasurementReference],
    exposure_note: str,
) -> TargetStatement | None:
    """Build the recommendation that gives one doubling in place of a count.

    Used when a noise floor puts the target beyond the practical limit
    (`TakeMoreResult.practical_limit_applied`). It asks for the frames that
    double the stack's exposure time, says what that buys, what the most
    frames could give, and where the floor probably comes from.

    Parameters
    ----------
    result : `TakeMoreResult`
        The answer, with ``doubling_frames`` and ``doubling_noise_cut``.
    capture_setting : `CaptureSetting` or `None`
        The setting the new frames use.
    overheads : `CaptureOverheads`
        Download and settle times, for the wall-clock time.
    evidence : `Iterable` [`MeasurementReference`]
        The rows the forecast used.
    exposure_note : `str`
        Why the exposure differs from the setting's, when it does.

    Returns
    -------
    statement : `TargetStatement` or `None`
        The ``more_frames`` recommendation, or ``None`` when the result lacks
        the doubling numbers.
    """
    if (
        result.doubling_frames is None
        or result.doubling_noise_cut is None
        or result.new_exposure_seconds is None
    ):
        return None
    rule = rule_by_name("more_frames")
    clock = wall_clock_estimate(result.doubling_frames, result.new_exposure_seconds, overheads)
    cut = 100.0 * result.doubling_noise_cut
    exposure = exposure_text(result.new_exposure_seconds)
    message = (
        f"Taking as many frames again as the stack holds ({result.doubling_frames} more at {exposure}, "
        f"about {duration_text(clock.total_seconds)} with downloads) cuts the noise by about {cut:.0f}%. "
        f"{_floor_share_sentence(result)} {FLOOR_CAUSE_TEXT} {FLOOR_NOT_REMOVED_TEXT}"
    )
    fields = {
        "frame_count": result.doubling_frames,
        "wall_clock_text": duration_text(clock.total_seconds) + " with downloads",
        "noise_percent": cut,
        **exposure_phrases(result.new_exposure_seconds),
    }
    plain = (
        f"{format_plain(rule, fields)} Frames after that help less, at most about "
        f"{100.0 * (result.most_reachable_cut or 0.0):.0f}% in all, because part of the noise is a "
        f"pattern fixed on the sky, {PLAIN_FLOOR_NOT_REMOVED_TEXT}"
    )
    statement = _from_rule(
        "more_frames",
        message,
        (
            "Doubling the effective time is the step, because it is about the smallest change the eye "
            "sees. The count asked for is the practical one: the target's own count is beyond "
            f"{PRACTICAL_TIME_FACTOR:g} times the stack's time, so one doubling is given instead. "
            f"{result.reason or ''} {exposure_note}"
        ).strip(),
        capture_setting=capture_setting,
        plain_message=plain,
        evidence=evidence,
        proposed_action=ProposedAction(
            exposure_seconds=result.new_exposure_seconds, frame_count=result.doubling_frames
        ),
        expected_effect=ExpectedEffect(
            noise_change_fraction=-result.doubling_noise_cut,
            effective_time_added_seconds=result.added_exposure_seconds,
            wall_clock_seconds=clock.total_seconds,
            description=f"Noise down about {cut:.0f}%.",
        ),
    )
    return statement.model_copy(
        update={
            "values": {
                **statement.values,
                "practical_limit_applied": 1.0,
                "doubling_frames": float(result.doubling_frames),
                "doubling_noise_cut": result.doubling_noise_cut,
                "most_reachable_cut": result.most_reachable_cut,
            }
        }
    )


def more_frames_statement(
    result: TakeMoreResult,
    capture_setting: CaptureSetting | None,
    overheads: CaptureOverheads,
    evidence: Iterable[MeasurementReference],
    *,
    exposure_note: str = "",
) -> TargetStatement | None:
    """Build the "take N more" recommendation.

    Parameters
    ----------
    result : `TakeMoreResult`
        The answer of `frames_to_cut_noise`.
    capture_setting : `CaptureSetting` or `None`
        The setting the new frames use.
    overheads : `CaptureOverheads`
        Download and settle times, for the wall-clock time.
    evidence : `Iterable` [`MeasurementReference`]
        The rows the forecast used.
    exposure_note : `str`, optional
        Why the exposure differs from the setting's, when it does.

    Returns
    -------
    statement : `TargetStatement` or `None`
        The recommendation, or ``None`` when the result has no frame count
        (the caller then reports the reason as missing data).
    """
    if result.practical_limit_applied:
        return _diminishing_returns_statement(result, capture_setting, overheads, evidence, exposure_note)
    if result.frames_needed is None or result.new_exposure_seconds is None:
        return None
    rule = rule_by_name("more_frames")
    clock = wall_clock_estimate(result.frames_needed, result.new_exposure_seconds, overheads)
    change = (result.predicted_noise_ratio or result.target_noise_ratio) - 1.0
    fields = {
        "frame_count": result.frames_needed,
        "exposure_text": exposure_text(result.new_exposure_seconds),
        "wall_clock_text": duration_text(clock.total_seconds) + " with downloads",
        "noise_percent": -change * 100.0,
        **exposure_phrases(result.new_exposure_seconds),
    }
    return _from_rule(
        "more_frames",
        rule.wording.format_map(fields),
        (
            "Doubling the effective time is the step, because it is about the smallest change the eye sees. "
            f"The count follows your own frames' sky and read noise. {result.reason or ''} {exposure_note}"
        ).strip(),
        capture_setting=capture_setting,
        plain_message=format_plain(rule, fields),
        evidence=evidence,
        proposed_action=ProposedAction(
            exposure_seconds=result.new_exposure_seconds, frame_count=result.frames_needed
        ),
        expected_effect=ExpectedEffect(
            noise_change_fraction=change,
            effective_time_added_seconds=result.added_exposure_seconds,
            wall_clock_seconds=clock.total_seconds,
            description=f"Noise down about {-change * 100:.0f}%.",
        ),
    )


def floor_limited_statement(
    result: TakeMoreResult,
    capture_setting: CaptureSetting | None,
    evidence: Iterable[MeasurementReference],
) -> TargetStatement:
    """Build the warning that a noise floor makes more frames pointless.

    Used when "take N more" has no count because a floor makes up so much of
    the stack's noise that the target cannot be reached
    (`TakeMoreResult.floor_limited`) and one doubling of the stack's time is
    not worth asking for (`TakeMoreResult.doubling_worthwhile` false). It says
    so in place of a count, with the same floor sentences as the
    diminishing-returns recommendation.

    Parameters
    ----------
    result : `TakeMoreResult`
        The answer, with ``floor_limited`` true.
    capture_setting : `CaptureSetting` or `None`
        The setting.
    evidence : `Iterable` [`MeasurementReference`]
        The rows the floor came from.

    Returns
    -------
    statement : `TargetStatement`
        The ``more_frames_will_not_help`` warning, worded for a floor.
    """
    if result.practical_limit_applied:
        doubling_cut = 100.0 * (result.doubling_noise_cut or 0.0)
        message = (
            f"More frames will not help: as many frames again as the stack holds would cut the noise by "
            f"only about {doubling_cut:.0f}%. {_floor_share_sentence(result)} {FLOOR_CAUSE_TEXT} "
            f"{FLOOR_NOT_REMOVED_TEXT}"
        )
        plain = (
            f"More frames probably will not help: doubling the frames would cut the noise by only about "
            f"{doubling_cut:.0f}%, because part of the noise is a pattern fixed on the sky, "
            f"{PLAIN_FLOOR_NOT_REMOVED_TEXT}"
        )
    else:
        share = 100.0 * (result.floor_variance_share or 0.0)
        best_cut = 100.0 * (1.0 - (result.lowest_reachable_ratio or 1.0))
        floor_text = f"{result.floor_noise:.3g} in stack units, " if result.floor_noise is not None else ""
        basis_sentence = _floor_basis_sentence(result)
        basis_sentence = f"{basis_sentence} " if basis_sentence else ""
        message = (
            f"More frames will not help much: a noise floor ({floor_text}{share:.0f}% of the noise) does "
            f"not shrink with more frames, so even very many more would cut the noise by only "
            f"{best_cut:.0f}%. {basis_sentence}{FLOOR_CAUSE_TEXT} {FLOOR_NOT_REMOVED_TEXT}"
        )
        plain = (
            f"More frames probably will not help: about {share:.0f}% of the noise does not shrink with "
            f"more frames. Find the cause (flats, dither, faint stars in the field) before adding more."
        )
    return _from_rule(
        "more_frames_will_not_help",
        message,
        result.reason or message,
        capture_setting=capture_setting,
        plain_message=plain,
        evidence=evidence,
    )


def stop_statement(
    signal: StopSignal,
    capture_setting: CaptureSetting | None,
    effective_seconds: float | None,
    evidence: Iterable[MeasurementReference],
) -> TargetStatement:
    """Build the warning that more frames will not help.

    Parameters
    ----------
    signal : `StopSignal`
        The stop signal, with ``stopped`` true.
    capture_setting : `CaptureSetting` or `None`
        The setting.
    effective_seconds : `float` or `None`
        The stack's effective time, in seconds, for the message.
    evidence : `Iterable` [`MeasurementReference`]
        The rows of the stack steps the fit used.

    Returns
    -------
    statement : `TargetStatement`
        The warning.
    """
    rule = rule_by_name("more_frames_will_not_help")
    time_text = duration_text(effective_seconds) if effective_seconds else "the present time"
    fields = {
        "effective_time_text": time_text,
        "advice_text": "Retake flats or dither before adding frames.",
        "plain_advice_text": (
            "Retake your flats, or shift the telescope slightly between frames, before adding more."
        ),
    }
    return _from_rule(
        "more_frames_will_not_help",
        rule.wording.format_map(fields),
        signal.reason,
        capture_setting=capture_setting,
        plain_message=format_plain(rule, fields),
        evidence=evidence,
    )
