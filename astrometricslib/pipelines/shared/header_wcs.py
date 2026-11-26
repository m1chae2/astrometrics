"""Reads the sky map (WCS) from a FITS header, and an image's corners on it.

A plate-solved image carries its sky map in its header: the World Coordinate
System (WCS), which turns a pixel position into a right ascension and
declination. Many steps read it, so the reading lives here once.

`celestial_wcs` reads the two sky axes of a header. It hides astropy's
notices about cards it had to repair (`FITSFixedWarning`), and gives `None`
for a header with no usable sky map instead of raising. A header that is not
plate-solved is an ordinary case, not an error.

`image_corners_on_sky` gives the sky positions of an image's four corners.
It uses the full map, including the SIP terms (polynomial corrections for
the lens distortion that a solver adds to the header), because the
distortion is largest at the corners.
"""

import warnings
from collections.abc import Mapping
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

__all__ = ["WCS_READ_ERRORS", "celestial_wcs", "image_corners_on_sky"]

# The errors that reading a broken or partial sky map can raise. astropy's
# own WCS errors (`InvalidTransformError`, `SingularMatrixError` and the
# rest) are `ValueError`s. A header too large to parse raises `MemoryError`.
WCS_READ_ERRORS = (ValueError, KeyError, TypeError, MemoryError, fits.verify.VerifyError)


def celestial_wcs(header: fits.Header | Mapping[str, Any] | None) -> WCS | None:
    """Read a header's sky map, if it has one.

    Parameters
    ----------
    header : `astropy.io.fits.Header` or `Mapping` or `None`
        A FITS header, or a plain mapping of its cards.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The two sky axes, with any SIP distortion terms, or `None` when there
        is no header or it has no usable sky map.
    """
    if header is None:
        return None
    try:
        card_header = header if isinstance(header, fits.Header) else fits.Header(dict(header))
        with warnings.catch_warnings():
            # astropy repairs old or odd date and unit cards and says so; the
            # repaired map is what is wanted.
            warnings.simplefilter("ignore", FITSFixedWarning)
            wcs = WCS(card_header, naxis=2)
        if not wcs.has_celestial:
            return None
        return wcs.celestial
    except WCS_READ_ERRORS:
        return None


def image_corners_on_sky(wcs: WCS, width: int, height: int) -> list[tuple[float, float]]:
    """Find the sky positions of an image's four corners.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The image's sky map. Its SIP distortion terms are applied.
    width, height : `int`
        The image size, in pixels.

    Returns
    -------
    corners : `list` [`tuple` [`float`, `float`]]
        ``(ra, dec)`` in degrees, in order around the edge, at the outer
        edges of the corner pixels.
    """
    pixel_x = np.array([-0.5, width - 0.5, width - 0.5, -0.5])
    pixel_y = np.array([-0.5, -0.5, height - 0.5, height - 0.5])
    ra_values, dec_values = wcs.all_pix2world(pixel_x, pixel_y, 0)
    return [(float(ra), float(dec)) for ra, dec in zip(ra_values, dec_values, strict=True)]
