"""Purpose: Find when the mount moved to a new target during recorded nights.

Description: The Ekos analyze log records the mount's state changes
(``Slewing``, ``Tracking`` and so on) and, now and then, where the mount
pointed (altitude, azimuth and declination). The stored
`EkosSessionContext` keeps both. This module reads them and answers one
question for `control.history.query(kind="target_changes")`: when did the
mount finish a slew and start tracking at a new place in the sky, and
which library target is that?

The steps:

1. A slew ends when a ``Tracking`` state follows a ``Slewing`` state.
2. The pointing at that moment is the mount position closest in time to
   the start of tracking, found no later than `POSITION_WINDOW_SECONDS`
   after it. The log gives altitude and azimuth, so right ascension and
   declination come from the observer's location and the time.
3. A slew that ends within `NEW_POSITION_DEGREES` of the last reported
   change is a re-slew to the same place (a meridian flip, a centering
   step) and is not listed again.
4. The nearest library target inside `MATCH_RADIUS_DEGREES` is the match.
   With none that close, the change has no target.

Nothing here writes. The Ekos record is not changed. A change whose
position cannot be worked out is still listed, with a note that says why.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import astropy.units as u
from astropy.coordinates import AltAz, SkyCoord
from astropy.time import Time

from astrometricslib import InvalidArgumentError, parse_coordinate_string
from wayfindinglib.astronomy.coordinate_transforms import angular_separation_arcsec, earth_location
from wayfindinglib.models.session.night_list import TargetChange

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext
    from wayfindinglib.models.session.ekos_session import EkosSessionContext

logger = logging.getLogger(__name__)

__all__ = [
    "MATCH_RADIUS_DEGREES",
    "NEW_POSITION_DEGREES",
    "POSITION_WINDOW_SECONDS",
    "find_target_changes",
    "match_library_target",
]

MATCH_RADIUS_DEGREES = 0.5
"""A pointing matches a library target only inside this distance, in degrees.
Design estimate, register entry WAY-D55: a mount that has not been
plate-solved points within a few tenths of a degree of its goal, and
0.5 degrees stays under the 0.6 degree gap between neighboring objects
such as M 81 and M 82."""

NEW_POSITION_DEGREES = 0.25
"""A slew counts as a new target only if it ends this far from the last
listed change, in degrees. Design estimate, register entry WAY-D56: centering
steps and a meridian flip end within a few arcminutes of the earlier
pointing, so a quarter of a degree separates them from a real move."""

POSITION_WINDOW_SECONDS = 120.0
"""How long after tracking starts a mount position still counts as the
pointing of that slew, in seconds. Design estimate, register entry
WAY-D57: the analyze log writes a position whenever the mount reports a
change, which in the logs read so far happened within about a minute of a
state change."""

SLEW_STATE = "Slewing"
"""The Ekos mount state that means a go-to slew is in progress."""

TRACKING_STATE = "Tracking"
"""The Ekos mount state that means the mount follows the sky."""

_UNSOLVED_PLACEHOLDER = (0.0, 0.0)
"""Where a target sits before it is plate-solved (RA 0, Dec 0). Such a
target has no real position to match."""


def library_target_positions(context: ControlContext) -> list[tuple[str, float, float]]:
    """List the library targets that have a real position.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the science library.

    Returns
    -------
    positions : `list` [`tuple` [`str`, `float`, `float`]]
        Target id, right ascension and declination in degrees. A target
        that is still at the unsolved placeholder position, or whose
        coordinates cannot be read, is left out.
    """
    positions = []
    for target in context.astrometrics.targets.list():
        try:
            ra_deg = parse_coordinate_string(target.ra, is_ra=True)
            dec_deg = parse_coordinate_string(target.dec, is_ra=False)
        except InvalidArgumentError:
            logger.info("Target %s has unreadable coordinates; not used to match pointings.", target.id)
            continue
        if (ra_deg, dec_deg) == _UNSOLVED_PLACEHOLDER:
            continue
        positions.append((target.id, ra_deg % 360.0, dec_deg))
    return positions


def match_library_target(
    ra_deg: float, dec_deg: float, positions: list[tuple[str, float, float]]
) -> tuple[str | None, float | None]:
    """Find the library target nearest a pointing, inside the match radius.

    Parameters
    ----------
    ra_deg, dec_deg : `float`
        The pointing, in degrees.
    positions : `list` [`tuple` [`str`, `float`, `float`]]
        Candidate targets, as from `library_target_positions`.

    Returns
    -------
    target_id : `str` or `None`
        The nearest target within `MATCH_RADIUS_DEGREES`, or `None`.
    separation_deg : `float` or `None`
        Its distance from the pointing, in degrees, or `None`.
    """
    best_id: str | None = None
    best_separation = MATCH_RADIUS_DEGREES
    for target_id, target_ra, target_dec in positions:
        separation = angular_separation_arcsec(ra_deg, dec_deg, target_ra, target_dec) / 3600.0
        if separation <= best_separation:
            best_id, best_separation = target_id, separation
    return (best_id, best_separation) if best_id is not None else (None, None)


def _slew_ends(record: EkosSessionContext) -> list[tuple[float, float]]:
    """Find where each slew in a session ended in tracking.

    Parameters
    ----------
    record : `EkosSessionContext`
        The recorded session.

    Returns
    -------
    ends : `list` [`tuple` [`float`, `float`]]
        For each slew, the time it started and the time tracking began.
    """
    ends = []
    slew_started_at: float | None = None
    for event in sorted(record.mount_state_events, key=lambda event: event.timestamp):
        if event.state == SLEW_STATE and slew_started_at is None:
            slew_started_at = event.timestamp
        elif event.state == TRACKING_STATE and slew_started_at is not None:
            ends.append((slew_started_at, event.timestamp))
            slew_started_at = None
        elif event.state != SLEW_STATE:
            slew_started_at = None
    return ends


def _pointing_at(
    record: EkosSessionContext, slew_started_at: float, tracking_at: float, location: dict[str, float] | None
) -> tuple[float | None, float | None, str | None]:
    """Work out where the mount pointed when a slew ended.

    Parameters
    ----------
    record : `EkosSessionContext`
        The recorded session, with its mount positions.
    slew_started_at, tracking_at : `float`
        When the slew began and when tracking began.
    location : `dict` [`str`, `float`] or `None`
        The observer's ``latitude``, ``longitude`` and ``elevation``.

    Returns
    -------
    ra_deg, dec_deg : `float` or `None`
        The pointing in ICRS, in degrees.
    note : `str` or `None`
        Why the pointing is missing, or `None`.
    """
    candidates = [
        position
        for position in record.mount_positions
        if slew_started_at <= position.timestamp <= tracking_at + POSITION_WINDOW_SECONDS
    ]
    if not candidates:
        return (
            None,
            None,
            (f"No mount position was logged within {POSITION_WINDOW_SECONDS:.0f} s after tracking began."),
        )
    if location is None:
        return None, None, "No observer location is known, so right ascension cannot be worked out."
    position = min(candidates, key=lambda candidate: abs(candidate.timestamp - tracking_at))
    site = earth_location(location["latitude"], location["longitude"], location.get("elevation", 0.0))
    horizontal = SkyCoord(
        alt=position.altitude_degrees * u.deg,
        az=position.azimuth_degrees * u.deg,
        frame=AltAz(obstime=Time(position.timestamp, format="unix"), location=site),
    )
    sky = horizontal.icrs
    return float(sky.ra.deg), float(sky.dec.deg), None


def find_target_changes(
    context: ControlContext, session_id: str | None = None, limit: int = 10
) -> list[TargetChange]:
    """List the slews that moved the mount to a new place in the sky.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the recorded Ekos sessions, the observer location and the
        library targets.
    session_id : `str`, optional
        Keep only this observing night, such as ``"2026-09-24"``. Every
        recorded night when omitted.
    limit : `int`, optional
        How many of the newest changes to return.

    Returns
    -------
    changes : `list` [`TargetChange`]
        The changes, oldest first. Empty when no recorded session has a
        slew that ended in tracking.
    """
    records = sorted(context.butler.get_all("ekos_session_context"), key=lambda record: record.started_at)
    if session_id:
        records = [record for record in records if record.session_id == session_id]
    slews = [(record, start, end) for record in records for start, end in _slew_ends(record)]
    if not slews:
        return []
    location = context.observer_location()
    positions = library_target_positions(context)
    changes: list[TargetChange] = []
    last_listed: tuple[float, float] | None = None
    for record, slew_started_at, tracking_at in slews:
        ra_deg, dec_deg, note = _pointing_at(record, slew_started_at, tracking_at, location)
        if ra_deg is not None and dec_deg is not None:
            if (
                last_listed is not None
                and angular_separation_arcsec(*last_listed, ra_deg, dec_deg) / 3600.0 < NEW_POSITION_DEGREES
            ):
                continue
            last_listed = (ra_deg, dec_deg)
            target_id, separation = match_library_target(ra_deg, dec_deg, positions)
        else:
            target_id, separation = None, None
        changes.append(
            TargetChange(
                time=tracking_at,
                night_id=record.session_id,
                ra_deg=ra_deg,
                dec_deg=dec_deg,
                target_id=target_id,
                separation_deg=separation,
                source=record.id,
                note=note,
            )
        )
    return changes[-limit:]
