"""Purpose: Unit tests for LoggerInterface telemetry persistence.

Description: Verifies that LoggerInterface creates alignment_logs and
guiding_logs tables in astrometrics_log.db, persists alignment attempts and
guiding telemetry samples, and correctly retrieves them with target and
timestamp filters.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib.drivers.logger_interface import LoggerInterface


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
