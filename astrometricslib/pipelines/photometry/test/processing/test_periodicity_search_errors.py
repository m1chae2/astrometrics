"""Purpose: Test that the period searches use per-point errors as weights.

Description: A light curve in which some points are measured much better than
others should be searched with those errors (review item S8). The test light
curve has a clean cycle seen by precise points and hidden by very noisy ones.
With the true errors, both searches must find the cycle. The tests also check
that errors of the wrong length or with a zero in them are ignored, and that
a search given no errors gives the same answer as before.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    VERDICT_DETECTED,
    box_search,
    lomb_scargle_search,
)

PERIOD_DAYS = 0.2
POINT_COUNT = 240
SPAN_DAYS = 4.0


def _mixed_quality_cycle() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Build a cycle seen by precise points and hidden by noisy ones.

    Returns
    -------
    light_curve : `tuple` [`numpy.ndarray`, `numpy.ndarray`, `numpy.ndarray`]
        The times in days, the brightness, and the 1-sigma error of each
        point. Every second point has an error of 0.5 and the rest 0.01. The
        cycle has an amplitude of 0.05 and a period of 0.2 d.
    """
    rng = np.random.default_rng(11)
    time_days = np.sort(rng.uniform(0.0, SPAN_DAYS, POINT_COUNT))
    errors = np.where(np.arange(POINT_COUNT) % 2 == 0, 0.01, 0.5)
    signal = 1.0 + 0.05 * np.sin(2.0 * np.pi * time_days / PERIOD_DAYS)
    return time_days, signal + rng.normal(0.0, errors), errors


def test_the_cycle_search_finds_a_cycle_that_only_the_precise_points_show() -> None:
    """With the true errors, the noisy half of the points is down-weighted."""
    time_days, flux, errors = _mixed_quality_cycle()

    weighted = lomb_scargle_search(time_days, flux, shuffle_count=200, flux_errors=errors)
    unweighted = lomb_scargle_search(time_days, flux, shuffle_count=200)

    assert weighted.best_period_days == pytest.approx(PERIOD_DAYS, rel=0.02)
    assert weighted.verdict == VERDICT_DETECTED
    assert unweighted.verdict != VERDICT_DETECTED


def test_the_dip_search_accepts_errors_and_keeps_its_fields() -> None:
    """The box search runs with errors and returns a complete candidate."""
    time_days, flux, errors = _mixed_quality_cycle()

    candidate = box_search(time_days, flux, shuffle_count=20, flux_errors=errors)

    assert candidate.period_days is not None
    assert candidate.transit_snr is not None


@pytest.mark.parametrize(
    "bad_errors",
    [np.full(POINT_COUNT - 1, 0.01), np.zeros(POINT_COUNT), np.full(POINT_COUNT, np.nan)],
    ids=["wrong length", "zeros", "not a number"],
)
def test_unusable_errors_give_the_same_answer_as_no_errors(bad_errors: np.ndarray) -> None:
    """Errors that cannot be weights are ignored, not partly used."""
    time_days, flux, _ = _mixed_quality_cycle()

    without = lomb_scargle_search(time_days, flux, shuffle_count=20)
    with_bad = lomb_scargle_search(time_days, flux, shuffle_count=20, flux_errors=bad_errors)

    assert with_bad.best_period_days == pytest.approx(without.best_period_days)
    assert with_bad.power == pytest.approx(without.power)
    assert with_bad.false_alarm_probability == pytest.approx(without.false_alarm_probability)
