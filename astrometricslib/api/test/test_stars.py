"""Purpose: Unit tests for the StellarCatalog high-level interface.

Description: Verifies `save_all` passes the catalog_access's required
`coordinate` argument (omitting it previously made every call raise
TypeError, including the backend's own save path), and that its
replace-all semantics cannot silently wipe the catalog when handed an
empty list.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject


def _make_catalog() -> StellarCatalog:
    """Build a catalog over a mock config and a mock storage.

    Returns
    -------
    catalog : `StellarCatalog`
        The catalog.
    """
    config = MagicMock()
    catalog_access = MagicMock()
    return StellarCatalog(config, catalog_access)


def _ids(catalog: StellarCatalog) -> list[str]:
    """List every star id in a catalog.

    Returns
    -------
    ids : `list` [`str`]
        The ids, in id order.
    """
    return catalog.query(detail="ids", limit=None).ids


class TestSaveAll:
    """Unit test suite for StellarCatalog.save_all."""

    def test_passes_required_coordinate_argument_to_catalog_access(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify the catalog_access receives all three required arguments.

        `AbstractCatalogAccess.put(obj, dataset_type, coordinate)` requires
        `coordinate`; the previous two-argument call raised TypeError on
        every invocation.
        """
        catalog = _make_catalog()
        objects = [StellarObject(id="* alf Lyr", name="Vega")]

        result = catalog.save_all(objects)

        catalog.catalog_access.put.assert_called_once_with(objects, "stellar_catalog", {})
        assert result == "stellar catalog saved"

    def test_empty_list_is_refused_by_default(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """An empty list must not silently delete the whole catalog.

        `put_all` runs an unconditional DELETE when given an empty
        list, so a caller that simply had not loaded the catalog yet
        would otherwise destroy every row.
        """
        catalog = _make_catalog()

        with pytest.raises(InvalidArgumentError, match="empty list"):
            catalog.save_all([])

        catalog.catalog_access.put.assert_not_called()

    def test_empty_list_allowed_when_explicitly_requested(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Deliberately clearing the catalog remains possible."""
        catalog = _make_catalog()

        result = catalog.save_all([], allow_empty=True)

        catalog.catalog_access.put.assert_called_once_with([], "stellar_catalog", {})
        assert result == "stellar catalog saved"


class TestAnalyzePeriodicity:
    """Unit test suite for StellarCatalog.analyze_periodicity."""

    def test_saves_a_periodogram_when_there_are_enough_points(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify 20 measurements produce a periodogram that is saved."""
        catalog = _make_catalog()
        start = datetime(2026, 1, 1, tzinfo=UTC)
        star = StellarObject(
            id="Gaia DR3 1",
            photometry=PhotometryResult(
                timestamps=[start + timedelta(minutes=10 * index) for index in range(20)],
                fluxes_detrended=[1.0 + 0.1 * (index % 4) for index in range(20)],
            ),
        )
        catalog.get = MagicMock(return_value=star)
        catalog.update = MagicMock(return_value=star)

        result = catalog.analyze_periodicity("Gaia DR3 1")

        assert result.photometry.periodogram is not None
        object_id, updates = catalog.update.call_args.args
        assert object_id == "Gaia DR3 1"
        assert updates["photometry"].periodogram is not None

    def test_returns_a_star_with_too_few_points_without_saving(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a star with 3 measurements is returned unchanged."""
        catalog = _make_catalog()
        star = StellarObject(
            id="Gaia DR3 1",
            photometry=PhotometryResult(
                timestamps=[datetime(2026, 1, 1, tzinfo=UTC)] * 3, fluxes_detrended=[1.0, 1.1, 1.0]
            ),
        )
        catalog.get = MagicMock(return_value=star)
        catalog.update = MagicMock()

        assert catalog.analyze_periodicity("Gaia DR3 1") is star
        catalog.update.assert_not_called()

    def test_returns_none_for_an_unknown_star(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify an id that is not in the catalog gives None."""
        catalog = _make_catalog()
        catalog.get = MagicMock(return_value=None)

        assert catalog.analyze_periodicity("missing") is None


def _make_real_catalog(tmp_path, stars: list[StellarObject]) -> StellarCatalog:  # ruff: ignore[missing-type-function-argument]
    """Build a `StellarCatalog` over a real temporary database holding `stars`.

    Returns
    -------
    catalog : `StellarCatalog`
        A catalog whose stellar_catalog table holds exactly `stars`.
    """
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.foundation.config import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    catalog_access = CatalogAccess(config=config)
    catalog_access.put(stars, "stellar_catalog", {})
    return StellarCatalog(config, catalog_access)


def _positioned_star(star_id: str, ra: float, dec: float, **fields: Any) -> StellarObject:
    """Build a star at a known position.

    Returns
    -------
    star : `StellarObject`
        The star, named after its id, at (`ra`, `dec`) degrees.
    """
    star = StellarObject(id=star_id, name=fields.pop("name", star_id))
    star.right_ascension = ra
    star.declination = dec
    for field_name, value in fields.items():
        setattr(star, field_name, value)
    return star


class TestQueriesNeverLoadTheWholeCatalog:
    """The everyday catalog operations read only the rows they need.

    Loading every star into new objects costs about 2.8 GB and 12 seconds
    on a real library, and the memory is not handed back. That is what got
    the backend killed for running out of memory, so nothing on this list
    may read the whole catalog.
    """

    @staticmethod
    def _catalog_that_fails_on_a_full_read(tmp_path, mocker) -> StellarCatalog:  # ruff: ignore[missing-type-function-argument]
        """Build a real catalog that raises if anything reads every star.

        Returns
        -------
        catalog : `StellarCatalog`
            A three-star catalog whose whole-table read is booby-trapped.
        """
        catalog = _make_real_catalog(
            tmp_path,
            [
                _positioned_star("* alf Lyr", 279.2347, 38.7837, name="Vega", target_ids=["Vega"]),
                _positioned_star("HD 172167", 279.2350, 38.7838, target_ids=["M 13"]),
                _positioned_star("Far Away", 10.0, -20.0, target_ids=["M 1", "M 13"]),
            ],
        )
        mocker.patch.object(
            catalog.catalog_access._generic, "get_all", side_effect=AssertionError("read the whole catalog")
        )
        return catalog

    def test_find_by_position_reads_only_stars_near_the_spot(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify the nearest star inside the tolerance is found."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        found = catalog.query(
            ra_deg=279.2347, dec_deg=38.7837, tolerance_arcsec=5.0, detail="objects"
        ).objects

        assert [star.id for star in found] == ["* alf Lyr"]

    def test_find_by_position_returns_none_outside_the_tolerance(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a spot with no star within the tolerance finds nothing."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        assert catalog.query(ra_deg=200.0, dec_deg=0.0, tolerance_arcsec=5.0, detail="ids").ids == []

    def test_find_by_position_prefers_the_nearest_of_two_candidates(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify that with two stars in range, the closer one wins."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        # 0.4 arcsec from HD 172167 and about 7 arcsec from Vega.
        found = catalog.query(ra_deg=279.2350, dec_deg=38.78381, tolerance_arcsec=20.0, detail="ids").ids

        assert found == ["HD 172167"]

    def test_get_object_fuzzy_lookup_loads_only_the_matching_star(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a slightly mistyped id still finds its star."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        found = catalog.get("*ALF_lyr")

        assert found is not None
        assert found.id == "* alf Lyr"
        assert catalog.get("no such star") is None

    def test_find_by_id_or_name_matches_the_name_ignoring_case(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a star is found by its common name or by its id."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        by_name = catalog.query(name="vega", detail="ids").ids
        by_id = catalog.query(name="HD 172167", detail="ids").ids

        assert by_name == ["* alf Lyr"]
        assert by_id == ["HD 172167"]
        assert catalog.query(name="Sirius", detail="ids").ids == []

    def test_list_objects_for_target_uses_exact_target_membership(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify "M 1" does not pick up a star that only belongs to "M 13"."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        assert catalog.query(target_id="M 1", detail="ids").ids == ["Far Away"]
        assert sorted(catalog.query(target_id="M 13", detail="ids").ids) == [
            "Far Away",
            "HD 172167",
        ]

    def test_list_objects_in_region_returns_full_records_near_a_spot(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a region query returns the stars inside it and no others."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        found = catalog.query(ra_deg=279.2347, dec_deg=38.7837, radius_deg=0.05, detail="objects").objects

        assert sorted(star.id for star in found) == ["* alf Lyr", "HD 172167"]

    def test_list_objects_by_ids_returns_only_the_requested_stars(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify only the given ids are loaded, in full."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        found = catalog.query(ids=["HD 172167", "Missing"], detail="objects").objects

        assert [star.id for star in found] == ["HD 172167"]

    def test_ids_and_audit_are_answered_without_loading_stars(self, tmp_path, mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify ids, existence checks and the audit load no stars."""
        catalog = self._catalog_that_fails_on_a_full_read(tmp_path, mocker)

        assert _ids(catalog) == ["* alf Lyr", "Far Away", "HD 172167"]
        assert catalog.query(ids=["Far Away", "Missing"], detail="exists").found == ["Far Away"]
        assert catalog.query(detail="stats").stats["total_objects"] == 3
        assert catalog.query(has_spectra=True, detail="ids").ids == []


class TestFindOrCreateByPosition:
    """Unit test suite for StellarCatalog.find_or_create_by_position."""

    def test_creates_a_named_star_with_its_details(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a brand-new star is created with its details."""
        catalog = _make_real_catalog(tmp_path, [_positioned_star("Other", 10.0, 10.0)])

        created = catalog.find_or_create_by_position(
            50.0, 20.0, name="HD 1", spectral_type="A0V", magnitude=5.5, target_id="M 1"
        )

        assert created.id == "HD 1"
        assert created.spectral_type == "A0V"
        assert created.magnitude == pytest.approx(5.5)
        assert created.target_ids == ["M 1"]
        assert _ids(catalog) == ["HD 1", "Other"]

    def test_reuses_the_star_at_the_same_position_instead_of_duplicating_it(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a star 1 arcsecond away is reused and gains the target."""
        catalog = _make_real_catalog(tmp_path, [_positioned_star("HD 1", 50.0, 20.0, name="HD 1")])

        found = catalog.find_or_create_by_position(50.0, 20.0 + 1.0 / 3600.0, target_id="M 13")

        assert found.id == "HD 1"
        assert found.target_ids == ["M 13"]
        assert _ids(catalog) == ["HD 1"]

    def test_finds_a_star_by_its_exact_name_wherever_it_sits(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify an existing id is reused wherever the position is."""
        catalog = _make_real_catalog(tmp_path, [_positioned_star("HD 1", 50.0, 20.0, name="HD 1")])

        found = catalog.find_or_create_by_position(120.0, -5.0, name="HD 1", magnitude=4.0)

        assert found.id == "HD 1"
        assert found.magnitude == pytest.approx(4.0)
        assert _ids(catalog) == ["HD 1"]

    def test_a_field_detection_takes_the_catalog_name_it_is_given(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify an unnamed "Star_" detection takes a catalog name."""
        catalog = _make_real_catalog(tmp_path, [_positioned_star("Star_1", 50.0, 20.0, name="")])

        found = catalog.find_or_create_by_position(50.0, 20.0, name="* bet Lyr")

        assert found.id == "* bet Lyr"
        assert _ids(catalog) == ["* bet Lyr"]

    def test_an_unnamed_new_star_gets_a_numbered_id(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a nameless star with no neighbour gets a Star_ id."""
        catalog = _make_real_catalog(tmp_path, [_positioned_star("HD 1", 10.0, 10.0)])

        created = catalog.find_or_create_by_position(200.0, -30.0)

        assert created.id == "Star_2"
        assert created.name == "Star_2"


def test_plate_solve_returns_the_solved_mapping(monkeypatch: pytest.MonkeyPatch) -> None:
    """`plate_solve` asks the star identifier to solve and returns its WCS."""
    from astrometricslib.pipelines.astrometry.processing import star_identifier

    calls: list[tuple[str, bool]] = []
    solved = object()

    class FakeIdentifier:
        """Stands in for `StarIdentifier` and records each request."""

        def __init__(self, config: object) -> None:
            """Accept the configuration the catalog passes."""

        def process_image(self, path: str, attempt_plate_solving: bool) -> tuple[list, object]:
            """Record the call and return a made-up solution.

            Returns
            -------
            result : `tuple`
                No stars, and the made-up solution.
            """
            calls.append((path, attempt_plate_solving))
            return [], solved

    monkeypatch.setattr(star_identifier, "StarIdentifier", FakeIdentifier)

    assert _make_catalog().plate_solve("/frames/one.fits") is solved
    assert calls == [("/frames/one.fits", True)]
