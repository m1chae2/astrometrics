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

from astropy.time import Time
from pydantic import ValidationError

from astrometricslib import SkyPosition
from astrometricslib.exceptions import (
    AstrometricsError,
    ConfigurationError,
    ConflictError,
    HardwareError,
    InvalidArgumentError,
    NotFoundError,
    PermissionDeniedError,
)
from wayfindinglib.analytics.frame_counting import normalize_filter
from wayfindinglib.astronomy.coordinate_transforms import current_epoch_to_icrs, icrs_to_current_epoch
from wayfindinglib.data_access.safety_policy_reader import get_safety_rule_set
from wayfindinglib.data_access.site_profile_reader import configured_observer_location
from wayfindinglib.drivers.indi.pyindi_compatibility import INDI_ERRORS
from wayfindinglib.models.control_status import MountControlStatus
from wayfindinglib.models.policy.delegation import ObservatoryCapability

if TYPE_CHECKING:
    from astrometricslib import StellarObject, Target
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.equipment_and_site.enclosure import EnclosureState
    from wayfindinglib.models.policy.safety import SafetyAssessment, SensorReadings

logger = logging.getLogger(__name__)

PULSE_DIRECTIONS = {
    "north": "N",
    "south": "S",
    "east": "E",
    "west": "W",
    "n": "N",
    "s": "S",
    "e": "E",
    "w": "W",
}
"""Guide pulse directions as callers name them, to the driver's initial."""


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


def require_authoritative(context: ControlContext, capability: ObservatoryCapability) -> None:
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
            "Promote this capability (or call control.safety.set_mode('controller')) before "
            "commanding hardware."
        )


def _require_accepted(accepted: object, command: str) -> None:
    """Refuse a device command that the driver did not accept.

    Parameters
    ----------
    accepted : `object`
        What the driver returned; a false value means it refused.
    command : `str`
        What was asked for, such as ``"park"``, for the message.

    Raises
    ------
    HardwareError
        If `accepted` is false.
    """
    if not accepted:
        raise HardwareError(f"The device did not accept the {command} command.")


def mount_status(context: ControlContext) -> MountControlStatus:
    """Read the mount's position and tracking. A read.

    There is no authority check for a read. The ambient temperature and
    humidity come with it, because the same session read refreshes them.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.

    Returns
    -------
    status : `MountControlStatus`
        The mount's reading.

    Notes
    -----
    ``ra_deg`` and ``dec_deg`` are ICRS (J2000), the frame of catalog
    stars, library targets and plate solutions, so they can be compared
    with them directly. The mount itself reports the current-epoch frame
    (JNow); `mount_position_to_icrs` converts it. A position or angle the
    mount does not know yet (such as ``"Unknown"``) is `None`.
    """
    mount = _run_sync(context.mount_driver.get_status())
    raw_status = getattr(context.driver, "status", {})
    ra_deg, dec_deg = mount_position_to_icrs(mount.ra, mount.dec, Time.now())
    return MountControlStatus(
        ra_deg=ra_deg,
        dec_deg=dec_deg,
        altitude_deg=_parse_mount_coordinate(mount.altitude),
        azimuth_deg=_parse_mount_coordinate(mount.azimuth),
        temperature=raw_status.get("TEMPERATURE", "-"),
        humidity=raw_status.get("HUMIDITY", "-"),
        tracking_status=mount.tracking_status,
        connection_status=mount.connection_status,
        target_name=mount.target_name,
        pier_side=mount.pier_side,
        parked=mount.parked,
        track_mode=mount.track_mode,
    )


def current_filter(context: ControlContext) -> str:
    """Read the filter in the beam now. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the filter wheel driver.

    Returns
    -------
    filter_name : `str`
        The filter's name; ``"L"`` if the wheel does not say.
    """
    filter_name = _run_sync(context.filter_wheel_driver.get_current_filter())
    return filter_name if filter_name is not None else "L"


def camera_reading(context: ControlContext) -> tuple[str, str]:
    """Read the main camera's sensor temperature and activity. A read.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the camera driver and the session.

    Returns
    -------
    temperature, camera_status : `tuple` [`str`, `str`]
        The sensor temperature as text such as ``"-10.0°C"`` (``"-"`` if
        unknown), and what the camera is doing, such as ``"Idle"``.
    """
    temperature_c = _run_sync(context.camera_driver.get_sensor_temperature_c())
    raw_status = getattr(context.driver, "status", {})
    temperature = f"{temperature_c:.1f}°C" if temperature_c is not None else "-"
    return temperature, raw_status.get("CAMERA_STATUS", "Idle")


def mount_position_to_icrs(ra_text: str, dec_text: str, obstime: Time) -> tuple[float | None, float | None]:
    """Convert the position a mount reports from JNow to ICRS (J2000) degrees.

    Mount drivers report the current-epoch frame (JNow: the true equator
    and equinox of the date). Catalog stars, library targets and plate
    solutions use ICRS, which differs by the precession since 2000 (about
    0.36 degrees in 2026). Converting here lets everything downstream
    compare the mount with the catalog in one frame.

    Parameters
    ----------
    ra_text : `str`
        The reported Right Ascension in hours, as a plain number or as
        hours, minutes and seconds (``"5° 35′ 17.3″"`` or ``"5:35:17.3"``).
    dec_text : `str`
        The reported Declination in degrees, in the same forms.
    obstime : `Time`
        The time of the reading, which defines the current epoch.

    Returns
    -------
    ra_deg, dec_deg : `tuple` [`float` or `None`, `float` or `None`]
        The ICRS position in degrees. If either input cannot be read (for
        example ``"Unknown"``), both are `None`.
    """
    ra_hours = _parse_mount_coordinate(ra_text)
    dec_deg = _parse_mount_coordinate(dec_text)
    if ra_hours is None or dec_deg is None:
        return None, None
    icrs_ra_deg, icrs_dec_deg = current_epoch_to_icrs((ra_hours * 15.0) % 360.0, dec_deg, obstime)
    return float(icrs_ra_deg) % 360.0, float(icrs_dec_deg)


def _mount_frame_position(position: SkyPosition) -> tuple[float, float]:
    """Convert an ICRS (J2000) position to the frame mount drivers take.

    Parameters
    ----------
    position : `SkyPosition`
        An ICRS position, as catalogs, targets and plate solutions give it.

    Returns
    -------
    ra_hours, dec_deg : `tuple` [`float`, `float`]
        The current-epoch (JNow) position: Right Ascension in hours and
        Declination in degrees.
    """
    current_ra_deg, current_dec_deg = icrs_to_current_epoch(position.ra_deg, position.dec_deg, Time.now())
    return current_ra_deg / 15.0, current_dec_deg


def _parse_mount_coordinate(text: str | None) -> float | None:
    """Read a mount coordinate given as a plain number or as sexagesimal text.

    Parameters
    ----------
    text : `str` or `None`
        Such as ``"5.5"``, ``"5:30:00"`` or ``"5° 30′ 0.0″"``.

    Returns
    -------
    value : `float` or `None`
        The value in the text's own unit (hours or degrees), or `None` if
        it holds no number (for example ``"Unknown"`` or ``"-"``).
    """
    stripped = (text or "").strip()
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", stripped):
        return float(stripped)
    return _parse_dms_degrees(stripped)


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


def sky_position_from(value: SkyPosition | dict[str, Any]) -> SkyPosition:
    """Turn ``{"ra_deg": ..., "dec_deg": ...}`` into a `SkyPosition`.

    Parameters
    ----------
    value : `SkyPosition` or `dict`
        A position, or a dictionary with its fields.

    Returns
    -------
    position : `SkyPosition`
        The position.

    Raises
    ------
    InvalidArgumentError
        If the dictionary is not a valid position.
    """
    if isinstance(value, SkyPosition):
        return value
    try:
        return SkyPosition.model_validate(value)
    except ValidationError as error:
        raise InvalidArgumentError(
            f'{value!r} is not a sky position: use {{"ra_deg": ..., "dec_deg": ...}}. {error}'
        ) from error


def resolve_destination(
    context: ControlContext, destination: str | Target | StellarObject | SkyPosition | dict[str, Any]
) -> SkyPosition:
    """Turn a slew destination into a sky position.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the target catalog.
    destination : `Target`, `StellarObject`, `SkyPosition`, `str` or `dict`
        A library target, a star, a position, a target id (library code
        only) or ``{"ra_deg": ..., "dec_deg": ...}``.

    Returns
    -------
    position : `SkyPosition`
        Where to point.

    Raises
    ------
    NotFoundError
        If a target id names no library target.
    InvalidArgumentError
        If the target or star has no coordinates yet, or they cannot be
        read.
    """
    from astrometricslib import StellarObject

    if isinstance(destination, SkyPosition):
        return destination
    if isinstance(destination, dict):
        return sky_position_from(destination)
    if isinstance(destination, StellarObject):
        if destination.right_ascension is None or destination.declination is None:
            raise InvalidArgumentError(f"Star '{destination.id}' has no sky position yet.")
        return SkyPosition(
            ra_deg=float(destination.right_ascension) % 360.0, dec_deg=float(destination.declination)
        )
    target = destination
    if isinstance(destination, str):
        target = context.astrometrics.targets.get(destination)
        if target is None:
            raise NotFoundError(f"Target '{destination}' not found in library")

    is_placeholder_ra = target.ra in (None, "", "None", "0h 0m 0s")
    is_placeholder_dec = target.dec in (None, "", "None", "0° 0′ 0′′", "0d 0m 0s", "0° 0′ 0″")
    if is_placeholder_ra and is_placeholder_dec:
        raise InvalidArgumentError(
            f"Target '{target.id}' has no coordinates yet — it hasn't been plate-solved."
        )

    try:
        ra_deg = target.ra_deg
        dec_deg = target.dec_deg
    except InvalidArgumentError as error:
        raise InvalidArgumentError(
            f"Target '{target.id}' has invalid coordinate format: {error!s}"
        ) from error
    return SkyPosition(ra_deg=ra_deg % 360.0, dec_deg=dec_deg)


def slew(context: ControlContext, position: SkyPosition) -> None:
    """Slew the mount to a sky position.

    Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`. If this app has a
    capture run going, it is stopped and its exposure aborted first (see
    `stop_own_capture`).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    position : `SkyPosition`
        Where to point, in ICRS (J2000). It is converted to the mount's
        current-epoch frame (JNow) before it is sent.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the slew.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    stop_own_capture(context)
    _require_accepted(_run_sync(context.mount_driver.slew(*_mount_frame_position(position))), "slew")


def sync_mount(context: ControlContext, position: SkyPosition) -> None:
    """Tell the mount it is pointing at a plate-solved position.

    Gated on `PLATE_SOLVE_ALIGNMENT`, not `MOUNT_CONTROL`: a sync corrects
    the mount's own pointing model after a plate solve, which is the
    alignment capability's job.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    position : `SkyPosition`
        The solved position, in ICRS (J2000). It is converted to the
        mount's current-epoch frame (JNow) before it is sent.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the sync.
    """
    require_authoritative(context, ObservatoryCapability.PLATE_SOLVE_ALIGNMENT)
    _require_accepted(_run_sync(context.mount_driver.sync(*_mount_frame_position(position))), "sync")


def park(context: ControlContext) -> None:
    """Park the mount. Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`.

    If this app has a capture run going, it is stopped and its exposure
    aborted first (see `stop_own_capture`).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the park command.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    stop_own_capture(context)
    _require_accepted(_run_sync(context.mount_driver.park()), "park")


def unpark(context: ControlContext) -> None:
    """Unpark the mount. Requires `MOUNT_CONTROL` to be `AUTHORITATIVE`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the unpark command.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    _require_accepted(_run_sync(context.mount_driver.unpark()), "unpark")


def set_tracking(context: ControlContext, on: bool) -> None:
    """Turn mount tracking on or off. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    on : `bool`
        `True` to track.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the tracking
    command.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    _require_accepted(_run_sync(context.mount_driver.set_tracking(on)), "tracking")


def manual_move(context: ControlContext, direction: str, start: bool = True) -> None:
    """Start or stop a manual mount move. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    direction : `str`
        ``"north"``, ``"south"``, ``"east"`` or ``"west"``.
    start : `bool`, optional
        `True` to start moving, `False` to stop.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the move command.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    _require_accepted(_run_sync(context.mount_driver.move(direction, start)), "move")


def stop_mount(context: ControlContext) -> None:
    """Stop all mount motion and any centering run. Needs `MOUNT_CONTROL`.

    The centering loop (``control.mount.slew(center=True)``) stops before
    its next step. Monitor mode refuses a stop too (user decision of
    2026-10-24, issue #95, decision U4), so the check comes before anything
    else, and a refused stop changes nothing.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.

    Notes
    -----
    Raises `PermissionDeniedError` unless `MOUNT_CONTROL` is
    `AUTHORITATIVE`. Raises `HardwareError` if the mount does not accept
    the abort command.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    context.motion_stop.set()
    _require_accepted(_run_sync(context.mount_driver.abort_motion()), "stop")


def set_slew_rate(context: ControlContext, rate: int) -> None:
    """Choose the mount's slew rate. Requires `MOUNT_CONTROL`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the mount driver and the policy.
    rate : `int`
        Index into the mount's list of slew rates.

    Notes
    -----
    Raises `HardwareError` if the mount does not accept the rate.
    """
    require_authoritative(context, ObservatoryCapability.MOUNT_CONTROL)
    _require_accepted(_run_sync(context.mount_driver.set_slew_rate(rate)), "slew rate")


def set_filter(context: ControlContext, filter_name: str) -> None:
    """Turn the filter wheel to a filter.

    Requires `CAPTURE_ORCHESTRATION`: changing filters is part of setting
    up a capture.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the filter wheel driver and the policy.
    filter_name : `str`
        The filter name, or a name the wheel can match to one.

    Raises
    ------
    NotFoundError
        If `filter_name` matches no filter on the wheel.
    HardwareError
        If the wheel fails to turn.
    """
    require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)

    known_filters = _run_sync(context.filter_wheel_driver.get_names())
    resolved_name = _run_sync(context.filter_wheel_driver.resolve_name(filter_name))
    if known_filters and not resolved_name:
        resolved_name = _equivalent_filter_name(filter_name, known_filters)

    if known_filters and not resolved_name:
        raise NotFoundError(f"Filter '{filter_name}' not recognized. Available: {known_filters}")

    final_name = resolved_name if resolved_name else filter_name
    success = _run_sync(context.filter_wheel_driver.set_position(final_name))
    if not success:
        raise HardwareError(f"Filter change to '{final_name}' failed at hardware level")


def _equivalent_filter_name(filter_name: str, known_filters: list[str]) -> str | None:
    """Find the wheel slot that names the same filter in another spelling.

    The wheel driver matches a short name to a long one ("L" to
    "Luminance"). This covers the other direction ("Luminance" to a slot
    called "L"), using the library's one rule for filter spellings.

    Parameters
    ----------
    filter_name : `str`
        The requested filter.
    known_filters : `list` [`str`]
        The names the wheel reports.

    Returns
    -------
    name : `str` or `None`
        The wheel's own name for the filter, or `None` if no slot matches.
    """
    wanted = normalize_filter(filter_name)
    for known_filter in known_filters:
        if known_filter and normalize_filter(known_filter) == wanted:
            return known_filter
    return None


def move_focuser(context: ControlContext, steps: int) -> None:
    """Move the focuser by a number of steps. Requires `AUTOFOCUS`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the focuser driver and the policy.
    steps : `int`
        Steps to move; the sign gives the direction.

    Notes
    -----
    Raises `HardwareError` if the focuser does not accept the move.
    """
    require_authoritative(context, ObservatoryCapability.AUTOFOCUS)
    _require_accepted(_run_sync(context.focuser_driver.move_relative(steps)), "focuser move")


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
        ``"north"``, ``"south"``, ``"east"`` or ``"west"`` (or the
        initial, ``"N"``, ``"S"``, ``"E"``, ``"W"``). Sent to the mount
        driver as the initial.
    duration_ms : `float`
        Pulse length in milliseconds.

    Returns
    -------
    success : `bool`
        `True` if the pulse command was accepted.

    Raises
    ------
    InvalidArgumentError
        If `direction` is not one of the four directions.
    """
    require_authoritative(context, ObservatoryCapability.AUTOGUIDING)
    axis = PULSE_DIRECTIONS.get(direction.strip().lower())
    if axis is None:
        raise InvalidArgumentError(
            f"Unknown guide direction {direction!r}. Use north, south, east or west.",
            details={"direction": direction},
        )
    return _run_sync(context.mount_driver.pulse_guide(axis, duration_ms))


def capture_image(context: ControlContext, exposure_s: float) -> Any:
    """Take one exposure with the main camera.

    Requires `CAPTURE_ORCHESTRATION`.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the camera driver and the policy.
    exposure_s : `float`
        Exposure length in seconds.

    Returns
    -------
    result : `Any`
        The camera driver's exposure result.
    """
    require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    return _run_sync(context.camera_driver.expose(exposure_s))


def abort_exposure(context: ControlContext) -> bool:
    """Cancel the exposure the main camera is taking.

    No authority check: callers use it only to end a run this app
    started (`stop_own_capture`, and a run that sees its own stop flag).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the camera driver.

    Returns
    -------
    success : `bool`
        `True` if the abort command was sent.
    """
    return _run_sync(context.camera_driver.abort_exposure())


def stop_own_capture(context: ControlContext) -> bool:
    """Stop the capture run this app started, and abort its exposure.

    The run is asked to stop first, then the camera is told to abort. If
    this app has no run going, nothing is sent to the camera: a run started
    by another program (Ekos, for example) is never touched. A camera that
    cannot abort is logged, not raised, so a slew or park that called this
    still goes ahead; the run has already been told to stop and takes no
    more frames.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the list of runs and the camera driver.

    Returns
    -------
    stopped : `bool`
        `True` if a run was going and was asked to stop, `False` if none
        was going.
    """
    if not context.capture_runs.request_stop():
        return False
    try:
        abort_exposure(context)
    except Exception:  # a failed abort must not block the move that called this
        logger.warning("The camera did not accept the abort for the stopped capture run.", exc_info=True)
    return True


def stop_capture(context: ControlContext) -> None:
    """Stop the capture run this app started. Requires `CAPTURE_ORCHESTRATION`.

    Monitor mode refuses a stop too, whether or not a run is going (the
    same rule as `stop_mount`, decision of 2026-10-24, issue #95), so the
    check comes before anything else. When no run is going nothing is sent
    to the camera.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the list of runs, the camera driver and the policy.

    Notes
    -----
    Raises `PermissionDeniedError` unless `CAPTURE_ORCHESTRATION` is
    `AUTHORITATIVE`.
    """
    require_authoritative(context, ObservatoryCapability.CAPTURE_ORCHESTRATION)
    stop_own_capture(context)


def guide_expose(context: ControlContext, exposure_s: float, gain: float | None = None) -> Any:
    """Take one exposure with the guide camera.

    Requires `AUTOGUIDING`, like a guide pulse, because a guide exposure
    only matters as part of guiding.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guide camera driver and the policy.
    exposure_s : `float`
        Exposure length in seconds.
    gain : `float`, optional
        Camera gain to set first. `None` leaves it alone.

    Returns
    -------
    result : `Any`
        The guide camera driver's exposure result.
    """
    require_authoritative(context, ObservatoryCapability.AUTOGUIDING)
    return _run_sync(context.guide_camera_driver.expose(exposure_s, gain=gain))


def capture_guide_frame(context: ControlContext, exposure_s: float, gain: float | None = None) -> None:
    """Take one guide camera exposure as a device action (`AUTOGUIDING`).

    Parameters
    ----------
    context : `ControlContext`
        Supplies the guide camera driver and the policy.
    exposure_s : `float`
        Exposure length in seconds.
    gain : `float`, optional
        Camera gain to set first. `None` leaves it alone.

    Notes
    -----
    Raises `HardwareError` if the guide camera does not start the exposure.
    """
    _require_accepted(guide_expose(context, exposure_s, gain=gain), "guide exposure")


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


def connect(context: ControlContext) -> None:
    """Connect every device driver.

    The mount driver's `connect` opens the shared INDI session; the other
    drivers share that session, so their own `connect` only reports. Each
    driver connects on a best-effort basis.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers.
    """
    _run_sync(context.mount_driver.connect())
    _run_sync(context.focuser_driver.connect())
    _run_sync(context.filter_wheel_driver.connect())
    _run_sync(context.camera_driver.connect())
    _run_sync(context.guide_camera_driver.connect())
    _run_sync(context.enclosure_driver.connect())


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
    require_authoritative(context, ObservatoryCapability.OBSERVATORY_SAFETY)
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

    require_authoritative(context, ObservatoryCapability.OBSERVATORY_SAFETY)

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


def set_device_property(
    context: ControlContext,
    device_name: str,
    property_name: str,
    value: Any = "On",
    element: str | None = None,
) -> None:
    """Set one raw INDI property element on a device, for diagnosis.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the INDI session.
    device_name : `str`
        The INDI device.
    property_name : `str`
        The property to change.
    value : `Any`, optional
        The new value. ``"On"`` turns a switch element on.
    element : `str`, optional
        The element of the property to change.

    Raises
    ------
    ConfigurationError
        If the active mount does not use INDI. Its ``report`` is
        ``"status"``.
    NotFoundError
        If the device, the property or the element was not found.
    """
    diagnostics = context.indi_diagnostics
    if diagnostics is None:
        raise ConfigurationError(
            "The active mount does not use INDI, so it has no INDI properties.", report="status"
        )
    if not diagnostics.set_property(device_name, property_name, value, element):
        raise NotFoundError(f"No element {element!r} of {property_name!r} on INDI device {device_name!r}.")
