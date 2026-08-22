"""Purpose: Unit tests for observation session domain models.

Description: Verifies QueueEntryStatus has no INFEASIBLE value (unplaced
packages never enter the queue), QueuedObservationPackage freezes a
self-contained snapshot, and ObservationSession round-trips its full
placement-and-outcome shape including the new SUSPENDED status.
"""

import pytest

from wayfindinglib.models.planning.observation_package import DitherConfig, ExposureRequest, FrameType
from wayfindinglib.models.session.observation_session import (
    InfeasibilityReason,
    InfeasibilityReasonCode,
    ObservationSession,
    QueuedObservationPackage,
    QueueEntryStatus,
    SessionStatus,
    StartTimeMode,
    WeatherSample,
)


def test_queue_entry_status_has_no_infeasible_value():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify INFEASIBLE was deliberately removed as unreachable.

    Unplaced packages never enter the queue -- infeasibility is
    reported on unplaced_package_diagnostics, a separate channel.
    """
    assert not hasattr(QueueEntryStatus, "INFEASIBLE")
    assert {s.value for s in QueueEntryStatus} == {"PENDING", "RUNNING", "COMPLETED", "FAILED", "SKIPPED"}


def test_session_status_includes_suspended():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify SUSPENDED exists alongside the four original lifecycle states."""
    assert {s.value for s in SessionStatus} == {"PLANNED", "RUNNING", "SUSPENDED", "COMPLETED", "ABORTED"}


def test_all_five_infeasibility_reason_codes_distinct():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the closed set of infeasibility reason codes has five members."""
    codes = {c.value for c in InfeasibilityReasonCode}
    assert codes == {
        "NEVER_CLEARS_ALTITUDE",
        "BLOCKED_BY_AVOIDANCE_ZONE",
        "WINDOW_TOO_SHORT",
        "NIGHT_FULLY_COMMITTED",
        "FIXED_TIME_CONFLICT",
    }


def test_queued_observation_package_freezes_a_self_contained_snapshot():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a queue entry carries its own copy of requests and priority.

    Template/Instance Separation: editing the originating ObservationPackage
    afterward must not be able to change what this entry recorded, which
    requires the entry to carry every value needed to execute it.
    """
    entry = QueuedObservationPackage(
        id="qp1",
        observation_package_id="pkg1",
        target_id="M 81",
        exposure_requests=[ExposureRequest(frame_type=FrameType.LIGHT, exposure_sec=300.0, count=10)],
        dither_config=DitherConfig(enabled=True),
        minimum_altitude_deg=30.0,
        priority=5,
        applied_priority_boost=2,
        start_time_mode=StartTimeMode.SOONEST,
    )
    assert entry.exposure_requests[0].exposure_sec == pytest.approx(300.0)
    assert entry.dither_config.enabled is True
    assert entry.priority == 5
    assert entry.applied_priority_boost == 2
    assert entry.status == QueueEntryStatus.PENDING


def test_fixed_disposition_requires_no_special_handling_of_requested_start_time():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a FIXED entry carries its requested start time."""
    from datetime import UTC, datetime

    entry = QueuedObservationPackage(
        id="qp1",
        observation_package_id="pkg1",
        target_id="M 81",
        start_time_mode=StartTimeMode.FIXED,
        requested_start_time=datetime(2026, 8, 10, 22, 30, tzinfo=UTC),
    )
    assert entry.requested_start_time is not None


def test_observation_session_round_trips_full_shape():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an ObservationSession constructs with placement/diagnostics."""
    from datetime import date

    session = ObservationSession(
        id="session1",
        night_date=date(2026, 8, 10),
        site_profile_id="site1",
        telescope_id="t1",
        camera_id="c1",
        queue=[
            QueuedObservationPackage(
                id="qp1",
                observation_package_id="pkg1",
                target_id="M 81",
                start_time_mode=StartTimeMode.SOONEST,
            )
        ],
        unplaced_package_diagnostics=[
            InfeasibilityReason(
                observation_package_id="pkg2",
                reason_code=InfeasibilityReasonCode.NEVER_CLEARS_ALTITUDE,
                detail="Target never rises above 30 degrees at this site.",
            )
        ],
    )
    assert session.status == SessionStatus.PLANNED
    assert len(session.queue) == 1
    assert len(session.unplaced_package_diagnostics) == 1
    assert session.divergence_records == []
    assert session.fault_records == []
    assert session.meridian_flips == []
    assert session.closed_at is None


def test_weather_sample_optional_fields_default_none():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify WeatherSample's readings default to absent, no populator yet."""
    sample = WeatherSample(time=1723334400.0)
    assert sample.ambient_temperature_c is None
    assert sample.humidity_percent is None
    assert sample.dew_point_c is None
