"""Purpose: Tests of `find_or_create_star`, the private same-star lookup.

Description: The star at a sky position is reused instead of duplicated, a
star with the same name is reused wherever it sits, a field detection takes
the catalog name it is given, and a star with no neighbor is made but not
saved (the catalog keeps only measured stars). The cases came from the old
public `StellarCatalog.find_or_create_by_position`, which is now private.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib._internal.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.shared.star_catalog_queries import find_or_create_star


def _storage(tmp_path: Path, stars: list[StellarObject]) -> CatalogAccess:
    """Build a real temporary catalog storage holding `stars`.

    Returns
    -------
    storage : `CatalogAccess`
        The storage.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    storage = CatalogAccess(config=config)
    storage.put(stars, "stellar_catalog", {})
    return storage


def _measured_star(star_id: str, ra: float, dec: float, **fields: Any) -> StellarObject:
    """Build a star with a one-point light curve at a known position.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    star = StellarObject(id=star_id, name=fields.pop("name", star_id))
    star.right_ascension = ra
    star.declination = dec
    star.photometry = PhotometryResult(timestamps=["2026-01-01T00:00:00"], fluxes=[1.0])
    for field_name, value in fields.items():
        setattr(star, field_name, value)
    return star


def test_creates_a_named_star_with_its_details_without_saving_it(tmp_path: Path) -> None:
    """A new star has its details but no measurement, so it is not saved."""
    storage = _storage(tmp_path, [_measured_star("Other", 10.0, 10.0)])

    created = find_or_create_star(
        storage, 50.0, 20.0, name="HD 1", spectral_type="A0V", magnitude=5.5, target_id="M 1"
    )

    assert created.id == "HD 1"
    assert created.spectral_type == "A0V"
    assert created.magnitude == pytest.approx(5.5)
    assert created.target_ids == ["M 1"]
    assert storage.list_star_ids() == ["Other"]


def test_reuses_the_star_at_the_same_position(tmp_path: Path) -> None:
    """A star 1 arcsecond away is reused and gains the target."""
    storage = _storage(tmp_path, [_measured_star("HD 1", 50.0, 20.0)])

    found = find_or_create_star(storage, 50.0, 20.0 + 1.0 / 3600.0, target_id="M 13")

    assert found.id == "HD 1"
    assert found.target_ids == ["M 13"]
    assert storage.list_star_ids() == ["HD 1"]


def test_the_default_distance_is_the_registered_same_star_distance(tmp_path: Path) -> None:
    """A star 4 arcseconds away is new by default, and reused at 5."""
    storage = _storage(tmp_path, [_measured_star("HD 1", 50.0, 20.0)])

    separate = find_or_create_star(storage, 50.0, 20.0 + 4.0 / 3600.0)
    reused = find_or_create_star(storage, 50.0, 20.0 + 4.0 / 3600.0, tolerance_arcsec=5.0)

    assert separate.id != "HD 1"
    assert reused.id == "HD 1"


def test_finds_a_star_by_its_exact_name_wherever_it_sits(tmp_path: Path) -> None:
    """An existing id is reused wherever it sits, and gains a magnitude."""
    storage = _storage(tmp_path, [_measured_star("HD 1", 50.0, 20.0)])

    found = find_or_create_star(storage, 120.0, -5.0, name="HD 1", magnitude=4.0)

    assert found.id == "HD 1"
    assert found.magnitude == pytest.approx(4.0)
    assert storage.list_star_ids() == ["HD 1"]


def test_a_field_detection_takes_the_catalog_name_and_keeps_its_light_curve(tmp_path: Path) -> None:
    """An unnamed "Star_" detection moves to the catalog name it is given."""
    storage = _storage(tmp_path, [_measured_star("Star_1", 50.0, 20.0, name="")])

    found = find_or_create_star(storage, 50.0, 20.0, name="* bet Lyr")

    assert found.id == "* bet Lyr"
    assert storage.list_star_ids() == ["* bet Lyr"]
    assert found.photometry.fluxes == [1.0]


def test_an_unnamed_new_star_gets_a_numbered_id(tmp_path: Path) -> None:
    """A nameless star with no neighbor gets a Star_ id."""
    storage = _storage(tmp_path, [_measured_star("HD 1", 10.0, 10.0)])

    created = find_or_create_star(storage, 200.0, -30.0)

    assert created.id == "Star_2"
    assert created.name == "Star_2"
