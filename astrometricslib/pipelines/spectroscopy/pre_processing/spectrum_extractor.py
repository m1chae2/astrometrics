"""Tools to read the brightness of a spectral streak in an image.

This file provides two ways to measure a spectrum:
1. Basic: Draws a straight rectangular box over the spectrum and adds up
   all the light inside it.
2. Advanced (Traced): Carefully follows the exact center of the spectrum as
   it bends or widens. It sizes the box from the spectrum's width, smoothed
   over many steps so that fit noise does not make the box jump by a whole
   pixel from one step to the next (`traced_aperture_half_widths_px`). The
   box can end part-way through a pixel, and each edge pixel counts for the
   fraction of it inside the box (`box_pixel_weights`).

Pipeline stage: sky background subtraction
------------------------------------------
Purpose: the box we add up contains the star's light AND the glow of the
night sky (city lights, moonlight, airglow). If we leave the sky in, it
does two kinds of damage to the spectrum:

* It adds a flat pedestal under the whole spectrum. Dark absorption lines
  are measured against "star plus sky" instead of "star alone", so they
  look shallower than they really are.
* Sky glow has its own bright lines (for example sodium and mercury street
  lamps). These add fake bumps at exact wavelengths, including near real
  absorption lines such as the sodium D line.

So, at every step along the spectrum, every extraction method in this file
measures the sky in narrow "sky bands" just outside the box, on both sides
of the streak, and subtracts that sky level from the box total. The sky
level is the mean of the two bands' medians, or the lower-valued band's
median alone when the bands disagree by more than noise allows
(`measure_sky`). This stage runs BEFORE wavelength calibration, so the
calibrator and every later stage (feature detection, classification) only
ever see star light.

In a crowded field the "sky" beside a star is mostly the light of other
stars: their spectra overlap, and the glow around each one adds up. That
light is under our star's box as well, so subtracting it is right. On the
M 13 stack it was large: for stars of magnitude 8.5-9, 50-90% of the raw
spectrum was this background, growing toward the red, and K stars looked
like M stars until it was removed (see
logs/k_star_sky_investigation_20260919.json). The Vega frame, by contrast,
is a bright star in a sparse field, and only 0.6% of its spectrum was sky.
Check a fainter star in a busier field before trusting a change to this
stage.

Per-sample uncertainty
----------------------
When the extractor has a `PixelNoiseModel`, every reading also gets a
variance (the square of its 1-sigma error), kept in
`last_diagnostics.sample_variance`. The variance is the photon and read
noise of the pixels in the box, with each edge pixel's fraction squared,
plus the variance of the sky level times the square of the box's total
weight. A sample between two columns blends two readings, and its variance
blends theirs with the squared weights. `intensity_variance` describes the
model and its limits.

What this stage does NOT do: it does not remove Earth's atmosphere
absorption (telluric lines from oxygen and water vapour). Those are dips
in the star's own light after it passes through the air, so they are not
part of the sky glow that we measure beside the streak.

Limits of adding up a box
-------------------------
Both methods here add up the light in a box. That is simple and works well
for a star whose streak looks the same at every colour. It gives a
distorted spectrum when the streak's blur changes with wavelength, because
the light of neighbouring wavelengths then lands in each other's boxes.
Neveu et al. (2024, Astronomy & Astrophysics 684, A21) describe this and
the alternative: model the whole 2D image, with a blur for every
wavelength, and fit it to the pixels. We do not do that.

The blur does change with wavelength in this setup. A grating placed in a
converging beam (the Star Analyzer 200 sits in front of the sensor) blurs
the redder light more, and that paper reports the same for a grating of
that kind. Measured on the stored spectra (2026-09-26, the fitted trail
width, in pixels): Vega 1.2 at 4200 A, 1.9 at 5000 A, 1.6 at 6600 A and
1.9 at 7400 A, and the other four stars checked show the same pattern. It
is not a steady rise, so we have no simple correction for it.

What this costs, as far as we know:

* Balmer lines look shallower than the reference spectra's, more so toward
  the red. On Vega, the observed depth as a fraction of the reference's
  (2026-09-26, blurring the reference with a width that grows with
  wavelength instead of one fixed width) went from 1.03 to 0.98 at
  H-gamma, 0.68 to 0.85 at H-beta and 0.23 to 0.65 at H-alpha. So part of
  the gap comes from the blur changing with colour, and part is still
  unexplained. Treat line depths, especially H-alpha, as understated.
* The zero-order star of a bright target is saturated, so its position
  comes from `find_zero_order_position`, not from a fit to the star.

Do not read this as a measured size of the error for any one star. It has
not been tested that a 2D fit would remove the gap.
"""

import logging
import math
import warnings
from dataclasses import dataclass, field

import numpy as np
from astropy.modeling import fitting, models
from scipy.ndimage import binary_dilation, median_filter

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.spectroscopy.pre_processing.intensity_variance import (
    PixelNoiseModel,
    combine_neighbour_variances,
    median_variance_factor,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

try:
    # pyrefly: ignore[missing-import] -- optional C accelerator built from
    # _extractor_c.c. Only the source is tracked, so the compiled module is
    # absent until it is built, and a static checker cannot introspect the .so
    # even once it is. The except branch below is the supported path.
    #
    # Why C, measured (2026-08-27): this function runs once per pixel column
    # along a spectrum's dispersion axis, and with this observatory's real
    # camera config that's ~416 calls per star, 10+ stars per frame. A
    # benchmark comparing this C fit against the pure-Python fallback below
    # (same Gaussian math, on a realistic synthetic trace) measured the C
    # path at ~2.7 microseconds/call versus ~890 microseconds/call for the
    # Python fallback (astropy's LevMarLSQFitter re-instantiated every call)
    # -- about 325x slower. At the real per-frame call volume, going
    # Python-only would add roughly 3.7 seconds of pure fit time per frame,
    # which is not negligible against a session's total processing time.
    # That's why this stays a C extension instead of being simplified away.
    from astrometricslib.pipelines.spectroscopy.pre_processing._extractor_c import (
        fit_cross_section_gaussian_c as _fit_cross_section_c,
    )

    HAS_C_EXTENSION = True
except ImportError:
    _fit_cross_section_c = None
    HAS_C_EXTENSION = False

# The minimum number of pixels we need to confidently find the center of
# the spectrum line. If the line is too thin, the math will fail, so we
# fall back to a simpler method.
_MINIMUM_CROSS_SECTION_SAMPLES = 5

# How wide to make the reading box, compared to the measured width of the
# spectrum. A value of 2.5 is wide enough to capture almost all (99%) of
# the star's light without accidentally including too much empty black sky.
APERTURE_SIGMA_MULTIPLIER = 2.5

# How many neighbouring steps the width is smoothed over before it sets the
# box size. The width is fitted again at every step and each fit is noisy
# (about 0.01 px on a bright star, several times more on a faint one). A box
# that follows each fit directly changes size from step to step, and every
# change shows up in the spectrum as a step in brightness that the star did
# not cause. The real width changes over hundreds of steps (Vega: 1.2 px at
# 4200 A to 1.9 px at 5000 A, 400 or so steps), so a median over 61 steps
# removes the noise and still follows the change. 61 steps is about a tenth
# of a typical trail (Vega's has 563). Chosen by judgement, not tuned.
APERTURE_SIGMA_SMOOTHING_STEPS = 61

# The smallest box half-width the traced extraction will use, in pixels.
_MINIMUM_APERTURE_HALF_WIDTH_PX = 1.0

# The narrowest line width (in pixels) we'll accept as a real measurement,
# not a math mistake. A Gaussian fit needs a handful of pixels spread
# across its width to tell a real line from noise; if the fitter reports
# something narrower than half a pixel, it isn't describing the star --
# it has locked onto a single noisy sample and shrunk the curve down to
# fit that one point almost exactly, the same way you could draw a
# "curve" through just one dot on a graph. This exact failure was found
# empirically while testing this file's C extension against its Python
# fallback: on a noisy, barely-resolved line, one solver's fit collapsed
# to a sigma of about 1e-38, which is far below anything a real spectral
# line could be, but was still being accepted because the only existing
# check was "greater than zero".
_MINIMUM_FIT_SIGMA_PX = 0.5

# --- Sky background subtraction (see the module docstring) ---------------
# The sky is measured in two strips ("sky bands"), one on each side of the
# reading box, like this (top-down view across the streak):
#
#     | sky band | gap | reading box (the star) | gap | sky band |
#
# The gap keeps the sky bands away from the star's own faint outer glow (its
# "wings"), which stretches past the reading box. If a band touches the
# wings, we would subtract some of the star's real light and call it sky.
# gap = 6 px: swept on a real frame (Vega, ASI533MM Pro + 405 mm, 2026-09-19,
# see logs/sky_band_sweep_vega_20260919.json). Moving the bands out from a
# 2 px gap to a 6 px gap dropped the amount "removed" from 2.05% to 0.57% of
# the total flux, which is the wings leaving the band. Past 6 px the drop is
# slower (0.46% at 8 px, 0.17% at 30 px), so the extra distance gains little
# but risks reaching a neighbouring star's spectrum. That sweep is a
# one-frame result on a sparse field. The same 6 px gap was later used on the
# crowded M 13 stack, where it gave K0V for a K0 star and correct-side types
# for most of the others (see logs/k_star_sky_investigation_20260919.json),
# but no sweep of the gap was run there.
SKY_BAND_GAP_PX = 6

# Wider bands give a steadier median (more pixels, less noise). Narrower
# bands follow a changing sky better. 10 px gives 20 sky pixels per step,
# enough that a few odd pixels cannot move the median, while the bands stay
# close to the star. Chosen in the same Vega sweep as the gap above (gap 6,
# width 10). Line depths (Na D, H-beta, H-gamma) moved by less than 0.002
# across all the widths tried, so this is not a sensitive setting on that
# frame. Same one-frame caveat as the gap.
SKY_BAND_WIDTH_PX = 10

# The fewest sky pixels we will trust in one band. Near the edge of the image
# a band can be cut off. If fewer than this many pixels are left in a band,
# one or two noisy pixels would decide its sky level, so that band is not
# used. If neither band has enough, we skip the subtraction for that step
# instead of guessing.
SKY_BAND_MINIMUM_SAMPLE_COUNT = 4


def fit_cross_section_gaussian(
    data: np.ndarray,
    center: tuple[float, float],
    perpendicular_vector: tuple[float, float],
    search_radius: float,
) -> tuple[float, float] | None:
    """Look sideways across the spectrum to find its exact center and width.

    Parameters
    ----------
    data : `numpy.ndarray`
        The picture data.
    center : `tuple[float, float]`
        Where we think the center is `(x, y)`.
    perpendicular_vector : `tuple[float, float]`
        An arrow pointing exactly sideways across the spectrum.
    search_radius : `float`
        How many pixels sideways to look.

    Returns
    -------
    fit_result : `tuple[float, float]` or `None`
        How far off our guess was (offset) and how wide the line is (sigma).
        Returns None if we couldn't find a clear line.

    Raises
    ------
    InvalidArgumentError
        If `data` is not a 2-D array.
    """
    if data.ndim != 2:
        raise InvalidArgumentError(f"Cross-section fitting needs a 2-D image, got {data.ndim} dimensions.")

    if HAS_C_EXTENSION and _fit_cross_section_c is not None:
        # The C path reads float64 pixels directly out of the array
        # buffer, so hand it exactly that. This is free on the hot path:
        # AstrometricsImage.data is already contiguous float64, and
        # ascontiguousarray returns that same object untouched rather
        # than copying (measured at 0.3 microseconds for a 4000x6000
        # frame). Only an unusual dtype or a strided view pays a copy,
        # and those would otherwise be the cases the C path got wrong.
        c_input = np.ascontiguousarray(data, dtype=np.float64)
        try:
            c_res = _fit_cross_section_c(c_input, center, perpendicular_vector, float(search_radius))
            if c_res is not None:
                return c_res
        except DATA_ERRORS as exc:
            logger.debug("C extension cross-section fit failed, falling back to Python: %s", exc)

    return _fit_cross_section_gaussian_python(data, center, perpendicular_vector, search_radius)


def _fit_cross_section_gaussian_python(
    data: np.ndarray,
    center: tuple[float, float],
    perpendicular_vector: tuple[float, float],
    search_radius: float,
) -> tuple[float, float] | None:
    """Fit the cross section with astropy, without trying the C extension.

    This is the reference implementation. `fit_cross_section_gaussian`
    uses the C extension when it is available and falls back to this,
    and `test_extractor_c_equivalence.py` fits the same cross sections
    both ways to show the two agree. It is a separate function so that
    test can reach the astropy path directly, instead of switching a
    module-level flag off and hoping nothing else reads it.

    Parameters
    ----------
    data : `numpy.ndarray`
        The picture data.
    center : `tuple[float, float]`
        Where we think the center is `(x, y)`.
    perpendicular_vector : `tuple[float, float]`
        An arrow pointing exactly sideways across the spectrum.
    search_radius : `float`
        How many pixels sideways to look.

    Returns
    -------
    fit_result : `tuple[float, float]` or `None`
        How far off our guess was (offset) and how wide the line is (sigma).
        Returns None if we couldn't find a clear line.
    """
    height, width = data.shape
    perpendicular_x, perpendicular_y = perpendicular_vector
    center_x, center_y = center

    offsets = np.arange(-int(search_radius), int(search_radius) + 1)
    pixel_x = np.rint(center_x + offsets * perpendicular_x).astype(int)
    pixel_y = np.rint(center_y + offsets * perpendicular_y).astype(int)

    valid_mask = (pixel_x >= 0) & (pixel_x < width) & (pixel_y >= 0) & (pixel_y < height)
    if np.count_nonzero(valid_mask) < _MINIMUM_CROSS_SECTION_SAMPLES:
        return None

    offsets_array = offsets[valid_mask].astype(float)
    values_array = data[pixel_y[valid_mask], pixel_x[valid_mask]].astype(float)

    edge_sample_count = min(2, len(values_array) // 2)
    background = float(
        np.median(np.concatenate([values_array[:edge_sample_count], values_array[-edge_sample_count:]]))
    )
    background_subtracted = values_array - background

    amplitude_guess = float(np.max(background_subtracted))
    if amplitude_guess <= 0:
        return None
    mean_guess = float(offsets_array[np.argmax(background_subtracted)])

    gaussian_model = models.Gaussian1D(amplitude=amplitude_guess, mean=mean_guess, stddev=search_radius / 3.0)
    fitter = fitting.LevMarLSQFitter()

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            fitted_model = fitter(gaussian_model, offsets_array, background_subtracted)
    except (RuntimeError, *DATA_ERRORS):
        # RuntimeError covers astropy's NonFiniteValueError (NaN in the data).
        return None

    center_offset = float(fitted_model.mean.value)
    sigma = float(fitted_model.stddev.value)

    if not np.isfinite(center_offset) or not np.isfinite(sigma):
        return None
    if sigma < _MINIMUM_FIT_SIGMA_PX or sigma > search_radius * 2:
        return None
    if abs(center_offset) > search_radius:
        return None

    return center_offset, sigma


def fit_trail_centerline_polynomial(raw_centers: list[float | None], degree: int) -> list[float]:
    """Draw a smooth curve through all the center points we found.

    Sometimes we can't find the center perfectly because a pixel is too dark
    or completely blown out (white). This fits a smooth curve through the
    points we *did* find, so we can guess where the center should have been
    in the bad spots.

    Parameters
    ----------
    raw_centers : `List[Optional[float]]`
        One entry per step along the dispersion axis; `None` where the
        per-position Gaussian fit failed.
    degree : `int`
        Polynomial degree to fit.

    Returns
    -------
    smoothed_centers : `List[float]`
        One smoothed centerline value per step, same length as
        `raw_centers`. Falls back to all-zero (no correction) if too few
        steps produced a valid fit to constrain the requested degree.

    """
    valid_indices = [index for index, center in enumerate(raw_centers) if center is not None]
    if len(valid_indices) < degree + 1:
        return [0.0] * len(raw_centers)

    valid_values = [raw_centers[index] for index in valid_indices]
    coefficients = np.polyfit(valid_indices, valid_values, deg=degree)
    all_indices = np.arange(len(raw_centers))
    return [float(value) for value in np.polyval(coefficients, all_indices)]


# A line of pixels across a nebula's box is smooth on the scale of tens of
# pixels, but another star's trail crossing it is a narrow spike. To tell them
# apart, each line is compared with a median-smoothed copy of itself. A
# running median ignores anything narrower than half its window, so the window
# must be wider than twice a trail and narrower than the nebula's own
# structure. Measured on the M 27 spectral stack (2026-09-24), the flagged
# spikes were a median 7 pixels wide (5-11 for the middle 80%, including the
# dilation below), so 31 pixels is comfortably wide enough. It is a first
# choice, checked only on M 27 and M 57 and not tuned.
CONTAMINANT_BASELINE_WIDTH_PX = 31

# A pixel counts as part of a contaminating spike when it stands this many
# noise-widths above the smoothed copy. 4 is the usual "clearly not noise"
# cut: with Gaussian noise fewer than 1 pixel in 15,000 passes by chance.
CONTAMINANT_THRESHOLD_SIGMA = 4.0

# Each side of a flagged spike is also replaced, because a trail's faint
# edges sit below the threshold but still carry its light. Two pixels is a
# judgement, not a measurement: on M 27 it leaves 5.9% of the box replaced.
CONTAMINANT_DILATION_PX = 2


def replace_narrow_spikes(cross_section: np.ndarray) -> np.ndarray:
    """Swap narrow bright spikes in one line of pixels for the smooth level.

    This is the measuring half of the contaminant rejection stage. It is
    meant for a nebula's wide reading box, where the trails and zero-order
    points of ordinary stars cross the box and would otherwise be added to
    the nebula's spectrum as fake bumps (on M 27 the strongest, near 6100
    Angstroms, was 0.32 in units where the nebula's hump is 0.1). Turning
    this on for ordinary point-source extraction was tried on 2026-09-27,
    after a star (HD 5747, a G8III) showed the same kind of narrow spike
    (near 5170 Angstroms) in its own raw spectrum, and reverted the same
    day: a star's own reading box is itself narrower than
    `CONTAMINANT_BASELINE_WIDTH_PX`, so its own real trace reads as a
    "spike" against that wider baseline and gets erased. See
    `SpectroscopyConfig.reject_narrow_contaminants` for the regression this
    caused and what would need to change before trying again.

    Steps:
    1. Smooth the line with a running median. A median ignores a spike
       that is narrower than half the window, so the result follows the
       nebula and the sky but not the trails.
    2. Subtract the smoothed line. What is left is noise plus the spikes.
    3. Measure the noise from the middle value of the leftovers' distance
       from their own middle (the median absolute deviation, times 1.4826 to
       match a Gaussian's spread). Spikes are too few to move it.
    4. Any pixel more than `CONTAMINANT_THRESHOLD_SIGMA` noise-widths above
       the smoothed line is a spike (on a line with no noise at all, any
       excess is a spike). It and its neighbours within
       `CONTAMINANT_DILATION_PX` are replaced by the smoothed value.

    Only bright spikes are replaced. A dark dip (a dead pixel, a gap) is
    left alone because other stars can only add light, never remove it.

    Parameters
    ----------
    cross_section : `numpy.ndarray`
        One line of pixels across the box.

    Returns
    -------
    cleaned : `numpy.ndarray`
        A copy of the line with spikes replaced. The input is not changed.
        A line shorter than the smoothing window comes back unchanged.
    """
    values = np.asarray(cross_section, dtype=float)
    if values.size < CONTAMINANT_BASELINE_WIDTH_PX:
        return values.copy()
    finite = np.isfinite(values)
    if not finite.all():
        return values.copy()
    baseline = median_filter(values, size=CONTAMINANT_BASELINE_WIDTH_PX, mode="nearest")
    residual = values - baseline
    noise_width = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    is_spike = residual > CONTAMINANT_THRESHOLD_SIGMA * noise_width
    if not is_spike.any():
        return values.copy()
    is_spike = binary_dilation(is_spike, iterations=CONTAMINANT_DILATION_PX)
    return np.where(is_spike, baseline, values)


# Each sky band is cleaned before its median is taken: pixels further than
# this many sigma from the band's median are dropped, and the median and
# sigma are found again, until nothing more is dropped. Three sigma drops a
# hot pixel, a cosmic ray or the edge of a faint passing trail, and keeps
# 99.7 percent of plain sky noise, so it leaves the median unbiased.
SKY_CLIP_SIGMA = 3.0

# The most clipping passes per band. Three or four passes settle on a typical
# band of 10 pixels; the cap only stops a pathological band from looping.
SKY_CLIP_MAX_PASSES = 5

# How many times the noise of the difference between the two band medians
# the bands may differ before the pipeline treats the higher one as
# contaminated (a neighbour's trail or glow in it) and uses only the
# lower-valued band. A false alarm costs little: the answer then falls back
# to that band alone, which reads about half a band-median noise low.
SKY_CONTAMINATION_SIGMA = 3.0

# The noise used to judge whether the two band medians differ is the lower
# of the two bands' pixel scatter, but never below this fraction of the
# scatter of both bands pooled. With only 10 pixels per band, the lower of
# two scatter estimates is often well below the true scatter, and with the
# lower one alone about 11 percent of steps on plain sky noise were flagged
# as contaminated; with this floor about 4 percent are, while a band half
# filled by a neighbour's trail is still flagged in most cases (simulated
# 2026-10-10: 10-pixel bands of normal noise, clean sky bias -0.30 ADU at 13
# ADU of noise per pixel, against -2.6 ADU for taking the lower median).
# Chosen by simulation, not from real frames.
SKY_POOLED_NOISE_FLOOR = 0.75

# The standard error of a median of normal data is this many times the
# data's sigma divided by the square root of the sample count:
# sqrt(pi / 2) = 1.2533.
MEDIAN_STANDARD_ERROR_FACTOR = 1.2533

# The labels for how a sky level was found; see `SkyMeasurement`.
SKY_MODE_BOTH_BANDS = "both_bands"
SKY_MODE_LOWER_BAND_CONTAMINATED = "lower_band_contaminated"
SKY_MODE_UPPER_BAND_CONTAMINATED = "upper_band_contaminated"
SKY_MODE_SINGLE_BAND = "single_band"
SKY_MODE_NO_SKY = "no_sky"


@dataclass(frozen=True)
class SkyMeasurement:
    """The sky level at one step along the spectrum and how it was found.

    Attributes
    ----------
    level_per_pixel : `float`
        The sky brightness of a single pixel, in ADU (the camera's counts).
        `0.0` when no sky could be measured.
    mode : `str`
        How the level was found. `"both_bands"`: the mean of the two band
        medians. `"lower_band_contaminated"`: the band on the low-index
        side of the trail was the brighter of the two and differed from the
        other by more than `SKY_CONTAMINATION_SIGMA` times the noise, so
        only the other band was used. `"upper_band_contaminated"`: the same
        for the band on the high-index side. `"single_band"`: only one band
        had enough pixels on the image. `"no_sky"`: neither had.
    level_variance : `float`
        The variance of `level_per_pixel` (the square of its 1-sigma
        error), in ADU squared. Each band's median has a variance of about
        ``(pi / 2) * s**2 / (n + 1.4)`` for `n` pixels with scatter `s` (see
        `median_variance_factor`); the mean of two bands has a quarter of
        the sum of their two variances. `0.0` when no sky could be measured.
    """

    level_per_pixel: float
    mode: str
    level_variance: float = 0.0


@dataclass
class ExtractionDiagnostics:
    """What the extractor did during its latest extraction.

    The extractor resets this at the start of every `extract_*` call.

    Attributes
    ----------
    aperture_half_width_px : `list` [`float`]
        The half-width of the reading box at each step of a traced
        extraction, in pixels. A box reaches this far each side of its
        centre. Empty for the untraced methods, which use the same fixed
        radius at every step.
    sky_mode_counts : `dict` [`str`, `int`]
        How many sky readings used each `SkyMeasurement.mode`. Each step
        makes one reading, and a step between two columns makes two.
    sample_variance : `list` [`float`]
        The variance of each step's reading (the square of its 1-sigma
        error), in the image's units squared, one value per step in the same
        order as the profile the `extract_*` call returned. NaN for a step
        that is off the image. Empty when the extractor has no
        `PixelNoiseModel`. The summary from `as_dict` leaves it out.
    """

    aperture_half_width_px: list[float] = field(default_factory=list)
    sky_mode_counts: dict[str, int] = field(default_factory=dict)
    sample_variance: list[float] = field(default_factory=list)

    @property
    def dominant_sky_mode(self) -> str | None:
        """Give the sky mode used by the most readings.

        Returns
        -------
        mode : `str` or `None`
            The most common `SkyMeasurement.mode`, or `None` when no sky
            was read.
        """
        if not self.sky_mode_counts:
            return None
        return max(self.sky_mode_counts, key=lambda mode: self.sky_mode_counts[mode])

    @property
    def contaminated_sky_fraction(self) -> float:
        """Give the fraction of sky readings that had to drop a band.

        Returns
        -------
        fraction : `float`
            The share of readings in either "contaminated" mode, 0 to 1.
            `0.0` when no sky was read.
        """
        total = sum(self.sky_mode_counts.values())
        if total == 0:
            return 0.0
        contaminated = self.sky_mode_counts.get(SKY_MODE_LOWER_BAND_CONTAMINATED, 0) + (
            self.sky_mode_counts.get(SKY_MODE_UPPER_BAND_CONTAMINATED, 0)
        )
        return contaminated / total

    def as_dict(self) -> dict[str, object]:
        """Give a short summary of the extraction as plain values.

        The summary is what the pipeline saves on the star's spectroscopy
        result. The list of half-widths is boiled down to its median and its
        spread, so a long trace does not bloat the saved record.

        Returns
        -------
        summary : `dict`
            ``sky_mode_counts`` (`dict` [`str`, `int`]), ``dominant_sky_mode``
            (`str` or `None`), ``contaminated_sky_fraction`` (`float`),
            ``aperture_half_width_median_px`` (`float` or `None`) and
            ``aperture_half_width_spread_px`` (`float` or `None`, the
            standard deviation). Both half-width values are `None` for the
            untraced methods, which keep no half-widths.
        """
        half_widths = np.asarray(self.aperture_half_width_px, dtype=float)
        has_widths = half_widths.size > 0
        return {
            "sky_mode_counts": {mode: int(count) for mode, count in self.sky_mode_counts.items()},
            "dominant_sky_mode": self.dominant_sky_mode,
            "contaminated_sky_fraction": float(self.contaminated_sky_fraction),
            "aperture_half_width_median_px": float(np.median(half_widths)) if has_widths else None,
            "aperture_half_width_spread_px": float(np.std(half_widths)) if has_widths else None,
        }


def _clipped_band_statistics(band: np.ndarray) -> tuple[float, float, int] | None:
    """Find a sky band's median and noise after clipping outlying pixels.

    Parameters
    ----------
    band : `numpy.ndarray`
        The pixels of one sky band, in ADU. Non-finite values are ignored.

    Returns
    -------
    statistics : `tuple` [`float`, `float`, `int`] or `None`
        The median, the standard deviation of one pixel, and the number of
        pixels kept, or `None` when fewer than `SKY_BAND_MINIMUM_SAMPLE_COUNT`
        finite pixels exist.
    """
    pixels = band.astype(float)
    pixels = pixels[np.isfinite(pixels)]
    if pixels.size < SKY_BAND_MINIMUM_SAMPLE_COUNT:
        return None
    for _ in range(SKY_CLIP_MAX_PASSES):
        median = float(np.median(pixels))
        # 1.4826 times the median absolute deviation is the sigma of normal
        # data, and a bright pixel cannot inflate it the way it inflates a
        # plain standard deviation.
        robust_sigma = 1.4826 * float(np.median(np.abs(pixels - median)))
        if robust_sigma <= 0.0:
            break
        kept = pixels[np.abs(pixels - median) <= SKY_CLIP_SIGMA * robust_sigma]
        if kept.size == pixels.size or kept.size < SKY_BAND_MINIMUM_SAMPLE_COUNT:
            break
        pixels = kept
    noise = float(np.std(pixels, ddof=1)) if pixels.size > 1 else 0.0
    return float(np.median(pixels)), noise, int(pixels.size)


def _sky_level_variance(bands: list[tuple[float, float, int]], noise_model: PixelNoiseModel | None) -> float:
    """Give the variance of the sky level made from one or two bands.

    Each band's median has a variance of ``s**2 * median_variance_factor(n)``
    for `n` kept pixels with pixel variance ``s**2``. The sky level is the
    mean of the medians of the bands in use, so its variance is the sum of
    those over the square of the band count.

    The pixel variance `s**2` is the scatter of the bands' pixels about their
    medians, pooled over the bands in use (``n - 1`` degrees of freedom
    each). A band of ten pixels gives a noisy scatter, and the 3-sigma
    clipping in `_clipped_band_statistics` reads it about 12 percent low in
    variance on plain noise. So when a noise model is given, `s**2` is never
    taken below what the camera model predicts for pixels at the sky level
    (``a * level + b``, see `PixelNoiseModel`): sky pixels cannot scatter
    less than their photon and read noise. A larger measured scatter (a
    neighbour's light in the band, flat-field residue) still counts in full.

    Parameters
    ----------
    bands : `list` [`tuple` [`float`, `float`, `int`]]
        The median, pixel scatter and kept-pixel count of each band in use,
        as `_clipped_band_statistics` returns them. One or two entries.
    noise_model : `PixelNoiseModel` or `None`
        The camera noise, or `None` to use the measured scatter alone.

    Returns
    -------
    variance : `float`
        The variance of the sky level per pixel, in the image's units
        squared.
    """
    degrees_of_freedom = sum(count - 1 for _, _, count in bands)
    pixel_variance = (
        sum((count - 1) * noise**2 for _, noise, count in bands) / degrees_of_freedom
        if degrees_of_freedom > 0
        else 0.0
    )
    if noise_model is not None:
        level = max(sum(median for median, _, _ in bands) / len(bands), 0.0)
        pixel_variance = max(
            pixel_variance, noise_model.poisson_coefficient * level + noise_model.read_variance
        )
    return pixel_variance * sum(median_variance_factor(count) for _, _, count in bands) / len(bands) ** 2


def measure_sky(
    cross_section: np.ndarray,
    aperture_center: float,
    aperture_half_width: float,
    noise_model: PixelNoiseModel | None = None,
) -> SkyMeasurement:
    """Measure how bright the night sky is beside the spectrum.

    This is the measuring half of the sky background subtraction stage
    (see the module docstring). It looks at one line of pixels running
    straight across the streak, ignores the reading box and the gap next
    to it, and measures the two sky bands beyond.

    Each band is cleaned first: a pixel far from the band's median (a hot
    pixel, a cosmic ray, the edge of a passing trail) is dropped, and the
    band's typical value is the median of what is left. A median is the
    middle number once the pixels are sorted, so a few bright pixels cannot
    pull it up.

    The two band values are then combined:

    * When both agree to within noise, the sky level is their mean. Each
      median is a noisy estimate of the same sky, so their mean is the best
      estimate. Taking the lower of the two instead is biased low by about
      0.2 of one pixel's noise for 10-pixel bands, because the minimum of
      two noisy numbers sits below their true value. That under-subtracts
      the sky from every reading.
    * When the bands differ by more than `SKY_CONTAMINATION_SIGMA` times the
      noise of their difference, one band holds light that is not sky (the
      trail or glow of a neighbouring star, as in a star cluster). Light
      can only add to a band, so the band with the lower median is used
      alone. The noise used is set by the cleaner (lower-scatter) band,
      which has no neighbour in it.
    * When only one band has enough pixels on the image, that band is used.

    Parameters
    ----------
    cross_section : `numpy.ndarray`
        One line of pixels running across the spectrum: a column of the
        image when the spectrum runs left to right, or a row when it runs top
        to bottom.
    aperture_center : `float`
        Index in `cross_section` of the middle of the reading box. A
        fractional value is rounded to the nearest pixel to place the bands.
    aperture_half_width : `float`
        How many pixels the reading box reaches on each side of its middle.
        A fractional value is rounded up, so the bands never overlap the
        box.
    noise_model : `PixelNoiseModel`, optional
        The camera noise. It sets a floor under the pixel scatter that the
        variance of the sky level uses (see `_sky_level_variance`). Without
        it, the measured scatter alone is used.

    Returns
    -------
    measurement : `SkyMeasurement`
        The sky level per pixel, in ADU, how it was found, and the variance
        of that level.
    """
    centre = round(aperture_center)
    nearest_band_edge = math.ceil(aperture_half_width) + SKY_BAND_GAP_PX
    farthest_band_edge = nearest_band_edge + SKY_BAND_WIDTH_PX
    line_length = cross_section.size

    lower_band = cross_section[max(0, centre - farthest_band_edge) : max(0, centre - nearest_band_edge)]
    upper_band = cross_section[
        min(line_length, centre + nearest_band_edge + 1) : min(line_length, centre + farthest_band_edge + 1)
    ]

    lower = _clipped_band_statistics(lower_band)
    upper = _clipped_band_statistics(upper_band)
    if lower is None and upper is None:
        return SkyMeasurement(0.0, SKY_MODE_NO_SKY)
    if lower is None or upper is None:
        only = upper if lower is None else lower
        return SkyMeasurement(only[0], SKY_MODE_SINGLE_BAND, _sky_level_variance([only], noise_model))

    lower_median, lower_noise, lower_count = lower
    upper_median, upper_noise, upper_count = upper
    # The lower of two noisy scatter estimates reads low on average, which
    # would flag clean sky as contaminated. Pooling both bands reads high when
    # one band holds a neighbour. So the reference noise is the lower band
    # scatter, but not below `SKY_POOLED_NOISE_FLOOR` of the pooled scatter.
    pooled_noise = math.sqrt(0.5 * (lower_noise**2 + upper_noise**2))
    reference_noise = max(min(lower_noise, upper_noise), SKY_POOLED_NOISE_FLOOR * pooled_noise)
    difference_noise = (
        MEDIAN_STANDARD_ERROR_FACTOR * reference_noise * math.sqrt(1.0 / lower_count + 1.0 / upper_count)
    )
    if abs(upper_median - lower_median) > SKY_CONTAMINATION_SIGMA * difference_noise:
        if upper_median > lower_median:
            return SkyMeasurement(
                lower_median, SKY_MODE_UPPER_BAND_CONTAMINATED, _sky_level_variance([lower], noise_model)
            )
        return SkyMeasurement(
            upper_median, SKY_MODE_LOWER_BAND_CONTAMINATED, _sky_level_variance([upper], noise_model)
        )
    return SkyMeasurement(
        0.5 * (lower_median + upper_median),
        SKY_MODE_BOTH_BANDS,
        _sky_level_variance([lower, upper], noise_model),
    )


def measure_sky_level_per_pixel(
    cross_section: np.ndarray, aperture_center: float, aperture_half_width: float
) -> float:
    """Measure how bright the night sky is beside the spectrum.

    This gives only the number from `measure_sky`. Use `measure_sky` when
    the way the level was found matters.

    Parameters
    ----------
    cross_section : `numpy.ndarray`
        One line of pixels running across the spectrum.
    aperture_center : `float`
        Index in `cross_section` of the middle of the reading box.
    aperture_half_width : `float`
        How many pixels the reading box reaches on each side of its middle.

    Returns
    -------
    sky_level_per_pixel : `float`
        The typical sky brightness of a single pixel, in ADU. `0.0` when
        neither band has enough pixels on the image to measure it (subtract
        nothing).
    """
    return measure_sky(cross_section, aperture_center, aperture_half_width).level_per_pixel


def smooth_aperture_sigmas(sigma_px: list[float | None] | np.ndarray) -> np.ndarray:
    """Smooth the per-step trail width so the reading box changes slowly.

    Each step's width fit is noisy. A box sized straight from it would grow
    and shrink from step to step, and the extracted flux would jump with it.
    This takes a running median over `APERTURE_SIGMA_SMOOTHING_STEPS` steps,
    skipping steps whose fit failed. The median follows the slow change of
    the real width along the trail and ignores the fit noise.

    Parameters
    ----------
    sigma_px : `list` [`float` or `None`] or `numpy.ndarray`
        The fitted trail width at each step, in pixels. `None`, `NaN` or a
        value of 0 or less marks a step whose fit failed.

    Returns
    -------
    smoothed_sigma_px : `numpy.ndarray`
        The smoothed width at each step, in pixels. `NaN` where no fit
        exists anywhere in the window.
    """
    values = np.array([np.nan if value is None else value for value in sigma_px], dtype=float)
    values[~(values > 0)] = np.nan
    half_window = APERTURE_SIGMA_SMOOTHING_STEPS // 2
    smoothed = np.full(values.size, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)  # all-NaN windows give NaN, as wanted
        for index in range(values.size):
            window = values[max(0, index - half_window) : index + half_window + 1]
            if np.isfinite(window).any():
                smoothed[index] = np.nanmedian(window)
    return smoothed


def traced_aperture_half_widths_px(
    sigma_px: list[float | None] | np.ndarray, fallback_half_width_px: float
) -> np.ndarray:
    """Give the reading-box half-width at every step of a traced extraction.

    The half-width is `APERTURE_SIGMA_MULTIPLIER` times the smoothed trail
    width (see `smooth_aperture_sigmas`), and at least one pixel. It can
    be a fraction of a pixel: the reading box weights each edge pixel by the
    fraction of it the box covers. Steps whose own fit failed use the
    fallback, which is the fixed box the extractor reads there.

    Parameters
    ----------
    sigma_px : `list` [`float` or `None`] or `numpy.ndarray`
        The fitted trail width at each step, in pixels. `None`, `NaN` or a
        value of 0 or less marks a step whose fit failed.
    fallback_half_width_px : `float`
        The half-width, in pixels, for a step whose fit failed.

    Returns
    -------
    half_widths_px : `numpy.ndarray`
        The half-width at each step, in pixels.
    """
    raw = np.array([np.nan if value is None else value for value in sigma_px], dtype=float)
    smoothed = smooth_aperture_sigmas(raw)
    half_widths = np.maximum(_MINIMUM_APERTURE_HALF_WIDTH_PX, APERTURE_SIGMA_MULTIPLIER * smoothed)
    return np.where(np.isfinite(raw) & (raw > 0) & np.isfinite(smoothed), half_widths, fallback_half_width_px)


def box_pixel_weights(
    line_length: int, aperture_center: float, aperture_half_width: float
) -> tuple[np.ndarray, np.ndarray]:
    """Give the pixels a reading box touches and how much of each it covers.

    A pixel at index `i` spans `i - 0.5` to `i + 0.5`. A box with half-width
    `h` centred on `c` reaches `h` pixels beyond the centre pixel, so its
    edges sit at `c - h - 0.5` and `c + h + 0.5`. Each pixel's weight is the
    fraction of it inside the box, 0 to 1. A whole-number centre and
    half-width give a weight of 1 to every pixel from `c - h` to `c + h`,
    which is the box the extractor always read. A fractional half-width
    gives the edge pixels a weight between 0 and 1, so the reading grows
    smoothly as the box grows.

    Parameters
    ----------
    line_length : `int`
        The number of pixels in the line across the spectrum.
    aperture_center : `float`
        Where the middle of the box is, as a pixel index (may be fractional).
    aperture_half_width : `float`
        How far the box reaches each side of its middle, in pixels.

    Returns
    -------
    indices : `numpy.ndarray`
        The indices of the pixels with a weight above zero, on the image.
    weights : `numpy.ndarray`
        The fraction of each of those pixels the box covers.
    """
    low_edge = aperture_center - aperture_half_width - 0.5
    high_edge = aperture_center + aperture_half_width + 0.5
    first = max(0, math.floor(low_edge - 0.5))
    last = min(line_length, math.ceil(high_edge + 0.5) + 1)
    if first >= last:
        return np.array([], dtype=int), np.array([], dtype=float)
    indices = np.arange(first, last)
    weights = np.clip(np.minimum(indices + 0.5, high_edge) - np.maximum(indices - 0.5, low_edge), 0.0, 1.0)
    covered = weights > 0.0
    return indices[covered], weights[covered]


def _window_weights(line_length: int, centre: float, half_width: float) -> np.ndarray:
    """Give every pixel of a line its share inside a window.

    A pixel at index `i` spans `i - 0.5` to `i + 0.5`. The window spans
    `centre - half_width` to `centre + half_width`.

    Parameters
    ----------
    line_length : `int`
        The number of pixels in the line.
    centre : `float`
        The middle of the window, as a pixel index (may be fractional).
    half_width : `float`
        How far the window reaches each side of its middle, in pixels.

    Returns
    -------
    weights : `numpy.ndarray`
        The fraction of each pixel inside the window, 0 to 1.
    """
    indices = np.arange(line_length)
    low_edge = centre - half_width
    high_edge = centre + half_width
    return np.clip(np.minimum(indices + 0.5, high_edge) - np.maximum(indices - 0.5, low_edge), 0.0, 1.0)


class SpectrumExtractor:
    """Reads the brightness of a spectrum from an image.

    Each brightness reading is the light inside the reading box minus the
    night-sky glow measured beside it (the sky background subtraction
    stage described in the module docstring).

    Attributes
    ----------
    radius : `int`
        How wide of a box to draw around the spectrum (in pixels).
    subtract_sky_background : `bool`
        Whether each reading has the sky glow taken out of it.
    reject_narrow_contaminants : `bool`
        Whether narrow bright spikes in the reading box (other stars'
        trails) are replaced by the smooth level before adding up.
    last_diagnostics : `ExtractionDiagnostics`
        What the latest `extract_*` call did: the box half-width at each
        step and how the sky was found. Replaced at the start of every
        `extract_*` call.
    noise_model : `PixelNoiseModel` or `None`
        The camera noise used to give each reading a variance. `None` skips
        the variance, and `last_diagnostics.sample_variance` stays empty.
    """

    def __init__(
        self,
        radius: int = 10,
        subtract_sky_background: bool = True,
        reject_narrow_contaminants: bool = False,
        noise_model: PixelNoiseModel | None = None,
    ) -> None:
        """Set up the extractor.

        Parameters
        ----------
        radius : `int`, optional
            How wide of a box to use (default is 10 pixels).
        subtract_sky_background : `bool`, optional
            Take the night-sky glow out of every reading (default is
            `True`). Turn this off only to compare against the raw,
            un-subtracted spectrum.
        reject_narrow_contaminants : `bool`, optional
            Replace narrow bright spikes in the box by the smooth level
            (see `replace_narrow_spikes`), default `False`. Meant for a
            nebula's wide box; a star's own narrow box loses its own real
            trace to this instead (see `replace_narrow_spikes` for the
            2026-09-27 regression that confirmed it).
        noise_model : `PixelNoiseModel`, optional
            The camera noise. When given, every reading also gets a
            variance (see `intensity_variance`), kept in
            `last_diagnostics.sample_variance`. The default `None` computes
            no variance.
        """
        self.radius = radius
        self.subtract_sky_background = subtract_sky_background
        self.reject_narrow_contaminants = reject_narrow_contaminants
        self.noise_model = noise_model
        self.last_diagnostics = ExtractionDiagnostics()

    def _measure_aperture(
        self,
        data: np.ndarray,
        line_index: int,
        aperture_center: float,
        aperture_half_width: float,
        is_horizontal: bool,
    ) -> tuple[float, float]:
        """Add up the star's light in one reading box, without the sky.

        Every extraction method in this class reads its brightness through
        this one method (directly or through `_sum_aperture_minus_sky`), so
        the sky background subtraction stage cannot be skipped by accident
        in one of them.

        The box can start and end part-way through a pixel. Each pixel at
        the edge counts for the fraction of it inside the box (see
        `box_pixel_weights`), and the sky is subtracted for the same
        number of pixel-areas. A box that changes size in whole pixels
        would add a step to the spectrum each time; weighting the edge
        pixels makes the reading change smoothly with the box size.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        line_index : `int`
            Which position along the spectrum to read: the column when the
            spectrum runs left to right, or the row when it runs top to
            bottom.
        aperture_center : `float`
            Where the middle of the reading box is, across the spectrum, as
            a pixel index. A fraction places the box between pixels.
        aperture_half_width : `float`
            How many pixels the box reaches on each side of its middle. A
            fraction makes the edge pixels count in part.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The total light in the box minus the sky glow, in ADU, or `NaN`
            when the box is not on the image.
        variance : `float`
            The variance of `flux`, in the image's units squared: the box's
            Poisson and read variance (`PixelNoiseModel.box_variance`) plus
            the sky level's variance times the squared total pixel weight.
            `NaN` when the box is not on the image or the extractor has no
            noise model.
        """
        height, width = data.shape
        if is_horizontal:
            if not 0 <= line_index < width:
                return np.nan, np.nan
            cross_section = data[:, line_index]
        else:
            if not 0 <= line_index < height:
                return np.nan, np.nan
            cross_section = data[line_index, :]

        box_indices, box_weights = box_pixel_weights(cross_section.size, aperture_center, aperture_half_width)
        if box_indices.size == 0:
            return np.nan, np.nan

        box_pixels = cross_section[box_indices]
        if self.reject_narrow_contaminants:
            # Clean with a margin of real pixels beyond each box edge, so a
            # trail at the edge still has neighbours to be compared with.
            margin_start = max(0, int(box_indices[0]) - CONTAMINANT_BASELINE_WIDTH_PX)
            margin_end = min(cross_section.size, int(box_indices[-1]) + 1 + CONTAMINANT_BASELINE_WIDTH_PX)
            cleaned_with_margin = replace_narrow_spikes(cross_section[margin_start:margin_end])
            box_pixels = cleaned_with_margin[box_indices - margin_start]
        box_total = float(np.sum(box_weights * box_pixels))
        # The box's own variance does not depend on the sky estimate: the raw
        # pixels already hold source and sky together.
        variance = (
            self.noise_model.box_variance(box_pixels, box_weights) if self.noise_model is not None else np.nan
        )
        if not self.subtract_sky_background:
            return box_total, variance

        sky = measure_sky(cross_section, aperture_center, aperture_half_width, self.noise_model)
        counts = self.last_diagnostics.sky_mode_counts
        counts[sky.mode] = counts.get(sky.mode, 0) + 1
        total_weight = float(np.sum(box_weights))
        # The sky level is subtracted once for each pixel-area of the box, so
        # its error is multiplied by the total weight, and its variance by the
        # square of it. The band pixels are not in the box, so this term is
        # independent of the box's own.
        variance += total_weight**2 * sky.level_variance
        return box_total - sky.level_per_pixel * total_weight, variance

    def _sum_aperture_minus_sky(
        self,
        data: np.ndarray,
        line_index: int,
        aperture_center: float,
        aperture_half_width: float,
        is_horizontal: bool,
    ) -> float:
        """Add up the star's light in one reading box, without the sky.

        This gives only the flux from `_measure_aperture`; read that method
        for how the box, the edge pixels and the sky are handled.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        line_index : `int`
            Which position along the spectrum to read: the column when the
            spectrum runs left to right, or the row when it runs top to
            bottom.
        aperture_center : `float`
            Where the middle of the reading box is, across the spectrum.
        aperture_half_width : `float`
            How many pixels the box reaches on each side of its middle.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The total light in the box minus the sky glow, in ADU, or `NaN`
            when the box is not on the image.
        """
        return self._measure_aperture(data, line_index, aperture_center, aperture_half_width, is_horizontal)[
            0
        ]

    def _measure_aperture_at_position(
        self,
        data: np.ndarray,
        along_position: float,
        aperture_center: float,
        aperture_half_width: float,
        is_horizontal: bool,
    ) -> tuple[float, float]:
        """Read the box at a position along the spectrum, whole pixel or not.

        A sample is meant to sit at one exact distance from the zero-order
        star, because the wavelength given to it comes from that distance.
        Reading the whole pixel that contains the position (`int()` rounds
        down) puts the light up to a pixel too close to the star: about
        half a pixel on average, so every wavelength came out about 5 A too
        long, by an amount that changed with where the star's centre fell
        within its pixel (0 to 11 A for a star measured to a fraction of a
        pixel). The reading here is the reading of the two whole pixels on
        either side of the position, weighted by how near each is, so it is
        the reading at the position itself.

        The two readings use different pixels (their sky bands too), so they
        are independent and the blend has variance
        ``(1 - u)**2 * V_lower + u**2 * V_upper`` for an upper weight `u`
        (see `combine_neighbour_variances`). The next position along the
        spectrum shares one of the two readings, so neighbouring samples
        have correlated errors. The variance returned here is the sample's
        own and does not hold that covariance.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        along_position : `float`
            The position along the spectrum, in pixels: the column when the
            spectrum runs left to right, or the row when it runs top to
            bottom. A whole number is the middle of that pixel.
        aperture_center : `float`
            Where the middle of the reading box is, across the spectrum.
        aperture_half_width : `float`
            How many pixels the box reaches on each side of its middle.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The light in the box minus the sky glow, in ADU, or `NaN` when
            the position is not on the image.
        variance : `float`
            The variance of `flux` (see `_measure_aperture`), `NaN` when the
            position is off the image or the extractor has no noise model.
        """
        lower_index = math.floor(along_position)
        upper_weight = along_position - lower_index
        lower, lower_variance = self._measure_aperture(
            data, lower_index, aperture_center, aperture_half_width, is_horizontal
        )
        if upper_weight <= 0.0:
            return lower, lower_variance
        upper, upper_variance = self._measure_aperture(
            data, lower_index + 1, aperture_center, aperture_half_width, is_horizontal
        )
        if np.isnan(upper):
            return lower, lower_variance
        if np.isnan(lower):
            return upper, upper_variance
        return (
            (1.0 - upper_weight) * lower + upper_weight * upper,
            combine_neighbour_variances(lower_variance, upper_variance, upper_weight),
        )

    def _sum_aperture_at_position(
        self,
        data: np.ndarray,
        along_position: float,
        aperture_center: float,
        aperture_half_width: float,
        is_horizontal: bool,
    ) -> float:
        """Read the box at a position along the spectrum, flux only.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        along_position : `float`
            The position along the spectrum, in pixels.
        aperture_center : `float`
            Where the middle of the reading box is, across the spectrum.
        aperture_half_width : `float`
            How many pixels the box reaches on each side of its middle.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The light in the box minus the sky glow, in ADU, or `NaN` when
            the position is not on the image.
        """
        return self._measure_aperture_at_position(
            data, along_position, aperture_center, aperture_half_width, is_horizontal
        )[0]

    def _store_sample_variance(self, variances: list[float]) -> None:
        """Keep the per-step variances of the latest extraction.

        Parameters
        ----------
        variances : `list` [`float`]
            The variance of each step, in order. Dropped (the stored list
            stays empty) when the extractor has no noise model.
        """
        if self.noise_model is not None:
            self.last_diagnostics.sample_variance = [float(value) for value in variances]

    def extract_line(
        self, image: AstrometricsImage, start_pos: tuple[float, float], vector: np.ndarray, length: float
    ) -> np.ndarray:
        """Read a straight line across the image.

        This draws a straight box and adds up all the light inside it.
        It assumes the spectrum is perfectly straight.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where the line starts `(x, y)`.
        vector : `np.ndarray`
            Which direction the line goes.
        length : `float`
            How long the line is (in pixels).

        Returns
        -------
        profile : `np.ndarray`
            The total brightness at each step along the line.
        """
        self.last_diagnostics = ExtractionDiagnostics()
        data = image.data
        h, w = data.shape
        x0, y0 = start_pos
        vx, vy = vector

        # Simple extraction for now:
        # If horizontal/vertical, use slice. If diagonal, use profiling
        # (future). Assuming horizontal/vertical for now as per config.

        pixels = []
        variances = []
        for i in range(int(length)):
            exact_x = x0 + i * vx
            exact_y = y0 + i * vy
            curr_x = int(exact_x)
            curr_y = int(exact_y)

            if 0 <= curr_x < w and 0 <= curr_y < h:
                # Sum over radius, minus the sky glow measured beside it, at
                # the exact position along the spectrum (see
                # `_measure_aperture_at_position`).
                if abs(vx) > abs(vy):  # Horizontal-ish
                    val, variance = self._measure_aperture_at_position(
                        data, exact_x, curr_y, self.radius, True
                    )
                else:  # Vertical-ish
                    val, variance = self._measure_aperture_at_position(
                        data, exact_y, curr_x, self.radius, False
                    )
                pixels.append(val)
                variances.append(variance)
            else:
                pixels.append(np.nan)
                variances.append(np.nan)

        self._store_sample_variance(variances)
        return np.array(pixels)

    def extract_line_traced(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        vector: np.ndarray,
        length: float,
        centerline_polynomial_degree: int = 2,
    ) -> tuple[np.ndarray, list[float], list[float]]:
        """Advanced version of extract_line that follows curves.

        Instead of assuming the spectrum is perfectly straight, this checks the
        true center at every step along the line. It draws a smooth curve
        through
        those centers, and widens or narrows its reading box with the
        spectrum's width, smoothed over many steps (see
        `traced_aperture_half_widths_px`). If it loses the trail for a
        moment, it reads a fixed-size box there until it finds it again.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where to start looking.
        vector : `np.ndarray`
            Which direction to go.
        length : `float`
            How long the spectrum is.
        centerline_polynomial_degree : `int`, optional
            How flexible the curve should be (default 2 means a simple curve).

        Returns
        -------
        profile : `np.ndarray`
            The brightness at each step.
        trail_centerline_px : `List[float]`
            How far the actual center was from the straight line we guessed.
        trail_width_px : `List[float]`
            How wide the spectrum was at each step.
        """
        self.last_diagnostics = ExtractionDiagnostics()
        data = image.data
        height, width = data.shape
        x0, y0 = start_pos
        vx, vy = vector
        perpendicular_vector = (-vy, vx)

        raw_centers: list[float | None] = []
        raw_sigmas: list[float | None] = []
        for i in range(int(length)):
            curr_x = x0 + i * vx
            curr_y = y0 + i * vy
            fit_result = fit_cross_section_gaussian(data, (curr_x, curr_y), perpendicular_vector, self.radius)
            if fit_result is None:
                raw_centers.append(None)
                raw_sigmas.append(None)
            else:
                center_offset, sigma = fit_result
                raw_centers.append(center_offset)
                raw_sigmas.append(sigma)

        smoothed_centerline = fit_trail_centerline_polynomial(raw_centers, centerline_polynomial_degree)
        fitted_sigmas = [sigma for sigma in raw_sigmas if sigma is not None]
        fallback_sigma = float(np.median(fitted_sigmas)) if fitted_sigmas else float(self.radius) / 3.0

        aperture_half_widths = traced_aperture_half_widths_px(raw_sigmas, float(self.radius))
        pixels = []
        variances = []
        trail_width_px: list[float] = []
        for i in range(int(length)):
            curr_x = x0 + i * vx
            curr_y = y0 + i * vy
            int_x, int_y = int(curr_x), int(curr_y)

            if raw_centers[i] is None:
                # If we couldn't find the exact center here, just draw a
                # standard fixed-size box exactly where we expected the
                # line to be.
                if 0 <= int_x < width and 0 <= int_y < height:
                    if abs(vx) > abs(vy):
                        val, variance = self._measure_aperture_at_position(
                            data, curr_x, int_y, self.radius, True
                        )
                    else:
                        val, variance = self._measure_aperture_at_position(
                            data, curr_y, int_x, self.radius, False
                        )
                    pixels.append(val)
                    variances.append(variance)
                else:
                    pixels.append(np.nan)
                    variances.append(np.nan)
                trail_width_px.append(0.0)
                self.last_diagnostics.aperture_half_width_px.append(float(self.radius))
                continue

            sigma = raw_sigmas[i] if raw_sigmas[i] is not None else fallback_sigma
            aperture_radius = float(aperture_half_widths[i])
            true_center_x = curr_x + perpendicular_vector[0] * smoothed_centerline[i]
            true_center_y = curr_y + perpendicular_vector[1] * smoothed_centerline[i]

            if abs(vx) > abs(vy):
                val, variance = self._measure_aperture_at_position(
                    data, curr_x, true_center_y, aperture_radius, True
                )
            else:
                val, variance = self._measure_aperture_at_position(
                    data, curr_y, true_center_x, aperture_radius, False
                )
            pixels.append(val)
            variances.append(variance)
            trail_width_px.append(sigma)
            self.last_diagnostics.aperture_half_width_px.append(aperture_radius)

        self._store_sample_variance(variances)
        return np.array(pixels), smoothed_centerline, trail_width_px

    def extract_with_flare_mask(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        flare_offset_pixels: float,
        max_offset_pixels: float,
        radius: int,
        orientation: str = "vertical",
        angle_degrees: float = 0.0,
    ) -> tuple[np.ndarray, float, float]:
        """Measure a spectrum while ignoring the bright star flare.

        First, this finds the exact center of the star using a 21x21 pixel box.
        Then, it starts measuring the spectrum a few pixels away to avoid the
        bright flare from the star itself. It tracks any tilt in the image and
        adds up the light inside a tight box.

        Parameters
        ----------
        image : `AstrometricsImage`
            The 2D image data.
        start_pos : `Tuple[float, float]`
            Rough coordinates of the zero-order star (x, y).
        flare_offset_pixels : `float`
            Starting pixel offset relative to the anchor to avoid the
            astigmatism flare.
        max_offset_pixels : `float`
            Ending pixel offset relative to the anchor to define the
            bounding box.
        radius : `int`
            Half-width of the extraction window.
        orientation : `str`, optional
            Orientation of the dispersion, "horizontal" or "vertical"
            (default "vertical").
        angle_degrees : `float`, optional
            Rotation/tilt angle of the dispersion streak in degrees
            (default 0.0).

        Returns
        -------
        profile : `np.ndarray`
            1D array of summed intensities.
        anchor_x : `float`
            Sub-pixel zero-order anchor X coordinate.
        anchor_y : `float`
            Sub-pixel zero-order anchor Y coordinate.

        """
        self.last_diagnostics = ExtractionDiagnostics()
        data = image.data

        # 1. Centroid Anchor (21x21 subgrid around rough start position)
        trail_side = 1 if flare_offset_pixels + max_offset_pixels >= 0 else -1
        anchor_x, anchor_y = self._compute_centroid_reference_point(
            data, start_pos, orientation, angle_degrees, trail_side
        )

        # 2. Bounding Box & Profile Extraction with Dynamic Tilt Tracking
        profile = []
        variances = []
        slope = -np.tan(np.radians(angle_degrees))

        if orientation == "horizontal":
            start_x = round(anchor_x + flare_offset_pixels)
            end_x = round(anchor_x + max_offset_pixels)

            for x in range(start_x, end_x):
                # Calculate dynamically tilted y center
                y_center = anchor_y + slope * (x - anchor_x)
                iy_center = round(y_center)
                value, variance = self._measure_aperture(data, x, iy_center, radius, True)
                profile.append(value)
                variances.append(variance)
        else:  # vertical
            # Vertical dispersion: from anchor_y + flare_offset_pixels to
            # anchor_y + max_offset_pixels
            start_y = round(anchor_y + flare_offset_pixels)
            end_y = round(anchor_y + max_offset_pixels)

            for y in range(start_y, end_y):
                # Calculate dynamically tilted x center
                x_center = anchor_x + slope * (y - anchor_y)
                ix_center = round(x_center)
                # Sum rows horizontally in the bounding box centered
                # around the tilted x center
                value, variance = self._measure_aperture(data, y, ix_center, radius, False)
                profile.append(value)
                variances.append(variance)

        self._store_sample_variance(variances)
        return np.array(profile), anchor_x, anchor_y

    def _compute_centroid_reference_point(
        self,
        data: np.ndarray,
        start_pos: tuple[float, float],
        orientation: str | None = None,
        angle_degrees: float = 0.0,
        trail_side: int = 1,
    ) -> tuple[float, float]:
        """Find the sub-pixel centroid of a star in a 21x21 pixel box.

        The centroid is the brightness-weighted average position. The
        spectrum trail that leaves the star puts light on one side of it,
        and that light drags a plain centroid toward the trail: about 0.06
        pixel for a trail of 3000 ADU per column beside a 2 million ADU
        star. When the caller gives the dispersion orientation, this method
        removes the trail first (see `_subtract_trail_from_box`).

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        start_pos : `tuple` [`float`, `float`]
            Rough star position `(x, y)`.
        orientation : `str`, optional
            `"horizontal"` or `"vertical"`, the direction the trail runs.
            `None` skips the trail removal and gives the plain centroid.
        angle_degrees : `float`, optional
            The tilt of the trail, in the same convention as
            `extract_with_flare_mask` (the trail centre moves by
            `-tan(angle)` across for each step along).
        trail_side : `int`, optional
            `1` when the trail leaves toward larger pixel indices along the
            dispersion axis, `-1` when it leaves toward smaller ones.

        Returns
        -------
        anchor_x, anchor_y : `float`
            The sub-pixel zero-order anchor coordinates.
        """
        h, w = data.shape
        x0, y0 = start_pos
        ix0, iy0 = round(x0), round(y0)
        y_start = max(0, iy0 - 10)
        y_end = min(h, iy0 + 11)
        x_start = max(0, ix0 - 10)
        x_end = min(w, ix0 + 11)

        subgrid = data[y_start:y_end, x_start:x_end]
        total_mass = np.sum(subgrid)
        if total_mass <= 0:
            return x0, y0

        y_indices, x_indices = np.indices(subgrid.shape)
        anchor_x = np.sum(subgrid * x_indices) / total_mass
        anchor_y = np.sum(subgrid * y_indices) / total_mass

        if orientation is not None:
            # Clean the box around the latest centre, then measure again.
            # The cleaning needs the centre and the centre needs the
            # cleaning, so a few rounds settle both.
            is_vertical = orientation == "vertical"
            slope = -np.tan(np.radians(angle_degrees))
            for _ in range(4):
                if is_vertical:
                    cleaned = self._subtract_trail_from_box(subgrid.T, slope, trail_side, anchor_y).T
                else:
                    cleaned = self._subtract_trail_from_box(subgrid, slope, trail_side, anchor_x)
                if np.sum(cleaned) <= 0:
                    break
                anchor_x, anchor_y = self._recentre_centroid_window(cleaned, anchor_x, anchor_y)
        return x_start + anchor_x, y_start + anchor_y

    @staticmethod
    def _recentre_centroid_window(
        box: np.ndarray, centre_x: float, centre_y: float, half_width: float = 8.0, iterations: int = 100
    ) -> tuple[float, float]:
        """Refine a centroid with a window that stays centred on the star.

        The box around the star is centred on a whole pixel, so a star
        between pixels has more of one side of its faint wings inside the
        box than the other. That pulls the centroid by up to 0.03 pixel.
        This method averages inside a smaller window instead, and moves the
        window to the latest centroid each time. A pixel at the window edge
        counts for the fraction of it inside the window (see
        `box_pixel_weights`).

        Parameters
        ----------
        box : `numpy.ndarray`
            The pixels around the star, with the trail and background
            already removed.
        centre_x, centre_y : `float`
            The first centroid, in the box's own pixel indices.
        half_width : `float`, optional
            How far the window reaches each side of its middle, in pixels.
            It must be smaller than half the box, so the window stays
            inside the box.
        iterations : `int`, optional
            The most times to recentre the window. The loop stops early
            once the centre moves by less than 0.0001 pixel. Each step
            closes only a small part of the gap, so it needs many steps.

        Returns
        -------
        centre_x, centre_y : `float`
            The refined centroid, in the box's own pixel indices.
        """
        n_rows, n_cols = box.shape
        for _ in range(iterations):
            weights_x = _window_weights(n_cols, centre_x, half_width)
            weights_y = _window_weights(n_rows, centre_y, half_width)
            weighted = box * np.outer(weights_y, weights_x)
            mass = np.sum(weighted)
            if mass <= 0:
                break
            new_x = float(np.sum(weighted.sum(axis=0) * np.arange(n_cols)) / mass)
            new_y = float(np.sum(weighted.sum(axis=1) * np.arange(n_rows)) / mass)
            moved = max(abs(new_x - centre_x), abs(new_y - centre_y))
            centre_x, centre_y = new_x, new_y
            if moved < 1e-4:
                break
        return centre_x, centre_y

    @staticmethod
    def _subtract_trail_from_box(box: np.ndarray, slope: float, trail_side: int, centre: float) -> np.ndarray:
        """Remove the trail and the background from a box around a star.

        The method reads two cross-sections (one value per row, from the
        average of three nearby lines) at equal distances either side of the
        star's centre, near the box edge. The one away from the trail shows
        the sky and a faint wing of the star. The one on the trail side
        shows those two plus the trail. Equal distances from the centre
        give the star's wing the same size at both, so their difference is
        the trail's cross-section alone.

        Every line loses the away-side cross-section, which is the same
        background for each line. That leaves the star balanced around its
        centre. Every line on the trail side of the centre also loses the
        trail's cross-section, slid along the tilted trail to that line. The
        centroid then has no pull toward the trail.

        Parameters
        ----------
        box : `numpy.ndarray`
            The pixels around the star, with the dispersion axis along the
            columns.
        slope : `float`
            How many rows the trail centre moves for each column along.
        trail_side : `int`
            `1` when the trail leaves toward larger column numbers, `-1`
            when toward smaller ones.
        centre : `float`
            The star's centre along the dispersion axis, in the box's own
            column numbers.

        Returns
        -------
        cleaned : `numpy.ndarray`
            The box with the trail and background subtracted.
        """
        n_rows, n_cols = box.shape
        if n_cols < 8:
            return box
        rows = np.arange(n_rows, dtype=float)
        distances = (8.0, 8.5, 9.0)
        reference = float(np.clip(centre + trail_side * 8.5, 0.0, n_cols - 1.0))

        def read_line(position: float) -> np.ndarray:
            """Read one cross-section at a fractional column position.

            Parameters
            ----------
            position : `float`
                The column position, clamped to the box.

            Returns
            -------
            section : `numpy.ndarray`
                The interpolated value of each row.
            """
            left = min(int(np.floor(position)), n_cols - 2)
            share = position - left
            return (1.0 - share) * box[:, left] + share * box[:, left + 1]

        trail_section = np.zeros(n_rows)
        background = np.zeros(n_rows)
        for distance in distances:
            trail_position = float(np.clip(centre + trail_side * distance, 0.0, n_cols - 1.0))
            background_position = float(np.clip(centre - trail_side * distance, 0.0, n_cols - 1.0))
            # Slide the trail's line onto the reference column along the trail.
            trail_section += np.interp(
                rows + slope * (trail_position - reference),
                rows,
                read_line(trail_position),
                left=0.0,
                right=0.0,
            )
            background += read_line(background_position)
        background /= len(distances)
        trail_section = trail_section / len(distances) - background

        cleaned = box.astype(float) - background[:, np.newaxis]
        for column in np.arange(n_cols)[(np.arange(n_cols) - centre) * trail_side > 0]:
            cleaned[:, column] -= np.interp(
                rows - slope * (column - reference), rows, trail_section, left=0.0, right=0.0
            )
        return cleaned

    def extract_with_flare_mask_traced(
        self,
        image: AstrometricsImage,
        start_pos: tuple[float, float],
        flare_offset_pixels: float,
        max_offset_pixels: float,
        radius: int,
        orientation: str = "vertical",
        angle_degrees: float = 0.0,
        centerline_polynomial_degree: int = 2,
    ) -> tuple[np.ndarray, float, float, list[float], list[float]]:
        """Smart version of extract_with_flare_mask that follows curves.

        Like extract_with_flare_mask, this avoids the bright star flare.
        Like extract_line_traced, it also tracks the exact center of the
        spectrum as it bends and changes width.

        Parameters
        ----------
        image : `AstrometricsImage`
            The picture to read from.
        start_pos : `Tuple[float, float]`
            Where we think the star is `(x, y)`.
        flare_offset_pixels : `float`
            How far away to start reading, so we don't blind ourselves.
        max_offset_pixels : `float`
            Where to stop reading.
        radius : `int`
            How wide to look for the center.
        orientation : `str`, optional
            "horizontal" or "vertical".
        angle_degrees : `float`, optional
            Any overall tilt to the picture.
        centerline_polynomial_degree : `int`, optional
            How flexible the curve tracking should be.

        Returns
        -------
        profile : `np.ndarray`
            The brightness at each step.
        anchor_x : `float`
            The exact `x` center of the star.
        anchor_y : `float`
            The exact `y` center of the star.
        trail_centerline_px : `List[float]`
            How far the spectrum drifted from straight.
        trail_width_px : `List[float]`
            How fat the spectrum was at each step.
        """
        self.last_diagnostics = ExtractionDiagnostics()
        data = image.data
        trail_side = 1 if flare_offset_pixels + max_offset_pixels >= 0 else -1
        anchor_x, anchor_y = self._compute_centroid_reference_point(
            data, start_pos, orientation, angle_degrees, trail_side
        )

        steps, nominal_centers, perpendicular_vector = self._nominal_trace_centers(
            anchor_x, anchor_y, flare_offset_pixels, max_offset_pixels, orientation, angle_degrees
        )

        raw_centers, raw_sigmas, smoothed_centerline, fallback_sigma = self._fit_trace_centerline(
            data, nominal_centers, perpendicular_vector, radius, centerline_polynomial_degree
        )

        profile, trail_width_px = self._build_traced_flare_profile(
            data,
            steps,
            nominal_centers,
            perpendicular_vector,
            raw_centers,
            raw_sigmas,
            smoothed_centerline,
            fallback_sigma,
            orientation,
            radius,
        )

        return profile, anchor_x, anchor_y, smoothed_centerline, trail_width_px

    def _nominal_trace_centers(
        self,
        anchor_x: float,
        anchor_y: float,
        flare_offset_pixels: float,
        max_offset_pixels: float,
        orientation: str,
        angle_degrees: float,
    ) -> tuple[list[int], list[tuple[float, float]], tuple[float, float]]:
        """Compute the straight-line trace steps and their nominal centers.

        Returns
        -------
        steps : `list` [`int`]
            The pixel coordinate along the dispersion axis for each
            step.
        nominal_centers : `list` [`tuple`]
            The `(x, y)` position the trace would be at, ignoring any
            curvature, at each step.
        perpendicular_vector : `tuple` [`float`, `float`]
            The unit vector pointing sideways across the dispersion axis.
        """
        slope = -np.tan(np.radians(angle_degrees))
        is_horizontal = orientation == "horizontal"

        if is_horizontal:
            steps = list(range(round(anchor_x + flare_offset_pixels), round(anchor_x + max_offset_pixels)))
            perpendicular_vector = (0.0, 1.0)
        else:
            steps = list(range(round(anchor_y + flare_offset_pixels), round(anchor_y + max_offset_pixels)))
            perpendicular_vector = (1.0, 0.0)

        nominal_centers = []
        for step in steps:
            if is_horizontal:
                nominal_centers.append((float(step), anchor_y + slope * (step - anchor_x)))
            else:
                nominal_centers.append((anchor_x + slope * (step - anchor_y), float(step)))

        return steps, nominal_centers, perpendicular_vector

    def _fit_trace_centerline(
        self,
        data: np.ndarray,
        nominal_centers: list[tuple[float, float]],
        perpendicular_vector: tuple[float, float],
        radius: float,
        centerline_polynomial_degree: int,
    ) -> tuple[list[float | None], list[float | None], list[float], float]:
        """Fit the true center and width at every nominal trace position.

        Returns
        -------
        raw_centers, raw_sigmas : `list` [`float` or `None`]
            The per-step fit results; `None` where the fit failed.
        smoothed_centerline : `list` [`float`]
            `raw_centers` with gaps filled by a polynomial fit.
        fallback_sigma : `float`
            The width to use where a step's own fit failed: the
            median of the steps that did fit, or a fraction of
            `radius` if none did.
        """
        raw_centers: list[float | None] = []
        raw_sigmas: list[float | None] = []
        for center in nominal_centers:
            fit_result = fit_cross_section_gaussian(data, center, perpendicular_vector, radius)
            if fit_result is None:
                raw_centers.append(None)
                raw_sigmas.append(None)
            else:
                center_offset, sigma = fit_result
                raw_centers.append(center_offset)
                raw_sigmas.append(sigma)

        smoothed_centerline = fit_trail_centerline_polynomial(raw_centers, centerline_polynomial_degree)
        fitted_sigmas = [sigma for sigma in raw_sigmas if sigma is not None]
        fallback_sigma = float(np.median(fitted_sigmas)) if fitted_sigmas else float(radius) / 3.0

        return raw_centers, raw_sigmas, smoothed_centerline, fallback_sigma

    def _build_traced_flare_profile(
        self,
        data: np.ndarray,
        steps: list[int],
        nominal_centers: list[tuple[float, float]],
        perpendicular_vector: tuple[float, float],
        raw_centers: list[float | None],
        raw_sigmas: list[float | None],
        smoothed_centerline: list[float],
        fallback_sigma: float,
        orientation: str,
        radius: float,
    ) -> tuple[np.ndarray, list[float]]:
        """Sum intensities along a traced trail, widening the box by sigma.

        Returns
        -------
        profile : `numpy.ndarray`
            The summed intensity at each step.
        trail_width_px : `list` [`float`]
            The fitted trail sigma at each step, in pixels (0.0 where the
            fit failed). The box half-width is not this step's value; it
            comes from the widths smoothed over many steps (see
            `traced_aperture_half_widths_px`) and is recorded in
            `last_diagnostics`.
        """
        is_horizontal = orientation == "horizontal"

        aperture_half_widths = traced_aperture_half_widths_px(raw_sigmas, float(radius))
        profile = []
        variances = []
        trail_width_px: list[float] = []
        for index, step in enumerate(steps):
            nominal_x, nominal_y = nominal_centers[index]
            int_step_x, int_step_y = (step, round(nominal_y)) if is_horizontal else (round(nominal_x), step)

            if raw_centers[index] is None:
                # No fit here, so read a plain fixed-size box (still with
                # the sky taken out).
                if is_horizontal:
                    val, variance = self._measure_aperture(data, step, int_step_y, radius, True)
                else:
                    val, variance = self._measure_aperture(data, step, int_step_x, radius, False)
                profile.append(val)
                variances.append(variance)
                trail_width_px.append(0.0)
                self.last_diagnostics.aperture_half_width_px.append(float(radius))
                continue

            sigma = raw_sigmas[index] if raw_sigmas[index] is not None else fallback_sigma
            aperture_radius = float(aperture_half_widths[index])
            true_x = nominal_x + perpendicular_vector[0] * smoothed_centerline[index]
            true_y = nominal_y + perpendicular_vector[1] * smoothed_centerline[index]

            if is_horizontal:
                val, variance = self._measure_aperture(data, step, true_y, aperture_radius, True)
            else:
                val, variance = self._measure_aperture(data, step, true_x, aperture_radius, False)
            profile.append(val)
            variances.append(variance)
            trail_width_px.append(sigma)
            self.last_diagnostics.aperture_half_width_px.append(aperture_radius)

        self._store_sample_variance(variances)
        return np.array(profile), trail_width_px
