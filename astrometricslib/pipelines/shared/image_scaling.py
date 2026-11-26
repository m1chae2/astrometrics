"""Tools for adjusting image brightness and contrast for display.

This file only handles the math to convert raw telescope data into
a format that can be drawn on a screen. Saving the actual image
files happens somewhere else.
"""

import logging

import numpy as np

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.target import StretchParameters
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA, robust_sigma

logger = logging.getLogger(__name__)

# How many MAD-derived sigmas below the median the black point is set, in
# the auto-stretch's shadow-clipping step. -2.8 is Siril's
# `AS_DEFAULT_SHADOWS_CLIPPING` (`src/filters/mtf.h`, read 2026-11-22), also
# the PixInsight default -- not something re-derived for this codebase, but
# adopted so our viewer draws the picture Siril's Autostretch draws.
_AUTOSTRETCH_SHADOWS_CLIP_SIGMA = TunedParameter(
    name="shared.autostretch_shadows_clip_sigma",
    default=-2.8,
    unit="sigma",
    description="Offset of the autostretch black point from the image median, in noise units.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# The same value under a public name, for code that asks Siril for this
# stretch and needs the number to match.
AUTOSTRETCH_SHADOWS_CLIP_SIGMA = resolve(_AUTOSTRETCH_SHADOWS_CLIP_SIGMA).value

# The 0-1 brightness the image's own median is pushed to by the auto-
# stretch's midtones step. 0.25 is Siril's `AS_DEFAULT_TARGET_BACKGROUND`
# (`src/filters/mtf.h`), adopted like `_AUTOSTRETCH_SHADOWS_CLIP_SIGMA`; it
# makes faint real structure visible without also lifting near-zero
# background noise into visibility.
_AUTOSTRETCH_TARGET_BACKGROUND = TunedParameter(
    name="shared.autostretch_target_background",
    default=0.25,
    unit="fraction",
    description="Display brightness the autostretch gives the image median.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# How many pixels the plain percentile stretch samples from a large image,
# and the seed that picks them. The sample sets the black and white points,
# so an unseeded sample would shift the preview slightly on every run. A
# fixed seed makes two renders of the same image identical. The seed value
# is arbitrary.
_PERCENTILE_SAMPLE_COUNT = 10000
PREVIEW_SAMPLE_SEED = 20261010

# The three remaining numbers of Siril's autostretch, adopted from
# `find_linked_midtones_balance` in `src/filters/mtf.c` (read 2026-11-22), not
# tuned here. The first is the spread Siril uses when a channel's MAD is
# exactly 0, a guard against a zero spread ("avoid breakdown point"). The
# second is the median, as a fraction of full scale, above which a picture
# counts as inverted (a bright background): Siril then clips the highlights
# instead of the shadows. The third is Siril's full scale for 16-bit data, its
# `USHRT_MAX`; 8-bit data uses 255 and floating-point data 1.0.
_AUTOSTRETCH_ZERO_SPREAD_GUARD = TunedParameter(
    name="shared.autostretch_zero_spread_guard",
    default=0.001,
    unit="fraction of full scale",
    description="Spread the autostretch uses when the median absolute deviation is exactly 0 (Siril).",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)
_AUTOSTRETCH_INVERTED_MEDIAN = TunedParameter(
    name="shared.autostretch_inverted_median",
    default=0.5,
    unit="fraction of full scale",
    description="Median above which every channel makes the autostretch clip highlights (Siril).",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)
_FULL_SCALE_16_BIT = 65535.0
_FULL_SCALE_8_BIT = 255.0

# A pixel smaller than this fraction of the brightest pixel is treated as
# numerical dust, not background, when measuring the background level.
# Found on a real Siril-registered Albireo stack: the interpolation kernel
# leaves values down around 1e-31 across empty sky, which are technically
# nonzero but carry no signal, and they swamped the median/MAD estimate.
# 1e-6 is about ten times float32's machine epsilon (1.2e-7), i.e. below
# what float32 arithmetic on the stack could meaningfully represent
# relative to its brightest pixel. Not tuned beyond that one real stack.
_AUTOSTRETCH_DUST_FRACTION_OF_PEAK = TunedParameter(
    name="shared.autostretch_dust_fraction_of_peak",
    default=1e-6,
    unit="fraction",
    description="Fraction of the peak below which a pixel is ignored as dust in the sky statistics.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# About how many pixels the sky grid holds (see `measure_sky_background`).
# The median's error shrinks with the square root of the sample size: at this
# size it is about 0.2% of the noise (sigma) for a typical image, far below one
# display grey level, while measuring a 24-megapixel frame takes about 20 ms
# instead of 1-2 s. Checked on two real 24-megapixel frames, not tuned
# further.
_AUTOSTRETCH_SKY_SAMPLE_COUNT = 500_000

# How many columns apart the two pixels of a noise difference are, when the
# sky noise is measured (see `measure_sky_background`). Registration
# resamples the frames, which blends neighbouring pixels and makes their noise
# partly alike, so two pixels that are too close cancel too much and the noise
# reads too low. Measured on the M 31 and NGC 7331 L stacks on 2026-11-20 (the
# same test as the viewer's Linear mode): M 31 read 3.56e-5 at 1 px, 4.28e-5
# at 3 px and 4.42e-5 at 8 px, so it settles from about 3 px. 4 px is the
# first spacing past that, close enough that a galaxy's smooth light still
# cancels in the difference.
_SKY_NEIGHBOR_SPACING_PIXELS = TunedParameter(
    name="shared.sky_neighbor_spacing_pixels",
    default=4,
    unit="pixels",
    description="Columns between the two pixels whose difference measures the sky noise.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# A sampled pixel further than this many sky sigmas from the sky level is
# left out of the sky statistics, and the clipping repeats until nothing more
# is dropped. 3 sigma drops stars and galaxy light but keeps 99.7% of
# Gaussian sky. The same value the viewer's Linear mode uses.
_SKY_CLIP_SIGMAS = TunedParameter(
    name="shared.sky_clip_sigmas",
    default=3.0,
    unit="sigma",
    description="Sky pixels further than this from the sky level are clipped out of its statistics.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D45",
)

# The most clipping rounds. Each round drops the bright tail, so the median
# moves down toward the sky. Five rounds settled on the two real stacks
# (the viewer's Linear mode uses the same limit).
_SKY_MAXIMUM_CLIP_ROUNDS = 5

# The fewest sampled pixels that can describe the sky.
_SKY_MINIMUM_SAMPLE_PIXELS = 25

# The picture is scaled to 0-255 this many pixels at a time. Whole-image
# arithmetic makes several temporary copies of the image (192 MB each for a
# 24-megapixel float64 frame); a block this size stays in the processor's
# cache and the result is identical. About one million pixels was fastest on
# the two real frames it was timed on.
_SCALING_BLOCK_PIXELS = 1_000_000


def _midtones_transfer_function(x: np.ndarray, midtones: float) -> np.ndarray:
    """Apply the midtones transfer function (MTF) PixInsight/Siril use.

    This is the nonlinear curve an "Autostretch" display applies after
    black-point clipping: it pushes the image's median brightness to a
    target midtone value, brightening faint real structure while leaving
    pure black (0) and pure white (1) fixed, unlike a linear stretch which
    treats every brightness level the same.

    Parameters
    ----------
    x : `numpy.ndarray`
        Brightness values already normalized to 0-1 (post black-point
        clipping).
    midtones : `float`
        The balance point, strictly between 0 and 1: this input value
        maps to 0.5. The curve's denominator is never zero anywhere in
        that range, so no special-casing is needed.

    Returns
    -------
    stretched : `numpy.ndarray`
        `x`, remapped through the curve, still in 0-1.
    """
    return ((midtones - 1.0) * x) / ((2.0 * midtones - 1.0) * x - midtones)


def _solve_midtones_balance(normalized_median: float, target_background: float) -> float:
    """Find the midtones balance that maps a median to a target brightness.

    Parameters
    ----------
    normalized_median : `float`
        The image's median, already black-point-normalized. Must be
        strictly between 0 and 1: a median sitting exactly at black or
        white cannot be moved to a target brightness by any curve.
    target_background : `float`
        The brightness, strictly between 0 and 1, the median should land
        on.

    Returns
    -------
    midtones : `float`
        The `midtones` value for which
        `_midtones_transfer_function(normalized_median, midtones) ==
        target_background`. Always strictly between 0 and 1.
    """
    numerator = normalized_median * (target_background - 1.0)
    denominator = (2.0 * target_background - 1.0) * normalized_median - target_background
    return float(numerator / denominator)


def _clipped_median_and_sigma(values: np.ndarray) -> tuple[float, float] | None:
    """Find the middle and spread of values by repeated sigma clipping.

    Each round takes the median as the middle and the median absolute
    deviation (scaled to a standard deviation) as the spread, then drops the
    values further than the clip limit from the middle. It stops when nothing
    more is dropped.

    Parameters
    ----------
    values : `numpy.ndarray`
        Finite values, 1-D.

    Returns
    -------
    middle_and_sigma : `tuple` [`float`, `float`] or `None`
        The middle and the spread, or `None` when too few values are left or
        the spread is not above zero.
    """
    kept = np.sort(values)
    clip_sigmas = resolve(_SKY_CLIP_SIGMAS).value
    middle = sigma = 0.0
    for _ in range(_SKY_MAXIMUM_CLIP_ROUNDS):
        if kept.size < _SKY_MINIMUM_SAMPLE_PIXELS:
            return None
        middle = float(np.median(kept))
        sigma = robust_sigma(kept)
        if not sigma > 0.0:
            return None
        clipped = kept[np.abs(kept - middle) <= clip_sigmas * sigma]
        if clipped.size == kept.size:
            break
        kept = clipped
    if kept.size < _SKY_MINIMUM_SAMPLE_PIXELS:
        return None
    return middle, sigma


def _sky_sample(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
    """Sample an image on a sparse grid, each pixel with a partner nearby.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D.

    Returns
    -------
    sample : `tuple` [`numpy.ndarray`, `numpy.ndarray`] or `None`
        The sampled pixels and their partners, `_SKY_NEIGHBOR_SPACING_PIXELS`
        columns to the right, both 1-D and `float64`. Pixels that are not
        finite or are numerical dust (smaller than a tiny fraction of the
        brightest pixel, as in empty registration borders) are left out, in
        both arrays together. `None` when the image is not 2-D or too small.
    """
    if arr.ndim != 2 or arr.size == 0:
        return None
    peak = float(np.nanmax(arr))
    if not np.isfinite(peak):
        return None
    spacing = int(resolve(_SKY_NEIGHBOR_SPACING_PIXELS).value)
    step = max(1, int(np.sqrt(arr.size / _AUTOSTRETCH_SKY_SAMPLE_COUNT)))
    rows = np.arange(step // 2, arr.shape[0], step)
    columns = np.arange(step // 2, arr.shape[1] - spacing, step)
    if rows.size == 0 or columns.size == 0:
        return None
    sample = arr[np.ix_(rows, columns)].astype(np.float64)
    partner = arr[np.ix_(rows, columns + spacing)].astype(np.float64)
    dust = peak * resolve(_AUTOSTRETCH_DUST_FRACTION_OF_PEAK).value
    usable = np.isfinite(sample) & np.isfinite(partner) & (np.abs(sample) > dust) & (np.abs(partner) > dust)
    return sample[usable], partner[usable]


def measure_sky_background(arr: np.ndarray) -> tuple[float, float] | None:
    """Measure the empty sky's level and noise, ignoring big bright objects.

    This is the library's one sky measurement. A plain median of every pixel
    would be galaxy light, not sky, when a galaxy covers much of the frame.
    This function samples the image on a regular grid and clips out
    everything that stands above the sky, so a large galaxy or nebula does
    not raise the level. The noise comes from differences between pixels a
    few columns apart: smooth light cancels in a difference, so the noise is
    not widened by the galaxy.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D.

    Returns
    -------
    sky : `tuple` [`float`, `float`] or `None`
        The sky level and the noise (one standard deviation). `None` when the
        image is not 2-D or the sky cannot be measured (too few usable
        pixels, or no spread). Pixels smaller than a tiny fraction of the
        brightest one are numerical dust (empty registration borders) and are
        left out.
    """
    sampled = _sky_sample(arr)
    if sampled is None:
        return None
    sample, partner = sampled
    from_sample = _clipped_median_and_sigma(sample)
    from_differences = _clipped_median_and_sigma(partner - sample)
    if from_sample is None or from_differences is None:
        return None
    return from_sample[0], from_differences[1] / float(np.sqrt(2.0))


def measure_smoothed_sky_background(arr: np.ndarray) -> tuple[float, float] | None:
    """Measure the sky's level and the spread of its pixel values.

    The sibling of `measure_sky_background` for an image whose neighboring
    pixels are alike, such as a median-filtered image. There, the difference
    of two pixels a few columns apart cancels most of the noise, so the
    noise from differences would read far too low. This function takes the
    same grid sample and the same clipping, but its spread is that of the
    sampled pixel values themselves.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D.

    Returns
    -------
    sky : `tuple` [`float`, `float`] or `None`
        The sky level and the spread of the sky's pixel values (one standard
        deviation). `None` when the image is not 2-D or the sky cannot be
        measured.
    """
    sampled = _sky_sample(arr)
    if sampled is None:
        return None
    return _clipped_median_and_sigma(sampled[0])


def sky_level_for_peak(normalized_median: float, normalized_peak: float, peak_brightness: float) -> float:
    """Find the sky brightness that makes a bright feature land on a level.

    The midtones curve has one free setting. Choosing it so that
    `normalized_peak` comes out at `peak_brightness` fixes where the sky
    (`normalized_median`) comes out. A feature far above the sky then leaves
    the sky dark, and a feature barely above the sky pushes the sky up.

    Parameters
    ----------
    normalized_median : `float`
        The sky level after black-point normalization, strictly between 0
        and `normalized_peak`.
    normalized_peak : `float`
        The feature's level after the same normalization, strictly between
        `normalized_median` and 1.
    peak_brightness : `float`
        The brightness, strictly between 0 and 1, the feature should land
        on.

    Returns
    -------
    sky_brightness : `float`
        The brightness, between 0 and 1, the sky lands on.
    """
    midtones = _solve_midtones_balance(normalized_peak, peak_brightness)
    return float(_midtones_transfer_function(np.array([normalized_median]), midtones)[0])


def midtones_balance_for(normalized_value: float, target_brightness: float) -> float:
    """Find the midtones balance that maps a value to a target brightness.

    This is the public form of the solver the autostretch uses.

    Parameters
    ----------
    normalized_value : `float`
        A value already scaled to 0-1 between the black and white points,
        strictly between 0 and 1.
    target_brightness : `float`
        The brightness, strictly between 0 and 1, it should land on.

    Returns
    -------
    midtones : `float`
        The balance, strictly between 0 and 1.
    """
    return _solve_midtones_balance(normalized_value, target_brightness)


# A pixel counts as white when the stretched picture is at least this fraction
# of full brightness. 0.98 is grey 250 of 255, so a pixel that is merely
# bright is not counted.
_WHITE_FRACTION_OF_FULL_BRIGHTNESS = TunedParameter(
    name="shared.white_fraction_of_full_brightness",
    default=0.98,
    unit="fraction",
    description="Display brightness at or above which a stretched pixel counts as white.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D59",
)

# Every n-th pixel in each direction is used to count white pixels. A share of
# 1% of a 9-megapixel image is 90000 pixels, so one pixel in 16 still holds
# more than five thousand and the share is stable to a small fraction.
_WHITE_COUNT_STRIDE = 4


def stretch_like_autostretch(values: np.ndarray, arr: np.ndarray, sky_level: float) -> np.ndarray | None:
    """Stretch brightness values the way the normal autostretch would.

    The curve is the one `white_fraction_after_autostretch` describes, set from
    the sky and the brightest pixel of `arr`. The values to stretch need not
    be `arr` itself. This lets a caller ask what the normal stretch would do to
    one part of an image, such as a single object.

    Parameters
    ----------
    values : `numpy.ndarray`
        The linear brightness values to stretch, of any shape.
    arr : `numpy.ndarray`
        The whole image, 2-D. Its sky and brightest pixel set the curve.
    sky_level : `float`
        The brightness, strictly between 0 and 1, the sky would land on.

    Returns
    -------
    stretched : `numpy.ndarray` or `None`
        `values` after the stretch, between 0 and 1 and the same shape as
        `values`. `None` when the image has no measurable sky to anchor the
        stretch to.
    """
    sky = measure_sky_background(arr)
    if sky is None:
        return None
    median, sigma = sky
    peak = float(np.nanmax(arr))
    black_point = max(median + resolve(_AUTOSTRETCH_SHADOWS_CLIP_SIGMA).value * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return None
    normalized_median = (median - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < 1.0 or not 0.0 < sky_level < 1.0:
        return None
    midtones = _solve_midtones_balance(normalized_median, sky_level)
    return _midtones_transfer_function(
        np.clip((np.asarray(values, dtype=np.float64) - black_point) / (peak - black_point), 0.0, 1.0),
        midtones,
    )


def white_fraction_after_autostretch(arr: np.ndarray, sky_level: float) -> float | None:
    """Find what share of an image an autostretch would push to white.

    The stretch is the one `ImageScaler` and Siril's Autostretch apply: the
    black point set a few sigma below the sky, the brightest pixel as white,
    and the midtones curve that puts the sky at `sky_level`. A faint target
    leaves only the cores of stars white. A bright object such as the Moon
    puts a large part of the picture at white, and its detail is lost.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D.
    sky_level : `float`
        The brightness, strictly between 0 and 1, the sky would land on.

    Returns
    -------
    white_fraction : `float` or `None`
        The share of pixels, between 0 and 1, that would come out white.
        `None` when the image has no measurable sky to anchor the stretch to.
    """
    sample = arr[::_WHITE_COUNT_STRIDE, ::_WHITE_COUNT_STRIDE]
    sample = sample[np.isfinite(sample)]
    if sample.size == 0:
        return None
    stretched = stretch_like_autostretch(sample, arr, sky_level)
    if stretched is None:
        return None
    return float(np.mean(stretched >= resolve(_WHITE_FRACTION_OF_FULL_BRIGHTNESS).value))


def _full_scale_of(data: np.ndarray) -> float:
    """Find the brightness Siril treats as full scale for a data type.

    Parameters
    ----------
    data : `numpy.ndarray`
        The raw image data, read before it is converted to float.

    Returns
    -------
    full_scale : `float`
        1.0 for floating-point data, 255 for 8-bit integers, and 65535 for
        every other type (Siril's 16-bit full scale).
    """
    dtype = np.asarray(data).dtype
    if np.issubdtype(dtype, np.floating):
        return 1.0
    if dtype.itemsize == 1:
        return _FULL_SCALE_8_BIT
    return _FULL_SCALE_16_BIT


def _channel_median_and_spread(channel: np.ndarray, full_scale: float) -> tuple[float, float] | None:
    """Measure one channel's median and spread the way Siril does.

    Siril takes the median and the median absolute deviation (MAD) of every
    pixel that is not exactly 0 and not a NaN (`src/algos/statistics.c` and
    `statistics_float.c`), not of a sample of empty sky.

    Parameters
    ----------
    channel : `numpy.ndarray`
        One channel, 2-D.
    full_scale : `float`
        The brightness that counts as 1.0 (see `_full_scale_of`).

    Returns
    -------
    median_and_spread : `tuple` [`float`, `float`] or `None`
        The median and the MAD scaled to a standard deviation, both as
        fractions of full scale. The spread is Siril's guard value when the
        MAD is exactly 0. `None` when the channel has no usable pixel.
    """
    values = channel[np.isfinite(channel) & (np.abs(channel) > 0.0)]
    if values.size == 0:
        return None
    median = float(np.median(values))
    spread = float(np.median(np.abs(values - median))) / full_scale * MAD_TO_SIGMA
    if not spread > 0.0:
        spread = resolve(_AUTOSTRETCH_ZERO_SPREAD_GUARD).value
    return median / full_scale, spread


def _autostretch_parameters(arr: np.ndarray, full_scale: float = 1.0) -> tuple[float, float, float] | None:
    """Choose the black point, white point and midtones balance for an image.

    This is Siril's autostretch with its channels linked
    (`find_linked_midtones_balance` in `src/filters/mtf.c`): the median and
    spread of the whole image, with exact zeros and NaNs left out, set a black
    point 2.8 spreads below the median and a midtones balance that draws the
    median at 0.25. White is full scale. When every channel's median is above
    half of full scale (a bright background), the roles flip: black is 0 and
    white is 2.8 spreads above the median. The viewer draws every channel with
    one set of numbers, so a colour image uses Siril's linked form, which
    averages the channels' medians and black points.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D or colour (colour axis last).
    full_scale : `float`, optional
        The brightness Siril counts as 1.0 for this data type (see
        `_full_scale_of`). Floating-point data whose median is above 1.0 is
        in counts, not normalized, so it uses the 16-bit full scale instead.
        Defaults to 1.0.

    Returns
    -------
    parameters : `tuple` [`float`, `float`, `float`] or `None`
        The black point and white point (in the image's own units) and the
        midtones balance, or `None` when Siril's curve cannot be drawn: the
        image has no pixel that is neither 0 nor NaN, all of those pixels
        have one value, or the balance does not fall strictly between 0 and
        1. The caller falls back to a plain percentile stretch then.
    """
    if arr.size == 0:
        return None
    channels = [arr] if arr.ndim == 2 else [arr[..., index] for index in range(arr.shape[-1])]
    if full_scale <= 1.0:
        finite_values = arr[np.isfinite(arr) & (np.abs(arr) > 0.0)]
        if finite_values.size and float(np.median(finite_values)) > 1.0:
            full_scale = _FULL_SCALE_16_BIT
    statistics = [_channel_median_and_spread(channel, full_scale) for channel in channels]
    if any(measured is None for measured in statistics):
        logger.debug("Image has no usable pixels in a channel; using a percentile stretch instead.")
        return None
    nonzero_pixels = arr[np.isfinite(arr) & (np.abs(arr) > 0.0)]
    if not float(np.max(nonzero_pixels)) > float(np.min(nonzero_pixels)):
        logger.debug("Image has a single brightness; using a percentile stretch instead.")
        return None

    medians = [measured[0] for measured in statistics if measured is not None]
    spreads = [measured[1] for measured in statistics if measured is not None]
    mean_median = float(np.mean(medians))
    shadows_clip = resolve(_AUTOSTRETCH_SHADOWS_CLIP_SIGMA).value
    target = resolve(_AUTOSTRETCH_TARGET_BACKGROUND).value
    inverted_median = resolve(_AUTOSTRETCH_INVERTED_MEDIAN).value
    if all(median > inverted_median for median in medians):
        black = 0.0
        white = min(
            float(np.mean([m - shadows_clip * s for m, s in zip(medians, spreads, strict=True)])), 1.0
        )
        midtones = 1.0 - _solve_midtones_balance(white - mean_median, target)
    else:
        black = max(
            float(np.mean([m + shadows_clip * s for m, s in zip(medians, spreads, strict=True)])), 0.0
        )
        white = 1.0
        midtones = _solve_midtones_balance(mean_median - black, target)
    if not (white > black and 0.0 < midtones < 1.0):
        return None
    return black * full_scale, white * full_scale, midtones


def _scale_to_uint8_in_blocks(
    arr: np.ndarray, vmin: float, vmax: float, midtones: float | None
) -> np.ndarray:
    """Map brightness values to 0-255, a block of rows at a time.

    Gives exactly the same numbers as doing the whole image in one step, but
    without making several full-size temporary copies (see
    `_SCALING_BLOCK_PIXELS`).

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image (2-D, or colour with the colour axis last).
    vmin : `float`
        The brightness that becomes 0.
    vmax : `float`
        The brightness that becomes 255.
    midtones : `float` or `None`
        The midtones balance for the nonlinear curve, or `None` for a plain
        linear scaling.

    Returns
    -------
    img8 : `numpy.ndarray`
        The image as unsigned 8-bit numbers, with the same shape as `arr`.
    """

    def scale(block: np.ndarray) -> np.ndarray:
        """Scale one block of rows.

        Returns
        -------
        scaled : `numpy.ndarray`
            The block as unsigned 8-bit numbers.
        """
        img = np.clip((block - vmin) / (vmax - vmin), 0.0, 1.0)
        if midtones is not None:
            img = _midtones_transfer_function(img, midtones)
        return (img * 255.0).astype(np.uint8)

    if arr.ndim < 2 or arr.size == 0:
        return scale(arr)
    img8 = np.empty(arr.shape, dtype=np.uint8)
    rows_per_block = max(1, _SCALING_BLOCK_PIXELS // max(1, arr[0].size))
    for first_row in range(0, arr.shape[0], rows_per_block):
        img8[first_row : first_row + rows_per_block] = scale(arr[first_row : first_row + rows_per_block])
    return img8


def percentile_sample(arr: np.ndarray, seed: int = PREVIEW_SAMPLE_SEED) -> np.ndarray:
    """Pick the random pixels the percentile stretch measures.

    The same `seed` always gives the same pixels, so a preview does not
    change between runs. The pixels are spread over the whole image, not
    taken from its first rows.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image data, any shape.
    seed : `int`, optional
        Seed for the random generator. Defaults to `PREVIEW_SAMPLE_SEED`.

    Returns
    -------
    sample : `numpy.ndarray`
        `_PERCENTILE_SAMPLE_COUNT` pixel values. A pixel can be picked
        more than once.
    """
    chosen = np.random.default_rng(seed).integers(0, arr.size, _PERCENTILE_SAMPLE_COUNT)
    return arr.flat[chosen]


def _display_array(data: np.ndarray) -> np.ndarray:
    """Turn image data into the 2-D or colour-last array that is drawn.

    Parameters
    ----------
    data : `numpy.ndarray`
        The raw image data, 2-D or colour (colour axis first or last).

    Returns
    -------
    arr : `numpy.ndarray`
        Float data, 2-D or with the colour axis last. A cube that is not
        colour is reduced to its first plane.
    """
    arr = np.asarray(data, dtype=float)
    if arr.ndim == 3:
        if arr.shape[0] in [3, 4]:
            arr = np.transpose(arr, (1, 2, 0))
        elif arr.shape[2] not in [3, 4]:
            arr = arr[0] if arr.shape[0] == 1 else arr[:, :, 0]
    return arr


class ImageScaler:
    """Adjust an astronomy image's brightness and contrast for viewing."""

    @staticmethod
    def autostretch_parameters(data: np.ndarray) -> StretchParameters | None:
        """Return the automatic stretch `scale_to_uint8` would use.

        Parameters
        ----------
        data : `numpy.ndarray`
            The raw image data, 2-D or colour.

        Returns
        -------
        parameters : `StretchParameters` or `None`
            The black point, white point and midtones balance, or `None`
            when the image has no measurable sky (then `scale_to_uint8`
            uses a plain percentile stretch).
        """
        autostretch = _autostretch_parameters(_display_array(data), _full_scale_of(data))
        if autostretch is None:
            return None
        black_point, white_point, midtones = autostretch
        return StretchParameters(black_point=black_point, white_point=white_point, midtones=midtones)

    @staticmethod
    def scale_to_uint8(
        data: np.ndarray,
        vmin: float | None = None,
        vmax: float | None = None,
        stretch: bool = True,
        percentiles: tuple[float, float] = (1.0, 99.0),
    ) -> tuple[np.ndarray, float, float]:
        """Convert raw image data into standard computer colors (0-255).

        When `vmin`/`vmax` are not given, this applies Siril's Autostretch
        exactly (see `_autostretch_parameters`): the median and spread of
        the whole image set a black point a few spreads below the median,
        and a nonlinear midtones curve (see `_midtones_transfer_function`)
        puts the median at 25% grey. A linear percentile stretch made a
        harmless, tiny (~1e-4) interpolation-ringing artifact look like a
        dramatic pattern around bright stars, because it maps the entire
        near-zero background range up to full visible contrast. An image
        for which Siril's curve cannot be drawn (see
        `_autostretch_parameters`) falls back to the plain percentile
        stretch. Passing an explicit `vmin`/`vmax` (e.g. to keep several
        frames on one fixed scale) always gets plain linear scaling
        instead, since that is a deliberate choice of absolute brightness
        range, not a request for the automatic display stretch.

        Parameters
        ----------
        data : `numpy.ndarray`
            The raw image data, 2-D or colour.
        vmin, vmax : `float`, optional
            A fixed brightness range to map to 0-255 (see above).
        stretch : `bool`, optional
            Whether to apply the automatic stretch. Defaults to `True`.
        percentiles : `tuple` [`float`, `float`], optional
            Percentiles used for the plain linear stretch.

        Returns
        -------
        result : `tuple`
            The new image data, the black point used, and the white
            point used.
        """
        # Handle multi-channel data (e.g. RGB FITS)
        arr = _display_array(data)

        autostretch = (
            _autostretch_parameters(arr, _full_scale_of(data))
            if stretch and vmin is None and vmax is None
            else None
        )
        midtones: float | None = None
        if autostretch is not None:
            vmin, vmax, midtones = autostretch

        if vmin is None or vmax is None:
            if stretch:
                try:
                    # Sampling for large arrays to improve performance
                    if arr.size > 100000:
                        sample = percentile_sample(arr)
                        calc_vmin, calc_vmax = np.percentile(sample, percentiles)
                    else:
                        calc_vmin, calc_vmax = np.percentile(arr, percentiles)

                    if vmin is None:
                        vmin = calc_vmin
                    if vmax is None:
                        vmax = calc_vmax
                except DATA_ERRORS as e:
                    logger.warning("Error calculating percentiles for scaling: %s", e)

            # Fallback to absolute min/max if stretch is off or failed
            if vmin is None:
                vmin = float(np.nanmin(arr))
            if vmax is None:
                vmax = float(np.nanmax(arr))

        if not np.isfinite(vmin):
            vmin = 0.0
        if not np.isfinite(vmax) or vmax == vmin:
            vmax = vmin + 1.0

        # Clip and scale
        return _scale_to_uint8_in_blocks(arr, vmin, vmax, midtones), float(vmin), float(vmax)
