"""Tones down the stars in a stretched preview picture.

A stretch that lifts the faint sky and nebulae also lifts every star. The
result is a field of small, hard, white dots that all look about equally
bright, even though real stars differ in brightness by factors of thousands.
This module restores some of that range in the stretched picture with two
curves.

1. *Star dimming.* The stars are the light that sits well above the local
   background. The module separates that light from the background, raises it
   to a power, and adds it back. A power above 1 dims fainter stars more than
   brighter ones, so bright stars stand out again. Only light that clearly
   stands out is dimmed. Low-contrast structure, such as the knots along a
   galaxy's spiral arms, is left as it was. Nebulae and the sky are part of
   the background, so they are not touched.
2. *Soft highlights.* Above a knee, values are compressed with a smooth curve
   instead of being cut off at white. A saturated star core then ends a little
   below pure white, so it no longer looks like a flat disc.

Both curves act on the stretched picture only. The stack itself is never
changed.

A colour picture is toned on its brightness (the mean of its three channels),
and every channel of a pixel is scaled by the same factor. Toning each
channel on its own would not keep the colour: the star finder works from the
noise of one channel, and the channels differ in noise, so each would be
dimmed by a different amount and the sky would change colour from one part of
the frame to another.
"""

import logging

import numpy as np
from scipy import ndimage

from astrometricslib._internal.drivers.fits_access import read_data, read_header, write_image
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import robust_sigma

logger = logging.getLogger(__name__)

# Stretched values (0 to 1) at or below this are left alone by the soft
# highlight curve. Above it, values are compressed so that 1.0 comes out at
# about 0.92 (0.65 + 0.35 * tanh(1)). Chosen by eye on the Bubble Nebula
# stack: a knee of 0.5 capped the brightest stars at 0.88, and the user
# preferred the 0.65 version. No pixel of that picture stayed above 0.95.
# Not validated on other targets.
HIGHLIGHT_KNEE = TunedParameter(
    name="stacking.highlight_knee",
    default=0.65,
    unit="fraction",
    description="Stretched value at or below which the soft highlight curve leaves values alone.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-32",
)

# The power the star light is raised to. 1.0 changes nothing. Chosen by eye on
# the Bubble Nebula stack: the user compared 1.4 and 1.8 and picked 1.8, which
# leaves a few clearly brighter stars over a haze of faint ones. Not
# validated on other targets.
STAR_DIMMING_POWER = TunedParameter(
    name="stacking.star_dimming_power",
    default=1.8,
    unit="power",
    description="Power to which the star light is raised.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-32",
)

# Star light (stretched units above the local background) below the first
# value is left alone, light above the second is fully dimmed, and the weight
# rises smoothly between them. Without this limit, every small bright feature
# counted as a star: on the M 81 stack the spiral-arm knots, which reach only
# 0.02 to 0.08 above the background, were flattened, and the diffraction
# spikes of M 45 were removed. Stars of the Bubble Nebula stack that reach
# more than 0.12 above the background were dimmed as the user chose. Both
# limits were set by eye on the Bubble Nebula, M 81, M 45 and NGC 2244 stacks.
STAR_DIMMING_STARTS_AT = TunedParameter(
    name="stacking.star_dimming_starts_at",
    default=0.05,
    unit="fraction",
    description="Star light below which stars are not dimmed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-32",
)
STAR_DIMMING_FULL_AT = TunedParameter(
    name="stacking.star_dimming_full_at",
    default=0.12,
    unit="fraction",
    description="Star light above which stars are fully dimmed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-32",
)

# The star light that counts is also kept above this many sky-noise widths.
# The background under the stars is the local minimum, which lies 2 to 3 noise
# widths below the sky, so sky noise alone reaches 5.5 widths above it (the
# 99.99th percentile of sky pixels on M 51, M 106, M 81 and M 101). On every
# stack measured, the noise is about 0.055 in stretched units, above the 0.05
# start limit, so about 90 percent of the sky was counted as star light: the
# toning halved the sky level and smeared the arms of M 81. Six widths clears
# the noise. Where the noise is small, the 0.05 and 0.12 limits above apply
# unchanged. The factor is a design choice from those measurements.
NOISE_WIDTHS_ABOVE_THE_BACKGROUND = TunedParameter(
    name="stacking.noise_widths_above_the_background",
    default=6.0,
    unit="sigma",
    description="Sky-noise widths above the background the star light must also stay.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-32",
)

# The noise is measured on the picture minus a blurred copy of it, so the
# large-scale structure of a galaxy or nebula does not count as noise. The
# blur width, in pixels, is larger than a star and smaller than the structure.
# On the reference setup this stands for 3.0 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
_NOISE_BLUR_PIXELS = TunedParameter(
    name="stacking.noise_blur_pixels",
    default=factor_for(3.0, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=3.0,
    unit="× star width",
    description="Width of the blur taken away before the noise is measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D55",
)

# The background under the stars is found with a grey-scale opening (erode,
# then dilate) over a square of this many pixels on a side. The opening
# removes bright features smaller than the square and keeps larger ones. Stars
# are a few pixels wide on the ASI533MM Pro stacks (1.9 arcseconds per pixel),
# and nebulae are tens to hundreds of pixels wide. The size was set by eye on
# the Bubble Nebula stack, where the Bubble stayed out of the star light, and
# has not been tuned further.
# On the reference setup this stands for 15 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
_BACKGROUND_BOX_PIXELS = TunedParameter(
    name="stacking.background_box_pixels",
    default=factor_for(15, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=15,
    unit="× star width",
    description="Side of the square opening that finds the background under the stars.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D55",
)

# The opening leaves a faint blocky edge. A Gaussian blur of this many pixels
# hides it. It is small next to the box, so real structure is kept.
_BACKGROUND_SMOOTHING_PIXELS = TunedParameter(
    name="stacking.background_smoothing_pixels",
    default=2.0,
    unit="pixels",
    description="Width of the smoothing that hides the blocky edge of the opening.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D55",
)

# The smallest room left above the background, so a bright background never
# divides by zero.
_MINIMUM_ROOM = 1e-6


def soft_highlights(image: np.ndarray, knee: float | None = None) -> np.ndarray:
    """Compress the brightest values with a smooth curve.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched picture with values between 0 and 1.
    knee : `float`, optional
        The value above which compression starts, strictly between 0 and 1.
        If `None`, the value of `HIGHLIGHT_KNEE` is used.

    Returns
    -------
    toned : `numpy.ndarray`
        The picture with values at or below `knee` unchanged and values above
        it compressed smoothly toward, but never reaching, 1.
    """
    if knee is None:
        knee = resolve(HIGHLIGHT_KNEE).value
    return np.where(image <= knee, image, knee + (1.0 - knee) * np.tanh((image - knee) / (1.0 - knee)))


def estimate_noise(image: np.ndarray) -> float:
    """Measure the pixel-to-pixel noise of a stretched picture.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D stretched picture.

    Returns
    -------
    noise : `float`
        The standard deviation the noise would have if it were Gaussian,
        found from the median absolute deviation of the picture minus a
        blurred copy. Smooth structure and the few stars barely change it.
    """
    detail = image - ndimage.gaussian_filter(image, resolve(_NOISE_BLUR_PIXELS).value)
    return float(robust_sigma(detail))


def dim_stars(image: np.ndarray, power: float | None = None) -> np.ndarray:
    """Dim the stars in a stretched picture, faint ones most.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D stretched picture with values between 0 and 1.
    power : `float`, optional
        The power the star light is raised to. 1.0 changes nothing.
        If `None`, the value of `STAR_DIMMING_POWER` is used.

    Returns
    -------
    toned : `numpy.ndarray`
        The picture with the background unchanged. Light more than
        `STAR_DIMMING_FULL_AT` above the background is scaled by the fraction
        of the available room it fills, raised to `power`. Light less than
        `STAR_DIMMING_STARTS_AT` above it is unchanged, and light between the
        two is a smooth mix of both. Both limits move up together, keeping
        their distance, so that the start is at least
        `NOISE_WIDTHS_ABOVE_THE_BACKGROUND` noise widths: sky noise is never
        taken for a star.
    """
    if power is None:
        power = resolve(STAR_DIMMING_POWER).value
    star_dimming_starts_at = resolve(STAR_DIMMING_STARTS_AT).value
    background_box_pixels = resolve(_BACKGROUND_BOX_PIXELS).value
    background = ndimage.gaussian_filter(
        ndimage.grey_opening(image, size=(background_box_pixels, background_box_pixels)),
        resolve(_BACKGROUND_SMOOTHING_PIXELS).value,
    )
    star_light = np.clip(image - background, 0.0, None)
    room = np.clip(1.0 - background, _MINIMUM_ROOM, None)
    starts_at = max(
        star_dimming_starts_at, resolve(NOISE_WIDTHS_ABOVE_THE_BACKGROUND).value * estimate_noise(image)
    )
    full_at = starts_at + (resolve(STAR_DIMMING_FULL_AT).value - star_dimming_starts_at)
    ramp = np.clip((star_light - starts_at) / (full_at - starts_at), 0.0, 1.0)
    weight = ramp * ramp * (3.0 - 2.0 * ramp)
    dimmed = room * (star_light / room) ** power
    return background + (1.0 - weight) * star_light + weight * dimmed


def tone_stars(image: np.ndarray) -> np.ndarray:
    """Dim the stars and soften the highlights of a stretched picture.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched picture with values between 0 and 1. A colour picture
        (channels first) is toned on its brightness, the mean of its
        channels, and each pixel's channels are scaled by the same factor, so
        the toning cannot change a pixel's colour.

    Returns
    -------
    toned : `numpy.ndarray`
        The toned picture, the same shape, as 32-bit floats between 0 and 1.
    """
    clipped = np.clip(np.asarray(image, dtype=np.float64), 0.0, 1.0)
    if clipped.ndim == 3:
        brightness = clipped.mean(axis=0)
        toned_brightness = soft_highlights(dim_stars(brightness))
        factor = np.divide(
            toned_brightness, brightness, out=np.ones_like(brightness), where=brightness > _MINIMUM_ROOM
        )
        return np.clip(clipped * factor, 0.0, 1.0).astype(np.float32)
    return soft_highlights(dim_stars(clipped)).astype(np.float32)


def tone_stars_in_file(input_path: str, output_path: str) -> bool:
    """Tone the stars of a stretched FITS picture and save the result.

    Parameters
    ----------
    input_path : `str`
        Path of the stretched picture, with values between 0 and 1.
    output_path : `str`
        Path to write. The header of the input is kept.

    Returns
    -------
    succeeded : `bool`
        `True` if the toned picture was written, `False` if the input could
        not be read or was not a 2-D or 3-D image. The reason is logged.
    """
    try:
        data = read_data(input_path)
        header = read_header(input_path)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("Could not read '%s' to tone its stars: %s.", input_path, error)
        return False
    if data is None or data.ndim not in (2, 3):
        logger.warning("Could not tone the stars of '%s': it is not an image.", input_path)
        return False
    write_image(output_path, tone_stars(data), header)
    return True
