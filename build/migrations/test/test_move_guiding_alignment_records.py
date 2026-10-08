"""Purpose: Tests for the script that moves alignment and guiding records."""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from wayfindinglib import ControlRecordStore

_SPEC = importlib.util.spec_from_file_location(
    "move_guiding_alignment_records", Path(__file__).parent.parent / "move_guiding_alignment_records.py"
)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


def _make_logs_database(path: Path, with_source_column: bool = True) -> None:
    """Create a logs database with the old record tables and a job table."""
    connection = sqlite3.connect(path)
    connection.execute("CREATE TABLE processing_jobs (id TEXT PRIMARY KEY, status TEXT)")
    connection.execute("INSERT INTO processing_jobs VALUES ('job-1', 'completed')")
    connection.execute(
        "CREATE TABLE alignment_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
        "target_name TEXT, timestamp REAL NOT NULL, status TEXT NOT NULL, delta_ra_arcsec REAL, "
        "delta_dec_arcsec REAL, pointing_error_arcsec REAL, mount_ra REAL, mount_dec REAL)"
    )
    connection.execute(
        "INSERT INTO alignment_logs (session_id, target_name, timestamp, status, delta_ra_arcsec, "
        "delta_dec_arcsec, pointing_error_arcsec, mount_ra, mount_dec) "
        "VALUES ('2026-09-24', 'M 13', 1000.0, 'aligned', 3.0, 4.0, 5.0, 250.4, 36.5)"
    )
    source_column = ", source TEXT NOT NULL DEFAULT 'unverified'" if with_source_column else ""
    connection.execute(
        "CREATE TABLE guiding_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
        "target_name TEXT, timestamp REAL NOT NULL, dra REAL NOT NULL, ddec REAL NOT NULL, "
        f"pulse_ra REAL DEFAULT 0.0, pulse_dec REAL DEFAULT 0.0, snr REAL, rms_ra REAL, rms_dec REAL, star_mass REAL{source_column})"
    )
    if with_source_column:
        connection.execute(
            "INSERT INTO guiding_logs (session_id, timestamp, dra, ddec, source) "
            "VALUES ('2026-09-24', 1001.0, 0.1, 0.2, 'phd2_guide_log')"
        )
    else:
        connection.execute(
            "INSERT INTO guiding_logs (session_id, timestamp, dra, ddec) VALUES ('n', 1001.0, 0.1, 0.2)"
        )
    connection.execute(
        "CREATE TABLE polar_alignment_logs (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
        "timestamp REAL NOT NULL, status TEXT NOT NULL, total_error_arcsec REAL, alt_error_arcsec REAL, "
        "az_error_arcsec REAL, pole_ra REAL, pole_dec REAL, paa_points_json TEXT)"
    )
    connection.execute(
        "INSERT INTO polar_alignment_logs (session_id, timestamp, status, total_error_arcsec, "
        "paa_points_json) VALUES ('2026-09-24', 999.0, 'warning', 90.0, '[{\"ra\": 1.0}]')"
    )
    connection.execute("CREATE TABLE agent_interactions (id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE agent_knowledge (id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO agent_knowledge VALUES ('kept')")
    connection.commit()
    connection.close()


def _tables(path: Path) -> set[str]:
    """Return the names of the tables in a database.

    Returns
    -------
    names : `set` [`str`]
        The table names.
    """
    connection = sqlite3.connect(path)
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    connection.close()
    return names


def test_the_records_are_copied_and_the_old_tables_dropped(tmp_path: Path) -> None:
    """Every old row reaches the new store, and the old tables are gone."""
    logs_db, wayfinding_db = tmp_path / "astrometrics_log.db", tmp_path / "wayfinding.db"
    _make_logs_database(logs_db)

    copied = _MODULE.move_records(logs_db, wayfinding_db)

    assert copied == {"alignment_logs": 1, "guiding_logs": 1, "polar_alignment_logs": 1}
    store = ControlRecordStore(str(wayfinding_db))
    (attempt,) = store.get_alignment_attempts()
    assert attempt["target_name"] == "M 13"
    assert attempt["pointing_error_arcsec"] == pytest.approx(5.0)
    assert attempt["mount_ra"] == pytest.approx(250.4)
    (sample,) = store.get_guiding_samples()
    assert sample["source"] == "phd2_guide_log"
    (run,) = store.get_polar_alignments()
    assert run["paa_points"] == [{"ra": 1.0}]
    remaining = _tables(logs_db)
    assert not {"alignment_logs", "guiding_logs", "polar_alignment_logs", "agent_interactions"} & remaining
    assert {"processing_jobs", "agent_knowledge"} <= remaining
    assert logs_db.with_suffix(".db.bak").exists()


def test_samples_from_before_the_source_column_are_marked_unverified(tmp_path: Path) -> None:
    """A guiding sample that never named its source is copied as unverified."""
    logs_db, wayfinding_db = tmp_path / "astrometrics_log.db", tmp_path / "wayfinding.db"
    _make_logs_database(logs_db, with_source_column=False)

    _MODULE.move_records(logs_db, wayfinding_db)

    (sample,) = ControlRecordStore(str(wayfinding_db)).get_guiding_samples()
    assert sample["source"] == "unverified"


def test_running_again_changes_nothing(tmp_path: Path) -> None:
    """A second run finds no old tables and copies nothing twice."""
    logs_db, wayfinding_db = tmp_path / "astrometrics_log.db", tmp_path / "wayfinding.db"
    _make_logs_database(logs_db)
    _MODULE.move_records(logs_db, wayfinding_db)

    assert _MODULE.move_records(logs_db, wayfinding_db) == {}
    assert len(ControlRecordStore(str(wayfinding_db)).get_alignment_attempts()) == 1


def test_existing_new_records_are_kept(tmp_path: Path) -> None:
    """Records the new code already wrote stay next to the moved ones."""
    logs_db, wayfinding_db = tmp_path / "astrometrics_log.db", tmp_path / "wayfinding.db"
    _make_logs_database(logs_db)
    ControlRecordStore(str(wayfinding_db)).record_alignment_attempt({
        "timestamp": 2000.0,
        "status": "aligned",
        "pointing_error_arcsec": 9.0,
    })

    _MODULE.move_records(logs_db, wayfinding_db)

    timestamps = [row["timestamp"] for row in ControlRecordStore(str(wayfinding_db)).get_alignment_attempts()]
    assert timestamps == [2000.0, 1000.0]
