"""Purpose: Unit tests for cooling_control.

Description: Verifies the ramp respects the configured rate (and the
sensor's own maximum), reports settled only within tolerance, and
flags a settle timeout -- the cases
`Wayfinding_Library_Architecture.md` §2.5.11 calls out.
"""

import pytest

from wayfindinglib.models.equipment_and_site.equipment import Camera, CoolingPolicy
from wayfindinglib.tasks.control_tasks.cooling_control import (
    compute_ramped_setpoint,
    effective_ramp_rate_c_per_min,
    has_settle_timed_out,
    is_settled,
)


def _camera(**overrides) -> Camera:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "id": "cam-1",
        "name": "Test Camera",
        "pixel_size_um": 3.76,
        "sensor_width_px": 4000,
        "sensor_height_px": 3000,
        "max_cooling_ramp_c_per_min": 2.0,
    }
    defaults.update(overrides)
    return Camera(**defaults)


def test_ramped_setpoint_moves_no_faster_than_configured_rate():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify one minute at 2 C/min moves the setpoint by exactly 2 C."""
    setpoint = compute_ramped_setpoint(
        current_setpoint_c=10.0, target_temp_c=-10.0, ramp_c_per_min=2.0, elapsed_sec=60.0
    )
    assert setpoint == pytest.approx(8.0)


def test_ramped_setpoint_does_not_overshoot_target():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a step past the remaining distance clamps at the target."""
    setpoint = compute_ramped_setpoint(
        current_setpoint_c=-9.5, target_temp_c=-10.0, ramp_c_per_min=2.0, elapsed_sec=60.0
    )
    assert setpoint == pytest.approx(-10.0)


def test_ramped_setpoint_handles_warm_up_direction():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the same function ramps upward when the target is warmer."""
    setpoint = compute_ramped_setpoint(
        current_setpoint_c=-10.0, target_temp_c=20.0, ramp_c_per_min=2.0, elapsed_sec=60.0
    )
    assert setpoint == pytest.approx(-8.0)


def test_effective_ramp_rate_caps_at_camera_maximum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the sensor's own maximum ramp overrides a faster policy rate."""
    policy = CoolingPolicy(ramp_c_per_min=5.0)
    camera = _camera(max_cooling_ramp_c_per_min=2.0)
    assert effective_ramp_rate_c_per_min(policy, camera) == pytest.approx(2.0)


def test_effective_ramp_rate_uses_policy_when_no_camera_given():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the policy rate is used unmodified when no camera is given."""
    policy = CoolingPolicy(ramp_c_per_min=1.5)
    assert effective_ramp_rate_c_per_min(policy, None) == pytest.approx(1.5)


def test_is_settled_within_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a reading within tolerance of the target is settled."""
    assert is_settled(current_temp_c=-9.7, target_temp_c=-10.0, settle_tolerance_c=0.5) is True


def test_is_settled_outside_tolerance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a reading outside tolerance of the target is not settled."""
    assert is_settled(current_temp_c=-8.0, target_temp_c=-10.0, settle_tolerance_c=0.5) is False


def test_settle_timeout_flagged_after_bound_exceeded():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify elapsed time past settle_timeout_sec is flagged as timed out."""
    assert has_settle_timed_out(elapsed_sec=901.0, settle_timeout_sec=900) is True


def test_settle_timeout_not_flagged_within_bound():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify elapsed time within settle_timeout_sec is not flagged."""
    assert has_settle_timed_out(elapsed_sec=899.0, settle_timeout_sec=900) is False
