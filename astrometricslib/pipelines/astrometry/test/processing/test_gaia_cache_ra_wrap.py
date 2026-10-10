"""Purpose: Tests for the cached Gaia search box near Right Ascension 0.

Description: Right Ascension (RA, the east-west sky coordinate) wraps from
360 deg back to 0 deg. A field centred near that line must select cached
Gaia rows on both sides of it. These tests check the RA ranges that
`_catalog_ra_ranges` builds and the rows that
`StarIdentifier._query_gaia_region_from_cache` selects with them.
"""

from typing import Any

import pytest

from astrometricslib.drivers import catalog_store
from astrometricslib.pipelines.astrometry.processing.star_identifier import (
    StarIdentifier,
    _catalog_ra_ranges,
)

_DECLINATION_DEG = 20.0


def _fake_cache_rows(ra_values_deg: list[float]) -> list[tuple[str, float, float, float, str]]:
    """Build fake cached Gaia rows at the given RA values.

    Parameters
    ----------
    ra_values_deg : `list` [`float`]
        RA of each row, in degrees.

    Returns
    -------
    rows : `list` [`tuple`]
        Rows shaped like `catalog_store.query_gaia_sources_in_bounds`
        results: ``(source_id, ra, dec, phot_g_mean_mag, designation)``.
    """
    return [
        (f"id{index}", ra_deg, _DECLINATION_DEG, 12.0, f"Gaia DR3 {index}")
        for index, ra_deg in enumerate(ra_values_deg)
    ]


def _select_cached_ras(
    monkeypatch: pytest.MonkeyPatch,
    cached_ra_values_deg: list[float],
    ra_center_deg: float,
    radius_deg: float,
) -> list[float]:
    """Run the cache lookup on fake rows and list the RA values it selects.

    The fake store applies the same rule as the SQL query: a row is
    returned when its RA and declination lie inside the box it is given.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest fixture used to replace the catalog store functions.
    cached_ra_values_deg : `list` [`float`]
        RA of every row in the fake cache, in degrees.
    ra_center_deg : `float`
        RA of the search centre, in degrees.
    radius_deg : `float`
        Search radius, in degrees.

    Returns
    -------
    selected_ra_deg : `list` [`float`]
        Sorted RA of every row the lookup returned, or an empty list
        when the lookup returned nothing.
    """
    rows = _fake_cache_rows(cached_ra_values_deg)

    def fake_query(
        config: Any,
        min_ra: float,
        max_ra: float,
        min_dec: float,
        max_dec: float,
        *,
        include_proper_motion: bool = False,
    ) -> list[tuple]:
        """Return the fake rows inside the box, like the SQL query does.

        Parameters
        ----------
        config : `Any`
            Unused; present to match the real function.
        min_ra, max_ra, min_dec, max_dec : `float`
            The edges of the box, in degrees.
        include_proper_motion : `bool`, optional
            Add two unknown proper-motion values to each row, as the real
            function does.

        Returns
        -------
        rows : `list` [`tuple`]
            The fake rows inside the box.
        """
        inside = [row for row in rows if min_ra <= row[1] <= max_ra and min_dec <= row[2] <= max_dec]
        return [(*row, None, None) for row in inside] if include_proper_motion else inside

    monkeypatch.setattr(catalog_store, "query_gaia_sources_in_bounds", fake_query)
    monkeypatch.setattr(catalog_store, "get_catalog_cache_path", lambda config: "unused.db")

    result = StarIdentifier._query_gaia_region_from_cache(None, ra_center_deg, _DECLINATION_DEG, radius_deg)
    if result is None:
        return []
    return sorted(float(ra) for ra in result[0]["ra"])


def test_field_at_ra_zero_selects_rows_on_both_sides_of_the_wrap(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check rows at 359.7 and 0.3 are selected, and 1.0 and 359.0 are not."""
    near_wrap_ra_deg = [359.7, 359.8, 359.9, 0.1, 0.2, 0.3]
    far_ra_deg = [1.0, 359.0]

    selected = _select_cached_ras(monkeypatch, near_wrap_ra_deg + far_ra_deg, 0.0, 0.5)

    assert selected == sorted(near_wrap_ra_deg)
    assert 1.0 not in selected
    assert 359.0 not in selected


def test_field_just_east_of_zero_still_selects_rows_just_west(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check a centre at 0.01 deg, radius 0.5 deg, declination 20 deg."""
    cached_ra_deg = [359.6, 359.7, 359.8, 359.9, 0.0, 0.1, 0.2, 0.3, 0.4]

    selected = _select_cached_ras(monkeypatch, cached_ra_deg, 0.01, 0.5)

    assert selected == sorted(cached_ra_deg)


def test_field_just_west_of_360_selects_rows_just_east_of_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    """Check that a centre at 359.99 deg also selects rows near 0 deg.

    The cache lookup returns nothing for fewer than 5 rows, so the fake
    cache holds 5.
    """
    cached_ra_deg = [359.5, 359.6, 359.9, 0.1, 0.4]

    selected = _select_cached_ras(monkeypatch, cached_ra_deg, 359.99, 0.5)

    assert selected == sorted(cached_ra_deg)


def test_field_away_from_the_wrap_uses_one_plain_range() -> None:
    """Check that a field far from RA = 0 deg builds one plain range."""
    ranges = _catalog_ra_ranges(180.0, 0.0, 0.5)

    assert ranges == [pytest.approx((179.5, 180.5))]


@pytest.mark.parametrize("ra_center_deg", [0.01, 359.99])
def test_field_on_the_wrap_builds_two_ranges_inside_zero_to_360(ra_center_deg: float) -> None:
    """Check that a box crossing the wrap splits into two ranges."""
    ranges = _catalog_ra_ranges(ra_center_deg, _DECLINATION_DEG, 0.5)

    assert len(ranges) == 2
    for low_deg, high_deg in ranges:
        assert 0.0 <= low_deg <= high_deg <= 360.0


def test_box_wider_than_half_the_sky_searches_every_ra() -> None:
    """Check that a box 180 deg or more wide falls back to the full circle."""
    assert _catalog_ra_ranges(10.0, 89.9, 30.0) == [(0.0, 360.0)]
