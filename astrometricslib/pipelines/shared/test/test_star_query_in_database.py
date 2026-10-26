"""Tests that star listings and counts are answered inside the database.

`StellarCatalog.query` used to load every row of the star catalog, filter
and sort it in Python, and then cut one page. These tests build a small
catalog of varied stars in a temporary database and check three things:
every query shape gives the same answer through the database as through the
old Python path, a count or a page reads only what it needs, and a query the
database cannot answer falls back to the Python path.
"""

from pathlib import Path
from typing import Any

import pytest
from pytest_mock import MockerFixture

from astrometricslib.api.stars import StellarCatalog
from astrometricslib.drivers.catalog_access import CatalogAccess
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject


def _make_catalog(tmp_path: Path) -> StellarCatalog:
    """Build a catalog of a few hundred varied stars in a temporary library.

    The stars cover every case the filters and the orders look at: stars
    with and without a spectrum or light curve, magnitudes that are
    missing, zero, instrumental (below -2) or real, single-frame detection
    ids, position-only ids, names with SQL wildcard characters, mixed-case
    names, and several targets whose names start alike ("M 1", "M 13").

    Returns
    -------
    catalog : `StellarCatalog`
        A catalog over the temporary database.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    (library_path / "frames").mkdir(parents=True)
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    catalog = StellarCatalog(config, CatalogAccess(config))

    magnitudes = [None, 0.0, -5.0, -2.0, -1.0, 3.5, 3.5, 7.25, 11.0, 15.5]
    target_lists = [[], ["M 1"], ["M 13"], ["M 1", "M 13"], ["Orion Field"], ["M 13", "Orion Field"]]
    stars = []
    for number in range(240):
        id_text = f"HD {number:04d}"
        if number % 17 == 0:
            id_text = f"FIELD_J{number:04d}"
        elif number % 13 == 0:
            id_text = f"M 13:2026-01-0{number % 9 + 1}:100:10:Star_{number}"
        elif number % 29 == 0:
            id_text = f"Star_{number}"
        elif number % 31 == 0:
            id_text = f"M 1:night:100:10:Star_{number}x"
        name = f"Name {number}"
        if number % 11 == 0:
            name = f"Odd%Name_{number}"
        elif number % 7 == 0:
            name = f"ALPHA Orionis {number}"
        elif number % 5 == 0:
            name = ""
        star = StellarObject(
            id=id_text,
            name=name,
            target_ids=target_lists[number % len(target_lists)],
            ra=float(number),
            dec=float(number % 90),
        )
        star.magnitude = magnitudes[number % len(magnitudes)]
        if number % 3 == 0:
            star.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0])
        if number % 4 == 0:
            star.photometry = {"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]}
        stars.append(star)
    catalog.catalog_access.put(stars, "stellar_catalog", {})
    return catalog


@pytest.fixture
def catalog(tmp_path: Path) -> StellarCatalog:
    """Provide the varied catalog.

    Returns
    -------
    catalog : `StellarCatalog`
        The catalog over a temporary database.
    """
    return _make_catalog(tmp_path)


def _python_answer(catalog: StellarCatalog, mocker: MockerFixture, **arguments: Any) -> Any:
    """Answer a query through the old Python path.

    The storage is told it cannot page, so the whole listing is loaded,
    filtered and sorted in Python.

    Returns
    -------
    answer : `StarQueryResult`
        The answer from the Python path.
    """
    mocker.patch.object(CatalogAccess, "query_star_summary_page", return_value=None)
    try:
        return catalog.query(**arguments)
    finally:
        mocker.stopall()


QUERY_SHAPES: list[dict[str, Any]] = [
    {},
    {"include_unresolved": True},
    {"has_spectra": True},
    {"has_spectra": False, "include_unresolved": True},
    {"has_photometry": True},
    {"has_photometry": False},
    {"has_spectra": True, "has_photometry": True},
    {"has_catalog_magnitude": True},
    {"has_catalog_magnitude": False},
    {"magnitude_min": 3.0, "magnitude_max": 8.0},
    {"magnitude_min": -10.0, "magnitude_max": 0.0, "include_unresolved": True},
    {"magnitude_max": 4.0},
    {"search": "orion"},
    {"search": "  ALPHA  "},
    {"search": "hd 00"},
    {"search": "%"},
    {"search": "_"},
    {"search": "odd%name"},
    {"search": "star_"},
    {"search": "m 13:", "include_unresolved": True},
    {"search": "no such star"},
    {"target_id": "M 1"},
    {"target_id": "M 13"},
    {"target_id": "Orion Field"},
    {"target_id": "M 13", "has_spectra": True, "include_unresolved": True},
    {"target_id": "Nowhere"},
    {"target_id": "M"},
    {"target_id": "m 1"},
    {
        "has_spectra": True,
        "has_photometry": False,
        "magnitude_min": 0.0,
        "magnitude_max": 12.0,
        "search": "a",
    },
    {"spectral_class": "G"},
]


@pytest.mark.parametrize(
    "shape", QUERY_SHAPES, ids=lambda shape: ",".join(f"{k}={v}" for k, v in shape.items()) or "plain"
)
@pytest.mark.parametrize("order", [None, "id", "useful"])
def test_database_answer_equals_python_answer(
    catalog: StellarCatalog, mocker: MockerFixture, shape: dict[str, Any], order: str | None
) -> None:
    """Verify every filter and order gives the same pages and totals both ways.

    For each query shape this compares the full listing, then several pages
    (first, middle, last, past the end) as summaries and as bare ids.
    """
    for limit, offset, detail in [
        (None, 0, "summary"),
        (7, 0, "summary"),
        (7, 14, "summary"),
        (50, 30, "ids"),
        (3, 1000, "ids"),
        (1, 0, "ids"),
    ]:
        arguments = {**shape, "order": order, "limit": limit, "offset": offset, "detail": detail}
        expected = _python_answer(catalog, mocker, **arguments)
        actual = catalog.query(**arguments)
        assert actual.model_dump() == expected.model_dump(), arguments


def test_catalog_has_each_case_the_filters_look_at(catalog: StellarCatalog) -> None:
    """Verify the catalog holds the varied stars the tests rely on."""
    everything = catalog.query(include_unresolved=True, limit=None)
    visible = catalog.query(limit=None)
    assert everything.total_matching == 240
    assert 0 < visible.total_matching < everything.total_matching
    assert catalog.query(has_spectra=True, limit=None).total_matching > 0
    assert catalog.query(target_id="M 1", limit=None).total_matching > 0
    assert catalog.query(search="%", limit=None).total_matching > 0
    assert catalog.query(has_catalog_magnitude=True, limit=None).total_matching > 0
    assert catalog.query(has_catalog_magnitude=False, limit=None).total_matching > 0
    assert catalog.query(magnitude_max=0.0, limit=None).total_matching > 0
    useful = catalog.query(order="useful", limit=None, detail="ids").ids
    assert useful != sorted(useful)


def test_target_scoping_does_not_confuse_targets_that_start_alike(catalog: StellarCatalog) -> None:
    """Verify "M 1" matches only stars in "M 1", never stars only in "M 13"."""
    for star in catalog.query(target_id="M 1", limit=None, include_unresolved=True).stars:
        assert "M 1" in star["targetIds"]


def test_count_reads_no_rows(catalog: StellarCatalog, mocker: MockerFixture) -> None:
    """Verify a one-star page with a total reads one row, not the catalog.

    The old path called `list_projected` and returned every row; the
    database path must not call it, and must read at most the page size.
    """
    load_everything = mocker.spy(CatalogAccess, "list_star_summaries")
    list_projected = mocker.spy(type(catalog.catalog_access._generic), "list_projected")
    select_where = mocker.spy(type(catalog.catalog_access._generic), "select_where")
    answer = catalog.query(detail="ids", limit=1, has_spectra=True)
    assert answer.total_matching > 1
    assert len(answer.ids) == 1
    assert load_everything.call_count == 0
    assert list_projected.call_count == 0
    assert len(select_where.spy_return) == 1


def test_page_reads_only_the_page(catalog: StellarCatalog, mocker: MockerFixture) -> None:
    """Verify a page of ten stars reads ten rows from the database."""
    select_where = mocker.spy(type(catalog.catalog_access._generic), "select_where")
    answer = catalog.query(limit=10, offset=20, order="useful")
    assert len(answer.stars) == 10
    assert len(select_where.spy_return) == 10
    assert answer.truncated is True


def test_non_ascii_search_falls_back_to_python(catalog: StellarCatalog, mocker: MockerFixture) -> None:
    """Verify a non-ASCII search, which SQLite cannot case-fold, works."""
    answer = catalog.query(search="Ünknown", limit=None)
    assert answer.total_matching == 0
    assert catalog.catalog_access.query_star_summary_page(search="Ünknown") is None


def test_other_selectors_and_match_order_still_use_the_python_path(
    catalog: StellarCatalog, mocker: MockerFixture
) -> None:
    """Verify ids, region and spectral class skip the database page query."""
    spy = mocker.spy(CatalogAccess, "query_star_summary_page")
    catalog.query(ids=["HD 0001"], limit=None)
    catalog.query(ra_deg=10.0, dec_deg=10.0, radius_deg=5.0, limit=None)
    catalog.query(spectral_class="G", limit=None)
    assert spy.call_count == 0
