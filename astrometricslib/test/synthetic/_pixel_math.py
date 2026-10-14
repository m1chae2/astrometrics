"""Purpose: Shared pixel-integration helper for the synthetic generators.

Description: A camera pixel collects all the light that lands inside its
edges. It does not sample the light at one point. This file holds the one
function that turns a 1-D Gaussian brightness curve into the fraction of
light that falls in each pixel, using the error function (the integral of
a Gaussian). Both the photometry generator and the spectral generator use
it, so the two agree on what "pixel-integrated" means.
"""

import numpy as np
from scipy.special import erf

FWHM_TO_SIGMA = 1.0 / (2.0 * np.sqrt(2.0 * np.log(2.0)))


def pixel_fractions(centres: np.ndarray | float, sigma_px: np.ndarray | float, n_pixels: int) -> np.ndarray:
    """Return the fraction of a 1-D Gaussian that lands in each pixel.

    Pixel ``i`` covers the interval from ``i - 0.5`` to ``i + 0.5``, so the
    centre of pixel ``i`` sits at the integer coordinate ``i``. The
    fraction for each pixel is the exact integral of the Gaussian over
    that interval. It is not the Gaussian value at the pixel centre.

    Parameters
    ----------
    centres : `numpy.ndarray` or `float`
        Centre of the Gaussian, in pixel coordinates. A scalar gives one
        curve. An array of shape ``(m,)`` gives ``m`` curves.
    sigma_px : `numpy.ndarray` or `float`
        Standard deviation of the Gaussian, in pixels. Must be positive. A
        scalar gives every curve the same width. An array of shape ``(m,)``
        gives each of the ``m`` curves its own width.
    n_pixels : `int`
        Number of pixels along the axis.

    Returns
    -------
    fractions : `numpy.ndarray`
        Array of shape ``(n_pixels, m)`` with one column per centre
        (``m = 1`` for a scalar). Each column sums to 1 when the whole
        Gaussian lies inside the axis, and to less than 1 when part of it
        falls outside.
    """
    centre_row = np.atleast_1d(np.asarray(centres, dtype=np.float64))[np.newaxis, :]
    pixel_index = np.arange(n_pixels, dtype=np.float64)[:, np.newaxis]
    scale = sigma_px * np.sqrt(2.0)
    upper_edge = erf((pixel_index + 0.5 - centre_row) / scale)
    lower_edge = erf((pixel_index - 0.5 - centre_row) / scale)
    return 0.5 * (upper_edge - lower_edge)
