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

logger = logging.getLogger(__name__)

# How many MAD-derived sigmas below the median the black point is set, in
# the auto-stretch's shadow-clipping step. -2.8 is the standard default
# used by both PixInsight's "AutoStretch" Screen Transfer Function and
# Siril's own Autostretch display mode -- not something re-derived for
# this codebase, but adopted so our stretch looks like the one users
# already know from those tools.
_AUTOSTRETCH_SHADOWS_CLIP_SIGMA = TunedParameter(
    name="shared.autostretch_shadows_clip_sigma",
    default=-2.8,
    unit="sigma",
    description="Offset of the autostretch black point from the sky median, in noise units.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# The same value under a public name, for code that asks Siril for this
# stretch and needs the number to match.
AUTOSTRETCH_SHADOWS_CLIP_SIGMA = resolve(_AUTOSTRETCH_SHADOWS_CLIP_SIGMA).value

# The 0-1 brightness the image's own median is pushed to by the auto-
# stretch's midtones step. 0.25 is the same PixInsight/Siril default as
# `_AUTOSTRETCH_SHADOWS_CLIP_SIGMA`, chosen there to make faint real
# structure visible without also lifting near-zero background noise into
# visibility.
_AUTOSTRETCH_TARGET_BACKGROUND = TunedParameter(
    name="shared.autostretch_target_background",
    default=0.25,
    unit="fraction",
    description="Display brightness the autostretch gives the sky background.",
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

# MAD -> standard deviation for normally-distributed data (1 / Phi^-1(3/4)).
# A fixed astronomical/statistical constant, not a tuned parameter.
_MAD_TO_SIGMA = 1.4826

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

# The fewest populated pixels trusted to describe the background. Below
# this, the whole image is used instead. Unvalidated beyond keeping a
# median/MAD from resting on a handful of pixels.
_AUTOSTRETCH_MINIMUM_POPULATED_PIXELS = TunedParameter(
    name="shared.autostretch_minimum_populated_pixels",
    default=100,
    unit="pixels",
    description="Fewest populated pixels needed to measure the sky statistics from them alone.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D45",
)

# How many randomly chosen pixels stand in for the whole image when the sky
# is measured from a sample (see `measure_sky`). The median's error shrinks
# with the square root of the sample size: at this size it is about 0.2% of
# the noise (sigma) for a typical image, far below one display grey level,
# while measuring a 24-megapixel frame takes about 20 ms instead of 1-2 s.
# Checked on two real 24-megapixel frames, not tuned further.
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


def measure_sky(arr: np.ndarray, sample_pixels: bool = False) -> tuple[float, float, float] | None:
    """Measure the sky level and noise of an image, as the autostretch does.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D or colour.
    sample_pixels : `bool`, optional
        When `True`, the sky level and noise are measured from about
        500,000 randomly chosen pixels instead of every pixel. That is much
        faster on a large image and gives nearly the same numbers. The
        brightest pixel is always found from the whole image. The same
        pixels are chosen every time, so the same image always gives the
        same answer. Defaults to `False`, which uses every pixel.

    Returns
    -------
    sky : `tuple` [`float`, `float`, `float`] or `None`
        The sky level (the median), the noise (the median absolute
        deviation scaled to a standard deviation) and the brightest pixel.
        `None` when the image has no measurable sky: it is empty, has no
        finite pixels, or has no spread (for instance one that is almost
        entirely exactly 0). Pixels smaller than a tiny fraction of the
        brightest one are left out, because they are numerical dust and not
        sky.
    """
    if arr.size == 0:
        return None
    peak = float(np.nanmax(arr))
    if not np.isfinite(peak):
        return None
    sky_pixels = arr
    if sample_pixels and arr.size > _AUTOSTRETCH_SKY_SAMPLE_COUNT:
        chosen = np.random.default_rng(0).integers(0, arr.size, _AUTOSTRETCH_SKY_SAMPLE_COUNT)
        sky_pixels = arr.flat[chosen]
    populated = sky_pixels[np.abs(sky_pixels) > peak * resolve(_AUTOSTRETCH_DUST_FRACTION_OF_PEAK).value]
    stats_source = (
        populated if populated.size >= resolve(_AUTOSTRETCH_MINIMUM_POPULATED_PIXELS).value else sky_pixels
    )
    median = float(np.nanmedian(stats_source))
    sigma = float(np.nanmedian(np.abs(stats_source - median))) * _MAD_TO_SIGMA
    if not sigma > 0.0:
        return None
    return median, sigma, peak


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
        sigma = float(np.median(np.abs(kept - middle))) * _MAD_TO_SIGMA
        if not sigma > 0.0:
            return None
        clipped = kept[np.abs(kept - middle) <= clip_sigmas * sigma]
        if clipped.size == kept.size:
            break
        kept = clipped
    if kept.size < _SKY_MINIMUM_SAMPLE_PIXELS:
        return None
    return middle, sigma


def measure_sky_background(arr: np.ndarray) -> tuple[float, float] | None:
    """Measure the empty sky's level and noise, ignoring big bright objects.

    `measure_sky` takes the median of every pixel. When a galaxy covers much
    of the frame, that median is galaxy light, not sky. This function samples
    the image on a regular grid and clips out everything that stands above
    the sky, so a large galaxy or nebula does not raise the level. The noise
    comes from differences between pixels a few columns apart: smooth light
    cancels in a difference, so the noise is not widened by the galaxy.

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
        left out, as in `measure_sky`.
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
    from_sample = _clipped_median_and_sigma(sample[usable])
    from_differences = _clipped_median_and_sigma((partner - sample)[usable])
    if from_sample is None or from_differences is None:
        return None
    return from_sample[0], from_differences[1] / float(np.sqrt(2.0))


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
    sky = measure_sky(arr)
    if sky is None:
        return None
    median, sigma, peak = sky
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


def _autostretch_parameters(
    arr: np.ndarray, sample_pixels: bool = False
) -> tuple[float, float, float] | None:
    """Choose the black point, white point and midtones balance for an image.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D or colour.
    sample_pixels : `bool`, optional
        Measure the whole-image fallback sky from a sample of the pixels (see
        `measure_sky`). The sky-only measurement always uses its own grid
        sample. Defaults to `False`.

    Returns
    -------
    parameters : `tuple` [`float`, `float`, `float`] or `None`
        The black point, white point and midtones balance, or `None` when
        the image has no measurable background to anchor a stretch to
        (for instance one that is almost entirely exactly 0). The caller
        should fall back to a plain percentile stretch then.
    """
    # The empty sky sets the black point and midtones, so a large galaxy that
    # covers much of the frame does not pull them. If the sky alone cannot be
    # measured, the whole-image median and spread are used as before.
    sky_only = measure_sky_background(arr)
    sky = measure_sky(arr, sample_pixels=sample_pixels)
    if sky is None:
        logger.debug("Image background has no measurable spread; using a percentile stretch instead.")
        return None
    median, sigma, peak = sky
    if sky_only is not None:
        median, sigma = sky_only

    black_point = max(median + resolve(_AUTOSTRETCH_SHADOWS_CLIP_SIGMA).value * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return None
    normalized_median = (median - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < 1.0:
        return None
    return (
        black_point,
        peak,
        _solve_midtones_balance(normalized_median, resolve(_AUTOSTRETCH_TARGET_BACKGROUND).value),
    )


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
    def autostretch_parameters(data: np.ndarray, sample_sky: bool = False) -> StretchParameters | None:
        """Return the automatic stretch `scale_to_uint8` would use.

        Parameters
        ----------
        data : `numpy.ndarray`
            The raw image data, 2-D or colour.
        sample_sky : `bool`, optional
            Measure the sky from a sample of the pixels (see
            `measure_sky`), as `scale_to_uint8` does when asked to.

        Returns
        -------
        parameters : `StretchParameters` or `None`
            The black point, white point and midtones balance, or `None`
            when the image has no measurable sky (then `scale_to_uint8`
            uses a plain percentile stretch).
        """
        autostretch = _autostretch_parameters(_display_array(data), sample_pixels=sample_sky)
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
        sample_sky: bool = False,
    ) -> tuple[np.ndarray, float, float]:
        """Convert raw image data into standard computer colors (0-255).

        When `vmin`/`vmax` are not given, this applies the same
        "Autostretch" curve Siril's own display mode and PixInsight's
        Screen Transfer Function use: a black point clipped a few sigma
        below the image's own median, then a nonlinear midtones curve
        (see `_midtones_transfer_function`) rather than a plain linear
        ramp. A linear percentile stretch made a harmless, tiny
        (~1e-4) interpolation-ringing artifact look like a dramatic
        pattern around bright stars, because it maps the entire
        near-zero background range up to full visible contrast; the
        same background is compressed toward black under Autostretch,
        the way it would be in Siril itself. An image with no measurable
        background (see `_autostretch_parameters`) falls back to the
        plain percentile stretch. Passing an explicit `vmin`/`vmax` (e.g.
        to keep several frames on one fixed scale) always gets plain
        linear scaling instead, since that is a deliberate choice of
        absolute brightness range, not a request for the automatic
        display stretch.

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
        sample_sky : `bool`, optional
            Measure the sky for the autostretch from a sample of the pixels
            instead of all of them (see `measure_sky`). Much faster on a
            large image, with nearly identical output. Defaults to `False`.

        Returns
        -------
        result : `tuple`
            The new image data, the black point used, and the white
            point used.
        """
        # Handle multi-channel data (e.g. RGB FITS)
        arr = _display_array(data)

        autostretch = (
            _autostretch_parameters(arr, sample_pixels=sample_sky)
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
