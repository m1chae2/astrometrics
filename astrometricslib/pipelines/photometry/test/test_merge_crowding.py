"""Purpose: Test that merging sessions keeps a star's crowding fields.

Description: Each session measures crowding on its own reference frame. A
real M 27 run lost these fields for 484 of 773 stars because the merge of
two sessions' light curves did not copy them, so the crowded flag protected
only the stars seen on one night. The merge now keeps the worst case: a star
crowded on any night stays crowded, the nearest neighbor is the closest one
seen (with its own flux ratio), and the masked sky share is the largest.
"""

from datetime import datetime, timedelta

import pytest

from astrometricslib.models.stellar_source import PhotometryResult
from astrometricslib.pipelines.photometry.batch import _merge_light_curves


def _segment(
    night: int,
    *,
    crowded: bool | None,
    distance_px: float | None = None,
    ratio: float | None = None,
    masked: float | None = None,
) -> PhotometryResult:
    """Build one night's two-point light curve with crowding fields.

    Parameters
    ----------
    night : `int`
        The day of the month, so each segment has its own timestamps.
    crowded : `bool` or `None`
        The segment's crowded flag.
    distance_px, ratio, masked : `float` or `None`, optional
        The nearest-neighbor distance, its flux ratio, and the masked sky
        share.

    Returns
    -------
    light_curve : `PhotometryResult`
        The segment.
    """
    start = datetime(2026, 9, night, 3, 0)
    return PhotometryResult(
        timestamps=[start, start + timedelta(minutes=1)],
        fluxes=[1.0, 1.0],
        fluxes_normalized=[1.0, 1.0],
        crowded=crowded,
        nearest_neighbor_distance_px=distance_px,
        nearest_neighbor_flux_ratio=ratio,
        masked_sky_fraction=masked,
    )


def test_a_star_crowded_on_one_night_stays_crowded_with_the_closer_neighbor() -> None:
    """Crowded on either night means crowded; the pair comes from one night."""
    merged = _merge_light_curves(
        _segment(23, crowded=False, distance_px=7.5, ratio=0.9, masked=0.05),
        _segment(24, crowded=True, distance_px=3.4, ratio=0.2, masked=0.01),
    )

    assert merged.crowded is True
    assert merged.nearest_neighbor_distance_px == pytest.approx(3.4)
    assert merged.nearest_neighbor_flux_ratio == pytest.approx(0.2)
    assert merged.masked_sky_fraction == pytest.approx(0.05)


def test_two_uncrowded_nights_stay_uncrowded() -> None:
    """Both nights recorded the star as clear, so the merged star is clear."""
    merged = _merge_light_curves(_segment(23, crowded=False), _segment(24, crowded=False))

    assert merged.crowded is False
    assert merged.nearest_neighbor_distance_px is None


def test_a_night_without_crowding_fields_does_not_erase_the_other() -> None:
    """A segment saved before crowding was recorded keeps the other one."""
    merged = _merge_light_curves(
        _segment(23, crowded=None), _segment(24, crowded=True, distance_px=4.0, ratio=0.5, masked=0.1)
    )

    assert merged.crowded is True
    assert merged.nearest_neighbor_distance_px == pytest.approx(4.0)
    assert merged.nearest_neighbor_flux_ratio == pytest.approx(0.5)
    assert merged.masked_sky_fraction == pytest.approx(0.1)


def test_three_nights_keep_the_worst_case() -> None:
    """Merging night by night keeps the worst case at each step."""
    merged = _merge_light_curves(
        _merge_light_curves(
            _segment(23, crowded=False, distance_px=6.0, ratio=0.3, masked=0.0),
            _segment(24, crowded=True, distance_px=3.0, ratio=1.0, masked=0.2),
        ),
        _segment(25, crowded=False, distance_px=5.0, ratio=0.1, masked=0.1),
    )

    assert merged.crowded is True
    assert (merged.nearest_neighbor_distance_px, merged.nearest_neighbor_flux_ratio) == pytest.approx((
        3.0,
        1.0,
    ))
    assert merged.masked_sky_fraction == pytest.approx(0.2)


def test_no_crowding_recorded_anywhere_stays_unknown() -> None:
    """Old records with no crowding fields merge to `None`, not `False`."""
    merged = _merge_light_curves(_segment(23, crowded=None), _segment(24, crowded=None))

    assert merged.crowded is None
    assert merged.masked_sky_fraction is None
