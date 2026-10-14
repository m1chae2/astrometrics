"""Purpose: Tests that the Gaia cache is reused only for complete downloads.

Description: The local Gaia cache used to treat any 5 or more stars in the
search box as a complete answer. A region downloaded under the old
10,000-row limit, or only to a bright magnitude, was then reused as if it
were complete. The cache now records each download's centre, radius,
magnitude limit and whether it hit the row limit. These tests check that a
cached region is used only when a record covers the request, is at least as
deep, and was not cut short, and that a cache file with no records is
downloaded again once. They use a fake Gaia client, so no network is needed.
"""

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.drivers import catalog_store
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

CENTRE_RA_DEG = 250.42
CENTRE_DEC_DEG = 36.46
REQUEST_RADIUS_DEG = 0.3
REQUEST_MAGNITUDE = 18.0


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """Give each test an empty Gaia cache in its own folder.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's own folder, used as the library path.
    monkeypatch : `pytest.MonkeyPatch`
        Used to point the configuration at that folder.

    Yields
    ------
    config : `AppConfiguration`
        Settings whose library path is the test folder.
    """
    import astrometricslib.foundation.config as config_loader_module
    from astrometricslib.foundation.config import AppConfiguration

    app_config = AppConfiguration()
    app_config.update_config({"Image Library": {"path": str(tmp_path)}})
    monkeypatch.setattr(config_loader_module, "get_configuration", lambda: app_config)
    (app_config.get_library_path() / "catalogs").mkdir(parents=True, exist_ok=True)
    star_identifier_module.reset_gaia_circuit_breaker()
    yield app_config
    star_identifier_module.reset_gaia_circuit_breaker()


def _stars(count: int = 12, first_id: int = 0) -> list[tuple[str, float, float, float, str]]:
    """Build cached-star rows near the test field, all brighter than G = 17.

    Parameters
    ----------
    count : `int`, optional
        How many stars.
    first_id : `int`, optional
        The Gaia source id of the first star.

    Returns
    -------
    rows : `list` [`tuple`]
        Rows for `catalog_store.insert_gaia_sources`.
    """
    return [
        (
            str(first_id + index),
            CENTRE_RA_DEG + index * 1e-3,
            CENTRE_DEC_DEG,
            10.0 + 0.5 * index,
            f"Gaia DR3 {first_id + index}",
        )
        for index in range(count)
    ]


def _lookup(config: Any, radius_deg: float = REQUEST_RADIUS_DEG, magnitude: float = REQUEST_MAGNITUDE) -> Any:
    """Ask the cache for the test field.

    Parameters
    ----------
    config : `AppConfiguration`
        The settings that locate the cache.
    radius_deg : `float`, optional
        The search radius in degrees.
    magnitude : `float`, optional
        The search magnitude limit.

    Returns
    -------
    result : `tuple` or `None`
        What `StarIdentifier._query_gaia_region_from_cache` returned.
    """
    return StarIdentifier._query_gaia_region_from_cache(
        config, CENTRE_RA_DEG, CENTRE_DEC_DEG, radius_deg, magnitude
    )


def _fake_download(monkeypatch: pytest.MonkeyPatch, *, row_limit_reached: bool) -> list[tuple]:
    """Replace the Gaia download with one that returns a few stars.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace `StarIdentifier._download_gaia_region`.
    row_limit_reached : `bool`
        Whether the fake download reports that it hit the row limit.

    Returns
    -------
    calls : `list` [`tuple`]
        Collects the arguments of each download.
    """
    calls: list[tuple] = []

    def download(
        ra_center: float, dec_center: float, radius_deg: float, max_magnitude: float = 18.0, **_keywords: Any
    ) -> Table:
        """Record the request and return a small made-up table.

        Parameters
        ----------
        ra_center, dec_center, radius_deg : `float`
            The search circle in degrees.
        max_magnitude : `float`, optional
            The search magnitude limit.
        **_keywords
            Ignored options.

        Returns
        -------
        table : `astropy.table.Table`
            Six stars, with ``meta["row_limit_reached"]`` set as asked.
        """
        calls.append((ra_center, dec_center, radius_deg, max_magnitude))
        count = 6
        table = Table({
            "source_id": [str(index) for index in range(count)],
            "ra": ra_center + np.arange(count) * 1e-3,
            "dec": np.full(count, dec_center),
            "phot_g_mean_mag": np.linspace(9.0, max_magnitude - 0.5, count),
            "designation": [f"Gaia DR3 {index}" for index in range(count)],
            "pmra": np.zeros(count),
            "pmdec": np.zeros(count),
        })
        if row_limit_reached:
            table.meta["row_limit_reached"] = True
        return table

    monkeypatch.setattr(StarIdentifier, "_download_gaia_region", staticmethod(download))
    return calls


def test_a_region_recorded_as_truncated_is_not_reused(config: Any) -> None:
    """Plenty of rows are not enough when the download hit the row limit."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 20.0, row_limit_hit=True)

    assert _lookup(config) is None


def test_a_region_recorded_deeper_than_requested_is_reused(config: Any) -> None:
    """A complete download to G < 20 answers a request for G < 18."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 20.0, row_limit_hit=False)

    cached = _lookup(config)

    assert cached is not None
    assert len(cached[0]) == 12


def test_a_region_recorded_exactly_as_deep_as_requested_is_reused(config: Any) -> None:
    """A limit equal to the request's counts as deep enough."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(
        config, CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE, row_limit_hit=False
    )

    assert _lookup(config) is not None


def test_a_region_recorded_shallower_than_requested_is_not_reused(config: Any) -> None:
    """A download to G < 16 cannot answer a request for G < 18."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 16.0, row_limit_hit=False)

    assert _lookup(config) is None


def test_a_region_that_does_not_cover_the_request_is_not_reused(config: Any) -> None:
    """A smaller or off-centre record leaves part of the request unfetched."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.1, 20.0, row_limit_hit=False)
    catalog_store.record_gaia_region(
        config, CENTRE_RA_DEG + 0.25, CENTRE_DEC_DEG, 0.3, 20.0, row_limit_hit=False
    )

    # Neither circle holds the whole 0.3 degree request circle.
    assert _lookup(config) is None
    # A request that fits inside the small circle is covered.
    assert _lookup(config, radius_deg=0.05) is not None


def test_a_truncated_record_does_not_hide_a_complete_one(config: Any) -> None:
    """One complete record is enough even when another was cut short."""
    catalog_store.insert_gaia_sources(config, _stars())
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 20.0, row_limit_hit=True)
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 18.5, row_limit_hit=False)

    assert _lookup(config) is not None


def test_a_covered_region_with_no_cached_stars_is_not_returned(config: Any) -> None:
    """With nothing to return, the lookup falls through to a download."""
    catalog_store.record_gaia_region(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5, 20.0, row_limit_hit=False)

    assert _lookup(config) is None


def test_an_old_cache_without_the_region_table_triggers_a_download(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stars from before region records existed are of unknown depth."""
    connection = sqlite3.connect(catalog_store.get_catalog_cache_path(config))
    connection.execute(
        "CREATE TABLE gaia_sources "
        "(source_id TEXT PRIMARY KEY, ra REAL, dec REAL, phot_g_mean_mag REAL, designation TEXT)"
    )
    connection.execute(
        "CREATE TABLE cached_regions (region_key TEXT PRIMARY KEY, ra REAL, dec REAL, radius REAL)"
    )
    connection.executemany(
        "INSERT INTO gaia_sources VALUES (?, ?, ?, ?, ?)",
        [(row[0], row[1], row[2], row[3], row[4]) for row in _stars(20)],
    )
    # The old table says this region was downloaded, but not how.
    connection.execute(
        "INSERT INTO cached_regions VALUES (?, ?, ?, ?)",
        ("old", CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.5),
    )
    connection.commit()
    connection.close()
    calls = _fake_download(monkeypatch, row_limit_reached=False)

    table, coords = StarIdentifier._query_gaia_region(
        CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE
    )

    assert len(calls) == 1
    assert table is not None
    assert coords is not None


def test_a_refreshed_old_cache_is_reused_the_next_time(config: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The refresh happens once; the download leaves a covering record."""
    catalog_store.insert_gaia_sources(config, _stars(20, first_id=100))
    calls = _fake_download(monkeypatch, row_limit_reached=False)

    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE)
    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE)

    assert len(calls) == 1


def test_a_download_that_hit_the_row_limit_is_downloaded_again(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cut-short download is recorded as such, so it is never reused."""
    calls = _fake_download(monkeypatch, row_limit_reached=True)

    first, _ = StarIdentifier._query_gaia_region(
        CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE
    )
    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, REQUEST_MAGNITUDE)

    assert first is not None
    assert first.meta["row_limit_reached"] is True
    assert len(calls) == 2


def test_a_deeper_request_downloads_again_after_a_shallower_one(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A download to G < 18 does not answer a later request for G < 19."""
    calls = _fake_download(monkeypatch, row_limit_reached=False)

    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, 18.0)
    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, 18.0)
    StarIdentifier._query_gaia_region(CENTRE_RA_DEG, CENTRE_DEC_DEG, REQUEST_RADIUS_DEG, 19.0)

    assert [call[3] for call in calls] == [18.0, 19.0]


def test_the_bulk_seed_records_its_region_and_skips_a_covered_one(
    config: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Seeding leaves a record, so a second seed downloads nothing."""
    import astroquery.gaia as gaia_module

    queries: list[str] = []
    count = 8
    table = Table({
        "source_id": [str(index) for index in range(count)],
        "ra": CENTRE_RA_DEG + np.arange(count) * 1e-3,
        "dec": np.full(count, CENTRE_DEC_DEG),
        "phot_g_mean_mag": np.linspace(9.0, 17.5, count),
        "designation": [f"Gaia DR3 {index}" for index in range(count)],
        "pmra": np.zeros(count),
        "pmdec": np.zeros(count),
    })

    def launch_job_async(query: str, **_keywords: object) -> MagicMock:
        """Record the query and return a finished job holding the table.

        Parameters
        ----------
        query : `str`
            The ADQL query text.
        **_keywords
            Ignored job options.

        Returns
        -------
        job : `unittest.mock.MagicMock`
            A job whose ``get_results()`` is the made-up table.
        """
        queries.append(query)
        job = MagicMock()
        job.get_results.return_value = table.copy()
        return job

    monkeypatch.setattr(gaia_module.Gaia, "launch_job_async", launch_job_async)

    seeded = StarIdentifier._seed_gaia_cache_for_field(CENTRE_RA_DEG, CENTRE_DEC_DEG, radius_deg=0.5)
    seeded_again = StarIdentifier._seed_gaia_cache_for_field(CENTRE_RA_DEG, CENTRE_DEC_DEG, radius_deg=0.5)
    deeper = StarIdentifier._seed_gaia_cache_for_field(
        CENTRE_RA_DEG, CENTRE_DEC_DEG, radius_deg=0.5, max_magnitude=19.0
    )

    assert seeded == count
    assert seeded_again == 0
    assert deeper == count
    assert len(queries) == 2
    assert catalog_store.is_gaia_region_complete(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.3, 18.0)
    assert catalog_store.is_gaia_region_complete(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.3, 19.0)
    assert not catalog_store.is_gaia_region_complete(config, CENTRE_RA_DEG, CENTRE_DEC_DEG, 0.3, 19.5)
