"""Finds stacks of bright extended objects and chooses their stretch.

The normal preview stretch is made for faint things on a dark sky. It sets
the black point from the sky's noise and lifts the sky to a grey level. That
fails for the Moon, a planet or the Sun. The Moon's sky is about 3000 times
fainter than its disc, so any sky level that is visible at all pushes the
whole disc to white and its craters and maria are lost.

This module has two jobs.

1. Recognise the case from the data. It finds what share of the picture the
   normal stretch would push to white. A star field, nebula or galaxy leaves
   about 0.1% of pixels white (the cores of the brightest stars). The Moon
   leaves 4%. A share of 1% or more means a large bright object.
2. Choose a different stretch for it. The black point is the sky level, the
   white point is near the top of the object's own brightness, and the curve
   places the object's typical brightness at a fixed grey. The sky stays
   near black and the object's detail is spread across the whole range.

The preview step skips GraXpert and the denoise for such a stack. GraXpert
removes sky gradients and would treat the Moon's glow as background. The
denoise is tuned for sky grain and could smooth the surface.
"""

import logging
from dataclasses import dataclass

import numpy as np

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.image_scaling import (
    measure_sky_background,
    midtones_balance_for,
    white_fraction_after_autostretch,
)
from astrometricslib.pipelines.stacking.post_processing.sky_level import choose_sky_level

logger = logging.getLogger(__name__)

# The share of pixels the normal stretch may push to white before the stack
# is treated as a bright object. Measured on 38 stacks: the normal stretch
# whites out 4.4% of the Moon, and at most 0.12% (M 45) of every nebula,
# galaxy and star field. 1% sits eight times above the others and four times
# below the Moon. Validated on the Moon only. A planet is too small to reach
# it (a disc a few dozen pixels wide covers well under 0.01% of the frame),
# so planet stacks are not caught by this rule.
SATURATED_FRACTION_LIMIT = TunedParameter(
    name="stacking.saturated_fraction_limit",
    default=0.01,
    unit="fraction",
    description="Share of pixels the normal stretch may whiten before a stack counts as a bright object.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-33",
)

# Pixels brighter than this fraction of the way from the sky to the peak count
# as the bright object when the white point is chosen. A quarter keeps the
# lunar disc, with its dark maria, and leaves out the glow around it.
BRIGHT_PIXEL_FRACTION_OF_PEAK = TunedParameter(
    name="stacking.bright_pixel_fraction_of_peak",
    default=0.25,
    unit="fraction",
    description="Fraction of the way from sky to peak above which pixels count as the bright object.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D59",
)

# The white point is this percentile of the bright object's pixels. A
# percentile near the top ignores a few extreme pixels (a bright limb, a
# hot pixel) and still reaches the brightest real surface. Chosen by eye on
# the Moon stack.
WHITE_POINT_PERCENTILE = TunedParameter(
    name="stacking.white_point_percentile",
    default=99.8,
    unit="percentile",
    description="Percentile of the bright object's pixels that sets the white point.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-33",
)

# The display brightness the bright object's median is placed at, as a
# fraction of full brightness. Chosen by eye on the Moon stack, where it
# kept both the dark maria and the bright rays visible. The only tuned
# number besides the white-point percentile.
BRIGHT_OBJECT_BRIGHTNESS = TunedParameter(
    name="stacking.bright_object_brightness",
    default=0.55,
    unit="fraction",
    description="Display brightness at which the bright object's median is placed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-33",
)

# The smallest number of bright pixels needed to measure the object. Fewer
# than this are a few stars, not an extended object.
_MINIMUM_BRIGHT_PIXELS = TunedParameter(
    name="stacking.minimum_bright_pixels",
    default=100,
    unit="pixels",
    description="Fewest bright pixels needed to treat them as an extended object.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D59",
)

# Every n-th pixel in each direction is used to measure the object's
# brightness, as the measurement of a whole disc does not need every pixel.
_SAMPLE_STRIDE = 2


@dataclass(frozen=True)
class BrightObjectStretch:
    """The stretch chosen for a stack of a bright extended object.

    All brightness values are for the image after it has been multiplied by
    `scale`, so they are between 0 and 1 even if the stack's own values go
    above 1.

    Attributes
    ----------
    scale : `float`
        The factor to multiply the image by first. 1.0 unless the white
        point is above 1, then the inverse of it.
    black_point : `float`
        The brightness that comes out black.
    midtones : `float`
        The midtones balance, between 0 and 1.
    white_point : `float`
        The brightness that comes out white.
    white_fraction : `float`
        The share of pixels the normal stretch would have pushed to white,
        for the log.
    """

    scale: float
    black_point: float
    midtones: float
    white_point: float
    white_fraction: float


def choose_bright_object_stretch(data: np.ndarray) -> BrightObjectStretch | None:
    """Decide whether a stack shows a bright object, and choose its stretch.

    Parameters
    ----------
    data : `numpy.ndarray`
        The stack, in linear units. A colour image is averaged across its
        channels first.

    Returns
    -------
    stretch : `BrightObjectStretch` or `None`
        The stretch, or `None` if the normal stretch should be used: the sky
        cannot be measured, the normal stretch would not white out a large
        share of the image, or the bright object cannot be measured.
    """
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=0 if arr.shape[0] in (1, 3, 4) else 2)
    if arr.ndim != 2:
        return None
    sky = measure_sky_background(arr)
    if sky is None:
        return None
    median = sky[0]
    peak = float(np.nanmax(arr))
    white_fraction = white_fraction_after_autostretch(arr, choose_sky_level(arr).sky_level)
    if white_fraction is None or white_fraction < resolve(SATURATED_FRACTION_LIMIT).value:
        return None
    sample = arr[::_SAMPLE_STRIDE, ::_SAMPLE_STRIDE]
    bright = sample[
        np.isfinite(sample)
        & (sample > median + resolve(BRIGHT_PIXEL_FRACTION_OF_PEAK).value * (peak - median))
    ]
    if bright.size < resolve(_MINIMUM_BRIGHT_PIXELS).value:
        return None
    white_point = float(np.percentile(bright, resolve(WHITE_POINT_PERCENTILE).value))
    if not white_point > median:
        return None
    typical = (float(np.median(bright)) - median) / (white_point - median)
    midtones = midtones_balance_for(min(0.99, max(0.01, typical)), resolve(BRIGHT_OBJECT_BRIGHTNESS).value)
    scale = 1.0 / white_point if white_point > 1.0 else 1.0
    return BrightObjectStretch(
        scale=scale,
        black_point=median * scale,
        midtones=midtones,
        white_point=white_point * scale,
        white_fraction=white_fraction,
    )


def choose_bright_object_stretch_for_file(path: str) -> BrightObjectStretch | None:
    """Decide whether a FITS stack on disk shows a bright object.

    Parameters
    ----------
    path : `str`
        Path of the FITS image the stretch would be applied to.

    Returns
    -------
    stretch : `BrightObjectStretch` or `None`
        The stretch, or `None` if the normal stretch should be used. A file
        that cannot be read gives `None`, with the reason logged at debug
        level.
    """
    try:
        data = AstrometricsImage(path).data
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read '%s' to look for a bright object: %s", path, error)
        return None
    return choose_bright_object_stretch(data)
