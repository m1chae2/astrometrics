"""Purpose: Tests for the download records of the local Gaia cache.

Description: Next to the stars, the cache keeps one record per downloaded
region: its centre, radius, magnitude limit and whether the download hit the
row limit. `is_gaia_region_complete` answers from those records. These tests
check the covering rules (circle inside circle, depth, truncation, Right
Ascension wrap) and that a cache file written before the records existed
gets the new table added without losing its stars.
"""

import sqlite3
from pathlib import Path

import pytest

from astrometricslib.drivers import catalog_store


class _Config:
    """A stand-in for `AppConfiguration` that names a library folder."""

    def __init__(self, library_path: Path) -> None:
        """Remember the folder and make sure ``catalogs`` exists.

        Parameters
        ----------
        library_path : `pathlib.Path`
            The folder that holds the ``catalogs`` directory.
        """
        self.library_path = library_path
        (library_path / "catalogs").mkdir(exist_ok=True)

    def get_library_path(self) -> Path:
        """Return the library folder.

        Returns
        -------
        path : `pathlib.Path`
            The folder given at construction.
        """
        return self.library_path


def _table_names(config: _Config) -> set[str]:
    """List the tables in the cache file.

    Parameters
    ----------
    config : `_Config`
        The stand-in settings that locate the cache.

    Returns
    -------
    names : `set` [`str`]
        The table names.
    """
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_nothing_is_complete_before_any_record(tmp_path: Path) -> None:
    """An empty cache has no complete region."""
    config = _Config(tmp_path)

    assert not catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.5, 18.0)


def test_a_recorded_region_covers_a_request_inside_it(tmp_path: Path) -> None:
    """A request circle that fits inside a complete record is covered."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 18.0, row_limit_hit=False)

    assert catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 1.0, 18.0)
    assert catalog_store.is_gaia_region_complete(config, 100.2, 20.1, 0.5, 17.0)
    assert not catalog_store.is_gaia_region_complete(config, 100.8, 20.0, 0.5, 18.0)
    assert not catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 1.1, 18.0)


def test_a_truncated_record_never_covers(tmp_path: Path) -> None:
    """A download that hit the row limit is never complete."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 20.0, row_limit_hit=True)

    assert not catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.1, 10.0)


def test_the_magnitude_limit_must_be_at_least_as_deep(tmp_path: Path) -> None:
    """A larger G limit means fainter stars, so deeper."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 18.0, row_limit_hit=False)

    assert catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.5, 15.0)
    assert catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.5, 18.0)
    assert not catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 0.5, 18.5)


def test_coverage_works_across_the_ra_wrap(tmp_path: Path) -> None:
    """Right Ascension 359.9 and 0.1 are 0.2 degrees apart, not 359.8."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 359.9, 0.0, 0.5, 18.0, row_limit_hit=False)

    assert catalog_store.is_gaia_region_complete(config, 0.1, 0.0, 0.2, 18.0)
    assert not catalog_store.is_gaia_region_complete(config, 0.1, 0.0, 0.4, 18.0)


def test_recording_the_same_region_again_replaces_the_record(tmp_path: Path) -> None:
    """A repeat download of one region keeps one record, the newest."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 18.0, row_limit_hit=True)
    catalog_store.record_gaia_region(config, 100.0, 20.0, 1.0, 18.0, row_limit_hit=False)

    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        count = connection.execute("SELECT COUNT(*) FROM gaia_cache_regions").fetchone()[0]
    finally:
        connection.close()
    assert count == 1
    assert catalog_store.is_gaia_region_complete(config, 100.0, 20.0, 1.0, 18.0)


def test_the_schema_migration_adds_the_table_to_an_existing_database(tmp_path: Path) -> None:
    """An old cache file gains ``gaia_cache_regions`` and keeps its data."""
    config = _Config(tmp_path)
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    connection.execute(
        "CREATE TABLE gaia_sources (source_id TEXT PRIMARY KEY, ra REAL, dec REAL, phot_g_mean_mag REAL, "
        "designation TEXT, pmra REAL, pmdec REAL)"
    )
    connection.execute(
        "CREATE TABLE cached_regions (region_key TEXT PRIMARY KEY, ra REAL, dec REAL, radius REAL)"
    )
    connection.execute("INSERT INTO gaia_sources VALUES ('1', 10.0, 20.0, 12.0, 'Gaia DR3 1', NULL, NULL)")
    connection.execute("INSERT INTO cached_regions VALUES ('old', 10.0, 20.0, 0.5)")
    connection.commit()
    connection.close()
    assert "gaia_cache_regions" not in _table_names(config)

    rows = catalog_store.query_gaia_sources_in_bounds(config, 9.0, 11.0, 19.0, 21.0)

    assert "gaia_cache_regions" in _table_names(config)
    assert len(rows) == 1
    assert catalog_store.is_region_cached(config, "old")


def test_old_cache_regions_are_not_complete_after_the_migration(tmp_path: Path) -> None:
    """Old ``cached_regions`` rows say nothing about depth: not complete."""
    config = _Config(tmp_path)
    catalog_store.mark_region_cached(config, "old", 10.0, 20.0, 0.5)

    assert catalog_store.is_region_cached(config, "old")
    assert not catalog_store.is_gaia_region_complete(config, 10.0, 20.0, 0.3, 10.0)


@pytest.mark.parametrize("row_limit_hit", [True, False])
def test_the_truncation_flag_round_trips(tmp_path: Path, row_limit_hit: bool) -> None:
    """The flag is stored as given."""
    config = _Config(tmp_path)
    catalog_store.record_gaia_region(config, 10.0, 20.0, 0.5, 18.0, row_limit_hit=row_limit_hit)

    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    try:
        stored = connection.execute("SELECT row_limit_hit FROM gaia_cache_regions").fetchone()[0]
    finally:
        connection.close()
    assert bool(stored) is row_limit_hit
