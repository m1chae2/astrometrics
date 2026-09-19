"""Tools to check if a photo is too bright (overexposed).

This counts how many pixels are completely maxed out (pure white). The
photo isn't thrown away just because a few stars are too bright, but
a warning flag is left so later steps know those specific stars can't
be measured accurately.
"""

import numpy as np


def compute_saturated_pixel_fraction(data: np.ndarray, saturation_threshold: float) -> float:
    """Calculate what percentage of the image is completely blown out.

    Parameters
    ----------
    data : `numpy.ndarray`
        The actual pixels of the image.
    saturation_threshold : `float`
        The brightness level that counts as 'maxed out'. This changes
        depending on which camera took the picture.

    Returns
    -------
    saturated_fraction : `float`
        The percentage of pixels that are too bright (from 0.0 to 1.0).
    """
    if data.size == 0:
        return 0.0
    return float(np.count_nonzero(data >= saturation_threshold) / data.size)


def is_saturation_significant(saturated_fraction: float, flag_threshold: float = 0.001) -> bool:
    """Decide if there are enough overexposed pixels to warn the user about it.

    Parameters
    ----------
    saturated_fraction : `float`
        The percentage of maxed-out pixels found.
    flag_threshold : `float`, optional
        How much overexposure is 'too much'. Default is 0.1%. (Note: this
        is just a guess right now and might need to be adjusted later).

    Returns
    -------
    is_significant : `bool`
        True if the image is too bright, False if it's fine.
    """
    return saturated_fraction >= flag_threshold
