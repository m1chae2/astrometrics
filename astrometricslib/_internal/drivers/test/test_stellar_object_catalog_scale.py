"""Purpose: Regression tests for stellar-object catalog performance at scale.

Description: Covers a performance issue that appears when the catalog
grows to hundreds of thousands of stellar objects: catalog summaries.
Instead of loading all the heavy data (like light curves and spectra)
for every single star just to show a simple list, the code now reads
only the specific summary columns it needs directly from the database.
This is much faster.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.foundation.config import AppConfiguration


def _make_isolated_config(tmp_path: Path) -> AppConfiguration:
    """Build an AppConfiguration pointed at a fresh, empty tmp_path library.

    Returns
    -------
    AppConfiguration
        A configuration pointed at a fresh, empty library under tmp_path.
    """
    library_path = tmp_path / "library"
    (library_path / "targets").mkdir(parents=True)
    frames_path = library_path / "frames"
    frames_path.mkdir(parents=True)

    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(library_path)}})
    return config


def _rows(catalog: Any, target_id: str | None = None) -> list[dict[str, Any]]:
    """List a catalog's stars as summary rows through the owner.

    Returns
    -------
    rows : `list` [`dict`]
        One summary row per star, in id order.
    """
    from astrometricslib.pipelines.shared.star_catalog_queries import StarFilters, star_summary_rows

    return star_summary_rows(catalog._catalog_access, StarFilters(target_id=target_id))


def test_star_summaries_reports_the_expected_fields(tmp_path: Path) -> None:
    """Verify summaries carry id/name/targetIds/hasSpectra/hasPhotometry.

    Exercises `star_summary_rows` (the short rows behind
    `StellarCatalog.query`) end-to-end through a
    real CatalogAccess -- has_spectra/has_photometry are real columns
    populated by drivers.catalog_access._stellar_extra_columns at write
    time (via StellarObject's own computed properties), not derived
    from the JSON at read time the way the code this superseded did.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"], ra=279.2347, dec=38.7837)
    vega.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0])
    betelgeuse = StellarObject(id="Betelgeuse", name="Betelgeuse", target_ids=["Orion Field"])
    betelgeuse.photometry = {"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]}
    empty_star = StellarObject(id="EmptyStar", name="EmptyStar", target_ids=[])
    catalog._catalog_access.put([vega, betelgeuse, empty_star], "stellar_catalog", {})

    summaries = {s["id"]: s for s in _rows(catalog)}

    assert summaries["Vega"]["hasSpectra"] is True
    assert summaries["Vega"]["hasPhotometry"] is False
    assert summaries["Vega"]["targetIds"] == ["Lyra Field"]
    # Consumers like the Planetarium's object picker resolve a selected
    # star's sky position from this same summary list -- omitting these
    # meant every star selected through it recentered on RA=0, Dec=0.
    assert summaries["Vega"]["ra"] == pytest.approx(279.2347)
    assert summaries["Vega"]["dec"] == pytest.approx(38.7837)

    assert summaries["Betelgeuse"]["hasSpectra"] is False
    assert summaries["Betelgeuse"]["hasPhotometry"] is True

    assert summaries["EmptyStar"]["hasSpectra"] is False
    assert summaries["EmptyStar"]["hasPhotometry"] is False


def test_star_summaries_filters_by_target_id(tmp_path: Path) -> None:
    """Verify target_id restricts to stars whose targetIds include it."""
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    in_field = StellarObject(id="InField", name="InField", target_ids=["M 13"])
    out_of_field = StellarObject(id="OutOfField", name="OutOfField", target_ids=["M 81"])
    catalog._catalog_access.put([in_field, out_of_field], "stellar_catalog", {})

    summaries = _rows(catalog, target_id="M 13")

    assert [s["id"] for s in summaries] == ["InField"]


def test_star_summaries_target_id_substring_collision_is_still_exact(tmp_path: Path) -> None:
    """A target id that is a substring of another must not false-match.

    target_id narrows the SQL query with a LIKE prefilter for
    performance, but "M 1" is a substring of "M 13" -- the LIKE
    narrowing alone would incorrectly include a star that only belongs
    to "M 13" when asked for "M 1". Correctness must come entirely from
    the exact `target_ids` membership check that runs after the SQL
    query, not from the SQL LIKE itself.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    in_m1 = StellarObject(id="InM1", name="InM1", target_ids=["M 1"])
    in_m13_only = StellarObject(id="InM13Only", name="InM13Only", target_ids=["M 13"])
    in_both = StellarObject(id="InBoth", name="InBoth", target_ids=["M 1", "M 13"])
    catalog._catalog_access.put([in_m1, in_m13_only, in_both], "stellar_catalog", {})

    summaries = _rows(catalog, target_id="M 1")

    assert {s["id"] for s in summaries} == {"InM1", "InBoth"}


def test_a_query_without_a_limit_returns_every_star_and_a_limit_pages(tmp_path: Path) -> None:
    """The library returns every match by default; adapters cap pages.

    The backend and the MCP servers pass their own page size, so a
    browsing view never sends the whole catalog.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog._catalog_access.put(
        [StellarObject(id=f"Star{i:03d}", name=f"Star{i}") for i in range(60)], "stellar_catalog", {}
    )

    assert len(catalog.query()) == 60
    answer = catalog.query(limit=50)

    assert len(answer) == 50
    assert answer.total == 60
    assert answer.next_offset == 50


def test_star_summaries_explicit_limit_overrides_the_default(tmp_path: Path) -> None:
    """A caller-supplied limit wins over the built-in default."""
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog._catalog_access.put(
        [StellarObject(id=f"Star{i}", name=f"Star{i}") for i in range(10)], "stellar_catalog", {}
    )

    assert len(catalog.query(limit=7)) == 7


def test_star_summaries_without_a_limit_return_every_row(tmp_path: Path) -> None:
    """A caller doing its own full-catalog search must see every row.

    A caller that filters by search text or category *after* reading the
    summaries would otherwise never see a real match sitting past the
    cutoff, so the search would wrongly report no match at all.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog._catalog_access.put(
        [StellarObject(id=f"Star{i:03d}", name=f"Star{i}") for i in range(60)], "stellar_catalog", {}
    )

    assert len(_rows(catalog)) == 60


def test_star_summaries_matches_the_model_computed_properties(tmp_path: Path) -> None:
    """Verify the recorded columns agree with StellarObject's own properties.

    has_spectra/has_photometry are computed once at write time (see
    drivers.catalog_access._stellar_extra_columns) by calling
    StellarObject's own computed properties directly, so there is no
    separate logic to drift out of sync with the model -- this checks
    that wiring, not a reimplementation of the model's rules.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"])
    vega.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0])
    vega.photometry = {"timestamps": ["2026-01-01T00:00:00Z"], "fluxes": [1.0]}
    catalog._catalog_access.put([vega], "stellar_catalog", {})

    (summary,) = _rows(catalog)

    assert summary["hasSpectra"] == vega.has_spectra
    assert summary["hasPhotometry"] == vega.has_photometry


def test_stars_read_without_light_curves_keep_their_lengths(tmp_path: Path) -> None:
    """The database takes the light-curve points out and counts them.

    A star read this way cannot be saved back, since that would erase the
    stored points; an unsafe record path is refused.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.foundation.errors import InvalidArgumentError
    from astrometricslib.models.stellar_source import PhotometryResult, StellarObject

    storage = CatalogAccess(_make_isolated_config(tmp_path))
    star = StellarObject(
        id="Vega", photometry=PhotometryResult(timestamps=["2026-01-01T00:00:00Z"] * 6, fluxes=[1.0] * 6)
    )
    storage.put([star], "stellar_catalog", {})

    (light,) = storage.get_stars_without_light_curves(["Vega"])

    assert light.photometry.timestamps == []
    assert light.has_photometry
    assert light.can_run_period_search
    assert not light.light_curves_included
    with pytest.raises(InvalidArgumentError, match="light curve"):
        storage.merge_and_record("stellar_catalog", [light], lambda _current, updated: updated)
    with pytest.raises(InvalidArgumentError, match="unsafe"):
        storage._generic.get_by_ids_leaving_out("stellar_catalog", ["Vega"], ("photometry'); --",))
    assert len(storage.get_by_ids("stellar_catalog", ["Vega"])[0].photometry.timestamps) == 6


def test_a_light_read_counts_only_the_lengths_it_keeps(tmp_path: Path) -> None:
    """The database counts only the lists a star remembers the length of.

    Every light-curve list is taken out, but only ``timestamps``,
    ``magnitudes`` and ``fluxes`` are counted, since a star keeps only
    those lengths. A path that is not counted is refused the same way.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.foundation.errors import InvalidArgumentError
    from astrometricslib.models.stellar_source import PhotometryResult, StellarObject

    storage = CatalogAccess(_make_isolated_config(tmp_path))
    star = StellarObject(
        id="Vega",
        photometry=PhotometryResult(
            timestamps=["2026-01-01T00:00:00Z"] * 4, magnitudes=[1.0] * 4, airmasses=[1.1] * 4
        ),
    )
    storage.put([star], "stellar_catalog", {})

    ((record, lengths),) = storage._generic.get_by_ids_leaving_out(
        "stellar_catalog",
        ["Vega"],
        ("photometry.timestamps", "photometry.airmasses"),
        ("photometry.timestamps",),
    )

    assert lengths == {"photometry.timestamps": 4}
    assert record.photometry.timestamps == []
    assert record.photometry.airmasses == []
    with pytest.raises(InvalidArgumentError, match="unsafe"):
        storage._generic.get_by_ids_leaving_out("stellar_catalog", ["Vega"], (), ("photometry'); --",))


def test_a_light_read_keeps_nan_and_infinity_in_a_star_record(tmp_path: Path) -> None:
    """A star whose record holds NaN still reads without its light curve.

    SQLite reads a stored ``NaN`` but writes it back as ``null``, which a
    float list rejects. Such a record comes back whole and Python takes
    the light-curve lists out, so the star validates, ``NaN`` and infinity
    survive, and the stored list lengths are still counted. A star
    without ``NaN`` goes the usual way beside it, through both the driver
    and `StellarCatalog.query`.
    """
    import math

    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import PhotometryResult, SpectroscopyResult, StellarObject

    config = _make_isolated_config(tmp_path)
    storage = CatalogAccess(config)
    with_nan = StellarObject(
        id="Albireo",
        photometry=PhotometryResult(
            timestamps=["2026-01-01T00:00:00Z"] * 5, fluxes=[1.0, 2.0, 3.0, 4.0, 5.0]
        ),
        spectroscopy=SpectroscopyResult(
            response_corrected_intensities=[1.0, float("nan"), float("inf"), float("-inf")],
            response_corrected_intensity_errors=[0.1, float("nan"), 0.1, 0.1],
        ),
    )
    plain = StellarObject(
        id="Vega", photometry=PhotometryResult(timestamps=["2026-01-01T00:00:00Z"] * 3, fluxes=[1.0] * 3)
    )
    storage.put([with_nan, plain], "stellar_catalog", {})

    stars = {star.id: star for star in storage.get_stars_without_light_curves(["Albireo", "Vega"])}
    records = {
        record.id: lengths
        for record, lengths in storage._generic.get_by_ids_leaving_out(
            "stellar_catalog", ["Albireo", "Vega"], ("photometry.timestamps", "photometry.fluxes")
        )
    }
    (queried,) = [star for star in StellarCatalog(config, storage).query().items if star.id == "Albireo"]

    for light in (stars["Albireo"], queried):
        intensities = light.spectroscopy.response_corrected_intensities
        assert math.isnan(intensities[1])
        assert intensities[2:] == [math.inf, -math.inf]
        assert math.isnan(light.spectroscopy.response_corrected_intensity_errors[1])
        assert light.photometry.timestamps == []
        assert light.photometry.fluxes == []
        assert light.has_photometry
        assert not light.light_curves_included
    assert records["Albireo"] == {"photometry.timestamps": 5, "photometry.fluxes": 5}
    assert records["Vega"] == {"photometry.timestamps": 3, "photometry.fluxes": 3}
    assert stars["Vega"].photometry.timestamps == []
    assert stars["Vega"].has_photometry


def test_query_lists_real_star_records_without_their_points(tmp_path: Path) -> None:
    """`StellarCatalog.query` returns star records with real nested models.

    Without ``include`` the light-curve points are left out, but each star
    is a `StellarObject` whose nested results are model objects, not dicts,
    and whose computed properties still describe the stored star. With
    ``include=["light_curves"]`` the star equals the one that was saved.
    """
    from astrometricslib._internal.drivers.catalog_access import CatalogAccess
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import (
        PhotometryResult,
        SpectroscopyResult,
        StellarObject,
        StellarSessionMatch,
    )

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    saved = StellarObject(
        id="Vega",
        name="Vega",
        target_ids=["Lyra Field"],
        photometry=PhotometryResult(timestamps=["2026-01-01T00:00:00Z"] * 3, fluxes=[1.0, 2.0, 3.0]),
        session_matches=[
            StellarSessionMatch(session_id="Lyra Field:2026-01-01", angular_separation_arcsec=0.4)
        ],
    )
    catalog._catalog_access.put([saved], "stellar_catalog", {})

    (light,) = catalog.query().items
    (full,) = catalog.query(include=["light_curves"]).items

    assert isinstance(light, StellarObject)
    assert isinstance(light.photometry, PhotometryResult)
    assert isinstance(light.spectroscopy, SpectroscopyResult)
    assert isinstance(light.session_matches[0], StellarSessionMatch)
    assert light.photometry.fluxes == []
    assert light.has_photometry
    assert not light.light_curves_included
    assert full.light_curves_included
    assert full == saved
