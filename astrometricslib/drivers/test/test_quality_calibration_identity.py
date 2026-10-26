"""Tests for the calibration identity side table of the quality store.

Covers the migration from schema version 2 to 3 (creates the table, repeats
as a no-op, leaves old rows alone), the write and read round trip, and that a
repeated run id adds nothing.
"""

import sqlite3
from pathlib import Path

from astrometricslib.drivers.quality_store import SCHEMA_VERSION, QualityStore
from astrometricslib.models.quality.calibration_identities import (
    CalibrationFileIdentity,
    CalibrationIdentities,
)
from astrometricslib.models.quality.capture_setting import CaptureSetting


def make_identities(
    run_id: str = "run-1", target_id: str = "M31", **overrides: object
) -> CalibrationIdentities:
    """Build a record with one flat, one bias and one dark master.

    Returns
    -------
    identities : `CalibrationIdentities`
        A record for the stack ``<target_id>/stack.fits``.
    """
    fields: dict = {
        "run_id": run_id,
        "subject_id": f"{target_id}/stack.fits",
        "target_id": target_id,
        "camera": "ASI533MM",
        "optic": "RedCat 51",
        "focal_length_mm": 250.0,
        "flat_masters": [CalibrationFileIdentity(path="/cal/flat.fits", size_bytes=10, modified_ns=11)],
        "bias_masters": [CalibrationFileIdentity(path="/cal/bias.fits", size_bytes=20, modified_ns=21)],
        "dark_masters": [CalibrationFileIdentity(path="/cal/dark.fits", size_bytes=None, modified_ns=None)],
        "siril_version": "1.4.0",
        "stacking_algorithm_version": 4,
    }
    fields.update(overrides)
    return CalibrationIdentities(**fields)


def make_version_2_database(path: Path) -> None:
    """Write a database at schema version 2 holding one capture setting row."""
    store = QualityStore(str(path))
    store.get_or_create_capture_setting(CaptureSetting(camera="D5300"))
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE quality_calibration_identity")
        connection.execute("UPDATE quality_schema_version SET version = 2")


def test_migration_from_version_2_adds_table_once(tmp_path: Path) -> None:
    """A version-2 file gets the table on the first write only."""
    path = tmp_path / "astrometrics.db"
    make_version_2_database(path)
    store = QualityStore(str(path))
    assert store.get_calibration_identities() == []  # a read never migrates
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM quality_schema_version").fetchall() == [(2,)]

    assert store.write_calibration_identities(make_identities()) is True
    store.write_calibration_identities(make_identities(run_id="run-2"))
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT version FROM quality_schema_version").fetchall() == [
            (SCHEMA_VERSION,)
        ]
        assert connection.execute("SELECT camera FROM quality_capture_setting").fetchall() == [("D5300",)]
        assert connection.execute("SELECT COUNT(*) FROM quality_calibration_identity").fetchone()[0] == 2


def test_write_then_read_round_trip(tmp_path: Path) -> None:
    """Every field comes back, and a repeated run id adds nothing."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    original = make_identities()
    assert store.write_calibration_identities(original) is True
    assert store.write_calibration_identities(make_identities(camera="other")) is False
    records = store.get_calibration_identities()
    assert len(records) == 1
    record = records[0]
    assert record.camera == "ASI533MM"
    assert record.flat_masters == original.flat_masters
    assert record.dark_masters[0].size_bytes is None
    assert record.stacking_algorithm_version == 4
    assert record.recorded_at is not None


def test_read_by_run_id_and_target(tmp_path: Path) -> None:
    """The reader filters by run ids and target; no file reads as empty."""
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    assert store.get_calibration_identities() == []
    assert not (tmp_path / "astrometrics.db").exists()
    store.write_calibration_identities(make_identities("run-1", "M31"))
    store.write_calibration_identities(make_identities("run-2", "M13"))
    assert [r.run_id for r in store.get_calibration_identities(["run-2"])] == ["run-2"]
    assert [r.target_id for r in store.get_calibration_identities(target_id="M31")] == ["M31"]
    assert store.get_calibration_identities([]) == []
    assert len(store.get_calibration_identities(["run-1", "run-2"], "M13")) == 1
