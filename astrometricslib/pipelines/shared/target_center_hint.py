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
import os
import warnings
from typing import Any

from astropy.wcs import WCS, FITSFixedWarning

from astrometricslib.drivers.fits_access import read_header
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


def resolve_solved_stack_center_hint(
    target: Any, spectral_stack_path: str
) -> tuple[float | None, float | None]:
    """Read where the telescope really pointed from a plate-solved stack.

    A spectral stack has no plate solution of its own, and the position in
    its FITS header is only the mount's report, which can be far from the
    truth: on the Vega session it named a spot 24 arcminutes from Vega, so
    the star at the frame centre was labelled with a faint neighbour of that
    spot (TYC 3105-827-1, magnitude 10.6) instead of Vega. The same target's
    plate-solved standard stack, taken with the same camera and telescope,
    knows where the frame centre is on the sky: Vega fell 13 pixels from the
    solved centre and 3 pixels from the same star's zero order in the
    spectral stack.

    So the centre of the target's solved stack with the same camera and focal
    length as the spectral stack is used as the hint. Nothing is guessed when
    no such stack exists.

    Parameters
    ----------
    target : `astrometricslib.models.target.Target`
        The target whose standard stacks are searched.
    spectral_stack_path : `str`
        The spectral stack that needs the hint. Its ``INSTRUME`` and
        ``FOCALLEN`` header entries say which camera and telescope took it.

    Returns
    -------
    center_ra : `float` or `None`
        The solved stack's centre right ascension in decimal degrees, or
        `None` when there is no matching solved stack.
    center_dec : `float` or `None`
        The declination in decimal degrees, or `None` under the same
        condition.
    """
    try:
        spectral_header = read_header(spectral_stack_path)
    except OSError:
        return None, None
    spectral_camera = spectral_header.get("INSTRUME")
    spectral_focal_length = spectral_header.get("FOCALLEN")
    if not spectral_camera or spectral_focal_length is None:
        return None, None

    candidate_paths = [target.stacked_image] + [
        configuration.stacked_image for configuration in target.stacks_by_configuration.values()
    ]
    for candidate_path in dict.fromkeys(path for path in candidate_paths if path):
        if not os.path.exists(candidate_path):
            continue
        try:
            header = read_header(candidate_path)
        except OSError:
            continue
        same_setup = (
            header.get("INSTRUME") == spectral_camera
            and header.get("FOCALLEN") is not None
            and float(header["FOCALLEN"]) == float(spectral_focal_length)  # ruff: ignore[float-equality-comparison] -- a focal length copied from the same setup, not a measurement
        )
        if not same_setup or "CRVAL1" not in header:
            continue
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(header, naxis=2)
        centre_x = header.get("NAXIS1", 0) / 2.0
        centre_y = header.get("NAXIS2", 0) / 2.0
        center_ra, center_dec = wcs.pixel_to_world_values(centre_x, centre_y)
        logger.info(
            "Using the centre of the solved stack %s as the spectral position hint: %.4f, %.4f.",
            os.path.basename(candidate_path),
            float(center_ra),
            float(center_dec),
        )
        return float(center_ra), float(center_dec)
    return None, None
