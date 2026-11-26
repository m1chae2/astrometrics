"""Tools to check how bright the sky background is in an image.

This file only handles reading the image data from the hard drive and
measuring how bright the background is. The actual decision about
whether that brightness has changed too much during a stacking session
is kept separately, in `stacking/pre_processing/background_homogeneity.py`.

This is a documented sibling of the library's one sky measurement,
`image_scaling.measure_sky_background`. The level here is stored with every
raw frame under a method number (`BACKGROUND_LEVEL_METHOD`) and compared
across frames and nights, so its estimator stays fixed: the sigma-clipped
median of every pixel of the raw frame (`astropy.stats.sigma_clipped_stats`,
whose spread is the plain standard deviation). `measure_sky_background`
samples a grid and clips with the robust spread, which suits a stacked
picture with a large galaxy in it; switching the stored level to it would
need a new method number and a fresh measurement of every frame.
"""

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib._internal.drivers.fits_access import (
    collapse_to_2d,
    read_data,
    read_header_and_data,
)
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.shared.quality.star_profile_fitting import BAYER_CELL_SIZE_PX

# The version of the method that gives a frame's stored background level
# (`FrameMeasurements.background_level_method`). Level 1 was the sigma-clipped
# median of every pixel of the frame as stored, so a raw color mosaic's red,
# green and blue pixels were mixed. Level 2 (this file) takes the clipped
# median of 2 x 2 super-pixels for a raw mosaic and is the same as level 1 for
# any other frame. A level of one method must never be compared with a level
# of the other: on real D5300 frames the new level is 0.6 to 8.7 percent lower
# (STK-04). Raise this number when the estimator changes again, so frames
# measured earlier are measured again.
BACKGROUND_LEVEL_METHOD = 2


def is_color_mosaic(header: fits.Header, data: np.ndarray) -> bool:
    """Say whether an image is a raw color mosaic (one plane, a Bayer pattern).

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header of the HDU that holds the pixels.
    data : `numpy.ndarray`
        The pixels.

    Returns
    -------
    is_mosaic : `bool`
        `True` when the header names a Bayer pattern (``BAYERPAT``) and the
        pixels are one 2-D plane.
    """
    return bool(str(header.get("BAYERPAT", "")).strip()) and data.ndim == 2


def measure_background_of_pixels(plane: np.ndarray, color_mosaic: bool) -> float:
    """Calculate the sky background level of one 2-D plane of pixels.

    The level is the median of the pixels left after sigma clipping at 3 sigma
    (astropy's default of up to 5 passes). A raw color mosaic is first
    averaged over each 2 x 2 cell (one red, two green and one blue pixel).
    That removes the pattern of the color filters, so the level is the
    brightness of the sky averaged over the colors instead of a median of
    three differently bright sets of pixels, and the clipping runs over a
    quarter of the pixels, which makes it about 2.5 times faster on a 24
    megapixel frame (STK-04).

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels (integers or floats).
    color_mosaic : `bool`
        `True` for a raw color mosaic (see `is_color_mosaic`).

    Returns
    -------
    median_background : `float`
        The background level, in the units of the pixels.
    """
    if color_mosaic:
        rows = plane.shape[0] - plane.shape[0] % BAYER_CELL_SIZE_PX
        columns = plane.shape[1] - plane.shape[1] % BAYER_CELL_SIZE_PX
        cells = plane[:rows, :columns].reshape(
            rows // BAYER_CELL_SIZE_PX, BAYER_CELL_SIZE_PX, columns // BAYER_CELL_SIZE_PX, BAYER_CELL_SIZE_PX
        )
        plane = cells.mean(axis=(1, 3), dtype=np.float64)
    _, median, _ = sigma_clipped_stats(np.asarray(plane, dtype=float), sigma=3.0)
    return float(median)


def measure_frame_background_level(path: str) -> float | None:
    """Calculate the average brightness of the sky background in an image.

    This ignores the stars and only measures the dark background part
    of the image. A raw color mosaic is measured on 2 x 2 super-pixels (see
    `measure_background_of_pixels`). The result is of method
    `BACKGROUND_LEVEL_METHOD`.

    Parameters
    ----------
    path : `str`
        The file path to the image to check.

    Returns
    -------
    median_background : `float` or `None`
        The average background brightness number. Returns None if the
        file is empty.
    """
    header, data = read_header_and_data(path, first_hdu_only=True)
    if data is None:
        return None
    color_mosaic = is_color_mosaic(header, data)
    return measure_background_of_pixels(collapse_to_2d(data), color_mosaic)


def measure_frame_saturated_pixel_fraction(path: str, saturation_threshold_adu: float) -> float | None:
    """Calculate what percentage of the image is completely blown out (white).

    Cameras can only record so much light before a pixel maxes out and
    just records pure white (saturation). This checks how much of the
    image has hit that maximum limit.

    Parameters
    ----------
    path : `str`
        The file path to the image to check.
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated. Take it from
        the camera's profile.

    Returns
    -------
    saturated_fraction : `float` or `None`
        A number between 0.0 and 1.0 representing the percentage of blown out
        pixels. Returns None if the file is empty.
    """
    data = read_data(path, first_hdu_only=True)
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    data = collapse_to_2d(data)
    return compute_saturated_pixel_fraction(data, saturation_threshold_adu)
