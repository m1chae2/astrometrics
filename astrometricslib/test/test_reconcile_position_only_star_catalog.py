"""Purpose: Tests for the one-time star id cleanup script.

Description: Builds a throwaway catalog database and a throwaway Gaia cache
(never the real ones) and runs `reconcile_position_only_star_catalog` on
them. Checks that stars move to their Gaia DR3 number with every reference
updated, that position-only rows fold into the star they duplicate with
their measurements kept, that genuine non-Gaia sources stay, that a second
run changes nothing, and that a dry run writes nothing. Also keeps the
tests of the shared merge rules the script relies on.
"""

import math
import sqlite3
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib._internal.drivers.catalog_store import GAIA_TILE_LEVEL, record_tile_fetch
from astrometricslib._internal.drivers.sky_tiles import healpix_pixels_of_points, pixel_source_id_range
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.storage.local_database import safe_json_dumps
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject
from astrometricslib.pipelines.shared.catalog_star_identity import (
    is_empty_value,
    merge_duplicate_into_survivor,
)
from astrometricslib.scripts.reconcile_position_only_star_catalog import run_cleanup

_DECLINATION = 34.7
_ARCSEC = 1.0 / 3600.0


def _make_isolated_config(tmp_path: Path) -> AppConfiguration:
    """Build an AppConfiguration pointed at a fresh, empty tmp_path library.

    Returns
    -------
    AppConfiguration
        A configuration pointed at a fresh, empty library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return config


def _ra(base: float, arcsec_east: float) -> float:
    """Move a right ascension by a true sky distance at the test declination.

    Returns
    -------
    right_ascension : `float`
        The shifted right ascension, in degrees.
    """
    return base + arcsec_east * _ARCSEC / math.cos(math.radians(_DECLINATION))


def _gaia_source_id(right_ascension: float, declination: float, serial: int) -> int:
    """Make a Gaia-like source number that lies in the tile of a position.

    Returns
    -------
    source_id : `int`
        A number whose tile, read from its bits, holds the position.
    """
    level = int(resolve(GAIA_TILE_LEVEL).value)
    tile = int(healpix_pixels_of_points(level, right_ascension, declination))
    low, _ = pixel_source_id_range(level, tile)
    return low + serial


def _curve(times: list[str], flux: float) -> PhotometryResult:
    """Build a light curve with one flux per time.

    Returns
    -------
    curve : `PhotometryResult`
        The light curve.
    """
    return PhotometryResult(timestamps=times, fluxes=[flux] * len(times), mean_flux=flux)


def _days(curve: PhotometryResult) -> list[str]:
    """List the days of a light curve's measurements.

    Returns
    -------
    days : `list` [`str`]
        Each measurement time as ``YYYY-MM-DD``.
    """
    return [str(timestamp)[:10] for timestamp in curve.timestamps]


def _star(star_id: str, ra: float, **fields: object) -> StellarObject:
    """Build a saved star of NGC 7331 at the test declination.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    return StellarObject(
        id=star_id,
        name=star_id,
        ra=ra,
        dec=_DECLINATION,
        targetIds=["NGC 7331"],
        isCatalogIdentified=not star_id.startswith("FIELD_J"),
        **fields,
    )


def _field_id(ra: float) -> str:
    """Write the position-only id of a star at the test declination.

    Returns
    -------
    star_id : `str`
        The ``FIELD_J`` id.
    """
    return f"FIELD_J{ra:.4f}{_DECLINATION:+.4f}"


@pytest.fixture
def catalog(tmp_path: Path) -> tuple[AppConfiguration, Path, dict[str, int]]:
    """Build a small catalog and Gaia cache covering every cleanup case.

    Returns
    -------
    config, database_path, gaia : `tuple`
        The configuration, the catalog database file, and the Gaia numbers
        of the two cached stars (``"A"`` and ``"B"``).
    """
    config = _make_isolated_config(tmp_path)
    star_a_ra, star_b_ra = 339.0, 339.1
    gaia = {
        "A": _gaia_source_id(star_a_ra, _DECLINATION, 11),
        "B": _gaia_source_id(star_b_ra, _DECLINATION, 12),
    }
    level = int(resolve(GAIA_TILE_LEVEL).value)
    for source_id, star_ra, g_magnitude in ((gaia["A"], star_a_ra, 9.0), (gaia["B"], star_b_ra, 12.0)):
        record_tile_fetch(
            config,
            int(healpix_pixels_of_points(level, star_ra, _DECLINATION)),
            21.0,
            [(str(source_id), star_ra, _DECLINATION, g_magnitude, f"Gaia DR3 {source_id}", 0.0, 0.0)],
            "test",
        )
    spectrum = SpectroscopyResult(wavelengthsAngstrom=[4000.0, 4010.0], intensities=[1.0, 2.0])
    duplicate_ra, alone_ra, vega_ra = 339.2, 339.3, 339.4
    stars = [
        # Star A: an HD row whose SIMBAD names list Gaia A, and two
        # position-only rows of it holding light curves of two nights.
        _star("HD 1", _ra(star_a_ra, 0.3), gaiaDr3SourceId=gaia["A"], magnitude=9.2, spectroscopy=spectrum),
        _star(_field_id(_ra(star_a_ra, 2.0)), _ra(star_a_ra, 2.0), photometry=_curve(["2026-01-01"], 5.0)),
        _star(_field_id(_ra(star_a_ra, -2.5)), _ra(star_a_ra, -2.5), photometry=_curve(["2026-01-02"], 6.0)),
        # Star B: a Tycho row with no stored Gaia number, 0.5 arcsec away.
        _star("TYC 1-1-1", _ra(star_b_ra, 0.5), magnitude=12.4),
        # Two position-only rows of one source Gaia does not list.
        _star(_field_id(duplicate_ra), duplicate_ra, photometry=_curve(["2026-01-01"], 1.0)),
        _star(
            _field_id(_ra(duplicate_ra, 1.5)), _ra(duplicate_ra, 1.5), photometry=_curve(["2026-01-03"], 1.1)
        ),
        # A lone source with no Gaia star (an artifact, say).
        _star(_field_id(alone_ra), alone_ra),
        # A bright named star Gaia does not list, and a position-only row.
        _star("* alf Lyr", vega_ra, magnitude=0.03),
        _star(_field_id(_ra(vega_ra, 2.0)), _ra(vega_ra, 2.0), photometry=_curve(["2026-01-04"], 9.0)),
    ]
    CatalogAccess(config).put(stars, "stellar_catalog", {})
    database_path = Path(config.get_library_path()) / "astrometrics.db"
    # One row in the old full form, with a field today's model no longer
    # reads, to check the rewrite keeps it.
    with sqlite3.connect(database_path) as connection:
        old_star = _star("Gaia DR3 777", 10.0)
        record = old_star.serialize()
        record["spectraHistory"] = [{"timestamp": "2026-01-17"}]
        connection.execute(
            "INSERT INTO stellar_objects (id, target_id, name, ra, dec, magnitude, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (old_star.id, "NGC 7331", old_star.name, 10.0, _DECLINATION, None, safe_json_dumps(record)),
        )
    return config, database_path, gaia


def _dump(database_path: Path) -> list[tuple]:
    """Read every row of the star table, in id order.

    Returns
    -------
    rows : `list` [`tuple`]
        Every column of every row.
    """
    with sqlite3.connect(database_path) as connection:
        return connection.execute("SELECT * FROM stellar_objects ORDER BY id").fetchall()


def _run(config: AppConfiguration, database_path: Path, apply: bool):  # ruff: ignore[missing-return-type-private-function]
    """Run the cleanup on the test database.

    Returns
    -------
    report : `CleanupReport`
        What the run found or did.
    """
    connection = sqlite3.connect(database_path)
    try:
        return run_cleanup(connection, config, apply=apply)
    finally:
        connection.close()


def test_the_cleanup_moves_stars_to_gaia_ids_and_folds_position_only_rows(catalog: tuple) -> None:
    """Check every case: Gaia rename, merges, a lone source kept, data kept."""
    config, database_path, gaia = catalog

    report = _run(config, database_path, apply=True)

    access = CatalogAccess(config)
    ids = sorted(access.list_star_ids())
    assert ids == sorted([
        f"Gaia DR3 {gaia['A']}",
        f"Gaia DR3 {gaia['B']}",
        "Gaia DR3 777",
        "* alf Lyr",
        _field_id(339.2),
        _field_id(339.3),
    ])
    (star_a,) = access.get_by_ids("stellar_catalog", [f"Gaia DR3 {gaia['A']}"])
    assert star_a.name == "HD 1"
    assert star_a.gaia_dr3_source_id == gaia["A"]
    assert star_a.spectroscopy.wavelengths_angstrom == [4000.0, 4010.0]
    assert _days(star_a.photometry) == ["2026-01-01", "2026-01-02"]
    (star_b,) = access.get_by_ids("stellar_catalog", [f"Gaia DR3 {gaia['B']}"])
    assert star_b.name == "TYC 1-1-1"
    assert star_b.is_catalog_identified
    (duplicate,) = access.get_by_ids("stellar_catalog", [_field_id(339.2)])
    assert _days(duplicate.photometry) == ["2026-01-01", "2026-01-03"]
    (vega,) = access.get_by_ids("stellar_catalog", ["* alf Lyr"])
    assert vega.photometry.fluxes == [9.0]

    assert report.rows_before == 10
    assert report.rows_after == 6
    assert report.ids_rewritten == 2
    assert report.rows_merged == 4
    assert report.gaia_groups == 2
    assert report.position_only_into_named == 1
    assert report.position_only_duplicate_groups == 1


def test_every_row_ends_in_the_short_form_and_old_fields_are_kept(catalog: tuple) -> None:
    """Check no row keeps worked-out values; an old field with data stays."""
    config, database_path, _ = catalog

    _run(config, database_path, apply=True)

    with sqlite3.connect(database_path) as connection:
        records = dict(connection.execute("SELECT id, data_json FROM stellar_objects").fetchall())
    assert all('"plotData"' not in record and '"hasSpectra"' not in record for record in records.values())
    assert '"spectraHistory"' in records["Gaia DR3 777"]
    assert '"id"' not in records["Gaia DR3 777"]


def test_a_second_run_changes_nothing(catalog: tuple) -> None:
    """Check the cleanup is idempotent."""
    config, database_path, _ = catalog
    _run(config, database_path, apply=True)
    rows_after_first_run = _dump(database_path)

    report = _run(config, database_path, apply=True)

    assert _dump(database_path) == rows_after_first_run
    assert (report.ids_rewritten, report.rows_merged, report.rows_compacted) == (0, 0, 0)


def test_a_dry_run_writes_nothing(catalog: tuple) -> None:
    """Check a run without apply writes nothing but counts the plan."""
    config, database_path, _ = catalog
    rows_before = _dump(database_path)

    report = _run(config, database_path, apply=False)

    assert _dump(database_path) == rows_before
    assert report.rows_merged == 4
    assert report.rows_compacted > 0


def test_is_empty_value_covers_every_shape_of_empty() -> None:
    """Verify None, blanks, empty lists, and empty light curves are empty."""
    assert is_empty_value(None)
    assert is_empty_value("")
    assert is_empty_value("   ")
    assert is_empty_value([])
    assert is_empty_value({})
    assert is_empty_value(PhotometryResult())
    assert not is_empty_value("Vega")
    assert not is_empty_value(0.0)
    assert not is_empty_value([1])
    assert not is_empty_value(PhotometryResult(fluxes=[1.0]))


def test_merge_duplicate_into_survivor_fills_gaps_without_overwriting() -> None:
    """Verify a duplicate fills empty fields but never overwrites a set one."""
    survivor = StellarObject(id="FIELD_JA", name="FIELD_JA")
    survivor.magnitude = 12.5
    survivor.target_ids = ["M42"]

    duplicate = StellarObject(id="FIELD_JB", name="FIELD_JB")
    duplicate.magnitude = 99.9  # must NOT overwrite the survivor's own value
    duplicate.photometry = PhotometryResult(fluxes=[1.0, 2.0, 3.0])
    duplicate.spectral_type = "A0V"
    duplicate.target_ids = ["M42", "M43"]

    merge_duplicate_into_survivor(survivor, duplicate)

    assert survivor.magnitude == 12.5  # ruff: ignore[float-equality-comparison]
    assert survivor.photometry.fluxes == [1.0, 2.0, 3.0]
    assert survivor.spectral_type == "A0V"
    assert survivor.target_ids == ["M42", "M43"]


def test_merge_duplicate_into_survivor_never_touches_identity_fields() -> None:
    """Verify id/name/is_catalog_identified never copy from a duplicate."""
    survivor = StellarObject(id="FIELD_JA", name="FIELD_JA")
    duplicate = StellarObject(id="FIELD_JB", name="FIELD_JB")
    duplicate.is_catalog_identified = True

    merge_duplicate_into_survivor(survivor, duplicate)

    assert survivor.id == "FIELD_JA"
    assert survivor.name == "FIELD_JA"
    assert survivor.is_catalog_identified is False
    assert survivor.aliases == []


def test_a_gaia_row_keeps_its_own_number_over_a_copied_one(catalog: tuple) -> None:
    """Check a row named by a Gaia number is never moved by a copied number."""
    config, database_path, gaia = catalog
    access = CatalogAccess(config)
    star = _star("Gaia DR3 555", 20.0)
    star.gaia_dr3_source_id = gaia["B"]
    access.merge_and_record("stellar_catalog", [star], lambda _existing, updated: updated)

    report = _run(config, database_path, apply=True)

    (kept,) = access.get_by_ids("stellar_catalog", ["Gaia DR3 555"])
    assert kept.gaia_dr3_source_id == 555
    assert report.gaia_numbers_corrected == 2  # this row, and the old-form row that had none
    assert access.get_by_ids("stellar_catalog", [f"Gaia DR3 {gaia['B']}"])[0].name == "TYC 1-1-1"


def test_a_chain_of_position_only_rows_joins_the_gaia_star_in_one_run(catalog: tuple) -> None:
    """Check a row chained to a Gaia star's row joins it in the first run."""
    config, database_path, gaia = catalog
    chained = _star(_field_id(_ra(339.0, 4.2)), _ra(339.0, 4.2), photometry=_curve(["2026-01-05"], 7.0))
    CatalogAccess(config).merge_and_record("stellar_catalog", [chained], lambda _existing, updated: updated)

    _run(config, database_path, apply=True)
    rows_after_first_run = _dump(database_path)
    second = _run(config, database_path, apply=True)

    (star_a,) = CatalogAccess(config).get_by_ids("stellar_catalog", [f"Gaia DR3 {gaia['A']}"])
    assert _days(star_a.photometry) == ["2026-01-01", "2026-01-02", "2026-01-05"]
    assert _dump(database_path) == rows_after_first_run
    assert second.rows_merged == 0


def test_a_neighbor_measured_in_the_same_frames_is_not_merged(catalog: tuple) -> None:
    """Check a nearby row 5 times fainter in the same frames stays apart."""
    config, database_path, gaia = catalog
    neighbor = _star(_field_id(_ra(339.0, 2.8)), _ra(339.0, 2.8), photometry=_curve(["2026-01-01"], 1.0))
    CatalogAccess(config).merge_and_record("stellar_catalog", [neighbor], lambda _existing, updated: updated)

    report = _run(config, database_path, apply=True)

    assert report.rows_kept_as_neighbors == 1
    assert CatalogAccess(config).get_by_ids("stellar_catalog", [neighbor.id])
    (star_a,) = CatalogAccess(config).get_by_ids("stellar_catalog", [f"Gaia DR3 {gaia['A']}"])
    assert star_a.photometry.fluxes == [5.0, 6.0]
