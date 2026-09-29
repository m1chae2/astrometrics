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

import asyncio
from typing import Any

from wayfindinglib.models.policy.delegation import ObservatoryCapability


def _run_sync(coroutine: Any) -> Any:
    """Run an async driver coroutine to completion from synchronous code.

    `ObservatoryControl`'s public methods stay synchronous for now,
    since several backend services (`GuidingService`, `TelescopeService`,
    ...) call them directly and are not yet async-aware, while the new
    protocol driver ABCs are `async def` throughout
    (`Wayfinding_Library_Architecture.md` §2.5.1a's concurrency
    decision). This bridges the two until those callers migrate.

    Returns
    -------
    result : `Any`
        The coroutine's result.
    """
    return asyncio.run(coroutine)


def _require_authoritative(manager, capability: ObservatoryCapability) -> None:  # ruff: ignore[missing-type-function-argument]
    """Raise unless `capability` is currently `AUTHORITATIVE`.

    Centralizes the authority check that used to live as a single
    global "Safe Mode" flag inside `IndiInterface`
    (`IndiInterface._should_block_command`), with no awareness of which
    capability was actually being commanded. Checked here, once, against
    the real `DelegationPolicy` before dispatching to any driver.

    Raises
    ------
    AstrometryHardwareError
        If `capability` is not currently `AUTHORITATIVE`.
    """
    from wayfindinglib import AstrometryHardwareError

    if not manager.delegation_policy().is_authoritative(capability):
        raise AstrometryHardwareError(
            f"Command requires {capability.value} to be AUTHORITATIVE, but it is not. "
            "Promote this capability (or call enter_controller_mode()) before commanding hardware."
        )


def get_telescope_status(manager) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Return the current mount coordinates, tracking, and telemetry status.

    Reassembled from four separate driver calls instead of one
    monolithic `IndiInterface.get_status()` (§4's "one interface per
    device type" migration): mount fields from `mount_driver`, filter
    from `filter_wheel_driver`, focuser position from `focuser_driver`,
    main-camera temperature from `camera_driver`. A read, not a
    command -- no authority check.

    ``temperature``/``humidity`` (ambient, from the powerbox) and
    ``cameraStatus`` have no driver ABC yet (a `WeatherDriver` is M12;
    no `CameraDriver` method reports exposure-status text) -- they are
    read from the shared session's status dict directly, which the
    `mount_driver.get_status()` call above already refreshes as a side
    effect (`IndiInterface.get_status()` internally calls
    `get_environmentals()`/`_refresh_camera_status()` before returning).

    Returns
    -------
    status : `dict`
        The telescope status fields, with ``"guidingHistory"`` added
        when a guiding service is available.
    """
    mount_status = _run_sync(manager.mount_driver.get_status())
    filter_name = _run_sync(manager.filter_wheel_driver.get_current_filter())
    focuser_position = _run_sync(manager.focuser_driver.get_position())
    camera_temperature_c = _run_sync(manager.camera_driver.get_sensor_temperature_c())

    raw_status = getattr(manager.driver, "status", {})
    data = {
        "ra": mount_status.ra,
        "dec": mount_status.dec,
        "altitude": mount_status.altitude,
        "azimuth": mount_status.azimuth,
        "temperature": raw_status.get("TEMPERATURE", "-"),
        "humidity": raw_status.get("HUMIDITY", "-"),
        "trackingStatus": mount_status.tracking_status,
        "connectionStatus": mount_status.connection_status,
        "focuserPosition": focuser_position,
        "filter": filter_name if filter_name is not None else "L",
        "guidingHistory": [],
        "cameraTemperature": (f"{camera_temperature_c:.1f}°C" if camera_temperature_c is not None else "-"),
        "cameraStatus": raw_status.get("CAMERA_STATUS", "Idle"),
        "targetName": mount_status.target_name,
    }

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

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.slew(ra, dec))


def sync_coordinates(manager, ra: float, dec: float) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Sync the mount's internal coordinates to a plate-solved position.

    Closes a verified gap: `IndiInterface.sync_coordinates` existed but
    was never exposed above the driver layer, so `AlignmentService`
    bypassed `ObservatoryControl` to reach it directly.

    Gated on `PLATE_SOLVE_ALIGNMENT` rather than `MOUNT_CONTROL`: a sync
    recalibrates the mount's own coordinate model after a plate solve,
    which is the alignment capability's job, not raw mount motion.

    Returns
    -------
    success : `bool`
        `True` if the sync command was accepted.

    Requires `PLATE_SOLVE_ALIGNMENT` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.PLATE_SOLVE_ALIGNMENT)
    return _run_sync(manager.mount_driver.sync(ra, dec))


def park(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Park the telescope mount.

    Returns
    -------
    success : `bool`
        `True` if the park command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.park())


def unpark(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Unpark the telescope mount.

    Returns
    -------
    success : `bool`
        `True` if the unpark command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.unpark())


def set_tracking(manager, enabled: bool) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Enable or disable mount tracking.

    Returns
    -------
    success : `bool`
        `True` if the tracking command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.set_tracking(enabled))


def set_filter(manager, filter_name: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Slew the filter wheel to a designated filter.

    Requires `CAPTURE_ORCHESTRATION` to be currently `AUTHORITATIVE` --
    a filter change is part of setting up a capture, not a
    correction-loop action.

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

    _require_authoritative(manager, ObservatoryCapability.CAPTURE_ORCHESTRATION)

    known_filters = _run_sync(manager.filter_wheel_driver.get_names())
    resolved_name = _run_sync(manager.filter_wheel_driver.resolve_name(filter_name))

    if known_filters and not resolved_name:
        raise ValueError(f"Filter '{filter_name}' not recognized. Available: {known_filters}")

    final_name = resolved_name if resolved_name else filter_name
    success = _run_sync(manager.filter_wheel_driver.set_position(final_name))
    if not success:
        raise AstrometryHardwareError(f"Filter change to '{final_name}' failed at hardware level")
    return True


def manual_move(manager, direction: str, start: bool = True) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Drives manual motor movement in a specific direction.

    Returns
    -------
    success : `bool`
        `True` if the move command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.move(direction, start))


def abort_motion(manager) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Abort all telescope mount motion immediately.

    Returns
    -------
    success : `bool`
        `True` if the abort motion command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.abort_motion())


def set_slew_rate(manager, rate_index: int) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Set mount slew rate index.

    Returns
    -------
    success : `bool`
        `True` if the slew rate command was accepted.

    Requires `MOUNT_CONTROL` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(manager.mount_driver.set_slew_rate(rate_index))


def focus_move(manager, steps: int) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Move the focuser motor by a designated number of steps.

    Requires `AUTOFOCUS` to be currently `AUTHORITATIVE`.

    Returns
    -------
    success : `bool`
        `True` if the focuser move command was accepted.
    """
    _require_authoritative(manager, ObservatoryCapability.AUTOFOCUS)
    return _run_sync(manager.focuser_driver.move_relative(steps))


def get_filter_names(manager) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List the configured filter wheel slot names.

    A read, not a command -- no authority check.

    Returns
    -------
    filter_names : `list` [`str`]
        Names of the filters configured on the active filter wheel.
    """
    return _run_sync(manager.filter_wheel_driver.get_names())


def pulse_guide(manager, direction: str, duration_ms: float) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Send a pulse guide command to the telescope mount.

    Gated on `AUTOGUIDING` rather than `MOUNT_CONTROL`: an ST4 pulse is
    physically a mount action, but authority over issuing it belongs to
    whichever system is doing the guiding correction, independent of
    who has raw slew/park/tracking authority over the mount.

    Returns
    -------
    success : `bool`
        `True` if the pulse guide command was accepted.

    Requires `AUTOGUIDING` to be currently `AUTHORITATIVE`.
    """
    _require_authoritative(manager, ObservatoryCapability.AUTOGUIDING)
    return _run_sync(manager.mount_driver.pulse_guide(direction, duration_ms))


def capture_image(manager, exposure_seconds: float):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Take an exposure with the main camera.

    Closes a verified gap: `IndiInterface.capture_image` existed but
    was never exposed above the driver layer, so `ImagingService`
    bypassed `ObservatoryControl` to reach it directly.

    Requires `CAPTURE_ORCHESTRATION` to be currently `AUTHORITATIVE`.

    Returns
    -------
    result
        The exposure result from the main camera driver.
    """
    _require_authoritative(manager, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    return _run_sync(manager.camera_driver.expose(exposure_seconds))


def guide_expose(manager, exposure_seconds: float, gain: float | None = None):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Take an exposure with the guide camera.

    Requires `AUTOGUIDING` to be currently `AUTHORITATIVE` -- the same
    capability as `pulse_guide`, since a guide exposure only matters as
    part of the guiding correction loop.

    Returns
    -------
    result
        The exposure result from the guide camera controller.
    """
    _require_authoritative(manager, ObservatoryCapability.AUTOGUIDING)
    return _run_sync(manager.guide_camera_driver.expose(exposure_seconds, gain=gain))


def get_guide_image(manager):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Retrieve the last image blob from the guide camera.

    A read, not a command -- no authority check.

    Returns
    -------
    image
        The last guide camera image blob.
    """
    return _run_sync(manager.guide_camera_driver.get_last_image())


def drain_external_pulses(manager) -> list[dict]:  # ruff: ignore[missing-type-function-argument]
    """Return and clear guide pulses issued by an external commander.

    Returns
    -------
    pulses : `list` [`dict`]
        Guide pulses detected since the last drain, in the shape
        documented by `IndiInterface.drain_external_pulses`.
    """
    return manager.driver.drain_external_pulses()


def get_focuser_position(manager) -> int:  # ruff: ignore[missing-type-function-argument]
    """Get current focuser absolute position.

    A read, not a command -- no authority check.

    Returns
    -------
    position : `int`
        The absolute focuser position.
    """
    return _run_sync(manager.focuser_driver.get_position())


def connect(observatory) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Connect every configured hardware driver.

    Loops over each per-device-type driver property instead of a
    single shared session call, per §4's "one interface per device
    type" migration. `IndiMountDriver.connect()` performs the real
    session handshake (`IndiInterface.connect_to_telescope`, which
    calls `_ensure_connection` internally); the other drivers share
    that same underlying session, so their own `.connect()` calls are
    informational once the mount driver has connected.

    Returns
    -------
    success : `bool`
        Always `True`; the connection is established or reused
        lazily, matching each driver's own best-effort semantics.
    """
    _run_sync(observatory.mount_driver.connect())
    _run_sync(observatory.focuser_driver.connect())
    _run_sync(observatory.filter_wheel_driver.connect())
    _run_sync(observatory.camera_driver.connect())
    _run_sync(observatory.guide_camera_driver.connect())
    return True


def disconnect(observatory) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Disconnect every configured hardware driver.

    The safe alternative to physically unplugging powered hardware
    (`Wayfinding_Library_Commissioning_Plan.md` §6.2) -- a subsequent
    `connect` re-establishes the connection through the same
    `_ensure_connection` reconnection path any other transient
    disconnection would recover through. Only `IndiMountDriver.disconnect()`
    performs the real teardown; the other drivers share the same
    session and no-op instead of disconnecting it a second time.

    Returns
    -------
    success : `bool`
        Always `True`; the underlying `disconnectServer` call is
        best-effort against a connection that may already be down.
    """
    _run_sync(observatory.mount_driver.disconnect())
    _run_sync(observatory.focuser_driver.disconnect())
    _run_sync(observatory.filter_wheel_driver.disconnect())
    _run_sync(observatory.camera_driver.disconnect())
    _run_sync(observatory.guide_camera_driver.disconnect())
    return True


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
    return _run_sync(manager.mount_driver.get_observer_location())
