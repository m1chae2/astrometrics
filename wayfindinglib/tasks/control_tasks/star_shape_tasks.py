"""Purpose: Review the star shapes of a target against the equipment's history.

Description: Reads the stored star-shape measurements of a target's frames
(and its stack, when the stack has them) and the frames the stacker set aside
as trailed, works out the equipment's own roundness limit for frames
(register entry WAY-42) and the library's stack baseline, and hands them to
`analytics.star_shape_review.judge_star_shape`. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from astrometricslib import Target
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


def get_star_shape_review(
    context: ControlContext,
    target: str,
    night: str | None = None,
    frame_paths: Sequence[str] | None = None,
) -> StarShapeReview:
    """Judge how round the stars of a target are, and what the stretch means.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library and the recorded sessions.
    target : `str`
        The target's id or name.
    night : `str`, optional
        Judge only this observing night's frames. The roundness limit then
        uses only earlier nights, so a bad night cannot make itself normal.
    frame_paths : `Sequence` [`str`], optional
        Judge only these frames.

    Returns
    -------
    review : `StarShapeReview`
        The verdicts, the set-aside frames, the typical elongation and the
        per-frame series.
    """
    resolved = (
        target if isinstance(target, Target) else context.astrometrics.targets.get(target, required=True)
    )
    frames, stack, set_aside = read_star_shape_rows(context.astrometrics, resolved, night, frame_paths)
    limit = None
    if frames:
        contexts, runs = night_analysis.recorded_sessions(context)
        envelope = night_analysis.performance_envelope(context, None, night, contexts, runs, {})
        limit = None if envelope is None else envelope.value(ROUNDNESS_LIMIT_NAME)
    stack_limit = None
    if stack is not None and stack.elongation is not None:
        stack_limit = read_stack_roundness_low_limit(context.astrometrics, stack.path)
    scope = "frame_list" if frame_paths is not None else ("night" if night is not None else None)
    return judge_star_shape(
        resolved.id,
        frames,
        stack,
        limit,
        scope,
        set_aside=set_aside,
        stack_roundness_low_limit=stack_limit,
    )
