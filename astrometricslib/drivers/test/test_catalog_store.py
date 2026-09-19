"""Tests for the local Gaia catalog cache database driver."""

import sqlite3

import pytest

from astrometricslib.drivers.catalog_store import (
    PIPELINE_CACHE_MAGNITUDE_LIMIT,
    find_planetarium_stars,
    get_catalog_cache_path,
    insert_gaia_sources,
    mark_region_cached,
    store_planetarium_region,
    summarize_catalog_coverage,
)


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


class _LibraryConfig:
    """A stand-in for `AppConfiguration` that points at a temporary folder."""

    def __init__(self, library_path: object) -> None:
        self._library_path = library_path

    def get_library_path(self) -> object:
        """Return the sandboxed library root.

        Returns
        -------
        path : `object`
            The temporary directory standing in for the library.
        """
        return self._library_path


def _star(source_id: str, ra: float, dec: float, magnitude: float) -> tuple[str, float, float, float, str]:
    """Build one cache row the way the store expects it.

    Returns
    -------
    row : `tuple`
        ``(source_id, ra, dec, phot_g_mean_mag, designation)``.
    """
    return (source_id, ra, dec, magnitude, f"Gaia DR3 {source_id}")


def _table_row_count(config: _LibraryConfig, table_name: str) -> int:
    """Count the rows in one cache table.

    Returns
    -------
    count : `int`
        Number of rows in the table.
    """
    connection = sqlite3.connect(get_catalog_cache_path(config))
    try:
        return connection.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]  # ruff: ignore[hardcoded-sql-expression] - fixed names from the tests below
    finally:
        connection.close()


def test_saved_planetarium_region_returns_stars_inside_the_circle_brightest_first(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fully saved circle gives back its stars, and only those inside it."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(
        config,
        ra=250.0,
        dec=36.0,
        radius=2.0,
        magnitude_limit=16.0,
        rows=[
            _star("faint", 250.1, 36.1, 15.0),
            _star("bright", 249.9, 35.9, 9.0),
            _star("outside_circle", 253.0, 36.0, 10.0),  # about 2.4 degrees away
        ],
    )

    rows = find_planetarium_stars(config, ra=250.0, dec=36.0, radius=1.0, magnitude_limit=16.0)

    assert rows is not None
    assert [row[0] for row in rows] == ["bright", "faint"]


def test_partly_saved_circle_is_a_miss_not_a_partial_answer(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Only part of the wanted circle being saved must not look like a hit."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(config, 250.0, 36.0, 1.0, 16.0, [_star("a", 250.0, 36.0, 10.0)])

    # Same center, but a bigger circle than was saved.
    assert find_planetarium_stars(config, 250.0, 36.0, 1.5, 16.0) is None
    # Same size, but shifted so part of it falls outside what was saved.
    assert find_planetarium_stars(config, 250.5, 36.0, 1.0, 16.0) is None


def test_region_is_only_complete_as_faintly_as_it_was_saved(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A region saved to G 14 cannot answer a request for G 16."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(config, 250.0, 36.0, 2.0, 14.0, [_star("a", 250.0, 36.0, 10.0)])

    assert find_planetarium_stars(config, 250.0, 36.0, 1.0, 14.0) is not None
    assert find_planetarium_stars(config, 250.0, 36.0, 1.0, 12.0) is not None
    assert find_planetarium_stars(config, 250.0, 36.0, 1.0, 16.0) is None


def test_saving_the_same_region_again_keeps_the_deeper_limit(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A later, shallower save must not make a region look less complete."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(config, 250.0, 36.0, 2.0, 16.0, [_star("a", 250.0, 36.0, 10.0)])
    store_planetarium_region(config, 250.0, 36.0, 2.0, 12.0, [_star("a", 250.0, 36.0, 10.0)])

    assert find_planetarium_stars(config, 250.0, 36.0, 1.0, 16.0) is not None


def test_star_identification_regions_count_as_complete_to_the_pipeline_limit(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A region star identification saved can be reused by the sky map."""
    config = _LibraryConfig(tmp_path)
    insert_gaia_sources(config, [_star("pipeline_star", 250.0, 36.0, 17.0)])
    mark_region_cached(config, "250.000_36.000_0.80", 250.0, 36.0, 0.8)

    rows = find_planetarium_stars(config, 250.0, 36.0, 0.5, 17.5)

    assert rows is not None
    assert [row[0] for row in rows] == ["pipeline_star"]
    # Deeper than that download went, so it cannot be trusted as complete.
    assert find_planetarium_stars(config, 250.0, 36.0, 0.5, PIPELINE_CACHE_MAGNITUDE_LIMIT + 1.0) is None


def test_saving_planetarium_results_never_touches_the_star_identification_tables(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Star identification treats 5+ stars in gaia_sources as complete.

    Regression guard: partial, brightest-stars-only sky-map results saved there
    would make identification stop downloading the fainter stars it needs.
    """
    config = _LibraryConfig(tmp_path)
    rows = [_star(str(index), 250.0 + index * 0.01, 36.0, 10.0 + index) for index in range(8)]

    store_planetarium_region(config, 250.0, 36.0, 2.0, 16.0, rows)

    assert _table_row_count(config, "planetarium_sources") == 8
    assert _table_row_count(config, "planetarium_regions") == 1
    assert _table_row_count(config, "gaia_sources") == 0
    assert _table_row_count(config, "cached_regions") == 0


def test_lookup_finds_stars_across_the_zero_hours_right_ascension_seam(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A circle straddling RA 0/360 finds stars on both sides."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(
        config,
        ra=0.0,
        dec=10.0,
        radius=2.0,
        magnitude_limit=16.0,
        rows=[_star("west_of_seam", 359.5, 10.0, 10.0), _star("east_of_seam", 0.5, 10.0, 11.0)],
    )

    rows = find_planetarium_stars(config, ra=0.0, dec=10.0, radius=1.0, magnitude_limit=16.0)

    assert rows is not None
    assert {row[0] for row in rows} == {"west_of_seam", "east_of_seam"}


def test_lookup_near_a_pole_does_not_crash_and_finds_stars(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Near the pole every RA is close, so the RA box must be skipped."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(
        config,
        ra=10.0,
        dec=89.5,
        radius=3.0,
        magnitude_limit=16.0,
        rows=[_star("same_side", 10.0, 89.8, 10.0), _star("far_ra", 190.0, 89.8, 11.0)],
    )

    rows = find_planetarium_stars(config, ra=10.0, dec=89.5, radius=1.0, magnitude_limit=16.0)

    assert rows is not None
    assert {row[0] for row in rows} == {"same_side", "far_ra"}


def test_lookup_without_a_cache_file_is_a_miss_and_creates_nothing(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A fresh install has no cache; looking must not create one."""
    config = _LibraryConfig(tmp_path)

    assert find_planetarium_stars(config, 250.0, 36.0, 1.0, 16.0) is None
    assert not get_catalog_cache_path(config).exists()


@pytest.mark.parametrize(
    ("magnitude_limit", "expected_ids"),
    [(8.0, ["bright"]), (16.0, ["bright", "faint"])],
)
def test_lookup_only_returns_stars_brighter_than_the_limit(tmp_path, magnitude_limit, expected_ids):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Stars fainter than the requested limit are left out."""
    config = _LibraryConfig(tmp_path)
    store_planetarium_region(
        config,
        250.0,
        36.0,
        2.0,
        16.0,
        [_star("bright", 250.0, 36.0, 7.0), _star("faint", 250.1, 36.0, 15.0)],
    )

    rows = find_planetarium_stars(config, 250.0, 36.0, 1.0, magnitude_limit)

    assert rows is not None
    assert [row[0] for row in rows] == expected_ids
