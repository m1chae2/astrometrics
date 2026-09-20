"""Tests for the period search a photometry run does on its main stars.

Purpose: a period search takes several seconds per star, so a photometry
run searches only the target's own star and its brightest stars, and does
it after the light curves are saved. These tests check which stars are
chosen, that results are saved without disturbing the rest of the row, and
that one star's failed search does not stop the others.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.batch import (
    MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH,
    MINIMUM_POINTS_FOR_PERIOD_SEARCH,
    search_periods_and_save,
    select_period_search_stars,
)
from astrometricslib.pipelines.photometry.variability_analyzer import VariabilityAnalyzer
from astrometricslib.utilities.config_loader import AppConfiguration

_CENTER_RA = 250.4225
_CENTER_DEC = 36.4603
# The same spot written the way a target stores it.
_TARGET = SimpleNamespace(id="M 13", ra="16h 41m 41.4s", dec="+36° 27′ 37″")
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _star(
    star_id: str,
    mean_flux: float,
    points: int = 12,
    arcseconds_from_center: float = 600.0,
    identified: bool = True,
) -> StellarObject:
    """Build a star with a short, flat light curve.

    Returns
    -------
    star : `StellarObject`
        A star `arcseconds_from_center` north of the test target.
    """
    random_generator = np.random.default_rng(abs(hash(star_id)) % 2**32)
    fluxes = list(1.0 + random_generator.normal(0.0, 0.01, points))
    return StellarObject(
        id=star_id,
        name=star_id,
        right_ascension=_CENTER_RA,
        declination=_CENTER_DEC + arcseconds_from_center / 3600.0,
        magnitude=10.0,
        is_catalog_identified=identified,
        target_ids=["M 13"],
        photometry=PhotometryResult(
            timestamps=[_START + timedelta(minutes=index) for index in range(points)],
            fluxes_normalized=fluxes,
            fluxes_detrended=fluxes,
            mean_flux=mean_flux,
        ),
    )


def test_the_targets_own_star_comes_first_then_the_brightest():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the target star comes first, then falling brightness."""
    stars = [
        _star("HD 1", mean_flux=50.0, arcseconds_from_center=900.0),
        _star("HD 2", mean_flux=5.0, arcseconds_from_center=3.0),  # the target's own, and faint
        _star("HD 3", mean_flux=80.0, arcseconds_from_center=400.0),
        _star("HD 4", mean_flux=20.0, arcseconds_from_center=700.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC, limit=2)

    assert [star.id for star in chosen] == ["HD 2", "HD 3", "HD 1"]


def test_only_stars_with_enough_measurements_are_chosen():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a star with too few points is never chosen."""
    too_few = MINIMUM_POINTS_FOR_PERIOD_SEARCH - 1
    stars = [
        _star("HD 1", mean_flux=100.0, points=too_few, arcseconds_from_center=1.0),
        _star("HD 2", mean_flux=10.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC)

    assert [star.id for star in chosen] == ["HD 2"]


def test_a_position_only_star_is_never_the_targets_star():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a position-only star is never the target's star."""
    stars = [
        _star("FIELD_J250.4225+36.4604", mean_flux=1.0, arcseconds_from_center=1.0, identified=False),
        _star("HD 2", mean_flux=10.0, arcseconds_from_center=500.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC)

    # HD 2 is the target's star (the only identified one). The position-only
    # star can still be chosen, but only as one of the bright stars.
    assert [star.id for star in chosen] == ["HD 2", "FIELD_J250.4225+36.4604"]


def test_without_target_coordinates_only_the_brightest_are_chosen():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unknown target position gives just the brightest stars."""
    stars = [_star(f"HD {number}", mean_flux=float(number)) for number in range(1, 16)]

    chosen = select_period_search_stars(stars, None, None)

    assert len(chosen) == MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH
    assert [star.id for star in chosen[:2]] == ["HD 15", "HD 14"]


@pytest.fixture
def catalog_access(tmp_path: Path) -> CatalogAccess:
    """Give the test its own empty catalog.

    Returns
    -------
    catalog_access : `CatalogAccess`
        Catalog access over a library that holds no stars.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return CatalogAccess(config)


def test_results_are_saved_without_disturbing_the_rest_of_the_row(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the search adds a periodogram and changes nothing else."""
    stars = [_star("HD 1", mean_flux=50.0, arcseconds_from_center=2.0), _star("HD 2", mean_flux=40.0)]
    catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)

    searched_count = search_periods_and_save(stars, _TARGET, catalog_access)

    assert searched_count == 2
    for row in catalog_access.get_by_ids("stellar_catalog", ["HD 1", "HD 2"]):
        assert row.photometry.periodogram is not None
        assert row.photometry.periodogram.verdict in (
            "detected",
            "possible",
            "not_detected",
            "insufficient_data",
        )
        assert len(row.photometry.timestamps) == 12
        assert row.magnitude == pytest.approx(10.0)
        assert row.target_ids == ["M 13"]


def test_one_stars_failed_search_does_not_stop_the_others(catalog_access, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a search that raises is skipped and the rest are saved."""
    stars = [_star("HD 1", mean_flux=50.0, arcseconds_from_center=2.0), _star("HD 2", mean_flux=40.0)]
    catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)
    real_search = VariabilityAnalyzer.run_lomb_scargle_periodogram

    def flaky_search(self: VariabilityAnalyzer, star: StellarObject) -> object:
        if star.id == "HD 1":
            raise RuntimeError("the search broke")
        return real_search(self, star)

    monkeypatch.setattr(VariabilityAnalyzer, "run_lomb_scargle_periodogram", flaky_search)

    searched_count = search_periods_and_save(stars, _TARGET, catalog_access)

    assert searched_count == 1
    (broken,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    (working,) = catalog_access.get_by_ids("stellar_catalog", ["HD 2"])
    assert broken.photometry.periodogram is None
    assert working.photometry.periodogram is not None


def test_nothing_is_saved_when_no_star_can_be_searched(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a run with no usable light curves does nothing."""
    stars = [_star("HD 1", mean_flux=50.0, points=2)]

    assert search_periods_and_save(stars, _TARGET, catalog_access) == 0
