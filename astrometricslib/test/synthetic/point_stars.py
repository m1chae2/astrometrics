"""Purpose: Draw point-sampled Gaussian stars onto an image quickly.

Description: Many stacking tests draw round stars as the Gaussian value at
each pixel center (a point sample, not the pixel integral that
`_pixel_math.pixel_fractions` gives). Drawing a star on every pixel of a
large image is slow, because almost every pixel is far from the star and
gets a value of zero. This helper draws the star only in a small square
around its center. The square reaches far enough out that the light left
out is below a ten-trillionth of the star's peak, far below the float32
precision the tests write their images in, so the image is the same as one
drawn over every pixel.
"""

import math

import numpy as np

# How far, in units of the star's Gaussian width, the drawn square reaches
# from the center. At this distance the Gaussian is exp(-0.5 * 8**2), about
# 1e-14 of its peak. Written to float32, that is lost below the precision
# of any sky level the tests use, so stopping here changes no pixel value
# that reaches a measurement.
STAMP_HALF_WIDTH_SIGMAS = 8.0


def add_gaussian_star(
    image: np.ndarray,
    x: float,
    y: float,
    peak: float,
    sigma_px: float,
) -> None:
    """Add one round Gaussian star to an image, in place.

    Each pixel gets ``peak * exp(-r**2 / (2 * sigma_px**2))``, where ``r``
    is the distance from the pixel center to the star center. Pixels farther
    than `STAMP_HALF_WIDTH_SIGMAS` widths away are left unchanged.

    Parameters
    ----------
    image : `numpy.ndarray`
        The image to draw on, of shape ``(rows, columns)``. It is changed in
        place.
    x : `float`
        The star's column, in pixels.
    y : `float`
        The star's row, in pixels.
    peak : `float`
        The star's height at its center, in the image's units.
    sigma_px : `float`
        The star's Gaussian width, in pixels.
    """
    reach = math.ceil(STAMP_HALF_WIDTH_SIGMAS * sigma_px)
    row_low = max(math.floor(y) - reach, 0)
    row_high = min(math.floor(y) + reach + 2, image.shape[0])
    column_low = max(math.floor(x) - reach, 0)
    column_high = min(math.floor(x) + reach + 2, image.shape[1])
    if row_low >= row_high or column_low >= column_high:
        return
    rows = np.arange(row_low, row_high)[:, None]
    columns = np.arange(column_low, column_high)[None, :]
    image[row_low:row_high, column_low:column_high] += peak * np.exp(
        -((columns - x) ** 2 + (rows - y) ** 2) / (2 * sigma_px**2)
    )
