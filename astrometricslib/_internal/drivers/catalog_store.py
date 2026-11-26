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
``PRAGMA user_version`` holds the schema version. A file from before tiles
existed (with a ``gaia_sources`` table and circle records) is treated as
empty: the tile tables are created next to the old ones and nothing is
converted, so the stars download again. The old tables are never read.
"""

import logging
import os
import sqlite3
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from astrometricslib._internal.drivers.sky_tiles import tile_of_source_id
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.paths import catalog_cache_directory
from astrometricslib.foundation.storage.local_database import connect_db
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.utilities.gaia_record_ids import gaia_record_id

logger = logging.getLogger(__name__)

_CATALOG_DB_FILENAME = "catalog_cache.db"

# The schema version this file writes into ``PRAGMA user_version``.
_SCHEMA_VERSION = 2

# The HEALPix level of a cache tile. At level 7 a tile covers about 0.21
# square degrees and is about 0.46 degrees across. Counted on 2026-11-20
# (AST-D22): the central level-7 tile of ten fields held 3,143 to 44,150
# stars to G 21 (median 14,000), under the 50,000-row limit of one download,
# while level-6 tiles held up to 168,274. Two of the 23 level-7 tiles around
# NGC 6888 hold 50,283 and 57,278; such a tile is fetched as its four level-8
# children (see `gaia_field_stars`). Changing the level makes every stored
# tile number wrong, so the cache file must be deleted and rebuilt after a
# change.
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
    "missing_tiles",
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
    star = (source_id, row[1], row[2], row[3], gaia_record_id(source_id))
    return (*star, row[4], row[5]) if include_proper_motion else star


_STAR_COLUMNS = "source_id, ra, dec, g, pmra, pmdec"


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
        One tuple per star: ``(source_id, ra, dec, G, designation)``, followed
        by ``pmra`` and ``pmdec`` (milliarcseconds per year, `None` when
        unknown) if `include_proper_motion` is set. This says nothing about
        completeness; ask `tile_status` or `missing_tiles` for that.
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
        The tuples of `stars_in_tiles`, for the IDs that are cached. IDs not
        cached are left out.
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
        The stars downloaded: ``(source_id, ra, dec, G, designation)``,
        optionally followed by ``pmra`` and ``pmdec`` in milliarcseconds per
        year (``pmra`` already includes the cosine of the declination, as in
        the Gaia catalog; `None` when unknown). A star already cached is
        replaced.
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
            # A file from before tiles has no tile tables and counts as empty.
            if _table_exists(connection, "gaia_stars"):
                coverage["source_count"] = connection.execute("SELECT COUNT(*) FROM gaia_stars").fetchone()[0]
                coverage["region_count"] = connection.execute(
                    "SELECT COUNT(*) FROM gaia_tiles WHERE complete_to_g IS NOT NULL"
                ).fetchone()[0]
        finally:
            connection.close()
    except sqlite3.Error as cache_error:
        # A cache that has never been written has no tables yet, which
        # is an empty result rather than a failure worth raising.
        logger.debug("Could not read local catalog cache: %s", cache_error)

    return coverage
