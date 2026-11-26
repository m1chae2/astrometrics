"""Purpose: Turn stored star-shape measurements into plain verdicts.

Description: `astrometricslib` measures how stretched the stars of each frame
are (`frame.star_elongation`), which way (`frame.star_elongation_angle_deg`),
how well the stars agree on that way (`frame.star_elongation_coherence`, 0
to 1) and how wide they are at
the center and in the four corners of the field. This module reads those
stored rows and says what they suggest:

* **Roundness**: round or elongated, judged against the equipment's own
  history limit `night_star_roundness_low_limit` (register entry WAY-42).
  The limit is the same one the night review uses.
* **Direction**: when the stars are elongated, a high coherence means the
  stars share one direction, which points at tracking, guiding, wind or
  flexure. A low coherence points at seeing or focus.
* **Standout frames**: frames whose elongation is far above the typical one,
  using the median plus a multiple of the robust spread. The multiple is the
  history convention `BASELINE_SPREAD_MULTIPLIER`.
* **Corners**: the corner widths compared with the center width. They are
  reported only. The corner measurements are exploratory (AST-18), so no tilt
  or curvature verdict is made.

The metric names are constants below, in one place. A stack may have its own
star-shape rows; when it has none, the median of the frames is used.
`judge_star_shape` is pure: it takes plain rows and a limit, so it is easy to
test. `read_star_shape_rows` reads the stored rows.
"""

import math
import statistics
from collections.abc import Sequence
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
    MAD_TO_SIGMA,
    MINIMUM_FRAMES_PER_NIGHT,
)
from wayfindinglib.models.session.star_shape_review import (
    FrameStarShape,
    ShapeVerdict,
    StandoutFrame,
    StarShapeReview,
)

__all__ = [
    "judge_star_shape",
    "read_star_shape_rows",
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
"""The envelope limit that separates round from elongated (WAY-42)."""

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
) -> tuple[list[FrameStarShape], FrameStarShape | None]:
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
    for metric, version in PINNED_FRAME_SHAPE_METRICS.items():
        for row in _query_rows(astrometrics, metric, "frame", target.id):
            if row.definition_version != version or row.subject.subject_id not in values:
                continue
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
    return frames, stack


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
        The equipment history's lowest roundness (WAY-42). Roundness is one
        over the elongation. Without it the stars are not called round or
        elongated, because there is no history to say what is normal.
    scope : `str`, optional
        The scope to name in the result. ``"stack"`` or ``"frames"`` is
        chosen when omitted.

    Returns
    -------
    review : `StarShapeReview`
        The verdicts, the standout frames and the per-frame series. With no
        measured elongation the verdicts say "not measured yet"; that is not
        an error.
    """
    measured = [frame for frame in frames if frame.elongation is not None]
    notes: list[str] = []
    use_stack = stack is not None and stack.elongation is not None
    if not measured and not use_stack:
        return _not_measured(target_id, scope or "frames")
    standouts = _find_standouts(measured)
    standout_paths = {item.path for item in standouts}
    measured = [frame.model_copy(update={"standout": frame.path in standout_paths}) for frame in measured]
    elongation = (
        stack.elongation
        if use_stack and stack is not None
        else statistics.median(frame.elongation for frame in measured if frame.elongation is not None)
    )
    source = stack if use_stack else None
    if scope is None:
        scope = "stack" if use_stack else "frames"
    if roundness_low_limit is None:
        notes.append("The equipment has too few measured nights to say what roundness is normal.")
    return StarShapeReview(
        target_id=target_id,
        scope=scope,
        frame_count=len(measured),
        roundness=_roundness_verdict(float(elongation), roundness_low_limit),
        direction=_direction_verdict(float(elongation), roundness_low_limit, source, measured),
        standout=_standout_verdict(standouts, len(measured)),
        corners=_corner_verdict(source, measured),
        standout_frames=standouts,
        series=list(measured),
        notes=notes,
    )


def _not_measured(target_id: str, scope: str) -> StarShapeReview:
    """Build the review for a target with no stored star shapes.

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
        standout=ShapeVerdict(state="not_measured", sentence=sentence),
        corners=ShapeVerdict(state="not_measured", sentence=sentence),
        notes=["Measure the frames' star shapes to get a review."],
    )


def _roundness_verdict(elongation: float, roundness_low_limit: float | None) -> ShapeVerdict:
    """Say whether the stars are round, against the history limit.

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
    if roundness_low_limit is None:
        return ShapeVerdict(
            state="no_history",
            sentence=(
                f"Stars are stretched by {elongation:.2f} (1 is round), but there is not enough history "
                "yet to say if that is normal for your equipment."
            ),
            numbers=numbers,
        )
    if roundness >= roundness_low_limit:
        return ShapeVerdict(
            state="round",
            sentence=f"Stars are round enough (stretch {elongation:.2f}), as usual for your equipment.",
            numbers=numbers,
        )
    return ShapeVerdict(
        state="elongated",
        sentence=f"Stars are stretched more than usual for your equipment (stretch {elongation:.2f}).",
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


def _find_standouts(frames: Sequence[FrameStarShape]) -> list[StandoutFrame]:
    """Find frames far above the typical elongation.

    A frame stands out when its elongation is above the median by more than
    `BASELINE_SPREAD_MULTIPLIER` robust spreads (1.4826 times the median
    absolute deviation). Fewer than `MINIMUM_FRAMES_PER_NIGHT` frames are
    too few to know the spread, and a spread of zero gives no standouts.

    Returns
    -------
    standouts : `list` [`StandoutFrame`]
        Most extreme first.
    """
    if len(frames) < resolve_parameter(MINIMUM_FRAMES_PER_NIGHT).value:
        return []
    values = [float(frame.elongation) for frame in frames if frame.elongation is not None]
    median = statistics.median(values)
    spread = MAD_TO_SIGMA * statistics.median(abs(value - median) for value in values)
    if spread <= 0.0:
        return []
    multiple = float(resolve_parameter(BASELINE_SPREAD_MULTIPLIER).value)
    found = [
        StandoutFrame(
            path=frame.path,
            timestamp=frame.timestamp,
            elongation=float(frame.elongation),
            spreads_above_typical=(float(frame.elongation) - median) / spread,
        )
        for frame in frames
        if frame.elongation is not None and (float(frame.elongation) - median) / spread > multiple
    ]
    return sorted(found, key=lambda item: item.spreads_above_typical, reverse=True)


def _standout_verdict(standouts: Sequence[StandoutFrame], measured_count: int) -> ShapeVerdict:
    """Say whether some frames stand out.

    Returns
    -------
    verdict : `ShapeVerdict`
        ``found`` or ``none_found``.
    """
    numbers: dict[str, float | None] = {
        "frames_checked": float(measured_count),
        "standouts": float(len(standouts)),
    }
    if not standouts:
        return ShapeVerdict(
            state="none_found",
            sentence="No frame has stars stretched far more than the others.",
            numbers=numbers,
        )
    plural = "frame has" if len(standouts) == 1 else "frames have"
    return ShapeVerdict(
        state="found",
        sentence=(
            f"{len(standouts)} {plural} stars stretched far more than the others. "
            "You may want to set them aside."
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
