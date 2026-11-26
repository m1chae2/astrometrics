"""Small statistics that the two spectral-detector tuners share.

The emission-line detector and the absorption-feature detector each tune a
polynomial degree and a fewest-points count from stored spectra. Both ask the
same kinds of questions: how well does a polynomial of this degree predict
samples it did not see, how steady is the noise estimate from this many
samples, and which is the smallest candidate value that a stated share of
real cases passes. The functions here answer those on plain arrays, so they
can be tested without a library.
"""

from collections.abc import Sequence

import numpy as np

from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA, robust_sigma


def smallest_passing_value(
    candidates: Sequence[float], pass_shares: Sequence[float | None], required_share: float
) -> float | None:
    """Find the smallest candidate that it and every larger one pass with.

    Parameters
    ----------
    candidates : `Sequence` [`float`]
        The candidate values in increasing order. A larger candidate is
        taken to be easier to pass.
    pass_shares : `Sequence` [`float` or `None`]
        For each candidate, the share of real cases that passed, or `None`
        when no case could be tested at that candidate. A `None` is skipped.
    required_share : `float`
        The share of cases that must pass.

    Returns
    -------
    value : `float` or `None`
        The smallest candidate from which every tested candidate passes, or
        `None` when the largest tested candidate fails or none was tested.
    """
    best = None
    for candidate, share in zip(reversed(candidates), reversed(pass_shares), strict=True):
        if share is None:
            continue
        if share < required_share:
            break
        best = candidate
    return best


def blocked_cross_validation_error(
    positions: np.ndarray, values: np.ndarray, degree: int, block_count: int
) -> float | None:
    """Measure how well a polynomial predicts blocks it was not fitted to.

    The samples are cut into contiguous blocks. Each block in turn is held
    out, a polynomial is fitted to the rest, and the held-out block is
    predicted. Holding out whole blocks tests the polynomial across a wide
    stretch of the spectrum, not just between neighboring samples, which is
    how a high degree that bends into a hump shows itself.

    Parameters
    ----------
    positions : `numpy.ndarray`
        The sample positions, in increasing order.
    values : `numpy.ndarray`
        The brightness at each position.
    degree : `int`
        The polynomial degree.
    block_count : `int`
        The number of blocks.

    Returns
    -------
    error : `float` or `None`
        The root-mean-square prediction error divided by the absolute
        median brightness, or `None` when a fit has too few samples or the
        median is zero.
    """
    count = len(positions)
    edges = np.linspace(0, count, block_count + 1).astype(int)
    errors = []
    for block in range(block_count):
        held_out = np.zeros(count, dtype=bool)
        held_out[edges[block] : edges[block + 1]] = True
        if (~held_out).sum() <= degree + 1 or not held_out.any():
            return None
        coefficients = np.polyfit(positions[~held_out], values[~held_out], degree)
        errors.append(values[held_out] - np.polyval(coefficients, positions[held_out]))
    scale = abs(float(np.median(values)))
    if scale <= 0.0:
        return None
    return float(np.sqrt(np.mean(np.concatenate(errors) ** 2)) / scale)


def smallest_degree_within_tolerance(errors_by_degree: dict[int, float], tolerance: float) -> int | None:
    """Pick the lowest degree whose error is close to the best error.

    Parameters
    ----------
    errors_by_degree : `dict` [`int`, `float`]
        The typical prediction error at each candidate degree.
    tolerance : `float`
        How much worse than the best error a lower degree may be, as a
        fraction of the best error.

    Returns
    -------
    degree : `int` or `None`
        The lowest degree with error at most ``(1 + tolerance)`` times the
        best, or `None` when there are no errors.
    """
    if not errors_by_degree:
        return None
    best = min(errors_by_degree.values())
    for degree in sorted(errors_by_degree):
        if errors_by_degree[degree] <= (1.0 + tolerance) * best:
            return degree
    return None


def window_variance_spread(
    positions: np.ndarray, values: np.ndarray, count: int, degree: int
) -> float | None:
    """Measure how much the noise estimate varies between windows of one size.

    The spectrum is cut into consecutive windows of ``count`` samples. In
    each, a polynomial is fitted and the variance of what is left is taken
    (divided by the samples left after the fit). The spread of those
    variances, relative to their median, says how steady a noise estimate
    from ``count`` samples is.

    Parameters
    ----------
    positions : `numpy.ndarray`
        The sample positions, in increasing order.
    values : `numpy.ndarray`
        The brightness at each position.
    count : `int`
        The samples in each window.
    degree : `int`
        The polynomial degree fitted in each window.

    Returns
    -------
    spread : `float` or `None`
        The scaled median absolute deviation of the window variances divided
        by their median, or `None` when fewer than three whole windows fit
        or a window has no samples left after the fit.
    """
    window_total = len(positions) // count
    if window_total < 3 or count <= degree + 1:
        return None
    variances = []
    for window in range(window_total):
        selection = slice(window * count, (window + 1) * count)
        local_positions = positions[selection] - positions[selection].mean()
        coefficients = np.polyfit(local_positions, values[selection], degree)
        residual = values[selection] - np.polyval(coefficients, local_positions)
        variances.append(float(residual @ residual) / (count - degree - 1))
    median_variance = float(np.median(variances))
    if median_variance <= 0.0:
        return None
    deviation = MAD_TO_SIGMA * float(np.median(np.abs(np.asarray(variances) - median_variance)))
    return deviation / median_variance


def point_to_point_noise(values: np.ndarray) -> float:
    """Estimate the sample-to-sample noise of a smooth curve.

    Parameters
    ----------
    values : `numpy.ndarray`
        The brightness at each sample, at least two.

    Returns
    -------
    noise : `float`
        The scaled median absolute value of the first differences, divided
        by the square root of two. A smooth curve cancels in the
        differences, so only the noise is left.
    """
    differences = np.diff(values)
    return robust_sigma(differences) / np.sqrt(2.0)


def raw_noise_inflation(residual: np.ndarray) -> float:
    """Work out how much neighboring samples' shared noise widens error bars.

    Parameters
    ----------
    residual : `numpy.ndarray`
        The fit residuals, in order.

    Returns
    -------
    inflation : `float`
        ``(1 + r) / (1 - r)`` for the lag-one correlation ``r`` of the
        residuals (kept between 0 and 0.999), with no upper cap. At least 1.
    """
    centered = residual - residual.mean()
    denominator = float(centered @ centered)
    if denominator <= 0.0:
        return 1.0
    lag_one = float(centered[:-1] @ centered[1:]) / denominator
    lag_one = min(max(lag_one, 0.0), 0.999)
    return (1.0 + lag_one) / (1.0 - lag_one)


def widest_gap_midpoint(values: Sequence[float], minimum_count: int) -> float | None:
    """Find the middle of the widest gap between sorted values.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The values.
    minimum_count : `int`
        The fewest values for a gap to mean anything.

    Returns
    -------
    midpoint : `float` or `None`
        The middle of the widest gap between neighboring values, or `None`
        when there are fewer than ``minimum_count`` values.
    """
    if len(values) < max(minimum_count, 2):
        return None
    ordered = np.sort(np.asarray(values, dtype=float))
    gaps = np.diff(ordered)
    index = int(np.argmax(gaps))
    return float(0.5 * (ordered[index] + ordered[index + 1]))
