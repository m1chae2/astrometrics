"""Tests for judging period-search verdicts against every search made.

A run searches the target star and ten bright ones, each for a cycle and a
dip. At a 1% limit per search, about one run in five would show a false
"detected" somewhere. These tests check the arithmetic, which verdicts the
correction may lower, and that it can only lower them.
"""

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PeriodogramResult, TransitCandidate
from astrometricslib.pipelines.photometry.processing import family_wise_correction as fwc
from astrometricslib.pipelines.photometry.processing import periodicity_search as search


def cycle(
    false_alarm: float, verdict: str = search.VERDICT_DETECTED, shuffles: int = 300
) -> PeriodogramResult:
    """Build a cycle-search result.

    Returns
    -------
    result : `PeriodogramResult`
        A result seen over five cycles.
    """
    return PeriodogramResult(
        falseAlarmProbability=false_alarm, verdict=verdict, cyclesObserved=5.0, shuffleCount=shuffles
    )


def dip(false_alarm: float, verdict: str = search.VERDICT_DETECTED, shuffles: int = 300) -> TransitCandidate:
    """Build a dip-search result.

    Returns
    -------
    result : `TransitCandidate`
        A result with five dips and plenty of points in them.
    """
    return TransitCandidate(
        falseAlarmProbability=false_alarm,
        verdict=verdict,
        transitCount=5,
        pointsInTransit=30,
        shuffleCount=shuffles,
    )


def test_one_search_is_left_as_it_is() -> None:
    """A family of one needs no correction."""
    assert fwc.family_wise_false_alarm(0.01, 1) == pytest.approx(0.01)


def test_twenty_two_searches_at_one_percent_each_give_about_one_in_five() -> None:
    """The case the correction exists for: 22 chances at 1% is about 20%."""
    assert fwc.family_wise_false_alarm(0.01, 22) == pytest.approx(1 - 0.99**22)
    assert fwc.family_wise_false_alarm(0.01, 22) == pytest.approx(0.198, abs=0.001)


def test_the_correction_handles_the_extremes() -> None:
    """Zero stays zero, one stays one, and a family below one counts as one."""
    assert fwc.family_wise_false_alarm(0.0, 22) == pytest.approx(0.0)
    assert fwc.family_wise_false_alarm(1.0, 22) == pytest.approx(1.0)
    assert fwc.family_wise_false_alarm(0.05, 0) == pytest.approx(0.05)


def test_the_corrected_rate_of_pure_noise_matches_the_stated_limit() -> None:
    """Simulate runs of 22 noise searches: false 'detected' calls fall to 1%.

    Each noise search has a uniform false-alarm probability. Uncorrected,
    about 20% of runs contain a value at or below 0.01; corrected with the
    module's own function, about 1%.
    """
    runs = np.random.default_rng(8).uniform(size=(5000, 22))
    uncorrected = np.mean(np.any(runs <= 0.01, axis=1))
    corrected = np.mean([any(fwc.family_wise_false_alarm(p, 22) <= 0.01 for p in run) for run in runs])

    assert uncorrected == pytest.approx(0.2, abs=0.03)
    assert corrected == pytest.approx(0.01, abs=0.01)


def test_only_searches_that_ran_count_toward_the_family() -> None:
    """Missing and insufficient results took no chance of a false alarm."""
    results = [cycle(0.5), None, cycle(0.5, search.VERDICT_INSUFFICIENT_DATA), dip(0.5)]

    assert fwc.count_family(results) == 2


def test_the_repeat_size_grows_with_the_family_and_is_capped() -> None:
    """One hundred versions per search, at most 5000."""
    assert fwc.shuffles_needed(1) == 100
    assert fwc.shuffles_needed(22) == 2200
    assert fwc.shuffles_needed(500) == fwc.MAXIMUM_REPEAT_SHUFFLES


def test_a_result_at_the_floor_is_repeated_but_one_above_it_is_not() -> None:
    """Only a result no noise-only version matched can hide a smaller one."""
    at_floor = cycle(1 / 301)
    above_floor = cycle(3 / 301)

    assert fwc.is_at_the_floor(at_floor)
    assert not fwc.is_at_the_floor(above_floor)
    assert fwc.needs_repeat(at_floor, 22)
    assert not fwc.needs_repeat(above_floor, 22)


def test_a_repeat_is_not_needed_when_it_was_already_made_with_enough_versions() -> None:
    """A result already measured with 2200 versions is final."""
    assert not fwc.needs_repeat(cycle(1 / 2201, shuffles=2200), 22)


def test_nothing_that_failed_is_repeated() -> None:
    """Not-detected, insufficient and missing results are never repeated."""
    assert not fwc.needs_repeat(cycle(1 / 301, search.VERDICT_NOT_DETECTED), 22)
    assert not fwc.needs_repeat(cycle(1 / 301, search.VERDICT_INSUFFICIENT_DATA), 22)
    assert not fwc.needs_repeat(None, 22)


def test_a_marginal_detection_falls_when_judged_against_the_family() -> None:
    """A cycle that passed alone at 0.5% fails at the 22-search level."""
    result = cycle(0.005)

    fwc.apply_family_wise_correction([result], 22)

    assert result.uncorrected_verdict == search.VERDICT_DETECTED
    assert result.family_wise_false_alarm_probability == pytest.approx(1 - 0.995**22)
    assert result.verdict == search.VERDICT_NOT_DETECTED
    assert result.searches_in_family == 22
    assert "all 22 searches" in result.note


def test_a_strong_detection_survives_the_family_correction() -> None:
    """A result resolved to 0.0002 stays detected in a family of 22."""
    result = cycle(0.0002, shuffles=5000)

    fwc.apply_family_wise_correction([result], 22)

    assert result.verdict == search.VERDICT_DETECTED
    assert result.note == ""


def test_a_dip_is_judged_by_the_same_rule() -> None:
    """The dip search uses its event and point counts, as before."""
    weak = dip(0.005)
    strong = dip(0.0002, shuffles=5000)

    fwc.apply_family_wise_correction([weak, strong], 22)

    assert weak.verdict == search.VERDICT_NOT_DETECTED
    assert strong.verdict == search.VERDICT_DETECTED


def test_the_correction_only_ever_lowers_a_verdict() -> None:
    """A not-detected or insufficient result is left exactly as it was."""
    not_detected = cycle(0.4, search.VERDICT_NOT_DETECTED)
    insufficient = cycle(1.0, search.VERDICT_INSUFFICIENT_DATA)

    fwc.apply_family_wise_correction([not_detected, insufficient, None], 22)

    assert not_detected.verdict == search.VERDICT_NOT_DETECTED
    assert not_detected.family_wise_false_alarm_probability is None
    assert insufficient.verdict == search.VERDICT_INSUFFICIENT_DATA
