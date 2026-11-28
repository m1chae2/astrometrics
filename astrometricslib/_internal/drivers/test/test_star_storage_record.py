"""Tests for the short stored record of a star.

A star's row keeps its id, name, position, brightness and targets in their
own columns. The stored record (``data_json``) leaves those out, along with
every field still at its default and every worked-out value, and reading the
row puts them back. These tests check that a star reads back equal to the one
saved, in both the short form and the older full form.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from astrometricslib import StellarObject
from astrometricslib._internal.drivers import CatalogAccess
from astrometricslib._internal.drivers.catalog_access import stellar_storage_payload
from astrometricslib.foundation.storage.local_database import safe_json_dumps
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult


def _make_catalog_access_in(tmp_path: Path) -> CatalogAccess:
    """Build a CatalogAccess that saves into an empty temporary library.

    Returns
    -------
    catalog_access : `CatalogAccess`
        Reads and writes a library inside `tmp_path`, never the real one.
    """
    from astrometricslib.foundation.config import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return CatalogAccess(config=config)


def _unmeasured_star() -> StellarObject:
    """Build a catalog star with a detection record and no measurements.

    Returns
    -------
    star : `StellarObject`
        A Gaia star found in one image.
    """
    return StellarObject(
        id="Gaia DR3 3216114774451963520",
        name="Gaia DR3 3216114774451963520",
        ra=84.490787914617,
        dec=-2.598288396022309,
        magnitude=15.8,
        flux=0.0346,
        targetIds=["NGC 2024", "M 42"],
        starData={"id": 431, "x_centroid": 4102.8, "y_centroid": 160.6},
        isCatalogIdentified=True,
        gaiaDr3SourceId=3216114774451963520,
    )


def _measured_star() -> StellarObject:
    """Build a star with a light curve and a spectrum.

    Returns
    -------
    star : `StellarObject`
        A named star with both kinds of measurement.
    """
    star = StellarObject(id="Gaia DR3 1", name="HD 1", ra=10.0, dec=20.0, targetIds=["M 13"])
    star.photometry = PhotometryResult(
        timestamps=["2026-01-01T00:00:00", "2026-01-01T00:01:00"],
        fluxes=[1.0, 1.1],
        fluxesNormalized=[0.95, 1.05],
    )
    star.spectroscopy = SpectroscopyResult(wavelengthsAngstrom=[4000.0, 4010.0], intensities=[1.0, 2.0])
    return star


def _stored_record(catalog_access: CatalogAccess, star_id: str) -> dict:
    """Read a star's raw stored record from the database.

    Returns
    -------
    record : `dict`
        The parsed ``data_json`` of the row.
    """
    database_path = Path(catalog_access.config.get_library_path()) / "astrometrics.db"
    with sqlite3.connect(database_path) as connection:
        (data_json,) = connection.execute(
            "SELECT data_json FROM stellar_objects WHERE id = ?", (star_id,)
        ).fetchone()
    return json.loads(data_json)


def test_a_star_without_measurements_keeps_only_what_reading_back_needs(tmp_path: Path) -> None:
    """Check an unmeasured star's record leaves out columns and defaults."""
    catalog_access = _make_catalog_access_in(tmp_path)
    star = _unmeasured_star()
    catalog_access.put([star], "stellar_catalog", {})

    record = _stored_record(catalog_access, star.id)
    for key in ("id", "name", "ra", "dec", "magnitude", "targetIds", "photometry", "spectroscopy"):
        assert key not in record
    for key in ("hasSpectra", "hasPhotometry", "plotData", "variabilityScore"):
        assert key not in record
    assert record["starData"] == star.star_data

    (read_back,) = catalog_access.get_by_ids("stellar_catalog", [star.id])
    assert read_back == star


def test_a_measured_star_reads_back_equal_without_its_worked_out_values(tmp_path: Path) -> None:
    """Check a star with a light curve and spectrum reads back equal."""
    catalog_access = _make_catalog_access_in(tmp_path)
    star = _measured_star()
    catalog_access.put([star], "stellar_catalog", {})

    record = _stored_record(catalog_access, star.id)
    assert "plotData" not in record
    assert "hasSpectra" not in record
    assert record["photometry"]["fluxes"] == [1.0, 1.1]

    (read_back,) = catalog_access.get_by_ids("stellar_catalog", [star.id])
    assert read_back == star
    assert read_back.serialize() == star.serialize()
    assert catalog_access.get("stellar_catalog", {}) == [star]


def test_a_row_saved_in_the_old_full_form_still_reads_back_equal(tmp_path: Path) -> None:
    """Check a row saved in the old full form reads back the same."""
    catalog_access = _make_catalog_access_in(tmp_path)
    old_star, new_star = _unmeasured_star(), _measured_star()
    catalog_access.put([new_star], "stellar_catalog", {})
    database_path = Path(catalog_access.config.get_library_path()) / "astrometrics.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO stellar_objects (id, target_id, name, ra, dec, magnitude, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                old_star.id,
                ",".join(old_star.target_ids),
                old_star.name,
                old_star.right_ascension,
                old_star.declination,
                old_star.magnitude,
                safe_json_dumps(old_star.serialize()),
            ),
        )

    (read_back,) = catalog_access.get_by_ids("stellar_catalog", [old_star.id])
    assert read_back == old_star


def test_a_value_a_column_cannot_give_back_stays_in_the_record() -> None:
    """Check a position saved as text and a target id with a comma stay."""
    star = StellarObject(id="X 1", name="X 1", ra="12.5", dec=-3.0, targetIds=["A, B"])

    payload = stellar_storage_payload(star)

    assert payload["ra"] == "12.5"
    assert "dec" not in payload
    assert payload["targetIds"] == ["A, B"]


def test_the_star_radius_is_read_without_loading_the_records(tmp_path: Path) -> None:
    """Check `read_star_radii` gives each radius, or `None` without one."""
    catalog_access = _make_catalog_access_in(tmp_path)
    with_radius = _measured_star()
    with_radius.radius_px = 4.5
    catalog_access.put([with_radius, _unmeasured_star()], "stellar_catalog", {})

    radii = catalog_access.read_star_radii([with_radius.id, _unmeasured_star().id, "missing"])

    assert radii == {with_radius.id: 4.5, _unmeasured_star().id: None}


def test_a_star_is_found_by_an_alias_when_no_id_or_name_matches(tmp_path: Path) -> None:
    """Check a search by an older SIMBAD id finds the star saved under Gaia."""
    catalog_access = _make_catalog_access_in(tmp_path)
    star = _unmeasured_star()
    star.name = "Albireo"
    star.aliases = ["* bet01 Cyg", "HD 183912"]
    catalog_access.put([star], "stellar_catalog", {})

    assert catalog_access.find_star_ids_by_name("hd 183912") == [star.id]
    assert catalog_access.find_star_ids_by_name("Albireo") == [star.id]
    assert catalog_access.find_star_ids_by_name("HD 1") == []


def test_a_number_saved_as_blank_text_reads_back_as_not_known(tmp_path: Path) -> None:
    """Check a magnitude saved as blank text reads back as `None`.

    Older versions saved ``"magnitude": ""`` for an unknown magnitude. The
    Planetarium's sky sources need a number or `None` for the magnitude, so
    a blank stored value must not reach them as text. Both full reads and
    reads without light curves are checked.
    """
    catalog_access = _make_catalog_access_in(tmp_path)
    catalog_access.put([_measured_star()], "stellar_catalog", {})
    star_id = "FIELD_J072.0604+38.5236"
    database_path = Path(catalog_access.config.get_library_path()) / "astrometrics.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "INSERT INTO stellar_objects (id, target_id, name, ra, dec, magnitude, data_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                star_id,
                "M 1",
                star_id,
                72.0604,
                38.5236,
                None,
                json.dumps({"magnitude": "", "flux": 0.5, "bMinusV": " ", "starData": {"id": 7}}),
            ),
        )

    (read_back,) = catalog_access.get_by_ids("stellar_catalog", [star_id])
    (light_read,) = catalog_access.get_stars_without_light_curves([star_id])

    for star in (read_back, light_read):
        assert star.magnitude is None
        assert star.b_minus_v is None
        assert star.flux == pytest.approx(0.5)
        assert star.right_ascension == pytest.approx(72.0604)
        assert not star.has_catalog_magnitude
