"""Purpose: Tests for the '<id>::spectroscopy' stellar catalog row cleanup.

Description: Runs `merge_spectroscopy_star_rows` end to end against a
throwaway isolated catalog database -- never the real one -- and checks
that each star ends up as one row holding both its photometry and its
spectrum, with its normal-image position untouched.
"""

from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject
from astrometricslib.scripts.merge_spectroscopy_star_rows import (
    find_spectroscopy_row_ids,
    merge_spectroscopy_rows,
)
from astrometricslib.utilities.config_loader import AppConfiguration


class _FakeAstrometrics:
    """Minimal stand-in exposing only what this script's functions read."""

    def __init__(self, config: AppConfiguration):  # ruff: ignore[missing-return-type-special-method]
        self.config = config
        self.catalog_access = CatalogAccess(config)


def _make_isolated_astrometrics(tmp_path) -> _FakeAstrometrics:  # ruff: ignore[missing-type-function-argument]
    """Build catalog access pointed at a fresh, empty tmp_path library.

    Returns
    -------
    _FakeAstrometrics
        Catalog access over a fresh library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return _FakeAstrometrics(config)


def _save(astrometrics: _FakeAstrometrics, stars: list[StellarObject]) -> None:
    astrometrics.catalog_access.merge_and_record("stellar_catalog", stars, lambda _existing, updated: updated)


def _spectral_row(star_id: str) -> StellarObject:
    """Build a '<id>::spectroscopy' row as an older spectroscopy run saved it.

    Returns
    -------
    StellarObject
        A spectroscopy-only row; its `star_data` is a spectroscopy-image
        position.
    """
    return StellarObject(
        id=f"{star_id}::spectroscopy",
        name=star_id,
        right_ascension=10.0,
        declination=20.0,
        target_ids=["M 13"],
        star_data={"xcentroid": 900.0, "ycentroid": 800.0},
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[4000.0, 5000.0], intensities=[1.0, 2.0], dispersion_angle=1.5
        ),
    )


def test_merge_spectroscopy_rows_folds_spectrum_into_the_stars_own_row(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify one row remains, with photometry, spectrum and old position."""
    astrometrics = _make_isolated_astrometrics(tmp_path)
    base_row = StellarObject(
        id="Gaia DR3 1",
        name="Gaia DR3 1",
        right_ascension=10.0,
        declination=20.0,
        target_ids=["M 13"],
        star_data={"xcentroid": 100.0, "ycentroid": 200.0},
        photometry=PhotometryResult(fluxes=[1.0, 2.0, 3.0]),
    )
    _save(astrometrics, [base_row, _spectral_row("Gaia DR3 1")])

    spectroscopy_row_ids = find_spectroscopy_row_ids(astrometrics)
    assert spectroscopy_row_ids == ["Gaia DR3 1::spectroscopy"]

    merged_count, renamed_count = merge_spectroscopy_rows(astrometrics, spectroscopy_row_ids)

    assert (merged_count, renamed_count) == (1, 0)
    assert find_spectroscopy_row_ids(astrometrics) == []
    (star,) = astrometrics.catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert star.photometry.fluxes == [1.0, 2.0, 3.0]
    assert star.spectroscopy.wavelengths_angstrom == [4000.0, 5000.0]
    assert star.spectroscopy.star_position_px == [900.0, 800.0]
    assert star.star_data == {"xcentroid": 100.0, "ycentroid": 200.0}
    assert astrometrics.catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1::spectroscopy"]) == []


def test_merge_spectroscopy_rows_renames_a_row_that_has_no_base_row(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a spectroscopy-only row is renamed, keeping its spectrum."""
    astrometrics = _make_isolated_astrometrics(tmp_path)
    _save(astrometrics, [_spectral_row("HD 5")])

    merged_count, renamed_count = merge_spectroscopy_rows(astrometrics, ["HD 5::spectroscopy"])

    assert (merged_count, renamed_count) == (0, 1)
    (star,) = astrometrics.catalog_access.get_by_ids("stellar_catalog", ["HD 5"])
    assert star.spectroscopy.wavelengths_angstrom == [4000.0, 5000.0]
    assert star.spectroscopy.star_position_px == [900.0, 800.0]
    assert star.star_data == {}
    assert find_spectroscopy_row_ids(astrometrics) == []
