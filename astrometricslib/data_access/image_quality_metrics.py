"""Tools for measuring image quality.

This module contains functions to measure how good an image is, like
how blurry the stars are (FWHM) or how many pixels are completely white
(saturated). These help the program decide which images to keep and
which ones to throw away.
"""

import logging
import os
import re

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.tasks.shared.saturation_analysis import compute_saturated_pixel_fraction

logger = logging.getLogger(__name__)

FWHM_MEASUREMENT_BOX_RADIUS_PX = 15
FWHM_MEASUREMENT_STAR_COUNT = 15

# Just under the raw 16-bit unsigned max (65535) -- see
# measure_saturated_pixel_fraction's docstring for how this was chosen and its
# caveats.
DEFAULT_SATURATION_ADU_THRESHOLD = 65000.0

# Photometric linearity threshold for 14-bit CMOS sensors mapped to 16-bit ADC
# (e.g. ZWO ASI533MM Pro). Non-linear compression begins above ~60,000 ADU at
# Gain 0 / Offset 10 before hard clipping (Antonov 2026).
DEFAULT_PHOTOMETRIC_LINEARITY_ADU_THRESHOLD = 60000.0


_REGISTRATION_LINE_PATTERN = re.compile(r"^R(\d+) ")


def parse_seq_file(seq_path: str) -> list[dict[str, float]]:
    """Read the registration results file from Siril.

    Siril creates a `.seq` file when it aligns images. This function reads
    that file to get the quality measurements (like how blurry the stars are)
    for each image.

    Parameters
    ----------
    seq_path : `str`
        The file path to the Siril `.seq` file.

    Returns
    -------
    frames : `list` of `dict`
        A list containing the quality measurements for each image. Returns
        an empty list if the file doesn't exist.
    """
    frames = []
    if not os.path.exists(seq_path):
        return frames
    with open(seq_path) as seq_file:
        for line in seq_file:
            if not _REGISTRATION_LINE_PATTERN.match(line):
                continue
            parts = line.split()
            frames.append({
                "fwhm_x": float(parts[1]),
                "fwhm_y": float(parts[2]),
                "roundness": float(parts[3]),
                "rmse": float(parts[5]),
                "nb_stars": int(parts[6]),
                "dx": float(parts[10]),
                "dy": float(parts[13]),
            })
    return frames


def parse_zero_order_star(lst_path: str) -> dict[str, float] | None:
    """Find information about the brightest star from a Siril list file.

    Siril writes a `.lst` file with information about all the stars it found.
    This function reads the file and returns information about the first
    (brightest) star, which is often used for spectroscopy alignment.

    Parameters
    ----------
    lst_path : `str`
        The file path to the Siril `.lst` file.

    Returns
    -------
    result : `dict` or `None`
        The star's properties, or None if the file doesn't exist or is empty.
    """
    if not os.path.exists(lst_path):
        return None
    with open(lst_path) as lst_file:
        for line in lst_file:
            if line.startswith("#") or not line.strip():
                continue
            fields = line.split("\t")
            background = float(fields[2])
            amplitude = float(fields[3])
            return {
                "background": background,
                "amplitude": amplitude,
                "peak_to_background_ratio": amplitude / background if background else None,
                "x": float(fields[5]),
                "y": float(fields[6]),
                "fwhm_x_px": float(fields[7]),
                "fwhm_y_px": float(fields[8]),
                "rmse": float(fields[12]),
            }
    return None


def measure_image_fwhm(path: str, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT) -> float | None:
    """Measure the average blurriness (FWHM) of stars in an image file.

    Parameters
    ----------
    path : `str`
        The file path to the image.
    n_stars : `int`, optional
        How many of the brightest stars to measure. Defaults to 15.

    Returns
    -------
    fwhm : `float` or `None`
        The average FWHM in pixels. None if no stars are found or
        there's an error.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)

    return measure_fwhm_from_data(data, n_stars)


def measure_frame_input_quality(path: str, include_fwhm: bool = False) -> dict[str, float | None]:
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
    if data.ndim == 3:
        data = np.mean(data, axis=0 if data.shape[0] in (3, 4) else -1)

    try:
        _, median, _ = sigma_clipped_stats(data, sigma=3.0)
        metrics["background_level"] = float(median)
    except Exception as background_error:
        logger.debug("Background measurement failed for %s: %s", path, background_error)

    try:
        metrics["saturated_pixel_fraction"] = compute_saturated_pixel_fraction(
            data, DEFAULT_SATURATION_ADU_THRESHOLD
        )
    except Exception as saturation_error:
        logger.debug("Saturation measurement failed for %s: %s", path, saturation_error)

    if include_fwhm:
        try:
            metrics["fwhm_px"] = measure_fwhm_from_data(data)
        except Exception as fwhm_error:
            logger.debug("FWHM measurement failed for %s: %s", path, fwhm_error)

    return metrics


def measure_fwhm_from_data(data: np.ndarray, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT) -> float | None:
    """Measure the average blurriness (FWHM) of stars from a loaded image.

    This does the actual math for `measure_image_fwhm` so we don't have to
    read the file again if we already have the image open in memory.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data array.
    n_stars : `int`, optional
        How many of the brightest stars to measure. Defaults to 15.

    Returns
    -------
    fwhm : `float` or `None`
        The average FWHM in pixels, or None if it couldn't be calculated.
    """
    from photutils.morphology import data_properties

    from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

    sources = SourceDetector().detect(data)
    if not sources:
        return None

    box = FWHM_MEASUREMENT_BOX_RADIUS_PX
    fwhms = []
    for source in sources[:n_stars]:
        x = source.get("x_centroid", source.get("xcentroid"))
        y = source.get("y_centroid", source.get("ycentroid"))
        if x is None or y is None:
            continue
        x, y = round(x), round(y)
        y0, y1 = max(0, y - box), min(data.shape[0], y + box)
        x0, x1 = max(0, x - box), min(data.shape[1], x + box)
        cutout = data[y0:y1, x0:x1]
        if cutout.size == 0:
            continue
        try:
            _, median, _ = sigma_clipped_stats(cutout, sigma=3.0)
            fwhm = float(data_properties(cutout - median).fwhm.value)
            if np.isfinite(fwhm) and fwhm > 0:
                fwhms.append(fwhm)
        except Exception as exc:
            logger.debug("Skipping FWHM measurement for one star cutout: %s", exc)
            continue

    return float(np.median(fwhms)) if fwhms else None


def measure_saturated_pixel_fraction(
    path: str, saturation_threshold: float = DEFAULT_SATURATION_ADU_THRESHOLD
) -> float | None:
    """Calculate what percentage of the image is completely white (saturated).

    Parameters
    ----------
    path : `str`
        The file path to the image.
    saturation_threshold : `float`, optional
        The value above which a pixel is considered saturated. Defaults to
        65000.0.

    Returns
    -------
    fraction : `float` or `None`
        The fraction (between 0.0 and 1.0) of saturated pixels.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    return compute_saturated_pixel_fraction(np.asarray(data, dtype=float), saturation_threshold)


def measure_rejected_fraction(stacked_path: str) -> float | None:
    """Calculate what percentage of pixels were thrown out during stacking.

    Siril creates a rejection map file when it stacks images. This reads that
    map to tell us how much bad data (like satellites or clouds) had to be
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
