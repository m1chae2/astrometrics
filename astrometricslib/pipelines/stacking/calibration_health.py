"""Purpose: Check that calibration did not leave a stack mostly zeros.

Description: A calibration mistake, such as subtracting the bias twice, can
leave a stack with large areas of exactly zero pixels or with many negative
pixels. This module measures those signs on the finished stack and sets the
fields and flags on the stack's quality summary.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from astrometricslib.models.quality.quality_summary import StackQualitySummary

logger = logging.getLogger(__name__)

__all__ = ["measure_calibration_health"]


def measure_calibration_health(
    summary: StackQualitySummary,
    stacked_path: str,
    is_spectral: bool,
    diagnostics: dict,
) -> None:
    """Record whether calibration left the stack mostly zeros.

    Two signs are used: the share of exactly-zero pixels in the stack (for a
    colour stack, the share in its worst channel), and the
    worst "many negative pixels" percentage Siril printed after subtracting the
    dark. Both are recorded for every stack. The zero share is flagged only for
    images, because the sky of a spectral stack is legitimately at or below
    zero.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The summary being built; its metrics are set in place.
    stacked_path : `str`
        The stacked image to measure.
    is_spectral : `bool`
        Whether the stack is spectral.
    diagnostics : `dict`
        What the stacking run reported.
    """
    import numpy as np

    from astrometricslib.drivers.fits_access import read_data
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        is_negative_pixel_percent_significant,
        is_zero_fraction_significant,
        zero_fraction_threshold,
    )

    metrics = summary.stacking_metrics
    try:
        data = np.asarray(read_data(stacked_path))
    except OSError as read_error:
        logger.warning("Could not read '%s' to check for a blank stack: %s", stacked_path, read_error)
    else:
        # A colour stack is judged by its worst channel: one dead channel
        # (the red of a Nikon stack whose bias was subtracted twice) gives a
        # strongly tinted picture even when the other channels are fine.
        planes = list(data) if data.ndim == 3 else [data]
        metrics.zero_pixel_fraction = max(
            float(np.count_nonzero(plane == 0) / plane.size) for plane in planes
        )
        metrics.zero_fraction_flagged = is_zero_fraction_significant(
            metrics.zero_pixel_fraction, zero_fraction_threshold(is_spectral)
        )
    negative_percent = diagnostics.get("negative_pixel_max_percent")
    if negative_percent is not None:
        metrics.negative_pixel_max_percent = int(negative_percent)
        metrics.negative_pixels_flagged = is_negative_pixel_percent_significant(negative_percent)
