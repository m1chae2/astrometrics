"""Purpose: Work out a target's depth goal status and keep its chosen level.

Description: `build_depth_goal_status` answers, for one target: which level
the person chose, how deep the newest library stack is, which level that
reaches, and how long it would take to reach the chosen one. The time comes
from the "take N more" forecast of the target report, aimed at the noise ratio
that gives the missing depth. `depth_goal_met` is the short form the
suggestion refresh uses. `read_chosen_level` and `store_chosen_level` keep the
chosen level in wayfindinglib's own database (the ``target_depth_goal`` dataset
type), never in the configuration file. Register row WAY-D94 holds the level
values.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from astrometricslib import AstrometricsError, InvalidArgumentError, SubjectLevel
from wayfindinglib.analytics.depth_goal import (
    DEFAULT_DEPTH_LEVEL,
    DEPTH_METRICS,
    DEPTH_UNITS,
    LEVEL_LABELS,
    LEVEL_ORDER,
    depth_kind_of_target,
    goal_depth,
    level_label,
    level_reached,
    noise_ratio_for_depth_gain,
    time_text,
)
from wayfindinglib.analytics.result_quality.fixed_pattern_location import FixedPatternCause
from wayfindinglib.analytics.result_quality.forecast import (
    NORMALIZED_NOISE_METRIC,
    PINNED_STACK_METRICS,
    recommended_additional_frames_status_from_checks,
)
from wayfindinglib.models.planning.depth_goal import (
    DepthGoalState,
    DepthGoalStatus,
    DepthKind,
    DepthLevel,
    DepthLevelOption,
    TargetDepthGoal,
)
from wayfindinglib.models.planning.target_report import RecommendedAdditionalFramesStatus
from wayfindinglib.models.quality.statements import StatementKind

LIBRARY_STACK_EXCLUDED_MARKERS = ("#group-", "/groups/")
"""Parts of a stack id that mark an exposure-group stack."""

FIX_FIRST_ADVICE = {
    "retake_flats": "Retake flats first.",
}
"""The advice a quality statement adds to "more frames will not help"."""

FIXED_PATTERN_CAUSE_SENTENCES = {
    FixedPatternCause.FLATS: "The flats no longer match the optics; new flats should help.",
    FixedPatternCause.NEAR_TARGET: (
        "Faint light from the object itself fills the sky around it, so the noise there won't average away."
    ),
    FixedPatternCause.BANDING: "The frames carry a fixed stripe pattern.",
    FixedPatternCause.GRADIENT: "The sky brightness has a slope across the frame that stays in every frame.",
    FixedPatternCause.CALIBRATION_MASTERS: "Noise in the calibration frames is the same in every frame.",
    FixedPatternCause.SKY_STRUCTURE: (
        "The sky has fixed structure, likely faint light or a slope in the background."
    ),
    FixedPatternCause.UNPLACED: "The noise has a fixed part we can't place yet.",
}
"""One plain sentence for each cause of fixed-pattern noise (row WAY-D94).

The words are for a novice, so they use no statistics terms. The cause itself
is picked by `fixed_pattern_cause`, in a fixed order."""

CAUSE_STATEMENT_RULES = ("more_frames_will_not_help", "more_frames")
"""The statements that carry the cause code, most direct first."""


@dataclass(frozen=True)
class DepthReading:
    """The depth of a target's newest library stack, or why there is none.

    Parameters
    ----------
    value : `float` or `None`
        The depth, or ``None`` when there is none.
    stacked : `bool`
        Whether the target has any library stack row of the noise metric.
    reason : `str`
        Why there is no value (empty with a value).
    """

    value: float | None
    stacked: bool
    reason: str = ""


def coerce_level(level: DepthLevel | str) -> DepthLevel:
    """Turn a level or its text into a `DepthLevel`.

    Parameters
    ----------
    level : `DepthLevel` or `str`
        The level, or its value ("quick_look", "good", "deep").

    Returns
    -------
    level : `DepthLevel`
        The level.

    Raises
    ------
    InvalidArgumentError
        If the text is not a level.
    """
    try:
        return DepthLevel(level)
    except ValueError:
        choices = ", ".join(item.value for item in LEVEL_ORDER)
        raise InvalidArgumentError(f"The depth level must be one of: {choices}. Got {level!r}.") from None


def read_chosen_level(butler: Any, target_id: str) -> DepthLevel:
    """Read the level a person chose for a target.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    target_id : `str`
        The target.

    Returns
    -------
    level : `DepthLevel`
        The stored level, or Standard when none was stored.
    """
    record = butler.get("target_depth_goal", {"id": target_id})
    return DEFAULT_DEPTH_LEVEL if record is None else record.level


def store_chosen_level(butler: Any, target_id: str, level: DepthLevel) -> None:
    """Keep the level a person chose for a target.

    Parameters
    ----------
    butler : `DiskButler`
        The storage layer.
    target_id : `str`
        The target.
    level : `DepthLevel`
        The level to keep.
    """
    butler.put(TargetDepthGoal(id=target_id, level=level), "target_depth_goal", {"id": target_id})


def read_target_depth(astrometrics: Any, target_id: str, kind: DepthKind) -> DepthReading:
    """Read the depth of a target's newest library stack.

    Only a row of the pinned definition version counts. Exposure-group stacks
    and subset stacks are not the library stack and are left out.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle (``quality.query``).
    target_id : `str`
        The target.
    kind : `DepthKind`
        Which depth to read.

    Returns
    -------
    reading : `DepthReading`
        The newest usable depth, or the reason there is none.
    """
    metric = DEPTH_METRICS[kind]
    pinned = PINNED_STACK_METRICS[metric]

    def library_rows(name: str) -> list[Any]:
        """Query the newest library-stack rows of one metric.

        Parameters
        ----------
        name : `str`
            The metric name.

        Returns
        -------
        rows : `list`
            The rows of the library stack, without group stacks.
        """
        found = astrometrics.quality.query(
            metric=name,
            level=SubjectLevel.STACK.value,
            target_id=target_id,
            library_only=True,
            latest_only=True,
        )
        return [
            row
            for row in found
            if not any(marker in row.subject.subject_id for marker in LIBRARY_STACK_EXCLUDED_MARKERS)
        ]

    try:
        rows = library_rows(metric)
        stacked = bool(rows) or bool(library_rows(NORMALIZED_NOISE_METRIC))
    except (AstrometricsError, OSError) as error:
        return DepthReading(None, False, f"The depth could not be read: {error}")
    if not stacked:
        return DepthReading(None, False, "The target has no library stack.")
    usable = [row for row in rows if row.value is not None and row.definition_version == pinned]
    if usable:
        newest = max(usable, key=lambda row: row.measured_at)
        return DepthReading(float(newest.value), True)
    wrong_version = [row for row in rows if row.definition_version != pinned]
    if wrong_version:
        return DepthReading(
            None, True, f"The stored depth is an older definition (version {pinned} is needed). Restack."
        )
    reason = next((row.reason for row in rows if row.reason), "")
    return DepthReading(None, True, reason or "The depth of the newest stack was not measured.")


def read_depth_kind(astrometrics: Any, target_id: str) -> tuple[DepthKind | None, str]:
    """Say which depth a library target is judged by.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle, for the target's stored object type.
    target_id : `str`
        The target.

    Returns
    -------
    kind, reason : `tuple` [`DepthKind` or `None`, `str`]
        See `depth_kind_of_target`. A target the library does not hold has no
        stored type, so it gets the unknown-type default.
    """
    target = astrometrics.targets.get(target_id)
    return depth_kind_of_target(target_id, None if target is None else target.simbad_object_type)


def depth_goal_met(astrometrics: Any, butler: Any, target_id: str) -> bool | None:
    """Say whether a target's image has reached its chosen level.

    Used by the suggestion refresh. It reads no forecast.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The storage layer, for the chosen level.
    target_id : `str`
        The target.

    Returns
    -------
    met : `bool` or `None`
        True when the depth meets the chosen level, False when it does not,
        and ``None`` when the target has no depth kind, cannot be read or has
        no depth (the older noise goal then applies).
    """
    try:
        kind, _reason = read_depth_kind(astrometrics, target_id)
    except AstrometricsError, OSError, ValueError:
        # The target cannot be read now; the older noise goal applies.
        return None
    if kind is None:
        return None
    reading = read_target_depth(astrometrics, target_id, kind)
    if reading.value is None:
        return None
    return reading.value >= goal_depth(kind, read_chosen_level(butler, target_id))


def _options(kind: DepthKind | None) -> list[DepthLevelOption]:
    """List every level with its depth for a kind.

    Parameters
    ----------
    kind : `DepthKind` or `None`
        The target's depth kind.

    Returns
    -------
    options : `list` [`DepthLevelOption`]
        One option per level.
    """
    return [
        DepthLevelOption(
            level=level,
            label=LEVEL_LABELS[level],
            goal_depth=None if kind is None else goal_depth(kind, level),
        )
        for level in LEVEL_ORDER
    ]


def reason_frames_will_not_help(statements: list[Any]) -> str:
    """Pick the one plain sentence that says why more frames will not help.

    The same statements always give the same sentence. The cause code comes
    from the "will not help" statement first, then from the "take N more"
    statement. A report with no code gives the "can't place yet" sentence.

    Parameters
    ----------
    statements : `list` [`TargetStatement`]
        The statements of a report.

    Returns
    -------
    sentence : `str`
        The sentence for the cause.
    """
    for rule in CAUSE_STATEMENT_RULES:
        for statement in statements:
            code = statement.values.get("fixed_pattern_cause") if statement.rule == rule else None
            if code is not None:
                try:
                    return FIXED_PATTERN_CAUSE_SENTENCES[FixedPatternCause(code)]
                except ValueError:
                    # An unknown code is treated as no code.
                    continue
    return FIXED_PATTERN_CAUSE_SENTENCES[FixedPatternCause.UNPLACED]


def _time_to_goal(
    report: Any,
) -> tuple[
    DepthGoalState, float | None, int | None, float | None, str, RecommendedAdditionalFramesStatus, str
]:
    """Read the time to the goal from a report aimed at the goal.

    The first "take N more" recommendation in the report's priority order is
    used. A target with several capture settings is forecast per setting; the
    report puts the main one first.

    Parameters
    ----------
    report : `TargetDatasetReport`
        A report made with the noise ratio of the goal.

    Returns
    -------
    state, seconds, frames, exposure, advice, forecast_status, reason : `tuple`
        ``below_goal`` with the time and frames when there is a forecast,
        ``will_not_help`` with advice when frames will not reach the goal,
        and ``below_goal`` with no time and a reason otherwise. The advice is
        the plain reason (`reason_frames_will_not_help`), then the
        advice naming what to fix first, if any.
    """
    forecast_status = recommended_additional_frames_status_from_checks(report.forecast_checks)
    names = {
        statement.rule for statement in report.statements if statement.kind != StatementKind.INSUFFICIENT_DATA
    }
    advice = next((text for rule, text in FIX_FIRST_ADVICE.items() if rule in names), "")
    advice = f"{reason_frames_will_not_help(report.statements)} {advice}".strip()
    if "more_frames_will_not_help" in names:
        return DepthGoalState.WILL_NOT_HELP, None, None, None, advice, forecast_status, ""
    for statement in report.statements:
        if statement.rule != "more_frames":
            continue
        if statement.kind != StatementKind.RECOMMENDATION:
            return DepthGoalState.BELOW_GOAL, None, None, None, "", forecast_status, statement.reason
        if statement.values.get("practical_limit_applied"):
            return DepthGoalState.WILL_NOT_HELP, None, None, None, advice, forecast_status, ""
        action = statement.proposed_action
        effect = statement.expected_effect
        if action is None or effect is None or effect.wall_clock_seconds is None:
            break
        return (
            DepthGoalState.BELOW_GOAL,
            float(effect.wall_clock_seconds),
            action.frame_count,
            action.exposure_seconds,
            "",
            forecast_status,
            "",
        )
    return (
        DepthGoalState.BELOW_GOAL,
        None,
        None,
        None,
        "",
        forecast_status,
        "The time to reach the goal could not be forecast.",
    )


def build_depth_goal_status(
    target_id: str,
    *,
    astrometrics: Any,
    butler: Any,
    report_for_noise_ratio: Callable[[float], Any],
) -> DepthGoalStatus:
    """Build a target's depth goal status.

    A target of unknown object type is judged by the point-source depth, and
    the status reason then says so.

    Parameters
    ----------
    target_id : `str`
        The target.
    astrometrics : `Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The storage layer, for the chosen level.
    report_for_noise_ratio : `Callable`
        Builds the target report aimed at a noise ratio. Called only when the
        image is below its goal and the forecast is needed.

    Returns
    -------
    status : `DepthGoalStatus`
        The status, with the sentence for a novice in ``message``.
    """
    status = _build_status(
        target_id, astrometrics=astrometrics, butler=butler, report_for_noise_ratio=report_for_noise_ratio
    )
    _kind, kind_reason = read_depth_kind(astrometrics, target_id)
    if kind_reason and kind_reason not in status.reason:
        status = status.model_copy(update={"reason": f"{status.reason} {kind_reason}".strip()})
    return status


def _build_status(
    target_id: str,
    *,
    astrometrics: Any,
    butler: Any,
    report_for_noise_ratio: Callable[[float], Any],
) -> DepthGoalStatus:
    """Build a target's depth goal status, without the unknown-type note.

    Parameters
    ----------
    target_id : `str`
        The target.
    astrometrics : `Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The storage layer, for the chosen level.
    report_for_noise_ratio : `Callable`
        Builds the target report aimed at a noise ratio. Called only when the
        image is below its goal and the forecast is needed.

    Returns
    -------
    status : `DepthGoalStatus`
        The status, with the sentence for a novice in ``message``.
    """
    level = read_chosen_level(butler, target_id)
    kind, kind_reason = read_depth_kind(astrometrics, target_id)
    base: dict[str, Any] = {
        "target_id": target_id,
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "options": _options(kind),
    }
    if kind is None:
        return DepthGoalStatus(
            **base,
            state=DepthGoalState.NO_DEPTH_GOAL,
            message="No depth goal for this object.",
            reason=kind_reason,
        )
    base.update(depth_kind=kind, depth_unit=DEPTH_UNITS[kind], goal_depth=goal_depth(kind, level))
    reading = read_target_depth(astrometrics, target_id, kind)
    if reading.value is None:
        if not reading.stacked:
            return DepthGoalStatus(
                **base,
                state=DepthGoalState.NOT_STACKED,
                message="Stack the frames to see the depth.",
                reason=reading.reason,
            )
        return DepthGoalStatus(
            **base,
            state=DepthGoalState.DEPTH_NOT_MEASURED,
            message="The depth of this image has not been measured yet. Restack the target to measure it.",
            reason=reading.reason,
        )
    reached = level_reached(kind, reading.value)
    base.update(current_depth=reading.value, reached_level=reached, reached_level_label=level_label(reached))
    if reading.value >= base["goal_depth"]:
        return DepthGoalStatus(
            **base,
            state=DepthGoalState.REACHED,
            message="Integration goal reached.",
        )
    ratio = noise_ratio_for_depth_gain(base["goal_depth"], reading.value)
    state, seconds, frames, exposure, advice, forecast_status, reason = _time_to_goal(
        report_for_noise_ratio(ratio)
    )
    base.update(forecast_status=forecast_status)
    if state == DepthGoalState.WILL_NOT_HELP:
        message = f"More integration won't help much here. {advice}".strip()
        return DepthGoalStatus(**base, state=state, message=message)
    if forecast_status == RecommendedAdditionalFramesStatus.NOT_CHECKED:
        return DepthGoalStatus(
            **base,
            state=state,
            message=("Image this target on another night to get a time estimate."),
            reason=reason,
        )
    if seconds is None:
        return DepthGoalStatus(
            **base,
            state=state,
            message="The time needed could not be forecast.",
            reason=reason,
        )
    provisional = forecast_status == RecommendedAdditionalFramesStatus.PROVISIONAL
    suffix = " (provisional)" if provisional else ""
    return DepthGoalStatus(
        **base,
        state=state,
        seconds_to_goal=seconds,
        frames_to_goal=frames,
        exposure_seconds=exposure,
        provisional=provisional,
        message=f"About {time_text(seconds)} more integration needed{suffix}.",
    )
