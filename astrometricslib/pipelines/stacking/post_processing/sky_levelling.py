"""Levels the empty sky of a stretched color preview, so it is neutral.

The stretch puts its black point about three noise widths below the sky, so
it multiplies every small difference between the sky levels of the red, green
and blue channels. The white-balance steps on the linear data (see
`sun_like_balance`) leave differences of a tenth of a noise width. After the
stretch that is a visible tint: on the NGC 6992 stack the stretched sky read
red 27, green 30, blue 29 of 255, a green-cyan cast.

This step runs on the stretched picture, after the denoise and before the
star toning. It measures each channel's empty-sky level with the library's
sky measurement (`measure_sky_background`), which ignores stars and clips out
what stands above the sky. The measurement is made only on the darkest half
of the picture (by the mean of the three channels). On a frame covered by
nebula the red channel has more contrast than green, so over the whole frame
the three skies agree (NGC 6992: 33.3, 33.6, 33.3 of 255), but in the darkest
part, which the eye reads as the sky, red lies 3 to 5 levels lower. The step
adds one offset to the red channel and one to the blue channel so their
sky equals the green channel's. Green is the reference because it has the
most pixels on a color sensor and the linear white-balance check also keeps
green's sky. Only offsets are added, never factors: a factor would change the
colors of the stars and the nebulae, and an offset moves only the level. The
result is clipped to the range 0 to 1. Pixels that are zero in all three
channels (the empty border of a registered stack) are left at zero.

If a needed offset is larger than `SKY_LEVELLING_MAXIMUM_OFFSET`, something
other than a small residual is wrong (a colored nebula filling the frame, for
example), so nothing is changed and the reason is recorded. If the sky cannot
be measured, the step is skipped with a note.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import measure_sky_background

logger = logging.getLogger(__name__)

__all__ = [
    "SKY_LEVELLING_DARKEST_SHARE",
    "SKY_LEVELLING_MAXIMUM_OFFSET",
    "SkyLevelling",
    "level_sky_channels",
    "level_sky_in_file",
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

# The share of the picture, darkest first, on which the sky is measured. The
# darkest half holds the empty sky and leaves out the bright nebula. On the
# NGC 6992 preview the red-to-green gap of the median grew from 0.3 levels
# (all pixels) to 2.8 (darkest 60%) and 5.6 (darkest 30%). A half is a
# compromise: enough pixels for a steady level, dark enough to be sky.
SKY_LEVELLING_DARKEST_SHARE = TunedParameter(
    name="stacking.sky_levelling_darkest_share",
    default=0.5,
    unit="fraction",
    description="Share of the picture, darkest first, on which the sky levelling measures the sky.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D86",
)


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
    """

    applied: bool
    reason: str = ""
    sky_before: tuple[float, ...] = ()
    sky_after: tuple[float, ...] = ()
    red_offset: float = 0.0
    blue_offset: float = 0.0

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
        return f"Sky levelling: red {self.red_offset:+.4f}, blue {self.blue_offset:+.4f}"


def _measure_sky_levels(image: np.ndarray) -> tuple[float, ...] | None:
    """Measure the empty-sky level of each channel, on the darkest part.

    Parameters
    ----------
    image : `numpy.ndarray`
        A color image, channels first, shape (3, height, width).

    Returns
    -------
    levels : `tuple` [`float`, ...] or `None`
        The red, green and blue levels, or `None` when one cannot be measured.
    """
    brightness = np.mean(image, axis=0)
    valid = brightness > 0.0
    if not np.any(valid):
        return None
    share = float(resolve(SKY_LEVELLING_DARKEST_SHARE).value)
    limit = np.quantile(brightness[valid][:: max(1, int(valid.sum() // 2_000_000))], share)
    darkest = valid & (brightness <= limit)
    levels = []
    for channel in image:
        measured = measure_sky_background(np.where(darkest, channel, np.nan).astype(np.float32))
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
    before = _measure_sky_levels(image)
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
        )
    leveled = np.array(image, dtype=np.float32)
    border = np.max(image, axis=0) <= 0.0
    for index, offset in ((0, red_offset), (2, blue_offset)):
        shifted = np.clip(leveled[index] + np.float32(offset), 0.0, 1.0)
        leveled[index] = np.where(border, leveled[index], shifted)
    after = _measure_sky_levels(leveled)
    return leveled, SkyLevelling(
        applied=True,
        sky_before=before,
        sky_after=after if after is not None else (),
        red_offset=float(red_offset),
        blue_offset=float(blue_offset),
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
