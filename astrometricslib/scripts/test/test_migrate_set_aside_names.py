"""Purpose: Test the one-time rename of the stored quarantine names.

Description: Each test builds a small library database and a configuration
file in a temporary folder, runs the migration and checks the stored names,
the backups and the bytes of the configuration file.
"""

import json
import sqlite3
from pathlib import Path

from astrometricslib.scripts import migrate_set_aside_names as migration

CONFIG_TEXT = (
    b'[Processing.Siril]\r\nstack_weight = "noise"\r\nquarantine_bad_frames_enabled = "true"\r\n# end\r\n'
)


def make_library(folder: Path) -> Path:
    """Build a library database that holds old names.

    Parameters
    ----------
    folder : `pathlib.Path`
        The library folder.

    Returns
    -------
    database : `pathlib.Path`
        The database file.
    """
    database = folder / "astrometrics.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE quality_measurements (row_id INTEGER PRIMARY KEY, metric TEXT)")
    connection.execute("CREATE TABLE targets (id TEXT PRIMARY KEY, data_json TEXT)")
    connection.executemany(
        "INSERT INTO quality_measurements (metric) VALUES (?)",
        [("frame.quarantine_sky_median_adu",), ("stacking.gate_frame_quarantine",), ("frame.fwhm",)],
    )
    summary = {
        "framesQuarantined": 3,
        "gates": [{"name": "frame_quarantine"}],
        "excluded": [{"reason": "set aside by an earlier quarantine"}],
    }
    connection.execute("INSERT INTO targets VALUES ('M 1', ?)", (json.dumps(summary),))
    connection.commit()
    connection.close()
    return database


def test_a_dry_run_changes_nothing(tmp_path: Path) -> None:
    """Without ``--apply`` the database and the configuration are untouched."""
    database = make_library(tmp_path)
    config = tmp_path / "astrometrics.config.toml"
    config.write_bytes(CONFIG_TEXT)
    before = database.read_bytes()
    status = migration.main(["--library-folder", str(tmp_path), "--config", str(config)])
    assert status == 0
    assert database.read_bytes() == before
    assert config.read_bytes() == CONFIG_TEXT


def test_apply_renames_every_stored_name_and_backs_up(tmp_path: Path) -> None:
    """The metrics, the summary text and the key change; the backups open."""
    database = make_library(tmp_path)
    config = tmp_path / "astrometrics.config.toml"
    config.write_bytes(CONFIG_TEXT)
    backups = tmp_path / "backup"
    status = migration.main([
        "--library-folder",
        str(tmp_path),
        "--config",
        str(config),
        "--backup-folder",
        str(backups),
        "--apply",
    ])
    assert status == 0
    connection = sqlite3.connect(database)
    metrics = sorted(row[0] for row in connection.execute("SELECT metric FROM quality_measurements"))
    summary = json.loads(connection.execute("SELECT data_json FROM targets").fetchone()[0])
    connection.close()
    assert metrics == ["frame.fwhm", "frame.set_aside_sky_median_adu", "stacking.gate_frame_set_aside"]
    assert summary["framesSetAside"] == 3
    assert summary["gates"] == [{"name": "frame_set_aside"}]
    assert summary["excluded"][0]["reason"] == "set aside by an earlier set-aside check"
    assert config.read_bytes() == CONFIG_TEXT.replace(
        b"quarantine_bad_frames_enabled", b"set_aside_bad_frames_enabled"
    )
    assert len(list(backups.glob("astrometrics_before_set_aside_rename_*.db"))) == 1
    assert len(list(backups.glob("astrometrics.config.toml.before_set_aside_rename_*"))) == 1
