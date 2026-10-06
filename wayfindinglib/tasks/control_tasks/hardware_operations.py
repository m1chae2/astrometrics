"""Purpose: Commands and reads for the observatory hardware.

Description: The mount, filter wheel, focuser, cameras and enclosure
operations behind `control.mount`, `control.imaging`, `control.guiding`,
`control.safety` and `control.equipment`. Each function takes the shared
`ControlContext`, which supplies the device drivers, the configuration
and the delegation policy.

A command first checks that the capability it belongs to is
`AUTHORITATIVE` in the delegation policy, so this app never moves
hardware it has not been given. A read makes no such check.

The device drivers are `async`. These functions run each driver call to
completion with `asyncio.run`, because the `control` methods are plain
synchronous calls.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Coroutine
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import (
    AstrometricsError,
    ConfigurationError,
    ConflictError,
    HardwareError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from wayfindinglib.data_access.safety_policy_reader import get_safety_rule_set
from wayfindinglib.drivers.indi.pyindi_compatibility import INDI_ERRORS
from wayfindinglib.models.policy.delegation import ObservatoryCapability
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.observatorylib.site_location import configured_observer_location

if TYPE_CHECKING:
    from astrometricslib import Target
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState
    from wayfindinglib.models.policy.safety import SafetyAssessment
    from wayfindinglib.tasks.control_tasks.safety_monitor import SensorReadings

logger = logging.getLogger(__name__)

MOUNT_STATUS_SECTIONS = ("mount", "filter", "focuser", "camera")
"""Device reads `mount_status` can make. ``mount`` also brings the
ambient temperature and humidity, which the same session read refreshes."""


def _run_sync(coroutine: Coroutine[Any, Any, Any]) -> Any:
    """Run an async driver call to completion from synchronous code.

    Parameters
    ----------
    coroutine : `Coroutine`
        The driver call.

    Returns
    -------
    result : `Any`
        The call's result.
    """
    return asyncio.run(coroutine)


def _require_authoritative(context: ControlContext, capability: ObservatoryCapability) -> None:
    """Refuse a command unless `capability` is `AUTHORITATIVE`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the saved delegation policy.
    capability : `ObservatoryCapability`
        The capability the command belongs to.

    Raises
    ------
    PermissionDeniedError
        If `capability` is not currently `AUTHORITATIVE`.
    """
    if not context.delegation_policy().is_authoritative(capability):
        raise PermissionDeniedError(
            f"Command requires {capability.value} to be AUTHORITATIVE, but it is not. "
            "Promote this capability (or call control.safety.enter_controller_mode()) before "
            "commanding hardware."
        )


def mount_status(context: ControlContext, include: list[str] | None = None) -> dict[str, Any]:
    """Read the mount's position and tracking, and the imaging devices.

    A read, so there is no authority check.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.
    include : `list` [`str`], optional
        Device reads to make, from ``MOUNT_STATUS_SECTIONS``. All of them
        when `None`.

    Returns
    -------
    status : `dict` [`str`, `Any`]
        ``mount``: ``ra``, ``dec``, ``altitude``, ``azimuth``,
        ``trackingStatus``, ``connectionStatus``, ``targetName``,
        ``pierSide``, ``parked``, ``trackMode``, ``temperature``,
        ``humidity`` and ``cameraStatus``. ``filter``:
        ``filter``. ``focuser``: ``focuserPosition``. ``camera``:
        ``cameraTemperature``. An unknown section name is refused with
        `InvalidArgumentError`.
    """
    sections = check_sections(include, MOUNT_STATUS_SECTIONS)
    data: dict[str, Any] = {}
    if "mount" in sections:
        mount = _run_sync(context.mount_driver.get_status())
        raw_status = getattr(context.driver, "status", {})
        data.update({
            "ra": mount.ra,
            "dec": mount.dec,
            "altitude": mount.altitude,
            "azimuth": mount.azimuth,
            "temperature": raw_status.get("TEMPERATURE", "-"),
            "humidity": raw_status.get("HUMIDITY", "-"),
            "trackingStatus": mount.tracking_status,
            "connectionStatus": mount.connection_status,
            "cameraStatus": raw_status.get("CAMERA_STATUS", "Idle"),
            "targetName": mount.target_name,
            "pierSide": mount.pier_side,
            "parked": mount.parked,
            "trackMode": mount.track_mode,
        })
    if "filter" in sections:
        filter_name = _run_sync(context.filter_wheel_driver.get_current_filter())
        data["filter"] = filter_name if filter_name is not None else "L"
    if "focuser" in sections:
        data["focuserPosition"] = _run_sync(context.focuser_driver.get_position())
    if "camera" in sections:
        temperature_c = _run_sync(context.camera_driver.get_sensor_temperature_c())
        data["cameraTemperature"] = f"{temperature_c:.1f}°C" if temperature_c is not None else "-"
    return data


def check_sections(include: list[str] | None, known: tuple[str, ...]) -> list[str]:
    """Check a status `include` list against the known sections.

    Parameters
    ----------
    include : `list` [`str`] or `None`
        The sections asked for. `None` means all of them.
    known : `tuple` [`str`, ...]
        The sections the status read offers.

    Returns
    -------
    sections : `list` [`str`]
        The sections to read.

    Raises
    ------
    InvalidArgumentError
        If `include` names a section that is not in `known`.
    """
    if include is None:
        return list(known)
    unknown = [name for name in include if name not in known]
    if unknown:
        raise InvalidArgumentError(f"Unknown section(s) {unknown}. Choose from: {', '.join(known)}.")
    return list(include)


def resolve_destination(context: ControlContext, destination: str | Target | SkyPosition) -> SkyPosition:
    """Turn a slew destination into a sky position.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the target catalog.
    destination : `str`, `Target` or `SkyPosition`
        A library target, its id, or a position.

    Returns
    -------
    position : `SkyPosition`
        Where to point.

    Raises
    ------
    NotFoundError
        If a target id names no library target.
    InvalidArgumentError
        If the target has no coordinates yet, or they cannot be read.
    """
    if isinstance(destination, SkyPosition):
        return destination
    if isinstance(destination, dict):
        return SkyPosition.model_validate(destination)
    target = destination
    if isinstance(destination, str):
        target = context.astrometrics.targets.get(destination, refresh=True)
        if target is None:
            raise NotFoundError(f"Target '{destination}' not found in library")

    is_placeholder_ra = target.ra in (None, "", "None", "0h 0m 0s")
    is_placeholder_dec = target.dec in (None, "", "None", "0° 0′ 0′′", "0d 0m 0s", "0° 0′ 0″")
    if is_placeholder_ra and is_placeholder_dec:
        raise InvalidArgumentError(
            f"Target '{target.id}' has no coordinates yet — it hasn't been plate-solved."
        )

    from astrometricslib import parse_coordinate_string

    try:
        ra_deg = parse_coordinate_string(target.ra, is_ra=True)
        dec_deg = parse_coordinate_string(target.dec, is_ra=False)
    except InvalidArgumentError as error:
        raise InvalidArgumentError(
            f"Target '{target.id}' has invalid coordinate format: {error!s}"
        ) from error
    return SkyPosition(ra_deg=ra_deg % 360.0, dec_deg=dec_deg)


def slew(context: ControlContext, position: SkyPosition) -> bool:
    """Slew the mount to a sky position.

    Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    position : `SkyPosition`
        Where to point.

    Returns
    -------
    success : `bool`
        `True` if the slew command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.slew(position.ra_hours, position.dec_deg))


def sync_mount(context: ControlContext, position: SkyPosition) -> bool:
    """Tell the mount it is pointing at a plate-solved position.

    Gated on `PLATE_SOLVE_ALIGNMENT`, not `MOUNT_CONTROL`: a sync corrects
    the mount's own pointing model after a plate solve, which is the
    alignment capability's job.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    position : `SkyPosition`
        The solved position.

    Returns
    -------
    success : `bool`
        `True` if the sync command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.PLATE_SOLVE_ALIGNMENT)
    return _run_sync(context.mount_driver.sync(position.ra_hours, position.dec_deg))


def park(context: ControlContext) -> bool:
    """Park the mount. Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Returns
    -------
    success : `bool`
        `True` if the park command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.park())


def unpark(context: ControlContext) -> bool:
    """Unpark the mount. Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Returns
    -------
    success : `bool`
        `True` if the unpark command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.unpark())


def set_tracking(context: ControlContext, enabled: bool) -> bool:
    """Turn mount tracking on or off. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    enabled : `bool`
        `True` to track.

    Returns
    -------
    success : `bool`
        `True` if the tracking command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.set_tracking(enabled))


def manual_move(context: ControlContext, direction: str, start: bool = True) -> bool:
    """Start or stop a manual mount move. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    direction : `str`
        ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
    start : `bool`, optional
        `True` to start moving, `False` to stop.

    Returns
    -------
    success : `bool`
        `True` if the move command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.move(direction, start))


def abort_motion(context: ControlContext) -> bool:
    """Stop all mount motion now. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Returns
    -------
    success : `bool`
        `True` if the abort command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.abort_motion())


def set_slew_rate(context: ControlContext, rate_index: int) -> bool:
    """Choose the mount's slew rate. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    rate_index : `int`
        Index into the mount's list of slew rates.

    Returns
    -------
    success : `bool`
        `True` if the rate command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    return _run_sync(context.mount_driver.set_slew_rate(rate_index))


def set_filter(context: ControlContext, filter_name: str) -> bool:
    """Turn the filter wheel to a filter.

    Requires `CAPTURE_ORCHESTRATION`: changing filters is part of setting
    up a capture.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the filter wheel driver and the policy.
    filter_name : `str`
        The filter name, or a name the wheel can match to one.

    Returns
    -------
    success : `bool`
        `True` once the wheel reports the new filter.

    Raises
    ------
    NotFoundError
        If `filter_name` matches no filter on the wheel.
    HardwareError
        If the wheel fails to turn.
    """
    _require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)

    known_filters = _run_sync(context.filter_wheel_driver.get_names())
    resolved_name = _run_sync(context.filter_wheel_driver.resolve_name(filter_name))

    if known_filters and not resolved_name:
        raise NotFoundError(f"Filter '{filter_name}' not recognized. Available: {known_filters}")

    final_name = resolved_name if resolved_name else filter_name
    success = _run_sync(context.filter_wheel_driver.set_position(final_name))
    if not success:
        raise HardwareError(f"Filter change to '{final_name}' failed at hardware level")
    return True


def focus_move(context: ControlContext, steps: int) -> bool:
    """Move the focuser by a number of steps. Requires `AUTOFOCUS`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the focuser driver and the policy.
    steps : `int`
        Steps to move; the sign gives the direction.

    Returns
    -------
    success : `bool`
        `True` if the move command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.AUTOFOCUS)
    return _run_sync(context.focuser_driver.move_relative(steps))


def filter_names(context: ControlContext) -> list[str]:
    """List the filter wheel's slot names. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the filter wheel driver.

    Returns
    -------
    filter_names : `list` [`str`]
        The name of each slot, in order.
    """
    return _run_sync(context.filter_wheel_driver.get_names())


def focuser_position(context: ControlContext) -> int:
    """Read the focuser's position. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the focuser driver.

    Returns
    -------
    position : `int`
        The focuser position in steps.
    """
    return _run_sync(context.focuser_driver.get_position())


def pulse(context: ControlContext, direction: str, duration_ms: float) -> bool:
    """Send one guide pulse to the mount.

    Gated on `AUTOGUIDING`, not `MOUNT_CONTROL`: whoever does the guiding
    owns the pulses, whoever owns slewing and parking.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    direction : `str`
        ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
    duration_ms : `float`
        Pulse length in milliseconds.

    Returns
    -------
    success : `bool`
        `True` if the pulse command was accepted.
    """
    _require_authoritative(context, ObservatoryCapability.AUTOGUIDING)
    return _run_sync(context.mount_driver.pulse_guide(direction, duration_ms))


def capture_image(context: ControlContext, exposure_seconds: float) -> Any:
    """Take one exposure with the main camera.

    Requires `CAPTURE_ORCHESTRATION`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the camera driver and the policy.
    exposure_seconds : `float`
        Exposure length in seconds.

    Returns
    -------
    result : `Any`
        The camera driver's exposure result.
    """
    _require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    return _run_sync(context.camera_driver.expose(exposure_seconds))


def guide_expose(context: ControlContext, exposure_seconds: float, gain: float | None = None) -> Any:
    """Take one exposure with the guide camera.

    Requires `AUTOGUIDING`, like a guide pulse, because a guide exposure
    only matters as part of guiding.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guide camera driver and the policy.
    exposure_seconds : `float`
        Exposure length in seconds.
    gain : `float`, optional
        Camera gain to set first. `None` leaves it alone.

    Returns
    -------
    result : `Any`
        The guide camera driver's exposure result.
    """
    _require_authoritative(context, ObservatoryCapability.AUTOGUIDING)
    return _run_sync(context.guide_camera_driver.expose(exposure_seconds, gain=gain))


def guide_image(context: ControlContext) -> Any:
    """Return the guide camera's last image. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guide camera driver.

    Returns
    -------
    image : `Any`
        The last image data the guide camera sent.
    """
    return _run_sync(context.guide_camera_driver.get_last_image())


def drain_external_pulses(context: ControlContext) -> list[dict[str, Any]]:
    """Return and clear the guide pulses another program sent.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the INDI session.

    Returns
    -------
    pulses : `list` [`dict`]
        Guide pulses seen since the last call, in the form
        `IndiInterface.drain_external_pulses` documents.
    """
    return context.driver.drain_external_pulses()


def connect(context: ControlContext) -> bool:
    """Connect every device driver.

    The mount driver's `connect` opens the shared INDI session; the other
    drivers share that session, so their own `connect` only reports.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.

    Returns
    -------
    success : `bool`
        Always `True`.
    """
    _run_sync(context.mount_driver.connect())
    _run_sync(context.focuser_driver.connect())
    _run_sync(context.filter_wheel_driver.connect())
    _run_sync(context.camera_driver.connect())
    _run_sync(context.guide_camera_driver.connect())
    _run_sync(context.enclosure_driver.connect())
    return True


def disconnect(context: ControlContext) -> bool:
    """Disconnect every device driver.

    The safe alternative to unplugging powered hardware. Only the mount
    driver closes the shared session; the others do nothing.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.

    Returns
    -------
    success : `bool`
        Always `True`.
    """
    _run_sync(context.mount_driver.disconnect())
    _run_sync(context.focuser_driver.disconnect())
    _run_sync(context.filter_wheel_driver.disconnect())
    _run_sync(context.camera_driver.disconnect())
    _run_sync(context.guide_camera_driver.disconnect())
    _run_sync(context.enclosure_driver.disconnect())
    return True


def enclosure_state(context: ControlContext) -> EnclosureState:
    """Read the enclosure's motion state. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the enclosure driver.

    Returns
    -------
    state : `EnclosureState`
        The current state (`UNKNOWN` if the driver cannot tell).
    """
    return _run_sync(context.enclosure_driver.get_state())


def open_enclosure(context: ControlContext) -> bool:
    """Open the enclosure. Requires `OBSERVATORY_SAFETY`.

    Opening implies no mount motion, so there is no position check. The
    matching rule (the mount may leave park only when the enclosure is
    open) is checked when the mount is commanded.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the enclosure driver and the policy.

    Returns
    -------
    success : `bool`
        Whether the open command was issued and confirmed.
    """
    _require_authoritative(context, ObservatoryCapability.OBSERVATORY_SAFETY)
    return _run_sync(context.enclosure_driver.open())


def close_enclosure(context: ControlContext) -> bool:
    """Close the enclosure, refusing if the mount is not clear of it.

    Requires `OBSERVATORY_SAFETY`, then checks that the mount is parked
    within the enclosure's clearance tolerance. Closing on a mount outside
    that envelope is the damage this check prevents.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers, the enclosure record and the policy.

    Returns
    -------
    success : `bool`
        Whether the close command was issued and confirmed.

    Raises
    ------
    ConfigurationError
        If no `Enclosure` is configured.
    HardwareError
        If the mount position is not known.
    ConflictError
        If the mount is outside the clearance envelope of the park position.
    """
    from wayfindinglib.tasks.control_tasks.enclosure_control import can_close_enclosure

    _require_authoritative(context, ObservatoryCapability.OBSERVATORY_SAFETY)

    enclosure = context.active_enclosure()
    if enclosure is None:
        raise ConfigurationError("Cannot close enclosure: no Enclosure is configured")

    mount = _run_sync(context.mount_driver.get_status())
    mount_altitude_deg = _parse_dms_degrees(mount.altitude)
    mount_azimuth_deg = _parse_dms_degrees(mount.azimuth)
    if mount_altitude_deg is None or mount_azimuth_deg is None:
        raise HardwareError("Close refused: mount position is not currently known")
    if not can_close_enclosure(enclosure, mount_altitude_deg, mount_azimuth_deg):
        raise ConflictError(
            "Close refused: mount is not within the configured clearance envelope of the park position"
        )
    return _run_sync(context.enclosure_driver.close())


def _parse_dms_degrees(dms_string: str) -> float | None:
    """Read a degrees-minutes-seconds display string as decimal degrees.

    Parameters
    ----------
    dms_string : `str`
        A display value such as ``"45° 12′ 30.0″"``.

    Returns
    -------
    degrees : `float` or `None`
        The angle in degrees, or `None` if the string holds no three
        numbers (for example ``"Unknown"``).
    """
    numbers = re.findall(r"-?\d+(?:\.\d+)?", dms_string or "")
    if len(numbers) < 3:
        return None
    degrees, minutes, seconds = (float(number) for number in numbers[:3])
    sign = -1.0 if dms_string.strip().startswith("-") else 1.0
    return sign * (abs(degrees) + minutes / 60.0 + seconds / 3600.0)


def observer_location(context: ControlContext) -> dict[str, float] | None:
    """Return the observatory's location, from the mount or the configuration.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the configuration.

    Returns
    -------
    location : `dict` [`str`, `float`] or `None`
        ``latitude``, ``longitude`` and ``elevation``, or `None` if
        neither the mount nor the configuration gives one.
    """
    try:
        mount_location = _run_sync(context.mount_driver.get_observer_location())
    except (AstrometricsError, OSError, *INDI_ERRORS) as mount_error:
        logger.info("Mount did not report an observer location (%s); trying the configuration.", mount_error)
        mount_location = None
    if mount_location:
        return mount_location
    return configured_observer_location(context.config)


def assess_safety(
    context: ControlContext, readings: SensorReadings, now: datetime | None = None
) -> SafetyAssessment:
    """Judge sensor readings against the saved safety rules.

    The context's `SafetyMonitor` keeps the time of the last unsafe
    reading between calls, which is how a verdict waits before it turns
    safe again.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the saved rules and the safety monitor.
    readings : `SensorReadings`
        The weather readings.
    now : `datetime`, optional
        The time of the readings. Now when omitted.

    Returns
    -------
    assessment : `SafetyAssessment`
        The verdict, the rule that produced it, and how fresh it is.
    """
    rule_set = get_safety_rule_set(context.butler)
    return context.safety_monitor.evaluate(rule_set, readings, now or datetime.now(UTC))


def refresh_safety_assessment(context: ControlContext) -> SafetyAssessment:
    """Read the weather driver and judge the readings. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the weather driver, the rules and the safety monitor.

    Returns
    -------
    assessment : `SafetyAssessment`
        The verdict for a fresh reading.
    """
    readings = _run_sync(context.weather_driver.get_readings())
    return assess_safety(context, readings)
