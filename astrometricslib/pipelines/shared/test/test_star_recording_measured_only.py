"""Tests that the star catalog keeps only stars the library measured.

Purpose: a star record is saved only when it holds a light curve or a
spectrum. Astrometry measures neither, so it may rename a saved star but
never adds one; photometry saves the stars it measured and spectroscopy the
stars with a spectrum. These tests run `record_pipeline_stars` end to end on
a throwaway catalog (never the real one).
"""

from pathlib import Path

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject
from astrometricslib.pipelines.shared.star_recording import (
    merge_astrometry_stellar_object,
    merge_photometry_stellar_object,
    merge_spectroscopy_stellar_object,
    record_pipeline_stars,
)


@pytest.fixture
def catalog_access(tmp_path: Path) -> CatalogAccess:
    """Give each test its own empty catalog inside the test's temporary folder.

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


def _star(star_id: str, declination: float, **fields: object) -> StellarObject:
    """Build a named star far enough from the others to stay its own star.

    Returns
    -------
    star : `StellarObject`
        A catalog-identified star of M 13.
    """
    return StellarObject(
        id=star_id,
        name=star_id,
        right_ascension=250.0,
        declination=declination,
        is_catalog_identified=True,
        **{"target_ids": ["M 13"], **fields},
    )


def _light_curve() -> PhotometryResult:
    """Build a two-point light curve.

    Returns
    -------
    photometry : `PhotometryResult`
        The light curve.
    """
    return PhotometryResult(timestamps=["2026-01-01T00:00:00", "2026-01-01T00:01:00"], fluxes=[5.0, 5.1])


def _spectrum() -> SpectroscopyResult:
    """Build a short measured spectrum.

    Returns
    -------
    spectroscopy : `SpectroscopyResult`
        The spectrum.
    """
    return SpectroscopyResult(wavelengths_angstrom=[4000.0, 4100.0], intensities=[1.0, 1.0])


def _saved_ids(catalog_access: CatalogAccess) -> list[str]:
    """List every id in the catalog.

    Returns
    -------
    ids : `list` [`str`]
        The ids, sorted.
    """
    return sorted(catalog_access.list_star_ids())


def test_astrometry_stars_are_not_saved(catalog_access: CatalogAccess) -> None:
    """Verify an astrometry run adds no star record."""
    stars, _ = record_pipeline_stars(
        [_star("Gaia DR3 1", 36.0), _star("Gaia DR3 2", 36.1)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_astrometry_stellar_object,
        already_dropped=True,
    )

    assert [star.id for star in stars] == ["Gaia DR3 1", "Gaia DR3 2"]
    assert _saved_ids(catalog_access) == []


def test_astrometry_still_renames_a_saved_measured_star(catalog_access: CatalogAccess) -> None:
    """Verify a saved star with a light curve takes astrometry's names."""
    catalog_access.merge_and_record(
        "stellar_catalog",
        [_star("Gaia DR3 1", 36.0, photometry=_light_curve())],
        lambda _existing, updated: updated,
    )

    record_pipeline_stars(
        [_star("Gaia DR3 1", 36.0, aliases=["HD 1"], target_ids=["M 92"])],
        catalog_access=catalog_access,
        target_id="M 92",
        merge_function=merge_astrometry_stellar_object,
        already_dropped=True,
    )

    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert row.name == "HD 1"
    assert sorted(row.target_ids) == ["M 13", "M 92"]
    assert row.photometry.fluxes == [5.0, 5.1]


def test_photometry_saves_only_the_stars_it_measured(catalog_access: CatalogAccess) -> None:
    """Verify a star photometry could not measure is not saved."""
    withheld = PhotometryResult(light_curve_withheld_reason="saturated in every frame")
    record_pipeline_stars(
        [
            _star("Gaia DR3 1", 36.0, photometry=_light_curve()),
            _star("Gaia DR3 2", 36.1, photometry=withheld),
            _star("Gaia DR3 3", 36.2),
        ],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_photometry_stellar_object,
        pipeline_name="photometry",
        measured_only=True,
    )

    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]


def test_an_unmeasured_photometry_star_leaves_its_saved_row_alone(catalog_access: CatalogAccess) -> None:
    """Verify a run that did not measure a saved star does not empty it."""
    catalog_access.merge_and_record(
        "stellar_catalog",
        [_star("Gaia DR3 1", 36.0, photometry=_light_curve())],
        lambda _existing, updated: updated,
    )

    record_pipeline_stars(
        [_star("Gaia DR3 1", 36.0, photometry=PhotometryResult())],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_photometry_stellar_object,
        pipeline_name="photometry",
        measured_only=True,
    )

    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert row.photometry.fluxes == [5.0, 5.1]


def test_spectroscopy_saves_a_star_with_only_a_spectrum(catalog_access: CatalogAccess) -> None:
    """Verify a star with a spectrum and no light curve is saved."""
    record_pipeline_stars(
        [_star("Gaia DR3 1", 36.0, spectroscopy=_spectrum()), _star("Gaia DR3 2", 36.1)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_spectroscopy_stellar_object,
        pipeline_name="spectroscopy",
        measured_only=True,
    )

    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert row.has_spectra
