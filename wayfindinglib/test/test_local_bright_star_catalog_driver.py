"""Tests for the Hipparcos bright-star driver's cap and truncation flag."""

import numpy as np
import pytest

from wayfindinglib.drivers.catalog import local_bright_star_catalog_driver as driver_module
from wayfindinglib.drivers.catalog.local_bright_star_catalog_driver import LocalBrightStarCatalogDriver


def _driver_with_rows(star_count: int) -> LocalBrightStarCatalogDriver:
    """Build a driver whose catalog holds ``star_count`` stars near (10, 0).

    Parameters
    ----------
    star_count : int
        Number of stars to place in the in-memory catalog.

    Returns
    -------
    driver : `LocalBrightStarCatalogDriver`
        A driver with its catalog cache already filled.
    """
    rows = np.zeros(star_count, dtype=driver_module._ROW_DTYPE)
    rows["hip_id"] = np.arange(star_count)
    rows["ra"] = 10.0
    rows["dec"] = 0.0
    rows["magnitude"] = np.arange(star_count, dtype=float)
    driver = LocalBrightStarCatalogDriver()
    driver._cached_rows = rows
    return driver


def test_with_status_reports_truncated_when_cap_cuts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """More matches than the cap give the brightest and truncated=True."""
    monkeypatch.setattr(driver_module, "_MAX_RESULTS", 3)
    driver = _driver_with_rows(5)

    objects, truncated = driver.query_region_with_status(10.0, 0.0, 1.0)

    assert truncated is True
    assert [obj.id for obj in objects] == ["HIP_0", "HIP_1", "HIP_2"]
    assert len(driver.query_region(10.0, 0.0, 1.0)) == 3


def test_with_status_not_truncated_under_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """At or under the cap, nothing is left out."""
    monkeypatch.setattr(driver_module, "_MAX_RESULTS", 5)
    objects, truncated = _driver_with_rows(5).query_region_with_status(10.0, 0.0, 1.0)

    assert truncated is False
    assert len(objects) == 5
