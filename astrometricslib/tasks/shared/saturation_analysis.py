"""Saturation soft-flagging for photometry consumers.

Computes the fraction of saturated pixels in a frame or stack, as
metadata for downstream photometry consumers -- flagged, not used to
fail or exclude anything, since a partially-saturated frame can still
be scientifically useful for everything except the saturated stars
themselves.
"""

import numpy as np


def compute_saturated_pixel_fraction(data: np.ndarray, saturation_threshold: float) -> float:
    """Return the fraction of pixels at or above saturation_threshold.

    Parameters
    ----------
    data : `numpy.ndarray`
        The pixel data of the frame or stack to inspect.
    saturation_threshold : `float`
        The ADU value at or above which a pixel is considered
        saturated. This is the caller's responsibility (e.g. derived
        from the sensor's bit depth), since it varies by camera --
        there's no single correct ADU constant across the 16-bit ZWO
        and DSLR-RAW sources this pipeline handles.

    Returns
    -------
    saturated_fraction : `float`
        The fraction of pixels in data at or above
        saturation_threshold. Returns 0.0 if data is empty.
    """
    if data.size == 0:
        return 0.0
    return float(np.count_nonzero(data >= saturation_threshold) / data.size)


def is_saturation_significant(saturated_fraction: float, flag_threshold: float = 0.001) -> bool:
    """Return whether the saturated fraction is worth flagging.

    Parameters
    ----------
    saturated_fraction : `float`
        The fraction of saturated pixels, as returned by
        `compute_saturated_pixel_fraction`.
    flag_threshold : `float`, optional
        The fraction at or above which saturation is considered
        significant, default 0.001 (0.1%). This is an unvalidated
        placeholder -- unlike the sigma/filter defaults in
        rejection_thresholds.py, this has not yet been cross-checked
        against a real session with known saturated stars. Treat it
        as a starting point, not a derived constant.

    Returns
    -------
    is_significant : `bool`
        `True` if saturated_fraction is at or above flag_threshold,
        `False` otherwise.
    """
    return saturated_fraction >= flag_threshold
