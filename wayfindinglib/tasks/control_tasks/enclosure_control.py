"""Purpose: Enclosure/Mount Motion Interlock.

Description: Validates enclosure and mount motion against each other
before commanding either, per `Wayfinding_Library_Architecture.md`
§2.5.7. The interlock is mutual and geometric: the enclosure may close
only when the mount sits within `clearance_tolerance_deg` of the
configured park position, and the mount may leave park only when the
enclosure is `OPEN`. `UNKNOWN` enclosure state blocks mount motion,
following the same fail-closed posture as the safety verdict
(§2.5.6, "Unknown Is Unsafe" applied here to enclosure state). Every
function here is a pure check over already-known state -- exercisable
with no hardware or Execution package present (§2.5.9, "Safety Runs
Without Execution").
"""

from datetime import datetime

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureState


def _azimuth_distance_deg(first_deg: float, second_deg: float) -> float:
    """Return the shortest circular distance between two azimuths.

    Returns
    -------
    distance_deg : `float`
        The distance, in degrees, accounting for wraparound.
    """
    raw_distance = abs(first_deg - second_deg) % 360.0
    return min(raw_distance, 360.0 - raw_distance)


def mount_within_clearance(enclosure: Enclosure, mount_altitude_deg: float, mount_azimuth_deg: float) -> bool:
    """Return whether the mount sits within the enclosure's clearance envelope.

    Both altitude and azimuth must be within `clearance_tolerance_deg`
    of the configured park position.

    Returns
    -------
    within_clearance : `bool`
        `True` if both axes are within tolerance of the park position.
    """
    altitude_within = (
        abs(mount_altitude_deg - enclosure.park_altitude_deg) <= enclosure.clearance_tolerance_deg
    )
    azimuth_within = (
        _azimuth_distance_deg(mount_azimuth_deg, enclosure.park_azimuth_deg)
        <= enclosure.clearance_tolerance_deg
    )
    return altitude_within and azimuth_within


def can_close_enclosure(enclosure: Enclosure, mount_altitude_deg: float, mount_azimuth_deg: float) -> bool:
    """Return whether the enclosure may be commanded to close.

    Forcing closure with the mount outside clearance is the damage
    case this interlock exists to prevent, so the check runs
    unconditionally regardless of the enclosure's current state.

    Returns
    -------
    may_close : `bool`
        `True` if the mount is within the clearance envelope.
    """
    return mount_within_clearance(enclosure, mount_altitude_deg, mount_azimuth_deg)


def can_leave_park(enclosure_state: EnclosureState) -> bool:
    """Return whether the mount may be commanded to unpark or slew.

    Only an enclosure state of `OPEN` permits mount motion.
    `UNKNOWN` blocks motion identically to `CLOSED`/`FAULT` -- there is
    no configuration under which an unread enclosure state permits it.

    Returns
    -------
    may_leave_park : `bool`
        `True` only if `enclosure_state` is `OPEN`.
    """
    return enclosure_state == EnclosureState.OPEN


def check_motion_timeout(
    motion_started_at: datetime, current_state: EnclosureState, now: datetime, enclosure: Enclosure
) -> EnclosureState:
    """Return `FAULT` if enclosure motion has exceeded its configured timeout.

    Parameters
    ----------
    motion_started_at : `datetime`
        When the `OPENING`/`CLOSING` command was issued.
    current_state : `EnclosureState`
        The enclosure's last-reported state.
    now : `datetime`
        The current time.
    enclosure : `Enclosure`
        Supplies `motion_timeout_sec`.

    Returns
    -------
    state : `EnclosureState`
        `FAULT` if still `OPENING` or `CLOSING` past
        `motion_timeout_sec`, so a stuck motor never reports an
        indefinite in-progress state; `current_state` unchanged
        otherwise.
    """
    if current_state not in (EnclosureState.OPENING, EnclosureState.CLOSING):
        return current_state
    elapsed_sec = (now - motion_started_at).total_seconds()
    if elapsed_sec > enclosure.motion_timeout_sec:
        return EnclosureState.FAULT
    return current_state
