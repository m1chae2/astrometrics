"""Tests for saving one star under one name when two catalogs name it.

Purpose: a run that identified a star as ``HD 1`` and a later run that
identified it as ``Gaia DR3 1`` used to save two rows, splitting the
star's spectra and light curve. `record_pipeline_stars` now matches a new
star to a saved row by position first. These tests run it end to end on a
throwaway catalog (never the real one) and check the cases where matching
must NOT happen: two names from one catalog, a crowded spot, a far star.
The catalog keeps only measured stars, so each test star carries a short
light curve unless a test says otherwise.
"""

import math
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
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

_RIGHT_ASCENSION = 250.0
_DECLINATION = 36.0
_ONE_ARCSECOND = 1.0 / 3600.0
# A measured spectrum, for a saved star that must hold no light curve.
_SPECTRUM = SpectroscopyResult(wavelengths_angstrom=[4000.0, 4100.0], intensities=[1.0, 1.0])


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
        A catalog-identified star, with a one-point light curve unless
        ``photometry`` is given.
    """
    fields.setdefault("photometry", PhotometryResult(timestamps=["2026-01-01T00:00:00"], fluxes=[1.0]))
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


def test_a_gaia_star_at_a_saved_hd_position_renames_the_hd_row(catalog_access: CatalogAccess) -> None:
    """Verify the HD row moves to the Gaia id and keeps HD 1 as its name."""
    _save(catalog_access, _star("HD 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.5, target_id="M 92"))

    assert recorded.id == "Gaia DR3 1"
    assert recorded.name == "HD 1"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert sorted(row.target_ids) == ["M 13", "M 92"]
    assert row.name == "HD 1"


def test_an_hd_star_at_a_saved_gaia_position_reuses_the_row_and_keeps_its_data(
    catalog_access: CatalogAccess,
) -> None:
    """Verify the Gaia row survives with its spectrum and curve, named HD 1."""
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

    assert recorded.id == "Gaia DR3 1"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 1"])
    assert row.name == "HD 1"
    assert row.spectroscopy.wavelengths_angstrom == [4000.0, 4100.0, 4200.0]
    assert row.spectroscopy.self_determined_spectral_type == "K2V"
    assert row.photometry.mean_flux == pytest.approx(12.0)


def test_two_ids_from_one_catalog_are_left_as_two_stars(catalog_access: CatalogAccess) -> None:
    """Verify a close double star, with two Gaia ids, is not collapsed."""
    _save(catalog_access, _star("Gaia DR3 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 2", arcseconds_east=1.0))

    assert recorded.id == "Gaia DR3 2"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1", "Gaia DR3 2"]


def test_a_star_farther_than_the_tolerance_is_a_different_star(catalog_access: CatalogAccess) -> None:
    """Verify a neighbor 5 arcseconds away keeps its own name."""
    _save(catalog_access, _star("HD 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 9", arcseconds_east=5.0))

    assert recorded.id == "Gaia DR3 9"
    assert _saved_ids(catalog_access) == ["Gaia DR3 9", "HD 1"]


def test_a_spot_with_two_saved_rows_is_left_for_the_cleanup_script(catalog_access: CatalogAccess) -> None:
    """Verify an ambiguous match changes nothing rather than guessing."""
    _save(catalog_access, _star("HD 1"), _star("2MASS J1", arcseconds_east=0.4))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.2))

    assert recorded.id == "Gaia DR3 1"
    assert _saved_ids(catalog_access) == ["2MASS J1", "Gaia DR3 1", "HD 1"]


def test_a_star_that_is_already_saved_under_its_own_name_is_not_moved(catalog_access: CatalogAccess) -> None:
    """Verify saving the same name again updates the one row."""
    _save(catalog_access, _star("Gaia DR3 1"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 1", arcseconds_east=0.3))

    assert recorded.id == "Gaia DR3 1"
    assert _saved_ids(catalog_access) == ["Gaia DR3 1"]


def test_two_new_stars_never_collapse_onto_one_saved_row(catalog_access: CatalogAccess) -> None:
    """Verify only the first of two new stars takes the saved row."""
    _save(catalog_access, _star("HD 1"))

    recorded = _record(
        catalog_access, _star("Gaia DR3 1", arcseconds_east=0.2), _star("2MASS J1", arcseconds_east=0.4)
    )

    assert [star.id for star in recorded] == ["Gaia DR3 1", "2MASS J1"]


def test_between_two_unpreferred_catalogs_the_saved_name_is_kept(catalog_access: CatalogAccess) -> None:
    """Verify a 2MASS name does not replace a saved Tycho name."""
    _save(catalog_access, _star("TYC 1-2-1"))

    (recorded,) = _record(catalog_access, _star("2MASS J1", arcseconds_east=0.5))

    assert recorded.id == "TYC 1-2-1"
    assert _saved_ids(catalog_access) == ["TYC 1-2-1"]


def test_a_star_across_the_zero_right_ascension_seam_is_matched(catalog_access: CatalogAccess) -> None:
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
    assert recorded.id == "Gaia DR3 1"
    assert recorded.name == "HD 1"


def test_position_only_and_unpositioned_stars_are_ignored(catalog_access: CatalogAccess) -> None:
    """Verify FIELD_J ids and stars without a position are not matched here."""
    _save(catalog_access, _star("HD 1"))
    position_only = _star("FIELD_J250.0000+36.0000")
    no_position = StellarObject(id="Gaia DR3 5", name="Gaia DR3 5", target_ids=["M 13"])

    result = _reconcile_identified_star_ids(
        [position_only, no_position], catalog_access=catalog_access, target_id="M 13"
    )

    assert [star.id for star in result] == ["FIELD_J250.0000+36.0000", "Gaia DR3 5"]


def test_a_failed_catalog_lookup_does_not_stop_the_save() -> None:
    """Verify a broken lookup leaves the stars as they were."""

    class BrokenCatalogAccess:
        def list_stars_in_region(self, *args: object, **kwargs: object) -> None:
            raise sqlite3.OperationalError("database is locked")

    star = _star("Gaia DR3 1")

    result = _reconcile_identified_star_ids([star], catalog_access=BrokenCatalogAccess(), target_id="M 13")

    assert result == [star]
    assert star.id == "Gaia DR3 1"


def test_the_shared_naming_rules() -> None:
    """Verify the ranking the save step and the cleanup script both use."""
    assert name_preference_rank("HD 1") == name_preference_rank("BD+36 2775") == 0
    assert name_preference_rank("Gaia DR3 1") == 1
    assert name_preference_rank("2MASS J1") == 2
    assert choose_survivor_id(["2MASS J1", "Gaia DR3 1", "HD 1"]) == "Gaia DR3 1"
    assert catalog_family("HD  151086") == "HD"


def test_astrometry_merge_keeps_object_types_when_the_new_run_has_none() -> None:
    """A run that missed SIMBAD must not erase earlier object types."""
    from astrometricslib.models.stellar_source import StellarObject
    from astrometricslib.pipelines.shared.star_recording import merge_astrometry_stellar_object

    saved = StellarObject(id="Algol", simbadObjectTypes="*|EB*|V*")
    gaia_only = StellarObject(id="Algol")

    merged = merge_astrometry_stellar_object(saved, gaia_only)

    assert merged.simbad_object_types == "*|EB*|V*"


def test_astrometry_merge_takes_new_object_types() -> None:
    """A run that found types updates the saved ones."""
    from astrometricslib.models.stellar_source import StellarObject
    from astrometricslib.pipelines.shared.star_recording import merge_astrometry_stellar_object

    saved = StellarObject(id="Algol", simbadObjectTypes="*")
    found = StellarObject(id="Algol", simbadObjectTypes="*|EB*|V*")

    assert merge_astrometry_stellar_object(saved, found).simbad_object_types == "*|EB*|V*"


def _position_only_star(arcseconds_east: float, **fields: Any) -> StellarObject:
    """Build a saved position-only row the given distance from the test spot.

    Returns
    -------
    star : `StellarObject`
        A ``FIELD_J`` star of M 13, not catalog-identified.
    """
    star = _star("FIELD_J", arcseconds_east=arcseconds_east, **fields)
    star.id = star.name = f"FIELD_J{star.right_ascension:.4f}{star.declination:+.4f}"
    star.is_catalog_identified = False
    return star


def test_a_named_star_takes_over_a_position_only_row_within_three_arcsec(
    catalog_access: CatalogAccess,
) -> None:
    """Verify the FIELD_J row's light curve moves onto the new named star."""
    curve = PhotometryResult(timestamps=["2026-01-01T00:00:00"], fluxes=[5.0], mean_flux=5.0)
    field_row = _position_only_star(2.5, photometry=curve)
    _save(catalog_access, field_row)

    (recorded,) = _record(catalog_access, _star("Gaia DR3 7", photometry=None))

    assert recorded.id == "Gaia DR3 7"
    assert _saved_ids(catalog_access) == ["Gaia DR3 7"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 7"])
    assert row.photometry.fluxes == [5.0]
    assert row.is_catalog_identified


def test_a_position_only_row_merges_into_an_already_saved_named_row(catalog_access: CatalogAccess) -> None:
    """Verify the FIELD_J data fills the saved named row and the row goes."""
    curve = PhotometryResult(timestamps=["2026-01-01T00:00:00"], fluxes=[5.0], mean_flux=5.0)
    _save(
        catalog_access,
        _star("Gaia DR3 7", photometry=None, spectroscopy=_SPECTRUM),
        _position_only_star(1.0, photometry=curve),
    )

    _record(catalog_access, _star("Gaia DR3 7", arcseconds_east=0.2, photometry=None))

    assert _saved_ids(catalog_access) == ["Gaia DR3 7"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 7"])
    assert row.photometry.fluxes == [5.0]


def test_a_position_only_row_farther_than_three_arcsec_is_kept(catalog_access: CatalogAccess) -> None:
    """Verify a FIELD_J row 4 arcsec away is a different source and stays."""
    field_row = _position_only_star(4.0)
    _save(catalog_access, field_row)

    _record(catalog_access, _star("Gaia DR3 7"))

    assert _saved_ids(catalog_access) == sorted(["Gaia DR3 7", field_row.id])


def test_one_position_only_row_goes_to_its_nearest_named_star_only(catalog_access: CatalogAccess) -> None:
    """Verify two named stars never both take the same FIELD_J row."""
    field_row = _position_only_star(1.0)
    _save(catalog_access, field_row)

    _record(catalog_access, _star("Gaia DR3 7"), _star("Gaia DR3 8", arcseconds_east=2.5))

    assert _saved_ids(catalog_access) == ["Gaia DR3 7", "Gaia DR3 8"]


def test_a_name_keyed_row_without_a_gaia_number_matches_within_three_arcsec(
    catalog_access: CatalogAccess,
) -> None:
    """Verify Albireo's two rows 2.9 arcsec apart become one row."""
    _save(catalog_access, _star("* bet01 Cyg"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 2026116260337482112", arcseconds_east=2.9))

    assert recorded.id == "Gaia DR3 2026116260337482112"
    assert _saved_ids(catalog_access) == ["Gaia DR3 2026116260337482112"]


def test_a_name_keyed_row_three_and_a_half_arcsec_away_is_another_star(catalog_access: CatalogAccess) -> None:
    """Verify the wider distance stops at 3 arcsec."""
    _save(catalog_access, _star("* bet01 Cyg"))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 5", arcseconds_east=3.5))

    assert recorded.id == "Gaia DR3 5"
    assert _saved_ids(catalog_access) == ["* bet01 Cyg", "Gaia DR3 5"]


def test_two_different_gaia_numbers_are_never_one_star(catalog_access: CatalogAccess) -> None:
    """Verify a named row whose Gaia number differs keeps its own row."""
    _save(catalog_access, _star("HD 1", gaia_dr3_source_id=7))

    (recorded,) = _record(catalog_access, _star("Gaia DR3 8", arcseconds_east=0.5, gaia_dr3_source_id=8))

    assert recorded.id == "Gaia DR3 8"
    assert _saved_ids(catalog_access) == ["Gaia DR3 8", "HD 1"]


def test_a_later_run_merges_a_pair_saved_for_one_star(catalog_access: CatalogAccess) -> None:
    """Verify a named row and a Gaia row of one star merge into one."""
    curve = PhotometryResult(timestamps=["2026-01-02T00:00:00"], fluxes=[7.0])
    _save(
        catalog_access,
        _star("* bet01 Cyg", photometry=curve),
        _star("Gaia DR3 5", arcseconds_east=2.9, photometry=None, spectroscopy=_SPECTRUM),
    )

    (recorded,) = _record(catalog_access, _star("Gaia DR3 5", arcseconds_east=2.9, photometry=None))

    assert recorded.id == "Gaia DR3 5"
    assert _saved_ids(catalog_access) == ["Gaia DR3 5"]
    (row,) = catalog_access.get_by_ids("stellar_catalog", ["Gaia DR3 5"])
    assert row.photometry.fluxes == [7.0]
    assert row.has_spectra
    assert "* bet01 Cyg" in [row.name, *row.aliases]
