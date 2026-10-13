"""Measures whether the normal preview stretch would ruin the main object.

The normal preview stretch is made for faint things on a dark sky. It lifts
the sky to a visible grey, and that pushes anything much brighter than the sky
to pure white. A faint galaxy is not hurt: only its core and the stars go
white. A small bright object, such as the Ring Nebula, goes white almost
everywhere, and the structure inside it is lost.

The stretch is chosen today by one number: the share of the whole frame that
would turn white (see `bright_object.py`). That works for the Moon, which fills
much of the frame, and fails for a small bright object, which never reaches the
limit. This module asks the question directly, for the object itself:

1. Remove the stars, which are only a few pixels wide, by averaging blocks of
   pixels and then taking a median over a window several times the star
   width. Broad things survive and stars do not.
2. Take the largest connected region that is clearly brighter than the sky.
   That is "the object".
3. Apply the normal stretch to the object's pixels and count how many come out
   white, and how much brightness spread is left among them.

A high white share means the normal stretch destroys the object. This module
only measures. It does not choose a stretch, and nothing calls it yet. The
numbers it gives on the real stacks are what a later choice will be based on.
"""

import logging
from dataclasses import dataclass

import numpy as np
from scipy import ndimage

from astrometricslib.pipelines.shared.image_scaling import (
    measure_sky,
    stretch_like_autostretch,
    white_fraction_after_autostretch,
)
from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level

logger = logging.getLogger(__name__)

__all__ = ["ObjectClippingMeasurement", "measure_object_clipping"]

# The image is averaged in squares this many pixels on a side before the stars
# are removed. A star here is about 2.5 pixels across, so one block holds a
# whole star and averaging already spreads it thin. Averaging also makes the
# rest of the work about sixteen times cheaper. A starting value, not yet
# checked against the real stacks.
_BLOCK_PIXELS = 4

# The median filter that removes the stars is this many blocks wide, which is
# 20 pixels or about eight star widths. A star, even with a bright halo, is
# narrower than half of that and is removed; an object wider than the window
# survives. A starting value, not yet checked against the real stacks.
_STAR_REMOVAL_WINDOW_BLOCKS = 5

# A block is part of an object if it is this many noise levels above the sky of
# the star-free image. The noise is measured on that image, where averaging has
# made it small, so a lower number would pick up the grain of the sky. A
# starting value, not yet checked against the real stacks.
_OBJECT_DETECTION_SIGMA = 5.0

# An object must cover at least this many blocks (128 pixels at the block size
# above). Fewer is a bright star's halo or a hot patch, not an object. It also
# rules out a planet's disc in a wide frame, which is too small to measure this
# way. A starting value, not yet checked against the real stacks.
_MINIMUM_OBJECT_BLOCKS = 8

# A stretched pixel at or above this brightness counts as white. The same limit
# `white_fraction_after_autostretch` uses, so the two numbers can be compared.
_WHITE_BRIGHTNESS = 0.98

# The spread of the stretched object is measured between these percentiles of
# its pixels, so a few stray pixels do not set it.
_CONTRAST_LOW_PERCENTILE = 5.0
_CONTRAST_HIGH_PERCENTILE = 95.0

_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class ObjectClippingMeasurement:
    """What the normal stretch would do to the main object of a stack.

    Attributes
    ----------
    object_found : `bool`
        Whether a connected region clearly brighter than the sky, and large
        enough to measure, was found. When `False`, the three numbers about
        the object are 0.
    object_area_fraction : `float`
        The share of the frame the object covers, between 0 and 1.
    clipped_share : `float`
        The share of the object's pixels the normal stretch would turn white,
        between 0 and 1. Near 0, the object keeps its structure. Near 1, the
        object is a flat white patch.
    contrast_left : `float`
        The spread of brightness among the object's pixels after the normal
        stretch, between 0 and 1: the 95th percentile minus the 5th. Near 0,
        the stretch has flattened the object.
    frame_white_fraction : `float` or `None`
        The share of the whole frame the normal stretch would turn white,
        which is the number the stretch is chosen by today. `None` when it
        cannot be measured.
    """

    object_found: bool
    object_area_fraction: float
    clipped_share: float
    contrast_left: float
    frame_white_fraction: float | None


def _to_two_dimensions(data: np.ndarray) -> np.ndarray | None:
    """Turn a stack into a 2-D array of floats, averaging colour channels.

    Returns
    -------
    arr : `numpy.ndarray` or `None`
        The 2-D image, or `None` if `data` is not an image.
    """
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=0 if arr.shape[0] in (1, 3, 4) else 2)
    return arr if arr.ndim == 2 else None


def _block_mean(arr: np.ndarray, block: int) -> np.ndarray:
    """Average an image in squares of `block` pixels on a side.

    Pixels beyond the last whole block along an edge are left out.

    Returns
    -------
    reduced : `numpy.ndarray`
        The block means, `block` times smaller in each direction.
    """
    height, width = (size // block * block for size in arr.shape)
    cropped = arr[:height, :width]
    return cropped.reshape(height // block, block, width // block, block).mean(axis=(1, 3))


def measure_object_clipping(
    data: np.ndarray, sky_level: float | None = None
) -> ObjectClippingMeasurement | None:
    """Measure what the normal stretch would do to the main object of a stack.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack, in linear units. A colour image is averaged across its
        channels first.
    sky_level : `float`, optional
        The brightness the normal stretch would put the sky at. Defaults to the
        level the preview chooses for this stack (see `choose_sky_level`).

    Returns
    -------
    measurement : `ObjectClippingMeasurement` or `None`
        The measurement, or `None` if the stack is not an image or its sky
        cannot be measured.
    """
    arr = _to_two_dimensions(data)
    if arr is None or min(arr.shape) < _BLOCK_PIXELS * _STAR_REMOVAL_WINDOW_BLOCKS:
        return None
    sky = measure_sky(arr, sample_pixels=True)
    if sky is None:
        return None
    sky_median = sky[0]
    if sky_level is None:
        sky_level = choose_sky_level(arr).sky_level
    frame_white_fraction = white_fraction_after_autostretch(arr, sky_level)

    # Bad pixels become the sky, so they neither make an object nor hide one.
    filled = np.where(np.isfinite(arr), arr, sky_median)
    star_free = ndimage.median_filter(
        _block_mean(filled, _BLOCK_PIXELS), size=_STAR_REMOVAL_WINDOW_BLOCKS, mode="nearest"
    )
    layer_median = float(np.median(star_free))
    layer_noise = float(np.median(np.abs(star_free - layer_median))) * _MAD_TO_SIGMA
    no_object = ObjectClippingMeasurement(False, 0.0, 0.0, 0.0, frame_white_fraction)
    if not layer_noise > 0.0:
        return no_object

    labels, count = ndimage.label(star_free > layer_median + _OBJECT_DETECTION_SIGMA * layer_noise)
    if count == 0:
        return no_object
    areas = ndimage.sum(np.ones_like(star_free), labels, index=np.arange(1, count + 1))
    largest = int(np.argmax(areas)) + 1
    in_object = labels == largest
    object_blocks = int(in_object.sum())
    if object_blocks < _MINIMUM_OBJECT_BLOCKS:
        return no_object

    stretched = stretch_like_autostretch(star_free[in_object], arr, sky_level)
    if stretched is None:
        return no_object
    low, high = np.percentile(stretched, [_CONTRAST_LOW_PERCENTILE, _CONTRAST_HIGH_PERCENTILE])
    return ObjectClippingMeasurement(
        object_found=True,
        object_area_fraction=object_blocks / star_free.size,
        clipped_share=float(np.mean(stretched >= _WHITE_BRIGHTNESS)),
        contrast_left=float(high - low),
        frame_white_fraction=frame_white_fraction,
    )
