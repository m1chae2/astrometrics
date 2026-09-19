"""The one place that opens the local Gaia star catalog cache database.

Downloading star positions from the internet (the Gaia DR3 catalog) is
slow, so astrometricslib keeps a small copy on disk in a SQLite
database once it has looked a region of sky up. This file is the only
place that opens that database file directly. Everything else asks
these functions for cached stars or tells them to save some, instead
of running SQL itself.

The database has four tables, in two separate pairs:

- ``gaia_sources``: one row per star that star identification has
  downloaded, keyed by its Gaia source ID.
- ``cached_regions``: one row per circular patch of sky that star
  identification has already downloaded, so we know not to download it
  again.
- ``planetarium_sources`` and ``planetarium_regions``: the same two
  ideas, but for stars the Planetarium looked up to draw its sky map.

The Planetarium keeps its own pair on purpose. Star identification
counts any five or more stars in a box of ``gaia_sources`` as a
complete answer for that box, so if the Planetarium saved its
partial, brightest-stars-only results there, identification would stop
downloading the fainter stars it needs. The Planetarium may *read*
``gaia_sources`` (stars there are real), but only writes its own pair.
"""

import logging
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CATALOG_DB_FILENAME = "catalog_cache.db"

__all__ = [
    "PIPELINE_CACHE_MAGNITUDE_LIMIT",
    "find_planetarium_stars",
    "get_catalog_cache_path",
    "insert_gaia_sources",
    "is_region_cached",
    "mark_region_cached",
    "query_gaia_sources_in_bounds",
    "store_planetarium_region",
    "summarize_catalog_coverage",
]

# Star identification downloads every Gaia star brighter than this
# magnitude for each region it records in ``cached_regions``. It matches
# DEFAULT_MAGNITUDE_LIMIT in pipelines/astrometry/catalog_seeding.py and the
# default in StarIdentifier._seed_gaia_cache_for_field, which write those
# rows. That is what lets the Planetarium trust such a region as complete
# down to this depth.
PIPELINE_CACHE_MAGNITUDE_LIMIT = 18.0

# Angular slack, in degrees, when checking that one circle of sky lies
# inside another. It only absorbs rounding error, so a region that fits
# exactly is not rejected because of the last decimal place.
_CONTAINMENT_TOLERANCE_DEGREES = 1e-6


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
    connection.execute("""
        CREATE TABLE IF NOT EXISTS planetarium_sources (
            source_id TEXT PRIMARY KEY,
            ra REAL,
            dec REAL,
            phot_g_mean_mag REAL,
            designation TEXT
        )
    """)
    # ``magnitude_limit`` is how faint this region is complete to: every
    # Gaia star at least that bright inside the circle has been saved.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS planetarium_regions (
            region_key TEXT PRIMARY KEY,
            ra REAL,
            dec REAL,
            radius REAL,
            magnitude_limit REAL
        )
    """)
    # Box searches filter on declination first, so an index on it keeps
    # them from reading every row.
    connection.execute("CREATE INDEX IF NOT EXISTS idx_gaia_sources_dec ON gaia_sources (dec)")
    connection.execute("CREATE INDEX IF NOT EXISTS idx_planetarium_sources_dec ON planetarium_sources (dec)")
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


def _angular_separation_degrees(ra_one: float, dec_one: float, ra_two: float, dec_two: float) -> float:
    """Measure the angle between two points on the sky.

    Parameters
    ----------
    ra_one, dec_one : `float`
        The first point, in degrees.
    ra_two, dec_two : `float`
        The second point, in degrees.

    Returns
    -------
    separation : `float`
        The angle between the points, in degrees.
    """
    dec_one_radians = math.radians(dec_one)
    dec_two_radians = math.radians(dec_two)
    half_delta_dec = (dec_two_radians - dec_one_radians) / 2.0
    half_delta_ra = math.radians(ra_two - ra_one) / 2.0
    # The haversine formula stays accurate for very small angles, unlike
    # the simpler arccos formula.
    haversine_term = (
        math.sin(half_delta_dec) ** 2
        + math.cos(dec_one_radians) * math.cos(dec_two_radians) * math.sin(half_delta_ra) ** 2
    )
    return math.degrees(2.0 * math.asin(math.sqrt(min(1.0, max(0.0, haversine_term)))))


def _covering_region_exists(
    regions: list[tuple[float, float, float]], ra: float, dec: float, radius: float
) -> bool:
    """Check whether one saved region contains a whole circle of sky.

    A circle fits inside a bigger one when the distance between their
    centers plus the smaller radius is no more than the bigger radius.

    Parameters
    ----------
    regions : `list` of `tuple`
        Saved regions, each ``(ra, dec, radius)`` in degrees.
    ra, dec : `float`
        The center of the circle to look for, in degrees.
    radius : `float`
        The radius of the circle to look for, in degrees.

    Returns
    -------
    covered : `bool`
        `True` if at least one saved region contains the whole circle.
    """
    return any(
        _angular_separation_degrees(ra, dec, region_ra, region_dec) + radius
        <= region_radius + _CONTAINMENT_TOLERANCE_DEGREES
        for region_ra, region_dec, region_radius in regions
    )


def find_planetarium_stars(
    config: Any, ra: float, dec: float, radius: float, magnitude_limit: float
) -> list[tuple[str, float, float, float, str]] | None:
    """Look up saved Gaia stars for a circle of sky, if it is fully saved.

    A circle counts as saved when one earlier download covers all of it
    at least as faintly as ``magnitude_limit``. That earlier download can
    be one the Planetarium made (``planetarium_regions``) or one star
    identification made (``cached_regions``, complete to
    `PIPELINE_CACHE_MAGNITUDE_LIMIT`). When only part of the circle is
    saved, this returns `None` rather than a partial answer, so the
    caller knows to download it.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    ra, dec : `float`
        The center of the circle, in degrees.
    radius : `float`
        The radius of the circle, in degrees.
    magnitude_limit : `float`
        Only stars brighter than this Gaia G magnitude are wanted.

    Returns
    -------
    rows : `list` of `tuple` or `None`
        One tuple per star, ``(source_id, ra, dec, phot_g_mean_mag,
        designation)``, brightest first, or `None` if the circle is not
        fully saved yet.
    """
    cache_db_path = get_catalog_cache_path(config)
    if not cache_db_path.exists():
        return None
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)

        saved_regions: list[tuple[float, float, float]] = []
        if magnitude_limit <= PIPELINE_CACHE_MAGNITUDE_LIMIT:
            saved_regions.extend(connection.execute("SELECT ra, dec, radius FROM cached_regions").fetchall())
        saved_regions.extend(
            connection.execute(
                "SELECT ra, dec, radius FROM planetarium_regions WHERE magnitude_limit >= ?",
                (magnitude_limit,),
            ).fetchall()
        )
        if not _covering_region_exists(saved_regions, ra, dec, radius):
            return None

        # First narrow to a box with the database, then trim the box's
        # corners down to the true circle in Python.
        dec_low = dec - radius
        dec_high = dec + radius
        box_clause = "dec >= ? AND dec <= ? AND phot_g_mean_mag < ?"
        box_parameters: list[float] = [dec_low, dec_high, magnitude_limit]
        # Near a pole, or for a very wide circle, a right-ascension box is
        # meaningless (every RA is close), so it is left out there.
        cos_dec = math.cos(math.radians(min(89.0, abs(dec) + radius)))
        if dec_high < 89.0 and dec_low > -89.0 and radius / cos_dec < 90.0:
            half_width = radius / cos_dec
            ra_low = ra - half_width
            ra_high = ra + half_width
            if ra_low < 0.0:
                box_clause += " AND (ra >= ? OR ra <= ?)"
                box_parameters += [ra_low + 360.0, ra_high]
            elif ra_high >= 360.0:
                box_clause += " AND (ra >= ? OR ra <= ?)"
                box_parameters += [ra_low, ra_high - 360.0]
            else:
                box_clause += " AND ra >= ? AND ra <= ?"
                box_parameters += [ra_low, ra_high]

        rows_by_source_id: dict[str, tuple[str, float, float, float, str]] = {}
        for select_statement in (
            "SELECT source_id, ra, dec, phot_g_mean_mag, designation FROM gaia_sources WHERE ",
            "SELECT source_id, ra, dec, phot_g_mean_mag, designation FROM planetarium_sources WHERE ",
        ):
            # Only fixed text and "?" placeholders are joined here; the
            # values themselves always travel in `box_parameters`.
            for row in connection.execute(select_statement + box_clause, box_parameters):
                if _angular_separation_degrees(ra, dec, row[1], row[2]) <= radius:
                    rows_by_source_id[row[0]] = row
        return sorted(rows_by_source_id.values(), key=lambda row: row[3])
    finally:
        connection.close()


def store_planetarium_region(
    config: Any,
    ra: float,
    dec: float,
    radius: float,
    magnitude_limit: float,
    rows: list[tuple[str, float, float, float, str]],
) -> None:
    """Save the stars the Planetarium downloaded for a circle of sky.

    The stars go in ``planetarium_sources`` and the circle in
    ``planetarium_regions``, never in the tables star identification
    reads (see the module docstring). If the same circle was saved
    before, it keeps the deeper of the two magnitude limits.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    ra, dec : `float`
        The center of the circle, in degrees.
    radius : `float`
        The radius of the circle, in degrees.
    magnitude_limit : `float`
        How faint the saved stars are complete to: every Gaia star at
        least this bright inside the circle is in ``rows``. A download
        that was cut short at its row limit is only complete to the
        faintest star it did return, so pass that magnitude instead of
        the one that was asked for.
    rows : `list` of `tuple`
        One tuple per star: ``(source_id, ra, dec, phot_g_mean_mag,
        designation)``.
    """
    cache_db_path = get_catalog_cache_path(config)
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        connection.executemany(
            """
            INSERT OR REPLACE INTO planetarium_sources (source_id, ra, dec, phot_g_mean_mag, designation)
            VALUES (?, ?, ?, ?, ?)
        """,
            rows,
        )
        region_key = f"{ra:.4f}_{dec:.4f}_{radius:.4f}"
        connection.execute(
            """
            INSERT INTO planetarium_regions (region_key, ra, dec, radius, magnitude_limit)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(region_key) DO UPDATE SET
                magnitude_limit = MAX(magnitude_limit, excluded.magnitude_limit)
        """,
            (region_key, ra, dec, radius, magnitude_limit),
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
