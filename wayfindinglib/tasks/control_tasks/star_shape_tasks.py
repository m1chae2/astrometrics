"""Purpose: Review the star shapes of a target against the equipment's history.

Description: Reads the stored star-shape measurements of a target's frames
(and its stack, when the stack has them) and the frames the stacker set aside
as trailed, works out the equipment's own roundness limit for frames
(register entry WAY-42) and the library's stack baseline, and hands them to
`analytics.star_shape_review.judge_star_shape`. Nothing here writes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from astrometricslib import Target
from astrometricslib.arguments import FrameChoice, Page
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib.analytics.star_shape_review import (
    ROUNDNESS_LIMIT_NAME,
    judge_star_shape,
    read_stack_roundness_low_limit,
    read_star_shape_rows,
)
from wayfindinglib.models.session.star_shape_review import StarShapeReview
from wayfindinglib.tasks.control_tasks import night_analysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext


def frame_paths_of(frames: FrameChoice | None) -> list[str] | None:
    """Turn a frame choice into the paths of its frames.

    Parameters
    ----------
    frames : `FrameChoice` or `None`
        A page or list of frame records, or a list of paths.

    Returns
    -------
    paths : `list` [`str`] or `None`
        The frames' paths, or `None` for no choice (every frame).

    Raises
    ------
    InvalidArgumentError
        If `frames` is a page cut short by its ``limit``.
    """
    if frames is None:
        return None
    if isinstance(frames, Page) and not frames.is_complete:
        raise InvalidArgumentError("frames is a page cut short by limit; ask for every matching frame.")
    return [str(getattr(frame, "path", frame)) for frame in frames]


def get_star_shape_review(
    context: ControlContext,
    target: Target,
    night: str | None = None,
    frames: FrameChoice | None = None,
) -> StarShapeReview:
    """Judge how round the stars of a target are, and what the stretch means.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the recorded sessions.
    target : `Target`
        The target.
    night : `str`, optional
        Judge only this observing night's frames. The roundness limit then
        uses only earlier nights, so a bad night cannot make itself normal.
    frames : `FrameChoice`, optional
        Judge only these frames.

    Returns
    -------
    review : `StarShapeReview`
        The verdicts, the set-aside frames, the typical elongation and the
        per-frame series.
    """
    frame_paths = frame_paths_of(frames)
    rows, stack, set_aside = read_star_shape_rows(context.astrometrics, target, night, frame_paths)
    limit = None
    if rows:
        contexts, runs = night_analysis.recorded_sessions(context)
        envelope = night_analysis.performance_envelope(context, None, night, contexts, runs, {})
        limit = None if envelope is None else envelope.value(ROUNDNESS_LIMIT_NAME)
    stack_limit = None
    if stack is not None and stack.elongation is not None:
        stack_limit = read_stack_roundness_low_limit(context.astrometrics, stack.path)
    scope = "frame_list" if frame_paths is not None else ("night" if night is not None else None)
    return judge_star_shape(
        target.id,
        rows,
        stack,
        limit,
        scope,
        set_aside=set_aside,
        stack_roundness_low_limit=stack_limit,
    )
