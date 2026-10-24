"""Tests for the script that audits stored target positions.

The database is a small temporary SQLite file and SIMBAD is faked. They check
that a planted offset is flagged, that a good position and an unknown name are
not, and that the script opens the database read-only.
"""

import json
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
