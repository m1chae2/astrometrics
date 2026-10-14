"""Angle helpers that more than one pipeline needs.

Right Ascension (RA, the east-west sky coordinate) is an angle that wraps
from 360 deg back to 0 deg. Any code that compares two RA values, or
builds a search box around one, has to handle that wrap. The asteroid
detector and the astrometry star identifier both do, so the shared
helper lives here.
"""

from typing import overload

import numpy as np


@overload
def wrapped_ra_difference_deg(ra1_deg: float, ra2_deg: float) -> float: ...


@overload
def wrapped_ra_difference_deg(ra1_deg: np.ndarray, ra2_deg: float | np.ndarray) -> np.ndarray: ...


def wrapped_ra_difference_deg(ra1_deg: float | np.ndarray, ra2_deg: float | np.ndarray) -> float | np.ndarray:
    """Find the signed difference between two Right Ascensions (RA).

    RA is an angle that wraps around at 0/360 deg: 359.9 deg and
    0.1 deg are only 0.2 deg apart, but plain subtraction gives 359.8 deg.
    This function returns the shorter way around the circle. The sign
    tells the direction: positive when the first RA lies east of
    (greater than) the second. Every place in the pipelines that compares
    two RA values should call this function instead of subtracting.

    Parameters
    ----------
    ra1_deg : `float` or `numpy.ndarray`
        The first Right Ascension, in degrees. Any value is accepted,
        including values outside [0, 360).
    ra2_deg : `float` or `numpy.ndarray`
        The second Right Ascension, in degrees.

    Returns
    -------
    difference_deg : `float` or `numpy.ndarray`
        ``ra1_deg - ra2_deg`` wrapped into the range (-180, 180], in
        degrees. For example, 359.9 and 0.1 give -0.2, and 10 and 350
        give 20. The result is a ``float`` when both inputs are
        ``float``, and an array otherwise.
    """
    difference_deg = 180.0 - np.mod(180.0 - (np.asarray(ra1_deg) - np.asarray(ra2_deg)), 360.0)
    if difference_deg.ndim == 0:
        return float(difference_deg)
    return difference_deg
