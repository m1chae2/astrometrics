"""Tests for the driver that reads the downloaded Gaia deep-star catalog.

The Planetarium's faint-star layer reads from a copy of Gaia DR3 saved on
this computer. These tests check what the driver hands the map, that it
copes with the catalog not being downloaded yet, and (end to end, through
the real stars API and database) that a downloaded star comes back.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from astrometricslib import Astrometrics
from wayfindinglib.drivers.catalog import deep_star_catalog_driver
from wayfindinglib.drivers.catalog.deep_star_catalog_driver import DeepStarCatalogDriver
from wayfindinglib.skylib.catalog_operations import build_catalog_driver_registry


class _RecordingSource:
    """A stand-in star source that remembers how it was asked."""

    def __init__(self, stars: list[tuple[int, float, float, float]] | None) -> None:
        self._stars = stars
        self.calls: list[tuple] = []

    def find_deep_stars(
        self, ra: float, dec: float, radius: float, magnitude_limit: float, maximum_stars: int | None = None
    ) -> list[tuple[int, float, float, float]] | None:
        """Record the request and return the canned stars.

        Returns
        -------
        stars : `list` [`tuple`] or `None`
            The canned stars, or `None` to mean "not downloaded".
        """
        self.calls.append((ra, dec, radius, magnitude_limit, maximum_stars))
        return self._stars


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


def test_driver_without_a_source_returns_no_stars():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Constructed with nothing to read from, it is silent, not broken."""
    assert DeepStarCatalogDriver().query_region(250.0, 36.0, 2.0, 16.0) == []


def test_catalog_not_downloaded_gives_no_stars_not_an_error():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A source that says no catalog is installed (None) gives no stars."""
    driver = DeepStarCatalogDriver(star_source=_RecordingSource(None))

    assert driver.query_region(250.0, 36.0, 2.0, 16.0) == []


def test_driver_turns_catalog_rows_into_stars_named_like_the_library_names_them():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """IDs match the library's, so a star in both is only drawn once."""
    source = _RecordingSource([(1067177056683315584, 250.1, 36.2, 9.5), (42, 250.2, 36.3, 15.25)])

    stars = DeepStarCatalogDriver(star_source=source).query_region(250.0, 36.0, 2.0, 16.0)

    # The first ID is a real one from the star library, which names Gaia
    # stars exactly "Gaia DR3 <source_id>".
    assert [star.id for star in stars] == ["Gaia DR3 1067177056683315584", "Gaia DR3 42"]
    assert stars[0].name == "Gaia DR3 1067177056683315584"
    assert stars[0].right_ascension == pytest.approx(250.1)
    assert stars[0].declination == pytest.approx(36.2)
    assert stars[1].magnitude == pytest.approx(15.25)


def test_driver_passes_the_limit_and_a_cap_to_the_source():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The magnitude limit and the star cap reach the database lookup."""
    source = _RecordingSource([])

    DeepStarCatalogDriver(star_source=source).query_region(250.0, 36.0, 2.0, 13.0)

    ra, dec, radius, limit, cap = source.calls[0]
    assert (ra, dec, radius, limit) == (250.0, 36.0, 2.0, 13.0)
    assert cap == deep_star_catalog_driver._MAXIMUM_STARS_PER_QUERY


def test_no_limit_means_no_star_is_excluded_by_magnitude():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A caller that gives no limit still gets a valid, generous one."""
    source = _RecordingSource([])

    DeepStarCatalogDriver(star_source=source).query_region(250.0, 36.0, 2.0)

    assert source.calls[0][3] >= 30.0


def test_registry_holds_only_drivers_that_read_from_this_computer():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """No live SIMBAD or Gaia driver is registered any more."""
    registry = build_catalog_driver_registry()

    assert set(registry) == {"hipparcos", "deep_stars"}
    assert registry["deep_stars"].driver_name == "deep_stars"


def test_downloaded_stars_come_back_through_the_real_stars_api(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """End to end: save stars, then read them through the facade and driver."""
    # The stars API only reads the catalog; the download script is what
    # writes it. This test seeds it the same way the script does.
    from astrometricslib.drivers import deep_star_store  # ruff: ignore[banned-api]

    config = _LibraryConfig(tmp_path)
    astrometrics = Astrometrics(config=config, catalog_access=MagicMock())
    driver = DeepStarCatalogDriver(star_source=astrometrics.stars)

    # Nothing downloaded yet.
    assert driver.query_region(250.0, 36.0, 2.0, 16.0) == []
    assert astrometrics.stars.get_deep_catalog_status()["installed"] is False

    deep_star_store.record_downloaded_pixel(
        config,
        0,
        np.array([111, 222, 333], dtype=np.int64),
        np.array([250.0, 250.5, 100.0]),
        np.array([36.0, 36.2, -20.0]),
        np.array([9.0, 14.0, 12.0]),
    )

    stars = driver.query_region(250.2, 36.1, 1.0, 15.0)

    assert [star.id for star in stars] == ["Gaia DR3 111", "Gaia DR3 222"]
    assert astrometrics.stars.get_deep_catalog_status()["installed"] is True
    assert astrometrics.stars.find_deep_stars(100.0, -20.0, 0.5, 16.0) == [(333, 100.0, -20.0, 12.0)]
