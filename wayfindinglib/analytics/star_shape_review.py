"""Purpose: Turn stored star-shape measurements into plain verdicts.

Description: `astrometricslib` measures how stretched the stars of each frame
are (`frame.star_elongation`), which way (`frame.star_elongation_angle_deg`),
how well the stars agree on that way (`frame.star_elongation_coherence`, 0
to 1) and how wide they are at
the center and in the four corners of the field. This module reads those
stored rows and says what they suggest:

* **Roundness**: round or elongated. The frames are judged against the
  equipment's own history limit `night_star_roundness_low_limit` (register
  entry WAY-42), the limit the night review uses. A stack's own numbers are
  judged against a stack baseline: the low roundness limit of the library's
  other stacks (`stack_roundness_low_limit`), because a stack averages
  many frames and is rounder than a typical frame.
* **Direction**: when the stars are elongated, a high coherence means the
  stars share one direction, which points at tracking, guiding, wind or
  flexure. A low coherence points at seeing or focus.
* **Set-aside frames**: the frames the stacker's quarantine set aside as
  trailed (`astrometrics.quality.set_aside_frames`). The review does not
  judge the frames again; the quarantine owns that rule.
* **Corners**: the corner widths compared with the center width. They are
  reported only. The corner measurements are exploratory (AST-18), so no tilt
  or curvature verdict is made.

The review also gives the frames' typical elongation (their median), so the
screen does not work it out again.

The metric names are constants below, in one place. A stack may have its own
star-shape rows; when it has none, the median of the frames is used.
`judge_star_shape` is pure: it takes plain rows and limits, so it is easy to
test. `read_star_shape_rows` and `read_stack_roundness_low_limit` read the
stored rows.
"""

import math
import statistics
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.parameters import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    resolve_parameter,
)
from astrometricslib.reports import MeasurementStatus, observing_night_id
from wayfindinglib.analytics.performance_envelope import (
    BASELINE_SPREAD_MULTIPLIER,
    MINIMUM_BASELINE_SESSIONS,
    robust_median_and_spread,
)
from wayfindinglib.models.session.star_shape_review import (
    FrameStarShape,
    ShapeVerdict,
    StandoutFrame,
    StarShapeReview,
)

__all__ = [
    "judge_star_shape",
    "read_stack_roundness_low_limit",
    "read_star_shape_rows",
    "stack_roundness_low_limit",
]

FRAME_ELONGATION_METRIC = "frame.star_elongation"
"""Long axis over short axis of a frame's stars, with its pinned version."""

FRAME_ANGLE_METRIC = "frame.star_elongation_angle_deg"
"""Direction of the long axis of a frame's stars."""

FRAME_COHERENCE_METRIC = "frame.star_elongation_coherence"
"""How well a frame's stars agree on the direction, 0 to 1."""

FRAME_CENTER_WIDTH_METRIC = "frame.star_width_center_px"
"""Median star width at the center of a frame, in pixels (AST-18)."""

FRAME_CORNER_WIDTH_METRICS = {
    "xlow_ylow": "frame.star_width_corner_xlow_ylow_px",
    "xhigh_ylow": "frame.star_width_corner_xhigh_ylow_px",
    "xlow_yhigh": "frame.star_width_corner_xlow_yhigh_px",
    "xhigh_yhigh": "frame.star_width_corner_xhigh_yhigh_px",
}
"""Median star width in each corner of a frame, in pixels (AST-18)."""

STACK_ELONGATION_METRIC = "stacking.star_elongation"
"""The same measurement on a stack. Optional: a stack may not have it yet."""

STACK_ANGLE_METRIC = "stacking.star_elongation_angle_deg"
"""The stack's direction of the long axis. Optional."""

STACK_COHERENCE_METRIC = "stacking.star_elongation_coherence"
"""How well the stack's stars agree on the direction. Optional."""

STACK_CENTER_WIDTH_METRIC = "stacking.star_width_center_px"
"""The stack's center width, in pixels. Optional."""

STACK_CORNER_WIDTH_METRICS = {
    region: metric.replace("frame.", "stacking.") for region, metric in FRAME_CORNER_WIDTH_METRICS.items()
}
"""The stack's corner widths, in pixels. Optional."""

PINNED_FRAME_SHAPE_METRICS = {
    FRAME_ELONGATION_METRIC: 1,
    FRAME_ANGLE_METRIC: 2,
    FRAME_COHERENCE_METRIC: 1,
    FRAME_CENTER_WIDTH_METRIC: 1,
    **dict.fromkeys(FRAME_CORNER_WIDTH_METRICS.values(), 1),
}
"""The frame metrics read here, and the definition version of each.

A stored row of another version is not used. A test fails when the registry
moves past a version, so a change of definition is reviewed here.
"""

ROUNDNESS_LIMIT_NAME = "night_star_roundness_low_limit"
"""The envelope limit that separates round from elongated frames (WAY-42)."""

TRAILED_KIND = "trailed"
"""The kind the quarantine gives a frame it set aside for stretched stars."""

SHARED_DIRECTION_COHERENCE = TunedParameter(
    name="wayfinding.shared_direction_coherence",
    default=0.5,
    unit="coherence, 0 to 1",
    description="Lowest direction coherence at which elongated stars count as sharing one direction.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="WAY-D148",
)
"""Lowest direction coherence at which elongated stars share one direction.

Coherence is the length of the mean stretch vector divided by the mean stretch
of the stars. For N stars pointing every which way it is near 0.9 over the
square root of N: about 0.16 for 30 stars. Half is three times that, so noise
alone rarely reaches it, while a real shared stretch gives values near 1.
Design estimate, register row WAY-D148. It is not yet measured on real frames.
"""


def read_star_shape_rows(
    astrometrics: Any,
    target: Any,
    night: str | None = None,
    frame_paths: Sequence[str] | None = None,
) -> tuple[list[FrameStarShape], FrameStarShape | None, list[StandoutFrame]]:
    """Read the stored star-shape rows of a target's light frames and stack.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface (``Astrometrics``).
    target : `Any`
        The resolved target.
    night : `str`, optional
        Only frames of this observing night (such as ``"2026-09-24"``).
    frame_paths : `Sequence` [`str`], optional
        Only these frames.

    Returns
    -------
    frames : `list` [`FrameStarShape`]
        Each light frame with a stored elongation, oldest first.
    stack : `FrameStarShape` or `None`
        The target's library stack, when it has stored star-shape rows. It is
        left out when a night or a frame list narrows the question, because a
        stack covers all of the target's frames.
    set_aside : `list` [`StandoutFrame`]
        The frames the quarantine set aside as trailed, oldest first, with
        their stored elongation when there is one.
    """
    wanted = None if frame_paths is None else set(frame_paths)
    subjects = {}
    for frame in target.frames:
        if not str(frame.role).upper().endswith("LIGHT") or frame.timestamp is None:
            continue
        if wanted is not None and frame.path not in wanted:
            continue
        if night is not None and observing_night_id(frame.timestamp) != night:
            continue
        subjects[target.frame_subject_id(frame)] = frame
    values: dict[str, dict[str, Any]] = {subject: {} for subject in subjects}
    elongation_rows: dict[str, Any] = {}
    for metric, version in PINNED_FRAME_SHAPE_METRICS.items():
        for row in _query_rows(astrometrics, metric, "frame", target.id):
            if row.definition_version != version:
                continue
            if metric == FRAME_ELONGATION_METRIC:
                elongation_rows[row.subject.subject_id] = row
            if row.subject.subject_id in values:
                values[row.subject.subject_id][metric] = row
    frames = []
    for subject, frame in subjects.items():
        elongation_row = values[subject].get(FRAME_ELONGATION_METRIC)
        if elongation_row is None:
            continue
        frames.append(_shape_from_rows(frame.path, float(frame.timestamp), values[subject], _frame_names()))
    frames.sort(key=lambda shape: shape.timestamp or 0.0)
    stack = None
    if night is None and wanted is None:
        stack = _read_stack(astrometrics, target.id)
    set_aside = _read_set_aside_trailed(astrometrics, target, elongation_rows, night, wanted)
    return frames, stack, set_aside


def _read_set_aside_trailed(
    astrometrics: Any,
    target: Any,
    elongation_rows: dict[str, Any],
    night: str | None,
    wanted: set[str] | None,
) -> list[StandoutFrame]:
    """List the frames the quarantine set aside for stretched stars.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface.
    target : `Any`
        The resolved target.
    elongation_rows : `dict` [`str`, `Any`]
        The stored elongation rows of the target's frames, by subject id.
    night : `str` or `None`
        Only frames of this observing night.
    wanted : `set` [`str`] or `None`
        Only these frames (by their path before they were moved, or now).

    Returns
    -------
    set_aside : `list` [`StandoutFrame`]
        Oldest first; a frame with no capture time last.
    """
    found = []
    for record in astrometrics.quality.set_aside_frames(target):
        if record.kind != TRAILED_KIND:
            continue
        path = record.original_path or record.path
        if wanted is not None and path not in wanted and record.path not in wanted:
            continue
        timestamp = _capture_timestamp(record.date)
        if night is not None and (timestamp is None or observing_night_id(timestamp) != night):
            continue
        row = elongation_rows.get(record.subject_id) if record.subject_id else None
        found.append(
            StandoutFrame(
                path=path,
                timestamp=timestamp,
                elongation=None if row is None else float(row.value),
                reason=record.reason,
            )
        )
    return sorted(found, key=lambda item: (item.timestamp is None, item.timestamp or 0.0))


def _capture_timestamp(date: str) -> float | None:
    """Turn a set-aside frame's capture time into seconds since 1970.

    Parameters
    ----------
    date : `str`
        ``"YYYY-MM-DD HH:MM:SS"`` in UTC, as the set-aside listing gives it,
        or empty.

    Returns
    -------
    timestamp : `float` or `None`
        `None` when the text is empty or not a time.
    """
    try:
        return datetime.strptime(date, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC).timestamp()
    except ValueError:
        return None


def read_stack_roundness_low_limit(astrometrics: Any, exclude_subject: str | None = None) -> float | None:
    """Work out the stack baseline from the library's stored stacks.

    Parameters
    ----------
    astrometrics : `Any`
        The science library's high-level interface.
    exclude_subject : `str`, optional
        The stack being judged, which must not set its own limit.

    Returns
    -------
    limit : `float` or `None`
        See `stack_roundness_low_limit`. `None` when too few stacks have a
        stored elongation.
    """
    try:
        result = astrometrics.quality.query(metric=STACK_ELONGATION_METRIC, level="stack", latest_only=True)
    except InvalidArgumentError:
        return None
    elongations = [
        float(row.value)
        for row in result.rows
        if row.subject.channel is None
        and row.status != MeasurementStatus.NOT_MEASURED
        and row.value is not None
        and getattr(row.subject, "variant", None) is None
        and row.subject.subject_id != exclude_subject
    ]
    return stack_roundness_low_limit(elongations)


def stack_roundness_low_limit(elongations: Sequence[float]) -> float | None:
    """Find the lowest roundness that is usual for the library's stacks.

    The rule is the one the equipment history uses for its limits (WAY-42):
    roundness (one over the elongation) varies by factors, so the median and
    the robust spread are taken on its logarithm, and the limit is the
    median times ``exp(-BASELINE_SPREAD_MULTIPLIER x spread)``.

    Parameters
    ----------
    elongations : `Sequence` [`float`]
        The stored elongations of other stacks.

    Returns
    -------
    limit : `float` or `None`
        The lowest usual roundness, or `None` when fewer than
        `MINIMUM_BASELINE_SESSIONS` stacks have an elongation of 1 or more.
    """
    roundness = [1.0 / value for value in elongations if math.isfinite(value) and value >= 1.0]
    if len(roundness) < int(resolve_parameter(MINIMUM_BASELINE_SESSIONS).value):
        return None
    log_median, log_spread = robust_median_and_spread([math.log(value) for value in roundness])
    return math.exp(log_median - float(resolve_parameter(BASELINE_SPREAD_MULTIPLIER).value) * log_spread)


def _frame_names() -> tuple[str, str, str, str, dict[str, str]]:
    """Name the frame metrics in the order `_shape_from_rows` reads them.

    Returns
    -------
    names : `tuple`
        The elongation, angle, coherence and center-width metrics, and the
        corner metrics.
    """
    return (
        FRAME_ELONGATION_METRIC,
        FRAME_ANGLE_METRIC,
        FRAME_COHERENCE_METRIC,
        FRAME_CENTER_WIDTH_METRIC,
        FRAME_CORNER_WIDTH_METRICS,
    )


def _query_rows(astrometrics: Any, metric: str, level: str, target_id: str) -> list[Any]:
    """Read the usable stored rows of one metric.

    Returns
    -------
    rows : `list`
        Newest row of each subject that has a value and is not a color
        channel. Empty when the metric is not registered (yet).
    """
    try:
        result = astrometrics.quality.query(metric=metric, level=level, target_id=target_id, latest_only=True)
    except InvalidArgumentError:
        return []
    return [
        row
        for row in result.rows
        if row.subject.channel is None
        and row.status != MeasurementStatus.NOT_MEASURED
        and row.value is not None
        and getattr(row.subject, "variant", None) is None
    ]


def _read_stack(astrometrics: Any, target_id: str) -> FrameStarShape | None:
    """Read the target's library stack star-shape rows, if it has any.

    Returns
    -------
    stack : `FrameStarShape` or `None`
        The shape of the stack with the newest elongation row, or `None`.
    """
    elongation_rows = _query_rows(astrometrics, STACK_ELONGATION_METRIC, "stack", target_id)
    if not elongation_rows:
        return None
    newest = max(elongation_rows, key=lambda row: row.measured_at)
    subject = newest.subject.subject_id
    values: dict[str, Any] = {STACK_ELONGATION_METRIC: newest}
    for metric in (
        STACK_ANGLE_METRIC,
        STACK_COHERENCE_METRIC,
        STACK_CENTER_WIDTH_METRIC,
        *STACK_CORNER_WIDTH_METRICS.values(),
    ):
        for row in _query_rows(astrometrics, metric, "stack", target_id):
            if row.subject.subject_id == subject:
                values[metric] = row
    names = (
        STACK_ELONGATION_METRIC,
        STACK_ANGLE_METRIC,
        STACK_COHERENCE_METRIC,
        STACK_CENTER_WIDTH_METRIC,
        STACK_CORNER_WIDTH_METRICS,
    )
    return _shape_from_rows(subject, None, values, names)


def _shape_from_rows(
    path: str, timestamp: float | None, rows: dict[str, Any], names: tuple[str, str, str, str, dict[str, str]]
) -> FrameStarShape:
    """Gather one subject's stored rows into a `FrameStarShape`.

    Returns
    -------
    shape : `FrameStarShape`
        The subject's numbers; a number with no stored row is `None`.
    """
    elongation_name, angle_name, coherence_name, center_name, corner_names = names
    elongation_row, angle_row, coherence_row, center_row = (
        rows.get(elongation_name),
        rows.get(angle_name),
        rows.get(coherence_name),
        rows.get(center_name),
    )
    return FrameStarShape(
        path=path,
        timestamp=timestamp,
        elongation=None if elongation_row is None else float(elongation_row.value),
        angle_degrees=None if angle_row is None else float(angle_row.value),
        direction_coherence=None if coherence_row is None else float(coherence_row.value),
        center_width_px=None if center_row is None else float(center_row.value),
        corner_widths_px={
            region: float(rows[metric].value) for region, metric in corner_names.items() if metric in rows
        },
        elongation_row_id=None if elongation_row is None else elongation_row.row_id,
    )


def judge_star_shape(
    target_id: str,
    frames: Sequence[FrameStarShape],
    stack: FrameStarShape | None = None,
    roundness_low_limit: float | None = None,
    scope: str | None = None,
    set_aside: Sequence[StandoutFrame] = (),
    stack_roundness_low_limit: float | None = None,
) -> StarShapeReview:
    """Judge the star shapes of a target's frames and stack.

    Parameters
    ----------
    target_id : `str`
        The target.
    frames : `Sequence` [`FrameStarShape`]
        The frames' stored numbers. Frames with no elongation are ignored.
    stack : `FrameStarShape`, optional
        The stack's own numbers. When it has an elongation, it is used for
        the roundness, direction and corner verdicts; any number it lacks
        falls back to the median of the frames.
    roundness_low_limit : `float`, optional
        The equipment history's lowest roundness of a night's frames
        (WAY-42). Roundness is one over the elongation. Without it the frames
        are not called round or elongated, because there is no history to
        say what is normal.
    scope : `str`, optional
        The scope to name in the result. ``"stack"`` or ``"frames"`` is
        chosen when omitted.
    set_aside : `Sequence` [`StandoutFrame`], optional
        The frames the quarantine set aside as trailed (see
        `read_star_shape_rows`). They are reported as they are; nothing is
        judged again.
    stack_roundness_low_limit : `float`, optional
        The stack baseline (`stack_roundness_low_limit`). A stack's own
        numbers are judged against it, not against the frames' limit.

    Returns
    -------
    review : `StarShapeReview`
        The verdicts, the set-aside frames, the frames' typical elongation
        and the per-frame series. With no measured elongation the verdicts
        say "not measured yet"; that is not an error.
    """
    measured = [frame for frame in frames if frame.elongation is not None]
    notes: list[str] = []
    use_stack = stack is not None and stack.elongation is not None
    if not measured and not use_stack:
        return _not_measured(target_id, scope or "frames", set_aside)
    set_aside_paths = {item.path for item in set_aside}
    measured = [frame.model_copy(update={"standout": frame.path in set_aside_paths}) for frame in measured]
    typical = (
        statistics.median(float(frame.elongation) for frame in measured if frame.elongation is not None)
        if measured
        else None
    )
    elongation = stack.elongation if use_stack and stack is not None else typical
    limit = stack_roundness_low_limit if use_stack else roundness_low_limit
    source = stack if use_stack else None
    if scope is None:
        scope = "stack" if use_stack else "frames"
    if limit is None:
        notes.append(
            "The library has too few stacks with measured stars to say what roundness is normal for a stack."
            if use_stack
            else "The equipment has too few measured nights to say what roundness is normal."
        )
    return StarShapeReview(
        target_id=target_id,
        scope=scope,
        frame_count=len(measured),
        typical_elongation=typical,
        roundness=_roundness_verdict(float(elongation), limit, use_stack),
        direction=_direction_verdict(float(elongation), limit, source, measured),
        standout=_standout_verdict(set_aside, len(measured)),
        corners=_corner_verdict(source, measured),
        standout_frames=list(set_aside),
        series=list(measured),
        notes=notes,
    )


def _not_measured(target_id: str, scope: str, set_aside: Sequence[StandoutFrame]) -> StarShapeReview:
    """Build the review for a target with no stored star shapes.

    The frames the quarantine set aside are still listed.

    Returns
    -------
    review : `StarShapeReview`
        Every verdict says the shapes are not measured yet.
    """
    sentence = "Star shapes are not measured yet for these frames."
    return StarShapeReview(
        target_id=target_id,
        scope=scope,
        frame_count=0,
        roundness=ShapeVerdict(state="not_measured", sentence=sentence),
        direction=ShapeVerdict(state="not_measured", sentence=sentence),
        standout=_standout_verdict(set_aside, 0),
        corners=ShapeVerdict(state="not_measured", sentence=sentence),
        standout_frames=list(set_aside),
        notes=["Measure the frames' star shapes to get a review."],
    )


def _roundness_verdict(elongation: float, roundness_low_limit: float | None, is_stack: bool) -> ShapeVerdict:
    """Say whether the stars are round, against the history limit.

    A stack is compared with the library's other stacks, the frames with the
    equipment's nights.

    Returns
    -------
    verdict : `ShapeVerdict`
        ``round`` or ``elongated``, or ``no_history`` without a limit.
    """
    roundness = 1.0 / elongation if elongation >= 1.0 else None
    numbers: dict[str, float | None] = {
        "elongation": elongation,
        "roundness": roundness,
        "roundness_low_limit": roundness_low_limit,
    }
    if roundness is None:
        return ShapeVerdict(
            state="not_measured",
            sentence="The stored star stretch is below 1, so it cannot be used.",
            numbers=numbers,
        )
    usual = "your stacks" if is_stack else "your equipment"
    if roundness_low_limit is None:
        return ShapeVerdict(
            state="no_history",
            sentence=(
                f"Stars are stretched by {elongation:.2f} (1 is round), but there is not enough history "
                f"yet to say if that is normal for {usual}."
            ),
            numbers=numbers,
        )
    if roundness >= roundness_low_limit:
        return ShapeVerdict(
            state="round",
            sentence=f"Stars are round enough (stretch {elongation:.2f}), as usual for {usual}.",
            numbers=numbers,
        )
    return ShapeVerdict(
        state="elongated",
        sentence=f"Stars are stretched more than usual for {usual} (stretch {elongation:.2f}).",
        numbers=numbers,
    )


def _direction_verdict(
    elongation: float,
    roundness_low_limit: float | None,
    source: FrameStarShape | None,
    frames: Sequence[FrameStarShape],
) -> ShapeVerdict:
    """Say whether the stretch points one way, and what that suggests.

    Returns
    -------
    verdict : `ShapeVerdict`
        ``shared`` or ``mixed`` for elongated stars; ``not_needed`` for round
        stars; ``no_history`` or ``not_measured`` when it cannot be said.
    """
    angle, coherence = _direction(source, frames)
    cut = float(resolve_parameter(SHARED_DIRECTION_COHERENCE).value)
    numbers: dict[str, float | None] = {
        "angle_degrees": angle,
        "coherence": coherence,
        "coherence_cut": cut,
    }
    if roundness_low_limit is None:
        return ShapeVerdict(
            state="no_history",
            sentence="The direction is not judged until the stars are known to be stretched.",
            numbers=numbers,
        )
    if 1.0 / elongation >= roundness_low_limit:
        return ShapeVerdict(
            state="not_needed",
            sentence="The stars are round, so their direction does not matter.",
            numbers=numbers,
        )
    if coherence is None:
        return ShapeVerdict(
            state="not_measured",
            sentence="The direction of the stretch is not measured yet.",
            numbers=numbers,
        )
    if coherence >= cut:
        return ShapeVerdict(
            state="shared",
            sentence=(
                f"The stars are stretched the same way (about {angle:.0f} degrees). That points to tracking, "
                "guiding, wind or flexing, not to seeing."
            ),
            numbers=numbers,
        )
    return ShapeVerdict(
        state="mixed",
        sentence=(
            "The stars are stretched in different directions. That points to seeing or "
            "focus, not to tracking."
        ),
        numbers=numbers,
    )


def _direction(
    source: FrameStarShape | None, frames: Sequence[FrameStarShape]
) -> tuple[float | None, float | None]:
    """Take the direction and coherence from the stack, or else the frames.

    The frames' angles are averaged as axes (a direction and the one 180
    degrees away are the same), and their coherences by median.

    Returns
    -------
    angle_degrees, coherence : `float` or `None`
        The direction in degrees from 0 up to 180, and the coherence.
    """
    if source is not None and source.angle_degrees is not None:
        return source.angle_degrees, source.direction_coherence
    angles = [frame.angle_degrees for frame in frames if frame.angle_degrees is not None]
    coherences = [frame.direction_coherence for frame in frames if frame.direction_coherence is not None]
    angle = None
    if angles:
        doubled = [math.radians(2.0 * value) for value in angles]
        mean = math.atan2(sum(math.sin(v) for v in doubled), sum(math.cos(v) for v in doubled)) / 2.0
        angle = math.degrees(mean) % 180.0
    return angle, (statistics.median(coherences) if coherences else None)


def _standout_verdict(set_aside: Sequence[StandoutFrame], measured_count: int) -> ShapeVerdict:
    """Say whether the quarantine set frames aside for stretched stars.

    Returns
    -------
    verdict : `ShapeVerdict`
        ``found`` or ``none_found``.
    """
    numbers: dict[str, float | None] = {
        "frames_checked": float(measured_count),
        "standouts": float(len(set_aside)),
    }
    if not set_aside:
        return ShapeVerdict(
            state="none_found",
            sentence="No frame was set aside for stretched stars.",
            numbers=numbers,
        )
    plural = "frame was" if len(set_aside) == 1 else "frames were"
    return ShapeVerdict(
        state="found",
        sentence=(
            f"{len(set_aside)} {plural} set aside for stretched stars when the target was stacked. "
            "You can put a frame back if you disagree."
        ),
        numbers=numbers,
    )


def _corner_verdict(source: FrameStarShape | None, frames: Sequence[FrameStarShape]) -> ShapeVerdict:
    """Report the corner widths against the center width, without a verdict.

    Returns
    -------
    verdict : `ShapeVerdict`
        Always ``not_validated`` when the widths exist, else ``not_measured``.
    """
    center, corners = _widths(source, frames)
    ratios = {
        f"{region}_over_center": width / center if center else None for region, width in corners.items()
    }
    median_corner = statistics.median(corners.values()) if corners else None
    ratio = median_corner / center if center and median_corner is not None else None
    numbers: dict[str, float | None] = {
        "center_width_px": center,
        "corner_median_width_px": median_corner,
        "corner_over_center": ratio,
    }
    numbers.update(ratios)
    if ratio is None:
        return ShapeVerdict(
            state="not_measured",
            sentence="Center and corner star widths are not measured yet.",
            numbers=numbers,
        )
    return ShapeVerdict(
        state="not_validated",
        sentence=(
            f"Corner stars are {ratio:.2f} times as wide as center stars. This is not yet validated "
            "(AST-18), so it gives no verdict on tilt or curvature."
        ),
        numbers=numbers,
    )


def _widths(
    source: FrameStarShape | None, frames: Sequence[FrameStarShape]
) -> tuple[float | None, dict[str, float]]:
    """Take the center and corner widths from the stack, or else the frames.

    Returns
    -------
    center, corners : `float` or `None`, `dict` [`str`, `float`]
        The center width and each corner's width, in pixels (frame medians
        when the stack has none).
    """
    if source is not None and source.center_width_px is not None and source.corner_widths_px:
        return source.center_width_px, dict(source.corner_widths_px)
    centers = [frame.center_width_px for frame in frames if frame.center_width_px is not None]
    corners: dict[str, float] = {}
    for region in FRAME_CORNER_WIDTH_METRICS:
        widths = [frame.corner_widths_px[region] for frame in frames if region in frame.corner_widths_px]
        if widths:
            corners[region] = statistics.median(widths)
    return (statistics.median(centers) if centers else None), corners
