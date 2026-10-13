"""Purpose: Tests for the light-curve readiness fields on `StellarObject`.

Description: The Astronomy Manager used to hold its own copies of the
fewest measurements each brightness-pattern search accepts. The star now
says itself whether each search can run (`can_run_period_search`,
`can_run_transit_search`), from the library's own minimums.
"""

from datetime import datetime, timedelta

import pytest

from astrometricslib.models.stellar_source import (
    MINIMUM_POINTS_FOR_PERIOD_SEARCH,
    MINIMUM_POINTS_FOR_TRANSIT_SEARCH,
    PhotometryResult,
    StellarObject,
)


def _star(points: int) -> StellarObject:
    """Build a star with a light curve of the given length.

    Parameters
    ----------
    points : `int`
        How many measurements.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    start = datetime(2026, 1, 1)
    stamps = [start + timedelta(hours=hour) for hour in range(points)]
    return StellarObject(id="S", photometry=PhotometryResult(timestamps=stamps))


@pytest.mark.parametrize(
    ("points", "period", "transit"), [(0, False, False), (4, False, False), (5, True, False), (8, True, True)]
)
def test_each_search_needs_its_minimum_number_of_points(points: int, period: bool, transit: bool) -> None:
    """Five points allow the cycle search; eight also allow the dip search."""
    star = _star(points)
    assert star.can_run_period_search is period
    assert star.can_run_transit_search is transit


def test_the_minimums_are_five_and_eight_and_reach_the_app() -> None:
    """The limits match the searches, and the flags are in the star's JSON."""
    assert (MINIMUM_POINTS_FOR_PERIOD_SEARCH, MINIMUM_POINTS_FOR_TRANSIT_SEARCH) == (5, 8)
    data = _star(6).model_dump(by_alias=True)
    assert data["canRunPeriodSearch"] is True
    assert data["canRunTransitSearch"] is False
    assert StellarObject(id="S", photometry=None).can_run_period_search is False
