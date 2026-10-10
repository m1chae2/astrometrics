"""Purpose: Check that a saved star keeps its photometry and spectrum.

Description: A star is saved to the catalog and loaded again. These tests
build a star whose photometry and spectroscopy carry the newest fields (flux
errors, BJD times, per-session summaries, variability scores, spectral
ambiguity, extraction diagnostics, extinction record) and check that every
one of them comes back with an equal value. They save the star in two ways:
straight into the catalog, and through the pipeline merge rules
(`merge_photometry_stellar_object` and `merge_spectroscopy_stellar_object`),
which replace a whole result object. The catalog is a throwaway one inside
the test's temporary folder, never the real one.
"""

from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import (
    ExtinctionCorrectionRecord,
    PhotometryResult,
    SessionPhotometrySummary,
    SpectralExtractionDiagnostics,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.shared.star_recording import (
    merge_photometry_stellar_object,
    merge_spectroscopy_stellar_object,
    record_pipeline_stars,
)

STAR_ID = "HD 86728"
"""A catalog name, so the star counts as identified and is not dropped."""


@pytest.fixture
def catalog_access(tmp_path: Path) -> CatalogAccess:
    """Give each test its own empty catalog inside the test's temporary folder.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test's temporary folder.

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


def _photometry() -> PhotometryResult:
    """Build a light curve that fills the newest photometry fields.

    Returns
    -------
    photometry : `PhotometryResult`
        Three points with errors, BJD times, two session summaries and the
        variability numbers.
    """
    return PhotometryResult(
        timestamps=[
            datetime(2026, 5, 1, 0, 0, 0),
            datetime(2026, 5, 1, 0, 1, 0),
            datetime(2026, 5, 1, 0, 2, 0),
        ],
        fluxes=[100.0, 101.5, 99.25],
        flux_errors=[0.5, 0.625, 0.5],
        fluxes_normalized=[1.0, 1.015, 0.9925],
        fluxes_normalized_errors=[0.005, 0.00625, 0.005],
        time_bjd_tdb=[2461161.5003, 2461161.5010, 2461161.5017],
        time_basis="mid-exposure BJD_TDB, observer at Earth's centre",
        errors_assume_unit_gain=True,
        errors_assume_zero_read_noise=False,
        is_saturated=[False, False, False],
        mean_flux=100.25,
        coefficient_of_variation=0.0075,
        session_summaries=[
            SessionPhotometrySummary(
                session_id="2026-05-01",
                point_count=3,
                median_normalized_flux=1.0,
                normalized_flux_scatter=0.01,
                comparison_star_count=2,
                ensemble_median_flux=250.5,
                comparison_star_ids=["HD 1", "HD 2"],
                comparison_scatter_mag=0.004,
                comparison_rejected_count=1,
            ),
            SessionPhotometrySummary(
                session_id="2026-05-02",
                point_count=2,
                median_normalized_flux=1.03,
                comparison_star_ids=["HD 1", "HD 3"],
            ),
        ],
        between_session_amplitude_mag=0.0321,
        between_session_significance=4.5,
        instrumental_mag=-5.0,
        rms_mag=0.0081,
        excess_scatter=0.002,
        reduced_chi_square=1.7,
        stetson_j=0.42,
        variability_score=0.77,
    )


def _spectroscopy() -> SpectroscopyResult:
    """Build a spectrum that fills the newest spectroscopy fields.

    Returns
    -------
    spectroscopy : `SpectroscopyResult`
        A short spectrum with three reference candidates, which gives a
        close call (`is_ambiguous`), extraction diagnostics and an
        extinction record.
    """
    return SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 4100.0, 4200.0],
        intensities=[1.0, 1.1, 1.2],
        self_determined_spectral_type="K2V",
        self_determined_spectral_type_rms=0.05,
        self_determined_spectral_type_candidates=[
            {"spectral_type": "K2V", "rms": 0.05, "correlation": 0.99},
            {"spectral_type": "K3V", "rms": 0.06, "correlation": 0.98},
            {"spectral_type": "G8V", "rms": 0.12, "correlation": 0.9},
        ],
        extraction_diagnostics=SpectralExtractionDiagnostics(
            sky_mode_counts={"both_bands": 40, "upper_band_contaminated": 10},
            dominant_sky_mode="both_bands",
            contaminated_sky_fraction=0.2,
            aperture_half_width_median_px=6.5,
            aperture_half_width_spread_px=0.75,
        ),
        extinction_correction=ExtinctionCorrectionRecord(
            is_applied=True,
            target_airmass=1.25,
            reference_airmass=1.05,
            curve_name="La Silla",
            reason=None,
        ),
    )


def _star(**fields: Any) -> StellarObject:
    """Build the test star.

    Parameters
    ----------
    **fields : `Any`
        Extra `StellarObject` fields, such as `photometry`.

    Returns
    -------
    star : `StellarObject`
        A catalog-identified star at a fixed position.
    """
    return StellarObject(
        id=STAR_ID,
        name=STAR_ID,
        right_ascension=250.0,
        declination=36.0,
        target_ids=["M 13"],
        is_catalog_identified=True,
        **fields,
    )


def _reload(catalog_access: CatalogAccess) -> StellarObject:
    """Load the test star back from the catalog.

    Parameters
    ----------
    catalog_access : `CatalogAccess`
        The catalog the star was saved into.

    Returns
    -------
    star : `StellarObject`
        The saved row.
    """
    (row,) = catalog_access.get_by_ids("stellar_catalog", [STAR_ID])
    return row


def _dump(result: Any) -> dict[str, Any]:
    """Turn a photometry or spectroscopy result into plain data.

    Parameters
    ----------
    result : `PhotometryResult` or `SpectroscopyResult`
        The result to dump.

    Returns
    -------
    data : `dict` [`str`, `Any`]
        Every field, including the computed ones, under its alias.
    """
    return result.model_dump(mode="json", by_alias=True)


def _assert_photometry_round_trips(sent: PhotometryResult, loaded: PhotometryResult | None) -> None:
    """Check the newest photometry fields one by one, then the whole result.

    Parameters
    ----------
    sent : `PhotometryResult`
        The result that was saved.
    loaded : `PhotometryResult` or `None`
        The result that was loaded.
    """
    assert loaded is not None
    assert loaded.flux_errors == sent.flux_errors
    assert loaded.fluxes_normalized_errors == sent.fluxes_normalized_errors
    assert loaded.time_bjd_tdb == sent.time_bjd_tdb
    assert loaded.time_basis == sent.time_basis
    assert loaded.session_summaries == sent.session_summaries
    assert [s.comparison_star_ids for s in loaded.session_summaries] == [["HD 1", "HD 2"], ["HD 1", "HD 3"]]
    assert loaded.between_session_amplitude_mag == sent.between_session_amplitude_mag
    assert loaded.variability_score == sent.variability_score
    assert loaded.stetson_j == sent.stetson_j
    assert _dump(loaded) == _dump(sent)


def _assert_spectroscopy_round_trips(sent: SpectroscopyResult, loaded: SpectroscopyResult | None) -> None:
    """Check the newest spectroscopy fields one by one, then the whole result.

    Parameters
    ----------
    sent : `SpectroscopyResult`
        The result that was saved.
    loaded : `SpectroscopyResult` or `None`
        The result that was loaded.
    """
    assert loaded is not None
    assert loaded.rms_gap_to_second_best == pytest.approx(0.01)
    assert loaded.rms_gap_to_second_best == sent.rms_gap_to_second_best
    assert loaded.is_ambiguous is True
    assert loaded.is_ambiguous == sent.is_ambiguous
    assert loaded.extraction_diagnostics == sent.extraction_diagnostics
    assert loaded.extinction_correction == sent.extinction_correction
    assert _dump(loaded) == _dump(sent)


def test_new_photometry_and_spectroscopy_fields_survive_a_plain_save(catalog_access: CatalogAccess) -> None:
    """Verify a star saved as it is comes back with every new field equal."""
    star = _star(photometry=_photometry(), spectroscopy=_spectroscopy())
    catalog_access.merge_and_record("stellar_catalog", [star], lambda _existing, updated: updated)

    loaded = _reload(catalog_access)

    _assert_photometry_round_trips(star.photometry, loaded.photometry)
    _assert_spectroscopy_round_trips(star.spectroscopy, loaded.spectroscopy)


def test_new_photometry_fields_survive_the_photometry_merge(catalog_access: CatalogAccess) -> None:
    """Verify the photometry merge rule keeps every new field."""
    first = _photometry()
    record_pipeline_stars(
        [_star(photometry=first)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_photometry_stellar_object,
        already_dropped=True,
    )
    _assert_photometry_round_trips(first, _reload(catalog_access).photometry)

    # A repeat run replaces the whole result, so the second run's values win.
    second = _photometry()
    second.flux_errors = [0.4, 0.5, 0.4]
    second.session_summaries[0].comparison_star_ids = ["HD 7", "HD 8", "HD 9"]
    second.between_session_amplitude_mag = 0.05
    second.stetson_j = 0.9
    second.variability_score = 0.1
    record_pipeline_stars(
        [_star(photometry=second)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_photometry_stellar_object,
        already_dropped=True,
    )
    loaded = _reload(catalog_access).photometry
    assert loaded is not None
    assert loaded.session_summaries[0].comparison_star_ids == ["HD 7", "HD 8", "HD 9"]
    assert _dump(loaded) == _dump(second)


def test_new_spectroscopy_fields_survive_the_spectroscopy_merge(catalog_access: CatalogAccess) -> None:
    """Verify the spectroscopy merge rule keeps every new field."""
    first = _spectroscopy()
    record_pipeline_stars(
        [_star(spectroscopy=first)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_spectroscopy_stellar_object,
        already_dropped=True,
    )
    _assert_spectroscopy_round_trips(first, _reload(catalog_access).spectroscopy)

    second = _spectroscopy()
    second.extinction_correction = ExtinctionCorrectionRecord(
        is_applied=False, curve_name="La Silla", reason="no airmass in the header"
    )
    record_pipeline_stars(
        [_star(spectroscopy=second)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_spectroscopy_stellar_object,
        already_dropped=True,
    )
    loaded = _reload(catalog_access).spectroscopy
    assert loaded is not None
    assert loaded.extinction_correction == second.extinction_correction
    assert _dump(loaded) == _dump(second)


def test_photometry_and_spectroscopy_saved_by_different_runs_both_survive(
    catalog_access: CatalogAccess,
) -> None:
    """Verify a photometry run keeps a saved spectrum, and the reverse."""
    spectroscopy = _spectroscopy()
    record_pipeline_stars(
        [_star(spectroscopy=spectroscopy)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_spectroscopy_stellar_object,
        already_dropped=True,
    )
    photometry = _photometry()
    record_pipeline_stars(
        [_star(photometry=photometry)],
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_photometry_stellar_object,
        already_dropped=True,
    )

    loaded = _reload(catalog_access)

    _assert_photometry_round_trips(photometry, loaded.photometry)
    _assert_spectroscopy_round_trips(spectroscopy, loaded.spectroscopy)
