"""Purpose: Unit tests for the StellarCatalog high-level interface.

Description: Checks every public method of `Astrometrics.stars` against a
real temporary database: reading stars with and without their light curves,
the query filters and paging, the counts, creating, updating and deleting
stars, the period searches, and the old names that now raise "moved to".
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError, MovedError, NotFoundError
from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject
from astrometricslib.models.target import Target


def _make_real_catalog(tmp_path: Path, stars: list[StellarObject]) -> StellarCatalog:
    """Build a `StellarCatalog` over a real temporary database holding `stars`.

    Returns
    -------
    catalog : `StellarCatalog`
        A catalog whose stellar_catalog table holds exactly `stars`.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.foundation.config import AppConfiguration

    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    catalog_access = CatalogAccess(config=config)
    catalog_access.put(stars, "stellar_catalog", {})
    return StellarCatalog(config, catalog_access)


def _light_curve(points: int) -> PhotometryResult:
    """Build a light curve with some points, one every ten minutes.

    Returns
    -------
    photometry : `PhotometryResult`
        A light curve whose detrended brightness repeats every four points.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return PhotometryResult(
        timestamps=[start + timedelta(minutes=10 * index) for index in range(points)],
        fluxes=[1.0 + 0.1 * (index % 4) for index in range(points)],
        fluxes_detrended=[1.0 + 0.1 * (index % 4) for index in range(points)],
    )


def _positioned_star(star_id: str, ra: float, dec: float, **fields: Any) -> StellarObject:
    """Build a measured star at a known position.

    The catalog keeps only measured stars, so the star carries a one-point
    light curve unless ``photometry`` is given.

    Returns
    -------
    star : `StellarObject`
        The star, named after its id, at (`ra`, `dec`) degrees.
    """
    fields.setdefault("photometry", PhotometryResult(timestamps=["2026-01-01T00:00:00"], fluxes=[1.0]))
    star = StellarObject(id=star_id, name=fields.pop("name", star_id))
    star.right_ascension = ra
    star.declination = dec
    for field_name, value in fields.items():
        setattr(star, field_name, value)
    return star


def _three_star_catalog(tmp_path: Path, mocker: MockerFixture) -> StellarCatalog:
    """Build a real catalog that raises if anything reads every star.

    Loading every star costs gigabytes on a real library, so no method may
    read the whole table.

    Returns
    -------
    catalog : `StellarCatalog`
        A three-star catalog whose whole-table read is booby-trapped.
    """
    spectrum = SpectroscopyResult(wavelengths_angstrom=[4000.0, 5000.0], intensities=[1.0, 2.0])
    catalog = _make_real_catalog(
        tmp_path,
        [
            _positioned_star(
                "* alf Lyr",
                279.2347,
                38.7837,
                name="Vega",
                target_ids=["Vega"],
                spectral_type="A0V",
                magnitude=0.03,
                photometry=_light_curve(12),
                spectroscopy=spectrum,
                simbad_object_types="V*|*",
            ),
            _positioned_star("HD 172167", 279.2350, 38.7838, target_ids=["M 13"], spectral_type="G2V"),
            _positioned_star("Far Away", 10.0, -20.0, target_ids=["M 1", "M 13"], magnitude=9.0),
        ],
    )
    mocker.patch.object(
        catalog._catalog_access._generic, "get_all", side_effect=AssertionError("read the whole catalog")
    )
    return catalog


def _one_star_catalog(tmp_path: Path, points: int) -> StellarCatalog:
    """Build a real catalog holding one star with a light curve.

    Returns
    -------
    catalog : `StellarCatalog`
        A catalog holding ``Gaia DR3 1`` with ``points`` measurements.
    """
    return _make_real_catalog(
        tmp_path, [_positioned_star("Gaia DR3 1", 10.0, 10.0, photometry=_light_curve(points))]
    )


class TestGet:
    """`get` reads one star, its light curve only when asked."""

    def test_a_slightly_mistyped_id_still_finds_its_star(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Spaces, underscores and capitals may differ from the stored id."""
        catalog = _three_star_catalog(tmp_path, mocker)

        found = catalog.get("*ALF_lyr")

        assert found is not None
        assert found.id == "* alf Lyr"
        assert catalog.get("no such star") is None

    def test_the_light_curve_comes_only_with_include(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Without include the points are left out, but the flags stay."""
        catalog = _three_star_catalog(tmp_path, mocker)

        light = catalog.get("* alf Lyr")
        whole = catalog.get("* alf Lyr", include=["light_curves"])

        assert light.photometry.timestamps == []
        assert not light.light_curves_included
        assert light.has_photometry
        assert light.can_run_period_search
        assert light.has_spectra
        assert len(whole.photometry.timestamps) == 12
        assert whole.light_curves_included

    def test_required_raises_and_bad_arguments_are_refused(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """required=True raises; an unknown part or empty id is refused."""
        catalog = _three_star_catalog(tmp_path, mocker)

        with pytest.raises(NotFoundError):
            catalog.get("Sirius", required=True)
        with pytest.raises(InvalidArgumentError):
            catalog.get("* alf Lyr", include=["spectra"])
        with pytest.raises(InvalidArgumentError):
            catalog.get("  ")

    def test_get_spectrum_reads_the_star_s_spectrum(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The spectrum comes from the star; a star without one gives None."""
        catalog = _three_star_catalog(tmp_path, mocker)

        assert catalog.get_spectrum(catalog.get("* alf Lyr")).intensities == [1.0, 2.0]
        assert catalog.get_spectrum(catalog.get("Far Away")) is None


class TestQuery:
    """`query` returns a page of star records for any mix of filters."""

    def test_every_star_by_default_in_id_order(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """With no filter and no limit, every star comes back."""
        catalog = _three_star_catalog(tmp_path, mocker)

        page = catalog.query()

        assert [star.id for star in page] == ["* alf Lyr", "Far Away", "HD 172167"]
        assert page.total == 3
        assert page.next_offset is None
        assert not page[0].light_curves_included

    def test_limit_and_offset_page_the_matches(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A limit cuts the page and says where the next one starts."""
        catalog = _three_star_catalog(tmp_path, mocker)

        first = catalog.query(limit=2)
        rest = catalog.query(limit=2, offset=first.next_offset)

        assert [star.id for star in first] == ["* alf Lyr", "Far Away"]
        assert first.next_offset == 2
        assert [star.id for star in rest] == ["HD 172167"]
        with pytest.raises(InvalidArgumentError):
            catalog.query(limit=0)

    def test_target_filter_uses_exact_membership(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The target "M 1" does not pick up a star only in "M 13"."""
        catalog = _three_star_catalog(tmp_path, mocker)

        assert [star.id for star in catalog.query(target=Target(id="M 1"))] == ["Far Away"]
        assert [star.id for star in catalog.query(target=Target(id="M 13"))] == ["Far Away", "HD 172167"]
        with pytest.raises(InvalidArgumentError):
            catalog.query(target="M 13")

    def test_region_text_class_and_variable_filters(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """A circle, a text, a class and the variable flag narrow the list."""
        catalog = _three_star_catalog(tmp_path, mocker)

        in_region = catalog.query(ra_deg=279.2347, dec_deg=38.7837, radius_deg=0.05)
        assert [star.id for star in in_region] == ["* alf Lyr", "HD 172167"]
        assert [star.id for star in catalog.query(text="vega")] == ["* alf Lyr"]
        assert [star.id for star in catalog.query(spectral_class="G")] == ["HD 172167"]
        assert [star.id for star in catalog.query(variable=True)] == ["* alf Lyr"]
        assert [star.id for star in catalog.query(variable=False)] == ["Far Away", "HD 172167"]
        assert [star.id for star in catalog.query(magnitude_max=5.0)] == ["* alf Lyr"]
        assert [star.id for star in catalog.query(has_spectra=True)] == ["* alf Lyr"]
        with pytest.raises(InvalidArgumentError):
            catalog.query(ra_deg=279.2, dec_deg=38.7)
        with pytest.raises(InvalidArgumentError):
            catalog.query(spectral_class="X")

    def test_useful_order_puts_spectra_first(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The useful order puts spectra first; an unknown order is refused."""
        catalog = _three_star_catalog(tmp_path, mocker)

        assert catalog.query(order_by="useful")[0].id == "* alf Lyr"
        with pytest.raises(InvalidArgumentError):
            catalog.query(order_by="brightness")


class TestCounts:
    """The counts read only the indexed columns."""

    def test_counts_by_class_and_by_target(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """Each class and each target is counted once per star."""
        catalog = _three_star_catalog(tmp_path, mocker)

        classes = {entry.spectral_class: entry.count for entry in catalog.count_by_class()}
        m13 = {
            entry.spectral_class: entry.count for entry in catalog.count_by_class(target=Target(id="M 13"))
        }
        targets = {entry.target_id: entry.star_count for entry in catalog.count_by_target()}

        assert classes == {"A": 1, "G": 1}
        assert m13 == {"G": 1}
        assert targets == {"M 1": 1, "M 13": 2, "Vega": 1}

    def test_statistics_count_the_whole_catalog(self, tmp_path: Path, mocker: MockerFixture) -> None:
        """The statistics count every star and the share with each fact."""
        catalog = _three_star_catalog(tmp_path, mocker)

        statistics = catalog.get_statistics()

        assert statistics.total_objects == 3
        assert statistics.with_spectral_type == 2
        assert statistics.with_magnitude == 2
        assert statistics.spectral_coverage_pct == pytest.approx(66.67)


class TestEdits:
    """`create`, `update` and `delete` change the catalog."""

    def test_create_saves_a_measured_star_and_refuses_a_second_one(self, tmp_path: Path) -> None:
        """A measured star is saved; the same id again is a conflict."""
        catalog = _make_real_catalog(tmp_path, [])

        created = catalog.create("Test Star", 279.23, 38.78, photometry=_light_curve(3))

        assert (created.right_ascension, created.declination) == (279.23, 38.78)
        assert catalog.get("Test Star") is not None
        with pytest.raises(ConflictError):
            catalog.create("Test Star", 279.23, 38.78)

    def test_create_returns_an_unmeasured_star_without_saving_it(self, tmp_path: Path) -> None:
        """The catalog keeps only measured stars."""
        catalog = _make_real_catalog(tmp_path, [])

        created = catalog.create("Empty", 10.0, 10.0, spectral_type="K0")

        assert created.spectral_type == "K0"
        assert catalog.get("Empty") is None

    def test_create_refuses_text_or_out_of_range_angles_and_unknown_fields(self, tmp_path: Path) -> None:
        """Text, a bad angle or an unknown field name is refused."""
        catalog = _make_real_catalog(tmp_path, [])

        for bad in ({"ra_deg": "18:36:56"}, {"ra_deg": 400.0}, {"dec_deg": -91.0}, {"spectralType": "G2V"}):
            with pytest.raises(InvalidArgumentError):
                catalog.create("Test Star", **bad)

    def test_update_of_a_light_star_keeps_its_light_curve(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """The stored record changes, so the left-out points are not lost."""
        catalog = _three_star_catalog(tmp_path, mocker)
        light = catalog.get("* alf Lyr")

        updated = catalog.update(light, spectral_type="A1V")

        assert updated.spectral_type == "A1V"
        assert len(updated.photometry.timestamps) == 12
        assert light.spectral_type == "A0V"
        assert len(catalog.get("* alf Lyr", include=["light_curves"]).photometry.timestamps) == 12

    def test_update_refuses_unknown_fields_and_missing_stars(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A bad field, the id or a missing star raises; nothing changes."""
        catalog = _three_star_catalog(tmp_path, mocker)
        star = catalog.get("HD 172167")

        with pytest.raises(InvalidArgumentError, match="spectralType"):
            catalog.update(star, spectralType="K0")
        with pytest.raises(InvalidArgumentError):
            catalog.update(star, id="Other")
        with pytest.raises(NotFoundError):
            catalog.update(StellarObject(id="Nowhere"), spectral_type="K0")
        assert catalog.get("HD 172167").spectral_type == "G2V"

    def test_delete_removes_the_star_and_a_second_delete_raises(
        self, tmp_path: Path, mocker: MockerFixture
    ) -> None:
        """A deleted star is gone; deleting it again is a NotFoundError."""
        catalog = _three_star_catalog(tmp_path, mocker)
        star = catalog.get("Far Away")

        assert catalog.delete(star) is None
        assert catalog.get("Far Away") is None
        with pytest.raises(NotFoundError):
            catalog.delete(star)


class TestPeriodicity:
    """`measure_periodicity` saves nothing; `record_periodicity` saves."""

    def test_measure_reads_the_light_curve_and_saves_nothing(self, tmp_path: Path) -> None:
        """A light star is searched through its stored light curve."""
        catalog = _one_star_catalog(tmp_path, 20)

        search = catalog.measure_periodicity(catalog.get("Gaia DR3 1"))

        assert search.star_id == "Gaia DR3 1"
        assert search.periodogram is not None
        assert search.transit_candidate is not None
        assert catalog.get("Gaia DR3 1").photometry.periodogram is None

    def test_measure_warns_when_there_are_too_few_points(self, tmp_path: Path) -> None:
        """Three points run neither search, and the warnings say why."""
        catalog = _one_star_catalog(tmp_path, 3)

        search = catalog.measure_periodicity(catalog.get("Gaia DR3 1"))

        assert search.periodogram is None
        assert search.transit_candidate is None
        assert len(search.warnings) == 2

    def test_record_saves_the_periodogram(self, tmp_path: Path) -> None:
        """The saved star has the period result and keeps its light curve."""
        catalog = _one_star_catalog(tmp_path, 20)

        saved = catalog.record_periodicity(catalog.get("Gaia DR3 1"))

        assert saved.photometry.periodogram is not None
        stored = catalog.get("Gaia DR3 1", include=["light_curves"])
        assert stored.photometry.periodogram is not None
        assert len(stored.photometry.timestamps) == 20
        with pytest.raises(NotFoundError):
            catalog.record_periodicity(StellarObject(id="Nowhere"))


@pytest.mark.parametrize("old_name", ["dataset_version", "find_or_create_by_position", "analyze_periodicity"])
def test_removed_names_say_where_they_moved(tmp_path: Path, old_name: str) -> None:
    """Reading a removed method raises MovedError naming the new call."""
    catalog = _make_real_catalog(tmp_path, [])

    with pytest.raises(MovedError, match="moved to"):
        getattr(catalog, old_name)
