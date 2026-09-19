"""Purpose: Tests for merging catalog rows that are one star.

Description: Checks the clustering by sky position, the choice of which
name survives, and the safe joining of two light curves, then runs the
merge end to end on a throwaway catalog -- never the real one.
"""

from datetime import UTC, datetime, timedelta

from astrometricslib.drivers.catalog_access import CatalogAccess, StarSummary
from astrometricslib.models.stellar_source import (
    PhotometryResult,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.scripts.merge_duplicate_catalog_stars import (
    catalog_family,
    choose_survivor_id,
    find_duplicate_clusters,
    merge_cluster,
    merge_light_curves,
)
from astrometricslib.utilities.config_loader import AppConfiguration

_START = datetime(2026, 1, 1, tzinfo=UTC)


def _summary(star_id: str, ra: float, dec: float) -> StarSummary:
    return StarSummary(id=star_id, right_ascension=ra, declination=dec)


def _curve(minutes: list[int], flux: float = 10.0) -> PhotometryResult:
    """Build a light curve with one measurement at each given minute.

    Returns
    -------
    PhotometryResult
        A light curve of constant `flux`.
    """
    times = [_START + timedelta(minutes=minute) for minute in minutes]
    return PhotometryResult(
        timestamps=times, fluxes=[flux] * len(times), fluxes_detrended=[flux] * len(times)
    )


def test_rows_on_the_same_spot_form_a_cluster_and_neighbors_do_not():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a close pair clusters and a wide pair does not."""
    summaries = [
        _summary("HD 1", 250.0, 36.0),
        _summary("Gaia DR3 1", 250.0 + 0.5 / 3600.0, 36.0),
        _summary("Gaia DR3 2", 251.0, 36.0),
        _summary("Gaia DR3 3", 251.0 + 10.0 / 3600.0, 36.0),
        _summary("FIELD_J250.0000+36.0000", 250.0, 36.0),
    ]

    assert find_duplicate_clusters(summaries) == [["Gaia DR3 1", "HD 1"]]


def test_survivor_prefers_hd_or_bd_then_gaia():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the preferred name wins."""
    assert choose_survivor_id(["Gaia DR3 9", "HD 5", "2MASS J1"]) == "HD 5"
    assert choose_survivor_id(["Gaia DR3 9", "BD+36  2764"]) == "BD+36  2764"
    assert choose_survivor_id(["2MASS J1", "Gaia DR3 9", "TYC 1-2-3"]) == "Gaia DR3 9"
    assert choose_survivor_id(["TYC 1-2-3", "2MASS J1"]) == "2MASS J1"


def test_two_light_curves_are_joined_by_time_with_the_first_winning_ties():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the union, ordering, and tie rule."""
    merged = merge_light_curves(_curve([0, 10, 20], flux=1.0), _curve([10, 30], flux=2.0))

    assert [t.minute for t in merged.timestamps] == [0, 10, 20, 30]
    assert merged.fluxes == [1.0, 1.0, 1.0, 2.0]


def test_a_light_curve_out_of_step_with_its_times_is_not_merged():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a misaligned list makes the join refuse rather than guess."""
    broken = _curve([0, 10, 20])
    broken.fluxes = [1.0, 2.0]

    assert merge_light_curves(broken, _curve([30])) is None


def test_merge_cluster_folds_the_duplicate_into_the_survivor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify photometry is joined and targets combined, keeping the HD row."""
    survivor = StellarObject(id="HD 1", target_ids=["M 13"], photometry=_curve([0, 10]))
    duplicate = StellarObject(id="Gaia DR3 1", target_ids=["M 92"], photometry=_curve([20, 30]))

    result = merge_cluster({"HD 1": survivor, "Gaia DR3 1": duplicate}, "HD 1")

    merged, removed = result
    assert merged.id == "HD 1"
    assert removed == ["Gaia DR3 1"]
    assert len(merged.photometry.timestamps) == 4
    assert sorted(merged.target_ids) == ["M 13", "M 92"]


def test_when_two_rows_have_spectra_the_longer_one_is_kept():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the spectrum with more samples survives."""
    short = SpectroscopyResult(wavelengths_angstrom=[4000.0, 4100.0], intensities=[1.0, 1.0])
    long = SpectroscopyResult(wavelengths_angstrom=[4000.0, 4100.0, 4200.0], intensities=[1.0, 1.0, 1.0])
    survivor = StellarObject(id="HD 1", spectroscopy=short)
    duplicate = StellarObject(id="Gaia DR3 1", spectroscopy=long)

    merged, removed = merge_cluster({"HD 1": survivor, "Gaia DR3 1": duplicate}, "HD 1")

    assert merged.id == "HD 1"
    assert len(merged.spectroscopy.wavelengths_angstrom) == 3
    assert removed == ["Gaia DR3 1"]


def test_a_spectrum_held_only_by_the_duplicate_moves_to_the_survivor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the spectrum result is kept when only the duplicate has one."""
    spectrum = SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 4100.0, 4200.0],
        intensities=[1.0, 1.0, 1.0],
        self_determined_spectral_type="K2V",
    )
    survivor = StellarObject(id="HD 1")
    duplicate = StellarObject(id="Gaia DR3 1", spectroscopy=spectrum)

    merged, removed = merge_cluster({"HD 1": survivor, "Gaia DR3 1": duplicate}, "HD 1")

    assert merged.spectroscopy.wavelengths_angstrom == [4000.0, 4100.0, 4200.0]
    assert merged.spectroscopy.self_determined_spectral_type == "K2V"
    assert removed == ["Gaia DR3 1"]


def test_two_rows_from_the_same_catalog_are_different_objects():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify two Gaia ids are never merged, however close."""
    first = StellarObject(id="Gaia DR3 1")
    second = StellarObject(id="Gaia DR3 2")

    assert isinstance(merge_cluster({"Gaia DR3 1": first, "Gaia DR3 2": second}, "Gaia DR3 1"), str)
    assert catalog_family("Gaia DR3 1327617929278818176") == "Gaia DR"
    assert catalog_family("2MASS J05353652-0534193") == "2MASS J"
    assert catalog_family("HD 151086") == "HD"
    assert catalog_family("[H97b] 20710") == "[H"


def test_end_to_end_on_an_isolated_catalog(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the found cluster merges and the duplicate row disappears."""
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    catalog_access = CatalogAccess(config)
    catalog_access.merge_and_record(
        "stellar_catalog",
        [
            StellarObject(
                id="HD 1",
                right_ascension=250.0,
                declination=36.0,
                target_ids=["M 13"],
                photometry=_curve([0]),
            ),
            StellarObject(
                id="Gaia DR3 1",
                right_ascension=250.0 + 0.5 / 3600.0,
                declination=36.0,
                target_ids=["M 13"],
                photometry=_curve([10]),
            ),
        ],
        lambda _existing, updated: updated,
    )

    (ids,) = find_duplicate_clusters(catalog_access.list_star_summaries())
    stars = {star.id: star for star in catalog_access.get_by_ids("stellar_catalog", ids)}
    survivor, removed = merge_cluster(stars, choose_survivor_id(ids))
    catalog_access.merge_and_record("stellar_catalog", [survivor], lambda _existing, updated: updated)
    catalog_access.delete_by_ids("stellar_catalog", removed)

    assert catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"]) == []
    (kept,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    assert len(kept.photometry.timestamps) == 2
