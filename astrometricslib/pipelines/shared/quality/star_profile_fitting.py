"""Finding stars in a frame and measuring their width, shape and direction.

Up to about 200 isolated, unsaturated stars spread on a grid over the field are
fitted with a Moffat profile (a bright core with slowly fading wings, closer to
a real star than a Gaussian). The second moments of the same stars give their
elongation and the direction of the long axis. The frame's width is the median
over the stars, with a bootstrap uncertainty, and the same stars are grouped
into field regions so the width can be compared between the center and the
corners. A raw color mosaic is measured on 2 x 2 super-pixels so the Bayer
pattern does not bend the moments.

`measure_frame` in `frame_measurement.py` calls these functions and writes the
results as measurements.
"""

import logging
import math
from dataclasses import dataclass, field, replace

import numpy as np
from astropy.stats import SigmaClip, sigma_clipped_stats
from photutils.background import Background2D, MedianBackground
from scipy import optimize
from scipy.spatial import cKDTree

from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.pipelines.shared.quality.frame_statistics import (
    BOOTSTRAP_RESAMPLE_COUNT,
    BOOTSTRAP_SEED,
    bootstrap_median_error,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    REGION_CENTER,
    REGION_XHIGH_YHIGH,
    REGION_XHIGH_YLOW,
    REGION_XLOW_YHIGH,
    REGION_XLOW_YLOW,
)
from astrometricslib.pipelines.shared.quality.time_budget import Stopwatch
from astrometricslib.pipelines.shared.star_finding import find_sources_above_background
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)


# The number of grid cells over the field. One star is taken from each cell, so
# this is the largest sample (the plan asks for up to about 200). 14 x 14 = 196
# cells give a star for each 0.5 percent of the frame area, which is
# enough to see a tilt or a coma pattern across the field and few enough that
# the fits finish in seconds.
MOFFAT_GRID_CELL_COUNT = 196

# The most candidates tried in one cell before the cell is given up. The
# brightest star of a cell is tried first; when its fit is refused the next
# brightest is tried. Three keeps a cell with one odd star from being empty
# without letting a hopeless cell cost a dozen fits.
MOFFAT_CANDIDATES_PER_CELL = 3

# Half the side, in pixels, of the box a star is fitted in (a 21 x 21 box). The
# box must hold the core and the start of the wings: 10 px is about 1.4 FWHM of
# a 7 px star and 3.3 FWHM of a 3 px star. A constant background in the fit
# absorbs the wings beyond the box, so a star wider than about 7 px reads a
# little narrow. It matches `FWHM_FIT_BOX_RADIUS_PX` of the Gaussian fit in
# `astrometry/pre_processing/fwhm.py`.
MOFFAT_FIT_BOX_HALF_WIDTH_PX = 10

# A star is isolated when no other detected source lies within this many pixels
# of it. The fit box reaches 10 px (14 px along its diagonal), and a neighbor
# just outside would still add its wing to the box corners. 14 px puts every
# neighbor's core beyond the corner of the box.
MOFFAT_ISOLATION_RADIUS_PX = 14.0

# A star is skipped when any pixel of its fit box reaches this fraction of the
# camera's usable top level (the smaller of the saturation threshold and the
# photometric linearity limit). Near the top the sensor stops responding in
# proportion to the light and the core flattens, which widens the fit. 0.9
# leaves a 10 percent margin; `FWHM_SATURATION_FRACTION` in `fwhm.py` uses 0.95
# on a stack, and a raw frame needs more room because a bright pixel's noise
# can push it over the line.
MOFFAT_SATURATION_MARGIN_FRACTION = 0.9

# Each pixel is modeled as the average of this many by this many sub-samples of
# the continuous profile, which stands in for integrating the profile over the
# pixel's area. With 3 x 3 the model error on the fitted width is below 1
# percent for stars of 2 px FWHM and above (checked on synthetic stars, see the
# tests); 1 x 1 reads 2 px stars about 5 percent wide.
MOFFAT_SUBPIXEL_FACTOR = 3

# The Moffat power index beta. A first pass fits it for every star; the median
# of those values is then held fixed for a second pass. Beta and the scale
# radius trade off against each other, so a free beta makes a faint star's
# width noisy; a shared beta removes that noise and still lets the frame's own
# wing shape decide the value. The limits keep a fit from wandering. The total
# flux of a Moffat profile, proportional to 1 / (beta - 1), grows without bound
# as beta falls to 1 and is infinite at beta <= 1, so beta must stay above 1.
# The lower limit of 1.2 is not that mathematical edge: it keeps the profile's
# wings from carrying five times the light of a beta of 2 (same amplitude and
# scale radius), which a 21 x 21 px fit box can hardly tell apart from the sky
# background because its constant background term absorbs the wings. It is a
# design choice, not a measured value. Above 8 the profile is a Gaussian to
# within the fit's noise.
MOFFAT_BETA_MINIMUM = 1.2

MOFFAT_BETA_MAXIMUM = 8.0

MOFFAT_BETA_START = 3.0

# A fit is kept only if it explains this fraction of the variance in its box (R
# squared). Same value as `FWHM_MINIMUM_FIT_R_SQUARED` in `fwhm.py`, which was
# chosen on the Bubble Nebula stack. It rejects stars with a bright neighbor
# or a cosmic ray in the box.
MOFFAT_MINIMUM_R_SQUARED = 0.8

# The fitted center must stay within this many pixels of the detected one; a
# fit that wanders further has locked onto a neighbor. Same as
# `FWHM_MAXIMUM_CENTRE_SHIFT_PX` in `fwhm.py`.
MOFFAT_MAXIMUM_CENTER_SHIFT_PX = 2.5

# A fitted width outside this range (in pixels, FWHM) is a fit gone wrong, not
# a star: a cosmic ray or hot pixel is narrower than 0.8 px, and a width above
# 25 px does not fit in the box at all.
MOFFAT_MINIMUM_FWHM_PX = 0.8

MOFFAT_MAXIMUM_FWHM_PX = 25.0

# The fewest stars that give a median and a bootstrap uncertainty worth
# storing. With fewer, a resample has too few distinct medians to say anything.
MOFFAT_MINIMUM_STAR_COUNT = 5

# The aperture the second moments are taken in, as a multiple of the star's
# fitted FWHM, and its limits in pixels. 1.5 FWHM holds about 90 percent of the
# light of a Moffat star with beta = 3 and is small enough to keep neighbors
# out. The window is elliptical, following the star's first-pass shape (see
# `_measure_second_moments`), so a trailed star is not cut short.
MOMENT_APERTURE_FWHM_MULTIPLE = 1.5

MOMENT_APERTURE_MINIMUM_RADIUS_PX = 3.0

# The blur a square pixel adds to a star, as a multiple of the pixel side
# squared (the "c" of the effective star width). A star is the sky's image
# convolved (smeared) with the pixel's square sensitive area. The variance of
# a position spread evenly over a side p is p^2 / 12, and variances add under
# convolution, so for a Gaussian-like core of width sigma the effective
# sigma^2 is sigma^2 + p^2 / 12 along each axis. The FWHM of a Gaussian is
# 2 sqrt(2 ln 2) sigma, so FWHM_eff^2 = FWHM^2 + 8 ln 2 * p^2 / 12 =
# FWHM^2 + (2 ln 2 / 3) * p^2, and the multiple is 2 ln 2 / 3 = 0.4621 (the
# decimal 2.3548^2 / 12 gives the same). It is a theoretical value, not fitted
# (user decision of 2026-10-23, science plan section 2.1); the register entry
# AST-18 states what the Gaussian-core assumption leaves out.
PIXEL_BLUR_FWHM_SQUARED_FACTOR = 2.0 * math.log(2.0) / 3.0

# The star-width regions of the field. The field is cut into the same 14 x 14
# cells the stars are chosen from (`MOFFAT_GRID_CELL_COUNT`). The center region
# is the middle 4 x 4 cells and each corner region is the 4 x 4 cells at that
# corner, so the five regions have the same area (16 of 196 cells, 8 percent
# of the frame each) and hold about the same number of stars. Four cells is
# the smallest block that holds enough stars to give a median with a
# bootstrap error (the grid takes at most one star per cell, so a block holds
# at most 16 and usually 5 to 12 in a sparse field). In every name below, x is
# the column and y is the array row, with row 0 first (see
# `metrics.REGION_XLOW_YLOW`).
FIELD_GRID_CELLS_PER_SIDE = 14

FIELD_REGION_CELLS_PER_SIDE = 4

# The names of the five regions, in the order the metrics are emitted. A corner
# is named by whether it lies at the low or the high end of the x axis (the
# columns) and of the y axis (the rows), counted from the first pixel, so the
# name does not depend on how a viewer flips the image.
FIELD_REGION_NAMES = (
    REGION_CENTER,
    REGION_XLOW_YLOW,
    REGION_XHIGH_YLOW,
    REGION_XLOW_YHIGH,
    REGION_XHIGH_YHIGH,
)

# The side, in pixels, of the repeating cell of a Bayer color filter array: one
# red, two green and one blue pixel in a 2 x 2 block. The star shape metrics of
# a raw color mosaic are measured on blocks of this size summed into single
# super-pixels. The cell is 2 x 2 for every common Bayer order (RGGB, BGGR,
# GRBG, GBRG), so the factor does not depend on the header's BAYERPAT text.
BAYER_CELL_SIZE_PX = 2


@dataclass(frozen=True)
class CandidateRefusals:
    """How many detected sources were refused at each cut, and why.

    A trailed star is more likely to touch a neighbor, to be wide enough to
    reach the saturation margin, or to fit badly. These cuts therefore remove
    the most trailed stars first, and the counts let a reader see how much of
    the field the width and elongation medians rest on.

    Attributes
    ----------
    detected : `int`
        All sources the star finder returned.
    near_edge : `int`
        Sources whose fit box would reach outside the frame.
    near_neighbor : `int`
        Sources with another detected source within
        `MOFFAT_ISOLATION_RADIUS_PX`.
    near_saturation : `int`
        Sources whose fit box reaches `MOFFAT_SATURATION_MARGIN_FRACTION` of
        the camera's usable top level.
    fit_refused : `int`
        Stars tried whose Moffat fit was refused (a poor fit, a center that
        wandered, a width out of range, or a fit that failed), in either pass.
    """

    detected: int = 0
    near_edge: int = 0
    near_neighbor: int = 0
    near_saturation: int = 0
    fit_refused: int = 0

    def describe(self) -> str:
        """Write the counts as one sentence fragment for a reason.

        Returns
        -------
        text : `str`
            For example ``"412 sources detected; refused: 3 near the frame
            edge, 120 with a neighbor within 14 px, ...``.
        """
        return (
            f"{self.detected} sources detected; refused: {self.near_edge} near the frame edge, "
            f"{self.near_neighbor} with a neighbor within {MOFFAT_ISOLATION_RADIUS_PX:g} px, "
            f"{self.near_saturation} near saturation, {self.fit_refused} by the fit"
        )


@dataclass(frozen=True)
class StarWidthSample:
    """The width of one fitted star and where it sits in the frame.

    Attributes
    ----------
    x_px, y_px : `float`
        The star's detected position in original pixels (the column and the
        row, counted from 0). For a raw color mosaic the position found on the
        super-pixels is given back in original pixels.
    width_px : `float`
        The star's Moffat FWHM (the geometric mean of the two axes) in original
        pixels, as it enters the frame's median.
    """

    x_px: float
    y_px: float
    width_px: float


@dataclass(frozen=True)
class StarProfileResult:
    """The star widths, elongations and angles of one plane of one frame.

    Attributes
    ----------
    width_px : `float` or `None`
        Median Moffat FWHM in pixels, or `None` when it could not be measured.
    width_px_uncertainty : `float` or `None`
        Bootstrap standard error of the median, in pixels.
    elongation : `float` or `None`
        Median elongation (long axis divided by short axis, at least 1).
    elongation_uncertainty : `float` or `None`
        Bootstrap standard error of the median elongation.
    angle_deg : `float` or `None`
        Direction of the long axis in degrees, from 0 up to but not including
        180, measured from the image x axis toward the y axis. It comes from
        the average of the stars' ellipticity components, so a round star
        counts for little and a stretched one for much.
    angle_uncertainty_deg : `float` or `None`
        Bootstrap standard deviation of that angle, in degrees.
    angle_coherence : `float` or `None`
        Length of the mean ellipticity vector divided by the mean ellipticity
        of the stars, from 0 (no preferred direction) to 1 (all stars point
        the same way).
    star_count : `int`
        Number of stars the medians are over.
    candidate_count : `int`
        Number of isolated, unsaturated stars that were tried.
    median_beta : `float` or `None`
        The shared Moffat power index of the second pass.
    refusals : `CandidateRefusals`
        How many detected sources each cut refused.
    reason : `str`
        Why the values are missing, or a note on how they were found.
    samples : `tuple` of `StarWidthSample`
        The position and width of every star behind the medians, from the same
        second-pass fits (no second fit is made for the regions).
    field_shape_px : `tuple` of `int`, or `None`
        The (rows, columns) of the measured plane in original pixels, for the
        star-width regions. `None` when nothing was measured.
    """

    width_px: float | None = None
    width_px_uncertainty: float | None = None
    elongation: float | None = None
    elongation_uncertainty: float | None = None
    angle_deg: float | None = None
    angle_uncertainty_deg: float | None = None
    angle_coherence: float | None = None
    star_count: int = 0
    candidate_count: int = 0
    median_beta: float | None = None
    refusals: CandidateRefusals = field(default_factory=CandidateRefusals)
    reason: str = ""
    samples: tuple[StarWidthSample, ...] = ()
    field_shape_px: tuple[int, int] | None = None


@dataclass
class _FitResult:
    """One accepted Moffat fit of one star."""

    center_x: float
    center_y: float
    fwhm_major_px: float
    fwhm_minor_px: float
    angle_rad: float
    beta: float
    background: float
    r_squared: float


def usable_top_level_adu(camera_profile: CameraProfile | None) -> float | None:
    """Find the pixel value above which the sensor can no longer be trusted.

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile.

    Returns
    -------
    level_adu : `float` or `None`
        The smaller of the saturation threshold (or the clip ceiling, when the
        threshold lies above it and no pixel could reach it) and the
        photometric linearity limit. `None` without a profile.
    """
    if camera_profile is None:
        return None
    return float(camera_profile.usable_peak_level_adu.value)


def plateau_saturation_level_adu(plane: np.ndarray) -> float | None:
    """Guess a plane's saturation level from its own pixels.

    The brightest value counts as the saturation level only when at least
    three pixels sit at it, the flat top that clipped cores make. A single hot
    pixel does not count. This is the rule `measure_fwhm_from_data` uses, and
    it is the fallback when the frame has no camera profile.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.

    Returns
    -------
    level_adu : `float` or `None`
        The brightest value, or `None` when it is not a plateau.
    """
    brightest = float(np.nanmax(plane))
    return brightest if np.count_nonzero(plane >= brightest) >= 3 else None


def _moffat_fwhm_px(alpha: float, beta: float) -> float:
    """Convert a Moffat scale radius to a full width at half maximum.

    Parameters
    ----------
    alpha : `float`
        The scale radius, in pixels.
    beta : `float`
        The power index.

    Returns
    -------
    fwhm_px : `float`
        ``2 * alpha * sqrt(2 ** (1 / beta) - 1)``.
    """
    return 2.0 * alpha * math.sqrt(2.0 ** (1.0 / beta) - 1.0)


@dataclass
class _SubpixelGrid:
    """The sub-pixel sample positions of one fit box."""

    fine_x: np.ndarray
    fine_y: np.ndarray
    size: int


def _subpixel_grid(size: int) -> _SubpixelGrid:
    """Build the positions at which the profile is sampled inside each pixel.

    Parameters
    ----------
    size : `int`
        The side of the box, in pixels.

    Returns
    -------
    grid : `_SubpixelGrid`
        Pixel-center coordinates of every sub-sample along one axis. Pixel
        ``i`` spans ``i - 0.5`` to ``i + 0.5``.
    """
    factor = MOFFAT_SUBPIXEL_FACTOR
    offsets = (np.arange(factor) + 0.5) / factor - 0.5
    axis = (np.arange(size)[:, None] + offsets[None, :]).ravel()
    return _SubpixelGrid(fine_x=axis[None, :], fine_y=axis[:, None], size=size)


def _moffat_model(parameters: np.ndarray, grid: _SubpixelGrid, fixed_beta: float | None) -> np.ndarray:
    """Evaluate the pixel-integrated elliptical Moffat profile on a box.

    The profile is sampled on a sub-pixel grid and averaged over each pixel.

    Parameters
    ----------
    parameters : `numpy.ndarray`
        ``[amplitude, center_x, center_y, alpha_major, alpha_minor, angle,
        background]`` when `fixed_beta` is given, with ``beta`` inserted before
        ``background`` otherwise.
    grid : `_SubpixelGrid`
        The sample positions.
    fixed_beta : `float` or `None`
        The power index to hold, or `None` when it is a fit parameter.

    Returns
    -------
    model : `numpy.ndarray`
        The model pixel values, shape ``(size, size)``.
    """
    if fixed_beta is None:
        amplitude, center_x, center_y, alpha_major, alpha_minor, angle, beta, background = parameters
    else:
        amplitude, center_x, center_y, alpha_major, alpha_minor, angle, background = parameters
        beta = fixed_beta
    delta_x = grid.fine_x - center_x
    delta_y = grid.fine_y - center_y
    along = delta_x * math.cos(angle) + delta_y * math.sin(angle)
    across = -delta_x * math.sin(angle) + delta_y * math.cos(angle)
    radius_squared = (along / alpha_major) ** 2 + (across / alpha_minor) ** 2
    fine = amplitude * (1.0 + radius_squared) ** (-beta)
    factor = MOFFAT_SUBPIXEL_FACTOR
    pixels = fine.reshape(grid.size, factor, grid.size, factor).mean(axis=(1, 3))
    return pixels + background


def _fit_moffat(cutout: np.ndarray, grid: _SubpixelGrid, fixed_beta: float | None) -> _FitResult | None:
    """Fit one star with a pixel-integrated elliptical Moffat profile.

    Parameters
    ----------
    cutout : `numpy.ndarray`
        The square box of pixels, with the star near its center.
    grid : `_SubpixelGrid`
        The sub-pixel sample positions for that box size.
    fixed_beta : `float` or `None`
        Hold the power index at this value, or fit it when `None`.

    Returns
    -------
    fit : `_FitResult` or `None`
        The accepted fit, or `None` when the fit failed or was refused (see the
        ``MOFFAT_*`` limits above).
    """
    size = cutout.shape[0]
    half = size // 2
    background_start = float(np.median(cutout))
    peak = float(cutout[half - 2 : half + 3, half - 2 : half + 3].max()) - background_start
    if not math.isfinite(peak) or peak <= 0.0:
        return None
    alpha_start = 2.5
    start = [peak, half, half, alpha_start, alpha_start, 0.0]
    lower = [0.0, half - 3.0, half - 3.0, 0.3, 0.3, -math.pi]
    upper = [np.inf, half + 3.0, half + 3.0, size / 2.0, size / 2.0, math.pi]
    if fixed_beta is None:
        start.append(MOFFAT_BETA_START)
        lower.append(MOFFAT_BETA_MINIMUM)
        upper.append(MOFFAT_BETA_MAXIMUM)
    start.append(background_start)
    lower.append(-np.inf)
    upper.append(np.inf)

    def residuals(parameters: np.ndarray) -> np.ndarray:
        """Give model minus data for the least-squares solver.

        Returns
        -------
        residual : `numpy.ndarray`
            The flattened difference.
        """
        return (_moffat_model(parameters, grid, fixed_beta) - cutout).ravel()

    try:
        result = optimize.least_squares(residuals, np.array(start), bounds=(lower, upper), max_nfev=200)
    except ValueError, FloatingPointError:
        return None
    if not result.success:
        return None
    total_variance = float(np.sum((cutout - cutout.mean()) ** 2))
    if total_variance <= 0.0:
        return None
    r_squared = 1.0 - float(np.sum(result.fun**2)) / total_variance
    if r_squared < MOFFAT_MINIMUM_R_SQUARED:
        return None
    parameters = result.x
    beta = parameters[6] if fixed_beta is None else fixed_beta
    background = parameters[-1]
    center_shift = math.hypot(parameters[1] - half, parameters[2] - half)
    if center_shift > MOFFAT_MAXIMUM_CENTER_SHIFT_PX:
        return None
    fwhm_a = _moffat_fwhm_px(parameters[3], beta)
    fwhm_b = _moffat_fwhm_px(parameters[4], beta)
    fwhm_major, fwhm_minor = max(fwhm_a, fwhm_b), min(fwhm_a, fwhm_b)
    if not (MOFFAT_MINIMUM_FWHM_PX <= fwhm_minor and fwhm_major <= MOFFAT_MAXIMUM_FWHM_PX):
        return None
    angle = parameters[5] if fwhm_a >= fwhm_b else parameters[5] + math.pi / 2.0
    return _FitResult(
        center_x=float(parameters[1]),
        center_y=float(parameters[2]),
        fwhm_major_px=float(fwhm_major),
        fwhm_minor_px=float(fwhm_minor),
        angle_rad=float(angle),
        beta=float(beta),
        background=float(background),
        r_squared=float(r_squared),
    )


def _measure_second_moments(cutout: np.ndarray, fit: _FitResult) -> tuple[float, float, float] | None:
    """Measure a star's elongation and ellipticity from second moments.

    The pixels are weighted by their light above the fitted background
    (negative values count as zero), inside an elliptical window that follows
    the fitted star. The window is a multiple of the fitted FWHM
    (`MOMENT_APERTURE_FWHM_MULTIPLE`) along each fitted axis. A circular window
    would cut a trailed star short and read it too round.

    Parameters
    ----------
    cutout : `numpy.ndarray`
        The box of pixels around the star.
    fit : `_FitResult`
        The star's accepted Moffat fit.

    Returns
    -------
    shape : `tuple` [`float`, `float`, `float`] or `None`
        ``(elongation, first ellipticity component, second ellipticity
        component)``. The components are ``(Ixx - Iyy) / (Ixx + Iyy)`` and
        ``2 Ixy / (Ixx + Iyy)`` of the second moments ``I``. Their length is
        ``(a**2 - b**2) / (a**2 + b**2)`` for long and short axes ``a`` and
        ``b``, and the long axis points along half the angle of the vector.
        `None` when the weights sum to nothing.
    """
    size = cutout.shape[0]
    rows, columns = np.mgrid[:size, :size]
    delta_x = columns - fit.center_x
    delta_y = rows - fit.center_y
    cosine, sine = math.cos(fit.angle_rad), math.sin(fit.angle_rad)
    along = delta_x * cosine + delta_y * sine
    across = -delta_x * sine + delta_y * cosine
    semi_major = max(MOMENT_APERTURE_FWHM_MULTIPLE * fit.fwhm_major_px, MOMENT_APERTURE_MINIMUM_RADIUS_PX)
    semi_minor = max(MOMENT_APERTURE_FWHM_MULTIPLE * fit.fwhm_minor_px, MOMENT_APERTURE_MINIMUM_RADIUS_PX)
    inside = (along / semi_major) ** 2 + (across / semi_minor) ** 2 <= 1.0
    weights = np.where(inside, np.clip(cutout - fit.background, 0.0, None), 0.0)
    total = float(weights.sum())
    if total <= 0.0:
        return None
    mean_x = float((weights * columns).sum() / total)
    mean_y = float((weights * rows).sum() / total)
    variance_x = float((weights * (columns - mean_x) ** 2).sum() / total)
    variance_y = float((weights * (rows - mean_y) ** 2).sum() / total)
    covariance = float((weights * (columns - mean_x) * (rows - mean_y)).sum() / total)
    spread = math.hypot((variance_x - variance_y) / 2.0, covariance)
    mean_variance = (variance_x + variance_y) / 2.0
    largest, smallest = mean_variance + spread, mean_variance - spread
    if smallest <= 0.0:
        return None
    elongation = math.sqrt(largest / smallest)
    trace = variance_x + variance_y
    return elongation, (variance_x - variance_y) / trace, 2.0 * covariance / trace


def _mean_ellipticity_angle(
    first_components: np.ndarray, second_components: np.ndarray
) -> tuple[float, float]:
    """Find the direction the stars are stretched in, from their ellipticities.

    Averaging the ellipticity components (not the stars' angles) lets a
    stretched star count for more than a round one. A round star's angle is
    set by noise, so averaging angles with equal weight would let the round
    stars of a mostly round field wash out, or bias, the direction of the few
    stretched ones.

    Parameters
    ----------
    first_components : `numpy.ndarray`
        ``(Ixx - Iyy) / (Ixx + Iyy)`` of each star.
    second_components : `numpy.ndarray`
        ``2 Ixy / (Ixx + Iyy)`` of each star.

    Returns
    -------
    angle_deg : `float`
        The direction of the long axis, from 0 up to but not including 180
        degrees, measured from the x axis toward the y axis.
    coherence : `float`
        The length of the mean ellipticity vector divided by the mean length
        of the stars' own ellipticity vectors, from 0 to 1. It is 0 when the
        stars' stretches cancel and 1 when they all point the same way. A
        field of stars that are all exactly round gives 0.
    """
    mean_first = float(np.mean(first_components))
    mean_second = float(np.mean(second_components))
    angle_deg = (math.degrees(math.atan2(mean_second, mean_first)) / 2.0) % 180.0
    mean_length = float(np.mean(np.hypot(first_components, second_components)))
    coherence = math.hypot(mean_first, mean_second) / mean_length if mean_length > 0.0 else 0.0
    return angle_deg, coherence


def _bootstrap_angle_error_deg(
    first_components: np.ndarray, second_components: np.ndarray, generator: np.random.Generator
) -> float:
    """Estimate the error of the stretch direction by resampling the stars.

    Parameters
    ----------
    first_components : `numpy.ndarray`
        ``(Ixx - Iyy) / (Ixx + Iyy)`` of each star.
    second_components : `numpy.ndarray`
        ``2 Ixy / (Ixx + Iyy)`` of each star.
    generator : `numpy.random.Generator`
        The seeded random generator.

    Returns
    -------
    error_deg : `float`
        The spread of the resampled directions about their own circular mean,
        in degrees (each difference wrapped into -90 to +90 degrees).
    """
    draws = generator.integers(
        0, first_components.size, size=(BOOTSTRAP_RESAMPLE_COUNT, first_components.size)
    )
    resampled_first = first_components[draws].mean(axis=1)
    resampled_second = second_components[draws].mean(axis=1)
    angles_deg = np.degrees(np.arctan2(resampled_second, resampled_first)) / 2.0
    center = (
        np.degrees(
            np.arctan2(
                np.sin(np.radians(2.0 * angles_deg)).mean(), np.cos(np.radians(2.0 * angles_deg)).mean()
            )
        )
        / 2.0
    )
    wrapped = (angles_deg - center + 90.0) % 180.0 - 90.0
    return float(np.std(wrapped))


# The star finder's settings. The expected width (4 px) and the 5 sigma
# threshold are the defaults of `SourceDetector`, which finds stars for the
# astrometry stage. Its roundness cut is the one setting that is not copied:
# see `detect_stars_without_shape_cut`.
STAR_FINDER_EXPECTED_FWHM_PX = 4.0

STAR_FINDER_THRESHOLD_SIGMA = 5.0

# The background map for the star finder uses boxes of this fraction of the
# shorter side of the plane, limited to this range in pixels. The same numbers
# as `SourceDetector`: boxes much larger than a star, small enough to follow
# nebula-sized glow. Below three boxes per axis the map is not usable and one
# global value is used instead.
STAR_FINDER_BOX_FRACTION = 1.0 / 20.0

STAR_FINDER_BOX_MINIMUM_PX = 20

STAR_FINDER_BOX_MAXIMUM_PX = 200

STAR_FINDER_MINIMUM_BOXES_PER_AXIS = 3


def detect_stars_without_shape_cut(
    plane: np.ndarray, expected_fwhm_px: float = STAR_FINDER_EXPECTED_FWHM_PX
) -> list[dict]:
    """Find the stars of a plane without refusing the stretched ones.

    `SourceDetector` keeps only sources whose DAOStarFinder roundness lies in
    (-0.6, 0.6), which suits matching stars to a catalog. A star stretched to
    1.5 times as long as it is wide already fails that test, and on synthetic
    frames of 3 px stars the detector finds 4 sources at an elongation of 1.6
    where 294 exist. A width and elongation metric with no elongation cut needs
    those stars, so this function estimates the background the way
    `SourceDetector` does and runs the same star finder core
    (`pipelines/shared/star_finding.py`) with the roundness range left open.
    The sharpness range (0.2 to 1.0) still removes hot pixels and broad glow.
    This module sits in a layer below the astrometry pipeline and may not
    import `SourceDetector` in any case.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.
    expected_fwhm_px : `float`, optional
        How wide a star is expected to be, in the plane's own pixels. Defaults
        to `STAR_FINDER_EXPECTED_FWHM_PX`. A plane of 2 x 2 super-pixels needs
        half that.

    Returns
    -------
    sources : `list` of `dict`
        The detected sources, brightest first, with ``x_centroid``,
        ``y_centroid`` and ``flux``.
    """
    box_size = int(
        np.clip(
            min(plane.shape) * STAR_FINDER_BOX_FRACTION,
            STAR_FINDER_BOX_MINIMUM_PX,
            STAR_FINDER_BOX_MAXIMUM_PX,
        )
    )
    background: np.ndarray | float | None = None
    noise = 0.0
    if min(plane.shape) >= box_size * STAR_FINDER_MINIMUM_BOXES_PER_AXIS:
        try:
            estimate = Background2D(
                plane,
                box_size=box_size,
                sigma_clip=SigmaClip(sigma=3.0),
                bkg_estimator=MedianBackground(),
            )
            background = estimate.background
            noise = float(estimate.background_rms_median)
        except DATA_ERRORS as error:
            logger.warning("Background2D failed for the star finder, using one global value: %s", error)
            background = None
    if background is None:
        _, background, noise = sigma_clipped_stats(plane, sigma=3.0)
    return find_sources_above_background(
        plane - background,
        noise=noise,
        expected_fwhm_px=expected_fwhm_px,
        threshold_sigma=STAR_FINDER_THRESHOLD_SIGMA,
        roundness_range=None,
    )


def _grid_candidates(
    sources: list[dict], shape: tuple[int, int], plane: np.ndarray, top_level_adu: float | None
) -> tuple[list[list[tuple[float, float]]], CandidateRefusals]:
    """Pick the candidate stars of each grid cell.

    A candidate is isolated (no other detected source within
    `MOFFAT_ISOLATION_RADIUS_PX`), has its whole fit box inside the frame, and
    keeps every pixel of the box below `MOFFAT_SATURATION_MARGIN_FRACTION` of
    the top level. The cells are about square and number about
    `MOFFAT_GRID_CELL_COUNT`.

    Parameters
    ----------
    sources : `list` of `dict`
        Detected sources, brightest first, with ``x_centroid`` and
        ``y_centroid``.
    shape : `tuple` [`int`, `int`]
        The plane's ``(rows, columns)``.
    plane : `numpy.ndarray`
        The plane's pixels.
    top_level_adu : `float` or `None`
        The level above which a pixel is not trusted. `None` means no star is
        refused for saturation.

    Returns
    -------
    cells : `list` of `list`
        For each occupied cell, up to `MOFFAT_CANDIDATES_PER_CELL` ``(x, y)``
        centers, brightest first.
    refusals : `CandidateRefusals`
        How many sources each cut refused. A source is counted at the first
        cut it fails, in the order edge, neighbor, saturation. The fit's own
        refusals are added later by the caller.
    """
    rows, columns = shape
    cell_side = math.sqrt(rows * columns / MOFFAT_GRID_CELL_COUNT)
    cells_x = max(round(columns / cell_side), 1)
    cells_y = max(round(rows / cell_side), 1)
    positions = np.array([[source["x_centroid"], source["y_centroid"]] for source in sources], dtype=float)
    tree = cKDTree(positions)
    half = MOFFAT_FIT_BOX_HALF_WIDTH_PX
    cell_lists: dict[tuple[int, int], list[tuple[float, float]]] = {}
    near_edge = near_neighbor = near_saturation = 0
    for index, (x, y) in enumerate(positions):
        center_x, center_y = round(x), round(y)
        if center_x < half or center_y < half or center_x + half >= columns or center_y + half >= rows:
            near_edge += 1
            continue
        neighbors = tree.query_ball_point(positions[index], MOFFAT_ISOLATION_RADIUS_PX)
        if len(neighbors) > 1:
            near_neighbor += 1
            continue
        if top_level_adu is not None:
            box = plane[center_y - half : center_y + half + 1, center_x - half : center_x + half + 1]
            if box.max() >= MOFFAT_SATURATION_MARGIN_FRACTION * top_level_adu:
                near_saturation += 1
                continue
        key = (min(int(x / columns * cells_x), cells_x - 1), min(int(y / rows * cells_y), cells_y - 1))
        cell = cell_lists.setdefault(key, [])
        if len(cell) < MOFFAT_CANDIDATES_PER_CELL:
            cell.append((float(x), float(y)))
    refusals = CandidateRefusals(
        detected=len(sources),
        near_edge=near_edge,
        near_neighbor=near_neighbor,
        near_saturation=near_saturation,
    )
    return list(cell_lists.values()), refusals


def _sum_bayer_cells(plane: np.ndarray) -> np.ndarray:
    """Add up each 2 x 2 cell of a raw color mosaic into one super-pixel.

    A one-shot-color sensor puts a red, two green and a blue filter over every
    2 x 2 block of pixels (the Bayer pattern, named for its inventor). The four
    pixels of a block see the same star through different filters, so the
    pixel-to-pixel pattern of the mosaic adds a fixed 2-pixel ripple to every
    star. That ripple bends the star's second moments and its fitted angle.
    Summing each block removes the ripple, because every block holds one of
    each color, whatever the pattern order (RGGB, BGGR and so on).

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D raw mosaic.

    Returns
    -------
    super_pixels : `numpy.ndarray`
        The plane with an odd last row or column dropped, then every 2 x 2
        block summed. Its sides are half those of the input.
    """
    rows, columns = plane.shape
    trimmed = plane[: rows - rows % 2, : columns - columns % 2]
    return trimmed.reshape(rows // 2, 2, columns // 2, 2).sum(axis=(1, 3))


def measure_star_profiles(
    plane: np.ndarray,
    top_level_adu: float | None,
    stopwatch: Stopwatch | None = None,
    sources: list[dict] | None = None,
    *,
    bayer_mosaic: bool = False,
) -> StarProfileResult:
    """Measure the Moffat width, elongation and angle of one plane's stars.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels, in ADU.
    top_level_adu : `float` or `None`
        The pixel value above which the sensor is not trusted (see
        `usable_top_level_adu`). `None` falls back to the plane's own plateau
        of equal brightest pixels, and to no saturation test when there is
        none.
    stopwatch : `Stopwatch` or `None`, optional
        The frame's time budget. When it runs out the result says so and holds
        no values.
    sources : `list` of `dict` or `None`, optional
        Detected sources of this plane, brightest first. They are detected
        here when not given. Ignored for a Bayer mosaic, whose sources are
        detected on the super-pixels.
    bayer_mosaic : `bool`, optional
        The plane is a raw color mosaic. Each 2 x 2 cell is summed into one
        super-pixel (`BAYER_CELL_SIZE_PX` pixels on a side) and the stars are
        measured on those. The widths are then multiplied by that factor to
        give original pixels. The elongation and the angle are ratios and a
        direction, so the factor does not change them.

    Returns
    -------
    result : `StarProfileResult`
        The medians over the accepted stars, with their bootstrap
        uncertainties, or the reason nothing was measured.

    Notes
    -----
    Pass 1 fits a Moffat profile with a free power index to the brightest
    isolated star of each grid cell (another candidate of the cell if the first
    is refused). Pass 2 holds the power index at the median of pass 1 and fits
    the same stars again. The width is the geometric mean of the two axis
    widths, so a trailed star reads wide. The elongation is not taken from the
    fit but from second moments, which do not depend on the profile shape.
    """
    factor = BAYER_CELL_SIZE_PX if bayer_mosaic else 1
    expected_fwhm_px = STAR_FINDER_EXPECTED_FWHM_PX / factor
    field_shape_px = (plane.shape[0] - plane.shape[0] % factor, plane.shape[1] - plane.shape[1] % factor)
    if bayer_mosaic:
        plane = _sum_bayer_cells(plane)
        sources = None
        # A super-pixel holds the sum of factor ** 2 pixels, so its top level
        # is that many times the pixel's.
        top_level_adu = None if top_level_adu is None else top_level_adu * factor**2
    if sources is None:
        sources = detect_stars_without_shape_cut(plane, expected_fwhm_px)
    if stopwatch is not None and stopwatch.expired():
        return StarProfileResult(reason=stopwatch.reason())
    if not sources:
        return StarProfileResult(reason="no stars were detected in the frame")
    if top_level_adu is None:
        top_level_adu = plateau_saturation_level_adu(plane)
    cells, refusals = _grid_candidates(sources, plane.shape, plane, top_level_adu)
    half = MOFFAT_FIT_BOX_HALF_WIDTH_PX
    grid = _subpixel_grid(2 * half + 1)

    def cutout_at(x: float, y: float) -> np.ndarray:
        """Cut the fit box around a detected star.

        Returns
        -------
        cutout : `numpy.ndarray`
            The box of pixels.
        """
        center_x, center_y = round(x), round(y)
        return plane[center_y - half : center_y + half + 1, center_x - half : center_x + half + 1]

    first_pass: list[tuple[tuple[float, float], _FitResult]] = []
    candidate_count = 0
    for cell in cells:
        for x, y in cell:
            if stopwatch is not None and stopwatch.expired():
                return StarProfileResult(reason=stopwatch.reason(), candidate_count=candidate_count)
            candidate_count += 1
            fit = _fit_moffat(cutout_at(x, y), grid, fixed_beta=None)
            if fit is not None:
                first_pass.append(((x, y), fit))
                break
    first_pass_refused = candidate_count - len(first_pass)
    refusals = replace(refusals, fit_refused=first_pass_refused)
    if len(first_pass) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(first_pass),
            refusals=refusals,
            reason=(
                f"only {len(first_pass)} isolated, unsaturated stars fitted from {candidate_count} tried; "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed ({refusals.describe()})"
            ),
        )
    shared_beta = float(
        np.clip(np.median([fit.beta for _, fit in first_pass]), MOFFAT_BETA_MINIMUM, MOFFAT_BETA_MAXIMUM)
    )
    widths: list[float] = []
    samples: list[StarWidthSample] = []
    elongations: list[float] = []
    first_components: list[float] = []
    second_components: list[float] = []
    for (x, y), _ in first_pass:
        if stopwatch is not None and stopwatch.expired():
            return StarProfileResult(reason=stopwatch.reason(), candidate_count=candidate_count)
        cutout = cutout_at(x, y)
        fit = _fit_moffat(cutout, grid, fixed_beta=shared_beta)
        if fit is None:
            continue
        shape = _measure_second_moments(cutout, fit)
        if shape is None:
            continue
        widths.append(factor * math.sqrt(fit.fwhm_major_px * fit.fwhm_minor_px))
        # A super-pixel's center is at its first pixel plus (factor - 1) / 2
        # original pixels, so (x + 0.5) * factor - 0.5 puts the star back on
        # the original pixel grid (it is x itself when the factor is 1).
        samples.append(StarWidthSample((x + 0.5) * factor - 0.5, (y + 0.5) * factor - 0.5, widths[-1]))
        elongations.append(shape[0])
        first_components.append(shape[1])
        second_components.append(shape[2])
    refusals = replace(refusals, fit_refused=first_pass_refused + len(first_pass) - len(widths))
    if len(widths) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(widths),
            refusals=refusals,
            reason=(
                f"only {len(widths)} stars survived the second pass (shared beta {shared_beta:.2f}); "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed ({refusals.describe()})"
            ),
        )
    width_array = np.array(widths)
    elongation_array = np.array(elongations)
    first_array = np.array(first_components)
    second_array = np.array(second_components)
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    mean_angle, coherence = _mean_ellipticity_angle(first_array, second_array)
    return StarProfileResult(
        width_px=float(np.median(width_array)),
        width_px_uncertainty=bootstrap_median_error(width_array, generator),
        elongation=float(np.median(elongation_array)),
        elongation_uncertainty=bootstrap_median_error(elongation_array, generator),
        angle_deg=mean_angle,
        angle_uncertainty_deg=_bootstrap_angle_error_deg(first_array, second_array, generator),
        angle_coherence=coherence,
        star_count=len(widths),
        candidate_count=candidate_count,
        median_beta=shared_beta,
        refusals=refusals,
        samples=tuple(samples),
        field_shape_px=field_shape_px,
    )


@dataclass(frozen=True)
class RegionWidth:
    """The median star width in one region of the field.

    Attributes
    ----------
    region : `str`
        One of `FIELD_REGION_NAMES`.
    star_count : `int`
        How many fitted stars lie in the region.
    width_px : `float` or `None`
        Their median Moffat FWHM in original pixels, or `None` when the region
        holds fewer than `MOFFAT_MINIMUM_STAR_COUNT` stars.
    width_px_uncertainty : `float` or `None`
        The bootstrap standard error of that median.
    """

    region: str
    star_count: int
    width_px: float | None = None
    width_px_uncertainty: float | None = None


def _field_region_of(column_cell: int, row_cell: int) -> str | None:
    """Name the region a grid cell belongs to.

    Parameters
    ----------
    column_cell, row_cell : `int`
        The cell's index along x and along y, from 0 to
        `FIELD_GRID_CELLS_PER_SIDE` - 1.

    Returns
    -------
    region : `str` or `None`
        One of `FIELD_REGION_NAMES`, or `None` for a cell outside all five
        regions.
    """
    side = FIELD_GRID_CELLS_PER_SIDE
    block = FIELD_REGION_CELLS_PER_SIDE
    middle_start = (side - block) // 2

    def band(cell: int) -> str | None:
        """Name the part of the grid axis that a cell index falls in.

        Returns
        -------
        band : `str` or `None`
            ``"low"``, ``"middle"``, ``"high"`` or `None`.
        """
        if cell < block:
            return "low"
        if cell >= side - block:
            return "high"
        if middle_start <= cell < middle_start + block:
            return "middle"
        return None

    column_band, row_band = band(column_cell), band(row_cell)
    if column_band == "middle" and row_band == "middle":
        return REGION_CENTER
    corner_by_bands = {
        ("low", "low"): REGION_XLOW_YLOW,
        ("high", "low"): REGION_XHIGH_YLOW,
        ("low", "high"): REGION_XLOW_YHIGH,
        ("high", "high"): REGION_XHIGH_YHIGH,
    }
    return corner_by_bands.get((column_band, row_band))


def measure_field_region_widths(result: StarProfileResult) -> list[RegionWidth]:
    """Give the median star width of the center and of each corner.

    The stars are those the frame's width already rests on (their fits are not
    repeated). Each is put in one of the five equal regions described at
    `FIELD_REGION_NAMES`; a star between the regions is left out.

    Parameters
    ----------
    result : `StarProfileResult`
        The frame's star profile result, with its samples and field shape.

    Returns
    -------
    regions : `list` of `RegionWidth`
        One entry for each region, in `FIELD_REGION_NAMES` order. The width and
        its error are `None` for a region with fewer than
        `MOFFAT_MINIMUM_STAR_COUNT` stars; the count is always given.
    """
    members: dict[str, list[float]] = {name: [] for name in FIELD_REGION_NAMES}
    if result.field_shape_px is not None:
        rows, columns = result.field_shape_px
        side = FIELD_GRID_CELLS_PER_SIDE
        for sample in result.samples:
            column_cell = min(side - 1, max(0, int(sample.x_px / columns * side)))
            row_cell = min(side - 1, max(0, int(sample.y_px / rows * side)))
            region = _field_region_of(column_cell, row_cell)
            if region is not None:
                members[region].append(sample.width_px)
    regions = []
    for name in FIELD_REGION_NAMES:
        widths = np.array(members[name])
        if len(widths) < MOFFAT_MINIMUM_STAR_COUNT:
            regions.append(RegionWidth(name, len(widths)))
            continue
        generator = np.random.default_rng(BOOTSTRAP_SEED)
        regions.append(
            RegionWidth(
                name,
                len(widths),
                float(np.median(widths)),
                bootstrap_median_error(widths, generator),
            )
        )
    return regions


def effective_star_width_px(width_px: float) -> float:
    """Add the blur of one square pixel to a star's intrinsic width.

    Parameters
    ----------
    width_px : `float`
        The star's fitted Moffat FWHM in original pixels. The fit integrates
        the profile over each pixel, so this is the width before the pixel's
        blur.

    Returns
    -------
    effective_width_px : `float`
        ``sqrt(width_px ** 2 + c)`` with ``c`` equal to
        `PIXEL_BLUR_FWHM_SQUARED_FACTOR`, in original pixels (the pixel side
        is 1 in these units).
    """
    return math.sqrt(width_px**2 + PIXEL_BLUR_FWHM_SQUARED_FACTOR)
