"""Tools for measuring image quality.

This module contains functions to measure how good an image is, like
how blurry the stars are (FWHM) or how many pixels are completely white
(saturated). These help the program decide which images to keep and
which ones to throw away.
"""

import logging
import os

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.pipelines.shared.quality.saturation import (
    compute_saturated_pixel_fraction,
    compute_stack_saturated_pixel_fraction,
)

logger = logging.getLogger(__name__)


def measure_frame_input_quality(
    path: str, include_fwhm: bool = False, *, saturation_threshold_adu: float
) -> dict[str, float | None]:
    """Measure the basic quality of an image before trying to stack it.

    This checks the background noise level and how much of the image is
    saturated (completely white). It can optionally measure star blurriness
    (FWHM) too, but that takes much longer.

    Parameters
    ----------
    path : `str`
        The file path to the image.
    include_fwhm : `bool`, optional
        If True, it also measures star blurriness (which is slow).
        Defaults to False.
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated. Take it from
        the camera's profile.

    Returns
    -------
    metrics : `dict`
        A dictionary with the measurements. Any measurement that
        failed will be None.
    """
    metrics: dict[str, float | None] = {
        "background_level": None,
        "saturated_pixel_fraction": None,
        "fwhm_px": None,
    }

    try:
        with fits.open(path, memmap=False) as hdul:
            data = hdul[0].data
            if data is None and len(hdul) > 1:
                data = hdul[1].data
            if data is None:
                return metrics
            data = np.asarray(data, dtype=float)
    except Exception as read_error:
        logger.debug("Could not read %s for input-quality measurement: %s", path, read_error)
        return metrics

    # Matches the mono-flattening the existing per-frame measurements use,
    # so a value measured here is comparable with one measured there.
    data = collapse_to_2d(data)

    try:
        _, median, _ = sigma_clipped_stats(data, sigma=3.0)
        metrics["background_level"] = float(median)
    except Exception as background_error:
        logger.debug("Background measurement failed for %s: %s", path, background_error)

    try:
        metrics["saturated_pixel_fraction"] = compute_saturated_pixel_fraction(data, saturation_threshold_adu)
    except Exception as saturation_error:
        logger.debug("Saturation measurement failed for %s: %s", path, saturation_error)

    if include_fwhm:
        try:
            from astrometricslib.pipelines.astrometry.fwhm import measure_fwhm_from_data

            metrics["fwhm_px"] = measure_fwhm_from_data(data)
        except Exception as fwhm_error:
            logger.debug("FWHM measurement failed for %s: %s", path, fwhm_error)

    return metrics


def measure_saturated_pixel_fraction(path: str) -> float | None:
    """Calculate what fraction of a stacked image is saturated.

    Siril writes stacks as 32-bit floats scaled so the brightest pixel is
    1.0, so a raw-frame ADU level (such as 65000) can never be reached.
    A stack is instead saturated where its pixels pile up at a ceiling; see
    `compute_stack_saturated_pixel_fraction`. For a raw frame, use the camera
    profile's `saturation_threshold_adu`.

    Parameters
    ----------
    path : `str`
        The file path to the stacked image.

    Returns
    -------
    fraction : `float` or `None`
        The fraction (between 0.0 and 1.0) of saturated pixels.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    return compute_stack_saturated_pixel_fraction(np.asarray(data, dtype=float))


def measure_rejected_fraction(stacked_path: str) -> float | None:
    """Calculate what percentage of pixels were thrown out during stacking.

    Siril creates a rejection map file when it stacks images. This reads that
    map to tell how much bad data (like satellites or clouds) had to be
    removed to make the final image.

    Parameters
    ----------
    stacked_path : `str`
        The file path to the final stacked image.

    Returns
    -------
    fraction : `float` or `None`
        The average fraction of rejected pixels. None if the rejection map
        file can't be found.
    """
    rejmap_path = os.path.splitext(stacked_path)[0] + "_RejMap.fits"
    if not os.path.exists(rejmap_path):
        return None
    with fits.open(rejmap_path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    return float(np.mean(data))
