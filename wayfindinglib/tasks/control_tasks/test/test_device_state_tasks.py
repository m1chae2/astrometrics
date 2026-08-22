"""Purpose: Unit tests for device_state_tasks.

Description: Verifies every device maps to exactly one
`DeviceSummaryState` and an alert property produces `FAULT` with
detail -- the cases `Wayfinding_Library_Architecture.md` §2.5.11
calls out.
"""

from wayfindinglib.models.policy.device_state import DeviceRole, DeviceSummaryState
from wayfindinglib.tasks.control_tasks.device_state_tasks import (
    classify_device_state,
    find_alert_detail,
    summarize_device,
)


def test_absent_device_is_offline():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a device not present on the server is OFFLINE."""
    state = classify_device_state(is_present=False, is_connected=False, allow_commands=True, has_alert=False)
    assert state == DeviceSummaryState.OFFLINE


def test_present_but_disconnected_device_is_standby():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a present, unconnected device is STANDBY."""
    state = classify_device_state(is_present=True, is_connected=False, allow_commands=True, has_alert=False)
    assert state == DeviceSummaryState.STANDBY


def test_connected_with_commands_withheld_is_disabled():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a connected device with allow_commands False is DISABLED."""
    state = classify_device_state(is_present=True, is_connected=True, allow_commands=False, has_alert=False)
    assert state == DeviceSummaryState.DISABLED


def test_connected_and_commandable_is_enabled():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a connected, commandable device is ENABLED."""
    state = classify_device_state(is_present=True, is_connected=True, allow_commands=True, has_alert=False)
    assert state == DeviceSummaryState.ENABLED


def test_alerting_device_is_fault_regardless_of_allow_commands():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an alerting device is FAULT even when commands are allowed."""
    state = classify_device_state(is_present=True, is_connected=True, allow_commands=True, has_alert=True)
    assert state == DeviceSummaryState.FAULT


def test_offline_device_takes_precedence_over_alert_flag():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify absence is checked first: absent means OFFLINE, not FAULT."""
    state = classify_device_state(is_present=False, is_connected=False, allow_commands=True, has_alert=True)
    assert state == DeviceSummaryState.OFFLINE


def test_summarize_device_carries_fault_detail_only_when_faulted():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify fault_detail is dropped unless the classification is FAULT."""
    enabled = summarize_device(
        "mount-1", DeviceRole.MOUNT, True, True, True, has_alert=False, fault_detail="stale detail"
    )
    assert enabled.summary_state == DeviceSummaryState.ENABLED
    assert enabled.fault_detail is None

    faulted = summarize_device(
        "mount-1", DeviceRole.MOUNT, True, True, True, has_alert=True, fault_detail="slew failed"
    )
    assert faulted.summary_state == DeviceSummaryState.FAULT
    assert faulted.fault_detail == "slew failed"


def test_find_alert_detail_locates_alerting_element():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify find_alert_detail names the first alerting property/element."""
    properties = {
        "CONNECTION": {"elements": {"CONNECT": "On", "DISCONNECT": "Off"}},
        "FOCUS_ABORT_MOTION": {"elements": {"ABORT": "Off"}},
        "MOUNT_SAFETY": {"elements": {"HORIZON_LIMIT": "Alert"}},
    }
    assert find_alert_detail(properties) == "MOUNT_SAFETY: HORIZON_LIMIT"


def test_find_alert_detail_returns_none_when_no_alert():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify find_alert_detail returns None when nothing is alerting."""
    properties = {"CONNECTION": {"elements": {"CONNECT": "On"}}}
    assert find_alert_detail(properties) is None
