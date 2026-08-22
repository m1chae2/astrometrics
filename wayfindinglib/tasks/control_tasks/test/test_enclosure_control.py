"""Purpose: Unit tests for the enclosure/mount motion interlock.

Description: Verifies enclosure closure is refused when the mount is
outside clearance, mount motion is refused for every non-OPEN
enclosure state including UNKNOWN, and a motion timeout yields FAULT
-- the cases `Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

from datetime import UTC, datetime, timedelta

import pytest

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureState, EnclosureType
from wayfindinglib.tasks.control_tasks.enclosure_control import (
    can_close_enclosure,
    can_leave_park,
    check_motion_timeout,
)

_NOW = datetime(2026, 8, 5, 4, 0, 0, tzinfo=UTC)


def _enclosure(**overrides) -> Enclosure:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": "enc-1",
        "enclosure_type": EnclosureType.ROLL_OFF_ROOF,
        "park_azimuth_deg": 180.0,
        "park_altitude_deg": 0.0,
        "clearance_tolerance_deg": 2.0,
        "motion_timeout_sec": 120,
    }
    defaults.update(overrides)
    return Enclosure(**defaults)


def test_close_permitted_when_mount_within_clearance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify closure is permitted when the mount sits at the park position."""
    assert can_close_enclosure(_enclosure(), mount_altitude_deg=0.5, mount_azimuth_deg=181.0) is True


def test_close_refused_when_mount_outside_altitude_clearance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify closure is refused when altitude is outside clearance."""
    assert can_close_enclosure(_enclosure(), mount_altitude_deg=10.0, mount_azimuth_deg=180.0) is False


def test_close_refused_when_mount_outside_azimuth_clearance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify closure is refused when azimuth is outside clearance."""
    assert can_close_enclosure(_enclosure(), mount_altitude_deg=0.0, mount_azimuth_deg=90.0) is False


def test_close_permitted_across_azimuth_wraparound():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify azimuth clearance is checked via shortest circular distance."""
    enclosure = _enclosure(park_azimuth_deg=359.0, clearance_tolerance_deg=3.0)
    assert can_close_enclosure(enclosure, mount_altitude_deg=0.0, mount_azimuth_deg=1.0) is True


@pytest.mark.parametrize(
    "state",
    [
        EnclosureState.CLOSED,
        EnclosureState.CLOSING,
        EnclosureState.OPENING,
        EnclosureState.FAULT,
        EnclosureState.UNKNOWN,
    ],
)
def test_mount_motion_refused_for_every_non_open_state(state):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify mount motion is refused for every non-OPEN state."""
    assert can_leave_park(state) is False


def test_mount_motion_permitted_when_open():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify mount motion is permitted when the enclosure is OPEN."""
    assert can_leave_park(EnclosureState.OPEN) is True


def test_motion_timeout_yields_fault():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify motion held past motion_timeout_sec resolves to FAULT."""
    enclosure = _enclosure(motion_timeout_sec=60)
    started_at = _NOW
    now = started_at + timedelta(seconds=61)
    result = check_motion_timeout(started_at, EnclosureState.OPENING, now, enclosure)
    assert result == EnclosureState.FAULT


def test_motion_within_timeout_stays_in_progress():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify motion still within motion_timeout_sec is unchanged."""
    enclosure = _enclosure(motion_timeout_sec=60)
    started_at = _NOW
    now = started_at + timedelta(seconds=30)
    result = check_motion_timeout(started_at, EnclosureState.CLOSING, now, enclosure)
    assert result == EnclosureState.CLOSING


def test_timeout_check_does_not_affect_settled_states():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a non-transitional state is returned unchanged over time."""
    enclosure = _enclosure(motion_timeout_sec=60)
    started_at = _NOW
    now = started_at + timedelta(hours=1)
    result = check_motion_timeout(started_at, EnclosureState.OPEN, now, enclosure)
    assert result == EnclosureState.OPEN
