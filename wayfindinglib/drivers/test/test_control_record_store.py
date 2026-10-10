"""Purpose: Unit tests for `ControlRecordStore`.

Description: Checks that the store keeps alignment attempts, guiding
samples and polar alignment runs in ``wayfinding.db``, files each record
under its observing night, and reads them back with target, night, time
and source filters.
"""

from pathlib import Path
from typing import Any

import pytest

from astrometricslib import InvalidArgumentError, StorageError
from wayfindinglib.drivers.control_record_store import ControlRecordStore


@pytest.fixture
def store(tmp_path: Path) -> ControlRecordStore:
    """Create a record store on a temporary database.

    Parameters
    ----------
    tmp_path : `Path`
        Temporary directory provided by pytest.

    Returns
    -------
    store : `ControlRecordStore`
        A store on the temporary database.
    """
    db_file = tmp_path / "test_telemetry.db"
    return ControlRecordStore(db_path=str(db_file))


def test_record_and_get_alignment_attempts(store: ControlRecordStore) -> None:
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

    store.record_alignment_attempt(attempt_1)
    store.record_alignment_attempt(attempt_2)

    # Query all alignment logs
    all_logs = store.get_alignment_attempts()
    assert len(all_logs) == 2
    # Should be ordered timestamp DESC
    assert all_logs[0]["target_name"] == "M42"
    assert all_logs[1]["target_name"] == "M31"

    # Query filtered by target
    m31_logs = store.get_alignment_attempts(target_name="M31")
    assert len(m31_logs) == 1
    assert m31_logs[0]["delta_ra_arcsec"] == pytest.approx(14.5)
    assert m31_logs[0]["delta_dec_arcsec"] == pytest.approx(-22.1)
    assert m31_logs[0]["pointing_error_arcsec"] == pytest.approx(26.4)
    assert m31_logs[0]["status"] == "aligned"


def test_record_and_get_guiding_samples(store: ControlRecordStore) -> None:
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

    store.record_guiding_samples(samples)

    # Query all
    all_guiding = store.get_guiding_samples()
    assert len(all_guiding) == 3

    # Query filtered by target
    ngc_guiding = store.get_guiding_samples(target_name="NGC7000")
    assert len(ngc_guiding) == 2
    assert ngc_guiding[0]["dra"] == pytest.approx(0.15)
    assert ngc_guiding[0]["pulse_ra"] == pytest.approx(40.0)
    assert ngc_guiding[1]["dra"] == pytest.approx(-0.05)

    # Query with start_time
    recent_guiding = store.get_guiding_samples(start_time=1700000103.0)
    assert len(recent_guiding) == 1
    assert recent_guiding[0]["target_name"] == "IC1396"


def test_reject_sub_arcsecond_tracking_echoes(store: ControlRecordStore) -> None:
    """Verify spurious sub-0.5 arcsecond tracking loop echoes are rejected.

    Parameters
    ----------
    store : `ControlRecordStore`
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

    store.record_alignment_attempt(echo_attempt)
    store.record_alignment_attempt(solve_attempt)

    logs = store.get_alignment_attempts()
    assert len(logs) == 1
    assert logs[0]["pointing_error_arcsec"] == pytest.approx(8.5)


def test_guiding_samples_with_no_session_id_are_filed_under_their_observing_night(
    store: ControlRecordStore,
) -> None:
    """Verify a night that crosses midnight is stored as one session."""
    from datetime import datetime

    evening = datetime(2026, 9, 24, 21, 0).timestamp()
    after_midnight = datetime(2026, 9, 25, 1, 30).timestamp()
    store.record_guiding_samples([
        {"timestamp": evening, "dra": 0.1, "ddec": 0.2},
        {"timestamp": after_midnight, "dra": 0.3, "ddec": 0.4},
    ])

    stored = store.get_guiding_samples()
    assert {row["session_id"] for row in stored} == {"2026-09-24"}
    assert len(store.get_guiding_samples(session_id="2026-09-24")) == 2


def test_alignment_attempts_with_no_session_id_are_filed_under_their_observing_night(
    store: ControlRecordStore,
) -> None:
    """Verify alignment attempts get the same night name as guiding samples."""
    from datetime import datetime

    after_midnight = datetime(2026, 9, 25, 1, 30).timestamp()
    store.record_alignment_attempt({
        "timestamp": after_midnight,
        "target_name": "M13",
        "status": "warning",
        "delta_ra_arcsec": 40.0,
        "delta_dec_arcsec": 30.0,
    })

    (stored,) = store.get_session_alignment_attempts("2026-09-24")
    assert stored["session_id"] == "2026-09-24"


def test_an_explicit_session_id_is_kept(store: ControlRecordStore) -> None:
    """Verify the automatic night name never overrides a given session id."""
    store.record_guiding_samples([{"timestamp": 1700000000.0, "session_id": "my-session"}])
    assert store.get_guiding_samples()[0]["session_id"] == "my-session"


def test_guiding_samples_record_where_they_came_from(store: ControlRecordStore) -> None:
    """Verify `source` is stored, defaults to 'unverified', and filters."""
    store.record_guiding_samples([
        {"timestamp": 1.0, "source": "measured_test"},
        {"timestamp": 2.0, "source": "estimated_test"},
        {"timestamp": 3.0},
    ])

    by_source = {row["timestamp"]: row["source"] for row in store.get_guiding_samples()}
    assert by_source == {1.0: "measured_test", 2.0: "estimated_test", 3.0: "unverified"}

    measured_only = store.get_guiding_samples(sources=["measured_test"])
    assert [row["timestamp"] for row in measured_only] == [1.0]
    assert store.get_guiding_samples(sources=[]) == []


def test_replacing_guiding_samples_does_not_duplicate_a_reread_log(store: ControlRecordStore) -> None:
    """Verify reading the same log twice leaves one copy of each sample."""
    batch = [
        {"timestamp": 100.0, "dra": 0.1, "source": "log_a"},
        {"timestamp": 101.0, "dra": 0.2, "source": "log_a"},
    ]

    assert store.replace_guiding_samples(batch) == 0
    assert store.replace_guiding_samples(batch) == 2

    assert [row["timestamp"] for row in store.get_guiding_samples()] == [100.0, 101.0]


def test_replacing_guiding_samples_picks_up_a_log_that_grew(store: ControlRecordStore) -> None:
    """Verify a longer second read keeps every sample exactly once."""
    first_read = [{"timestamp": 100.0 + second, "source": "log_a"} for second in range(3)]
    second_read = [{"timestamp": 100.0 + second, "source": "log_a"} for second in range(5)]

    store.replace_guiding_samples(first_read)
    store.replace_guiding_samples(second_read)

    assert len(store.get_guiding_samples()) == 5


def test_replacing_guiding_samples_never_touches_other_sources(store: ControlRecordStore) -> None:
    """Verify an estimate at the same time survives a measured replace."""
    store.record_guiding_samples([{"timestamp": 100.0, "source": "estimated"}])
    store.replace_guiding_samples([{"timestamp": 100.0, "source": "measured"}])

    assert {row["source"] for row in store.get_guiding_samples()} == {"estimated", "measured"}


def test_replacing_guiding_samples_raises_when_the_database_cannot_be_written(tmp_path: Path) -> None:
    """Verify a write failure is raised, not hidden behind a zero count."""
    store = ControlRecordStore(db_path=str(tmp_path / "wayfinding.db"))
    store.db_path = str(tmp_path)

    with pytest.raises(StorageError):
        store.replace_guiding_samples([{"timestamp": 1.0, "source": "a"}])


def test_replacing_guiding_samples_rejects_mixed_sources(store: ControlRecordStore) -> None:
    """Verify a batch must come from one source."""
    with pytest.raises(InvalidArgumentError, match="single source"):
        store.replace_guiding_samples([
            {"timestamp": 1.0, "source": "a"},
            {"timestamp": 2.0, "source": "b"},
        ])


def test_polar_alignment_runs_round_trip_with_their_points(store: ControlRecordStore) -> None:
    """Verify a polar alignment run is stored and its points read back."""
    store.record_polar_alignment({
        "session_id": "2026-09-24",
        "timestamp": 1700000000.0,
        "status": "warning",
        "total_error_arcsec": 90.0,
        "paa_points": [{"ra": 1.0, "dec": 89.0}],
    })
    store.record_polar_alignment({"session_id": "2026-09-25", "timestamp": 1700090000.0})

    (run,) = store.get_polar_alignments(session_id="2026-09-24")
    assert run["status"] == "warning"
    assert run["paa_points"] == [{"ra": 1.0, "dec": 89.0}]
    assert [row["session_id"] for row in store.get_polar_alignments()] == ["2026-09-25", "2026-09-24"]
    assert store.get_polar_alignments()[0]["paa_points"] == []


def test_alignment_sessions_summarize_each_night(store: ControlRecordStore) -> None:
    """Verify a night's syncs, guiding samples and polar run are summarized."""
    store.record_alignment_attempt({
        "timestamp": 1700000000.0,
        "session_id": "night-a",
        "status": "aligned",
        "pointing_error_arcsec": 10.0,
    })
    store.record_alignment_attempt({
        "timestamp": 1700000100.0,
        "session_id": "night-a",
        "status": "aligned",
        "pointing_error_arcsec": 20.0,
    })
    store.record_guiding_samples([
        {"timestamp": 1700000050.0, "session_id": "night-a", "dra": 0.1, "ddec": 0.1}
    ])
    store.record_polar_alignment({
        "session_id": "night-a",
        "timestamp": 1700000010.0,
        "total_error_arcsec": 30.0,
    })

    (night,) = store.get_alignment_sessions()
    assert night["session_id"] == "night-a"
    assert night["sync_count"] == 2
    assert night["guiding_count"] == 1
    assert night["avg_error_arcsec"] == pytest.approx(15.0)
    assert night["polar_error_arcsec"] == pytest.approx(30.0)


def test_target_telemetry_uses_guiding_samples_near_the_frames(store: ControlRecordStore) -> None:
    """Verify a target's frames pick up the guiding samples taken with them."""
    from astrometricslib import FrameRecord, Target

    store.record_guiding_samples([{"timestamp": 1700000030.0, "dra": 3.0, "ddec": 4.0}])
    imaged = Target(
        id="M 13",
        ra="16h 41m 41s",
        dec="+36d 27m 35s",
        frames=[
            FrameRecord(path="a.fits", timestamp=1700000000.0),
            FrameRecord(path="b.fits", timestamp=1700000060.0),
        ],
    )
    unguided = Target(
        id="M 92",
        ra="17h 17m 07s",
        dec="+43d 08m 09s",
        frames=[
            FrameRecord(path="c.fits", timestamp=1700500000.0),
        ],
    )

    attempts = store.get_session_target_telemetry("all", [imaged, unguided])

    guided = [attempt for attempt in attempts if attempt["targetName"] == "M 13"]
    assert [attempt["pointingErrorArcsec"] for attempt in guided] == [pytest.approx(5.0)]
    assert [attempt["deltaRaArcsec"] for attempt in attempts if attempt["targetName"] == "M 92"] == [0.0]
