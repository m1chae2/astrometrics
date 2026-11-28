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

Two helpers build on these. `sigma_clipped_values` is the library's one
clipping loop: it drops the values that lie more than a set number of robust
spreads from the median, again and again, until none is dropped.
`median_or_none` takes the median of the known values in a list that may
hold gaps.

A camera frame holds whole numbers, often in steps of 4 (a 14-bit sensor stored
in 16 bits), and a sum of a few pixels lies on a finer but still regular grid.
The median and the MAD of such numbers can only land on the grid, so they jump
from one value to the next while the true noise changes smoothly.
`smooth_median_and_sigma` gives the same two numbers for those values by
spreading each value evenly over its grid cell. For numbers that are not
quantized it gives the ordinary median and MAD.

This module sits in `utilities/` so the drivers (the tuners read stored
quality rows there) and every pipeline can import it.
"""

import math
import warnings
from collections.abc import Iterable, Sequence

import numpy as np

__all__ = [
    "MAD_TO_SIGMA",
    "MEDIAN_DISTANCE_TO_AXIS_SIGMA",
    "median_or_none",
    "per_axis_sigma_from_distances",
    "robust_sigma",
    "sigma_clipped_values",
    "smooth_median_and_sigma",
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


def median_or_none(values: Iterable[float | None]) -> float | None:
    """Take the median of the values that are known.

    Parameters
    ----------
    values : `Iterable` [`float` or `None`]
        The values. `None` marks a value that was not measured and is left
        out.

    Returns
    -------
    median : `float` or `None`
        The median of the known values, or `None` when no value is known.
    """
    known = [float(value) for value in values if value is not None]
    return float(np.median(known)) if known else None


def sigma_clipped_values(
    values: Sequence[float] | np.ndarray, *, minimum_count: int, maximum_passes: int, clip_sigma: float
) -> np.ndarray | None:
    """Drop outlying values, again and again, until none is dropped.

    Each pass finds the median and the robust spread (`robust_sigma`) of the
    values still kept, then drops every value farther than `clip_sigma`
    spreads from the median. The passes stop when nothing more is dropped,
    when the spread is zero, when a pass would leave fewer than
    `minimum_count` values (that pass is not applied), or after
    `maximum_passes` passes.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        The values, any shape. Values that are not finite are left out.
    minimum_count : `int`
        The fewest finite values that give a result, and the fewest a pass
        may keep.
    maximum_passes : `int`
        The most clipping passes.
    clip_sigma : `float`
        How many robust spreads from the median a value may lie and stay.

    Returns
    -------
    kept : `numpy.ndarray` or `None`
        The kept values, 1-D and `float64`. `None` when fewer than
        `minimum_count` finite values are given.
    """
    kept = np.asarray(values, dtype=np.float64).ravel()
    kept = kept[np.isfinite(kept)]
    if kept.size < minimum_count:
        return None
    for _ in range(maximum_passes):
        median = float(np.median(kept))
        spread = robust_sigma(kept)
        if not spread > 0.0:
            break
        clipped = kept[np.abs(kept - median) <= clip_sigma * spread]
        if clipped.size == kept.size or clipped.size < minimum_count:
            break
        kept = clipped
    return kept


# The values are treated as quantized (on a grid) when each distinct value
# is shared by at least this many numbers on average. Then the grid step is
# larger than the median's own sampling error (about 1.25 sigma / sqrt(n)),
# so the ordinary median would show the grid. With fewer sharing numbers the
# grid is finer than that error and the ordinary median is smooth enough, and
# the (slower) grid path is skipped, which also leaves the result of float
# stacks unchanged. A method choice checked on raw ZWO and Nikon frames
# (see validation entry STK-D45).
_QUANTIZED_MINIMUM_SHARING = 4

# Two values closer than this fraction of the largest magnitude are one value
# (float round-off from averaging whole numbers is about 1e-13 of it).
_SAME_VALUE_FRACTION = 1e-9


def smooth_median_and_sigma(values: Sequence[float] | np.ndarray) -> tuple[float, float]:
    """Find the median and robust spread of numbers that sit on a grid.

    Raw camera pixels are whole numbers (or whole numbers averaged), so the
    ordinary median and MAD can only land on grid values and do not follow
    small changes of the true level and noise. This function spreads each
    value evenly over its grid cell (the cell is as wide as the smallest gap
    between distinct values), which gives a continuous distribution, then
    reads the median and the MAD from it. The spread is `MAD_TO_SIGMA` times
    the MAD, as in `robust_sigma`. The spreading adds a variance of step
    squared over 12 to numbers that were already rounded to the grid, which
    is under 0.3 percent of a spread of 25 on a step of 4 and is ignored.
    When the numbers are not quantized (fewer
    than `_QUANTIZED_MINIMUM_SHARING` numbers per distinct value) the result
    is the ordinary median and `robust_sigma`.

    Parameters
    ----------
    values : `Sequence` [`float`] or `numpy.ndarray`
        The numbers. Values that are not finite are left out.

    Returns
    -------
    median, sigma : `float`
        The median and the spread. Both are NaN when no number is finite.
    """
    array = np.asarray(values, dtype=np.float64).ravel()
    array = array[np.isfinite(array)]
    if array.size == 0:
        return float("nan"), float("nan")
    levels, counts = np.unique(array, return_counts=True)
    tolerance = _SAME_VALUE_FRACTION * float(np.max(np.abs(levels)))
    gaps = np.diff(levels)
    if (
        levels.size < 2
        or array.size < _QUANTIZED_MINIMUM_SHARING * levels.size
        or not np.any(gaps > tolerance)
    ):
        return float(np.median(array)), float(robust_sigma(array))
    # Merge values that differ only by round-off, then take the grid step.
    keep = np.concatenate(([True], gaps > tolerance))
    levels = levels[keep]
    counts = np.add.reduceat(counts, np.flatnonzero(keep))
    step = float(np.min(np.diff(levels))) if levels.size > 1 else 0.0
    if levels.size < 2 or not step > 0.0:
        return float(np.median(array)), float(robust_sigma(array))
    # The continuous distribution is flat inside each cell of width `step`
    # around a value, and empty between cells. Its cumulative share is
    # piecewise linear; these are the corner points.
    corners_x = np.empty(2 * levels.size)
    corners_x[0::2] = levels - 0.5 * step
    corners_x[1::2] = levels + 0.5 * step
    shares = np.cumsum(counts) / float(array.size)
    corners_f = np.empty(2 * levels.size)
    corners_f[0::2] = shares - counts / float(array.size)
    corners_f[1::2] = shares
    median = _inverse_share(0.5, corners_f, corners_x)

    def inside(radius: float) -> float:
        """Give the share of the numbers within a radius of the median.

        Returns
        -------
        share : `float`
            The share, between 0 and 1.
        """
        low = float(np.interp(median - radius, corners_x, corners_f))
        high = float(np.interp(median + radius, corners_x, corners_f))
        return high - low

    lower, upper = 0.0, float(levels[-1] - levels[0]) + step
    for _ in range(60):
        middle = 0.5 * (lower + upper)
        if inside(middle) < 0.5:
            lower = middle
        else:
            upper = middle
    return median, MAD_TO_SIGMA * 0.5 * (lower + upper)


def _inverse_share(share: float, corners_f: np.ndarray, corners_x: np.ndarray) -> float:
    """Find the value below which a given share of a gridded distribution lies.

    Parameters
    ----------
    share : `float`
        The cumulative share, between 0 and 1.
    corners_f : `numpy.ndarray`
        The cumulative share at each corner point; it is flat across the empty
        gaps between cells.
    corners_x : `numpy.ndarray`
        The values of the corner points, increasing.

    Returns
    -------
    value : `float`
        The value where the cumulative share first reaches ``share``. Inside
        a flat stretch (an empty gap) the first value is taken, so the answer
        always lies inside a cell.
    """
    index = int(np.searchsorted(corners_f, share, side="left"))
    index = min(max(index, 1), corners_f.size - 1)
    f_low, f_high = corners_f[index - 1], corners_f[index]
    if not f_high > f_low:
        return float(corners_x[index])
    return float(
        corners_x[index - 1] + (share - f_low) / (f_high - f_low) * (corners_x[index] - corners_x[index - 1])
    )
