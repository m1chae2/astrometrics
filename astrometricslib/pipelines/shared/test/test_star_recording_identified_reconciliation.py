"""Tests for saving one star under one name when two catalogs name it.

Purpose: a run that identified a star as ``HD 1`` and a later run that
identified it as ``Gaia DR3 1`` used to save two rows, splitting the
star's spectra and light curve. `record_pipeline_stars` now matches a new
star to a saved row by position first. These tests run it end to end on a
throwaway catalog (never the real one) and check the cases where matching
must NOT happen: two names from one catalog, a crowded spot, a far star.
"""

import math
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.models.stellar_source import (
    PhotometryResult,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.shared.catalog_star_identity import (
    catalog_family,
    choose_survivor_id,
    name_preference_rank,
)
from astrometricslib.pipelines.shared.star_recording import (
    _reconcile_identified_star_ids,
    merge_astrometry_stellar_object,
    record_pipeline_stars,
)
from astrometricslib.utilities.config_loader import AppConfiguration

_RIGHT_ASCENSION = 250.0
_DECLINATION = 36.0
_ONE_ARCSECOND = 1.0 / 3600.0


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


def _star(
    star_id: str,
    arcseconds_east: float = 0.0,
    target_id: str = "M 13",
    right_ascension: float = _RIGHT_ASCENSION,
    **fields: Any,
) -> StellarObject:
    """Build a named star the given number of arcseconds from the test spot.

    Returns
    -------
    star : `StellarObject`
        A catalog-identified star.
    """
    return StellarObject(
        id=star_id,
        name=star_id,
        # Right ascension lines squeeze together away from the equator, so
        # a true offset on the sky needs a larger right ascension step.
        right_ascension=right_ascension
        + arcseconds_east * _ONE_ARCSECOND / math.cos(math.radians(_DECLINATION)),
        declination=_DECLINATION,
        target_ids=[target_id],
        is_catalog_identified=True,
        **fields,
    )


def _save(catalog_access: CatalogAccess, *stars: StellarObject) -> None:
    """Write stars straight into the catalog, as an earlier run would have."""
    catalog_access.merge_and_record("stellar_catalog", list(stars), lambda _existing, updated: updated)


def _saved_ids(catalog_access: CatalogAccess) -> list[str]:
    """List every id in the catalog.

    Returns
    -------
    ids : `list` [`str`]
        The ids, sorted.
    """
    return sorted(summary.id for summary in catalog_access.list_star_summaries())


def _record(catalog_access: CatalogAccess, *stars: StellarObject) -> list[StellarObject]:
    """Save stars the way a pipeline does.

    Returns
    -------
    recorded : `list` [`StellarObject`]
        The stars as recorded.
    """
    recorded, _ = record_pipeline_stars(
        list(stars),
        catalog_access=catalog_access,
        target_id="M 13",
        merge_function=merge_astrometry_stellar_object,
        already_dropped=True,
    )
    return recorded


def test_a_gaia_star_at_a_saved_hd_position_reuses_the_hd_row(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the new star is saved into the HD row, not beside it."""
    _save(catalog_access, _star("HD 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.5, target_id="M 92"))

    assert recorded.id == "HD 1"
    assert recorded.name == "HD 1"
    assert _saved_ids(catalog_access) == ["HD 1"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    assert sorted(row.target_ids) == ["M 13", "M 92"]


def test_an_hd_star_at_a_saved_gaia_position_renames_the_row_and_keeps_its_data(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the better name survives with the saved spectrum and curve."""
    spectrum = SpectroscopyResult(
        wavelengths_angstrom=[4000.0, 4100.0, 4200.0],
        intensities=[1.0, 1.0, 1.0],
        self_determined_spectral_type="K2V",
    )
    _save(
        catalog_access,
        _star("Gaia DR3 1", spectroscopy=spectrum, photometry=PhotometryResult(mean_flux=12.0)),
    )

    (recorded,) = _record(catalog_access, _star("HD 1", arcseconds_east=0.5))

    assert recorded.id == "HD 1"
    assert _saved_ids(catalog_access) == ["HD 1"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["HD 1"])
    assert row.name == "HD 1"
    assert row.spectroscopy.wavelengths_angstrom == [4000.0, 4100.0, 4200.0]
    assert row.spectroscopy.self_determined_spectral_type == "K2V"
    assert row.photometry.mean_flux == pytest.approx(12.0)


def test_two_ids_from_one_catalog_are_left_as_two_stars(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a close double star, with two Gaia ids, is not collapsed."""
    _save(catalog_access, _star("Gaia DR3 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 2", arcseconds_east=1.0))

    assert recorded.id == "Gaia DR3 2"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1", "Gaia DR3 2"]


def test_a_star_farther_than_the_tolerance_is_a_different_star(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a neighbor 5 arcseconds away keeps its own name."""
    _save(catalog_access, _star("HD 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 9", arcseconds_east=5.0))

    assert recorded.id == "Gaia DR3 9"
    assert _saved_ids(catalog_access) == ["Gaia DR3 9", "HD 1"]


def test_a_spot_with_two_saved_rows_is_left_for_the_cleanup_script(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an ambiguous match changes nothing rather than guessing."""
    _save(catalog_access, _star("HD 1"), _star("2MASS J1", arcseconds_east=0.4))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.2))

    assert recorded.id == "Gaia DR3 1"
    assert _saved_ids(catalog_access) == ["2MASS J1", "Gaia DR3 1", "HD 1"]


def test_a_star_that_is_already_saved_under_its_own_name_is_not_moved(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify saving the same name again updates the one row."""
    _save(catalog_access, _star("Gaia DR3 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.3))

    assert recorded.id == "Gaia DR3 1"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]


def test_two_new_stars_never_collapse_onto_one_saved_row(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify only the first of two new stars takes the saved row."""
    _save(catalog_access, _star("HD 1"))

    recorded = _record(
        catalog_access, _star("Gaia DR3 1", arcseconds_east=0.2), _star("2MASS J1", arcseconds_east=0.4)
    )

    assert [star.id for star in recorded] == ["HD 1", "2MASS J1"]


def test_between_two_unpreferred_catalogs_the_saved_name_is_kept(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a 2MASS name does not replace a saved Tycho name."""
    _save(catalog_access, _star("TYC 1-2-1"))

    (recorded,) = _record(catalog_access, _star("2MASS J1", arcseconds_east=0.5))

    assert recorded.id == "TYC 1-2-1"
    assert _saved_ids(catalog_access) == ["TYC 1-2-1"]


def test_a_star_across_the_zero_right_ascension_seam_is_matched(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a pair straddling 0/360 degrees is found as one star."""
    _save(catalog_access, _star("HD 1", right_ascension=359.9999))

    (recorded,) = _record(
        catalog_access,
        StellarObject(
            id="Gaia DR3 1",
            name="Gaia DR3 1",
            right_ascension=0.0001,
            declination=_DECLINATION,
            target_ids=["M 13"],
        ),
    )

    # 0.0002 degrees of right ascension is 0.58 arcseconds at declination 36.
    assert recorded.id == "HD 1"


def test_position_only_and_unpositioned_stars_are_ignored(catalog_access):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify FIELD_J ids and stars without a position are not matched here."""
    _save(catalog_access, _star("HD 1"))
    position_only = _star("FIELD_J250.0000+36.0000")
    no_position = StellarObject(id="Gaia DR3 5", name="Gaia DR3 5", target_ids=["M 13"])

    result = _reconcile_identified_star_ids(
        [position_only, no_position], catalog_access=catalog_access, target_id="M 13"
    )

    assert [star.id for star in result] == ["FIELD_J250.0000+36.0000", "Gaia DR3 5"]


def test_a_failed_catalog_lookup_does_not_stop_the_save():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a broken lookup leaves the stars as they were."""

    class BrokenCatalogAccess:
        def list_stars_in_region(self, *args: object, **kwargs: object) -> None:
            raise RuntimeError("database is locked")

    star = _star("Gaia DR3 1")

    result = _reconcile_identified_star_ids([star], catalog_access=BrokenCatalogAccess(), target_id="M 13")

    assert result == [star]
    assert star.id == "Gaia DR3 1"


def test_the_shared_naming_rules():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the ranking the save step and the cleanup script both use."""
    assert name_preference_rank("HD 1") == name_preference_rank("BD+36 2775") == 0
    assert name_preference_rank("Gaia DR3 1") == 1
    assert name_preference_rank("2MASS J1") == 2
    assert choose_survivor_id(["2MASS J1", "Gaia DR3 1", "HD 1"]) == "HD 1"
    assert catalog_family("HD  151086") == "HD"
