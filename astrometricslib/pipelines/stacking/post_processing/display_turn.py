"""Purpose: Say how far to turn a raw frame on screen so it matches its stack.

Description: Frames taken on the two sides of a meridian flip are upside down
from each other. A viewer shows each raw frame turned by a quarter turn (0, 90,
180 or 270 degrees, clockwise) so it looks the same way round as the target's
stack. The turn is for display only: the file is never changed.

The turn is the stack's rotation minus the frame's, rounded to the nearest
quarter turn. The stack's rotation is where the top of its picture points on
the sky (`up_angle_degrees`, from its plate solution). The frame's rotation
comes from the capture program's ``CROTA2`` card. When the frame has no
``CROTA2``, the side of the pier decides (``PIERSIDE``): a frame from the
other side of the pier than the stack is turned half a turn.

No turn is given for a stack without a plate solution, for a frame that has a
plate solution of its own (it is a stack, or was solved, so its sky map is
already right), or for a frame already turned to north (``QTURN``).

Words used here: a picture is drawn with the file's first row at the top when
the file says ``ROWORDER = TOP-DOWN`` and at the bottom otherwise.
"""

import math
from collections.abc import Mapping
from typing import Any

from astrometricslib._internal.drivers.fits_access import header_is_plate_solved
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.stacking.post_processing.quarter_turn import (
    QUARTER_TURN_KEYWORD,
    up_angle_degrees,
)

__all__ = ["display_turn_degrees", "frame_north_angle_degrees", "nearest_quarter_turn"]


def nearest_quarter_turn(degrees: float) -> int:
    """Round an angle to the nearest quarter turn.

    Parameters
    ----------
    degrees : `float`
        Any angle in degrees.

    Returns
    -------
    turn : `int`
        0, 90, 180 or 270.
    """
    # Half a quarter rounds up, so 45 degrees gives 90.
    quarters = math.floor(degrees / 90.0 + 0.5)
    return (quarters % 4) * 90


def _is_top_down(header: Mapping[str, Any]) -> bool:
    """Say whether a picture is drawn with the file's first row at the top.

    Returns
    -------
    top_down : `bool`
        `True` when ``ROWORDER`` is ``TOP-DOWN``.
    """
    return str(header.get("ROWORDER", "")).strip().upper() == "TOP-DOWN"


def frame_north_angle_degrees(header: Mapping[str, Any]) -> float | None:
    """Work out where north points on screen for a raw frame, from ``CROTA2``.

    ``CROTA2`` is the angle from the file's up direction (the +y axis) to
    north, going clockwise on a picture drawn with row 1 at the bottom. A
    picture stored top-down is drawn upside down from that, so its angle is
    measured from the other end of the axis.

    Parameters
    ----------
    header : `Mapping`
        The frame's header cards.

    Returns
    -------
    angle : `float` or `None`
        Degrees clockwise from the top of the screen to north (0 up to 360),
        or `None` when the header has no numeric ``CROTA2``.
    """
    rotation = header.get("CROTA2")
    if isinstance(rotation, bool) or not isinstance(rotation, int | float):
        return None
    angle = 180.0 - float(rotation) if _is_top_down(header) else float(rotation)
    return angle % 360.0


def _pier_side(header: Mapping[str, Any]) -> str | None:
    """Name the side of the pier a header gives.

    Returns
    -------
    side : `str` or `None`
        ``EAST`` or ``WEST``, or `None` when the card is missing or says
        something else.
    """
    side = str(header.get("PIERSIDE", "")).strip().upper()
    return side if side in ("EAST", "WEST") else None


def display_turn_degrees(
    frame_header: Mapping[str, Any] | None, stack_header: Mapping[str, Any] | None
) -> int:
    """Decide how far to turn a frame on screen so it matches its stack.

    Parameters
    ----------
    frame_header : `Mapping` or `None`
        The frame's header cards, or `None` when it has none.
    stack_header : `Mapping` or `None`
        The header of the target's imaging stack, or `None` when there is no
        stack.

    Returns
    -------
    turn : `int`
        The turn, clockwise in degrees: 0, 90, 180 or 270.
    """
    if frame_header is None or stack_header is None or not header_is_plate_solved(stack_header):
        return 0
    stack_wcs = celestial_wcs(stack_header)
    if stack_wcs is None:
        return 0
    if QUARTER_TURN_KEYWORD in frame_header or header_is_plate_solved(frame_header):
        return 0
    stack_up = up_angle_degrees(stack_wcs, top_down=_is_top_down(stack_header))
    frame_north = frame_north_angle_degrees(frame_header)
    if frame_north is not None:
        return nearest_quarter_turn(stack_up - frame_north)
    frame_pier = _pier_side(frame_header)
    stack_pier = _pier_side(stack_header)
    if frame_pier is not None and stack_pier is not None and frame_pier != stack_pier:
        return 180
    return 0
