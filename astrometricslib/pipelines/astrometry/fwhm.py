"""Measure star sharpness (FWHM) by detecting stars and fitting their profile.

Lives alongside `source_detection.py` rather than in
`pipelines/shared/quality/quality_metrics.py` because measuring FWHM
this way means finding stars first -- the same `SourceDetector` step
astrometry uses before catalog matching. Anything that just wants "how
sharp is this image" (stacking's quality grading, the API layer)
imports this directly, the same way they'd import any other astrometry
tool.
"""

import logging
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.stats import gaussian_sigma_to_fwhm, sigma_clipped_stats

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.pipelines.astrometry.source_detection import SourceDetector

logger = logging.getLogger(__name__)

# The constant factor between a Gaussian's standard deviation and its
# FWHM (2*sqrt(2*ln(2))), spelled out here because `semiminor_axis` is
# reported as a standard deviation but every other number in this
# module is a FWHM.
GAUSSIAN_SIGMA_TO_FWHM = gaussian_sigma_to_fwhm

FWHM_MEASUREMENT_BOX_RADIUS_PX = 15
FWHM_MEASUREMENT_STAR_COUNT = 15

# How stretched out a measured "star" is allowed to look before it is
# treated as contaminated rather than a genuinely soft-focus point
# source. A star's own image is round -- elongation near 1.0 -- even
# when it is badly out of focus, so a normal image never needs this.
# It matters for a slitless spectrograph image, where every star sits
# at one end of its own dispersed trail: measured on a real session
# (Albireo, 2026-09-22, a K3II primary and a fainter B star only 34
# arcsec apart), a star's own trail crept into the measurement box and
# pushed elongation from about 1.2 up to 2.8, which in turn pushed the
# ordinary whole-blob FWHM from ~4px (correct) to ~9px (the trail's
# length, not the star's width) -- see
# `test_measure_fwhm_from_data_ignores_a_trail_attached_to_a_star`.
# 1.5 sits above the noise-driven wobble a round star shows in real
# data but below the elongation a trail or a chance-aligned neighbour
# introduces.
FWHM_MEASUREMENT_MAX_ELONGATION = 1.5

# A candidate is skipped unless its flux is at least this fraction of
# the single brightest candidate in the image. A field with only one
# or two real stars (as in the Albireo session that motivated this)
# otherwise fills the rest of its brightest-15 sample with ordinary
# noise peaks -- flux a hundredth of the real stars', but photutils
# still reports a (meaningless, noise-shaped) FWHM for each one, and
# those numbers dominate the median. A real star field's brightest 15
# stars are usually within a couple of magnitudes of each other, well
# inside this ratio, so this does not thin out a normal image.
FWHM_MEASUREMENT_MIN_RELATIVE_FLUX = 0.05


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

    brightest_flux = sources[0].get("flux", 0.0)
    minimum_flux = brightest_flux * FWHM_MEASUREMENT_MIN_RELATIVE_FLUX
    bright_sources = [source for source in sources if source.get("flux", 0.0) >= minimum_flux]

    box = FWHM_MEASUREMENT_BOX_RADIUS_PX
    fwhms = []
    for source in bright_sources[:n_stars]:
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
            properties = data_properties(cutout - median)
            fwhm = _star_fwhm_from_properties(properties)
            if np.isfinite(fwhm) and fwhm > 0:
                fwhms.append(fwhm)
        except Exception as exc:
            logger.debug("Skipping FWHM measurement for one star cutout: %s", exc)
            continue

    return float(np.median(fwhms)) if fwhms else None


def _star_fwhm_from_properties(properties: Any) -> float:
    """Turn one star's shape measurement into a single FWHM number.

    A normal star's image is round, so its whole-blob FWHM and its
    narrow-axis FWHM are almost the same number. When something other
    than the star reaches into the measurement box -- most often
    another star's dispersed trail in a slitless spectrograph image,
    but also a chance-aligned neighbour or a bit of nebulosity -- the
    blob stretches out along one axis and the whole-blob FWHM balloons
    to the size of that contamination instead of the star. The narrow
    axis is barely touched by contamination running mostly along the
    other axis, so it is used instead whenever the blob looks
    stretched. See `FWHM_MEASUREMENT_MAX_ELONGATION` for how stretched
    is too stretched.

    Parameters
    ----------
    properties : `photutils.morphology.SourceCatalog` properties row
        The shape measurement for one star's cutout, from
        `photutils.morphology.data_properties`.

    Returns
    -------
    fwhm : `float`
        The whole-blob FWHM, or the narrower axis's own FWHM when the
        blob is more stretched out than a real star should be.
    """
    if properties.elongation.value <= FWHM_MEASUREMENT_MAX_ELONGATION:
        return float(properties.fwhm.value)
    return float(properties.semiminor_axis.value * GAUSSIAN_SIGMA_TO_FWHM)
