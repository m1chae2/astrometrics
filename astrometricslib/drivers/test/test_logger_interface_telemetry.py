"""Purpose: Unit tests for LoggerInterface telemetry persistence.

Description: Verifies that LoggerInterface creates alignment_logs and
guiding_logs tables in astrometrics_log.db, persists alignment attempts and
guiding telemetry samples, and correctly retrieves them with target and
timestamp filters.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.foundation.jobs.store import LoggerInterface
from astrometricslib.foundation.errors import InvalidArgumentError


@pytest.fixture
def temp_logger_db(tmp_path: Path) -> LoggerInterface:
    """Create an isolated LoggerInterface using a temporary SQLite database.

    Parameters
    ----------
    tmp_path : `Path`
        Temporary directory provided by pytest.

    Returns
    -------
    logger_interface : `LoggerInterface`
        An initialized LoggerInterface pointing to the temp database.
    """
    db_file = tmp_path / "test_telemetry.db"
    return LoggerInterface(db_path=str(db_file))


def test_record_and_get_alignment_logs(temp_logger_db: LoggerInterface) -> None:
    """Verify persisting and querying plate-solve alignment logs."""
    attempt_1: dict[str, Any] = {
        "timestamp": 1700000000.0,
        "target_name": "M31",
        "status": "aligned",
        "delta_ra_arcsec": 14.5,
        "delta_dec_arcsec": -22.1,
        "pointing_error_arcsec": 26.4,
        "sync_mode": True,
    }
    attempt_2: dict[str, Any] = {
        "timestamp": 1700000010.0,
        "target_name": "M42",
        "status": "aligned",
        "delta_ra_arcsec": 5.2,
        "delta_dec_arcsec": 3.1,
        "pointing_error_arcsec": 6.0,
        "sync_mode": True,
    }

    temp_logger_db.record_alignment_attempt(attempt_1)
    temp_logger_db.record_alignment_attempt(attempt_2)

    # Query all alignment logs
    all_logs = temp_logger_db.get_alignment_logs()
    assert len(all_logs) == 2
    # Should be ordered timestamp DESC
    assert all_logs[0]["target_name"] == "M42"
    assert all_logs[1]["target_name"] == "M31"

    # Query filtered by target
    m31_logs = temp_logger_db.get_alignment_logs(target_name="M31")
    assert len(m31_logs) == 1
    assert m31_logs[0]["delta_ra_arcsec"] == pytest.approx(14.5)
    assert m31_logs[0]["delta_dec_arcsec"] == pytest.approx(-22.1)
    assert m31_logs[0]["pointing_error_arcsec"] == pytest.approx(26.4)
    assert m31_logs[0]["status"] == "aligned"


def test_record_and_get_guiding_logs(temp_logger_db: LoggerInterface) -> None:
    """Verify batch insertion and query filtering for guiding telemetry."""
    samples: list[dict[str, Any]] = [
        {
            "timestamp": 1700000100.0,
            "target_name": "NGC7000",
            "dra": 0.15,
            "ddec": -0.10,
            "pulse_ra": 40.0,
            "pulse_dec": -20.0,
            "snr": 35.0,
            "rms_ra": 0.12,
            "rms_dec": 0.08,
            "star_mass": 4200.0,
        },
        {
            "timestamp": 1700000102.5,
            "target_name": "NGC7000",
            "dra": -0.05,
            "ddec": 0.02,
            "pulse_ra": -15.0,
            "pulse_dec": 0.0,
            "snr": 36.5,
            "rms_ra": 0.11,
            "rms_dec": 0.08,
            "star_mass": 4210.0,
        },
        {
            "timestamp": 1700000105.0,
            "target_name": "IC1396",
            "dra": 0.20,
            "ddec": 0.25,
            "pulse_ra": 50.0,
            "pulse_dec": 60.0,
            "snr": 29.0,
            "rms_ra": 0.18,
            "rms_dec": 0.22,
            "star_mass": 3800.0,
        },
    ]

    temp_logger_db.record_guiding_samples(samples)

    # Query all
    all_guiding = temp_logger_db.get_guiding_logs()
    assert len(all_guiding) == 3

    # Query filtered by target
    ngc_guiding = temp_logger_db.get_guiding_logs(target_name="NGC7000")
    assert len(ngc_guiding) == 2
    assert ngc_guiding[0]["dra"] == pytest.approx(0.15)
    assert ngc_guiding[0]["pulse_ra"] == pytest.approx(40.0)
    assert ngc_guiding[1]["dra"] == pytest.approx(-0.05)

    # Query with start_time
    recent_guiding = temp_logger_db.get_guiding_logs(start_time=1700000103.0)
    assert len(recent_guiding) == 1
    assert recent_guiding[0]["target_name"] == "IC1396"


def test_reject_sub_arcsecond_tracking_echoes(temp_logger_db: LoggerInterface) -> None:
    """Verify spurious sub-0.5 arcsecond tracking loop echoes are rejected.

    Parameters
    ----------
    temp_logger_db : `LoggerInterface`
        Isolated temporary test database instance.
    """
    # Mount tracking loop heartbeat echo (0.02 arcsec error)
    echo_attempt: dict[str, Any] = {
        "timestamp": 1700000200.0,
        "target_name": "Navi",
        "status": "aligned",
        "delta_ra_arcsec": 0.02,
        "delta_dec_arcsec": 0.0,
        "pointing_error_arcsec": 0.02,
    }
    # Genuine plate-solve alignment (8.5 arcsec error)
    solve_attempt: dict[str, Any] = {
        "timestamp": 1700000210.0,
        "target_name": "Navi",
        "status": "aligned",
        "delta_ra_arcsec": 5.1,
        "delta_dec_arcsec": 6.8,
        "pointing_error_arcsec": 8.5,
    }

    temp_logger_db.record_alignment_attempt(echo_attempt)
    temp_logger_db.record_alignment_attempt(solve_attempt)

    logs = temp_logger_db.get_alignment_logs()
    assert len(logs) == 1
    assert logs[0]["pointing_error_arcsec"] == pytest.approx(8.5)


def test_cleanup_spurious_alignment_logs(temp_logger_db: LoggerInterface) -> None:
    """Verify cleanup_spurious_alignment_logs purges historical noise rows.

    Parameters
    ----------
    temp_logger_db : `LoggerInterface`
        Isolated temporary test database instance.
    """
    # Force insert spurious records
    temp_logger_db.record_alignment_attempt({
        "timestamp": 1700000300.0,
        "target_name": "Mirach",
        "status": "aligned",
        "delta_ra_arcsec": 0.01,
        "delta_dec_arcsec": 0.0,
        "pointing_error_arcsec": 0.01,
        "force_record": True,
    })
    temp_logger_db.record_alignment_attempt({
        "timestamp": 1700000305.0,
        "target_name": "Mirach",
        "status": "aligned",
        "delta_ra_arcsec": 12.0,
        "delta_dec_arcsec": -10.0,
        "pointing_error_arcsec": 15.62,
    })

    assert len(temp_logger_db.get_alignment_logs()) == 2
    deleted = temp_logger_db.cleanup_spurious_alignment_logs(max_threshold_arcsec=0.5)
    assert deleted == 1

    remaining = temp_logger_db.get_alignment_logs()
    assert len(remaining) == 1
    assert remaining[0]["pointing_error_arcsec"] == pytest.approx(15.62)


def test_guiding_samples_with_no_session_id_are_filed_under_their_observing_night(
    temp_logger_db: LoggerInterface,
) -> None:
    """Verify a night that crosses midnight is stored as one session."""
    from datetime import datetime

    evening = datetime(2026, 9, 24, 21, 0).timestamp()
    after_midnight = datetime(2026, 9, 25, 1, 30).timestamp()
    temp_logger_db.record_guiding_samples([
        {"timestamp": evening, "dra": 0.1, "ddec": 0.2},
        {"timestamp": after_midnight, "dra": 0.3, "ddec": 0.4},
    ])

    stored = temp_logger_db.get_guiding_logs()
    assert {row["session_id"] for row in stored} == {"2026-09-24"}
    assert len(temp_logger_db.get_guiding_logs(session_id="2026-09-24")) == 2


def test_alignment_attempts_with_no_session_id_are_filed_under_their_observing_night(
    temp_logger_db: LoggerInterface,
) -> None:
    """Verify alignment attempts get the same night name as guiding samples."""
    from datetime import datetime

    after_midnight = datetime(2026, 9, 25, 1, 30).timestamp()
    temp_logger_db.record_alignment_attempt({
        "timestamp": after_midnight,
        "target_name": "M13",
        "status": "warning",
        "delta_ra_arcsec": 40.0,
        "delta_dec_arcsec": 30.0,
    })

    (stored,) = temp_logger_db.get_session_alignment_attempts("2026-09-24")
    assert stored["session_id"] == "2026-09-24"


def test_an_explicit_session_id_is_kept(temp_logger_db: LoggerInterface) -> None:
    """Verify the automatic night name never overrides a given session id."""
    temp_logger_db.record_guiding_samples([{"timestamp": 1700000000.0, "session_id": "my-session"}])
    assert temp_logger_db.get_guiding_logs()[0]["session_id"] == "my-session"


def test_guiding_samples_record_where_they_came_from(temp_logger_db: LoggerInterface) -> None:
    """Verify `source` is stored, defaults to 'unverified', and filters."""
    temp_logger_db.record_guiding_samples([
        {"timestamp": 1.0, "source": "measured_test"},
        {"timestamp": 2.0, "source": "estimated_test"},
        {"timestamp": 3.0},
    ])

    by_source = {row["timestamp"]: row["source"] for row in temp_logger_db.get_guiding_logs()}
    assert by_source == {1.0: "measured_test", 2.0: "estimated_test", 3.0: "unverified"}

    measured_only = temp_logger_db.get_guiding_logs(sources=["measured_test"])
    assert [row["timestamp"] for row in measured_only] == [1.0]
    assert temp_logger_db.get_guiding_logs(sources=[]) == []


def test_a_database_created_before_the_source_column_is_upgraded_in_place(tmp_path: Path) -> None:
    """Verify old rows survive the upgrade and are marked 'unverified'."""
    import sqlite3

    db_path = str(tmp_path / "old_schema.db")
    old_connection = sqlite3.connect(db_path)
    old_connection.execute(
        """
        CREATE TABLE guiding_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, target_name TEXT,
            timestamp REAL NOT NULL, dra REAL NOT NULL, ddec REAL NOT NULL,
            pulse_ra REAL DEFAULT 0.0, pulse_dec REAL DEFAULT 0.0, snr REAL,
            rms_ra REAL, rms_dec REAL, star_mass REAL
        )
        """
    )
    old_connection.execute("INSERT INTO guiding_logs (timestamp, dra, ddec) VALUES (5.0, 0.5, 0.6)")
    old_connection.commit()
    old_connection.close()

    upgraded = LoggerInterface(db_path=db_path)

    (row,) = upgraded.get_guiding_logs()
    assert row["dra"] == pytest.approx(0.5)
    assert row["source"] == "unverified"
    upgraded.record_guiding_samples([{"timestamp": 6.0, "source": "measured_test"}])
    assert len(upgraded.get_guiding_logs()) == 2


def test_replacing_guiding_samples_does_not_duplicate_a_reread_log(temp_logger_db: LoggerInterface) -> None:
    """Verify reading the same log twice leaves one copy of each sample."""
    batch = [
        {"timestamp": 100.0, "dra": 0.1, "source": "log_a"},
        {"timestamp": 101.0, "dra": 0.2, "source": "log_a"},
    ]

    assert temp_logger_db.replace_guiding_samples(batch) == 0
    assert temp_logger_db.replace_guiding_samples(batch) == 2

    assert [row["timestamp"] for row in temp_logger_db.get_guiding_logs()] == [100.0, 101.0]


def test_replacing_guiding_samples_picks_up_a_log_that_grew(temp_logger_db: LoggerInterface) -> None:
    """Verify a longer second read keeps every sample exactly once."""
    first_read = [{"timestamp": 100.0 + second, "source": "log_a"} for second in range(3)]
    second_read = [{"timestamp": 100.0 + second, "source": "log_a"} for second in range(5)]

    temp_logger_db.replace_guiding_samples(first_read)
    temp_logger_db.replace_guiding_samples(second_read)

    assert len(temp_logger_db.get_guiding_logs()) == 5


def test_replacing_guiding_samples_never_touches_other_sources(temp_logger_db: LoggerInterface) -> None:
    """Verify an estimate at the same time survives a measured replace."""
    temp_logger_db.record_guiding_samples([{"timestamp": 100.0, "source": "estimated"}])
    temp_logger_db.replace_guiding_samples([{"timestamp": 100.0, "source": "measured"}])

    assert {row["source"] for row in temp_logger_db.get_guiding_logs()} == {"estimated", "measured"}


def test_replacing_guiding_samples_raises_when_the_database_cannot_be_written(tmp_path: Path) -> None:
    """Verify a write failure is raised, not hidden behind a zero count."""
    import sqlite3

    interface = LoggerInterface(db_path=str(tmp_path / "log.db"))
    interface.db_path = str(tmp_path / "missing_folder" / "log.db")

    with pytest.raises(sqlite3.Error):
        interface.replace_guiding_samples([{"timestamp": 1.0, "source": "a"}])


def test_replacing_guiding_samples_rejects_mixed_sources(temp_logger_db: LoggerInterface) -> None:
    """Verify a batch must come from one source."""
    with pytest.raises(InvalidArgumentError, match="single source"):
        temp_logger_db.replace_guiding_samples([
            {"timestamp": 1.0, "source": "a"},
            {"timestamp": 2.0, "source": "b"},
        ])
