"""Reads a stack's pixels and sums the light in one aperture of them.

The modules that measure a stack's noise (`empty_apertures.py`,
`stack_noise.py` and `difference_noise.py`) share a few small pieces. This
module holds them, and it imports none of those modules:

* `read_stack_plane` reads a stack file as one 2-D image, and
  `sky_level_of_plane` gives the median of its usable pixels.
* `signed_aperture_sum` sums the light in one circle after subtracting the sky
  taken from a ring around it, and the constants that give the noise aperture
  its shape.
* Helpers that turn pixel positions into sky positions and back, read a
  header's sky coordinate solution and give its pixel scale.
* `SaturationCheck` tells whether a star is saturated in the registration
  reference frame.
* The constants that turn a median absolute deviation into a spread and give
  the errors of a median and of a spread.

The estimator and the numbers are declared in `pipelines/stacking/metrics.py`.
"""

import math
from dataclasses import dataclass

import numpy as np
from astropy.io import fits
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales
from photutils.aperture import CircularAnnulus, CircularAperture

from astrometricslib.drivers.fits_access import collapse_to_2d, read_data

__all__ = [
    "APERTURE_RADIUS_PX",
    "MAD_TO_SIGMA",
    "RING_INNER_PX",
    "RING_OUTER_PX",
    "SaturationCheck",
    "celestial_wcs",
    "pixel_scale_arcsec",
    "pixels_to_sky",
    "read_stack_plane",
    "robust_sigma",
    "signed_aperture_sum",
    "sky_level_of_plane",
    "sky_to_pixels",
]

# The aperture and sky ring are the defaults of `measure_aperture_photometry`
# (the photometry pipeline's star aperture), so the noise is the noise of a
# faint star's brightness measured the way the pipeline measures it.
APERTURE_RADIUS_PX = 4.0
RING_INNER_PX = 7.0
RING_OUTER_PX = 12.0

# A ring must be this full of pixels (not off the stack, not empty edge).
MINIMUM_RING_FILL = 0.9

# A star is called saturated in the registration reference frame when its
# peak within this many pixels of its position reaches the camera's saturation
# level.
SATURATION_PEAK_HALF_WINDOW_PX = 3

# A robust standard deviation from the median absolute deviation (MAD) of a
# bell curve: sigma = 1.4826 MAD.
MAD_TO_SIGMA = 1.4826

# The standard error of a median is this times the standard error of a mean,
# for a bell curve.
MEDIAN_ERROR_FACTOR = 1.2533

# The standard error of a spread taken as 1.4826 MAD, in units of sigma /
# sqrt(n), for a bell curve. The variance of the MAD-based spread is that of
# the ordinary standard deviation, sigma^2 / (2 n), divided by the asymptotic
# efficiency of the MAD, 0.3675; so the error is sigma / sqrt(2 x 0.3675 x n)
# = 1.1664 sigma / sqrt(n). A formula was chosen over a bootstrap because it
# is exact in the limit, costs nothing and cannot differ between runs. The
# apertures do not overlap, so their sums are independent.
MAD_SIGMA_ERROR_FACTOR = 1.1664


def robust_sigma(values: np.ndarray, axis: int | None = None) -> np.ndarray | np.float64:
    """Give the robust spread of some values.

    This is `MAD_TO_SIGMA` times the median absolute deviation. The
    deviation is taken from the median of the same values (along the same
    axis), so a few outliers hardly change the result.

    Parameters
    ----------
    values : `numpy.ndarray`
        The values to measure.
    axis : `int`, optional
        The axis to measure along. By default all values are measured
        together.

    Returns
    -------
    spread : `numpy.ndarray` or `numpy.float64`
        The robust spread, one number for each position along the other axes.
    """
    centers = np.median(values, axis=axis, keepdims=True)
    return MAD_TO_SIGMA * np.median(np.abs(values - centers), axis=axis)


@dataclass(frozen=True)
class SaturationCheck:
    """Tells whether a star is saturated in the registration reference frame.

    A star saturated in the single frames that made the stack has a flux that
    does not grow in step with the others. The stack's own pixels cannot say:
    an average of many saturated frames looks smooth. So the check reads the
    registration reference frame, which has the stack's geometry (shifted by
    the trim offset).

    Parameters
    ----------
    frame_plane : `numpy.ndarray`
        The reference frame as one 2-D image, in ADU.
    crop_offset : `tuple` [`int`, `int`]
        The (columns, rows) cut from the stack's left and top edges.
    limit_adu : `float`
        A pixel at or above this value is saturated: the camera profile's
        saturation threshold, or its linearity limit when that is lower.
    source : `str`
        Words that say where the limit came from.
    """

    frame_plane: np.ndarray
    crop_offset: tuple[int, int]
    limit_adu: float
    source: str

    def peak_at(self, row: float, column: float) -> float | None:
        """Give the highest pixel value near a stack position.

        Parameters
        ----------
        row, column : `float`
            A position in the stack's pixels.

        Returns
        -------
        peak : `float` or `None`
            The highest value within `SATURATION_PEAK_HALF_WINDOW_PX` of the
            position in the reference frame, or `None` when the position is
            outside the frame.
        """
        frame_row = round(row) + self.crop_offset[1]
        frame_column = round(column) + self.crop_offset[0]
        half = SATURATION_PEAK_HALF_WINDOW_PX
        height, width = self.frame_plane.shape
        if not (half <= frame_row < height - half and half <= frame_column < width - half):
            return None
        window = self.frame_plane[
            frame_row - half : frame_row + half + 1, frame_column - half : frame_column + half + 1
        ]
        return float(np.max(window))

    def is_saturated(self, row: float, column: float) -> bool:
        """Say whether a star at a stack position reaches the saturation level.

        Parameters
        ----------
        row, column : `float`
            A position in the stack's pixels.

        Returns
        -------
        saturated : `bool`
            `True` when the peak is at or above the limit. A position outside
            the frame is not called saturated.
        """
        peak = self.peak_at(row, column)
        return peak is not None and peak >= self.limit_adu


def signed_aperture_sum(
    plane: np.ndarray,
    x: float,
    y: float,
    radius: float = APERTURE_RADIUS_PX,
    ring_inner: float = RING_INNER_PX,
    ring_outer: float = RING_OUTER_PX,
) -> float | None:
    """Sum the light in one circle after subtracting the sky from its ring.

    The sum is the same as the star brightness in `measure_aperture_photometry`
    (pixels weighted by their exact overlap with the circle, minus the ring's
    median times the circle's area) but it is not cut off at zero, because
    the spread of empty apertures needs the negative sums too.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image.
    x, y : `float`
        The center, in pixels (column, row), counted from 0.
    radius : `float`, optional
        The circle's radius in pixels. The noise aperture's by default.
    ring_inner, ring_outer : `float`, optional
        The sky ring's inner and outer radius in pixels.

    Returns
    -------
    value : `float` or `None`
        The sky-subtracted sum, or `None` when the circle or its ring reaches
        outside the image, or the ring or the circle holds non-finite pixels or
        exactly-zero pixels (the empty edge of a stack, or the pixels of a
        difference image that one of its two stacks does not cover).
    """
    height, width = plane.shape
    reach = ring_outer + 1.0
    if x < reach or y < reach or x > width - 1 - reach or y > height - 1 - reach:
        return None
    aperture = CircularAperture((x, y), r=radius)
    annulus = CircularAnnulus((x, y), r_in=ring_inner, r_out=ring_outer)
    ring_values = annulus.to_mask(method="center").get_values(plane)
    expected_ring_pixels = math.pi * (ring_outer**2 - ring_inner**2)
    if ring_values.size < MINIMUM_RING_FILL * expected_ring_pixels:
        return None
    if not np.all(np.isfinite(ring_values)) or np.any(ring_values == 0):
        return None
    aperture_values = aperture.to_mask(method="exact").get_values(plane)
    if not np.all(np.isfinite(aperture_values)) or np.any(aperture_values == 0):
        return None
    return float(aperture_values.sum() - aperture.area * np.median(ring_values))


def read_stack_plane(stack_path: str, dtype: type[np.floating] = np.float64) -> np.ndarray | None:
    """Read a stack as one 2-D float image.

    Parameters
    ----------
    stack_path : `str`
        The stack file.
    dtype : `type`, optional
        The floating-point type of the image. 64-bit by default; a caller that
        keeps two large planes at once (the half-stack jobs) asks for 32-bit,
        which is the type Siril writes.

    Returns
    -------
    plane : `numpy.ndarray` or `None`
        The image (color channels averaged), or `None` when the file holds no
        pixels.
    """
    data = read_data(stack_path)
    if data is None:
        return None
    return collapse_to_2d(np.asarray(data, dtype=dtype))


def sky_level_of_plane(plane: np.ndarray) -> float | None:
    """Give the sky level of a stack image: the median of its usable pixels.

    Pixels that are exactly zero (the empty edge of a registered stack) and
    pixels that are not finite are left out. Stars cover a small share of the
    stack, so the median is the sky.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The stack as one 2-D image (the noise step reads it once and passes
        it on).

    Returns
    -------
    level : `float` or `None`
        The median in the stack's own units, or `None` when the image holds no
        usable pixel or the median is not above zero.
    """
    usable = plane[np.isfinite(plane) & (plane != 0)]
    if usable.size == 0:
        return None
    level = float(np.median(usable))
    return level if level > 0 else None


def celestial_wcs(header: fits.Header) -> WCS | None:
    """Read a header's sky coordinate solution, if it has one.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        A FITS header.

    Returns
    -------
    wcs : `astropy.wcs.WCS` or `None`
        The two sky axes, or `None` when the header has no usable solution.
    """
    try:
        wcs = WCS(header)
    except ValueError, KeyError:
        return None
    if not wcs.has_celestial:
        return None
    return wcs.celestial


def pixel_scale_arcsec(wcs: WCS) -> float:
    """Give the mean pixel scale of a solution.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        A sky solution.

    Returns
    -------
    scale : `float`
        Arcseconds per pixel.
    """
    return float(np.mean(proj_plane_pixel_scales(wcs)) * 3600.0)


def pixels_to_sky(wcs: WCS, pixels: np.ndarray) -> np.ndarray:
    """Convert (row, column) pixel positions to (RA, Dec) in degrees.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky solution.
    pixels : `numpy.ndarray`
        Shape ``(n, 2)``, (row, column).

    Returns
    -------
    sky : `numpy.ndarray`
        Shape ``(n, 2)``, (RA, Dec) in degrees.
    """
    if len(pixels) == 0:
        return np.empty((0, 2))
    ra, dec = wcs.pixel_to_world_values(pixels[:, 1], pixels[:, 0])
    return np.column_stack([ra, dec]).astype(float)


def sky_to_pixels(wcs: WCS, sky: np.ndarray) -> np.ndarray:
    """Convert (RA, Dec) in degrees to (column, row) pixel positions.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The sky solution.
    sky : `numpy.ndarray`
        Shape ``(n, 2)``, (RA, Dec) in degrees.

    Returns
    -------
    pixels : `numpy.ndarray`
        Shape ``(n, 2)``, (column, row).
    """
    if len(sky) == 0:
        return np.empty((0, 2))
    column, row = wcs.world_to_pixel_values(sky[:, 0], sky[:, 1])
    return np.column_stack([column, row]).astype(float)
