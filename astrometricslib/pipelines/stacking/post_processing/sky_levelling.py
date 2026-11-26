"""Levels the empty sky of a stretched color preview, so it is neutral.

The stretch puts its black point about three noise widths below the sky, so
it multiplies every small difference between the sky levels of the red, green
and blue channels. The white-balance steps on the linear data (see
`sun_like_balance`) leave differences of a tenth of a noise width. After the
stretch that is a visible tint: on the NGC 6992 stack the stretched sky read
red 27, green 30, blue 29 of 255, a green-cyan cast.

This step runs on the stretched picture, after the denoise and before the
star toning. It measures each channel's empty-sky level with the library's
sky measurement (`measure_sky_background`), which clips out what stands above
the sky, on true sky only (see `SKY_LEVELLING_SKY_SAMPLE`):

- away from every star, because the stars' colored skirts are not sky. The
  first version measured on the darkest half of the picture; on the Nikon
  300 mm pictures that half held the red skirts of the faint stars, and every
  one of them came out with a red sky (red over green by 1 to 4 levels of 255
  in the darkest half).
- near the sky level of the picture's smoothed brightness (`sky_weight`),
  because a nebula or galaxy is not sky either. On a frame covered by nebula
  the red channel has more contrast than green, so over the whole frame the
  three skies agree (NGC 6992: 33.3, 33.6, 33.3 of 255), but in the dark sky
  red lies 3 to 5 levels lower.

The step adds one offset to the red channel and one to the blue channel so
their sky equals the green channel's. Green is the reference because it has the
most pixels on a color sensor and the linear white-balance check also keeps
green's sky. Only offsets are added, never factors: a factor would change the
colors of the stars and the nebulae, and an offset moves only the level. The
result is clipped to the range 0 to 1. Pixels that are zero in all three
channels (the empty border of a registered stack) are left at zero.

If a needed offset is larger than `SKY_LEVELLING_MAXIMUM_OFFSET`, something
other than a small residual is wrong (a colored nebula filling the frame, for
example), so nothing is changed and the reason is recorded. If the sky cannot
be measured, or too little true sky is found, the step is skipped with a
note.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy import ndimage

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background
from astrometricslib.utilities.robust_statistics import MAD_TO_SIGMA, robust_sigma

logger = logging.getLogger(__name__)

__all__ = [
    "SKY_LEVELLING_MAXIMUM_OFFSET",
    "SKY_LEVELLING_SKY_SAMPLE",
    "SkyLevelling",
    "level_sky_channels",
    "level_sky_in_file",
    "sky_weight",
    "true_sky",
]

# The largest offset, in stretched units (0 to 1), that is added to a channel.
# The tints seen on the stretched Nikon previews were 2 to 4 levels of 255
# (0.008 to 0.016; NGC 6992, STK-D86). 0.05 is 13 levels, three times the
# largest of them: a bigger difference between the channels' skies is more
# likely real color (a nebula that fills the frame) than a leftover of the
# calibration, so it is left alone.
SKY_LEVELLING_MAXIMUM_OFFSET = TunedParameter(
    name="stacking.sky_levelling_maximum_offset",
    default=0.05,
    unit="stretched units (0 to 1)",
    description="Largest offset the sky levelling of a stretched color preview adds to a channel.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)

# How the true sky is found on the stretched picture: how far a pixel must be
# from any star's footprint (pixels), how close the smoothed brightness must
# be to its sky level (in sky spreads, see `sky_weight`), and the width of
# the smoothing (pixels). The red skirt of a 300 mm Nikon star reaches about
# 11 px from its center (M 45, 2026-11-25), a few pixels past the footprint
# found at three noise widths, so 12 px clears it with a margin. Two spreads
# and 6 px are the values the color picture of a monochrome stack uses for
# its own sky sample (`stacking.color_preview_sky_noise_multiple` and the
# last number of `stacking.color_preview_color_fade`, STK-62), where they
# kept the faint disk of M 31 out of the sky.
SKY_LEVELLING_SKY_SAMPLE = TunedParameter(
    name="stacking.sky_levelling_sky_sample",
    default=(12.0, 2.0, 6.0),
    unit="pixels, sky spreads, pixels",
    description=(
        "Distance from the stars, closeness to the sky level and smoothing width of the true-sky "
        "sample of the sky levelling."
    ),
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)

# A pixel belongs to a star's footprint when the brightness stands this many
# noise widths above its smoothed surroundings. Three widths is the usual
# detection level; a little nebula texture is taken as stars too, which only
# leaves it out of the sky. Not tuned.
_FOOTPRINT_NOISE_WIDTHS = 3.0

# The fewest true-sky pixels needed, as a share of the picture; with fewer,
# the step is skipped. A sanity guard, not tuning.
_MINIMUM_SKY_SHARE = 0.005

# The number of histogram bins between the 1st and 60th percentiles used to
# find the most common (sky) value. Plumbing of a mode estimate, not tuned.
_SKY_HISTOGRAM_BINS = 200


@dataclass(frozen=True)
class SkyLevelling:
    """What the sky levelling found and did.

    Attributes
    ----------
    applied : `bool`
        Whether the offsets were added.
    reason : `str`
        Why nothing was changed, or an empty string.
    sky_before : `tuple` [`float`, `float`, `float`]
        The red, green and blue empty-sky levels before, or empty when the
        sky could not be measured.
    sky_after : `tuple` [`float`, `float`, `float`]
        The same levels after the offsets, or empty when none were added.
    red_offset : `float`
        The offset added to the red channel (0 when not applied).
    blue_offset : `float`
        The offset added to the blue channel (0 when not applied).
    sky_sample : `str`
        Where the sky was measured: away from the stars, or, in a field too
        crowded for that, wherever the picture is near its sky level.
    """

    applied: bool
    reason: str = ""
    sky_before: tuple[float, ...] = ()
    sky_after: tuple[float, ...] = ()
    red_offset: float = 0.0
    blue_offset: float = 0.0
    sky_sample: str = ""

    def record(self) -> dict[str, Any]:
        """Give the result for the preview settings record.

        Returns
        -------
        record : `dict`
            Plain numbers and text for the JSON file.
        """
        return {
            "applied": self.applied,
            "reason": self.reason,
            "sky_before": list(self.sky_before),
            "sky_after": list(self.sky_after),
            "red_offset": self.red_offset,
            "blue_offset": self.blue_offset,
            "sky_sample": self.sky_sample,
        }

    def step_sentence(self) -> str:
        """Say in one sentence what the step did, for the list of steps.

        Returns
        -------
        sentence : `str`
            For example ``"Sky levelling: red +0.012, blue +0.004"``, or the
            reason when nothing was changed.
        """
        if not self.applied:
            return f"Sky levelling not applied ({self.reason})"
        return (
            f"Sky levelling: red {self.red_offset:+.4f}, blue {self.blue_offset:+.4f}"
            f" (sky measured {self.sky_sample})"
        )


def sky_weight(blurred: np.ndarray, sample: np.ndarray, multiple: float) -> np.ndarray:
    """Say how much each pixel of a blurred picture looks like sky.

    The sky level is the most common value of the sample (the peak of its
    histogram between its 1st and 60th percentiles). The sky spread is the
    robust spread of the sample's values below that level, mirrored, so the
    light of a nebula or galaxy above the sky does not widen it.

    Parameters
    ----------
    blurred : `numpy.ndarray`
        The blurred picture.
    sample : `numpy.ndarray`
        The pixels to measure the sky on.
    multiple : `float`
        How many sky spreads above the sky level still count fully as sky.

    Returns
    -------
    weight : `numpy.ndarray`
        1 up to the sky level plus ``multiple`` spreads, falling evenly to 0
        at twice as many spreads above it.
    """
    values = blurred[sample]
    low, high = np.percentile(values, [1.0, 60.0])
    if not high > low:
        return (blurred <= high).astype(np.float32)
    counts, edges = np.histogram(values, bins=_SKY_HISTOGRAM_BINS, range=(float(low), float(high)))
    peak = int(np.argmax(counts))
    level = 0.5 * (edges[peak] + edges[peak + 1])
    below = level - values[values <= level]
    # The median distance of the lower half from the peak, times MAD_TO_SIGMA,
    # is one standard deviation of the sky; the bright side holds the object.
    spread = max(MAD_TO_SIGMA * float(np.median(below)) if below.size else 0.0, float(edges[1] - edges[0]))
    reach = multiple * spread
    return np.clip((level + 2.0 * reach - blurred) / reach, 0.0, 1.0).astype(np.float32)


def true_sky(image: np.ndarray, clear_of_stars: bool = True) -> np.ndarray:
    """Find the true sky of a stretched color picture.

    A pixel is true sky when it is far enough from every star's footprint
    and the smoothed brightness there is near its sky level (see
    `SKY_LEVELLING_SKY_SAMPLE`). The empty border of a registered stack
    (zero in every channel) is never sky.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched color image, channels first, shape (3, height, width).
    clear_of_stars : `bool`, optional
        Whether the pixels must keep clear of the stars. `False` keeps only
        the closeness to the sky level, for a field too crowded to have sky
        away from every star. Default `True`.

    Returns
    -------
    sky : `numpy.ndarray`
        A true-or-false map, shape (height, width).
    """
    clearance, multiple, blur = (float(value) for value in resolve(SKY_LEVELLING_SKY_SAMPLE).value)
    brightness = np.mean(image, axis=0, dtype=np.float32)
    valid = np.max(image, axis=0) > 0.0
    smooth = ndimage.gaussian_filter(brightness, blur)
    detail = brightness - smooth
    if not np.any(valid):
        return valid
    noise = float(robust_sigma(detail[valid][::7]))
    footprints = valid & (detail > _FOOTPRINT_NOISE_WIDTHS * noise)
    del detail
    if clear_of_stars and np.any(footprints):
        away = ndimage.distance_transform_edt(~footprints) > clearance
    else:
        away = np.ones(footprints.shape, dtype=bool)
    sample = valid & away
    if not np.any(sample):
        return sample
    return sample & (sky_weight(smooth, sample, multiple) >= 1.0)


def _measure_sky_levels(image: np.ndarray, sky: np.ndarray) -> tuple[float, ...] | None:
    """Measure the empty-sky level of each channel, on the true sky.

    Parameters
    ----------
    image : `numpy.ndarray`
        A color image, channels first, shape (3, height, width).
    sky : `numpy.ndarray`
        The true sky, from `true_sky`.

    Returns
    -------
    levels : `tuple` [`float`, ...] or `None`
        The red, green and blue levels, or `None` when one cannot be measured.
    """
    levels = []
    for channel in image:
        measured = measure_sky_background(np.where(sky, channel, np.nan).astype(np.float32))
        if measured is None:
            return None
        levels.append(float(measured[0]))
    return tuple(levels)


def level_sky_channels(image: np.ndarray) -> tuple[np.ndarray | None, SkyLevelling]:
    """Add offsets to the red and blue channels so their sky equals green's.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched color image with values from 0 to 1, channels first,
        shape (3, height, width).

    Returns
    -------
    leveled : `numpy.ndarray` or `None`
        The new image, or `None` when nothing was changed.
    result : `SkyLevelling`
        What was found and done.
    """
    if image.ndim != 3 or image.shape[0] != 3:
        return None, SkyLevelling(applied=False, reason="the image is not a color image")
    sky, sample = true_sky(image), "away from the stars"
    if int(sky.sum()) < _MINIMUM_SKY_SHARE * sky.size:
        # A crowded field: every block is near a star. The level is then
        # taken where the picture is near its sky level, stars or not.
        sky, sample = true_sky(image, clear_of_stars=False), "near the sky level (a crowded field)"
    if int(sky.sum()) < _MINIMUM_SKY_SHARE * sky.size:
        return None, SkyLevelling(applied=False, reason="too little sky was found")
    before = _measure_sky_levels(image, sky)
    if before is None:
        return None, SkyLevelling(applied=False, reason="the sky of a channel could not be measured")
    red_offset = before[1] - before[0]
    blue_offset = before[1] - before[2]
    limit = float(resolve(SKY_LEVELLING_MAXIMUM_OFFSET).value)
    if max(abs(red_offset), abs(blue_offset)) > limit:
        return None, SkyLevelling(
            applied=False,
            reason=(
                f"the channels' skies differ by more than {limit:.2f} "
                f"(red {red_offset:+.3f}, blue {blue_offset:+.3f}), which is more than a small leftover"
            ),
            sky_before=before,
            sky_sample=sample,
        )
    leveled = np.array(image, dtype=np.float32)
    border = np.max(image, axis=0) <= 0.0
    for index, offset in ((0, red_offset), (2, blue_offset)):
        shifted = np.clip(leveled[index] + np.float32(offset), 0.0, 1.0)
        leveled[index] = np.where(border, leveled[index], shifted)
    after = _measure_sky_levels(leveled, sky)
    return leveled, SkyLevelling(
        applied=True,
        sky_before=before,
        sky_after=after if after is not None else (),
        red_offset=float(red_offset),
        blue_offset=float(blue_offset),
        sky_sample=sample,
    )


def level_sky_in_file(input_path: str, output_path: str) -> SkyLevelling:
    """Level the sky of a stretched color FITS picture and save the result.

    Nothing is written unless the offsets are applied. A file that cannot be
    read is not an error: the step is skipped and the result says why.

    Parameters
    ----------
    input_path : `str`
        Path of the stretched picture, with values between 0 and 1.
    output_path : `str`
        Path to write when the offsets are applied. The header of the input
        is kept.

    Returns
    -------
    result : `SkyLevelling`
        What was found and done.
    """
    try:
        data = read_data(input_path)
        header = read_header(input_path)
    except (OSError, ValueError, KeyError) as error:
        return SkyLevelling(applied=False, reason=f"the picture could not be read ({error})")
    if data is None:
        return SkyLevelling(applied=False, reason="the picture has no image data")
    leveled, result = level_sky_channels(np.asarray(data))
    if leveled is not None:
        write_image(output_path, leveled, header)
    logger.info("%s.", result.step_sentence())
    return result
