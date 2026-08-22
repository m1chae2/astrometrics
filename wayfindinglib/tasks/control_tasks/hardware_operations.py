"""Purpose: INDI Hardware Operations.

Description: Telescope connection, manual slewing motor controls,
camera filter wheels, and focuser hardware operations over the INDI
driver layer. Relocated verbatim from the deprecated
`observatorylib.hardware_operations` into Observatory Control
(`Wayfinding_Library_Architecture.md` §2.5.1, §2.5.2: "Hardware
operations are carried forward from the earlier `observatorylib/`
implementation; relocation into `tasks/control_tasks/` is mechanical").
Callers pass a manager-like object exposing `.driver`/`._config`
(`ObservatoryControl` in `api/control_registry.py`), matching the
duck-typed shape the deprecated `ObservatoryManager` already used.
"""

from typing import Any


def get_telescope_status(manager) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Return the current mount coordinates, tracking, and telemetry status.

    Returns
    -------
    status : `dict`
        The telescope status fields, with ``"guidingHistory"`` added
        when a guiding service is available.
    """
    status_model = manager.driver.get_status()
    if hasattr(status_model, "model_dump"):
        data = status_model.model_dump(by_alias=True)
    else:
        data = status_model.dict(by_alias=True)

    guiding_service = getattr(manager, "_guiding_service", None)
    if guiding_service:
        guiding_service.poll_external_telemetry()
        guiding_status = guiding_service.get_status()
        data["guidingHistory"] = guiding_status.get("history", [])

    return data


def slew_to_target(manager, target_name: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Resolve target from library coordinates and drive mount to slew.

    Returns
    -------
    success : `bool`
        `True` if the slew command was accepted.

    Raises
    ------
    ValueError
        If the target is not found in the library, has no
        coordinates yet, or has an invalid coordinate format.
    """
    from astrometricslib import Astrometrics

    astrometrics = Astrometrics(manager._config)
    target = astrometrics.targets.get(target_name)
    if not target:
        raise ValueError(f"Target '{target_name}' not found in library")

    is_placeholder_ra = target.ra in (None, "", "None", "0h 0m 0s")
    is_placeholder_dec = target.dec in (None, "", "None", "0° 0′ 0′′", "0d 0m 0s", "0° 0′ 0″")
    if is_placeholder_ra and is_placeholder_dec:
        raise ValueError(f"Target '{target_name}' has no coordinates yet — it hasn't been plate-solved.")

    from astrometricslib import parse_coordinate_string

    try:
        ra_deg = parse_coordinate_string(target.ra, is_ra=True)
        dec = parse_coordinate_string(target.dec, is_ra=False)
    except Exception as e:
        raise ValueError(f"Target '{target_name}' has invalid coordinate format: {e!s}") from e

    ra = ra_deg / 15.0  # degrees -> hours, matching manager.slew_to_coordinates' expected units

    return manager.slew_to_coordinates(ra, dec)


def slew_to_coordinates(manager, ra: float, dec: float) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Command the telescope mount to slew to coordinates.

    Returns
    -------
    success : `bool`
        `True` if the slew command was accepted.
    """
    return manager.driver.slew(ra, dec)


def park(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Park the telescope mount.

    Returns
    -------
    success : `bool`
        `True` if the park command was accepted.
    """
    return manager.driver.park()


def unpark(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Unpark the telescope mount.

    Returns
    -------
    success : `bool`
        `True` if the unpark command was accepted.
    """
    return manager.driver.unpark()


def set_tracking(manager, enabled: bool) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Enable or disable mount tracking.

    Returns
    -------
    success : `bool`
        `True` if the tracking command was accepted.
    """
    return manager.driver.set_tracking(enabled)


def set_filter(manager, filter_name: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Slew the filter wheel to a designated filter.

    Returns
    -------
    success : `bool`
        `True` if the filter change succeeded.

    Raises
    ------
    ValueError
        If ``filter_name`` does not match any known filter.
    AstrometryHardwareError
        If the hardware fails to change to the resolved filter.
    """
    from wayfindinglib import AstrometryHardwareError

    known_filters = manager.driver.get_filter_names()
    resolved_name = manager.driver.resolve_filter_name(filter_name)

    if known_filters and not resolved_name:
        raise ValueError(f"Filter '{filter_name}' not recognized. Available: {known_filters}")

    final_name = resolved_name if resolved_name else filter_name
    success = manager.driver.set_filterwheel_position(final_name)
    if not success:
        raise AstrometryHardwareError(f"Filter change to '{final_name}' failed at hardware level")
    return True


def get_indi_devices(manager) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List connected INDI device names.

    Returns
    -------
    device_names : `list` [`str`]
        Names of the currently connected INDI devices.
    """
    drv = manager.driver
    if not drv.isServerConnected():
        drv.connect_to_server()
    if hasattr(drv, "deviceMap") and drv.deviceMap:
        return list(drv.deviceMap.keys())
    devices = drv.getDevices()
    if devices:
        return [d.getDeviceName() for d in devices]
    return []


def manual_move(manager, direction: str, start: bool = True) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Drives manual motor movement in a specific direction.

    Returns
    -------
    success : `bool`
        `True` if the move command was accepted.
    """
    return manager.driver.move(direction, start)


def abort_motion(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Abort all telescope mount motion immediately.

    Returns
    -------
    success : `bool`
        `True` if the abort motion command was accepted.
    """
    return manager.driver.abort_motion()


def set_slew_rate(manager, rate_index: int) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Set mount slew rate index.

    Returns
    -------
    success : `bool`
        `True` if the slew rate command was accepted.
    """
    return manager.driver.set_slew_rate(rate_index)


def focus_move(manager, steps: int) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Move the focuser motor by a designated number of steps.

    Returns
    -------
    success : `bool`
        `True` if the focuser move command was accepted.
    """
    return manager.driver.focus_move(steps)


def get_filter_names(manager) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List the configured filter wheel slot names.

    Returns
    -------
    filter_names : `list` [`str`]
        Names of the filters configured on the active filter wheel.
    """
    return manager.driver.get_filter_names()


def pulse_guide(manager, direction: str, duration_ms: float) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Send a pulse guide command to the telescope mount.

    Returns
    -------
    success : `bool`
        `True` if the pulse guide command was accepted.
    """
    return manager.driver.pulse_guide(direction, duration_ms)


def guide_expose(manager, exposure_seconds: float, gain: float | None = None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Take an exposure with the guide camera.

    Returns
    -------
    result
        The exposure result from the guide camera controller.
    """
    return manager.driver.guide_expose(exposure_seconds, gain=gain)


def get_guide_image(manager):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Retrieve the last image blob from the guide camera.

    Returns
    -------
    image
        The last guide camera image blob.
    """
    return manager.driver.get_guide_image()


def get_focuser_position(manager) -> int:  # ruff: ignore[missing-type-function-argument]
    """Get current focuser absolute position.

    Returns
    -------
    position : `int`
        The absolute focuser position.
    """
    return manager.driver.get_focuser_position()


def connect(observatory) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Commands telescope hardware connection.

    Returns
    -------
    success : `bool`
        Always `True`; the connection is established or reused
        lazily via `_ensure_connection`.
    """
    observatory._driver._ensure_connection()
    return True


def disconnect(observatory) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Command a real, driver-level disconnection from the INDI server.

    The safe alternative to physically unplugging powered hardware
    (`Wayfinding_Library_Commissioning_Plan.md` §6.2) -- a subsequent
    `connect` re-establishes the connection through the same
    `_ensure_connection` reconnection path any other transient
    disconnection would recover through.

    Returns
    -------
    success : `bool`
        Always `True`; the underlying `disconnectServer` call is
        best-effort against a connection that may already be down.
    """
    observatory._driver.disconnectServer()
    return True


def indi_properties(observatory, device_name: str) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """List all registered properties for an INDI device.

    Returns
    -------
    properties : `dict`
        Mapping of property name to its details for the device.
    """
    observatory._driver._ensure_connection()
    return observatory._driver.get_device_properties(device_name)


def set_indi_property(
    observatory,  # ruff: ignore[missing-type-function-argument]
    device_name: str,
    property_name: str,
    value: Any,
    element: str | None = None,
) -> bool:
    """Modify a property element on an INDI device.

    Returns
    -------
    success : `bool`
        `True` if the property element was updated.
    """
    observatory._driver._ensure_connection()
    return observatory._driver.set_property(device_name, property_name, element, value)


def sync(observatory, target_name: str) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Start a remote sync task for target frames.

    Returns
    -------
    sync_status : `dict`
        Status information for the newly started sync task.

    Raises
    ------
    RuntimeError
        If no sync service is configured (standalone mode).
    """
    if not observatory._sync_service:
        raise RuntimeError("Sync service is not available in standalone mode.")
    return observatory._sync_service.start_sync(target_name)


def is_syncing(observatory, target_name: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Query active sync status for a target.

    Returns
    -------
    is_syncing : `bool`
        `True` if a sync task for the target is in progress.

    Raises
    ------
    RuntimeError
        If no sync service is configured (standalone mode).
    """
    if not observatory._sync_service:
        raise RuntimeError("Sync service is not available in standalone mode.")
    return observatory._sync_service.is_syncing(target_name)


def get_observer_location(manager) -> dict[str, float] | None:  # ruff: ignore[missing-type-function-argument]
    """Retrieve observer latitude, longitude, and elevation from the telescope.

    Returns
    -------
    location : `dict[str, float]` or `None`
        Geographical location dictionary with ``"latitude"``,
        ``"longitude"``, and ``"elevation"`` keys, or `None` if the
        telescope does not report a location.
    """
    coords = manager.driver.get_observer_location()
    if coords:
        return {"latitude": coords[0], "longitude": coords[1], "elevation": coords[2]}
    return None
