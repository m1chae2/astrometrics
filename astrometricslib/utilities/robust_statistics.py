"""The library's one robust spread: the scaled median absolute deviation.

A robust spread is a measure of how widely numbers scatter that a few stray
values (a star in a sky sample, a bad match in a list of star offsets) cannot
inflate. The library uses one: the median absolute deviation about the median
(MAD), times 1.4826. For numbers that follow a normal distribution (a bell
curve) this equals the standard deviation, because the middle half of a bell
curve lies within 0.6745 standard deviations of its center (1 / 0.6745 =
1.4826).

A second rule covers distances in a plane. When a star's measured position
misses its predicted position by a small error in each of the two axes, the
miss distance is always positive, so the MAD rule does not apply to it. For
equal bell-curve errors on both axes, half of the misses are shorter than
1.1774 times the standard deviation of one axis (the square root of 2 ln 2).
`per_axis_sigma_from_distances` divides the median miss by that number.

This module sits in `utilities/` so the drivers (the tuners read stored
quality rows there) and every pipeline can import it.
"""

import math
import warnings
from collections.abc import Sequence

import numpy as np

__all__ = [
    "MAD_TO_SIGMA",
    "MEDIAN_DISTANCE_TO_AXIS_SIGMA",
    "per_axis_sigma_from_distances",
    "robust_sigma",
]

# The factor that turns the median absolute deviation of normally spread
# numbers into one standard deviation, 1 / 0.6745. A constant of the normal
# distribution, not a tuned value.
MAD_TO_SIGMA = 1.4826

# The median length of a two-dimensional error whose two parts are
# independent bell curves of standard deviation 1: sqrt(2 ln 2). The length
# follows a Rayleigh distribution, whose median is this number. A constant,
# not a tuned value.
MEDIAN_DISTANCE_TO_AXIS_SIGMA = math.sqrt(2.0 * math.log(2.0))


def _median(values: np.ndarray, axis: int | None, keepdims: bool = False) -> np.ndarray:
    """Take a median, leaving out not-a-number values when there are any.

    Parameters
    ----------
    values : `numpy.ndarray`
        Floating-point numbers.
    axis : `int` or `None`
        The axis to take the median along, or `None` for all values.
    keepdims : `bool`, optional
        Keep the reduced axis with length one.

    Returns
    -------
    median : `numpy.ndarray`
        The median. A slice with no finite value gives NaN, without a warning.
    """
    if not np.isnan(values).any():
        return np.median(values, axis=axis, keepdims=keepdims)
    with warnings.catch_warnings():
        # A slice of only NaN values gives NaN; the caller reads that as "no
        # spread could be measured".
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmedian(values, axis=axis, keepdims=keepdims)


def robust_sigma(values: Sequence[float] | np.ndarray, axis: int | None = None) -> float | np.ndarray:
    """Measure a spread of numbers that a few stray values cannot move.

    The spread is `MAD_TO_SIGMA` times the median absolute deviation from the
    median of the same numbers (along the same axis). Not-a-number values are
    left out.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        The numbers.
    axis : `int`, optional
        The axis to measure along. By default all numbers are measured
        together.

    Returns
    -------
    sigma : `float` or `numpy.ndarray`
        One `float` when ``axis`` is `None`, else one spread for each position
        along the other axes. It equals the standard deviation for normally
        spread numbers. NaN when there is no finite number to measure.
    """
    array = np.asarray(values, dtype=np.float64)
    if array.size == 0:
        return float("nan") if axis is None else np.full(np.delete(array.shape, axis), np.nan)
    centers = _median(array, axis=axis, keepdims=True)
    spread = MAD_TO_SIGMA * _median(np.abs(array - centers), axis=axis)
    return float(spread) if axis is None else spread


def per_axis_sigma_from_distances(distances: Sequence[float] | np.ndarray) -> float:
    """Turn the lengths of two-dimensional misses into the error of one axis.

    Use this for the distance between a measured and a predicted position on
    an image, where the error in x and in y are alike. `robust_sigma` does
    not apply to such distances: they are all positive, so their median is
    not their center. The result is the median distance divided by
    `MEDIAN_DISTANCE_TO_AXIS_SIGMA`. A clip at k times this value drops the
    misses longer than k standard deviations of one axis. Not-a-number values
    are left out.

    Parameters
    ----------
    distances : `Sequence` [`float`] or `numpy.ndarray`
        The miss lengths, zero or positive.

    Returns
    -------
    sigma : `float`
        The standard deviation of the error along one axis. NaN when there is
        no finite distance.
    """
    array = np.asarray(distances, dtype=np.float64)
    if array.size == 0:
        return float("nan")
    return float(_median(array, axis=None)) / MEDIAN_DISTANCE_TO_AXIS_SIGMA
