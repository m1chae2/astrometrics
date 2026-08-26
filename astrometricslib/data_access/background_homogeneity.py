"""Tools to check how bright the sky background is in an image.

This file only handles reading the image data from the hard drive.
The actual math to decide if the background is changing too much is
kept separately in the target tasks folder.
"""

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.tasks.shared.saturation_analysis import compute_saturated_pixel_fraction

# Just under the raw 16-bit unsigned max -- same convention as
# image_quality_metrics.DEFAULT_SATURATION_ADU_THRESHOLD and
# photometry's _SATURATION_ADU_THRESHOLD, not reused directly to keep
# this module's import surface matching
# measure_frame_background_level's existing minimal footprint.
_SATURATION_ADU_THRESHOLD = 65000.0


def measure_frame_background_level(path: str) -> float | None:
    """Calculate the average brightness of the sky background in an image.

    This ignores the stars and only measures the dark background part
    of the image.

    Parameters
    ----------
    path : `str`
        The file path to the image we want to check.

    Returns
    -------
    median_background : `float` or `None`
        The average background brightness number. Returns None if the
        file is empty.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)
    _, median, _ = sigma_clipped_stats(data, sigma=3.0)
    return float(median)


def measure_frame_saturated_pixel_fraction(path: str) -> float | None:
    """Calculate what percentage of the image is completely blown out (white).

    Cameras can only record so much light before a pixel maxes out and
    just records pure white (saturation). This checks how much of the
    image has hit that maximum limit.

    Parameters
    ----------
    path : `str`
        The file path to the image to check.

    Returns
    -------
    saturated_fraction : `float` or `None`
        A number between 0.0 and 1.0 representing the percentage of blown out
        pixels. Returns None if the file is empty.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)
    return compute_saturated_pixel_fraction(data, _SATURATION_ADU_THRESHOLD)
