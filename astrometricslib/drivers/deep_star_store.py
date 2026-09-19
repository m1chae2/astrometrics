"""The one place that opens the local deep-star catalog database.

The Planetarium draws faint stars from a copy of the Gaia DR3 catalog that
was downloaded once and saved on this computer, so panning and zooming never
wait on the internet. This file is the only place that opens that database
file directly. Everything else asks these functions for stars, or tells them
to save some, instead of running SQL itself.

How the stars are stored
------------------------
Looking up "every star in this circle of sky" would be slow if the
database had to check every star. So the sky is cut into small rectangular
tiles, each star is filed under the tile it sits in, and a lookup only opens
the tiles that touch the circle. Inside a tile the stars are kept brightest
first, so asking for "brighter than magnitude 13" stops reading early.

There are two grids of tiles:

- A *coarse* grid of big tiles holds the bright stars. A wide view of the
  sky only needs bright stars, and big tiles mean it opens only a few
  hundred of them instead of thousands.
- A *fine* grid of small tiles holds the faint stars. They are only needed
  when the view is zoomed in, so only a small patch of sky is opened.

Progress of the download is saved too, one row per chunk of sky (a "pixel"
of the Gaia archive's own sky map), written in the same step as that chunk's
stars. That is what lets an interrupted download pick up where it stopped.
"""

import logging
import math
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

__all__ = [
    "BRIGHT_TIER_MAX_MAGNITUDE",
    "COARSE_TILE_HEIGHT_DEGREES",
    "FINE_TILE_HEIGHT_DEGREES",
    "TileGrid",
    "find_deep_stars",
    "get_deep_catalog_path",
    "get_deep_catalog_status",
    "get_downloaded_pixels",
    "record_downloaded_pixel",
    "set_deep_catalog_plan",
]

_DEEP_CATALOG_FILENAME = "deep_star_catalog.db"

# Height, in degrees of declination, of one tile in each grid.
#
# Derivation: the coarse grid only has to serve wide views. The widest view
# that still asks for stars this faint is about a 20 degree field, which
# reads a circle about 30 degrees in radius; 5 degree tiles make that a
# couple of hundred tiles, where 1 degree tiles would make it thousands.
# The fine grid serves zoomed-in views; 1 degree tiles keep the part of a
# tile that is read but then thrown away (the corners outside the circle)
# small next to the roughly 2 degree fields this library images.
# Chosen by reasoning, not yet measured on the finished catalog.
COARSE_TILE_HEIGHT_DEGREES = 5.0
FINE_TILE_HEIGHT_DEGREES = 1.0

# Stars brighter than this Gaia G magnitude go in the coarse grid; fainter
# ones go in the fine grid.
#
# Derivation: a wide view needs at most about magnitude 12 (the map asks
# for magnitude 12.8 at a 10 degree field), and by my estimate the whole sky
# holds only a few million stars that bright, which is small enough to read
# a few hundred big tiles of quickly. Not verified against the real counts:
# the download script prints how many stars land in each grid.
BRIGHT_TIER_MAX_MAGNITUDE = 12.0

# The most tile numbers one row of tiles can have. Each tile's number is
# ``row * 1024 + column``, so this must be bigger than the most columns any
# row has (360 degrees divided by the smallest tile height, 0.5, is 720).
_TILE_NUMBER_STRIDE = 1024

# Angular slack, in degrees, when working out which rows of tiles a circle
# touches. It only stops a star exactly on a tile edge being missed because
# of the last decimal place of a floating point number.
_EDGE_TOLERANCE_DEGREES = 1e-9

# Positions are saved as whole millionths of a degree (about 3.6
# milliarcseconds), and magnitudes as whole thousandths of a magnitude.
# Whole numbers take fewer bytes than decimals, which matters when there are
# tens of millions of stars, and both are far finer than the map can show.
_MICRODEGREES_PER_DEGREE = 1_000_000
_MILLIMAGNITUDES_PER_MAGNITUDE = 1000

# SQLite limits how many "?" placeholders one query may have, so long lists
# of tiles are sent in batches of this size.
_TILES_PER_QUERY = 500

_GRID_COARSE = 0
_GRID_FINE = 1

# The lookup query is written in two halves, with the "?" placeholders for the
# tile numbers joined in between, because how many tiles there are changes
# from lookup to lookup. Only "?" characters are ever joined in; every value
# itself travels separately, in the parameter list.
_STARS_IN_TILES_QUERY_START = (
    "SELECT source_id, ra_micro, dec_micro, magnitude_milli FROM deep_stars WHERE grid = ? AND tile IN ("
)
_STARS_IN_TILES_QUERY_END = ") AND magnitude_milli > ? AND magnitude_milli <= ?"


class TileGrid:
    """A way of cutting the sky into rows and columns of small tiles.

    The sky is cut into rows of equal height in declination. Near the poles
    a degree of right ascension covers very little sky, so rows near the
    poles are cut into fewer, wider columns. That keeps every tile about the
    same size on the sky, roughly ``height_degrees`` on each side.

    Parameters
    ----------
    height_degrees : `float`
        The height of every row, in degrees. Must divide 180 evenly.
    """

    def __init__(self, height_degrees: float) -> None:
        row_count = 180.0 / height_degrees
        if abs(row_count - round(row_count)) > 1e-9:
            raise ValueError(f"Tile height {height_degrees} does not divide 180 degrees evenly.")
        self.height_degrees = height_degrees
        self.row_count = round(row_count)
        self._column_counts = [self._count_columns(row) for row in range(self.row_count)]

    def _count_columns(self, row: int) -> int:
        """Work out how many columns one row of tiles is cut into.

        Parameters
        ----------
        row : `int`
            The row number, counting from the south pole (0).

        Returns
        -------
        column_count : `int`
            How many tiles fit around the sky in this row.
        """
        center_dec = -90.0 + (row + 0.5) * self.height_degrees
        # Around the sky at this declination is 360 * cos(dec) degrees long,
        # so that many tile-heights fit; always at least one tile.
        return max(1, int(360.0 * math.cos(math.radians(center_dec)) / self.height_degrees))

    def _row_of(self, dec: float) -> int:
        """Find which row of tiles a declination falls in.

        Parameters
        ----------
        dec : `float`
            Declination in degrees.

        Returns
        -------
        row : `int`
            The row number, kept inside the grid.
        """
        row = math.floor((dec + 90.0) / self.height_degrees)
        return min(self.row_count - 1, max(0, row))

    def tile_number(self, ra: float, dec: float) -> int:
        """Find the tile a point on the sky belongs to.

        Parameters
        ----------
        ra, dec : `float`
            Right ascension and declination, in degrees.

        Returns
        -------
        tile_number : `int`
            A number that identifies the tile.
        """
        row = self._row_of(dec)
        columns = self._column_counts[row]
        column = min(columns - 1, int((ra % 360.0) / 360.0 * columns))
        return row * _TILE_NUMBER_STRIDE + column

    def tile_numbers(self, ra: np.ndarray, dec: np.ndarray) -> np.ndarray:
        """Find the tile of many points at once.

        Parameters
        ----------
        ra, dec : `numpy.ndarray`
            Right ascension and declination of each point, in degrees.

        Returns
        -------
        tile_numbers : `numpy.ndarray`
            The tile number of each point, as 64-bit integers.
        """
        rows = np.clip(np.floor((dec + 90.0) / self.height_degrees).astype(np.int64), 0, self.row_count - 1)
        column_counts = np.asarray(self._column_counts, dtype=np.int64)[rows]
        columns = np.minimum(column_counts - 1, ((ra % 360.0) / 360.0 * column_counts).astype(np.int64))
        return rows * _TILE_NUMBER_STRIDE + columns

    def tiles_for_circle(self, ra: float, dec: float, radius: float) -> list[int]:
        """List every tile a circle of sky might touch.

        The list can include a few tiles that only sit next to the circle,
        never fewer than the tiles it really touches, so no star is missed.
        The caller checks each star's true distance afterwards.

        Parameters
        ----------
        ra, dec : `float`
            The center of the circle, in degrees.
        radius : `float`
            The radius of the circle, in degrees.

        Returns
        -------
        tile_numbers : `list` [`int`]
            The tiles to open.
        """
        radius = max(radius, 0.0)
        dec_low = max(-90.0, dec - radius - _EDGE_TOLERANCE_DEGREES)
        dec_high = min(90.0, dec + radius + _EDGE_TOLERANCE_DEGREES)
        first_row = self._row_of(dec_low)
        last_row = self._row_of(dec_high)

        # A circle that reaches a pole takes in every right ascension. Any
        # other circle spans at most this many degrees either side of its
        # center, measured along the sky's meridians. It is the widest the
        # circle gets, so it is a safe bound for every row.
        covers_all_ra = radius + abs(dec) >= 90.0 - _EDGE_TOLERANCE_DEGREES
        half_width = 180.0
        if not covers_all_ra:
            sine_ratio = math.sin(math.radians(radius)) / math.cos(math.radians(dec))
            half_width = 180.0 if sine_ratio >= 1.0 else math.degrees(math.asin(sine_ratio))
            half_width += _EDGE_TOLERANCE_DEGREES

        tiles: list[int] = []
        for row in range(first_row, last_row + 1):
            columns = self._column_counts[row]
            if covers_all_ra or half_width >= 180.0:
                columns_to_open: range | list[int] = range(columns)
            else:
                first_column = math.floor((ra - half_width) / 360.0 * columns)
                last_column = math.floor((ra + half_width) / 360.0 * columns)
                if last_column - first_column + 1 >= columns:
                    columns_to_open = range(columns)
                else:
                    # Columns numbered past either end wrap around the sky.
                    columns_to_open = [column % columns for column in range(first_column, last_column + 1)]
            tiles.extend(row * _TILE_NUMBER_STRIDE + column for column in columns_to_open)
        return tiles


_COARSE_GRID = TileGrid(COARSE_TILE_HEIGHT_DEGREES)
_FINE_GRID = TileGrid(FINE_TILE_HEIGHT_DEGREES)


def get_deep_catalog_path(config: Any) -> Path:
    """Find where the deep-star catalog database lives on disk.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings, used to find the library's data folder.

    Returns
    -------
    path : `pathlib.Path`
        The full path to the database file. The file may not exist yet,
        which just means the catalog has not been downloaded.
    """
    return config.get_library_path() / "catalogs" / _DEEP_CATALOG_FILENAME


def _ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the catalog's tables if they don't already exist.

    Parameters
    ----------
    connection : `sqlite3.Connection`
        An open connection to the catalog database.
    """
    # One row per star. The primary key is the order the stars are stored in
    # on disk: by grid, then tile, then brightness, so a lookup reads one
    # tile's stars brightest first.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS deep_stars (
            grid INTEGER NOT NULL,
            tile INTEGER NOT NULL,
            magnitude_milli INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            ra_micro INTEGER NOT NULL,
            dec_micro INTEGER NOT NULL,
            PRIMARY KEY (grid, tile, magnitude_milli, source_id)
        ) WITHOUT ROWID
    """)
    # One row per chunk of sky that has been downloaded completely.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS downloaded_pixels (
            pixel INTEGER PRIMARY KEY,
            star_count INTEGER NOT NULL
        )
    """)
    # What this catalog is: the settings it is being built with.
    connection.execute("""
        CREATE TABLE IF NOT EXISTS catalog_plan (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
    """)
    connection.commit()


def set_deep_catalog_plan(config: Any, healpix_level: int, magnitude_limit: float) -> None:
    """Record what the catalog is being built with, or check it matches.

    The first call saves the settings. Later calls (when a download is
    resumed) must use the same settings, because chunks downloaded with a
    different depth or chunk size cannot be mixed into one catalog.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    healpix_level : `int`
        How finely the Gaia archive's sky map was cut for the download.
    magnitude_limit : `float`
        The faintest Gaia G magnitude being downloaded.

    Raises
    ------
    ValueError
        If the catalog was already started with different settings.
    """
    wanted = {"healpix_level": str(healpix_level), "magnitude_limit": repr(float(magnitude_limit))}
    cache_db_path = get_deep_catalog_path(config)
    os.makedirs(cache_db_path.parent, exist_ok=True)
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        saved = dict(connection.execute("SELECT key, value FROM catalog_plan").fetchall())
        if saved:
            for key, value in wanted.items():
                if saved.get(key) != value:
                    raise ValueError(
                        f"The deep-star catalog at {cache_db_path} was started with {key}="
                        f"{saved.get(key)}, but {value} was requested. Delete the file to start over."
                    )
            return
        connection.executemany("INSERT INTO catalog_plan (key, value) VALUES (?, ?)", list(wanted.items()))
        connection.commit()
    finally:
        connection.close()


def get_downloaded_pixels(config: Any) -> set[int]:
    """List the chunks of sky that are already fully downloaded.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    pixels : `set` [`int`]
        The numbers of the finished chunks. Empty if nothing is saved yet.
    """
    cache_db_path = get_deep_catalog_path(config)
    if not cache_db_path.exists():
        return set()
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        return {row[0] for row in connection.execute("SELECT pixel FROM downloaded_pixels")}
    finally:
        connection.close()


def record_downloaded_pixel(
    config: Any,
    pixel: int,
    source_ids: np.ndarray,
    ra: np.ndarray,
    dec: np.ndarray,
    magnitude: np.ndarray,
) -> int:
    """Save one downloaded chunk of sky, and note that it is finished.

    The stars and the "this chunk is done" note are saved in one step, so an
    interrupted download never leaves a chunk that looks finished but is
    missing stars.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    pixel : `int`
        The number of the chunk in the Gaia archive's sky map.
    source_ids : `numpy.ndarray`
        The Gaia source ID of each star.
    ra, dec : `numpy.ndarray`
        Position of each star, in degrees.
    magnitude : `numpy.ndarray`
        Gaia G magnitude of each star.

    Returns
    -------
    star_count : `int`
        How many stars were saved.
    """
    cache_db_path = get_deep_catalog_path(config)
    os.makedirs(cache_db_path.parent, exist_ok=True)

    is_bright = magnitude <= BRIGHT_TIER_MAX_MAGNITUDE
    grids = np.where(is_bright, _GRID_COARSE, _GRID_FINE)
    tiles = np.where(
        is_bright,
        _COARSE_GRID.tile_numbers(ra, dec),
        _FINE_GRID.tile_numbers(ra, dec),
    )
    rows = list(
        zip(
            grids.tolist(),
            tiles.tolist(),
            np.rint(magnitude * _MILLIMAGNITUDES_PER_MAGNITUDE).astype(np.int64).tolist(),
            np.asarray(source_ids, dtype=np.int64).tolist(),
            np.rint((ra % 360.0) * _MICRODEGREES_PER_DEGREE).astype(np.int64).tolist(),
            np.rint(dec * _MICRODEGREES_PER_DEGREE).astype(np.int64).tolist(),
            strict=True,
        )
    )

    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        with connection:
            connection.executemany(
                """
                INSERT OR REPLACE INTO deep_stars
                    (grid, tile, magnitude_milli, source_id, ra_micro, dec_micro)
                VALUES (?, ?, ?, ?, ?, ?)
            """,
                rows,
            )
            connection.execute(
                "INSERT OR REPLACE INTO downloaded_pixels (pixel, star_count) VALUES (?, ?)",
                (pixel, len(rows)),
            )
    finally:
        connection.close()
    return len(rows)


def _separation_degrees(ra: float, dec: float, star_ra: np.ndarray, star_dec: np.ndarray) -> np.ndarray:
    """Measure the angle from one point to many stars.

    Parameters
    ----------
    ra, dec : `float`
        The point, in degrees.
    star_ra, star_dec : `numpy.ndarray`
        The stars' positions, in degrees.

    Returns
    -------
    separation : `numpy.ndarray`
        The angle from the point to each star, in degrees.
    """
    dec_radians = math.radians(dec)
    star_dec_radians = np.radians(star_dec)
    half_delta_dec = (star_dec_radians - dec_radians) / 2.0
    half_delta_ra = np.radians(star_ra - ra) / 2.0
    # The haversine formula stays accurate for very small angles.
    haversine_term = (
        np.sin(half_delta_dec) ** 2
        + math.cos(dec_radians) * np.cos(star_dec_radians) * np.sin(half_delta_ra) ** 2
    )
    return np.degrees(2.0 * np.arcsin(np.sqrt(np.clip(haversine_term, 0.0, 1.0))))


def find_deep_stars(
    config: Any,
    ra: float,
    dec: float,
    radius: float,
    magnitude_limit: float,
    maximum_stars: int | None = None,
) -> list[tuple[int, float, float, float]] | None:
    """Look up the saved stars inside a circle of sky.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    ra, dec : `float`
        The center of the circle, in degrees.
    radius : `float`
        The radius of the circle, in degrees.
    magnitude_limit : `float`
        Only stars brighter than (or as bright as) this Gaia G magnitude.
    maximum_stars : `int`, optional
        Return at most this many stars, keeping the brightest.

    Returns
    -------
    stars : `list` [`tuple`] or `None`
        One ``(source_id, ra, dec, magnitude)`` tuple per star, brightest
        first, or `None` if no catalog has been downloaded at all.
    """
    cache_db_path = get_deep_catalog_path(config)
    if not cache_db_path.exists():
        return None
    connection = sqlite3.connect(cache_db_path)
    try:
        _ensure_schema(connection)
        limit_milli = math.floor(magnitude_limit * _MILLIMAGNITUDES_PER_MAGNITUDE + 1e-6)
        bright_limit_milli = round(BRIGHT_TIER_MAX_MAGNITUDE * _MILLIMAGNITUDES_PER_MAGNITUDE)

        # Each grid holds a range of magnitudes. The faint grid is skipped
        # entirely for a view that only wants bright stars.
        searches = [(_GRID_COARSE, _COARSE_GRID, -(10**9), min(limit_milli, bright_limit_milli))]
        if limit_milli > bright_limit_milli:
            searches.append((_GRID_FINE, _FINE_GRID, bright_limit_milli, limit_milli))

        found: list[np.ndarray] = []
        for grid_number, grid, low_milli_exclusive, high_milli in searches:
            tiles = grid.tiles_for_circle(ra, dec, radius)
            for start in range(0, len(tiles), _TILES_PER_QUERY):
                batch = tiles[start : start + _TILES_PER_QUERY]
                placeholders = ",".join("?" * len(batch))
                query = _STARS_IN_TILES_QUERY_START + placeholders + _STARS_IN_TILES_QUERY_END
                rows = connection.execute(
                    query, [grid_number, *batch, low_milli_exclusive, high_milli]
                ).fetchall()
                if rows:
                    found.append(np.array(rows, dtype=np.int64))
    finally:
        connection.close()

    if not found:
        return []
    stars = np.concatenate(found)
    star_ra = stars[:, 1] / _MICRODEGREES_PER_DEGREE
    star_dec = stars[:, 2] / _MICRODEGREES_PER_DEGREE
    # A tile can stick out past the circle, so keep only stars truly inside.
    inside = _separation_degrees(ra, dec, star_ra, star_dec) <= radius
    stars = stars[inside]
    order = np.argsort(stars[:, 3], kind="stable")
    stars = stars[order]
    if maximum_stars is not None:
        stars = stars[:maximum_stars]
    return [
        (
            int(row[0]),
            float(row[1]) / _MICRODEGREES_PER_DEGREE,
            float(row[2]) / _MICRODEGREES_PER_DEGREE,
            float(row[3]) / _MILLIMAGNITUDES_PER_MAGNITUDE,
        )
        for row in stars
    ]


def get_deep_catalog_status(config: Any) -> dict[str, Any]:
    """Describe how much of the deep-star catalog is downloaded.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.

    Returns
    -------
    status : `dict`
        ``installed`` (any chunk saved), ``complete`` (every chunk saved),
        ``star_count``, ``pixels_downloaded``, ``pixels_total``,
        ``healpix_level``, ``magnitude_limit`` (the last three are `None`
        before a download has started), and ``size_megabytes``.
    """
    cache_db_path = get_deep_catalog_path(config)
    status: dict[str, Any] = {
        "installed": False,
        "complete": False,
        "star_count": 0,
        "pixels_downloaded": 0,
        "pixels_total": None,
        "healpix_level": None,
        "magnitude_limit": None,
        "size_megabytes": 0.0,
    }
    if not cache_db_path.exists():
        return status
    status["size_megabytes"] = round(os.path.getsize(cache_db_path) / 1_000_000, 2)
    try:
        connection = sqlite3.connect(f"file:{cache_db_path}?mode=ro", uri=True)
        try:
            plan = dict(connection.execute("SELECT key, value FROM catalog_plan").fetchall())
            downloaded, star_count = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(star_count), 0) FROM downloaded_pixels"
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as catalog_error:
        # A catalog that was never started has no tables yet; that just
        # means it is not installed.
        logger.debug("Could not read the deep-star catalog: %s", catalog_error)
        return status

    status["pixels_downloaded"] = downloaded
    status["star_count"] = star_count
    if "healpix_level" in plan:
        level = int(plan["healpix_level"])
        status["healpix_level"] = level
        status["pixels_total"] = 12 * 4**level
        status["complete"] = downloaded >= status["pixels_total"]
    if "magnitude_limit" in plan:
        status["magnitude_limit"] = float(plan["magnitude_limit"])
    status["installed"] = downloaded > 0
    return status
