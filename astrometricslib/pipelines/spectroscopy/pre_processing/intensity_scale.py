"""Purpose: Say what the numbers in a stored spectrum are measured in.

Description: A spectrum's intensities are whatever the image they were read
from held. A single raw frame holds detector counts. A stack that Siril wrote
as floating point holds each pixel as a fraction of full scale, averaged over
the frames: 1.0 means 65535 counts in one frame, whatever the frame's
exposure was. So two stored spectra can differ by a factor of 65535 for the
same star, and by the exposure length as well, and nothing in the spectrum
says which it is.

This module works out one number per image, the factor that turns the
stored intensities into detector counts per second. With it, spectra from
different images and exposures can be compared for brightness. The
intensities themselves are not changed.

The full-scale value is the one the stacker already uses
(`exposure_groups.FULL_SCALE_COUNTS`), checked on the ASI533MM Pro's stacks:
Deneb's stack tops out at exactly 1.0 where the star saturates.
"""

import logging
from typing import Any

from astrometricslib.pipelines.stacking.exposure_groups import FULL_SCALE_COUNTS

logger = logging.getLogger(__name__)

# The FITS bit depth Siril gives a floating-point stack. Raw camera frames
# are integers (positive BITPIX).
FLOAT_STACK_BITPIX = -32


def counts_per_second_factor(header: Any) -> float | None:
    """Work out how to turn an image's pixel values into counts per second.

    Parameters
    ----------
    header : `astropy.io.fits.Header` or `None`
        The header of the image the spectrum was read from, or `None` when
        the image has none.

    Returns
    -------
    factor : `float` or `None`
        Multiply a stored intensity by this to get detector counts per
        second. For a raw frame that is 1 divided by its exposure time. For a
        Siril float stack it is full scale (65535 counts) divided by the
        average exposure of the frames in the stack (``EXPTIME`` divided by
        ``STACKCNT``). `None` when the header does not say enough, so an
        unknown scale is never guessed.
    """
    if header is None:
        return None
    try:
        exposure_seconds = float(header.get("EXPTIME", 0.0) or 0.0)
        bits_per_pixel = int(header.get("BITPIX", 0) or 0)
        stack_count = int(header.get("STACKCNT", 0) or 0)
    except TypeError, ValueError:
        return None
    if exposure_seconds <= 0:
        return None
    if bits_per_pixel == FLOAT_STACK_BITPIX and stack_count > 0:
        return FULL_SCALE_COUNTS / (exposure_seconds / stack_count)
    if bits_per_pixel > 0 and stack_count == 0:
        return 1.0 / exposure_seconds
    return None
