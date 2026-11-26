"""Fills the Gaia tiles under an image and reads back the stars it can trust.

The local Gaia cache is cut into sky tiles (see
``_internal/drivers/sky_tiles.py``). Before the stars of an image are named,
this module lists every tile under the image (its four corners, plus a margin
the size of the catalog match radius), downloads the tiles the cache does not
yet hold to the depth wanted, and then reads the stars of the tiles that are
complete. A tile that is not complete is left out on purpose: if half its
stars were missing, a detection could be matched to the wrong neighbor and
keep that name for good.

A download that hits the row limit was cut short, because the rows come
brightest first. Such a tile is fetched again as its four children one level
finer, which hold a quarter of the stars each, and is complete only to the
shallowest depth the four children reached.

This module does not decide when to go online or how long to wait. The
caller passes a `TileFetcher` (one function that fetches one tile at one
level), so the circuit breaker and the time limits stay in the star
identifier. A run that may not go online passes no fetcher and reads the
cache alone.
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from astropy import units as u
from astropy.coordinates import SkyCoord
from astropy.table import Table

from astrometricslib._internal.drivers import catalog_store
from astrometricslib._internal.drivers.gaia_cone_driver import TileFetch
from astrometricslib._internal.drivers.sky_tiles import tiles_for_footprint
from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.pipelines.shared.catalog_star_identity import CATALOG_MATCH_RADIUS_ARCSEC
from astrometricslib.pipelines.shared.header_wcs import image_corners_on_sky

logger = logging.getLogger(__name__)

__all__ = [
    "FieldGaiaStars",
    "TileFetcher",
    "fill_field_tiles",
    "load_field_gaia_stars",
    "tiles_under_image",
]

# Fetches one tile: (level, tile, depth already held, depth wanted) -> answer.
# It raises `ExternalServiceError` when every service failed.
TileFetcher = Callable[[int, int, float, float], TileFetch]

# A tile counts as complete to a depth when its recorded depth reaches the
# depth wanted, give or take this rounding (magnitudes).
_DEPTH_TOLERANCE = 1e-6

# A tile that hits the row limit is split once, into its four children one
# level finer. A child that hits the limit too is kept to the depth it
# reached; it is not split again.
_CHILDREN_PER_TILE = 4


@dataclass
class FieldGaiaStars:
    """The Gaia stars of one image, and which tiles they came from.

    Attributes
    ----------
    table : `astropy.table.Table` or `None`
        The stars of the complete tiles brighter than `depth`: columns
        ``source_id``, ``ra``, ``dec`` (epoch 2016.0), ``phot_g_mean_mag``,
        ``DESIGNATION``, ``pmra`` and ``pmdec`` (NaN when unknown). `None`
        when no tile is complete.
    coords : `astropy.coordinates.SkyCoord` or `None`
        The positions of `table`, in the same order.
    depth : `float`
        The Gaia G magnitude the tiles were asked for.
    tiles : `list` [`int`]
        Every tile under the image.
    complete_tiles : `list` [`int`]
        The tiles complete to `depth`, whose stars are in `table`.
    missing_tiles : `list` [`int`]
        The tiles not complete to `depth`: never downloaded, failed, cut
        short, or skipped because the run may not go online.
    failed_tiles : `list` [`int`]
        The tiles whose download failed on every service in this call.
    split_tiles : `list` [`int`]
        The tiles that hit the row limit and were fetched as four children.
    skipped_reason : `str`
        Why no download was tried for the missing tiles, or empty.
    """

    table: Table | None
    coords: SkyCoord | None
    depth: float
    tiles: list[int] = field(default_factory=list)
    complete_tiles: list[int] = field(default_factory=list)
    missing_tiles: list[int] = field(default_factory=list)
    failed_tiles: list[int] = field(default_factory=list)
    split_tiles: list[int] = field(default_factory=list)
    skipped_reason: str = ""


def tiles_under_image(wcs: Any, width: int, height: int, margin_deg: float | None = None) -> list[int]:
    """List every cache tile that any part of an image falls in.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The image's plate solution.
    width, height : `int`
        The image size, in pixels.
    margin_deg : `float`, optional
        How far past the edge to include, in degrees. The default is the
        catalog match radius, so a star at the very edge still has every
        catalog star it could match.

    Returns
    -------
    tiles : `list` [`int`]
        The tile numbers at the cache's level, smallest first.
    """
    if margin_deg is None:
        margin_deg = float(resolve(CATALOG_MATCH_RADIUS_ARCSEC).value) / 3600.0
    level = int(resolve(catalog_store.GAIA_TILE_LEVEL).value)
    return tiles_for_footprint(image_corners_on_sky(wcs, width, height), margin_deg, level)


def _cache_rows(table: Table) -> list[tuple]:
    """Turn a tile answer into the rows the cache stores.

    Parameters
    ----------
    table : `astropy.table.Table`
        The stars of one answer, in the column layout of the tile driver.

    Returns
    -------
    rows : `list` [`tuple`]
        ``(source_id, ra, dec, G, designation, pmra, pmdec)`` per star, with
        `None` for an unknown proper motion.
    """

    def _number(value: Any) -> float | None:
        """Give a finite float, or `None`.

        Parameters
        ----------
        value : `Any`
            A table cell.

        Returns
        -------
        number : `float` or `None`
            The value, or `None` when it is masked or not finite.
        """
        try:
            number = float(value)
        except TypeError, ValueError:
            return None
        return number if math.isfinite(number) else None

    return [
        (
            str(row["source_id"]),
            float(row["ra"]),
            float(row["dec"]),
            float(row["phot_g_mean_mag"]),
            str(row["designation"]),
            _number(row["pmra"]),
            _number(row["pmdec"]),
        )
        for row in table
    ]


def _fetch_one_tile(
    config: Any, tile: int, have: float, depth: float, fetch: TileFetcher, level: int
) -> tuple[float, bool]:
    """Download the missing depth of one tile and save it.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tile : `int`
        The tile, at `level`.
    have : `float`
        The depth the cache already holds for this tile (0 for none).
    depth : `float`
        The depth wanted.
    fetch : `TileFetcher`
        Fetches one tile at one level.
    level : `int`
        The cache's tile level.

    Returns
    -------
    reached : `float`
        The depth the tile is now complete to.
    split : `bool`
        True when the tile hit the row limit and was fetched as children.

    The `ExternalServiceError` of a tile or child that failed on every
    service passes through, and nothing of the tile is saved.
    """
    answer = fetch(level, tile, have, depth)
    rows = _cache_rows(answer.table)
    reached = answer.complete_to_g
    service = answer.service
    split = False
    if answer.row_limit_reached:
        split = True
        logger.info("Gaia tile %s hit the row limit; fetching its four children instead.", tile)
        rows = []
        reached = depth
        for child in range(_CHILDREN_PER_TILE * tile, _CHILDREN_PER_TILE * (tile + 1)):
            child_answer = fetch(level + 1, child, have, depth)
            rows.extend(_cache_rows(child_answer.table))
            reached = min(reached, child_answer.complete_to_g)
            service = child_answer.service
    catalog_store.record_tile_fetch(config, tile, reached, rows, service)
    return reached, split


def fill_field_tiles(
    config: Any, tiles: Sequence[int], depth: float, fetch: TileFetcher
) -> tuple[list[int], list[int]]:
    """Download every tile of a list that the cache does not hold to a depth.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tiles : `Sequence` [`int`]
        The tiles, at the cache's level.
    depth : `float`
        The Gaia G magnitude wanted.
    fetch : `TileFetcher`
        Fetches one tile at one level.

    Returns
    -------
    failed : `list` [`int`]
        The tiles whose download failed. Each failure is recorded in the
        cache with its error.
    split : `list` [`int`]
        The tiles fetched as four children because they hit the row limit.
    """
    level = int(resolve(catalog_store.GAIA_TILE_LEVEL).value)
    depths = catalog_store.tile_status(config, tiles)
    failed: list[int] = []
    split_tiles: list[int] = []
    for tile, held in depths.items():
        if held is not None and held >= depth - _DEPTH_TOLERANCE:
            continue
        try:
            _, split = _fetch_one_tile(config, tile, held or 0.0, depth, fetch, level)
        except ExternalServiceError as error:
            catalog_store.record_tile_failure(config, tile, str(error))
            failed.append(tile)
            continue
        if split:
            split_tiles.append(tile)
    return failed, split_tiles


def _stars_table(rows: list[tuple]) -> tuple[Table, SkyCoord]:
    """Build the star table and positions from cached rows.

    Parameters
    ----------
    rows : `list` [`tuple`]
        Cached stars with proper motion (see `catalog_store.stars_in_tiles`).

    Returns
    -------
    table, coords : `astropy.table.Table`, `astropy.coordinates.SkyCoord`
        The stars and their positions at epoch 2016.0.
    """
    ras = np.array([row[1] for row in rows], dtype=float)
    decs = np.array([row[2] for row in rows], dtype=float)
    table = Table(
        [
            [row[0] for row in rows],
            ras,
            decs,
            np.array([np.nan if row[3] is None else row[3] for row in rows], dtype=float),
            [row[4] for row in rows],
            np.array([np.nan if row[5] is None else row[5] for row in rows], dtype=float),
            np.array([np.nan if row[6] is None else row[6] for row in rows], dtype=float),
        ],
        names=["source_id", "ra", "dec", "phot_g_mean_mag", "DESIGNATION", "pmra", "pmdec"],
    )
    return table, SkyCoord(ra=ras * u.deg, dec=decs * u.deg)


def load_field_gaia_stars(
    config: Any,
    tiles: Sequence[int],
    depth: float,
    fetch: TileFetcher | None,
    *,
    skipped_reason: str = "",
) -> FieldGaiaStars:
    """Fill the tiles of an image and read the stars of the complete ones.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tiles : `Sequence` [`int`]
        The tiles under the image (see `tiles_under_image`).
    depth : `float`
        The Gaia G magnitude wanted.
    fetch : `TileFetcher` or `None`
        Fetches one tile. `None` reads the cache alone.
    skipped_reason : `str`, optional
        Why `fetch` is `None`, kept in the answer.

    Returns
    -------
    stars : `FieldGaiaStars`
        The stars, and which tiles are complete, missing or failed.
    """
    tile_list = [int(tile) for tile in tiles]
    failed: list[int] = []
    split_tiles: list[int] = []
    if fetch is not None:
        failed, split_tiles = fill_field_tiles(config, tile_list, depth, fetch)
    missing = catalog_store.missing_tiles(config, tile_list, depth)
    missing_set = set(missing)
    complete = [tile for tile in tile_list if tile not in missing_set]
    table = coords = None
    if complete:
        rows = catalog_store.stars_in_tiles(config, complete, depth, include_proper_motion=True)
        if rows:
            table, coords = _stars_table(rows)
    return FieldGaiaStars(
        table=table,
        coords=coords,
        depth=depth,
        tiles=tile_list,
        complete_tiles=complete,
        missing_tiles=missing,
        failed_tiles=failed,
        split_tiles=split_tiles,
        skipped_reason=skipped_reason if fetch is None and missing else "",
    )
