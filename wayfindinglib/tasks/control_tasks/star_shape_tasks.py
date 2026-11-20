"""Purpose: Review the star shapes of a target against the equipment's history.

Description: Reads the stored star-shape measurements of a target's frames
(and its stack, when the stack has them), works out the equipment's own
roundness limit (register entry WAY-42) and hands both to
`analytics.star_shape_review.judge_star_shape`. Nothing here writes.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from wayfindinglib.analytics.star_shape_review import (
    ROUNDNESS_LIMIT_NAME,
    judge_star_shape,
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
        The verdicts and the per-frame series.
    """
    resolved = context.astrometrics.targets.resolve(target)
    frames, stack = read_star_shape_rows(context.astrometrics, resolved, night, frame_paths)
    limit = None
    if frames or stack is not None:
        contexts, runs = night_analysis.recorded_sessions(context)
        envelope = night_analysis.performance_envelope(context, None, night, contexts, runs, {})
        limit = None if envelope is None else envelope.value(ROUNDNESS_LIMIT_NAME)
    scope = "frame_list" if frame_paths is not None else ("night" if night is not None else None)
    return judge_star_shape(resolved.id, frames, stack, limit, scope)
