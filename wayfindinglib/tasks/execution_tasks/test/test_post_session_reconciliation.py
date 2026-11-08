"""Purpose: Unit tests for post_session_reconciliation.

Description: Verifies target_session_ids attach only for completed entries
whose target has matching frames, and reconcile_session raises for a
non-terminal session and records the reconciled session for a terminal
one -- the behavior `Wayfinding_Library_Architecture.md` Section 5.7
describes. It no longer writes a calibration_stats record.
"""

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from astrometricslib._internal.frames import derive_target_sessions
from astrometricslib.exceptions import ConflictError
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
    reconcile_session,
)


def _entry(
    entry_id: str, target_id: str, status: QueueEntryStatus, exposure_requests: list[ExposureRequest]
) -> QueuedObservationPackage:
    return QueuedObservationPackage(
        id=entry_id,
        observation_package_id=f"pkg-{entry_id}",
        target_id=target_id,
        exposure_requests=exposure_requests,
        start_time_mode=StartTimeMode.SOONEST,
        status=status,
    )


def _session(
    session_id: str,
    camera_id: str,
    status: SessionStatus,
    entries: list[QueuedObservationPackage],
    night_date: date = date(2026, 8, 10),
) -> ObservationSession:
    return ObservationSession(
        id=session_id,
        night_date=night_date,
        status=status,
        site_profile_id="site-1",
        telescope_id="scope-1",
        camera_id=camera_id,
        queue=entries,
    )


@dataclass
class _FakeFrame:
    """A stand-in frame record with the fields reconciliation reads."""

    timestamp: float
    iso: str = "800"
    offset: str = "0"
    path: str = "frame.fits"


@dataclass
class _FakeTarget:
    """A stand-in target holding only its frames."""

    frames: list[_FakeFrame]


class _FakeTargetRegistry:
    """A stand-in target catalog that looks targets up by id."""

    def __init__(self, targets: dict[str, _FakeTarget]) -> None:
        """Keep the targets by id."""
        self._targets = targets

    def get(self, target_id: str) -> _FakeTarget | None:
        """Return the target with this id, if any.

        Returns
        -------
        target : `_FakeTarget` or `None`
            The target, or `None` when no target has this id.
        """
        return self._targets.get(target_id)

    def sessions(self, target: _FakeTarget, night_date: date | None = None) -> list[Any]:
        """Group a target's frames into sessions, as the library does.

        Returns
        -------
        sessions : `list`
            The target's sessions, only those of `night_date` when given.
        """
        target_id = next(key for key, value in self._targets.items() if value is target)
        found = derive_target_sessions(target_id, target.frames)
        return [session for session in found if night_date is None or session.night_date == night_date]


class _FakeAstrometrics:
    """A stand-in `Astrometrics` with only a target catalog."""

    def __init__(self, targets: dict[str, _FakeTarget]) -> None:
        """Build the target catalog."""
        self._targets = targets
        self.targets = _FakeTargetRegistry(targets)


def test_attach_target_session_ids_derives_from_matching_target_frames() -> None:
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


def test_attach_target_session_ids_ignores_non_completed_entries() -> None:
    """Verify a target with only a FAILED entry contributes no ids."""
    astrometrics = _FakeAstrometrics({"M 81": _FakeTarget([_FakeFrame(timestamp=0.0)])})
    entry = _entry("entry-1", "M 81", QueueEntryStatus.FAILED, [])
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    result = attach_target_session_ids(session, astrometrics)

    assert result.target_session_ids == []


def test_attach_target_session_ids_ignores_unknown_target() -> None:
    """Verify an unresolvable target is skipped rather than raising."""
    astrometrics = _FakeAstrometrics({})
    entry = _entry("entry-1", "does-not-exist", QueueEntryStatus.COMPLETED, [])
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])

    result = attach_target_session_ids(session, astrometrics)

    assert result.target_session_ids == []


@pytest.fixture
def butler(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> DiskButler:
    """Build a DiskButler backed by an isolated temporary database.

    Returns
    -------
    butler : `DiskButler`
        A fresh, isolated butler instance.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_reconcile_session_raises_for_non_terminal_session(butler: DiskButler) -> None:
    """Verify reconcile_session refuses a session that has not ended."""
    entry = _entry("entry-1", "M 81", QueueEntryStatus.RUNNING, [])
    session = _session("session-1", "cam-1", SessionStatus.RUNNING, [entry])

    with pytest.raises(ConflictError, match="not terminal"):
        reconcile_session(butler, session, _FakeAstrometrics({}))


def test_reconcile_session_persists_the_session_and_writes_no_calibration_stats(butler: DiskButler) -> None:
    """Verify a terminal session's reconciliation records the session only."""
    entry = _entry(
        "entry-1",
        "M 81",
        QueueEntryStatus.COMPLETED,
        [ExposureRequest(frame_type=FrameType.DARK, exposure_sec=300.0, count=20)],
    )
    session = _session("session-1", "cam-1", SessionStatus.COMPLETED, [entry])
    butler.put(session, "observation_session", {"session_id": session.id})

    reconcile_session(butler, session, _FakeAstrometrics({}))

    assert butler.get("calibration_stats", {"camera_id": "cam-1"}) is None

    reloaded_session = butler.get("observation_session", {"session_id": "session-1"})
    assert reloaded_session is not None
