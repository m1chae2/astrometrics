"""Tools to read the brightness of a spectral streak in an image.

This file provides two ways to measure a spectrum:
1. Basic: Draws a straight rectangular box over the spectrum and adds up
   all the light inside it.
2. Advanced (Traced): Carefully follows the exact center of the spectrum as
   it bends or widens. It adjusts the size of the box on the fly to get
   the best possible reading while ignoring background noise.

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
of the streak, and subtracts that sky level from the box total. This stage
runs BEFORE wavelength calibration, so the calibrator and every later stage
(feature detection, classification) only ever see star light.

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

import numpy as np
from astropy.modeling import fitting, models
from scipy.ndimage import binary_dilation, median_filter

from astrometricslib.drivers.image import AstrometricsImage

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
    from astrometricslib.pipelines.spectroscopy._extractor_c import (
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
    ValueError
        If `data` is not a 2-D array.
    """
    if data.ndim != 2:
        raise ValueError(f"Cross-section fitting needs a 2-D image, got {data.ndim} dimensions.")

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
        except Exception as exc:
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
    except Exception:
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
    Angstroms, was 0.32 in units where the nebula's hump is 0.1).

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


def measure_sky_level_per_pixel(
    cross_section: np.ndarray, aperture_center: int, aperture_half_width: int
) -> float:
    """Measure how bright the night sky is beside the spectrum.

    This is the measuring half of the sky background subtraction stage
    (see the module docstring). It looks at one line of pixels running
    straight across the streak, ignores the reading box and the gap next
    to it, and measures the two sky bands beyond. Each band gets its own
    typical value, and the LOWER of the two is used.

    Why the lower one: anything that is not sky (the trail of a
    neighbouring star, or its glow) can only add light to a band. In a
    crowded field such as a star cluster, a neighbour's spectrum often runs
    alongside ours and fills one band. Pooling both bands would then give a
    sky level far too high, and subtracting it would remove the star's own
    light. The lower band is the one less likely to have a neighbour in it.
    When the sky is smooth and both bands agree, this costs almost nothing.

    Each band's typical value is its median, not its average. The median is
    the middle number once the pixels are sorted, so a few unusually bright
    pixels (a hot pixel or a cosmic ray) cannot pull it up.

    Parameters
    ----------
    cross_section : `numpy.ndarray`
        One line of pixels running across the spectrum: a column of the
        image when the spectrum runs left to right, or a row when it runs
        top to bottom.
    aperture_center : `int`
        Index in `cross_section` of the middle of the reading box.
    aperture_half_width : `int`
        How many pixels the reading box reaches on each side of its
        middle.

    Returns
    -------
    sky_level_per_pixel : `float`
        The typical sky brightness of a single pixel. `0.0` when neither
        band has enough pixels on the image to measure it (subtract
        nothing).
    """
    nearest_band_edge = aperture_half_width + SKY_BAND_GAP_PX
    farthest_band_edge = nearest_band_edge + SKY_BAND_WIDTH_PX
    line_length = cross_section.size

    lower_band = cross_section[
        max(0, aperture_center - farthest_band_edge) : max(0, aperture_center - nearest_band_edge)
    ]
    upper_band = cross_section[
        min(line_length, aperture_center + nearest_band_edge + 1) : min(
            line_length, aperture_center + farthest_band_edge + 1
        )
    ]

    band_levels = []
    for band in (lower_band, upper_band):
        sky_pixels = band.astype(float)
        sky_pixels = sky_pixels[np.isfinite(sky_pixels)]
        if sky_pixels.size >= SKY_BAND_MINIMUM_SAMPLE_COUNT:
            band_levels.append(float(np.median(sky_pixels)))
    if not band_levels:
        return 0.0
    return min(band_levels)


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
    """

    def __init__(  # ruff: ignore[missing-return-type-special-method]
        self,
        radius: int = 10,
        subtract_sky_background: bool = True,
        reject_narrow_contaminants: bool = False,
    ):
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
            nebula's wide box; a star's own narrow box would lose its
            light to this.
        """
        self.radius = radius
        self.subtract_sky_background = subtract_sky_background
        self.reject_narrow_contaminants = reject_narrow_contaminants

    def _sum_aperture_minus_sky(
        self,
        data: np.ndarray,
        line_index: int,
        aperture_center: int,
        aperture_half_width: int,
        is_horizontal: bool,
    ) -> float:
        """Add up the star's light in one reading box, without the sky.

        Every extraction method in this class reads its brightness through
        this one method, so the sky background subtraction stage cannot be
        skipped by accident in one of them.

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        line_index : `int`
            Which position along the spectrum to read: the column when the
            spectrum runs left to right, or the row when it runs top to
            bottom.
        aperture_center : `int`
            Where the middle of the reading box is, across the spectrum.
        aperture_half_width : `int`
            How many pixels the box reaches on each side of its middle.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The total light in the box minus the sky glow, or `NaN` when
            the box is not on the image.
        """
        height, width = data.shape
        if is_horizontal:
            if not 0 <= line_index < width:
                return np.nan
            cross_section = data[:, line_index]
        else:
            if not 0 <= line_index < height:
                return np.nan
            cross_section = data[line_index, :]

        box_start = max(0, aperture_center - aperture_half_width)
        box_end = min(cross_section.size, aperture_center + aperture_half_width + 1)
        if box_start >= box_end:
            return np.nan

        box_pixels = cross_section[box_start:box_end]
        if self.reject_narrow_contaminants:
            # Clean with a margin of real pixels beyond each box edge, so a
            # trail at the edge still has neighbours to be compared with.
            margin_start = max(0, box_start - CONTAMINANT_BASELINE_WIDTH_PX)
            margin_end = min(cross_section.size, box_end + CONTAMINANT_BASELINE_WIDTH_PX)
            cleaned_with_margin = replace_narrow_spikes(cross_section[margin_start:margin_end])
            box_pixels = cleaned_with_margin[box_start - margin_start : box_end - margin_start]
        box_total = float(np.sum(box_pixels))
        if not self.subtract_sky_background:
            return box_total

        sky_level_per_pixel = measure_sky_level_per_pixel(cross_section, aperture_center, aperture_half_width)
        return box_total - sky_level_per_pixel * (box_end - box_start)

    def _sum_aperture_at_position(
        self,
        data: np.ndarray,
        along_position: float,
        aperture_center: int,
        aperture_half_width: int,
        is_horizontal: bool,
    ) -> float:
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

        Parameters
        ----------
        data : `numpy.ndarray`
            The 2-D image.
        along_position : `float`
            The position along the spectrum, in pixels: the column when the
            spectrum runs left to right, or the row when it runs top to
            bottom. A whole number is the middle of that pixel.
        aperture_center : `int`
            Where the middle of the reading box is, across the spectrum.
        aperture_half_width : `int`
            How many pixels the box reaches on each side of its middle.
        is_horizontal : `bool`
            `True` when the spectrum runs left to right.

        Returns
        -------
        flux : `float`
            The light in the box minus the sky glow, or `NaN` when the
            position is not on the image.
        """
        lower_index = math.floor(along_position)
        upper_weight = along_position - lower_index
        lower = self._sum_aperture_minus_sky(
            data, lower_index, aperture_center, aperture_half_width, is_horizontal
        )
        if upper_weight <= 0.0:
            return lower
        upper = self._sum_aperture_minus_sky(
            data, lower_index + 1, aperture_center, aperture_half_width, is_horizontal
        )
        if np.isnan(upper):
            return lower
        if np.isnan(lower):
            return upper
        return (1.0 - upper_weight) * lower + upper_weight * upper

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
        data = image.data
        h, w = data.shape
        x0, y0 = start_pos
        vx, vy = vector

        # Simple extraction for now:
        # If horizontal/vertical, use slice. If diagonal, use profiling
        # (future). Assuming horizontal/vertical for now as per config.

        pixels = []
        for i in range(int(length)):
            exact_x = x0 + i * vx
            exact_y = y0 + i * vy
            curr_x = int(exact_x)
            curr_y = int(exact_y)

            if 0 <= curr_x < w and 0 <= curr_y < h:
                # Sum over radius, minus the sky glow measured beside it, at
                # the exact position along the spectrum (see
                # `_sum_aperture_at_position`).
                if abs(vx) > abs(vy):  # Horizontal-ish
                    val = self._sum_aperture_at_position(data, exact_x, curr_y, self.radius, True)
                else:  # Vertical-ish
                    val = self._sum_aperture_at_position(data, exact_y, curr_x, self.radius, False)
                pixels.append(val)
            else:
                pixels.append(np.nan)

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
        those centers, and widens or narrows its reading box depending on how
        fat the spectrum is at that spot. If it loses the trail for a moment,
        it just guesses using a straight line until it finds it again.

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

        pixels = []
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
                        val = self._sum_aperture_at_position(data, curr_x, int_y, self.radius, True)
                    else:
                        val = self._sum_aperture_at_position(data, curr_y, int_x, self.radius, False)
                    pixels.append(val)
                else:
                    pixels.append(np.nan)
                trail_width_px.append(0.0)
                continue

            sigma = raw_sigmas[i] if raw_sigmas[i] is not None else fallback_sigma
            aperture_radius = max(1, round(sigma * APERTURE_SIGMA_MULTIPLIER))
            true_center_x = curr_x + perpendicular_vector[0] * smoothed_centerline[i]
            true_center_y = curr_y + perpendicular_vector[1] * smoothed_centerline[i]
            center_int_x, center_int_y = round(true_center_x), round(true_center_y)

            if abs(vx) > abs(vy):
                val = self._sum_aperture_at_position(data, curr_x, center_int_y, aperture_radius, True)
            else:
                val = self._sum_aperture_at_position(data, curr_y, center_int_x, aperture_radius, False)
            pixels.append(val)
            trail_width_px.append(sigma)

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
        data = image.data

        # 1. Centroid Anchor (21x21 subgrid around rough start position)
        anchor_x, anchor_y = self._compute_centroid_reference_point(data, start_pos)

        # 2. Bounding Box & Profile Extraction with Dynamic Tilt Tracking
        profile = []
        slope = -np.tan(np.radians(angle_degrees))

        if orientation == "horizontal":
            start_x = round(anchor_x + flare_offset_pixels)
            end_x = round(anchor_x + max_offset_pixels)

            for x in range(start_x, end_x):
                # Calculate dynamically tilted y center
                y_center = anchor_y + slope * (x - anchor_x)
                iy_center = round(y_center)
                profile.append(self._sum_aperture_minus_sky(data, x, iy_center, radius, True))
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
                profile.append(self._sum_aperture_minus_sky(data, y, ix_center, radius, False))

        return np.array(profile), anchor_x, anchor_y

    def _compute_centroid_reference_point(
        self, data: np.ndarray, start_pos: tuple[float, float]
    ) -> tuple[float, float]:
        """Find the sub-pixel centroid of a star in a 21x21 pixel box.

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

        if total_mass > 0:
            y_indices, x_indices = np.indices(subgrid.shape)
            anchor_x = x_start + np.sum(subgrid * x_indices) / total_mass
            anchor_y = y_start + np.sum(subgrid * y_indices) / total_mass
        else:
            anchor_x, anchor_y = x0, y0

        return anchor_x, anchor_y

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
        data = image.data
        anchor_x, anchor_y = self._compute_centroid_reference_point(data, start_pos)

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
            The aperture sigma used at each step (0.0 where the fit
            failed).
        """
        is_horizontal = orientation == "horizontal"

        profile = []
        trail_width_px: list[float] = []
        for index, step in enumerate(steps):
            nominal_x, nominal_y = nominal_centers[index]
            int_step_x, int_step_y = (step, round(nominal_y)) if is_horizontal else (round(nominal_x), step)

            if raw_centers[index] is None:
                # No fit here, so read a plain fixed-size box (still with
                # the sky taken out).
                if is_horizontal:
                    val = self._sum_aperture_minus_sky(data, step, int_step_y, radius, True)
                else:
                    val = self._sum_aperture_minus_sky(data, step, int_step_x, radius, False)
                profile.append(val)
                trail_width_px.append(0.0)
                continue

            sigma = raw_sigmas[index] if raw_sigmas[index] is not None else fallback_sigma
            aperture_radius = max(1, round(sigma * APERTURE_SIGMA_MULTIPLIER))
            true_x = nominal_x + perpendicular_vector[0] * smoothed_centerline[index]
            true_y = nominal_y + perpendicular_vector[1] * smoothed_centerline[index]

            if is_horizontal:
                val = self._sum_aperture_minus_sky(data, step, round(true_y), aperture_radius, True)
            else:
                val = self._sum_aperture_minus_sky(data, step, round(true_x), aperture_radius, False)
            profile.append(val)
            trail_width_px.append(sigma)

        return np.array(profile), trail_width_px
