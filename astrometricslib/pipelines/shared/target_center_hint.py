"""Turning a target's RA/Dec into a plate-solve center hint.

`astrometricslib.utilities.coordinate_parsing.parse_coordinate_string` raises
on an empty string or a malformed value, but parses the "0h 0m 0s" / "0d 0m
0s" placeholder a target starts with before it has ever been plate solved
just fine, as a literal (0.0, 0.0) -- so that case has to be checked for
explicitly. Astrometry (resolving its own plate-solve center hint),
photometry (twice: seeding a session with an astrometry lookup, and solving
a session's WCS for cross-session star matching), and spectroscopy (seeding
session star identification) all want the same fallback for either case:
pass no hint and let plate solving fall back to a blind solve, rather than
fail outright, or silently center on RA=0/Dec=0. This keeps that fallback in
one place instead of four.
"""

import logging
from typing import Any

from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string

logger = logging.getLogger(__name__)


def resolve_center_hint(ra: str, dec: str) -> tuple[float | None, float | None]:
    """Parse an RA/Dec string pair into a plate-solve center hint.

    Parameters
    ----------
    ra : `str`
        Right ascension, in any format `parse_coordinate_string` accepts.
    dec : `str`
        Declination, in any format `parse_coordinate_string` accepts.

    Returns
    -------
    center_ra : `float` or `None`
        The right ascension in decimal degrees, or `None` if it could not
        be parsed, or was the never-plate-solved placeholder -- the caller
        should blind solve instead.
    center_dec : `float` or `None`
        The declination in decimal degrees, or `None` under the same
        condition as `center_ra`.
    """
    try:
        center_ra = parse_coordinate_string(str(ra), is_ra=True)
        center_dec = parse_coordinate_string(str(dec), is_ra=False)
    except Exception as exc:
        logger.debug("Falling back to blind solve, could not parse RA/Dec: %s", exc)
        return None, None
    # 0h0m0s/0d0m0s parses without error as a literal (0.0, 0.0); it is
    # almost certainly the placeholder, not an actual pointing at that
    # coordinate.
    if center_ra == 0.0 and center_dec == 0.0:  # ruff: ignore[float-equality-comparison] -- 0.0 placeholder sentinel, not measured
        return None, None
    return center_ra, center_dec


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
    return resolve_center_hint(target.ra, target.dec)
