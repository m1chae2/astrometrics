"""The one place that opens the local Gaia star catalog cache database.

Downloading star positions from the internet (the Gaia DR3 catalog) is
slow, so astrometricslib keeps a small copy on disk in a SQLite
database once it has looked a region of sky up. This file is the only
place that opens that database file directly. Everything else asks
these functions for cached stars or tells them to save some, instead
of running SQL itself.

The database has two tables:

- ``gaia_sources``: one row per star we have downloaded, keyed by its
  Gaia source ID.
- ``cached_regions``: one row per circular patch of sky we have already
  downloaded, so we know not to download it again.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CATALOG_DB_FILENAME = "catalog_cache.db"

__all__ = [
    "get_catalog_cache_path",
    "insert_gaia_sources",
    "is_region_cached",
    "mark_region_cached",
    "query_gaia_sources_in_bounds",
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


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the cache's two tables if they don't already exist.

    Safe to call every time a connection is opened -- `CREATE TABLE IF
    NOT EXISTS` does nothing when the tables are already there.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the cache database.
    """
    connection.execute("""
        CREATE TABLE IF NOT EXISTS gaia_sources (
            source_id TEXT PRIMARY KEY,
            ra REAL,
            dec REAL,
            phot_g_mean_mag REAL,
            designation TEXT
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
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
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
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        connection.execute(
            "INSERT OR REPLACE INTO cached_regions (region_key, ra, dec, radius) VALUES (?, ?, ?, ?)",
            (region_key, ra, dec, radius),
        )
        connection.commit()
    finally:
        connection.close()


def insert_gaia_sources(config: Any, rows: list[tuple[str, float, float, float, str]]) -> None:
    """Save a batch of stars to the local cache.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    rows : `list` of `tuple`
        One tuple per star: ``(source_id, ra, dec, phot_g_mean_mag,
        designation)``. Saving a star that is already cached replaces
        the old row with the new one.
    """
    cache_db_path = get_catalog_cache_path(config)
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        connection.executemany(
            """
            INSERT OR REPLACE INTO gaia_sources (source_id, ra, dec, phot_g_mean_mag, designation)
            VALUES (?, ?, ?, ?, ?)
        """,
            rows,
        )
        connection.commit()
    finally:
        connection.close()


def query_gaia_sources_in_bounds(
    config: Any, min_ra: float, max_ra: float, min_dec: float, max_dec: float
) -> list[tuple[str, float, float, float, str]]:
    """Look up every cached star inside a rectangular box of sky.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    min_ra, max_ra, min_dec, max_dec : `float`
        The edges of the search box, in degrees.

    Returns
    -------
    rows : `list` of `tuple`
        One tuple per star found: ``(source_id, ra, dec,
        phot_g_mean_mag, designation)``. Empty if none are cached in
        this box yet.
    """
    cache_db_path = get_catalog_cache_path(config)
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        cursor = connection.execute(
            "SELECT source_id, ra, dec, phot_g_mean_mag, designation "
            "FROM gaia_sources WHERE ra >= ? AND ra <= ? AND dec >= ? AND dec <= ?",
            (min_ra, max_ra, min_dec, max_dec),
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
        from astrometricslib.utilities.config_loader import get_configuration

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
