"""Corrects period-search verdicts for how many searches were made.

Each search reports the chance that noise alone would look this strong. That
chance is right for one search. A photometry run searches the target's own
star and ten bright stars, each twice (a smooth cycle, and a repeating dip),
so 22 chances are taken to see a pattern that is not there. At a 1% limit per
search, the chance that at least one of 22 passes by luck is about 20%.

The fix is the Sidak correction: the chance that at least one of ``m``
independent searches reaches probability ``p`` by luck is ``1 - (1 - p)^m``.
Judging each verdict on that number keeps the chance of a false "detected"
anywhere in the run at the stated 1% (or 5% for "possible"). Where the
searches are not independent (the cycle and dip searches of one star share
data), the correction is a little too strict, which is the safe side.

A correction only ever lowers a verdict. It also exposes a limit of the
search itself: with 300 noise-only versions the smallest probability
reportable is 1/301 (0.0033), and 1 - (1 - 0.0033)^22 is 0.07, above the
0.05 limit, so no verdict could survive. `shuffles_needed` says how many
versions a result needs for its corrected probability to be able to pass, and
the caller repeats only the searches that need it.
"""

import math
from collections.abc import Iterable, Sequence

from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    FALSE_ALARM_FOR_DETECTION,
    VERDICT_DETECTED,
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_POSSIBLE,
    verdict_from_false_alarm,
)

# The most noise-only versions a repeated search may use. Each version costs
# a full search, so this bounds the cost of the repeat; 5000 versions report
# a probability as small as 0.0002, enough for a family of about 50 searches.
MAXIMUM_REPEAT_SHUFFLES = 5000

SearchResult = PeriodogramResult | TransitCandidate


def family_wise_false_alarm(false_alarm: float, family_size: int) -> float:
    """Work out the chance that any of several searches looks this strong.

    Parameters
    ----------
    false_alarm : `float`
        One search's false-alarm probability.
    family_size : `int`
        How many searches were made in all (at least 1).

    Returns
    -------
    probability : `float`
        ``1 - (1 - false_alarm)^family_size``, between 0 and 1.
    """
    size = max(1, int(family_size))
    probability = min(max(float(false_alarm), 0.0), 1.0)
    return float(-math.expm1(size * math.log1p(-probability))) if probability < 1.0 else 1.0


def count_family(results: Iterable[SearchResult | None]) -> int:
    """Count the searches that were actually made.

    Parameters
    ----------
    results : `Iterable` [`PeriodogramResult`, `TransitCandidate` or `None`]
        Every search result of the run.

    Returns
    -------
    family_size : `int`
        The results that are not `None` and not ``insufficient_data``: a
        search that could not run took no chance of a false alarm.
    """
    return sum(1 for result in results if result is not None and result.verdict != VERDICT_INSUFFICIENT_DATA)


def shuffles_needed(family_size: int) -> int:
    """Say how many noise-only versions let a result pass the family limit.

    A result at the floor (no noise-only version matched it) has a
    probability of ``1 / (shuffles + 1)``. For its corrected probability to be
    able to reach the "detected" limit, that must be at most about the limit
    divided by the family size.

    Parameters
    ----------
    family_size : `int`
        How many searches were made.

    Returns
    -------
    shuffles : `int`
        100 per search in the family, at most `MAXIMUM_REPEAT_SHUFFLES`.
    """
    return int(min(MAXIMUM_REPEAT_SHUFFLES, math.ceil(max(1, family_size) / FALSE_ALARM_FOR_DETECTION)))


def is_at_the_floor(result: SearchResult) -> bool:
    """Say whether no noise-only version matched a result.

    Parameters
    ----------
    result : `PeriodogramResult` or `TransitCandidate`
        A search result.

    Returns
    -------
    at_floor : `bool`
        True when the false-alarm probability equals ``1 / (shuffles + 1)``,
        so more noise-only versions could show it is smaller still.
    """
    if not result.shuffle_count:
        return False
    return math.isclose(result.false_alarm_probability, 1.0 / (result.shuffle_count + 1), rel_tol=1e-9)


def needs_repeat(result: SearchResult | None, family_size: int) -> bool:
    """Say whether a search should be repeated with more noise-only versions.

    Parameters
    ----------
    result : `PeriodogramResult`, `TransitCandidate` or `None`
        A search result.
    family_size : `int`
        How many searches were made.

    Returns
    -------
    needed : `bool`
        True for a "detected" or "possible" result at the floor that used
        fewer versions than `shuffles_needed`.
    """
    if result is None or result.verdict not in (VERDICT_DETECTED, VERDICT_POSSIBLE):
        return False
    return is_at_the_floor(result) and (result.shuffle_count or 0) < shuffles_needed(family_size)


def apply_family_wise_correction(results: Sequence[SearchResult | None], family_size: int) -> None:
    """Judge every result on its family-wise probability, in place.

    A "detected" or "possible" result gets its family-wise probability and
    searches-in-family recorded, and its verdict recomputed with the same
    rules on that probability. The verdict can only fall. Results that were
    "not detected" or "insufficient data" are left as they were.

    Parameters
    ----------
    results : `Sequence` [`PeriodogramResult`, `TransitCandidate` or `None`]
        The run's search results.
    family_size : `int`
        How many searches were made (see `count_family`).
    """
    for result in results:
        if result is None or result.verdict not in (VERDICT_DETECTED, VERDICT_POSSIBLE):
            continue
        adjusted = family_wise_false_alarm(result.false_alarm_probability, family_size)
        result.uncorrected_verdict = result.verdict
        result.family_wise_false_alarm_probability = adjusted
        result.searches_in_family = family_size
        if isinstance(result, TransitCandidate):
            corrected = verdict_from_false_alarm(
                adjusted, 0.0, result.transit_count, result.points_in_transit
            )
        else:
            corrected = verdict_from_false_alarm(adjusted, result.cycles_observed or 0.0)
        result.verdict = corrected
        if corrected != result.uncorrected_verdict:
            sentence = (
                f"Judged against all {family_size} searches made on this target, the chance of a "
                f"pattern this strong by luck is {adjusted:.1%}, not {result.false_alarm_probability:.1%}."
            )
            result.note = f"{result.note} {sentence}".strip()
