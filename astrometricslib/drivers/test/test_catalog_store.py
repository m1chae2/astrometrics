"""Tests for the local Gaia catalog cache database driver."""

import sqlite3

from astrometricslib.drivers.catalog_store import summarize_catalog_coverage


def test_coverage_of_a_missing_cache_is_empty_not_an_error(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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


def test_coverage_counts_stored_sources_and_regions(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Coverage reports what the cache actually holds."""
    catalogs = tmp_path / "catalogs"
    catalogs.mkdir()
    connection = sqlite3.connect(catalogs / "catalog_cache.db")
    connection.execute("CREATE TABLE gaia_sources (source_id TEXT PRIMARY KEY, ra REAL, dec REAL)")
    connection.execute("CREATE TABLE cached_regions (region_key TEXT PRIMARY KEY, ra REAL)")
    connection.execute("INSERT INTO gaia_sources VALUES ('1', 1.0, 2.0)")
    connection.execute("INSERT INTO gaia_sources VALUES ('2', 3.0, 4.0)")
    connection.execute("INSERT INTO cached_regions VALUES ('k', 1.0)")
    connection.commit()
    connection.close()

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

    assert coverage["source_count"] == 2
    assert coverage["region_count"] == 1
