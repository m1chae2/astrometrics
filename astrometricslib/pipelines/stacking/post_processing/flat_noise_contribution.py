"""Works out how much noise the master flat adds to a calibrated stack.

Dividing every light frame by the master flat copies the flat's own noise into
the frame. The same pattern is in every frame, so stacking does not average it
away (unless the frames are dithered, moved a few pixels between exposures).
This module turns the flat's facts into the noise it adds to the same empty
apertures that `stack_noise.py` measures, in the same units, so the two can be
read side by side. The flat noise is stored as its parts, never as a ratio to
the stack noise.

The master flat in use is not the raw average of the flat frames. When the
average is noisier than ``MAXIMUM_FLAT_NOISE_FRACTION`` (see
`flat_calibration.py`), Siril blurs it with a Gaussian of width ``sigma``
before it is divided into the lights. This module follows that:

1. The flat assessment gives the noise of the average, as a fraction of the
   flat's level, before any blur: one frame's noise divided by the square root
   of the frame count (at most 32 frames are counted).
2. Blurring white noise with a Gaussian of width ``sigma`` leaves the noise
   times the square root of the sum of the squared kernel weights, which is
   about ``1 / (2 sigma sqrt(pi))`` for a kernel wider than 1 px. The sum is
   computed on the sampled kernel, so a small ``sigma`` is also right. The
   result is the noise of the master in use, ``flat_noise_after_smoothing``.
   (The master itself is not kept by the stacking step, so the noise of the
   master was not measured on the master; the figure is derived from the
   assessment and the blur width.)
3. A relative flat error ``e`` at a pixel changes the sky there by the factor
   ``1 - e``. The error is a fixed pattern, so over a circle of radius 4 px
   the changes add up. The pattern is blurred, so its neighboring pixels are
   correlated; the circle's sum is taken exactly: the circle's weights are
   blurred with the same kernel and the squares are summed. The result is the
   standard deviation of the flat's contribution to an aperture sum, in
   the stack's units, equal to the sky level of the stack times
   the master's noise times that sum's square root.

What the figure leaves out. The stack's apertures also subtract the median
of a sky ring. The ring adds noise of its own (a simulation put the subtracted
sum 11% above the figure for a flat that is not blurred), and it removes the
part of a blurred pattern that is the same inside and outside the ring (a
simulation with a 1.5 px blur still came out 7% above the figure, and a wider
blur removes more). A dithered stack averages the pattern over its frames, so
for a dithered stack the figure is an upper bound. It counts the master flat's
noise only; it says nothing about flat-field errors from dust that moved or a
flat taken at another focus.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np
from photutils.aperture import CircularAperture
from scipy.ndimage import gaussian_filter

from astrometricslib.pipelines.stacking.post_processing.stack_noise import APERTURE_RADIUS_PX

logger = logging.getLogger(__name__)

__all__ = [
    "FlatNoiseContribution",
    "FlatNoiseOutcome",
    "flat_noise_contribution",
    "smoothed_noise_factor",
]

# The kernel is cut at this many widths, as scipy does by default. At 4 widths
# the dropped tail holds under 1e-4 of the weight.
_KERNEL_TRUNCATE = 4.0

# The aperture's centre sits at an arbitrary place inside a pixel; the sums are
# averaged over this many places along each pixel axis.
_SUBPIXEL_PLACES = 4


@dataclass(frozen=True)
class FlatNoiseContribution:
    """The noise the master flat adds, as a fraction and in an aperture.

    Parameters
    ----------
    noise_after_smoothing_fraction : `float`
        The noise of the master flat in use, as a fraction of its level, after
        any blur.
    aperture_noise : `float`
        The flat's standard deviation in an aperture sum, in the stack's units.
    sky_level : `float`
        The stack's signal level the fraction was multiplied by, in the stack's
        units.
    """

    noise_after_smoothing_fraction: float
    aperture_noise: float
    sky_level: float


@dataclass(frozen=True)
class FlatNoiseOutcome:
    """The flat's contribution, or the reason there is none.

    Parameters
    ----------
    result : `FlatNoiseContribution` or `None`
        The numbers, or `None` when they could not be worked out.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    result: FlatNoiseContribution | None
    reason: str = ""


def smoothed_noise_factor(sigma_px: float | None) -> float:
    """Give the factor by which a Gaussian blur lowers white noise.

    Parameters
    ----------
    sigma_px : `float` or `None`
        The blur width. `None` or zero means no blur.

    Returns
    -------
    factor : `float`
        The square root of the sum of the squared weights of the sampled,
        normalized two-dimensional kernel. It is 1 without a blur and about
        ``1 / (2 sigma sqrt(pi))`` for a wide one.
    """
    if not sigma_px or sigma_px <= 0:
        return 1.0
    radius = math.ceil(_KERNEL_TRUNCATE * sigma_px)
    size = 2 * radius + 1
    impulse = np.zeros((size, size))
    impulse[radius, radius] = 1.0
    kernel = gaussian_filter(impulse, sigma_px, truncate=_KERNEL_TRUNCATE, mode="constant")
    return float(math.sqrt(np.sum(kernel**2)))


def _aperture_pattern_factor(sigma_px: float | None) -> float:
    """Give the standard deviation of a circle's sum of blurred white noise.

    Parameters
    ----------
    sigma_px : `float` or `None`
        The blur width. `None` or zero means no blur.

    Returns
    -------
    factor : `float`
        The standard deviation of the sum, over the circle of radius
        `APERTURE_RADIUS_PX` (pixels weighted by their overlap), of white noise
        of unit standard deviation after the blur. The circle's weights are
        blurred with the same kernel, and the squares of the result are summed.
        It is averaged over places of the circle's centre inside a pixel.
    """
    blur = sigma_px if sigma_px and sigma_px > 0 else 0.0
    margin = math.ceil(_KERNEL_TRUNCATE * blur) + math.ceil(APERTURE_RADIUS_PX) + 2
    size = 2 * margin + 1
    values = []
    for column_step in range(_SUBPIXEL_PLACES):
        for row_step in range(_SUBPIXEL_PLACES):
            x = margin + (column_step + 0.5) / _SUBPIXEL_PLACES - 0.5
            y = margin + (row_step + 0.5) / _SUBPIXEL_PLACES - 0.5
            weights = np.zeros((size, size))
            mask = CircularAperture((x, y), r=APERTURE_RADIUS_PX).to_mask(method="exact")
            box = np.asarray(mask.data)
            x0, y0 = mask.bbox.ixmin, mask.bbox.iymin
            weights[y0 : y0 + box.shape[0], x0 : x0 + box.shape[1]] = box
            if blur:
                weights = gaussian_filter(weights, blur, truncate=_KERNEL_TRUNCATE, mode="constant")
            values.append(math.sqrt(float(np.sum(weights**2))))
    return float(np.mean(values))


def flat_noise_contribution(
    noise_before_smoothing_fraction: float | None,
    smoothing_sigma_px: float | None,
    sky_level: float | None,
) -> FlatNoiseOutcome:
    """Work out the noise the master flat adds to an aperture of a stack.

    Parameters
    ----------
    noise_before_smoothing_fraction : `float` or `None`
        The relative noise of the averaged flat frames before any blur (the
        stack summary's ``flat_noise_fraction``). `None` when the stack used no
        flats.
    smoothing_sigma_px : `float` or `None`
        The Gaussian width that was applied (the stack summary's
        ``flat_smoothing_sigma_px``). `None` or zero when the flat was left as
        it was. A width that is not a finite number gives no result.
    sky_level : `float` or `None`
        The stack's signal level in the stack's units (see
        `stack_noise.sky_level_of_plane`).

    Returns
    -------
    outcome : `FlatNoiseOutcome`
        The master's noise after the blur and the noise it adds to an
        aperture sum, or the reason there is none.
    """
    if noise_before_smoothing_fraction is None:
        return FlatNoiseOutcome(None, "the stack used no flat frames, or their noise was not measured")
    if not math.isfinite(noise_before_smoothing_fraction) or noise_before_smoothing_fraction < 0:
        return FlatNoiseOutcome(None, "the master flat's noise is not a usable number")
    if smoothing_sigma_px is not None and not math.isfinite(smoothing_sigma_px):
        return FlatNoiseOutcome(None, "the master flat's blur width is not a usable number")
    if sky_level is None or not math.isfinite(sky_level) or not sky_level > 0:
        return FlatNoiseOutcome(None, "the stack's signal level could not be read")
    after = noise_before_smoothing_fraction * smoothed_noise_factor(smoothing_sigma_px)
    aperture_noise = (
        sky_level * noise_before_smoothing_fraction * _aperture_pattern_factor(smoothing_sigma_px)
    )
    return FlatNoiseOutcome(FlatNoiseContribution(after, aperture_noise, sky_level))
