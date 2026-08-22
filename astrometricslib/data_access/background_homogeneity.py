"""Per-frame FITS reads for the session background-homogeneity check.

The pure decision logic that operates on the resulting in-memory
values (detect_background_split) lives in
tasks/target_tasks/background_homogeneity_tasks.py instead --
everything in this module opens a FITS file from disk.
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
    """Return a frame's sigma-clipped median background level.

    Uses the full frame, matching exactly the method the
    DEFAULT_GAP_RATIO_THRESHOLD above was validated against.

    Parameters
    ----------
    path : `str`
        The filesystem path to the FITS frame to measure.

    Returns
    -------
    median_background : `Optional[float]`
        The sigma-clipped median background level, in the same units
        as the raw ADU. Returns `None` if the frame has no data.

    Notes
    -----
    A central-crop/memmap shortcut was tried to cut the ~1.4s/frame
    cost, but these cameras' FITS files use BZERO/BSCALE header
    scaling, which astropy can't memory-map (raises ValueError) --
    would need a raw partial read plus manual scale application to be
    a real optimization, not attempted here. At ~1.4s/frame this adds
    real but bounded overhead (e.g. ~100s for a 70-frame session),
    comparable to the stacking job itself.
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
    """Return the fraction of a raw frame's pixels at or above saturation.

    A sibling fact to measure_frame_background_level, gathered with the same
    file-open/mono-flatten pattern so a caller already paying that per-frame
    I/O cost (e.g. the background-homogeneity check) can compute both facts
    from one already-open frame's data.

    Parameters
    ----------
    path : `str`
        The filesystem path to the FITS frame to measure.

    Returns
    -------
    saturated_fraction : `Optional[float]`
        The fraction of pixels at or above the saturation threshold.
        Returns `None` if the frame has no data.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)
    return compute_saturated_pixel_fraction(data, _SATURATION_ADU_THRESHOLD)
