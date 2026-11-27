"""Purpose: Judge a target's data or one star's result, for two callers.

Description: `build_advisory` judges a target's data by kind. The planning
object needs the `"quality"` report when it refreshes its suggestions.
`review.targets` calls the two owners directly
(`build_target_dataset_report` and `judge_star_result`). The advice is
worked out on demand and never stored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from astrometricslib import Target
from astrometricslib.arguments import check_choice, reject_unused_arguments
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError

if TYPE_CHECKING:
    from wayfindinglib.models.planning.target_report import TargetDatasetReport
    from wayfindinglib.models.quality.statements import TargetStatement

__all__ = ["ADVISORY_ARGUMENTS", "AdvisoryKind", "build_advisory"]

AdvisoryKind = Literal["quality", "result"]
"""The kinds of advice `build_advisory` gives."""

ADVISORY_ARGUMENTS = {
    "quality": ("target",),
    "result": ("target", "object_id"),
}
"""The arguments each advisory kind uses."""


def build_advisory(
    kind: AdvisoryKind,
    target: str | Target,
    object_id: str | None = None,
    *,
    astrometrics: Any,
    butler: Any,
    config: Any,
) -> TargetDatasetReport | list[TargetStatement]:
    """Judge a target's data or one star's result.

    Parameters
    ----------
    kind : `str`
        ``"quality"`` for the target dataset report, or ``"result"`` for the
        statements about one star's result (needs `object_id`).
    target : `str` or `Target`
        The target, by id or as a `Target`.
    object_id : `str`, optional
        ``kind="result"`` only. The star whose result is judged.
    astrometrics : `astrometricslib.Astrometrics`
        The science library handle.
    butler : `DiskButler`
        The record store.
    config : `AppConfiguration`
        The application configuration.

    Returns
    -------
    advice : `TargetDatasetReport` or `list` [`TargetStatement`]
        The advice for the chosen kind.

    Raises
    ------
    InvalidArgumentError
        If `kind` is unknown, an argument the kind does not use is given,
        or a required argument is missing.
    NotFoundError
        If ``kind="result"`` names a star that is not in the catalog.
    """
    check_choice("kind", kind, tuple(ADVISORY_ARGUMENTS))
    reject_unused_arguments(kind, ADVISORY_ARGUMENTS, {"target": True, "object_id": object_id is not None})
    resolved = target if isinstance(target, Target) else astrometrics.targets.get(target, required=True)
    if kind == "quality":
        from wayfindinglib.tasks.planning_tasks.target_report_tasks import build_target_dataset_report

        return build_target_dataset_report(resolved, astrometrics=astrometrics, butler=butler, config=config)
    if object_id is None:
        raise InvalidArgumentError('kind="result" needs an object_id.')
    from wayfindinglib.tasks.planning_tasks.result_judgment_tasks import judge_star_result

    star = astrometrics.stars.get(object_id)
    if star is None:
        raise NotFoundError(f"No star {object_id!r} in the catalog.", details={"object_id": object_id})
    return judge_star_result(star, astrometrics=astrometrics, target=resolved)
