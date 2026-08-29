"""Purpose: Regression tests for stellar-object catalog performance at scale.

Description: Covers performance issues that appear when the catalog grows
to hundreds of thousands of stellar objects:

1. Database upgrades: Instead of rewriting every star in the database
   every time the program starts (which takes a long time), the code now
   checks the database version first and skips the rewrite if it's already
   up to date.

2. Catalog summaries: Instead of loading all the heavy data (like light
   curves and spectra) for every single star just to show a simple list,
   the code now reads only the specific summary columns it needs directly
   from the database. This is much faster.
"""

import json
import sqlite3

import pytest

from astrometricslib.drivers import disk_interface
from astrometricslib.utilities.config_loader import AppConfiguration


def _make_isolated_config(tmp_path) -> AppConfiguration:  # ruff: ignore[missing-type-function-argument]
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
    config.update_config({"Image Library": {"path": str(library_path), "frames_path": str(frames_path)}})
    return config


def _insert_raw_stellar_object(db_path, obj_id: str, data: dict) -> None:  # ruff: ignore[missing-type-function-argument]
    """Insert one stellar_objects row directly, bypassing the ORM layer."""
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS stellar_objects (
            id TEXT PRIMARY KEY, target_id TEXT, name TEXT,
            ra REAL, dec REAL, magnitude REAL, data_json TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO stellar_objects (id, data_json) VALUES (?, ?)",
        (obj_id, json.dumps(data)),
    )
    conn.commit()
    conn.close()


def test_verify_and_upgrade_skips_the_full_pass_once_already_current(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a second call does not re-touch rows already at current version.

    Simulates the cross-process case (a fresh backend restart) by
    resetting the in-process _database_verified guard, which only ever
    protected against repeat calls within one process and did nothing
    for the actual reported problem: every fresh process paying the
    full O(row count) cost.
    """
    config = _make_isolated_config(tmp_path)
    db_path = str(tmp_path / "library" / "astrometrics.db")
    _insert_raw_stellar_object(db_path, "Polaris", {"id": "Polaris", "ra": 37.95, "dec": 89.26})

    # _database_verified is a module-level, per-process flag: an earlier
    # test in the same run can leave it True, which would make this
    # test's own first call a silent no-op against a completely
    # different config/db_path and never set user_version at all.
    monkeypatch.setattr(disk_interface, "_database_verified", False)
    disk_interface.verify_and_upgrade_database(config)

    conn = sqlite3.connect(db_path)
    version_after_first_call = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version_after_first_call == disk_interface.STELLAR_OBJECT_DATA_VERSION

    # Simulate a fresh process: the in-process guard resets, but the
    # on-disk version marker persists.
    monkeypatch.setattr(disk_interface, "_database_verified", False)

    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE stellar_objects SET data_json = 'not valid json' WHERE id = 'Polaris'")
    conn.commit()
    conn.close()

    # If the pass ran again, it would choke on the deliberately-broken
    # JSON above (or at least rewrite it); since the row is already at
    # the current version, it must be left untouched.
    disk_interface.verify_and_upgrade_database(config)

    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT data_json FROM stellar_objects WHERE id = 'Polaris'").fetchone()
    conn.close()
    assert row[0] == "not valid json"


def test_list_object_summaries_reports_the_expected_fields(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify summaries carry id/name/targetIds/hasSpectra/hasPhotometry.

    Exercises StellarCatalog.list_object_summaries end-to-end through a
    real CatalogAccess -- has_spectra/has_photometry are real columns
    populated by data_access.catalog_access._stellar_extra_columns at write
    time (via StellarObject's own computed properties), not derived
    from the JSON at read time the way the code this superseded did.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"], ra=279.2347, dec=38.7837)
    vega.spectrum_data_processed = {"wavelengths_angstrom": [5000], "intensities": [1.0]}
    betelgeuse = StellarObject(id="Betelgeuse", name="Betelgeuse", target_ids=["Orion Field"])
    betelgeuse.light_curve = {"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]}
    empty_star = StellarObject(id="EmptyStar", name="EmptyStar", target_ids=[])
    catalog.catalog_access.put([vega, betelgeuse, empty_star], "stellar_catalog", {})

    summaries = {s["id"]: s for s in catalog.list_object_summaries()}

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


def test_list_object_summaries_filters_by_target_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify target_id restricts to stars whose targetIds include it."""
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)

    in_field = StellarObject(id="InField", name="InField", target_ids=["M 13"])
    out_of_field = StellarObject(id="OutOfField", name="OutOfField", target_ids=["M 81"])
    catalog.catalog_access.put([in_field, out_of_field], "stellar_catalog", {})

    summaries = catalog.list_object_summaries(target_id="M 13")

    assert [s["id"] for s in summaries] == ["InField"]


def test_list_object_summaries_target_id_substring_collision_is_still_exact(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A target id that is a substring of another must not false-match.

    target_id is passed to CatalogAccess.list_projected as a `like` prefilter
    for performance, but "M 1" is a substring of "M 13" -- the LIKE
    narrowing alone would incorrectly include a star that only belongs
    to "M 13" when asked for "M 1". Correctness must come entirely from
    the exact `target_ids` membership check that runs after the SQL
    query, not from the SQL LIKE itself.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)

    in_m1 = StellarObject(id="InM1", name="InM1", target_ids=["M 1"])
    in_m13_only = StellarObject(id="InM13Only", name="InM13Only", target_ids=["M 13"])
    in_both = StellarObject(id="InBoth", name="InBoth", target_ids=["M 1", "M 13"])
    catalog.catalog_access.put([in_m1, in_m13_only, in_both], "stellar_catalog", {})

    summaries = catalog.list_object_summaries(target_id="M 1")

    assert {s["id"] for s in summaries} == {"InM1", "InBoth"}


def test_list_object_summaries_caps_the_unfiltered_case_by_default(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A browse-everything request must not return the whole catalog.

    DEFAULT_UNFILTERED_SUMMARY_LIMIT bounds what an unfiltered listing
    transmits and re-serializes -- without it, a catalog-browsing view
    with no target filter hydrates and sends every row in the catalog
    on every poll.
    """
    import astrometricslib.api.stars as stars_module
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    monkeypatch.setattr(stars_module, "DEFAULT_UNFILTERED_SUMMARY_LIMIT", 3)
    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i}", name=f"Star{i}") for i in range(10)], "stellar_catalog", {}
    )

    summaries = catalog.list_object_summaries()

    assert len(summaries) == 3


def test_list_object_summaries_explicit_limit_overrides_the_default(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A caller-supplied limit wins over the built-in default either way."""
    import astrometricslib.api.stars as stars_module
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    monkeypatch.setattr(stars_module, "DEFAULT_UNFILTERED_SUMMARY_LIMIT", 3)
    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i}", name=f"Star{i}") for i in range(10)], "stellar_catalog", {}
    )

    summaries = catalog.list_object_summaries(limit=7)

    assert len(summaries) == 7


def test_list_object_summaries_a_target_scoped_request_is_not_capped_by_default(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A single target's own stars are not subject to the browse-all cap."""
    import astrometricslib.api.stars as stars_module
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    monkeypatch.setattr(stars_module, "DEFAULT_UNFILTERED_SUMMARY_LIMIT", 3)
    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)
    catalog.catalog_access.put(
        [StellarObject(id=f"Star{i}", name=f"Star{i}", target_ids=["M 13"]) for i in range(10)],
        "stellar_catalog",
        {},
    )

    summaries = catalog.list_object_summaries(target_id="M 13")

    assert len(summaries) == 10


def test_list_object_summaries_matches_the_model_computed_properties(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the persisted columns agree with StellarObject's own properties.

    has_spectra/has_photometry are computed once at write time (see
    data_access.catalog_access._stellar_extra_columns) by calling
    StellarObject's own computed properties directly, so there is no
    separate logic to drift out of sync with the model -- this checks
    that wiring, not a reimplementation of the model's rules.
    """
    from astrometricslib.api.stars import StellarCatalog
    from astrometricslib.models.stellar_source import StellarObject

    config = _make_isolated_config(tmp_path)
    catalog = StellarCatalog(config=config)

    vega = StellarObject(id="Vega", name="Vega", target_ids=["Lyra Field"])
    vega.spectrum_data_processed = {"wavelengths_angstrom": [5000], "intensities": [1.0]}
    vega.light_curve = {"timestamps": ["2026-01-01T00:00:00Z"], "fluxes": [1.0]}
    catalog.catalog_access.put([vega], "stellar_catalog", {})

    (summary,) = catalog.list_object_summaries()

    assert summary["hasSpectra"] == vega.has_spectra
    assert summary["hasPhotometry"] == vega.has_photometry
