"""Tests for the script that audits stored target positions.

The database is a small temporary SQLite file and SIMBAD is faked. They check
that a planted offset is flagged, that a good position and an unknown name are
not, and that the script opens the database read-only.
"""

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from astropy.table import Table

from astrometricslib.scripts import audit_target_positions as audit

RING = (283.396, 33.029)


class FakeSimbad:
    """A SIMBAD stand-in that answers from a dictionary."""

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> Any:
        """Return the Ring Nebula for ``M 57`` and nothing else.

        Parameters
        ----------
        object_name : `str`
            The name asked about.
        votable_fields : `tuple` [`str`], optional
            Ignored.

        Returns
        -------
        table : `astropy.table.Table` or `None`
            The answer.
        """
        if object_name == "M 31":
            return Table({
                "ra": np.array([10.685]),
                "dec": np.array([41.269]),
                "galdim_majaxis": np.array([190.0]),
            })
        if object_name != "M 57":
            return None
        return Table({"ra": np.array([RING[0]]), "dec": np.array([RING[1]])})


def make_database(path: Path, rows: list[tuple[str, str, str, dict]]) -> None:
    """Write a minimal ``targets`` table of (id, ra, dec, extra JSON) rows."""
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE targets (id TEXT PRIMARY KEY, name TEXT, ra TEXT, dec TEXT, data_json TEXT)"
    )
    for target_id, ra, dec, extra in rows:
        connection.execute(
            "INSERT INTO targets VALUES (?, ?, ?, ?, ?)", (target_id, "", ra, dec, json.dumps(extra))
        )
    connection.commit()
    connection.close()


def test_planted_offset_is_flagged(tmp_path: Path) -> None:
    """A position 37.8 arcmin off is listed; the others are not."""
    database = tmp_path / "astrometrics.db"
    make_database(
        database,
        [
            ("M 57", "18 55 54.17", "32 37 45.85", {"positionUnverified": True}),
            ("Unknown thing", "1 00 00", "10 00 00", {}),
            ("Empty", "0h 0m 0s", "0° 0′ 0′′", {}),
        ],
    )
    findings = audit.audit_positions(audit.load_stored_targets(database), FakeSimbad(), 15.0)
    by_id = {finding.target_id: finding for finding in findings}
    assert by_id["M 57"].status == audit.STATUS_OFFSET
    assert by_id["M 57"].separation_arcmin == pytest.approx(37.8, abs=1.0)
    assert by_id["M 57"].position_unverified
    assert by_id["Unknown thing"].status == audit.STATUS_UNRESOLVED
    assert by_id["Empty"].status == audit.STATUS_NO_STORED_POSITION
    report = audit.format_findings(findings, 15.0, show_all=False)
    assert "M 57" in report and "marked unverified" in report


def test_position_inside_tolerance_matches(tmp_path: Path) -> None:
    """The catalog position itself is not listed."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("M 57", "18 53 35.08", "+33 01 45.03", {})])
    findings = audit.audit_positions(audit.load_stored_targets(database), FakeSimbad(), 15.0)
    assert findings[0].status == audit.STATUS_MATCH


def test_database_is_opened_read_only(tmp_path: Path) -> None:
    """Loading leaves the file unchanged; read-only mode refuses writes."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("M 57", "18 55 54.17", "32 37 45.85", {})])
    before = database.read_bytes()
    audit.load_stored_targets(database)
    assert database.read_bytes() == before
    read_only = sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)
    with pytest.raises(sqlite3.OperationalError):
        read_only.execute("DELETE FROM targets")
    read_only.close()


def test_missing_database_is_reported(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A missing file gives exit code 1 and does not create a database."""
    missing = tmp_path / "none.db"
    assert audit.main([str(missing)]) == 1
    assert not missing.exists()


def test_extended_object_gets_a_wider_tolerance(tmp_path: Path) -> None:
    """M 31 framed 60 arcmin from its center is inside its 95 arcmin radius."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("M 31", "0 42 44.4", "+42 16 08", {})])
    findings = audit.audit_positions(audit.load_stored_targets(database), FakeSimbad())
    assert findings[0].status == audit.STATUS_MATCH
    assert findings[0].tolerance_arcmin == pytest.approx(95.0)


def test_moving_and_calibration_targets_are_not_compared(tmp_path: Path) -> None:
    """Mars and a calibration folder are listed as not compared."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("Mars", "1 00 00", "10 00 00", {}), ("flat", "0h 0m 0s", "0", {})])
    findings = audit.audit_positions(audit.load_stored_targets(database), FakeSimbad())
    assert {finding.status for finding in findings} == {audit.STATUS_MOVING}
    assert "Not compared: moving or calibration (2)" in audit.format_findings(findings, 15.0, show_all=False)


def test_wal_database_gets_no_new_files(tmp_path: Path) -> None:
    """Auditing a WAL-mode database creates no -wal or -shm file."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("M 57", "18 55 54.17", "32 37 45.85", {})])
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.close()
    before = sorted(os.listdir(tmp_path))
    assert before == ["astrometrics.db"]
    audit.load_stored_targets(database)
    assert sorted(os.listdir(tmp_path)) == before


def test_main_with_a_fake_driver(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """main() prints the offsets for a planted database."""
    database = tmp_path / "astrometrics.db"
    make_database(database, [("M 57", "18 55 54.17", "32 37 45.85", {})])
    assert audit.main([str(database)], simbad=FakeSimbad()) == 0
    output = capsys.readouterr().out
    assert "M 57" in output and "1 targets: 0 match, 1 offset." in output


def test_main_without_a_path_uses_the_configured_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With no DATABASE argument the configured library path is used."""
    make_database(tmp_path / "astrometrics.db", [("M 57", "18 53 35.08", "+33 01 45.03", {})])
    monkeypatch.setattr(
        audit, "get_configuration", lambda: type("C", (), {"get_library_path": lambda self: tmp_path})()
    )
    assert audit.main([], simbad=FakeSimbad()) == 0
    assert "1 targets: 1 match, 0 offset." in capsys.readouterr().out
