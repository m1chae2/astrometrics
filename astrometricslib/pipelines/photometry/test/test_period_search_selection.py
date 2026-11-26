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

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.run_abandonment import RunAbandonedError
from astrometricslib.models.stellar_source import (
    MINIMUM_POINTS_FOR_PERIOD_SEARCH,
    PhotometryResult,
    StellarObject,
)
from astrometricslib.pipelines.photometry import batch
from astrometricslib.pipelines.photometry.batch import (
    MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH,
    search_periods_and_save,
    select_period_search_stars,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

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


def test_the_targets_own_star_comes_first_then_the_brightest() -> None:
    """Verify the target star comes first, then falling brightness."""
    stars = [
        _star("HD 1", mean_flux=50.0, arcseconds_from_center=900.0),
        _star("HD 2", mean_flux=5.0, arcseconds_from_center=3.0),  # the target's own, and faint
        _star("HD 3", mean_flux=80.0, arcseconds_from_center=400.0),
        _star("HD 4", mean_flux=20.0, arcseconds_from_center=700.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC, limit=2)

    assert [star.id for star in chosen] == ["HD 2", "HD 3", "HD 1"]


def test_only_stars_with_enough_measurements_are_chosen() -> None:
    """Verify a star with too few points is never chosen."""
    too_few = MINIMUM_POINTS_FOR_PERIOD_SEARCH.default - 1
    stars = [
        _star("HD 1", mean_flux=100.0, points=too_few, arcseconds_from_center=1.0),
        _star("HD 2", mean_flux=10.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC)

    assert [star.id for star in chosen] == ["HD 2"]


def test_a_position_only_star_is_never_the_targets_star() -> None:
    """Verify a position-only star is never the target's star."""
    stars = [
        _star("FIELD_J250.4225+36.4604", mean_flux=1.0, arcseconds_from_center=1.0, identified=False),
        _star("HD 2", mean_flux=10.0, arcseconds_from_center=500.0),
    ]

    chosen = select_period_search_stars(stars, _CENTER_RA, _CENTER_DEC)

    # HD 2 is the target's star (the only identified one). The position-only
    # star can still be chosen, but only as one of the bright stars.
    assert [star.id for star in chosen] == ["HD 2", "FIELD_J250.4225+36.4604"]


def test_without_target_coordinates_only_the_brightest_are_chosen() -> None:
    """Verify an unknown target position gives just the brightest stars."""
    stars = [_star(f"HD {number}", mean_flux=float(number)) for number in range(1, 16)]

    chosen = select_period_search_stars(stars, None, None)

    assert len(chosen) == MAXIMUM_BRIGHTEST_STARS_FOR_PERIOD_SEARCH.default
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


def test_results_are_saved_without_disturbing_the_rest_of_the_row(catalog_access: CatalogAccess) -> None:
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


def test_one_stars_failed_search_does_not_stop_the_others(
    catalog_access: CatalogAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a search that raises is skipped and the rest are saved."""
    stars = [_star("HD 1", mean_flux=50.0, arcseconds_from_center=2.0), _star("HD 2", mean_flux=40.0)]
    catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)
    real_search = VariabilityAnalyzer.run_lomb_scargle_periodogram

    def flaky_search(self: VariabilityAnalyzer, star: StellarObject) -> object:
        if star.id == "HD 1":
            raise ValueError("the search broke")
        return real_search(self, star)

    monkeypatch.setattr(VariabilityAnalyzer, "run_lomb_scargle_periodogram", flaky_search)

    searched_count = search_periods_and_save(stars, _TARGET, catalog_access)

    assert searched_count == 1
    (broken,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    (working,) = catalog_access.get_by_ids("stellar_catalog", ["HD 2"])
    assert broken.photometry.periodogram is None
    assert working.photometry.periodogram is not None


def test_nothing_is_saved_when_no_star_can_be_searched(catalog_access: CatalogAccess) -> None:
    """Verify a run with no usable light curves does nothing."""
    stars = [_star("HD 1", mean_flux=50.0, points=2)]

    assert search_periods_and_save(stars, _TARGET, catalog_access) == 0


@pytest.mark.parametrize("points_left", [0, 1, 2])
def test_a_star_cut_short_by_saturation_is_skipped_and_the_slot_goes_on(points_left: int) -> None:
    """Verify a star left with 0, 1 or 2 points is skipped for the next star.

    Saturated-point removal can leave a star with almost no light curve; it
    must not be searched, and the next-brightest star takes its place.
    """
    short = _star("HD 1", mean_flux=100.0, points=12)
    short.photometry.timestamps = short.photometry.timestamps[:points_left]
    short.photometry.fluxes_normalized = short.photometry.fluxes_normalized[:points_left]
    short.photometry.fluxes_detrended = short.photometry.fluxes_detrended[:points_left]
    stars = [short, _star("HD 2", mean_flux=50.0), _star("HD 3", mean_flux=10.0)]

    chosen = select_period_search_stars(stars, None, None, limit=2)

    assert [star.id for star in chosen] == ["HD 2", "HD 3"]
    analyzer = VariabilityAnalyzer()
    assert analyzer.run_lomb_scargle_periodogram(short) is None
    assert analyzer.run_bls_transit_search(short) is None


class _RecordingJob:
    """A stand-in for a running job that keeps the progress it is told."""

    def __init__(self) -> None:
        """Start with no reports."""
        self.reports: list[tuple[int, int, str]] = []

    def report_count(self, done: int, total: int, message: str) -> None:
        """Keep one progress report.

        Parameters
        ----------
        done : `int`
            How many items are finished.
        total : `int`
            How many items there are.
        message : `str`
            The status line.
        """
        self.reports.append((done, total, message))


def test_each_star_is_reported_to_the_running_job(
    catalog_access: CatalogAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify the job hears which star is searched, then a final count."""
    stars = [_star("HD 1", mean_flux=50.0, arcseconds_from_center=2.0), _star("HD 2", mean_flux=40.0)]
    catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)
    job = _RecordingJob()
    monkeypatch.setattr(batch, "get_current_job", lambda: job)

    search_periods_and_save(stars, _TARGET, catalog_access)

    assert [(done, total) for done, total, _ in job.reports[:2]] == [(0, 2), (1, 2)]
    assert "Searching star 1 of 2 for repeating patterns (HD 1)" in job.reports[0][2]
    assert "M 13" in job.reports[0][2]
    assert job.reports[-1][:2] == (2, 2)
    assert "Searched 2 star(s)" in job.reports[-1][2]


def test_a_cancelled_job_stops_the_period_searches(
    catalog_access: CatalogAccess, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify a cancel is not swallowed as one star's failed search."""
    stars = [_star("HD 1", mean_flux=50.0, arcseconds_from_center=2.0), _star("HD 2", mean_flux=40.0)]
    catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)
    checks: list[int] = []

    def cancel_on_second_check() -> None:
        """Raise on the second check, as a cancel during star 2 would.

        Raises
        ------
        RunAbandonedError
            From the second call on.
        """
        checks.append(1)
        if len(checks) >= 2:
            raise RunAbandonedError("cancelled")

    monkeypatch.setattr(batch, "raise_if_abandoned", cancel_on_second_check)

    with pytest.raises(RunAbandonedError):
        search_periods_and_save(stars, _TARGET, catalog_access)

    (first,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    assert first.photometry.periodogram is None  # nothing was saved after the cancel


@pytest.mark.parametrize(
    ("search_name", "points_needed"),
    [("run_lomb_scargle_periodogram", 5), ("run_bls_transit_search", 8)],
)
def test_a_star_with_too_few_finite_points_gets_a_reason_not_silence(
    search_name: str, points_needed: int
) -> None:
    """Verify non-finite points are left out and the note counts the rest."""
    star = _star("HD 1", mean_flux=50.0, points=12)
    usable = points_needed - 1
    for index in range(usable, 12):
        star.photometry.fluxes_detrended[index] = float("nan")

    result = getattr(VariabilityAnalyzer(), search_name)(star)

    assert result.verdict == "insufficient_data"
    assert f"only {usable} usable measurements" in result.note
    assert f"{12 - usable} left out" in result.note
    assert f"{points_needed} are needed" in result.note
    saved = (
        star.photometry.periodogram
        if search_name == "run_lomb_scargle_periodogram"
        else (star.photometry.transit_candidate)
    )
    assert saved is result
