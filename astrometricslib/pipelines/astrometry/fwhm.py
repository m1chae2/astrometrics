"""Measure star sharpness (FWHM) by detecting stars and fitting their profile.

Lives alongside `source_detection.py` rather than in
`drivers/quality_metrics.py` because measuring FWHM this way means
finding stars first -- the same `SourceDetector` step astrometry uses
before catalog matching. Anything
that just wants "how sharp is this image" (stacking's quality grading,
the API layer) imports this directly, the same way they'd import any
other astrometry tool.
"""

import logging

import numpy as np
from astropy.io import fits
from astropy.stats import sigma_clipped_stats

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.pipelines.astrometry.source_detection import SourceDetector

logger = logging.getLogger(__name__)

FWHM_MEASUREMENT_BOX_RADIUS_PX = 15
FWHM_MEASUREMENT_STAR_COUNT = 15


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
    data = collapse_to_2d(data)

    return measure_fwhm_from_data(data, n_stars)


def measure_fwhm_from_data(data: np.ndarray, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT) -> float | None:
    """Measure the average blurriness (FWHM) of stars from a loaded image.

    This does the actual math for `measure_image_fwhm` so the file doesn't
    have to be read again if the image is already open in memory.

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
