"""Turning a target's RA/Dec into a plate-solve center hint.

`astrometricslib.utilities.coordinate_parsing.parse_coordinate_string` raises
on anything it can't parse -- an empty string, the "0h 0m 0s" placeholder a
target starts with before it has ever been plate solved, or a malformed
value. Photometry (twice: seeding a session with an astrometry lookup, and
solving a session's WCS for cross-session star matching) and spectroscopy
(seeding session star identification) all want the same fallback for that:
pass no hint and let plate solving fall back to a blind solve, rather than
fail outright. This keeps that fallback in one place instead of three.
"""

import logging
from typing import Any

from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)


def resolve_target_center_hint(target: Any) -> tuple[float | None, float | None]:
    """Parse a target's RA/Dec into a plate-solve center hint.

    Parameters
    ----------
    target : `astrometricslib.models.target.Target`
        The target to read `ra`/`dec` from.

    Returns
    -------
    center_ra : `float` or `None`
        The target's right ascension in decimal degrees, or `None` if it
        could not be parsed -- the caller should blind solve instead.
    center_dec : `float` or `None`
        The target's declination in decimal degrees, or `None` under the
        same condition as `center_ra`.
    """
    try:
        center_ra = parse_coordinate_string(str(target.ra), is_ra=True)
        center_dec = parse_coordinate_string(str(target.dec), is_ra=False)
    except Exception as exc:
        logger.debug("Falling back to blind solve, could not parse target RA/Dec: %s", exc)
        return None, None
    return center_ra, center_dec
