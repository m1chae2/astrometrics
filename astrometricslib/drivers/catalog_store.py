"""The one place that opens the local Gaia star catalog cache database.

Downloading star positions from the internet (the Gaia DR3 catalog) is
slow, so astrometricslib keeps a small copy on disk in a SQLite
database once it has looked a region of sky up. This file is the only
place that opens that database file directly. Everything else asks
these functions for cached stars or tells them to save some, instead
of running SQL itself.

The database has three tables:

- ``gaia_sources``: one row per star we have downloaded, keyed by its
  Gaia source ID. Each row holds the star's position at Gaia's reference
  epoch (2016.0), its G magnitude and, when Gaia measured it, its proper
  motion (how far the star moves across the sky each year).
- ``cached_regions``: one row per circular patch of sky we have already
  downloaded. It is the original record and says nothing about how deep the
  download went or whether it was cut short, so the lookup no longer uses it.
- ``gaia_cache_regions``: one row per downloaded patch of sky, with the
  faintest magnitude the download asked for and whether it hit the row
  limit. A patch counts as complete for a later request only when a row here
  covers the request, is at least as deep, and was not cut short. A cache
  file from before this table existed has no such rows, so none of its stars
  count as complete.
"""

import logging
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

from astrometricslib.foundation.storage.local_database import connect_db

logger = logging.getLogger(__name__)

_CATALOG_DB_FILENAME = "catalog_cache.db"

__all__ = [
    "get_catalog_cache_path",
    "insert_gaia_sources",
    "is_gaia_region_complete",
    "is_region_cached",
    "mark_region_cached",
    "query_gaia_sources_in_bounds",
    "record_gaia_region",
    "summarize_catalog_coverage",
]


def get_catalog_cache_path(config: Any) -> Path:
    """Find where the local Gaia catalog cache database lives on disk.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings, used to find the library's data folder.

    Returns
    -------
    path : `pathlib.Path`
        The full path to the cache database file. The file may not exist
        yet -- that just means nothing has been cached.
    """
    return config.get_library_path() / "catalogs" / _CATALOG_DB_FILENAME


# A recorded region covers a request if the request circle lies inside it.
# The small allowance (about 0.004 arcseconds) keeps rounding error in the
# angle maths from rejecting a region that covers the request exactly.
_COVERAGE_TOLERANCE_DEG = 1e-6


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the cache's three tables if they don't already exist.

    Safe to call every time a connection is opened -- `CREATE TABLE IF
    NOT EXISTS` does nothing when the tables are already there. A cache
    file written before proper motions were stored has no ``pmra`` or
    ``pmdec`` column, so this adds them. The rows already in the file get
    ``NULL`` there, which means "proper motion unknown". A cache file
    written before ``gaia_cache_regions`` existed gets that empty table
    added. Its stars stay, but no region is recorded for them, so they are
    never treated as a complete download.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.

    Raises
    ------
    sqlite3.OperationalError
        If adding a missing column fails for any reason other than another
        connection having added it first.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gaia_sources (
            source_id TEXT PRIMARY KEY,
            ra REAL,
            dec REAL,
            phot_g_mean_mag REAL,
            designation TEXT,
            pmra REAL,
            pmdec REAL
        )
    """)
    existing_columns = {row[1] for row in connection.execute("PRAGMA table_info(gaia_sources)")}
    for column in ("pmra", "pmdec"):
        if column not in existing_columns:
            try:
                connection.execute(f"ALTER TABLE gaia_sources ADD COLUMN {column} REAL")
            except sqlite3.OperationalError as alter_error:
                # Another connection added the column between the check above
                # and this statement. Any other failure is a real error.
                if "duplicate column" not in str(alter_error).lower():
                    raise
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
    centre, radius and magnitude limit again replaces the earlier record.

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
    do not count. The check errs toward downloading again.

    A cache file written before ``gaia_cache_regions`` existed has no
    records, so this returns `False` for every request until the region is
    downloaded again.

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
    return False


def insert_gaia_sources(
    config: Any,
    rows: list[
        tuple[str, float, float, float, str]
        | tuple[str, float, float, float, str, float | None, float | None]
    ],
) -> None:
    """Save a batch of stars to the local cache.

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
    full_rows = [tuple(row) + (None,) * (7 - len(row)) for row in rows]
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        connection.executemany(
            """
            INSERT OR REPLACE INTO gaia_sources
                (source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            full_rows,
        )
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
    cache_db_path = get_catalog_cache_path(config)
    connection = connect_db(str(cache_db_path))
    try:
        _ensure_schema(connection)
        box = (min_ra, max_ra, min_dec, max_dec)
        if include_proper_motion:
            cursor = connection.execute(
                "SELECT source_id, ra, dec, phot_g_mean_mag, designation, pmra, pmdec "
                "FROM gaia_sources WHERE ra >= ? AND ra <= ? AND dec >= ? AND dec <= ?",
                box,
            )
        else:
            cursor = connection.execute(
                "SELECT source_id, ra, dec, phot_g_mean_mag, designation "
                "FROM gaia_sources WHERE ra >= ? AND ra <= ? AND dec >= ? AND dec <= ?",
                box,
            )
        return cursor.fetchall()
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
            coverage["source_count"] = connection.execute("SELECT COUNT(*) FROM gaia_sources").fetchone()[0]
            coverage["region_count"] = connection.execute("SELECT COUNT(*) FROM cached_regions").fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as cache_error:
        # A cache that has never been written has no tables yet, which
        # is an empty result rather than a failure worth raising.
        logger.debug("Could not read local catalog cache: %s", cache_error)

    return coverage
