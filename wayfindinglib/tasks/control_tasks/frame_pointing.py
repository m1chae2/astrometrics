"""Purpose: Record the pointing error of new frames that hold a plate solve.

Description: When KStars/Ekos plate-solves a frame, it writes the solution
into the frame's FITS header as a world coordinate system (WCS): the
header block that maps pixels to sky positions. Its reference point
(``CRVAL1``, ``CRVAL2``) is where the frame really points. The header
also holds where the mount was sent (``OBJCTRA`` and ``OBJCTDEC``, or
``RA`` and ``DEC``). The difference is the mount's pointing error at that
moment.

`record_frame_pointing_errors` runs after `control.remote.sync_frames`
brings new frames into the library. It records one alignment attempt per
new frame that has both positions, so
`control.history.query(kind="alignment")` shows them. Only new frames
are read, so a frame is never recorded twice.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from astrometricslib import FITS_READ_ERRORS, InvalidArgumentError, parse_coordinate_string
from wayfindinglib.astronomy.coordinate_transforms import signed_offset_components_arcsec

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["ALIGNED_LIMIT_ARCSEC", "pointing_error_from_header", "record_frame_pointing_errors"]

logger = logging.getLogger(__name__)

ALIGNED_LIMIT_ARCSEC = 120.0
"""A frame whose pointing error is at most this counts as aligned."""


def _angle_deg(value: Any, is_ra: bool) -> float | None:
    """Read a header angle in degrees.

    Parameters
    ----------
    value : `Any`
        A number (already degrees) or a text angle (``"12 30 00"`` is
        hours for right ascension, degrees for declination).
    is_ra : `bool`
        Whether the angle is a right ascension.

    Returns
    -------
    angle_deg : `float` or `None`
        The angle in degrees, or `None` if it cannot be read.
    """
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return parse_coordinate_string(value.replace(":", " "), is_ra=is_ra)
        except InvalidArgumentError:
            return None
    return None


def _observation_time(header: Any, file_path: str) -> float:
    """Return when a frame was taken, as Unix time.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The frame's header.
    file_path : `str`
        The frame, whose change time is used when ``DATE-OBS`` is missing.

    Returns
    -------
    timestamp : `float`
        ``DATE-OBS`` (UTC when it has no time zone), or the file's
        modification time.
    """
    date_obs = header.get("DATE-OBS")
    if date_obs:
        try:
            moment = datetime.fromisoformat(str(date_obs).split(".")[0])
        except ValueError:
            moment = None
        if moment is not None:
            return (moment if moment.tzinfo else moment.replace(tzinfo=UTC)).timestamp()
    return os.path.getmtime(file_path)


def pointing_error_from_header(header: Any, file_path: str) -> dict[str, Any] | None:
    """Work out one frame's pointing error from its header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The frame's header.
    file_path : `str`
        The frame's path, for the time when the header has none.

    Returns
    -------
    attempt : `dict` [`str`, `Any`] or `None`
        An alignment attempt: ``status`` (``aligned`` up to
        `ALIGNED_LIMIT_ARCSEC`, otherwise ``warning``), the offset from
        the commanded to the solved position as ``delta_ra_arcsec`` and
        ``delta_dec_arcsec`` (a distance on the sky, wrapped at 0h/24h),
        ``pointing_error_arcsec``, the solved ``ra`` and ``dec`` in
        degrees, ``target_name`` and ``timestamp``. `None` when the header
        lacks either position.
    """
    solved_ra = header.get("CRVAL1")
    solved_dec = header.get("CRVAL2")
    if solved_ra is None or solved_dec is None:
        return None
    commanded_ra = _angle_deg(header.get("OBJCTRA") or header.get("RA"), is_ra=True)
    commanded_dec = _angle_deg(header.get("OBJCTDEC") or header.get("DEC"), is_ra=False)
    if commanded_ra is None or commanded_dec is None:
        return None
    solved_ra, solved_dec = float(solved_ra) % 360.0, float(solved_dec)
    delta_ra, delta_dec = signed_offset_components_arcsec(commanded_ra, commanded_dec, solved_ra, solved_dec)
    error = math.hypot(delta_ra, delta_dec)
    return {
        "status": "aligned" if error <= ALIGNED_LIMIT_ARCSEC else "warning",
        "delta_ra_arcsec": round(delta_ra, 2),
        "delta_dec_arcsec": round(delta_dec, 2),
        "pointing_error_arcsec": round(error, 2),
        "ra": round(solved_ra, 5),
        "dec": round(solved_dec, 5),
        "target_name": header.get("OBJECT"),
        "timestamp": _observation_time(header, file_path),
    }


def record_frame_pointing_errors(context: ControlContext, paths: list[str]) -> int:
    """Record the pointing error of each new frame that holds a plate solve.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database.
    paths : `list` [`str`]
        The frames just added to the library.

    Returns
    -------
    recorded : `int`
        How many alignment attempts were recorded.
    """
    from astropy.io import fits

    recorded = 0
    for path in paths:
        if not path.lower().endswith((".fits", ".fit")):
            continue
        try:
            with fits.open(path, memmap=False) as frame:
                attempt = pointing_error_from_header(frame[0].header, path)
        except FITS_READ_ERRORS as error:
            logger.debug("Skipping %s: %s", path, error)
            continue
        if attempt is None:
            continue
        context.records.record_alignment_attempt(attempt)
        recorded += 1
    if recorded:
        logger.info("Recorded the pointing error of %s new plate-solved frame(s)", recorded)
    return recorded
