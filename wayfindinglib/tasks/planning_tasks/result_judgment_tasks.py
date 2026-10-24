"""Purpose: Say how far to trust one star's science result.

Description: `judge_star_result` is the work behind
`ObservationPlanning.get_advisory(kind="result", target_id=..., object_id=...)`
(`QUALITY_METRICS_PLAN.md` Section 6.1, use case UC5). It reads the star's
record through the public root of the science library (`stars.get`), finds
which per-star results exist (a spectrum, a period search, a repeating-dip
search), and judges each with the rules of `star_result_rules`.

A per-star result has no row in the measurement store, so each statement cites
it by star, run and statistic (`StarResultReference`) instead of by row id. The
statements are the same shape as those of the target report, so the Astronomy
Manager shows them with the same statement view. They are worked out on each
request and never stored.

When the star has none of the results, the answer is one `insufficient_data`
statement that names what is missing. Planning code: this module never imports
Control.
"""

from typing import Any

from astrometricslib import NotFoundError
from wayfindinglib.analytics.result_quality.judge_results import order_statements
from wayfindinglib.analytics.result_quality.star_result_rules import judge_star_record
from wayfindinglib.models.quality.statements import TargetStatement


def judge_star_result(target_id: str, object_id: str, *, astrometrics: Any) -> list[TargetStatement]:
    """Judge the science results held for one star of a target.

    Parameters
    ----------
    target_id : `str`
        The target the star was found in. A star that is recorded as belonging
        to other targets only is refused.
    object_id : `str`
        The star's id in the stellar catalog.
    astrometrics : `Astrometrics`
        The science library handle (keyword only). Its ``stars`` catalog is
        read; nothing is written.

    Returns
    -------
    statements : `list` [`TargetStatement`]
        Warnings first, then the number behind each result that passed, then
        the notes about what cannot be judged yet. Never empty.

    Raises
    ------
    NotFoundError
        If the star is not in the catalog, or is recorded for other targets
        only.
    """
    star = astrometrics.stars.get(object_id)
    if star is None:
        raise NotFoundError(f"No star {object_id!r} in the catalog.", details={"object_id": object_id})
    if star.target_ids and target_id not in star.target_ids:
        raise NotFoundError(
            f"Star {object_id!r} is not recorded for target {target_id!r}.",
            details={"object_id": object_id, "target_id": target_id, "target_ids": list(star.target_ids)},
        )
    statements = judge_star_record(star.id or object_id, star.spectroscopy, star.photometry)
    return order_statements(statements)


__all__ = ["judge_star_result"]
