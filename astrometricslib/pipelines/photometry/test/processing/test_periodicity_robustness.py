"""Purpose: Test the period searches on light curves that used to stop them.

Description: In real analysis jobs the searches failed on some stars with
"zero-size array to reduction operation minimum", "Singular matrix", "The
maximum transit duration must be shorter than the minimum period" and "index 1
is out of bounds". Each test builds a light curve of the kind that caused one
of them and checks that the search finishes and says what it found, or says
why it could not search. The "Singular matrix" and "maximum transit duration"
errors come from `BoxLeastSquares.compute_stats`, which the box search no
longer calls; those tests make it fail and check that the search does not
notice.
"""

import numpy as np
import pytest
from astropy.timeseries import BoxLeastSquares

from astrometricslib.pipelines.photometry.processing import periodicity_search as search
from astrometricslib.pipelines.photometry.processing.periodicity_search import (
    VERDICT_INSUFFICIENT_DATA,
    VERDICT_NOT_DETECTED,
    box_search,
    clean_light_curve,
    lomb_scargle_search,
)


def _flat_light_curve(point_count: int = 24, seed: int = 2) -> tuple[np.ndarray, np.ndarray]:
    """Build a short, flat light curve with a gap after the first point.

    Returns
    -------
    light_curve : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The times in days (the first point at 0, the rest from 0.0216 d at
        0.0004 d steps) and the brightness (1 with 1% noise).
    """
    random_generator = np.random.default_rng(seed)
    time_days = np.concatenate([[0.0], 0.0216 + np.arange(point_count - 1) * 0.0004])
    return time_days, random_generator.normal(1.0, 0.01, point_count)


def test_a_dip_made_of_the_first_measurement_alone_is_counted_not_a_crash() -> None:
    """The best box can hold only the point at time zero.

    That point sits on the box's left edge. The search used to stop with
    "zero-size array to reduction operation minimum", because the dip count
    left out a point exactly on the edge of the box.
    """
    time_days, flux = _flat_light_curve()
    flux[0] = -3.9  # a wild first point: the strongest "dip" in the data

    candidate = box_search(time_days, flux, shuffle_count=20)

    assert candidate.transit_count == 1
    assert candidate.points_in_transit == 1
    assert candidate.verdict == VERDICT_NOT_DETECTED
    assert "only once" in candidate.note


def test_the_box_search_does_not_depend_on_the_sine_fit_of_compute_stats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error in `compute_stats` is not seen by the search.

    `BoxLeastSquares.compute_stats` fits a sine wave with a 3 by 3 solve and
    checks the box against the period; both failed in real runs. The search
    needs only the dip counts, which it makes itself.
    """

    def failing_stats(*_arguments: object, **_keywords: object) -> dict:
        """Fail the way real runs failed.

        Raises
        ------
        numpy.linalg.LinAlgError
            Always.
        """
        raise np.linalg.LinAlgError("Singular matrix")

    monkeypatch.setattr(BoxLeastSquares, "compute_stats", failing_stats)
    time_days, flux = _flat_light_curve()

    candidate = box_search(time_days, flux, shuffle_count=5)

    assert candidate.verdict in (VERDICT_NOT_DETECTED, "possible", "detected")
    assert candidate.period_days > 0.0


def test_a_period_range_too_narrow_for_two_frequencies_says_insufficient_data() -> None:
    """Fewer than two grid frequencies gave "index 1 is out of bounds"."""
    time_days = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.01, 6.01])

    result = lomb_scargle_search(time_days, np.linspace(1.0, 1.1, 8), shuffle_count=5)

    assert search.describe_frequency_grid(time_days) is None
    assert result.verdict == VERDICT_INSUFFICIENT_DATA
    assert "too short" in result.note


def test_a_brightness_that_never_changes_is_not_a_cycle() -> None:
    """A constant light curve once gave a "possible" cycle from no power."""
    time_days = np.linspace(0.0, 1.0, 40)

    result = lomb_scargle_search(time_days, np.ones(40), shuffle_count=20)

    assert result.verdict == VERDICT_INSUFFICIENT_DATA
    assert "same in every measurement" in result.note
    assert not result.best_period_days


def test_measurements_that_are_not_numbers_are_left_out_not_searched() -> None:
    """A missing brightness gives the answer of a curve without that point."""
    time_days = np.linspace(0.0, 1.0, 40)
    flux = np.random.default_rng(0).normal(1.0, 0.01, 40)
    with_gap = flux.copy()
    with_gap[7] = np.nan
    with_gap[8] = np.inf
    keep = np.ones(40, dtype=bool)
    keep[[7, 8]] = False

    gappy = lomb_scargle_search(time_days, with_gap, shuffle_count=20)
    trimmed = lomb_scargle_search(time_days[keep], flux[keep], shuffle_count=20)
    gappy_dip = box_search(time_days, with_gap, shuffle_count=20)

    assert gappy.best_period_days == pytest.approx(trimmed.best_period_days)
    assert gappy.false_alarm_probability == pytest.approx(trimmed.false_alarm_probability)
    assert gappy.verdict == trimmed.verdict
    assert gappy_dip.verdict != "" and np.isfinite(gappy_dip.period_days)


def test_a_light_curve_with_nothing_usable_says_insufficient_data() -> None:
    """All-missing brightness gives a note, not an error."""
    time_days = np.linspace(0.0, 1.0, 12)

    cycle = lomb_scargle_search(time_days, np.full(12, np.nan), shuffle_count=5)
    dip = box_search(time_days, np.full(12, np.nan), shuffle_count=5)

    assert cycle.verdict == VERDICT_INSUFFICIENT_DATA
    assert dip.verdict == VERDICT_INSUFFICIENT_DATA
    assert "no usable measurements" in cycle.note


def test_clean_light_curve_keeps_the_errors_of_the_kept_points() -> None:
    """Dropping a point drops its uncertainty too, and counts it."""
    time_days = np.array([0.0, 1.0, 2.0, 3.0])
    flux = np.array([1.0, np.nan, 1.1, 0.9])
    errors = np.array([0.1, 0.2, 0.3, 0.4])

    kept_time, kept_flux, kept_errors, dropped = clean_light_curve(time_days, flux, errors)

    assert dropped == 1
    assert kept_time.tolist() == [0.0, 2.0, 3.0]
    assert kept_flux.tolist() == [1.0, 1.1, 0.9]
    assert kept_errors.tolist() == [0.1, 0.3, 0.4]


@pytest.mark.parametrize(
    ("time_days", "expected"),
    [
        (np.array([0.0, 0.1, 3.0, 10.0, 10.2, 14.0]), (2, 4)),
        (np.array([5.0, 6.0, 7.0]), (0, 0)),
    ],
    ids=["two separate dips", "no dip"],
)
def test_dip_events_are_counted_from_the_box(time_days: np.ndarray, expected: tuple[int, int]) -> None:
    """Points inside the box around each epoch are counted by dip."""
    # Boxes 0.5 d wide, centered on 0, 10, 20, and so on.
    events, points = search._count_dip_events(time_days, 10.0, 0.5, 0.0)

    assert (events, points) == expected


def test_a_point_on_the_left_edge_is_inside_and_on_the_right_edge_is_outside() -> None:
    """The box is closed on its left edge and open on its right."""
    # The left edge, the right edge, and the left edge of the next box.
    time_days = np.array([-0.25, 0.25, 9.75])

    events, points = search._count_dip_events(time_days, 10.0, 0.5, 0.0)

    assert (events, points) == (2, 2)
