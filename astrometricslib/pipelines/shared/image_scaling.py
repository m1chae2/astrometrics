"""Tools for adjusting image brightness and contrast for display.

This file only handles the math to convert raw telescope data into
a format that can be drawn on a screen. Saving the actual image
files happens somewhere else.
"""

import logging

import numpy as np

from astrometricslib.models.target import StretchParameters
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# How many MAD-derived sigmas below the median the black point is set, in
# the auto-stretch's shadow-clipping step. -2.8 is the standard default
# used by both PixInsight's "AutoStretch" Screen Transfer Function and
# Siril's own Autostretch display mode -- not something re-derived for
# this codebase, but adopted so our stretch looks like the one users
# already know from those tools.
_AUTOSTRETCH_SHADOWS_CLIP_SIGMA = -2.8

# The same value under a public name, for code that asks Siril for this
# stretch and needs the number to match.
AUTOSTRETCH_SHADOWS_CLIP_SIGMA = _AUTOSTRETCH_SHADOWS_CLIP_SIGMA

# The 0-1 brightness the image's own median is pushed to by the auto-
# stretch's midtones step. 0.25 is the same PixInsight/Siril default as
# `_AUTOSTRETCH_SHADOWS_CLIP_SIGMA`, chosen there to make faint real
# structure visible without also lifting near-zero background noise into
# visibility.
_AUTOSTRETCH_TARGET_BACKGROUND = 0.25

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
_AUTOSTRETCH_DUST_FRACTION_OF_PEAK = 1e-6

# The fewest populated pixels trusted to describe the background. Below
# this, the whole image is used instead. Unvalidated beyond keeping a
# median/MAD from resting on a handful of pixels.
_AUTOSTRETCH_MINIMUM_POPULATED_PIXELS = 100

# How many randomly chosen pixels stand in for the whole image when the sky
# is measured from a sample (see `measure_sky`). The median's error shrinks
# with the square root of the sample size: at this size it is about 0.2% of
# the noise (sigma) for a typical image, far below one display grey level,
# while measuring a 24-megapixel frame takes about 20 ms instead of 1-2 s.
# Checked on two real 24-megapixel frames, not tuned further.
_AUTOSTRETCH_SKY_SAMPLE_COUNT = 500_000

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
    populated = sky_pixels[np.abs(sky_pixels) > peak * _AUTOSTRETCH_DUST_FRACTION_OF_PEAK]
    stats_source = populated if populated.size >= _AUTOSTRETCH_MINIMUM_POPULATED_PIXELS else sky_pixels
    median = float(np.nanmedian(stats_source))
    sigma = float(np.nanmedian(np.abs(stats_source - median))) * _MAD_TO_SIGMA
    if not sigma > 0.0:
        return None
    return median, sigma, peak


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
_WHITE_FRACTION_OF_FULL_BRIGHTNESS = 0.98

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
    black_point = max(median + _AUTOSTRETCH_SHADOWS_CLIP_SIGMA * sigma, float(np.nanmin(arr)))
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
    return float(np.mean(stretched >= _WHITE_FRACTION_OF_FULL_BRIGHTNESS))


def _autostretch_parameters(
    arr: np.ndarray, sample_pixels: bool = False
) -> tuple[float, float, float] | None:
    """Choose the black point, white point and midtones balance for an image.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D or colour.
    sample_pixels : `bool`, optional
        Measure the sky from a sample of the pixels (see `measure_sky`).
        Defaults to `False`.

    Returns
    -------
    parameters : `tuple` [`float`, `float`, `float`] or `None`
        The black point, white point and midtones balance, or `None` when
        the image has no measurable background to anchor a stretch to
        (for instance one that is almost entirely exactly 0). The caller
        should fall back to a plain percentile stretch then.
    """
    sky = measure_sky(arr, sample_pixels=sample_pixels)
    if sky is None:
        logger.debug("Image background has no measurable spread; using a percentile stretch instead.")
        return None
    median, sigma, peak = sky

    black_point = max(median + _AUTOSTRETCH_SHADOWS_CLIP_SIGMA * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return None
    normalized_median = (median - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < 1.0:
        return None
    return black_point, peak, _solve_midtones_balance(normalized_median, _AUTOSTRETCH_TARGET_BACKGROUND)


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
                        sample = arr.flat[np.random.randint(0, arr.size, 10000)]
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
