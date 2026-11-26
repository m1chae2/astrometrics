"""Purpose: Tests for filling and reading the Gaia tiles under an image.

Description: Before an image's stars are named, every cache tile under its
corners (plus the match radius) is filled to the query depth, a tile that hits
the row limit is fetched as its four children, and only complete tiles are
read. These tests use a fake fetcher and the test suite's own cache folder,
so no network is needed.
"""

import logging

import numpy as np
import pytest
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib._internal.drivers import catalog_store
from astrometricslib._internal.drivers.gaia_cone_driver import GAIA_CONE_COLUMNS, TileFetch
from astrometricslib._internal.drivers.sky_tiles import healpix_pixels_of_points
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.pipelines.astrometry.processing import gaia_field_matching, plate_solution_steps
from astrometricslib.pipelines.astrometry.processing.gaia_field_stars import (
    FieldGaiaStars,
    field_catalog_density,
    load_field_gaia_stars,
    tiles_under_image,
)
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.test.synthetic.sky_map import tan_sky_map

LEVEL = 7


def _wcs(ra: float = 339.28, dec: float = 34.57, size: int = 3000) -> WCS:
    """Build a north-up sky map of a 1.6-degree-wide image.

    Parameters
    ----------
    ra, dec : `float`
        The image center, in degrees.
    size : `int`, optional
        The image width and height, in pixels.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A tangent-plane map with 1.92 arcsec pixels.
    """
    wcs = tan_sky_map(ra, dec, (size / 2.0, size / 2.0), degrees_per_pixel=1.92 / 3600.0)
    return wcs


def _tile_table(tile: int, level: int, count: int, faintest: float = 20.0) -> Table:
    """Build the answer of one tile with stars whose IDs are in that tile.

    Parameters
    ----------
    tile : `int`
        The tile number at `level`.
    level : `int`
        The tile level.
    count : `int`
        How many stars.
    faintest : `float`, optional
        The faintest G among them.

    Returns
    -------
    table : `astropy.table.Table`
        The stars, brightest first.
    """
    shift = 35 + 2 * (12 - level)
    ids = [str((tile << shift) + index) for index in range(count)]
    return Table(
        [
            ids,
            np.full(count, 339.28),
            np.full(count, 34.57),
            np.linspace(10.0, faintest, count),
            [f"Gaia DR3 {number}" for number in ids],
            np.zeros(count),
            np.zeros(count),
        ],
        names=GAIA_CONE_COLUMNS,
    )


def test_the_tiles_reach_every_corner_of_a_wide_image() -> None:
    """Every corner of a 1.6-degree image falls in a listed tile."""
    wcs = _wcs()
    tiles = tiles_under_image(wcs, 3000, 3000)
    corners_x = np.array([0.0, 2999.0, 2999.0, 0.0])
    corners_y = np.array([0.0, 0.0, 2999.0, 2999.0])
    ra, dec = wcs.wcs_pix2world(corners_x, corners_y, 0)

    corner_tiles = set(healpix_pixels_of_points(LEVEL, ra, dec).tolist())

    assert corner_tiles <= set(tiles)


def test_missing_tiles_are_filled_and_read() -> None:
    """Every missing tile is fetched once, recorded and read back."""
    tiles = tiles_under_image(_wcs(size=300), 300, 300)
    calls = []

    def fetch(level: int, tile: int, have: float, want: float) -> TileFetch:
        """Answer each tile with three stars.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.

        Returns
        -------
        answer : `TileFetch`
            Three stars, complete to `want`.
        """
        calls.append((level, tile))
        return TileFetch(_tile_table(tile, level, 3, faintest=want - 0.5), want, "test", False)

    field = load_field_gaia_stars(None, tiles, 19.0, fetch)

    assert sorted(tile for _, tile in calls) == sorted(tiles)
    assert field.missing_tiles == []
    assert field.table is not None and len(field.table) == 3 * len(tiles)
    calls.clear()
    again = load_field_gaia_stars(None, tiles, 19.0, fetch)
    assert calls == []
    assert again.table is not None and len(again.table) == len(field.table)


def test_a_tile_at_the_row_limit_is_fetched_as_four_children() -> None:
    """A cut-short tile is asked again as its four level-8 children."""
    calls = []

    def fetch(level: int, tile: int, have: float, want: float) -> TileFetch:
        """Cut the level-7 answer short; answer each child in full.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.

        Returns
        -------
        answer : `TileFetch`
            The answer.
        """
        calls.append((level, tile))
        if level == LEVEL:
            return TileFetch(_tile_table(tile, level, 5, faintest=18.0), 18.0, "test", True)
        return TileFetch(_tile_table(tile, level, 2, faintest=20.5), want, "test", False)

    field = load_field_gaia_stars(None, [500], 21.0, fetch)

    assert calls == [(7, 500), (8, 2000), (8, 2001), (8, 2002), (8, 2003)]
    assert field.split_tiles == [500]
    assert field.missing_tiles == []
    assert field.table is not None and len(field.table) == 8
    assert catalog_store.tile_status(None, [500])[500] == pytest.approx(21.0)


def test_a_failed_tile_is_recorded_and_its_stars_are_not_used() -> None:
    """A tile that failed on every service is missing; the others are read."""

    def fetch(level: int, tile: int, have: float, want: float) -> TileFetch:
        """Fail tile 601 and answer tile 600.

        Parameters
        ----------
        level, tile : `int`
            The tile.
        have, want : `float`
            The depths.

        Returns
        -------
        answer : `TileFetch`
            The answer of tile 600.

        Raises
        ------
        ExternalServiceError
            For tile 601.
        """
        if tile == 601:
            raise ExternalServiceError("down")
        return TileFetch(_tile_table(tile, level, 4, faintest=19.0), want, "test", False)

    field = load_field_gaia_stars(None, [600, 601], 20.0, fetch)

    assert field.failed_tiles == [601]
    assert field.missing_tiles == [601]
    assert field.complete_tiles == [600]
    assert field.table is not None and len(field.table) == 4


def test_a_tile_complete_only_to_a_shallower_depth_is_missing() -> None:
    """Stars of a tile not complete to the depth wanted are not matched."""
    catalog_store.record_tile_fetch(None, 700, 17.0, [tuple(_tile_table(700, 7, 2, faintest=16.0)[0])], "t")

    field = load_field_gaia_stars(None, [700], 19.0, None, skipped_reason="offline")

    assert field.missing_tiles == [700]
    assert field.table is None
    assert field.skipped_reason == "offline"


def test_the_identifier_warns_once_and_flags_missing_tiles(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Missing tiles give one warning per field and the run flag."""
    monkeypatch.setattr(gaia_field_matching, "make_gaia_tile_fetcher", lambda: (None, "offline test"))
    identifier = StarIdentifier.__new__(StarIdentifier)
    identifier.config = None
    identifier.astrometry_flags = []
    identifier.gaia_depth_prediction = None
    identifier.field_gaia_stars = None

    with caplog.at_level(logging.WARNING):
        identifier._prepare_field_gaia_stars(_wcs(size=300), 300, 300)
        identifier._prepare_field_gaia_stars(_wcs(size=300), 300, 300)

    warnings = [record for record in caplog.records if "Gaia tiles under this image" in record.getMessage()]
    assert len(warnings) == 1
    assert "offline test" in warnings[0].getMessage()
    assert identifier.astrometry_flags == [gaia_field_matching.FLAG_GAIA_TILES_MISSING]


@pytest.mark.parametrize(("cache_only", "expected_level"), [(True, logging.INFO), (False, logging.WARNING)])
def test_an_empty_refit_cache_is_a_warning_only_after_a_real_fetch(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, cache_only: bool, expected_level: int
) -> None:
    """An empty refit cache is INFO in cache-only mode, WARNING otherwise.

    The stacking solve reads the cache only, and a new field has nothing
    cached until the analysis downloads it. After a real fetch, an empty
    answer is still a warning. Both keep the note and the flag.
    """
    monkeypatch.setattr(gaia_field_matching, "load_field_gaia_stars", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(plate_solution_steps, "load_field_gaia_stars", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        StarIdentifier,
        "_prepare_field_gaia_stars",
        lambda self, wcs, width, height: type("Field", (), {"tiles": [1]})(),
    )
    identifier = StarIdentifier.__new__(StarIdentifier)
    identifier.config = None
    identifier.astrometry_flags = []
    identifier.gaia_depth_prediction = None
    identifier.field_gaia_stars = None
    wcs = _wcs(size=300)

    with caplog.at_level(logging.INFO):
        kept = identifier._refine_wcs_against_gaia(wcs, [], 300, 300, cache_only=cache_only)

    records = [record for record in caplog.records if "No Gaia stars for the refit" in record.getMessage()]
    assert [record.levelno for record in records] == [expected_level]
    assert kept is wcs
    assert identifier.wcs_refit_note.startswith("not refit: no Gaia stars cached")
    assert identifier.astrometry_flags == [plate_solution_steps.FLAG_WCS_REFINEMENT_SKIPPED]


def test_the_catalog_density_counts_the_stars_on_the_image_per_square_degree() -> None:
    """Stars on the image over its sky area; stars off it do not count."""
    from astropy import units as u
    from astropy.coordinates import SkyCoord

    sky_map = tan_sky_map(100.0, 20.0, (50.5, 50.5), degrees_per_pixel=0.01)
    on_image = sky_map.pixel_to_world(np.linspace(5, 95, 30), np.linspace(10, 90, 30))
    off_image = SkyCoord(ra=[110.0, 90.0] * u.deg, dec=[20.0, 20.0] * u.deg)
    coords = SkyCoord(
        ra=np.concatenate([on_image.ra.deg, off_image.ra.deg]) * u.deg,
        dec=np.concatenate([on_image.dec.deg, off_image.dec.deg]) * u.deg,
    )
    field = FieldGaiaStars(table=None, coords=coords, depth=18.0, tiles=[1], complete_tiles=[1])

    # 30 stars over 100 x 100 pixels of 0.01 degree: one square degree.
    assert field_catalog_density(field, sky_map, 100, 100) == pytest.approx(30.0, rel=1e-3)


def test_the_catalog_density_is_unknown_when_a_tile_is_missing_or_no_map() -> None:
    """A missing tile would undercount, so the density is not given."""
    sky_map = tan_sky_map(100.0, 20.0, (50.5, 50.5), degrees_per_pixel=0.01)
    coords = sky_map.pixel_to_world([10.0], [10.0])
    missing = FieldGaiaStars(table=None, coords=coords, depth=18.0, tiles=[1, 2], missing_tiles=[2])
    empty = FieldGaiaStars(table=None, coords=None, depth=18.0, tiles=[1])

    assert field_catalog_density(missing, sky_map, 100, 100) is None
    assert field_catalog_density(empty, sky_map, 100, 100) is None
    assert (
        field_catalog_density(FieldGaiaStars(table=None, coords=coords, depth=18.0), None, 100, 100) is None
    )
