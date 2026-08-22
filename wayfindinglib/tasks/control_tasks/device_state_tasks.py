"""Purpose: Device Summary State Mapping.

Description: Maps each device's raw presence/connection/alert signals
onto the five-state `DeviceSummaryState` vocabulary, per
`Wayfinding_Library_Architecture.md` §2.5.2: `OFFLINE` where not
present on the server, `STANDBY` where present but not connected,
`DISABLED` where connected with commands withheld (the existing
`allow_commands` configuration), `ENABLED` where connected and
commandable, `FAULT` where the driver reports an alert property state
or a command failed in a way requiring attention.

Takes already-read raw signals rather than a live driver, so the
mapping is exercisable with no hardware or Execution package present
(§2.5.9, "Safety Runs Without Execution"). Gathering those signals per
device is a separate, thin step: `IndiInterface` currently exposes
device finders for `MOUNT`/`FOCUSER`/`FILTER_WHEEL`/`GUIDE_CAMERA`
only -- `PRIMARY_CAMERA`, `ENCLOSURE`, and `WEATHER` summary state
requires the new `drivers/indi/enclosure_controller.py` and
`weather_controller.py` this milestone's Table 6 calls for, which are
not yet built, so gathering those roles' signals is deferred rather
than stubbed against nothing to verify it against.
"""

from wayfindinglib.models.policy.device_state import DeviceRole, DeviceState, DeviceSummaryState


def classify_device_state(
    is_present: bool, is_connected: bool, allow_commands: bool, has_alert: bool
) -> DeviceSummaryState:
    """Classify one device's raw signals into the summary-state vocabulary.

    `has_alert` is checked first: a connected device reporting an
    alert is a `FAULT` regardless of `allow_commands`, since a fault
    is a condition on the device itself, not a policy choice about
    whether to command it.

    Returns
    -------
    state : `DeviceSummaryState`
        `OFFLINE` if absent, `FAULT` if present, connected, and
        alerting, `STANDBY` if present but not connected, `DISABLED`
        if connected but commands are withheld, `ENABLED` otherwise.
    """
    if not is_present:
        return DeviceSummaryState.OFFLINE
    if not is_connected:
        return DeviceSummaryState.STANDBY
    if has_alert:
        return DeviceSummaryState.FAULT
    if not allow_commands:
        return DeviceSummaryState.DISABLED
    return DeviceSummaryState.ENABLED


def summarize_device(
    device_id: str,
    device_role: DeviceRole,
    is_present: bool,
    is_connected: bool,
    allow_commands: bool,
    has_alert: bool,
    fault_detail: str | None = None,
) -> DeviceState:
    """Build a `DeviceState` from one device's raw signals.

    Returns
    -------
    device_state : `DeviceState`
        The classified summary state, with `fault_detail` carried
        through only when the classification is `FAULT`.
    """
    summary_state = classify_device_state(is_present, is_connected, allow_commands, has_alert)
    return DeviceState(
        device_id=device_id,
        device_role=device_role,
        summary_state=summary_state,
        fault_detail=fault_detail if summary_state == DeviceSummaryState.FAULT else None,
    )


def find_alert_detail(properties: dict) -> str | None:
    """Scan a device's properties for an alerting element.

    Parameters
    ----------
    properties : `dict`
        The shape returned by `IndiInterface.get_device_properties`:
        property name -> `{"elements": {element_name: state_str, ...}, ...}`.

    Returns
    -------
    detail : `str` or `None`
        ``"{property_name}: {element_name}"`` for the first element
        reporting an ``"Alert"`` state, or `None` if none do.
    """
    for property_name, property_info in properties.items():
        elements = property_info.get("elements", {})
        for element_name, element_state in elements.items():
            if element_state == "Alert":
                return f"{property_name}: {element_name}"
    return None
