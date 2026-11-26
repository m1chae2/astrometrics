"""Tests for the local Gaia catalog cache database driver."""

import sqlite3
from pathlib import Path

from astrometricslib._internal.drivers.catalog_store import summarize_catalog_coverage
from astrometricslib.foundation.paths import catalog_cache_directory


def test_coverage_of_a_missing_cache_is_empty_not_an_error(tmp_path: Path) -> None:
    """A fresh install has no cache yet; that is a zero, not a failure."""

    class _Config:
        def get_library_path(self) -> object:
            """Return the sandboxed library root.

            Returns
            -------
            path : `object`
                The temporary directory standing in for the library.
            """
            return tmp_path

    coverage = summarize_catalog_coverage(_Config())

    assert coverage["exists"] is False
    assert coverage["source_count"] == 0


def test_coverage_counts_stored_stars_and_complete_tiles(tmp_path: Path) -> None:
    """Coverage reports the stars and the complete tiles the cache holds."""
    from astrometricslib._internal.drivers import catalog_store

    catalog_store.record_tile_fetch(
        None,
        3,
        19.0,
        [(str((3 << 45) + 1), 1.0, 2.0, 12.0, "x"), (str((3 << 45) + 2), 3.0, 4.0, 13.0, "y")],
        "t",
    )
    catalog_store.record_tile_failure(None, 4, "down")

    coverage = summarize_catalog_coverage(None)

    assert coverage["source_count"] == 2
    assert coverage["region_count"] == 1


def test_a_cache_from_before_tiles_counts_as_empty(tmp_path: Path) -> None:
    """An old file with only the circle tables holds nothing usable."""
    catalogs = Path(catalog_cache_directory())
    connection = sqlite3.connect(catalogs / "catalog_cache.db")
    connection.execute("CREATE TABLE gaia_sources (source_id TEXT PRIMARY KEY, ra REAL, dec REAL)")
    connection.execute("CREATE TABLE cached_regions (region_key TEXT PRIMARY KEY, ra REAL)")
    connection.execute("INSERT INTO gaia_sources VALUES ('1', 1.0, 2.0)")
    connection.execute("INSERT INTO cached_regions VALUES ('k', 1.0)")
    connection.commit()
    connection.close()

    coverage = summarize_catalog_coverage(None)

    assert coverage["exists"] is True
    assert coverage["source_count"] == 0
    assert coverage["region_count"] == 0
