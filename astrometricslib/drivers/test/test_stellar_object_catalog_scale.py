"""Purpose: Regression tests for stellar-object catalog performance at scale.

Description: Covers a performance issue that appears when the catalog
grows to hundreds of thousands of stellar objects: catalog summaries.
Instead of loading all the heavy data (like light curves and spectra)
for every single star just to show a simple list, the code now reads
only the specific summary columns it needs directly from the database.
This is much faster.
"""

from pathlib import Path

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


def test_star_summaries_reports_the_expected_fields(tmp_path: Path) -> None:
    """Verify summaries carry id/name/targetIds/hasSpectra/hasPhotometry.

    Exercises StellarCatalog.query end-to-end through a
    real CatalogAccess -- has_spectra/has_photometry are real columns
    populated by drivers.catalog_access._stellar_extra_columns at write
    time (via StellarObject's own computed properties), not derived
    from the JSON at read time the way the code this superseded did.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"], ra=279.2347, dec=38.7837)
    vega.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0])
    betelgeuse = StellarObject(id="Betelgeuse", name="Betelgeuse", target_ids=["Orion Field"])
    betelgeuse.photometry = {"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]}
    empty_star = StellarObject(id="EmptyStar", name="EmptyStar", target_ids=[])
    catalog.catalog_access.put([vega, betelgeuse, empty_star], "stellar_catalog", {})

    summaries = {s["id"]: s for s in catalog.query(limit=None).stars}

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
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    in_field = StellarObject(id="InField", name="InField", target_ids=["M 13"])
    out_of_field = StellarObject(id="OutOfField", name="OutOfField", target_ids=["M 81"])
    catalog.catalog_access.put([in_field, out_of_field], "stellar_catalog", {})

    summaries = catalog.query(target_id="M 13", limit=None).stars

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
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    in_m1 = StellarObject(id="InM1", name="InM1", target_ids=["M 1"])
    in_m13_only = StellarObject(id="InM13Only", name="InM13Only", target_ids=["M 13"])
    in_both = StellarObject(id="InBoth", name="InBoth", target_ids=["M 1", "M 13"])
    catalog.catalog_access.put([in_m1, in_m13_only, in_both], "stellar_catalog", {})

    summaries = catalog.query(target_id="M 1", limit=None).stars

    assert {s["id"] for s in summaries} == {"InM1", "InBoth"}


def test_star_summaries_are_capped_by_the_default_limit(tmp_path: Path) -> None:
    """A browse-everything request must not return the whole catalog.

    The default limit bounds what an unfiltered listing transmits and
    re-serializes -- without it, a catalog-browsing view with no target
    filter hydrates and sends every row in the catalog on every poll.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i:03d}", name=f"Star{i}") for i in range(60)], "stellar_catalog", {}
    )

    answer = catalog.query()

    assert len(answer.stars) == 50
    assert answer.total_matching == 60
    assert answer.truncated is True


def test_star_summaries_explicit_limit_overrides_the_default(tmp_path: Path) -> None:
    """A caller-supplied limit wins over the built-in default."""
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i}", name=f"Star{i}") for i in range(10)], "stellar_catalog", {}
    )

    assert len(catalog.query(limit=7).stars) == 7


def test_star_summaries_without_a_limit_return_every_row(tmp_path: Path) -> None:
    """A caller doing its own full-catalog search must see every row.

    A caller that filters by search text or category *after* reading the
    summaries would otherwise never see a real match sitting past the
    cutoff, so the search would wrongly report no match at all.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i:03d}", name=f"Star{i}") for i in range(60)], "stellar_catalog", {}
    )

    assert len(catalog.query(limit=None).stars) == 60


def test_star_summaries_matches_the_model_computed_properties(tmp_path: Path) -> None:
    """Verify the recorded columns agree with StellarObject's own properties.

    has_spectra/has_photometry are computed once at write time (see
    drivers.catalog_access._stellar_extra_columns) by calling
    StellarObject's own computed properties directly, so there is no
    separate logic to drift out of sync with the model -- this checks
    that wiring, not a reimplementation of the model's rules.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.drivers.catalog_access import CatalogAccess
    from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config, CatalogAccess(config))

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"])
    vega.spectroscopy = SpectroscopyResult(wavelengths_angstrom=[5000], intensities=[1.0])
    vega.photometry = {"timestamps": ["2026-01-01T00:00:00Z"], "fluxes": [1.0]}
    catalog.catalog_access.put([vega], "stellar_catalog", {})

    (summary,) = catalog.query(limit=None).stars

    assert summary["hasSpectra"] == vega.has_spectra
    assert summary["hasPhotometry"] == vega.has_photometry
