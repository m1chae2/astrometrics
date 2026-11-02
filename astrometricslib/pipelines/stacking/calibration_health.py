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
    diagnostics: dict,
) -> None:
    """Measure whether calibration left the stack mostly zeros.

    Two signs are used: the share of exactly-zero pixels in the stack (for a
    colour stack, the share in its worst channel), and the
    worst "many negative pixels" percentage Siril printed after subtracting the
    dark. Both are recorded for every stack. The output gates compare them
    with their limits (a lower zero limit applies to spectral stacks).

    Parameters
    ----------
    summary : `StackQualitySummary`
        The summary being built; its metrics are set in place.
    stacked_path : `str`
        The stacked image to measure.
    diagnostics : `dict`
        What the stacking run reported.
    """
    import numpy as np

    from astrometricslib._internal.drivers.fits_access import read_data

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
    negative_percent = diagnostics.get("negative_pixel_max_percent")
    if negative_percent is not None:
        metrics.negative_pixel_max_percent = int(negative_percent)
