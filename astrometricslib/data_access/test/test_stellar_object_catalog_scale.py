"""Purpose: Regression tests for stellar-object catalog performance at scale.

Description: Covers two problems that only showed up once a real catalog
grew to 270,450 stellar objects on 2026-08-25, after an earlier fix removed
the 100-star identification cap:

1. verify_and_upgrade_database's stellar_objects pass used to fully
   hydrate, re-serialize, and rewrite every row on every single call --
   including every Astrometrics() construction, not just backend
   startup -- regardless of whether anything had changed. Measured
   against the real catalog it took ~26 seconds and rewrote 0 rows.
   It now gates the whole pass behind PRAGMA user_version so a
   steady-state call costs one PRAGMA read.

2. load_stellar_objects() fully hydrates a StellarObject (nested
   light curve, spectra history, etc.) for every row, which the
   astronomy-list view (polled on an interval, needing only five
   scalar fields per star) does not need and pays for regardless.
   load_stellar_object_summaries() reads the same rows but only
   touches the handful of top-level JSON keys those fields need.
"""

import json
import sqlite3

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


def test_load_stellar_object_summaries_reports_the_expected_fields(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify summaries carry id/name/targetIds/hasSpectra/hasPhotometry."""
    config = _make_isolated_config(tmp_path)
    db_path = str(tmp_path / "library" / "astrometrics.db")

    _insert_raw_stellar_object(
        db_path,
        "Vega",
        {
            "id": "Vega",
            "name": "Vega",
            "targetIds": ["Lyra Field"],
            "spectrumDataProcessed": {"wavelengths_angstrom": [5000], "intensities": [1.0]},
        },
    )
    _insert_raw_stellar_object(
        db_path,
        "Betelgeuse",
        {
            "id": "Betelgeuse",
            "name": "Betelgeuse",
            "targetIds": ["Orion Field"],
            "lightCurve": {"timestamps": ["2026-01-01T00:00:00Z"], "magnitudes": [0.5]},
        },
    )
    _insert_raw_stellar_object(
        db_path, "EmptyStar", {"id": "EmptyStar", "name": "EmptyStar", "targetIds": []}
    )

    summaries = {s["id"]: s for s in disk_interface.load_stellar_object_summaries(config)}

    assert summaries["Vega"]["hasSpectra"] is True
    assert summaries["Vega"]["hasPhotometry"] is False
    assert summaries["Vega"]["targetIds"] == ["Lyra Field"]

    assert summaries["Betelgeuse"]["hasSpectra"] is False
    assert summaries["Betelgeuse"]["hasPhotometry"] is True

    assert summaries["EmptyStar"]["hasSpectra"] is False
    assert summaries["EmptyStar"]["hasPhotometry"] is False


def test_load_stellar_object_summaries_filters_by_target_id(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify target_id restricts to stars whose targetIds include it."""
    config = _make_isolated_config(tmp_path)
    db_path = str(tmp_path / "library" / "astrometrics.db")

    _insert_raw_stellar_object(
        db_path, "InField", {"id": "InField", "name": "InField", "targetIds": ["M 13"]}
    )
    _insert_raw_stellar_object(
        db_path, "OutOfField", {"id": "OutOfField", "name": "OutOfField", "targetIds": ["M 81"]}
    )

    summaries = disk_interface.load_stellar_object_summaries(config, target_id="M 13")

    assert [s["id"] for s in summaries] == ["InField"]


def test_load_stellar_object_summaries_matches_the_full_hydration_path(tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify the lightweight path agrees with StellarObject's computed fields.

    The summary logic re-implements has_spectra/has_photometry rather
    than reusing StellarObject's computed properties (to avoid building
    a full model per row) -- this checks the two never drift apart.
    """
    config = _make_isolated_config(tmp_path)
    db_path = str(tmp_path / "library" / "astrometrics.db")
    data = {
        "id": "Vega",
        "name": "Vega",
        "targetIds": ["Lyra Field"],
        "spectrumDataProcessed": {"wavelengths_angstrom": [5000], "intensities": [1.0]},
        "lightCurve": {"timestamps": ["2026-01-01T00:00:00Z"], "fluxes": [1.0]},
    }
    _insert_raw_stellar_object(db_path, "Vega", data)

    from astrometricslib.models.stellar_source import StellarObject

    full = StellarObject.model_validate(data)
    (summary,) = disk_interface.load_stellar_object_summaries(config)

    assert summary["hasSpectra"] == full.has_spectra
    assert summary["hasPhotometry"] == full.has_photometry
