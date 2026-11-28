"""Purpose: Say how far to trust one star's science result.

Description: `judge_star_result` is the work behind
`review.targets.get_result(star)`
(use case UC5 in `Wayfinding_Quality_Use_Cases.md`). It reads the star's
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

from astrometricslib import StellarObject, Target
from astrometricslib.exceptions import NotFoundError
from wayfindinglib.analytics.result_quality.judge_results import order_statements
from wayfindinglib.analytics.result_quality.star_result_rules import judge_star_record
from wayfindinglib.models.quality.statements import TargetStatement


def judge_star_result(
    star: StellarObject, *, astrometrics: Any, target: Target | None = None
) -> list[TargetStatement]:
    """Judge the science results held for one star.

    Parameters
    ----------
    star : `StellarObject`
        The star whose results are judged. Its record is read again with
        its light curves, so a star read without them gives the same
        answer.
    astrometrics : `Astrometrics`
        The science library handle (keyword only). Its ``stars`` catalog is
        read; nothing is written.
    target : `Target`, optional
        The target the star was found in. A star that is recorded as
        belonging to other targets only is refused.

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
    record = astrometrics.stars.get(star.id, include=["light_curves"]) if star.id else star
    if record is None:
        raise NotFoundError(f"No star {star.id!r} in the catalog.", details={"object_id": star.id})
    if target is not None and record.target_ids and target.id not in record.target_ids:
        raise NotFoundError(
            f"Star {star.id!r} is not recorded for target {target.id!r}.",
            details={"object_id": star.id, "target_id": target.id, "target_ids": list(record.target_ids)},
        )
    statements = judge_star_record(record.id or star.id, record.spectroscopy, record.photometry)
    return order_statements(statements)


__all__ = ["judge_star_result"]
