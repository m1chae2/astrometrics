"""Tests for the Gaia catalog driver's local cache and magnitude limit.

The Planetarium's deep-star layer asks ESA's Gaia archive for stars around
the view. That is slow, so the driver first looks for the circle in the
local star cache, saves what it downloads, and never asks for stars fainter
than the sky map can use. Every test here runs offline: the Gaia archive is
replaced by a fake.
"""

import sys
import threading
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from astropy.table import Table

from astrometricslib import Astrometrics
from wayfindinglib.drivers.catalog import gaia_catalog_driver
from wayfindinglib.drivers.catalog.gaia_catalog_driver import GaiaCatalogDriver

CENTER_RA = 250.0
CENTER_DEC = 36.0
QUERY_RADIUS = 2.0


class _FakeStarCache:
    """A stand-in local star cache that remembers what it was asked."""

    def __init__(self, saved_rows: list[tuple] | None = None) -> None:
        self.saved_rows = saved_rows
        self.find_calls: list[tuple] = []
        self.save_calls: list[tuple] = []

    def find_saved_gaia_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float
    ) -> list[tuple] | None:
        """Record the request and return whatever was saved, if anything.

        Returns
        -------
        rows : `list` [`tuple`] or `None`
            The saved stars, or `None` if nothing was saved.
        """
        self.find_calls.append((ra, dec, radius, magnitude_limit))
        return self.saved_rows

    def save_downloaded_gaia_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float, rows: list[tuple]
    ) -> None:
        """Record what the driver asked to save."""
        self.save_calls.append((ra, dec, radius, magnitude_limit, rows))


class _BrokenCache(_FakeStarCache):
    """A cache whose file is corrupt, so every lookup fails."""

    def find_saved_gaia_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float
    ) -> list[tuple] | None:
        """Fail as a corrupt cache file would.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("database disk image is malformed")


class _ReadOnlyCache(_FakeStarCache):
    """A cache on a read-only disk, so every save fails."""

    def save_downloaded_gaia_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float, rows: list[tuple]
    ) -> None:
        """Fail as a read-only disk would.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("read-only file system")


class _LibraryConfig:
    """A stand-in for `AppConfiguration` that points at a temporary folder."""

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path

    def get_library_path(self) -> Path:
        """Return the sandboxed library root.

        Returns
        -------
        path : `pathlib.Path`
            The temporary directory standing in for the library.
        """
        return self._library_path


def _install_fake_gaia(
    monkeypatch: pytest.MonkeyPatch,
    result_table: Table | None,
    job_error: Exception | None = None,
    block_forever: threading.Event | None = None,
) -> list[str]:
    """Replace ``astroquery.gaia.Gaia`` with a fake.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The test's monkeypatch fixture.
    result_table : `astropy.table.Table` or `None`
        What the fake job returns.
    job_error : `Exception`, optional
        If given, the fake job raises this instead of returning.
    block_forever : `threading.Event`, optional
        If given, the fake job waits for this to be set before finishing.

    Returns
    -------
    calls : `list` [`str`]
        Each ADQL query the driver sent, in order.
    """
    calls: list[str] = []

    class _FakeJob:
        def get_results(self) -> Table | None:
            """Return the canned result table, fail, or hang.

            Returns
            -------
            table : `astropy.table.Table` or `None`
                The canned result.
            """
            if block_forever is not None:
                block_forever.wait()
            if job_error is not None:
                raise job_error
            return result_table

    class _FakeGaia:
        @staticmethod
        def launch_job_async(query: str, **_keyword_arguments: Any) -> _FakeJob:
            """Record the query and hand back a fake job.

            Returns
            -------
            job : `_FakeJob`
                A job that returns the canned result.
            """
            calls.append(query)
            return _FakeJob()

        @staticmethod
        def launch_job(query: str, **_keyword_arguments: Any) -> None:
            """Fail loudly: a synchronous query is limited to 2000 rows.

            Raises
            ------
            AssertionError
                Always.
            """
            raise AssertionError("the driver must use the asynchronous query")

    fake_module = types.ModuleType("astroquery.gaia")
    fake_module.Gaia = _FakeGaia
    monkeypatch.setitem(sys.modules, "astroquery.gaia", fake_module)
    return calls


def _gaia_table(*stars: tuple[str, float, float, float]) -> Table:
    """Build a Gaia-shaped result table.

    Parameters
    ----------
    *stars : `tuple`
        Each star as ``(source_id, ra, dec, magnitude)``.

    Returns
    -------
    table : `astropy.table.Table`
        The stars as the archive would return them.
    """
    return Table({
        "source_id": [star[0] for star in stars],
        "ra": [star[1] for star in stars],
        "dec": [star[2] for star in stars],
        "phot_g_mean_mag": [star[3] for star in stars],
    })


def test_saved_circle_is_served_without_asking_the_archive(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A circle already in the local cache causes no download."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table())
    cache = _FakeStarCache(saved_rows=[("111", 250.0, 36.0, 12.5, "Gaia DR3 111")])

    stars = GaiaCatalogDriver(star_cache=cache).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert calls == []
    assert [star.id for star in stars] == ["GAIA_111"]
    assert stars[0].name == "Gaia DR3 111"
    assert cache.save_calls == []


def test_unsaved_circle_is_downloaded_then_saved_for_next_time(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A miss downloads the circle and saves it, complete to the limit."""
    calls = _install_fake_gaia(
        monkeypatch, _gaia_table(("111", 250.0, 36.0, 9.0), ("222", 250.1, 36.1, 14.0))
    )
    cache = _FakeStarCache(saved_rows=None)

    stars = GaiaCatalogDriver(star_cache=cache).query_region(
        CENTER_RA, CENTER_DEC, QUERY_RADIUS, magnitude_limit=15.0
    )

    assert len(calls) == 1
    assert {star.id for star in stars} == {"GAIA_111", "GAIA_222"}
    assert len(cache.save_calls) == 1
    ra, dec, radius, saved_limit, rows = cache.save_calls[0]
    assert (ra, dec, radius) == (CENTER_RA, CENTER_DEC, QUERY_RADIUS)
    assert saved_limit == pytest.approx(15.0)
    assert [row[0] for row in rows] == ["111", "222"]


def test_query_never_asks_for_stars_fainter_than_the_driver_limit(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The map may ask for G 22 at deep zoom; the driver stops at its limit."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table(("111", 250.0, 36.0, 9.0)))
    cache = _FakeStarCache(saved_rows=None)

    GaiaCatalogDriver(star_cache=cache).query_region(
        CENTER_RA, CENTER_DEC, QUERY_RADIUS, magnitude_limit=22.0
    )

    limit = gaia_catalog_driver._GAIA_MAGNITUDE_LIMIT
    assert f"phot_g_mean_mag < {limit}" in calls[0]
    assert cache.find_calls[0][3] == pytest.approx(limit)
    assert cache.save_calls[0][3] == pytest.approx(limit)


def test_query_uses_the_default_limit_when_none_is_given(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Callers that pass no limit still get a bounded query."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table(("111", 250.0, 36.0, 9.0)))

    GaiaCatalogDriver().query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert f"phot_g_mean_mag < {gaia_catalog_driver._GAIA_MAGNITUDE_LIMIT}" in calls[0]


def test_download_at_the_row_limit_is_only_complete_to_its_faintest_star(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Stars past the row limit were never fetched, so G 16 is not reached."""
    monkeypatch.setattr(gaia_catalog_driver, "_GAIA_ROW_LIMIT", 3)
    _install_fake_gaia(
        monkeypatch,
        _gaia_table(("1", 250.0, 36.0, 8.0), ("2", 250.0, 36.1, 10.0), ("3", 250.0, 36.2, 11.5)),
    )
    cache = _FakeStarCache(saved_rows=None)

    GaiaCatalogDriver(star_cache=cache).query_region(
        CENTER_RA, CENTER_DEC, QUERY_RADIUS, magnitude_limit=16.0
    )

    assert cache.save_calls[0][3] == pytest.approx(11.5)


def test_failed_download_returns_nothing_and_saves_nothing(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A failure must not be saved as "there are no stars here"."""
    _install_fake_gaia(monkeypatch, None, job_error=RuntimeError("archive unreachable"))
    cache = _FakeStarCache(saved_rows=None)

    stars = GaiaCatalogDriver(star_cache=cache).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert stars == []
    assert cache.save_calls == []


def test_missing_result_table_is_a_failure_not_an_empty_sky(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No table at all is not the same as a table with no rows."""
    _install_fake_gaia(monkeypatch, None)
    cache = _FakeStarCache(saved_rows=None)

    stars = GaiaCatalogDriver(star_cache=cache).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert stars == []
    assert cache.save_calls == []


def test_genuinely_empty_table_is_saved_as_a_complete_empty_circle(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A real answer of "no stars this bright here" is worth remembering."""
    _install_fake_gaia(monkeypatch, _gaia_table())
    cache = _FakeStarCache(saved_rows=None)

    stars = GaiaCatalogDriver(star_cache=cache).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert stars == []
    assert len(cache.save_calls) == 1
    assert cache.save_calls[0][4] == []


def test_download_that_never_finishes_is_abandoned_after_the_timeout(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A Gaia job stuck in the server's queue must not hang the request."""
    monkeypatch.setattr(gaia_catalog_driver, "_GAIA_QUERY_TIMEOUT_SECONDS", 0.05)
    release_job = threading.Event()
    _install_fake_gaia(monkeypatch, _gaia_table(), block_forever=release_job)
    cache = _FakeStarCache(saved_rows=None)

    try:
        stars = GaiaCatalogDriver(star_cache=cache).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)
    finally:
        release_job.set()

    assert stars == []
    assert cache.save_calls == []


def test_whole_sky_request_skips_the_circle_cache_and_filter(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A 90 degree radius is not a circle ADQL can express, so no caching."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table(("1", 10.0, 10.0, 6.0)))
    cache = _FakeStarCache(saved_rows=None)

    GaiaCatalogDriver(star_cache=cache).query_region(0.0, 0.0, 180.0, magnitude_limit=8.0)

    assert "CIRCLE" not in calls[0]
    assert cache.find_calls == []
    assert cache.save_calls == []


def test_driver_without_a_cache_always_downloads(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Constructed with no cache (the old behaviour), it still works."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table(("1", 250.0, 36.0, 9.0)))

    stars = GaiaCatalogDriver().query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert len(calls) == 1
    assert [star.id for star in stars] == ["GAIA_1"]


def test_unreadable_cache_falls_back_to_the_download(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The cache only speeds things up; a broken one must not break the map."""
    calls = _install_fake_gaia(monkeypatch, _gaia_table(("1", 250.0, 36.0, 9.0)))

    stars = GaiaCatalogDriver(star_cache=_BrokenCache()).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert len(calls) == 1
    assert [star.id for star in stars] == ["GAIA_1"]


def test_unwritable_cache_still_returns_the_downloaded_stars(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Failing to save is only a lost speed-up, not a lost result."""
    _install_fake_gaia(monkeypatch, _gaia_table(("1", 250.0, 36.0, 9.0)))

    stars = GaiaCatalogDriver(star_cache=_ReadOnlyCache()).query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS)

    assert [star.id for star in stars] == ["GAIA_1"]


def test_second_look_at_the_same_sky_is_served_from_the_real_local_cache(monkeypatch, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """End to end through the real stars API and cache database.

    The first query has to go to the archive. The same view again, and a
    smaller view inside it, must then be answered from disk with no
    download, which is what makes revisiting a patch of sky instant.
    """
    astrometrics = Astrometrics(config=_LibraryConfig(tmp_path), catalog_access=MagicMock())
    calls = _install_fake_gaia(
        monkeypatch,
        _gaia_table(("1", 250.0, 36.0, 9.0), ("2", 250.5, 36.2, 13.0), ("3", 251.2, 36.0, 15.0)),
    )
    driver = GaiaCatalogDriver(star_cache=astrometrics.stars)

    first = driver.query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS, magnitude_limit=16.0)
    second = driver.query_region(CENTER_RA, CENTER_DEC, QUERY_RADIUS, magnitude_limit=16.0)
    zoomed_in = driver.query_region(250.1, 36.0, 0.5, magnitude_limit=14.0)

    assert len(calls) == 1
    assert {star.id for star in first} == {"GAIA_1", "GAIA_2", "GAIA_3"}
    assert {star.id for star in second} == {"GAIA_1", "GAIA_2", "GAIA_3"}
    assert {star.id for star in zoomed_in} == {"GAIA_1", "GAIA_2"}
