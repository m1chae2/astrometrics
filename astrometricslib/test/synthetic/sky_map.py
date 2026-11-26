"""Purpose: Build a simple sky map (WCS) for tests.

Description: Many tests need a plate solution, the World Coordinate System
(WCS) that turns a pixel position into a right ascension and declination.
`tan_sky_map` builds the common one: a gnomonic (TAN) projection, the flat
projection a camera's field follows, with no lens distortion terms. Tests
that need distortion (SIP) terms, a mirrored image or a turned field build
their own, because those settings are what they test.
"""

from collections.abc import Sequence

import numpy as np
from astropy.wcs import WCS

__all__ = ["tan_sky_map"]


def tan_sky_map(
    center_ra_deg: float,
    center_dec_deg: float,
    reference_pixel: Sequence[float],
    degrees_per_pixel: float | None = None,
    cd: Sequence[Sequence[float]] | np.ndarray | None = None,
) -> WCS:
    """Build a TAN sky map, with north up and east to the left by default.

    Parameters
    ----------
    center_ra_deg, center_dec_deg : `float`
        The sky position of the reference pixel, in degrees.
    reference_pixel : `Sequence` [`float`]
        The reference pixel ``(x, y)``, counted from 1 as the FITS standard
        does (``CRPIX1``, ``CRPIX2``).
    degrees_per_pixel : `float`, optional
        The pixel size on the sky. The map then has ``CDELT`` of
        ``(-degrees_per_pixel, +degrees_per_pixel)``: right ascension grows
        to the left and declination grows upward.
    cd : array-like, optional
        A full 2 by 2 ``CD`` matrix (degrees per pixel), for a turned or
        stretched field. Give this or ``degrees_per_pixel``, not both.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The sky map. ``wcs.to_header()`` gives its header cards.

    Raises
    ------
    ValueError
        If not exactly one of ``degrees_per_pixel`` and ``cd`` is given.
    """
    if (degrees_per_pixel is None) == (cd is None):
        raise ValueError("give exactly one of degrees_per_pixel and cd")
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [center_ra_deg, center_dec_deg]
    wcs.wcs.crpix = [float(reference_pixel[0]), float(reference_pixel[1])]
    if cd is not None:
        wcs.wcs.cd = np.asarray(cd, dtype=float)
    else:
        wcs.wcs.cdelt = [-degrees_per_pixel, degrees_per_pixel]
    return wcs
