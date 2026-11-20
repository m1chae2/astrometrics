"""The one place that opens the local Gaia star catalog cache database.

Downloading star positions from the internet (the Gaia DR3 catalog) is
slow, so astrometricslib keeps a copy on disk in a SQLite database. This
file is the only place that opens that database file directly. Everything
else asks these functions for cached stars or tells them to save some,
instead of running SQL itself.

The cache is cut into sky tiles. A tile is one equal-area piece of the sky
(see ``sky_tiles``), and a Gaia star's ID tells which tile it is in. The
database has these tables:

- ``gaia_stars``: one row per star we have downloaded, keyed by its Gaia
  source ID, with the tile it is in, its position at Gaia's reference epoch
  (2016.0), its G magnitude and, when Gaia measured it, its proper motion
  (how far the star moves across the sky each year).
- ``gaia_tiles``: one row per tile we have tried to download. It says how
  deep the tile is complete (every star brighter than ``complete_to_g`` is
  in ``gaia_stars``), where the answer came from, and when and why the last
  attempt failed. A later request for a fainter depth fetches only the
  missing fainter stars of the tile.
- ``cached_regions`` and ``gaia_cache_regions``: the older circle records.
  The circle functions (`is_gaia_region_complete`, `record_gaia_region`) still
  use ``gaia_cache_regions`` until the star identifier works in tiles.

``PRAGMA user_version`` holds the schema version. A file from before tiles
existed (version 0, with a ``gaia_sources`` table) is treated as empty: the
tile tables are created next to the old ones and nothing is converted, so
the stars download again.
"""

import logging
import math
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.gaia_cone_driver import separations_degrees, tile_outline_points
from astrometricslib._internal.drivers.sky_tiles import tile_of_source_id, tiles_for_cone
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import catalog_cache_directory
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

logger = logging.getLogger(__name__)

_CATALOG_DB_FILENAME = "catalog_cache.db"

# The schema version this file writes into ``PRAGMA user_version``.
_SCHEMA_VERSION = 2

# The HEALPix level of a cache tile. At level 7 a tile covers about 0.21
# square degrees and is about 0.46 degrees across. Dense Cygnus fields hold
# about 13,000 to 30,000 stars per tile down to G = 21, which stays under the
# 50,000-row GAIA_ROW_LIMIT of one download; level 6 tiles (four times the
# area) would exceed it. A design estimate, not yet measured on a full
# download. Changing the level makes every stored tile number wrong, so the
# cache file must be deleted and rebuilt after a change.
GAIA_TILE_LEVEL = TunedParameter(
    name="astrometry.gaia_tile_level",
    default=7,
    unit="HEALPix level",
    description="How finely the Gaia cache cuts the sky into tiles (level 7: about 0.21 square degrees).",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="AST-D22",
)

# A tile counts as complete to a depth when its recorded depth is at least
# the wanted depth, give or take rounding of this size (magnitudes).
_DEPTH_TOLERANCE = 1e-6

# The most tile numbers put into one SQL statement (SQLite allows 999 values).
_SQL_CHUNK = 500

__all__ = [
    "GAIA_TILE_LEVEL",
    "get_catalog_cache_path",
    "insert_gaia_sources",
    "is_gaia_region_complete",
    "is_region_cached",
    "mark_region_cached",
    "missing_tiles",
    "query_gaia_sources_in_bounds",
    "record_gaia_region",
    "record_tile_failure",
    "record_tile_fetch",
    "stars_by_source_id",
    "stars_in_tiles",
    "summarize_catalog_coverage",
    "tile_status",
]


def get_catalog_cache_path(config: Any) -> Path:
    """Find where the local Gaia catalog cache database lives on disk.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. Unused: the cache is in the shared catalog
        cache folder, not in the library.

    Returns
    -------
    path : `pathlib.Path`
        The full path to the cache database file, which can be deleted and
        downloaded again. The file may not exist
        yet -- that just means nothing has been cached.
    """
    return Path(catalog_cache_directory()) / _CATALOG_DB_FILENAME


# A recorded region covers a request if the request circle lies inside it.
# The small allowance (about 0.004 arcseconds) keeps rounding error in the
# angle maths from rejecting a region that covers the request exactly.
_COVERAGE_TOLERANCE_DEG = 1e-6


def _create_tables(connection: sqlite3.Connection) -> None:
    """Create every table of the current schema if it is missing.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gaia_stars (
            source_id INTEGER PRIMARY KEY,
            tile INTEGER NOT NULL,
            ra REAL,
            dec REAL,
            g REAL,
            pmra REAL,
            pmdec REAL
        )
    """)
    connection.execute("CREATE INDEX IF NOT EXISTS gaia_stars_tile_g ON gaia_stars (tile, g)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gaia_tiles (
            tile INTEGER PRIMARY KEY,
            complete_to_g REAL,
            row_count INTEGER,
            service TEXT,
            fetched_at TEXT,
            last_attempt_at TEXT,
            last_error TEXT,
            attempts INTEGER NOT NULL DEFAULT 0
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS cached_regions (
            region_key TEXT PRIMARY KEY,
            ra REAL,
            dec REAL,
            radius REAL
        )
    """)
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gaia_cache_regions (
            region_key TEXT PRIMARY KEY,
            ra REAL NOT NULL,
            dec REAL NOT NULL,
            radius REAL NOT NULL,
            magnitude_limit REAL,
            row_limit_hit INTEGER NOT NULL
        )
    """)


def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    """Check whether the database has a table of this name.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.
    name : `str`
        The table name.

    Returns
    -------
    exists : `bool`
        True if the table exists.
    """
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
    ).fetchone()
    return row is not None


def _tiles_inside_cone(ra: float, dec: float, radius: float, level: int) -> list[int]:
    """List the tiles that lie wholly inside a circle of sky.

    A tile counts as inside when its center, its four corners and its four
    edge midpoints are all inside the circle.

    Parameters
    ----------
    ra, dec : `float`
        The circle's center, in degrees.
    radius : `float`
        The circle's radius, in degrees.
    level : `int`
        The tile level.

    Returns
    -------
    tiles : `list` [`int`]
        The tile numbers.
    """
    inside = []
    for tile in tiles_for_cone(ra, dec, radius, level):
        outline_ra, outline_dec = tile_outline_points(level, tile)
        if (
            float(np.max(separations_degrees(ra, dec, outline_ra, outline_dec)))
            <= radius + _COVERAGE_TOLERANCE_DEG
        ):
            inside.append(tile)
    return inside


def _raise_tile_depths(connection: sqlite3.Connection, tiles: Iterable[int], depth: float) -> None:
    """Mark tiles as complete to a depth, never lowering a recorded depth.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.
    tiles : `iterable` of `int`
        The tile numbers.
    depth : `float`
        The depth, as a Gaia G magnitude.
    """
    for tile in tiles:
        connection.execute(
            "INSERT INTO gaia_tiles (tile, complete_to_g, row_count, attempts) "
            "VALUES (?, ?, (SELECT COUNT(*) FROM gaia_stars WHERE tile = ?), 0) "
            "ON CONFLICT(tile) DO UPDATE SET "
            "complete_to_g = MAX(IFNULL(complete_to_g, ?), ?), "
            "row_count = (SELECT COUNT(*) FROM gaia_stars WHERE tile = ?)",
            (tile, depth, tile, depth, depth, tile),
        )


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Make sure the tile tables exist and the schema version is set.

    Safe to call every time a connection is opened: a file already at the
    current version is left alone after one cheap check. A file with any
    other version, such as one from before tiles existed, is treated as
    empty: the tile tables are created next to whatever it holds and
    nothing is converted or copied. The stars then download again.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.
    """
    if connection.execute("PRAGMA user_version").fetchone()[0] == _SCHEMA_VERSION:
        return
    connection.execute("BEGIN IMMEDIATE")
    _create_tables(connection)
    connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
    connection.commit()


def is_region_cached(config: Any, region_key: str) -> bool:
    """Check whether we have already downloaded stars for one patch of sky.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    region_key : `str`
        The patch of sky's cache key, built from its center and radius.

    Returns
    -------
    cached : `bool`
        `True` if this region has already been downloaded and saved.
    """
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        cursor = connection.execute("SELECT 1 FROM cached_regions WHERE region_key = ?", (region_key,))
        return cursor.fetchone() is not None
    finally:
        connection.close()


def mark_region_cached(config: Any, region_key: str, ra: float, dec: float, radius: float) -> None:
    """Record that one patch of sky has been downloaded, to skip it next time.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    region_key : `str`
        The patch of sky's cache key, built from its center and radius.
    ra, dec : `float`
        The center of the patch, in degrees.
    radius : `float`
        How wide the patch is, in degrees.
    """
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO cached_regions (region_key, ra, dec, radius) VALUES (?, ?, ?, ?)",
            (region_key, ra, dec, radius),
        )
        connection.commit()
    finally:
        connection.close()


def record_gaia_region(
    config: Any,
    ra: float,
    dec: float,
    radius: float,
    magnitude_limit: float,
    row_limit_hit: bool,
) -> None:
    """Record how one downloaded patch of sky was fetched.

    Call this after the stars of the download are saved. Recording the same
    centre, radius and magnitude limit again replaces the earlier record. A
    download that did not hit the row limit also marks every tile wholly
    inside the circle as complete to the magnitude limit.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    ra, dec : `float`
        The centre of the patch, in degrees.
    radius : `float`
        The radius of the patch, in degrees.
    magnitude_limit : `float`
        The download asked only for stars brighter than this Gaia G
        magnitude.
    row_limit_hit : `bool`
        `True` if the download returned as many rows as it was allowed. The
        faintest stars may then be missing, so the patch is never reused as
        complete.
    """
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        region_key = f"{ra:.5f}_{dec:.5f}_{radius:.4f}_{magnitude_limit:.2f}"
        connection.execute(
            "INSERT OR REPLACE INTO gaia_cache_regions "
            "(region_key, ra, dec, radius, magnitude_limit, row_limit_hit) VALUES (?, ?, ?, ?, ?, ?)",
            (region_key, ra, dec, radius, magnitude_limit, int(bool(row_limit_hit))),
        )
        if not row_limit_hit:
            _raise_tile_depths(
                connection,
                _tiles_inside_cone(ra, dec, radius, int(resolve(GAIA_TILE_LEVEL).value)),
                magnitude_limit,
            )
        connection.commit()
    finally:
        connection.close()


def _angular_separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    """Measure the angle between two points on the sky.

    Uses the haversine formula, which stays accurate for small angles and
    handles Right Ascension (RA) wrapping from 360 degrees back to 0.

    Parameters
    ----------
    ra1, dec1, ra2, dec2 : `float`
        The two positions, in degrees.

    Returns
    -------
    separation : `float`
        The angle between the positions, in degrees.
    """
    phi1 = math.radians(dec1)
    phi2 = math.radians(dec2)
    half_delta_ra = math.radians(ra2 - ra1) / 2.0
    half_delta_dec = (phi2 - phi1) / 2.0
    haversine = math.sin(half_delta_dec) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_delta_ra) ** 2
    return math.degrees(2.0 * math.asin(min(1.0, math.sqrt(haversine))))


def is_gaia_region_complete(
    config: Any,
    ra: float,
    dec: float,
    radius: float,
    magnitude_limit: float,
) -> bool:
    """Check whether the cache holds a complete answer for one request.

    The answer is complete when one recorded download meets all three
    conditions: its circle contains the whole requested circle, its
    magnitude limit is at least as faint as the request's, and it did not
    hit the row limit. Two smaller downloads that together cover the request
    do not count. The check errs toward downloading again. The request is
    also complete when every tile it touches is complete to the depth.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    ra, dec : `float`
        The centre of the request, in degrees.
    radius : `float`
        The radius of the request, in degrees.
    magnitude_limit : `float`
        The request wants stars brighter than this Gaia G magnitude.

    Returns
    -------
    complete : `bool`
        `True` if the cached stars can stand in for a fresh download.
    """
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        cursor = connection.execute(
            "SELECT ra, dec, radius FROM gaia_cache_regions "
            "WHERE row_limit_hit = 0 AND magnitude_limit IS NOT NULL AND magnitude_limit >= ?",
            (magnitude_limit - _COVERAGE_TOLERANCE_DEG,),
        )
        recorded_regions = cursor.fetchall()
    finally:
        connection.close()

    for region_ra, region_dec, region_radius in recorded_regions:
        separation = _angular_separation_deg(ra, dec, region_ra, region_dec)
        if separation + radius <= region_radius + _COVERAGE_TOLERANCE_DEG:
            return True
    # Tiles filled by tile downloads also answer, with no circle record.
    return not missing_tiles(
        config, tiles_for_cone(ra, dec, radius, int(resolve(GAIA_TILE_LEVEL).value)), magnitude_limit
    )


def _star_row(row: Sequence[Any], level: int) -> tuple:
    """Turn one star tuple into a ``gaia_stars`` row.

    Parameters
    ----------
    row : `Sequence`
        ``(source_id, ra, dec, phot_g_mean_mag)``, then optionally a name,
        ``pmra`` and ``pmdec``. The name is not stored: it is always
        ``"Gaia DR3 <source_id>"``.
    level : `int`
        The tile level.

    Returns
    -------
    stored : `tuple`
        ``(source_id, tile, ra, dec, g, pmra, pmdec)``.
    """
    full = tuple(row) + (None,) * (7 - len(row))
    number = int(full[0])
    return (number, int(tile_of_source_id(number, level)), full[1], full[2], full[3], full[5], full[6])


def _star_tuple(row: Sequence[Any], include_proper_motion: bool) -> tuple:
    """Turn one ``gaia_stars`` row into the tuple callers read.

    Parameters
    ----------
    row : `Sequence`
        ``(source_id, ra, dec, g, pmra, pmdec)``.
    include_proper_motion : `bool`
        Add ``pmra`` and ``pmdec`` at the end.

    Returns
    -------
    star : `tuple`
        ``(source_id, ra, dec, g, designation)`` with the source ID as text,
        as the cache has always returned it, plus the proper motion if asked.
    """
    source_id = str(row[0])
    star = (source_id, row[1], row[2], row[3], f"Gaia DR3 {source_id}")
    return (*star, row[4], row[5]) if include_proper_motion else star


_STAR_COLUMNS = "source_id, ra, dec, g, pmra, pmdec"


def insert_gaia_sources(
    config: Any,
    rows: list[
        tuple[str, float, float, float, str]
        | tuple[str, float, float, float, str, float | None, float | None]
    ],
) -> None:
    """Save a batch of stars to the local cache.

    This stores stars only. It does not say that any tile is complete; use
    `record_tile_fetch` for that.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    rows : `list` of `tuple`
        One tuple per star: ``(source_id, ra, dec, phot_g_mean_mag,
        designation)``, optionally followed by ``pmra`` and ``pmdec``, the
        proper motion in milliarcseconds per year (``pmra`` already includes
        the cosine of the declination, as in the Gaia catalog). A tuple
        without them, or with `None`, stores the proper motion as unknown.
        Saving a star that is already cached replaces the old row with the
        new one.
    """
    level = int(resolve(GAIA_TILE_LEVEL).value)
    stored = [_star_row(row, level) for row in rows]
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        connection.executemany("INSERT OR REPLACE INTO gaia_stars VALUES (?, ?, ?, ?, ?, ?, ?)", stored)
        connection.commit()
    finally:
        connection.close()


def query_gaia_sources_in_bounds(
    config: Any,
    min_ra: float,
    max_ra: float,
    min_dec: float,
    max_dec: float,
    *,
    include_proper_motion: bool = False,
) -> list[tuple]:
    """Look up every cached star inside a rectangular box of sky.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    min_ra, max_ra, min_dec, max_dec : `float`
        The edges of the search box, in degrees.
    include_proper_motion : `bool`, optional
        Add ``pmra`` and ``pmdec`` to the end of each tuple.

    Returns
    -------
    rows : `list` of `tuple`
        One tuple per star found: ``(source_id, ra, dec,
        phot_g_mean_mag, designation)``, followed by ``pmra`` and ``pmdec``
        (milliarcseconds per year, `None` when unknown) if
        `include_proper_motion` is set. Empty if none are cached in this
        box yet.
    """
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        cursor = connection.execute(
            f"SELECT {_STAR_COLUMNS} FROM gaia_stars WHERE ra >= ? AND ra <= ? AND dec >= ? AND dec <= ?",  # ruff: ignore[hardcoded-sql-expression]
            (min_ra, max_ra, min_dec, max_dec),
        )
        return [_star_tuple(row, include_proper_motion) for row in cursor.fetchall()]
    finally:
        connection.close()


def tile_status(config: Any, tiles: Iterable[int]) -> dict[int, float | None]:
    """Look up how deep each tile is complete.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tiles : `iterable` of `int`
        Tile numbers at the `GAIA_TILE_LEVEL` level.

    Returns
    -------
    depths : `dict` [`int`, `float` or `None`]
        For each tile, the depth to which every star is cached (a Gaia G
        magnitude), or `None` when no download of the tile has succeeded.
    """
    wanted = [int(tile) for tile in tiles]
    depths: dict[int, float | None] = dict.fromkeys(wanted)
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        for start in range(0, len(wanted), _SQL_CHUNK):
            chunk = wanted[start : start + _SQL_CHUNK]
            marks = ",".join("?" * len(chunk))
            for tile, depth in connection.execute(
                f"SELECT tile, complete_to_g FROM gaia_tiles WHERE tile IN ({marks})",  # ruff: ignore[hardcoded-sql-expression]
                chunk,
            ):
                depths[int(tile)] = depth
    finally:
        connection.close()
    return depths


def missing_tiles(config: Any, tiles: Iterable[int], g_wanted: float) -> list[int]:
    """List the tiles that are not yet complete to a depth.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tiles : `iterable` of `int`
        Tile numbers at the `GAIA_TILE_LEVEL` level.
    g_wanted : `float`
        The depth wanted, as a Gaia G magnitude.

    Returns
    -------
    missing : `list` [`int`]
        The tiles never downloaded or complete only to a shallower depth, in
        the order given, without repeats.
    """
    depths = tile_status(config, tiles)
    return [tile for tile, depth in depths.items() if depth is None or depth < g_wanted - _DEPTH_TOLERANCE]


def stars_in_tiles(
    config: Any, tiles: Iterable[int], g_max: float, *, include_proper_motion: bool = False
) -> list[tuple]:
    """Read the cached stars of some tiles down to a depth.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tiles : `iterable` of `int`
        Tile numbers at the `GAIA_TILE_LEVEL` level.
    g_max : `float`
        Only stars brighter than this G magnitude are returned.
    include_proper_motion : `bool`, optional
        Add ``pmra`` and ``pmdec`` to the end of each tuple.

    Returns
    -------
    rows : `list` of `tuple`
        The same tuples as `query_gaia_sources_in_bounds`. This says nothing
        about completeness; ask `tile_status` or `missing_tiles` for that.
    """
    wanted = [int(tile) for tile in tiles]
    found: list[tuple] = []
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        for start in range(0, len(wanted), _SQL_CHUNK):
            chunk = wanted[start : start + _SQL_CHUNK]
            marks = ",".join("?" * len(chunk))
            cursor = connection.execute(
                f"SELECT {_STAR_COLUMNS} FROM gaia_stars WHERE tile IN ({marks}) AND g < ?",  # ruff: ignore[hardcoded-sql-expression]
                [*chunk, g_max],
            )
            found.extend(_star_tuple(row, include_proper_motion) for row in cursor.fetchall())
    finally:
        connection.close()
    return found


def stars_by_source_id(
    config: Any, source_ids: Iterable[int | str], *, include_proper_motion: bool = False
) -> list[tuple]:
    """Read cached stars by their Gaia source IDs.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    source_ids : `iterable` of `int` or `str`
        The Gaia DR3 source IDs.
    include_proper_motion : `bool`, optional
        Add ``pmra`` and ``pmdec`` to the end of each tuple.

    Returns
    -------
    rows : `list` of `tuple`
        The tuples of `query_gaia_sources_in_bounds`, for the IDs that are
        cached. IDs not cached are left out.
    """
    wanted = [int(source_id) for source_id in source_ids]
    found: list[tuple] = []
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        for start in range(0, len(wanted), _SQL_CHUNK):
            chunk = wanted[start : start + _SQL_CHUNK]
            marks = ",".join("?" * len(chunk))
            cursor = connection.execute(
                f"SELECT {_STAR_COLUMNS} FROM gaia_stars WHERE source_id IN ({marks})",  # ruff: ignore[hardcoded-sql-expression]
                chunk,
            )
            found.extend(_star_tuple(row, include_proper_motion) for row in cursor.fetchall())
    finally:
        connection.close()
    return found


def record_tile_fetch(config: Any, tile: int, complete_to_g: float, rows: list[tuple], service: str) -> None:
    """Save the stars of one tile download and record how deep it is complete.

    The stars and the tile record are saved in one transaction, so the cache
    never says a tile is complete without its stars. A tile's recorded depth
    never goes down: a later, shallower answer adds stars but keeps the
    deeper record. A success clears the tile's last error.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tile : `int`
        The tile number.
    complete_to_g : `float`
        Every star of the tile brighter than this Gaia G magnitude is now
        cached. When the download hit the row limit, this is the faintest G
        reached, not the depth asked for.
    rows : `list` of `tuple`
        The stars downloaded, in the form `insert_gaia_sources` takes.
    service : `str`
        The service that answered.
    """
    from datetime import UTC, datetime

    level = int(resolve(GAIA_TILE_LEVEL).value)
    stored = [_star_row(row, level) for row in rows]
    now = datetime.now(UTC).isoformat(timespec="seconds")
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        connection.executemany("INSERT OR REPLACE INTO gaia_stars VALUES (?, ?, ?, ?, ?, ?, ?)", stored)
        connection.execute(
            "INSERT INTO gaia_tiles (tile, complete_to_g, row_count, service, fetched_at, last_attempt_at, "
            "last_error, attempts) VALUES "
            "(?, ?, (SELECT COUNT(*) FROM gaia_stars WHERE tile = ?), ?, ?, ?, NULL, 0) "
            "ON CONFLICT(tile) DO UPDATE SET complete_to_g = MAX(IFNULL(complete_to_g, ?), ?), "
            "row_count = (SELECT COUNT(*) FROM gaia_stars WHERE tile = ?), service = ?, fetched_at = ?, "
            "last_attempt_at = ?, last_error = NULL, attempts = 0",
            (
                tile,
                complete_to_g,
                tile,
                service,
                now,
                now,
                complete_to_g,
                complete_to_g,
                tile,
                service,
                now,
                now,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def record_tile_failure(config: Any, tile: int, error: str) -> None:
    """Record that a download of one tile failed.

    The tile's depth and stars are left as they were.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    tile : `int`
        The tile number.
    error : `str`
        What went wrong, in words.
    """
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat(timespec="seconds")
    connection = connect_db(str(get_catalog_cache_path(config)))
    try:
        _ensure_schema(connection)
        connection.execute(
            "INSERT INTO gaia_tiles (tile, last_attempt_at, last_error, attempts) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(tile) DO UPDATE SET last_attempt_at = ?, last_error = ?, attempts = attempts + 1",
            (tile, now, error, now, error),
        )
        connection.commit()
    finally:
        connection.close()


def summarize_catalog_coverage(config: Any = None) -> dict[str, Any]:
    """Check how many stars we already have saved on our hard drive.

    Parameters
    ----------
    config : `AppConfiguration`, optional
        The system settings (so we know where the database file is).
        Uses the default application settings if not given.

    Returns
    -------
    coverage : `dict`
        Stats about our database: where it is, how many stars it holds,
        and how much disk space it takes up.
    """
    if config is None:
        from astrometricslib.foundation.config import get_configuration

        config = get_configuration()

    cache_path = get_catalog_cache_path(config)
    coverage: dict[str, Any] = {
        "cache_path": str(cache_path),
        "exists": os.path.exists(cache_path),
        "source_count": 0,
        "region_count": 0,
        "size_megabytes": 0.0,
    }
    if not coverage["exists"]:
        return coverage

    coverage["size_megabytes"] = round(os.path.getsize(cache_path) / 1_000_000, 2)
    try:
        connection = sqlite3.connect(f"file:{cache_path}?mode=ro", uri=True)
        try:
            if _table_exists(connection, "gaia_stars"):
                coverage["source_count"] = connection.execute("SELECT COUNT(*) FROM gaia_stars").fetchone()[0]
                coverage["region_count"] = connection.execute(
                    "SELECT COUNT(*) FROM gaia_tiles WHERE complete_to_g IS NOT NULL"
                ).fetchone()[0]
            else:
                # Not yet converted to tiles; converted on first use.
                coverage["source_count"] = connection.execute("SELECT COUNT(*) FROM gaia_sources").fetchone()[
                    0
                ]
                coverage["region_count"] = connection.execute(
                    "SELECT COUNT(*) FROM cached_regions"
                ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as cache_error:
        # A cache that has never been written has no tables yet, which
        # is an empty result rather than a failure worth raising.
        logger.debug("Could not read local catalog cache: %s", cache_error)

    return coverage
