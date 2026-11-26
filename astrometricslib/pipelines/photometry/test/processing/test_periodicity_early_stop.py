"""Purpose: Test that a search stops early and keeps its verdict.

Description: The searches also give the same answer on any thread count.
The false-alarm probability is (1 + stronger) / (1 + versions).
Once it is certain to exceed the "possible" cutoff, the verdict is "not
detected" whatever the other versions show, so the search stops. These tests
check that the stop changes only what it should: the verdict and the period
stay the same, the reported number of versions is the number actually
searched, a real signal is never stopped, and the answer does not depend on
how many threads search the versions.
"""

from collections.abc import Callable
from typing import Any

import numpy as np
import pytest

from astrometricslib.pipelines.photometry.processing import periodicity_search as search
from astrometricslib.pipelines.photometry.processing.family_wise_correction import (
    family_wise_false_alarm,
    single_search_limit,
)
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    FALSE_ALARM_FOR_POSSIBLE,
    VERDICT_DETECTED,
    VERDICT_NOT_DETECTED,
    box_search,
    lomb_scargle_search,
)

PLANNED_VERSIONS = 300


def _noise_light_curve(seed: int = 4) -> tuple[np.ndarray, np.ndarray]:
    """Build 60 nightly-spaced measurements of pure white noise.

    Returns
    -------
    light_curve : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Times over 1.5 days and brightness with 1% noise.
    """
    random_generator = np.random.default_rng(seed)
    return np.sort(random_generator.uniform(0.0, 1.5, 60)), random_generator.normal(1.0, 0.01, 60)


def _sinusoid_light_curve() -> tuple[np.ndarray, np.ndarray]:
    """Build a strong 0.2 day cycle in light noise.

    Returns
    -------
    light_curve : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        Times over 1.5 days and the brightness.
    """
    time_days, flux = _noise_light_curve(seed=5)
    return time_days, flux + 0.05 * np.sin(2.0 * np.pi * time_days / 0.2)


@pytest.mark.parametrize("search_function", [lomb_scargle_search, box_search], ids=["cycle", "dip"])
def test_noise_stops_early_with_the_same_verdict_and_period(
    search_function: Callable[..., Any],
) -> None:
    """Noise stops before every version, and keeps verdict and period."""
    time_days, flux = _noise_light_curve()

    exhaustive = search_function(time_days, flux, shuffle_count=PLANNED_VERSIONS, stop_above=None)
    early = search_function(time_days, flux, shuffle_count=PLANNED_VERSIONS)

    assert exhaustive.shuffle_count == PLANNED_VERSIONS
    assert early.shuffle_count < PLANNED_VERSIONS
    assert f"stopped after {early.shuffle_count} of {PLANNED_VERSIONS}" in early.note
    assert exhaustive.note == "" or "stopped" not in exhaustive.note
    assert early.verdict == exhaustive.verdict == VERDICT_NOT_DETECTED
    period = "best_period_days" if search_function is lomb_scargle_search else "period_days"
    assert getattr(early, period) == getattr(exhaustive, period)
    # Both are above the cutoff; the early one is from fewer versions.
    assert early.false_alarm_probability > FALSE_ALARM_FOR_POSSIBLE
    assert exhaustive.false_alarm_probability > FALSE_ALARM_FOR_POSSIBLE


def test_a_real_signal_runs_every_version_and_matches_the_exhaustive_search() -> None:
    """A result below the cutoff is never stopped, so nothing changes."""
    time_days, flux = _sinusoid_light_curve()

    exhaustive = lomb_scargle_search(time_days, flux, shuffle_count=PLANNED_VERSIONS, stop_above=None)
    early = lomb_scargle_search(time_days, flux, shuffle_count=PLANNED_VERSIONS)

    assert early.verdict == exhaustive.verdict == VERDICT_DETECTED
    assert early.shuffle_count == PLANNED_VERSIONS
    assert early.false_alarm_probability == exhaustive.false_alarm_probability


def test_the_stopping_point_does_not_depend_on_the_thread_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One thread and several threads give the same result."""
    time_days, flux = _noise_light_curve()
    results = []
    for thread_count in (1, 4):
        monkeypatch.setattr(search, "_noise_thread_count", lambda _work, _maximum, count=thread_count: count)
        results.append(lomb_scargle_search(time_days, flux, shuffle_count=PLANNED_VERSIONS))
        results.append(lomb_scargle_search(time_days, flux, shuffle_count=PLANNED_VERSIONS, stop_above=None))
        results.append(box_search(time_days, flux, shuffle_count=PLANNED_VERSIONS))

    assert results[:3] == results[3:]


def test_a_search_that_is_never_stopped_counts_like_the_plain_loop() -> None:
    """The counting helper agrees with a plain loop over the same orders."""
    values = np.random.default_rng(1).normal(size=40)
    generator = np.random.default_rng(9)
    reference_generator = np.random.default_rng(9)

    def strongest(order: np.ndarray) -> float:
        """Use the first three values in the order as the "power".

        Returns
        -------
        power : `float`
            Their sum.
        """
        return float(values[order][:3].sum())

    stronger, versions = search._count_stronger_noise_versions(strongest, 40, generator, 1, 50, 1.0, None, 3)
    expected = sum(strongest(search._null_order(40, reference_generator, 1)) >= 1.0 for _ in range(50))

    assert (stronger, versions) == (expected, 50)


def test_the_family_limit_matches_the_family_wise_formula() -> None:
    """A probability just above the limit is just above the family cutoff."""
    assert single_search_limit(1) == pytest.approx(FALSE_ALARM_FOR_POSSIBLE)
    for family_size in (2, 22, 50):
        limit = single_search_limit(family_size)
        assert family_wise_false_alarm(limit, family_size) == pytest.approx(FALSE_ALARM_FOR_POSSIBLE)
        assert family_wise_false_alarm(limit * 1.01, family_size) > FALSE_ALARM_FOR_POSSIBLE
