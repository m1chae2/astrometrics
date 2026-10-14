"""Purpose: Unit tests for device summary state domain models.

Description: Verifies DeviceState round-trips and that the five summary
states plus every device role are distinct.
"""

from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState, DeviceSummaryState


def test_device_state_round_trips_with_fault_detail() -> None:
    """Verify a faulted DeviceState carries its detail through construction."""
    state = DeviceState(
        device_id="mount1",
        device_role=DeviceRole.MOUNT,
        summary_state=DeviceSummaryState.FAULT,
        fault_detail="ALERT property state on TELESCOPE_MOTION",
    )
    assert state.summary_state == DeviceSummaryState.FAULT
    assert state.fault_detail is not None


def test_device_state_fault_detail_defaults_to_none() -> None:
    """Verify fault_detail defaults to None for a non-faulted device."""
    state = DeviceState(
        device_id="mount1", device_role=DeviceRole.MOUNT, summary_state=DeviceSummaryState.ENABLED
    )
    assert state.fault_detail is None


def test_all_device_roles_distinct() -> None:
    """Verify every device role, including ENCLOSURE and WEATHER, is unique."""
    roles = {
        DeviceRole.MOUNT,
        DeviceRole.PRIMARY_CAMERA,
        DeviceRole.GUIDE_CAMERA,
        DeviceRole.FILTER_WHEEL,
        DeviceRole.FOCUSER,
        DeviceRole.ENCLOSURE,
        DeviceRole.WEATHER,
    }
    assert len(roles) == 7


def test_all_summary_states_distinct() -> None:
    """Verify all five uniform summary states are distinct."""
    states = {
        DeviceSummaryState.OFFLINE,
        DeviceSummaryState.STANDBY,
        DeviceSummaryState.DISABLED,
        DeviceSummaryState.ENABLED,
        DeviceSummaryState.FAULT,
    }
    assert len(states) == 5
