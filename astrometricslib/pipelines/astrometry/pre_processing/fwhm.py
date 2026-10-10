"""Measure star sharpness (FWHM) by detecting stars and fitting their profile.

The FWHM (full width at half maximum) is the width of a star's image at half
its peak brightness. Each star is fitted with a two-dimensional Gaussian (a
bell-shaped profile) and the FWHM comes from the fitted width. A fit looks at
the star's core. The spread of all the light in a large box (the second
moment) would also count neighbouring stars and faint wings, and reads about
twice as wide as the core on real stacks.

Lives alongside `source_detection.py` rather than in
`pipelines/shared/quality/quality_metrics.py` because measuring FWHM
this way means finding stars first -- the same `SourceDetector` step
astrometry uses before catalog matching. Anything that just wants "how
sharp is this image" (stacking's quality grading, the API layer)
imports this directly, the same way they'd import any other astrometry
tool.

Agreement with the other FWHM numbers in the project (checked on the
2026-10-02 Bubble Nebula stack, 2.4 px by an independent Gaussian fit):
Siril's PSF fit in the registration file reads about 20% wider (2.8 px),
because it fits a profile with extended wings. The two agree on trends,
which is what the quality checks use, and differ by a fixed factor in
absolute terms.
"""

import logging
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.stats import gaussian_sigma_to_fwhm, sigma_clipped_stats
from scipy import optimize

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# The constant factor between a Gaussian's standard deviation and its
# FWHM (2*sqrt(2*ln(2))). The fit reports standard deviations and every
# other number in this module is a FWHM.
GAUSSIAN_SIGMA_TO_FWHM = gaussian_sigma_to_fwhm

# Half the side, in pixels, of the square around a star that is fitted. The
# box must reach about three standard deviations from the centre so the fit
# sees the whole core: 10 pixels covers stars up to a FWHM of about 7 px.
# Checked on the Bubble Nebula and M 27 stacks (FWHM 2.4 px) and on synthetic
# stars of 4.0 and 7.1 px.
FWHM_FIT_BOX_RADIUS_PX = 10

# A star that fits wider than this standard deviation (a FWHM of about
# 7 px) is fitted again in the larger box below, because the first box
# cuts off the wings of a soft star.
FWHM_REFIT_SIGMA_PX = 3.0
FWHM_REFIT_BOX_RADIUS_PX = 15

# The number of the brightest unsaturated stars measured, and how many
# candidates are looked at to find them. The brightest stars of a field are
# often saturated, which widens the fit, so they are skipped without a fit.
# A rich field can have a hundred or more of them (the Bubble Nebula stack
# has about 40 above the saturation limit among its first 100 candidates),
# and a fit can also fail on a crowded or noisy star, so many more
# candidates than stars are looked at. Skipped candidates cost almost nothing.
FWHM_MEASUREMENT_STAR_COUNT = 15
FWHM_MEASUREMENT_CANDIDATE_COUNT = 400

# The image's brightest value counts as a saturation level only when at least
# this many pixels sit at it, which is the flat top clipped cores make. A
# single hot pixel or cosmic ray does not count.
FWHM_SATURATED_PLATEAU_PX = 3

# With a saturation level, a star is skipped when any pixel of its box
# reaches this fraction of it. Near the top, stars are clipped and their
# wings are bloated, so they fit wider than their neighbours. On the Bubble
# Nebula stack (saturation level 1.0) the stars that reach it fit 2.67 px wide,
# the stars below 0.75 of it fit 2.45 px wide, and the same split showed in a
# raw frame (2.45 and 2.38 px).
FWHM_SATURATION_FRACTION = 0.95

# A fit is kept only if it explains this fraction of the variance of the
# cutout (R squared). A star with a bright neighbour or a cosmic ray in its
# box fits badly and is dropped. Chosen on the Bubble Nebula stack, where
# 0.8 keeps the large majority of unsaturated stars and drops the visibly
# contaminated ones.
FWHM_MINIMUM_FIT_R_SQUARED = 0.8

# The fitted centre must stay within this many pixels of the detected one.
# A fit that wanders further has locked onto a neighbour.
FWHM_MAXIMUM_CENTRE_SHIFT_PX = 2.5

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
# otherwise fills the rest of its candidate list with ordinary
# noise peaks -- flux a hundredth of the real stars', but a fit still
# reports a (meaningless, noise-shaped) FWHM for each one, and
# those numbers dominate the median. A real star field's brightest stars
# are usually within a couple of magnitudes of each other, well
# inside this ratio, so this does not thin out a normal image.
FWHM_MEASUREMENT_MIN_RELATIVE_FLUX = 0.05

# A star is skipped when the exclusion mask (see `measure_fwhm_from_data`) is
# set within this many pixels of its centre. A star that clips in a long
# exposure has a saturated core several pixels across, and the detected
# centre lies within a pixel or two of the middle of that core. On the M 57
# stack of 2026-10-03 all 15 stars the check measured sat on such a core (peak
# 1.35 to 1.45 in the combined image, saturated at 1.00 in the 60 s group) and
# read a median of 3.40 px, while 5,000 stars off those cores read 2.38 px.
FWHM_EXCLUDED_CORE_RADIUS_PX = 3


def measure_image_fwhm(
    path: str, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT, excluded_mask: np.ndarray | None = None
) -> float | None:
    """Measure the average blurriness (FWHM) of stars in an image file.

    Parameters
    ----------
    path : `str`
        The file path to the image.
    n_stars : `int`, optional
        How many stars to measure. Defaults to 15.
    excluded_mask : `numpy.ndarray`, optional
        A boolean mask of the image's pixels; see `measure_fwhm_from_data`.

    Returns
    -------
    fwhm : `float` or `None`
        The median FWHM in pixels. None if no stars are found or
        there's an error.
    """
    with fits.open(path, memmap=False) as hdul:
        data = hdul[0].data
    if data is None:
        return None
    data = np.asarray(data, dtype=float)
    data = collapse_to_2d(data)

    return measure_fwhm_from_data(data, n_stars, excluded_mask=excluded_mask)


def measure_fwhm_from_data(
    data: np.ndarray, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT, excluded_mask: np.ndarray | None = None
) -> float | None:
    """Measure the average blurriness (FWHM) of stars from a loaded image.

    This does the actual math for `measure_image_fwhm` so the file doesn't
    have to be read again if the image is already open in memory.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data array.
    n_stars : `int`, optional
        How many stars to measure. Defaults to 15.
    excluded_mask : `numpy.ndarray`, optional
        A boolean mask the shape of `data`. A star with a masked pixel within
        `FWHM_EXCLUDED_CORE_RADIUS_PX` of its centre is skipped. Use it for a
        combined stack whose bright cores were taken from a shorter exposure
        (see `saturated_pixel_mask`): such a stack has no saturated plateau
        for the check below to see, so those patched stars would otherwise be
        measured as the brightest stars and read too wide.

    Returns
    -------
    fwhm : `float` or `None`
        The median FWHM in pixels, or None if it couldn't be calculated.
    """
    sources = SourceDetector().detect(data)
    if not sources:
        return None

    brightest_flux = sources[0].get("flux", 0.0)
    minimum_flux = brightest_flux * FWHM_MEASUREMENT_MIN_RELATIVE_FLUX
    candidates = [source for source in sources if source.get("flux", 0.0) >= minimum_flux]
    brightest_value = float(np.nanmax(data))
    saturation_level = (
        brightest_value if np.count_nonzero(data >= brightest_value) >= FWHM_SATURATED_PLATEAU_PX else None
    )

    fwhms: list[float] = []
    for source in candidates[:FWHM_MEASUREMENT_CANDIDATE_COUNT]:
        if len(fwhms) >= n_stars:
            break
        x = source.get("x_centroid", source.get("xcentroid"))
        y = source.get("y_centroid", source.get("ycentroid"))
        if x is None or y is None:
            continue
        if excluded_mask is not None and _touches_mask(excluded_mask, round(x), round(y)):
            continue
        fwhm = _fit_star_fwhm(data, round(x), round(y), saturation_level)
        if fwhm is not None:
            fwhms.append(fwhm)

    return float(np.median(fwhms)) if fwhms else None


def _touches_mask(mask: np.ndarray, x: int, y: int) -> bool:
    """Say whether any masked pixel lies near a star's centre.

    Returns
    -------
    touches : `bool`
        `True` if the mask is set anywhere within
        `FWHM_EXCLUDED_CORE_RADIUS_PX` of (x, y).
    """
    radius = FWHM_EXCLUDED_CORE_RADIUS_PX
    window = mask[max(y - radius, 0) : y + radius + 1, max(x - radius, 0) : x + radius + 1]
    return bool(window.any())


def _cutout(data: np.ndarray, x: int, y: int, radius: int) -> np.ndarray | None:
    """Cut a square around a position, if it fits inside the image.

    Returns
    -------
    cutout : `numpy.ndarray` or `None`
        The pixels, or `None` if the square reaches past the image edge.
    """
    if x < radius or y < radius or x + radius >= data.shape[1] or y + radius >= data.shape[0]:
        return None
    return data[y - radius : y + radius + 1, x - radius : x + radius + 1]


def _fit_star_fwhm(data: np.ndarray, x: int, y: int, saturation_level: float | None) -> float | None:
    """Fit one star and turn the fit into a single FWHM number.

    The star is fitted in a small box and, if it turns out soft, fitted
    again in a larger one. A star that is saturated, too close to the edge,
    badly fitted, or off its detected position gives `None`.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image.
    x, y : `int`
        The detected centre of the star, in pixels.
    saturation_level : `float` or `None`
        The image's saturation level, or `None` if it has no clipped pixels.
        A star that reaches `FWHM_SATURATION_FRACTION` of it is skipped.

    Returns
    -------
    fwhm : `float` or `None`
        The star's FWHM in pixels.
    """
    radius = FWHM_FIT_BOX_RADIUS_PX
    for _ in range(2):
        cutout = _cutout(data, x, y, radius)
        if cutout is None:
            return None
        if saturation_level is not None and cutout.max() >= FWHM_SATURATION_FRACTION * saturation_level:
            return None
        fit = _fit_gaussian(cutout)
        if fit is None:
            return None
        sigma_major, sigma_minor, centre_shift = fit
        if centre_shift > FWHM_MAXIMUM_CENTRE_SHIFT_PX:
            return None
        if sigma_major > FWHM_REFIT_SIGMA_PX and radius < FWHM_REFIT_BOX_RADIUS_PX:
            radius = FWHM_REFIT_BOX_RADIUS_PX
            continue
        return _fwhm_from_sigmas(sigma_major, sigma_minor)
    return None


def _fwhm_from_sigmas(sigma_major: float, sigma_minor: float) -> float:
    """Turn a star's two fitted widths into a single FWHM number.

    A normal star's image is round, so its two widths are almost the same.
    When something other than the star reaches into the box -- most often
    another star's dispersed trail in a slitless spectrograph image, but also
    a chance-aligned neighbour or a bit of nebulosity -- the fit stretches
    along one axis. The narrow axis is barely touched by contamination
    running mostly along the other axis, so it is used whenever the fit looks
    stretched. See `FWHM_MEASUREMENT_MAX_ELONGATION` for how stretched is too
    stretched.

    Parameters
    ----------
    sigma_major, sigma_minor : `float`
        The fitted standard deviations along the long and short axes, in
        pixels.

    Returns
    -------
    fwhm : `float`
        The FWHM from the geometric mean of the two widths, or from the
        narrow width alone when the fit is too stretched.
    """
    if sigma_major / sigma_minor <= FWHM_MEASUREMENT_MAX_ELONGATION:
        return float(GAUSSIAN_SIGMA_TO_FWHM * np.sqrt(sigma_major * sigma_minor))
    return float(GAUSSIAN_SIGMA_TO_FWHM * sigma_minor)


def _fit_gaussian(cutout: np.ndarray) -> tuple[float, float, float] | None:
    """Fit a tilted two-dimensional Gaussian to a small image of one star.

    Parameters
    ----------
    cutout : `numpy.ndarray`
        A square image with the star near its centre.

    Returns
    -------
    fit : `tuple` [`float`, `float`, `float`] or `None`
        The larger and smaller fitted standard deviations in pixels, and how
        far the fitted centre sits from the box centre in pixels. `None` if
        the fit failed, found no peak, or explains too little of the cutout
        (see `FWHM_MINIMUM_FIT_R_SQUARED`).
    """
    size = cutout.shape[0]
    half = size // 2
    _, background, _ = sigma_clipped_stats(cutout, sigma=3.0)
    values = cutout - background
    peak = float(values[half - 2 : half + 3, half - 2 : half + 3].max())
    if not np.isfinite(peak) or peak <= 0:
        return None
    rows, columns = np.mgrid[:size, :size]

    def model(parameters: np.ndarray) -> np.ndarray:
        """Evaluate the tilted Gaussian on the cutout's pixel grid.

        Returns
        -------
        brightness : `numpy.ndarray`
            The model brightness at every pixel.
        """
        amplitude, centre_x, centre_y, sigma_x, sigma_y, angle, offset = parameters
        dx, dy = columns - centre_x, rows - centre_y
        along = dx * np.cos(angle) + dy * np.sin(angle)
        across = -dx * np.sin(angle) + dy * np.cos(angle)
        return amplitude * np.exp(-0.5 * ((along / sigma_x) ** 2 + (across / sigma_y) ** 2)) + offset

    start = np.array([peak, half, half, 2.0, 2.0, 0.0, 0.0])
    lower = np.array([0.0, half - 3, half - 3, 0.5, 0.5, -np.pi, -np.inf])
    upper = np.array([np.inf, half + 3, half + 3, size / 2.0, size / 2.0, np.pi, np.inf])
    try:
        result = optimize.least_squares(lambda p: (model(p) - values).ravel(), start, bounds=(lower, upper))
    except ValueError, FloatingPointError:
        return None
    if not result.success:
        return None
    total_variance = float(np.sum((values - values.mean()) ** 2))
    if total_variance <= 0:
        return None
    r_squared = 1.0 - float(np.sum(result.fun**2)) / total_variance
    if r_squared < FWHM_MINIMUM_FIT_R_SQUARED:
        return None
    sigma_x, sigma_y = abs(result.x[3]), abs(result.x[4])
    centre_shift = float(np.hypot(result.x[1] - half, result.x[2] - half))
    return max(sigma_x, sigma_y), min(sigma_x, sigma_y), centre_shift


# Half the side, in pixels, of the box the blob width is measured in.
BLOB_WIDTH_BOX_RADIUS_PX = 15


def measure_blob_width_from_data(
    data: np.ndarray, n_stars: int = FWHM_MEASUREMENT_STAR_COUNT
) -> float | None:
    """Measure how wide the light around the brightest sources is.

    This is the spread (second moment) of all the light in a 30-pixel box
    around each of the brightest sources. It counts neighbours and faint
    wings as well as the star, so it reads about twice as wide as the
    star's core: 4.9 px against a 2.5 px FWHM on the Bubble Nebula stack.
    It is not a FWHM, and nothing should report it as one.

    It exists to size the star-detection kernel of the astrometry stage.
    That stage was validated with this number. On the Bubble Nebula stack it
    gave 335 catalogue matches and a 1.04 arcsecond residual, against 181
    matches and 5.0 arcseconds when the kernel was sized from the fitted
    FWHM. Use `measure_fwhm_from_data` for any report of star sharpness.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image data array.
    n_stars : `int`, optional
        How many of the brightest sources to measure. Defaults to 15.

    Returns
    -------
    width : `float` or `None`
        The median blob width in pixels, or None if it couldn't be
        calculated.
    """
    from photutils.morphology import data_properties

    sources = SourceDetector().detect(data)
    if not sources:
        return None

    brightest_flux = sources[0].get("flux", 0.0)
    minimum_flux = brightest_flux * FWHM_MEASUREMENT_MIN_RELATIVE_FLUX
    bright_sources = [source for source in sources if source.get("flux", 0.0) >= minimum_flux]

    box = BLOB_WIDTH_BOX_RADIUS_PX
    widths = []
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
            width = _blob_width_from_properties(properties)
            if np.isfinite(width) and width > 0:
                widths.append(width)
        except DATA_ERRORS as exc:
            logger.debug("Skipping blob width measurement for one star cutout: %s", exc)
            continue

    return float(np.median(widths)) if widths else None


def _blob_width_from_properties(properties: Any) -> float:
    """Turn one blob's shape measurement into a single width.

    When another star's dispersed trail or a neighbour reaches into the box,
    the blob stretches along one axis and its whole-blob width balloons. The
    narrow axis is barely touched, so it is used whenever the blob is more
    stretched than `FWHM_MEASUREMENT_MAX_ELONGATION`.

    Parameters
    ----------
    properties : `photutils.morphology.SourceCatalog` properties row
        The shape measurement for one cutout, from
        `photutils.morphology.data_properties`.

    Returns
    -------
    width : `float`
        The whole-blob width, or the narrower axis's width when the blob is
        stretched.
    """
    if properties.elongation.value <= FWHM_MEASUREMENT_MAX_ELONGATION:
        return float(properties.fwhm.value)
    return float(properties.semiminor_axis.value * GAUSSIAN_SIGMA_TO_FWHM)
