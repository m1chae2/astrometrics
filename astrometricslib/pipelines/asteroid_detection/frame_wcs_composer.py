"""Figure out where each individual picture was pointing.

We combine the very accurate rotation and scale information from the final
stacked image with the slightly-less-accurate pointing information saved by
the telescope in each individual picture's header. We have to do this
because our stacking software (Siril) doesn't save the exact per-picture
alignments it calculates. This method is close enough for finding moving
objects.
"""

import logging

from astropy.io import fits
from astropy.wcs import WCS

logger = logging.getLogger(__name__)


def estimate_frame_wcs_from_mount_pointing(
    stack_wcs: WCS,
    frame_fits_header: fits.Header,
) -> WCS | None:
    """Calculate how pixels map to the sky for one specific picture.

    This takes the rotation and scale from the main stack, but centers the
    view on where the telescope said it was pointing when it took this
    specific picture.

    Parameters
    ----------
    stack_wcs : `astropy.wcs.WCS`
        The accurate sky-mapping (WCS) from the final stacked image.
    frame_fits_header : `astropy.io.fits.Header`
        The data header loaded from this specific picture's file.

    Returns
    -------
    frame_wcs : `astropy.wcs.WCS` or `None`
        The sky-mapping for this specific picture, or None if the picture
        is missing necessary location data.
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
