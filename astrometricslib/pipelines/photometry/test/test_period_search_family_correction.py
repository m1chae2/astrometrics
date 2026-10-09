"""Tests that a target's period searches are judged together.

A fake analyzer stands in for the real searches, so the tests show which
searches are repeated, what the repeat is asked to do, and what verdicts come
out. The searches' own statistics are tested in `test_periodicity_null.py` and
`test_family_wise_correction.py`.
"""

from typing import Any

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.models.stellar_source import PeriodogramResult, StellarObject, TransitCandidate
from astrometricslib.pipelines.photometry import batch
from astrometricslib.pipelines.photometry.processing import periodicity_search as search


def make_star(star_id: str, cycle: PeriodogramResult | None, dip: TransitCandidate | None) -> StellarObject:
    """Build a star carrying finished search results.

    Returns
    -------
    star : `StellarObject`
        A star with the given cycle and dip results.
    """
    star = StellarObject(id=star_id)
    star.photometry.periodogram = cycle
    star.photometry.transit_candidate = dip
    return star


def detected_cycle(false_alarm: float, shuffles: int = 300) -> PeriodogramResult:
    """Build a 'detected' cycle result.

    Returns
    -------
    result : `PeriodogramResult`
        A result seen over five cycles.
    """
    return PeriodogramResult(
        falseAlarmProbability=false_alarm,
        verdict=search.VERDICT_DETECTED,
        cyclesObserved=5.0,
        shuffleCount=shuffles,
    )


def quiet_cycle() -> PeriodogramResult:
    """Build a 'not detected' cycle result.

    Returns
    -------
    result : `PeriodogramResult`
        A result with a large false-alarm probability.
    """
    return PeriodogramResult(
        falseAlarmProbability=0.6, verdict=search.VERDICT_NOT_DETECTED, cyclesObserved=5.0, shuffleCount=300
    )


class FakeAnalyzer:
    """A variability analyzer stand-in; a repeat resolves to a set value."""

    def __init__(self, resolved_false_alarm: float, fail: bool = False) -> None:
        self.resolved_false_alarm = resolved_false_alarm
        self.fail = fail
        self.repeats: list[tuple[str, str, int | None]] = []

    def run_lomb_scargle_periodogram(self, star: StellarObject, shuffle_count: int | None = None) -> Any:
        """Repeat a cycle search.

        Returns
        -------
        result : `PeriodogramResult`
            The repeated result, with the requested number of versions.

        Raises
        ------
        ProcessingError
            When this fake was built to fail.
        """
        self.repeats.append((star.id, "cycle", shuffle_count))
        if self.fail:
            raise ProcessingError("the repeat failed")
        result = detected_cycle(self.resolved_false_alarm, shuffle_count or 300)
        star.photometry.periodogram = result
        return result

    def run_bls_transit_search(self, star: StellarObject, shuffle_count: int | None = None) -> Any:
        """Repeat a dip search.

        Returns
        -------
        result : `TransitCandidate`
            The repeated result.
        """
        self.repeats.append((star.id, "dip", shuffle_count))
        return star.photometry.transit_candidate


def test_a_floor_result_is_repeated_with_enough_versions_and_can_survive() -> None:
    """A cycle at the floor is repeated with 100 versions per search."""
    stars = [make_star(f"S{index}", quiet_cycle(), None) for index in range(10)]
    stars.append(make_star("Hit", detected_cycle(1 / 301), None))
    analyzer = FakeAnalyzer(resolved_false_alarm=1 / 1101)

    batch._correct_for_the_number_of_searches(analyzer, stars, "T")

    assert analyzer.repeats == [("Hit", "cycle", 1100)]
    survivor = stars[-1].photometry.periodogram
    assert survivor.verdict == search.VERDICT_DETECTED
    assert survivor.searches_in_family == 11


def test_a_floor_result_that_resolves_to_a_large_probability_is_lowered() -> None:
    """If the repeat finds it is not that small, the verdict falls."""
    stars = [make_star(f"S{index}", quiet_cycle(), None) for index in range(10)]
    stars.append(make_star("Hit", detected_cycle(1 / 301), None))
    analyzer = FakeAnalyzer(resolved_false_alarm=0.01)

    batch._correct_for_the_number_of_searches(analyzer, stars, "T")

    lowered = stars[-1].photometry.periodogram
    assert lowered.verdict == search.VERDICT_NOT_DETECTED
    assert lowered.uncorrected_verdict == search.VERDICT_DETECTED


def test_a_result_above_the_floor_is_not_repeated_but_is_still_judged() -> None:
    """A 0.5% result cannot be rescued by more versions; it is corrected."""
    stars = [make_star(f"S{index}", quiet_cycle(), None) for index in range(10)]
    stars.append(make_star("Marginal", detected_cycle(0.005), None))
    analyzer = FakeAnalyzer(resolved_false_alarm=0.0001)

    batch._correct_for_the_number_of_searches(analyzer, stars, "T")

    assert analyzer.repeats == []
    assert stars[-1].photometry.periodogram.verdict == search.VERDICT_NOT_DETECTED


def test_a_single_search_is_left_alone() -> None:
    """A family of one has no multiple-search problem; it is not touched."""
    star = make_star("Only", detected_cycle(0.005), None)
    analyzer = FakeAnalyzer(resolved_false_alarm=0.0001)

    batch._correct_for_the_number_of_searches(analyzer, [star], "T")

    assert analyzer.repeats == []
    assert star.photometry.periodogram.verdict == search.VERDICT_DETECTED
    assert star.photometry.periodogram.family_wise_false_alarm_probability is None


def test_a_failed_repeat_does_not_stop_the_correction() -> None:
    """If a repeat fails, the original result is still corrected and kept."""
    stars = [make_star(f"S{index}", quiet_cycle(), None) for index in range(10)]
    stars.append(make_star("Hit", detected_cycle(1 / 301), None))
    analyzer = FakeAnalyzer(resolved_false_alarm=0.0, fail=True)

    batch._correct_for_the_number_of_searches(analyzer, stars, "T")

    kept = stars[-1].photometry.periodogram
    # 1/301 over 11 searches is 3.6%: no longer 'detected', still 'possible'.
    assert kept.verdict == search.VERDICT_POSSIBLE
    assert kept.uncorrected_verdict == search.VERDICT_DETECTED
    assert kept.searches_in_family == 11
