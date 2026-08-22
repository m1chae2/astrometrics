"""Per-frame WCS estimation for the asteroid-recovery pipeline.

Estimates a WCS in each individual frame's own pixel space using the
stack's plate-solve WCS for scale/rotation/projection plus that
frame's own mount-reported pointing for the sky position -- NOT
Siril's per-frame registration transform. Empirically confirmed (real
M 81/M 13 sessions, Siril 1.4.4) that although Siril's "Global Star
Alignment" computes a real per-frame homography during registration
(visible in its own log output as real, varying dx/dy/rotation/scale
values), it never serializes that data into the .seq file it writes
to disk -- every frame's H matrix in that file reads back as the
identity, regardless of what was actually computed. Since that data
isn't recoverable after the fact, this instead uses each frame's own
OBJCTRA/OBJCTDEC-derived RA/DEC header values (already present per
frame, confirmed to vary meaningfully frame-to-frame across a real
session), which carry the mount's own reported pointing at capture
time -- coarser than a star-based registration solution, but accuracy
loss here is irrelevant at the sky-motion scales this pipeline cares
about (arcsec-to-arcmin/hour).
"""

import logging

from astropy.io import fits
from astropy.wcs import WCS

logger = logging.getLogger(__name__)


def estimate_frame_wcs_from_mount_pointing(
    stack_wcs: WCS,
    frame_fits_header: fits.Header,
) -> WCS | None:
    """Estimate a frame-space WCS from the stack's WCS and mount pointing.

    Keeps the stack WCS's projection type, units, and pixel-scale
    matrix (scale and rotation are assumed effectively constant across
    one target's frame set for a tracked, equatorially-mounted imaging
    session -- real per-frame field rotation was confirmed, via
    Siril's own registration log output, to be on the order of
    hundredths of a degree, negligible at this pipeline's motion
    scales), but re-centers the sky position (``CRVAL``) on this
    frame's own reported RA/DEC rather than the stack's, and
    re-centers the reference pixel (``CRPIX``) on this frame's own
    image center rather than the stack's.

    Parameters
    ----------
    stack_wcs : `astropy.wcs.WCS`
        The stack's plate-solve WCS, used only for its projection
        type, units, and pixel-scale matrix.
    frame_fits_header : `astropy.io.fits.Header`
        The individual frame's own FITS header, read directly from
        disk (not a persisted field on `FrameRecord` -- this is
        computed on demand from the frame's existing `path`).

    Returns
    -------
    frame_wcs : `astropy.wcs.WCS` or `None`
        A WCS estimate valid in this frame's own native pixel space,
        or `None` (with a logged warning) if the frame's header is
        missing RA/DEC or image-dimension keywords.

    """
    right_ascension_deg = frame_fits_header.get("RA")
    declination_deg = frame_fits_header.get("DEC")
    if right_ascension_deg is None or declination_deg is None:
        logger.warning("Frame FITS header is missing RA/DEC keywords; cannot estimate its WCS.")
        return None

    frame_width_px = frame_fits_header.get("NAXIS1")
    frame_height_px = frame_fits_header.get("NAXIS2")
    if not frame_width_px or not frame_height_px:
        logger.warning("Frame FITS header is missing NAXIS1/NAXIS2; cannot estimate its WCS.")
        return None

    try:
        right_ascension_deg = float(right_ascension_deg)
        declination_deg = float(declination_deg)
    except TypeError, ValueError:
        logger.warning(
            f"Frame FITS header's RA/DEC values are not numeric "
            f"(RA={right_ascension_deg!r}, DEC={declination_deg!r}); cannot estimate its WCS."
        )
        return None

    frame_wcs = WCS(naxis=2)
    frame_wcs.wcs.ctype = list(stack_wcs.wcs.ctype)
    frame_wcs.wcs.cunit = list(stack_wcs.wcs.cunit)
    frame_wcs.wcs.crval = [right_ascension_deg, declination_deg]
    frame_wcs.wcs.crpix = [frame_width_px / 2.0, frame_height_px / 2.0]
    frame_wcs.wcs.cd = stack_wcs.pixel_scale_matrix
    frame_wcs.wcs.set()
    return frame_wcs
