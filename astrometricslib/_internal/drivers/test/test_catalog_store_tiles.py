"""Purpose: Tests for the tile-based Gaia cache in ``catalog_store``.

Description: The cache keeps stars with the sky tile they are in and records
how deep each tile is complete. These tests check that a star's tile matches
its source ID, that a tile fills in deeper step by step, that a cut-short
download records only the depth it reached, that a failed download is
recorded without hiding stars, and that a cache file from before tiles
existed is converted (inner tiles complete, edge tiles not).
"""

import sqlite3
from pathlib import Path

import pytest

from astrometricslib._internal.drivers import catalog_store
from astrometricslib._internal.drivers.sky_tiles import tile_of_source_id, tiles_for_cone

LEVEL = 7


class _Config:
    """A stand-in for `AppConfiguration` that names a library folder."""

    def __init__(self, library_path: Path) -> None:
        """Remember the folder.

        Parameters
        ----------
        library_path : `pathlib.Path`
            The folder that stands in for the library.
        """
        self.library_path = library_path

    def get_library_path(self) -> Path:
        """Return the library folder.

        Returns
        -------
        path : `pathlib.Path`
            The folder given at construction.
        """
        return self.library_path


def _star(tile: int, serial: int, magnitude: float) -> tuple:
    """Build a star row whose source ID lies in a tile.

    Parameters
    ----------
    tile : `int`
        The level-7 tile the star should be in.
    serial : `int`
        A number that makes the source ID unique within the tile.
    magnitude : `float`
        The star's G magnitude.

    Returns
    -------
    row : `tuple`
        ``(source_id, ra, dec, g, designation, pmra, pmdec)``.
    """
    source_id = (tile << 45) + serial
    return (str(source_id), 10.0, 20.0, magnitude, f"Gaia DR3 {source_id}", 1.5, -2.5)


def test_stars_are_stored_with_the_tile_of_their_source_id(tmp_path: Path) -> None:
    """The tile column is ``source_id >> 45``; lookups by tile find it."""
    config = _Config(tmp_path)
    star = _star(1234, 7, 12.0)
    catalog_store.insert_gaia_sources(config, [star])

    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        stored_tile = connection.execute("SELECT tile FROM gaia_stars").fetchone()[0]
    finally:
        connection.close()
    assert stored_tile == 1234
    assert stored_tile == tile_of_source_id(int(star[0]), LEVEL)
    assert catalog_store.stars_in_tiles(config, [1234], 13.0) == [star[:5]]
    assert catalog_store.stars_in_tiles(config, [1234], 11.0) == []
    assert catalog_store.stars_in_tiles(config, [1235], 13.0) == []
    assert catalog_store.stars_in_tiles(config, [1234], 13.0, include_proper_motion=True) == [star]
    assert catalog_store.stars_by_source_id(config, [int(star[0]), 5]) == [star[:5]]


def test_a_tile_fills_in_deeper_step_by_step(tmp_path: Path) -> None:
    """A deeper fetch raises the depth, a shallower one never lowers it."""
    config = _Config(tmp_path)
    assert catalog_store.tile_status(config, [50, 51]) == {50: None, 51: None}
    assert catalog_store.missing_tiles(config, [50, 51], 18.0) == [50, 51]

    catalog_store.record_tile_fetch(config, 50, 18.0, [_star(50, 1, 15.0)], "VizieR")
    assert catalog_store.tile_status(config, [50, 51]) == {50: 18.0, 51: None}
    assert catalog_store.missing_tiles(config, [50, 51], 18.0) == [51]
    assert catalog_store.missing_tiles(config, [50], 20.0) == [50]

    catalog_store.record_tile_fetch(config, 50, 20.0, [_star(50, 2, 19.0)], "ESA Gaia archive")
    catalog_store.record_tile_fetch(config, 50, 17.0, [], "VizieR")
    assert catalog_store.tile_status(config, [50]) == {50: 20.0}
    assert catalog_store.missing_tiles(config, [50], 20.0) == []
    assert len(catalog_store.stars_in_tiles(config, [50], 21.0)) == 2


def test_a_cut_short_download_records_only_the_depth_it_reached(tmp_path: Path) -> None:
    """The faintest G reached is recorded, so deeper requests stay missing."""
    config = _Config(tmp_path)
    catalog_store.record_tile_fetch(config, 9, 16.4, [_star(9, 1, 16.4)], "ESA Gaia archive")

    assert catalog_store.missing_tiles(config, [9], 16.0) == []
    assert catalog_store.missing_tiles(config, [9], 18.0) == [9]


def test_a_failed_fetch_is_recorded_and_keeps_the_stars(tmp_path: Path) -> None:
    """A failure counts an attempt and keeps the depth; a success clears it."""
    config = _Config(tmp_path)
    catalog_store.record_tile_fetch(config, 3, 15.0, [_star(3, 1, 14.0)], "VizieR")
    catalog_store.record_tile_failure(config, 3, "timed out")
    catalog_store.record_tile_failure(config, 3, "timed out again")
    catalog_store.record_tile_failure(config, 4, "refused")

    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        rows = connection.execute(
            "SELECT tile, complete_to_g, attempts, last_error FROM gaia_tiles ORDER BY tile"
        ).fetchall()
    finally:
        connection.close()
    assert rows == [(3, 15.0, 2, "timed out again"), (4, None, 1, "refused")]
    assert catalog_store.tile_status(config, [3, 4]) == {3: 15.0, 4: None}
    assert len(catalog_store.stars_in_tiles(config, [3], 16.0)) == 1

    catalog_store.record_tile_fetch(config, 4, 12.0, [], "VizieR")
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        cleared = connection.execute("SELECT attempts, last_error FROM gaia_tiles WHERE tile = 4").fetchone()
    finally:
        connection.close()
    assert cleared == (0, None)


def _write_old_cache(config: _Config, region_magnitude: float, row_limit_hit: int) -> list[int]:
    """Write a cache file in the old circle layout.

    Parameters
    ----------
    config : `_Config`
        The stand-in settings that locate the cache.
    region_magnitude : `float`
        The depth of the one recorded circle.
    row_limit_hit : `int`
        1 if the circle's download was cut short.

    Returns
    -------
    tiles : `list` [`int`]
        The tiles the circle touches.
    """
    tiles = tiles_for_cone(100.0, 20.0, 1.0, LEVEL)
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    connection.execute(
        "CREATE TABLE gaia_sources (source_id TEXT PRIMARY KEY, ra REAL, dec REAL, phot_g_mean_mag REAL, "
        "designation TEXT, pmra REAL, pmdec REAL)"
    )
    connection.execute(
        "CREATE TABLE gaia_cache_regions (region_key TEXT PRIMARY KEY, ra REAL NOT NULL, dec REAL NOT NULL, "
        "radius REAL NOT NULL, magnitude_limit REAL, row_limit_hit INTEGER NOT NULL)"
    )
    for tile in tiles:
        source_id = (tile << 45) + 11
        connection.execute(
            "INSERT INTO gaia_sources VALUES (?, 100.0, 20.0, 12.0, ?, 3.0, 4.0)",
            (str(source_id), f"Gaia DR3 {source_id}"),
        )
    connection.execute(
        "INSERT INTO gaia_cache_regions VALUES ('k', 100.0, 20.0, 1.0, ?, ?)",
        (region_magnitude, row_limit_hit),
    )
    connection.commit()
    connection.close()
    return tiles


def test_a_complete_circle_makes_its_inner_tiles_complete(tmp_path: Path) -> None:
    """Tiles wholly inside the circle are complete; edge tiles are not."""
    config = _Config(tmp_path)
    tiles = _write_old_cache(config, 18.0, 0)

    depths = catalog_store.tile_status(config, tiles)

    complete = [tile for tile, depth in depths.items() if depth is not None and depth > 17.999]
    partial = [tile for tile, depth in depths.items() if depth is None]
    assert complete
    assert partial
    assert len(complete) + len(partial) == len(tiles)
    # Every stored star was copied with its tile, complete or not.
    assert len(catalog_store.stars_in_tiles(config, tiles, 13.0)) == len(tiles)
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    finally:
        connection.close()
    assert "gaia_sources" not in tables
    assert version == 2


def test_a_cut_short_circle_makes_no_tile_complete(tmp_path: Path) -> None:
    """A circle that hit the row limit proves nothing about any tile."""
    config = _Config(tmp_path)
    tiles = _write_old_cache(config, 18.0, 1)

    assert set(catalog_store.tile_status(config, tiles).values()) == {None}


def test_the_conversion_runs_once(tmp_path: Path) -> None:
    """Opening the file again changes nothing."""
    config = _Config(tmp_path)
    tiles = _write_old_cache(config, 18.0, 0)
    first = catalog_store.tile_status(config, tiles)
    count = len(catalog_store.stars_in_tiles(config, tiles, 99.0))

    assert catalog_store.tile_status(config, tiles) == first
    assert len(catalog_store.stars_in_tiles(config, tiles, 99.0)) == count


def test_a_failed_conversion_starts_an_empty_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed conversion drops the old stars and warns once."""
    config = _Config(tmp_path)
    tiles = _write_old_cache(config, 18.0, 0)

    def fail(*arguments: object) -> list[int]:
        """Stand in for the tile finder and always fail.

        Parameters
        ----------
        *arguments : `object`
            Ignored.

        Raises
        ------
        ValueError
            Always.
        """
        raise ValueError("broken")

    monkeypatch.setattr(catalog_store, "_tiles_inside_cone", fail)
    monkeypatch.setattr(catalog_store, "_migration_warning_logged", False)
    with caplog.at_level("WARNING"):
        depths = catalog_store.tile_status(config, tiles)

    assert set(depths.values()) == {None}
    assert catalog_store.stars_in_tiles(config, tiles, 99.0) == []
    assert not catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.5, 10.0)
    assert len([record for record in caplog.records if "sky tiles" in record.getMessage()]) == 1


def test_the_circle_functions_work_on_top_of_tiles(tmp_path: Path) -> None:
    """A circle record marks inner tiles; tiles alone can also answer."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 18.0, row_limit_hit=False)
    inner = [
        t for t, d in catalog_store.tile_status(config, tiles_for_cone(100.0, 20.0, 1.0, LEVEL)).items() if d
    ]
    assert inner

    tiles = tiles_for_cone(100.0, 20.0, 0.2, LEVEL)
    for tile in tiles:
        catalog_store.record_tile_fetch(config, tile, 19.0, [], "VizieR")
    assert catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.2, 19.0)
