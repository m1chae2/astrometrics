"""Tools for adjusting image brightness and contrast for display.

This file only handles the math to convert raw telescope data into
a format that can be drawn on a screen. Saving the actual image
files happens somewhere else.
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# How many MAD-derived sigmas below the median the black point is set, in
# the auto-stretch's shadow-clipping step. -2.8 is the standard default
# used by both PixInsight's "AutoStretch" Screen Transfer Function and
# Siril's own Autostretch display mode -- not something re-derived for
# this codebase, but adopted so our stretch looks like the one users
# already know from those tools.
_AUTOSTRETCH_SHADOWS_CLIP_SIGMA = -2.8

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


def _autostretch_parameters(arr: np.ndarray) -> tuple[float, float, float] | None:
    """Choose the black point, white point and midtones balance for an image.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D or colour.

    Returns
    -------
    parameters : `tuple` [`float`, `float`, `float`] or `None`
        The black point, white point and midtones balance, or `None` when
        the image has no measurable background to anchor a stretch to
        (for instance one that is almost entirely exactly 0). The caller
        should fall back to a plain percentile stretch then.
    """
    if arr.size == 0:
        return None
    peak = float(np.nanmax(arr))
    if not np.isfinite(peak):
        return None

    populated = arr[np.abs(arr) > peak * _AUTOSTRETCH_DUST_FRACTION_OF_PEAK]
    stats_source = populated if populated.size >= _AUTOSTRETCH_MINIMUM_POPULATED_PIXELS else arr
    median = float(np.nanmedian(stats_source))
    sigma = float(np.nanmedian(np.abs(stats_source - median))) * _MAD_TO_SIGMA
    if not sigma > 0.0:
        logger.debug("Image background has no measurable spread; using a percentile stretch instead.")
        return None

    black_point = max(median + _AUTOSTRETCH_SHADOWS_CLIP_SIGMA * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return None
    normalized_median = (median - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < 1.0:
        return None
    return black_point, peak, _solve_midtones_balance(normalized_median, _AUTOSTRETCH_TARGET_BACKGROUND)


class ImageScaler:
    """Adjust an astronomy image's brightness and contrast for viewing."""

    @staticmethod
    def scale_to_uint8(
        data: np.ndarray,
        vmin: float | None = None,
        vmax: float | None = None,
        stretch: bool = True,
        percentiles: tuple[float, float] = (1.0, 99.0),
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

        Returns
        -------
        result : `tuple`
            The new image data, the black point used, and the white
            point used.
        """
        arr = np.array(data, dtype=float)

        # Handle multi-channel data (e.g. RGB FITS)
        if arr.ndim == 3:
            if arr.shape[0] in [3, 4]:
                arr = np.transpose(arr, (1, 2, 0))
            elif arr.shape[2] not in [3, 4]:
                arr = arr[0] if arr.shape[0] == 1 else arr[:, :, 0]

        autostretch = _autostretch_parameters(arr) if stretch and vmin is None and vmax is None else None
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
                except Exception as e:
                    logger.warning(f"Error calculating percentiles for scaling: {e}")

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
        img = np.clip((arr - vmin) / (vmax - vmin), 0.0, 1.0)
        if midtones is not None:
            img = _midtones_transfer_function(img, midtones)
        return (img * 255.0).astype(np.uint8), float(vmin), float(vmax)
