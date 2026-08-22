"""Purpose: Unit tests for post_session_reconciliation.

Description: Verifies calibration-frame folding counts only completed
calibration-type requests on terminal sessions, is idempotent when
recomputed, target_session_ids attach only for completed entries whose
target has matching frames, and reconcile_session raises for a
non-terminal session and persists both results for a terminal one --
the cases `Wayfinding_Library_Architecture.md` §2.4.11 calls out
("reconciliation run twice yields identical CalibrationStats").
"""

from datetime import date

import pytest

from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType
from wayfindinglib.models.session.observation_session import (
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
)
from wayfindinglib.tasks.execution_tasks.post_session_reconciliation import (
    attach_target_session_ids,
    compute_calibration_stats,
    reconcile_session,
)


def _entry(entry_id, target_id, status, exposure_requests) -> QueuedObservationPackage:  # ruff: ignore[missing-type-function-argument]
    return QueuedObservationPackage(
        id=entry_id,
        observation_package_id=f"pkg-{entry_id}",
        target_id=target_id,
        exposure_requests=exposure_requests,
        start_time_mode=StartTimeMode.SOONEST,
        status=status,
    )


def _session(session_id, camera_id, status, entries, night_date=date(2026, 8, 10)) -> ObservationSession:  # ruff: ignore[missing-type-function-argument]
    return ObservationSession(
        id=session_id,
        night_date=night_date,
        status=status,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id=camera_id,
        queue=entries,
    )


def test_compute_calibration_stats_counts_completed_calibration_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify calibration-type requests on COMPLETED entries are counted."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=20)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    stats = compute_calibration_stats("cam-1", [session])

    assert len(stats.darks) == 1
    assert stats.darks[0].count == 20
    assert stats.darks[0].exposure_sec == pytest.approx(300.0)


def test_compute_calibration_stats_ignores_light_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify LIGHT frame requests are never counted as calibration."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=20)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    stats = compute_calibration_stats("cam-1", [session])

    assert stats.darks == []
    assert stats.biases == []
    assert stats.flats == []


def test_compute_calibration_stats_ignores_non_completed_entries():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify calibration requests on a FAILED entry are not counted."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.FAILED,
        [ExposureRequest(frame_type=FrameType.BIAS, exposure_sec=0.001, count=50)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    stats = compute_calibration_stats("cam-1", [session])

    assert stats.biases == []


def test_compute_calibration_stats_ignores_non_terminal_sessions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a still-RUNNING session's frames are not counted."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.FLAT, exposure_sec=1.0, count=15)],
    )
    session = _session("session-1", "cam-1", SessionStatus.RUNNING, [entry])

    stats = compute_calibration_stats("cam-1", [session])

    assert stats.flats == []


def test_compute_calibration_stats_recompute_is_idempotent():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify recomputing from the same sessions twice is idempotent."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=20)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    first = compute_calibration_stats("cam-1", [session])
    second = compute_calibration_stats("cam-1", [session])

    assert first == second


def test_compute_calibration_stats_sums_across_multiple_sessions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify counts accumulate across multiple terminal sessions."""
    entry_a = _entry(
        "entry-a",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=10)],
    )
    entry_b = _entry(
        "entry-b",
        "NGC 7000",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=15)],
    )
    session_a = _session("session-a", "cam-1", SessionStatus.COMPLETED, [entry_a])
    session_b = _session("session-b", "cam-1", SessionStatus.ABORTED, [entry_b])

    stats = compute_calibration_stats("cam-1", [session_a, session_b])

    assert len(stats.darks) == 1
    assert stats.darks[0].count == 25


class _FakeFrame:
    def __init__(self, timestamp, iso="800", offset="0", path="frame.fits"):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.timestamp = timestamp
        self.iso = iso
        self.offset = offset
        self.path = path


class _FakeTarget:
    def __init__(self, frames):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.frames = frames


class _FakeTargetRegistry:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = targets

    def get(self, target_id):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return self._targets.get(target_id)


class _FakeAstrometrics:
    def __init__(self, targets):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self._targets = targets
        self.targets = _FakeTargetRegistry(targets)


def test_attach_target_session_ids_derives_from_matching_target_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify target_session_ids picks up sessions matching the night_date."""
    import time
    from datetime import datetime

    # A frame timestamp at local noon on 2026-08-10 falls in that night.
    night_noon = time.mktime(datetime(2026, 8, 10, 13, 0, 0).timetuple())
    frame = _FakeFrame(timestamp=night_noon)
    astrometrics = _FakeAstrometrics({"M 81": _FakeTarget([frame])})

    entry = _entry("entry-1", "M 81", QueueEntryStatus.COMPLETED, [])
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry], night_date=date(2026, 8, 10))

    result = attach_target_session_ids(session, astrometrics)

    assert result.target_session_ids == ["M 81:2026-08-10:800:0"]


def test_attach_target_session_ids_ignores_non_completed_entries():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a target with only a FAILED entry contributes no ids."""
    astrometrics = _FakeAstrometrics({"M 81": _FakeTarget([_FakeFrame(timestamp=0.0)])})
    entry = _entry("entry-1", "M 81", QueueEntryStatus.FAILED, [])
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    result = attach_target_session_ids(session, astrometrics)

    assert result.target_session_ids == []


def test_attach_target_session_ids_ignores_unknown_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unresolvable target is skipped rather than raising."""
    astrometrics = _FakeAstrometrics({})
    entry = _entry("entry-1", "does-not-exist", QueueEntryStatus.COMPLETED, [])
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    result = attach_target_session_ids(session, astrometrics)

    assert result.target_session_ids == []


@pytest.fixture
def butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_reconcile_session_raises_for_non_terminal_session(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify reconcile_session refuses a session that has not ended."""
    entry = _entry("entry-1", "M 81", QueueEntryStatus.RUNNING, [])
    session = _session("session-1", "cam-1", SessionStatus.RUNNING, [entry])

    with pytest.raises(ValueError, match="not terminal"):
        reconcile_session(butler, session, _FakeAstrometrics({}))


def test_reconcile_session_persists_calibration_stats_and_session(butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a terminal session's reconciliation persists both results."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=20)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])
    butler.put(session, "observation_session", {"session_id": session.id})

    reconcile_session(butler, session, _FakeAstrometrics({}))

    stats = butler.get("calibration_stats", {"camera_id": "cam-1"})
    assert stats is not None
    assert stats.darks[0].count == 20

    reloaded_session = butler.get("observation_session", {"session_id": "session-1"})
    assert reloaded_session is not None
