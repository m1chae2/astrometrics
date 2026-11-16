"""Purpose: Fit a straight line and keep its slope only when it is real.

Description: Two drift measurements (the drift of a target's plate solves
during alignment, and the drift of one piece of a guiding excursion) fit a
straight line to offsets against time. A slope fitted to noise looks as real
as a true drift, so `significant_slope` also estimates the slope's standard
error from the scatter about the line, and keeps the slope only when it is
more than a stated number of standard errors from zero. At least three points
are needed: two fix the line and leave no scatter to judge it by.
"""

import math
from collections.abc import Sequence

# The fewest points for which a fitted slope has a standard error: a line has
# two parameters, so three points leave one degree of freedom.
MINIMUM_POINTS_FOR_SLOPE_ERROR = 3


def fit_slope(times: Sequence[float], values: Sequence[float]) -> tuple[float, float] | None:
    """Fit a straight line and give its slope with the slope's standard error.

    Parameters
    ----------
    times : `Sequence` [`float`]
        The times of the points.
    values : `Sequence` [`float`]
        The values at those times.

    Returns
    -------
    fit : `tuple` [`float`, `float`] or `None`
        The least-squares slope (value per unit of time) and its standard
        error, estimated from the scatter about the line. The standard error
        is 0 when the points lie exactly on the line. `None` when there are
        fewer than three points, the lengths differ, or the times do not vary.
    """
    count = len(times)
    if count < MINIMUM_POINTS_FOR_SLOPE_ERROR or count != len(values):
        return None
    mean_time = sum(times) / count
    mean_value = sum(values) / count
    spread = sum((time - mean_time) ** 2 for time in times)
    if spread <= 0.0:
        return None
    slope = sum((time - mean_time) * (value - mean_value) for time, value in zip(times, values, strict=True))
    slope /= spread
    intercept = mean_value - slope * mean_time
    residual_sum = sum(
        (value - (intercept + slope * time)) ** 2 for time, value in zip(times, values, strict=True)
    )
    return slope, math.sqrt(residual_sum / (count - 2) / spread)


def significant_slope(times: Sequence[float], values: Sequence[float], sigmas: float) -> float:
    """Fit a straight line and return its slope if it is significant.

    Parameters
    ----------
    times : `Sequence` [`float`]
        The times of the points.
    values : `Sequence` [`float`]
        The values at those times.
    sigmas : `float`
        How many standard errors the slope must stand from zero.

    Returns
    -------
    slope : `float`
        The least-squares slope (value per unit of time), or 0 when there are
        fewer than three points, the times do not vary, or the slope is within
        ``sigmas`` standard errors of zero.
    """
    fit = fit_slope(times, values)
    if fit is None:
        return 0.0
    slope, standard_error = fit
    if standard_error <= 0.0:
        # The points lie exactly on the line, so the slope is exact.
        return slope
    return slope if abs(slope) > sigmas * standard_error else 0.0


__all__ = ["MINIMUM_POINTS_FOR_SLOPE_ERROR", "fit_slope", "significant_slope"]
