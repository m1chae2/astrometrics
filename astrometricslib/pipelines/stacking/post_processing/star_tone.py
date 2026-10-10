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
"""

import logging

import numpy as np
from scipy import ndimage

from astrometricslib.drivers.fits_access import read_data, read_header, write_image

logger = logging.getLogger(__name__)

# Stretched values (0 to 1) at or below this are left alone by the soft
# highlight curve. Above it, values are compressed so that 1.0 comes out at
# about 0.92 (0.65 + 0.35 * tanh(1)). Chosen by eye on the Bubble Nebula
# stack: a knee of 0.5 capped the brightest stars at 0.88, and the user
# preferred the 0.65 version. No pixel of that picture stayed above 0.95.
# Not validated on other targets.
HIGHLIGHT_KNEE = 0.65

# The power the star light is raised to. 1.0 changes nothing. Chosen by eye on
# the Bubble Nebula stack: the user compared 1.4 and 1.8 and picked 1.8, which
# leaves a few clearly brighter stars over a haze of faint ones. Not
# validated on other targets.
STAR_DIMMING_POWER = 1.8

# Star light (stretched units above the local background) below the first
# value is left alone, light above the second is fully dimmed, and the weight
# rises smoothly between them. Without this limit, every small bright feature
# counted as a star: on the M 81 stack the spiral-arm knots, which reach only
# 0.02 to 0.08 above the background, were flattened, and the diffraction
# spikes of M 45 were removed. Stars of the Bubble Nebula stack that reach
# more than 0.12 above the background were dimmed as the user chose. Both
# limits were set by eye on the Bubble Nebula, M 81, M 45 and NGC 2244 stacks.
STAR_DIMMING_STARTS_AT = 0.05
STAR_DIMMING_FULL_AT = 0.12

# The background under the stars is found with a grey-scale opening (erode,
# then dilate) over a square of this many pixels on a side. The opening
# removes bright features smaller than the square and keeps larger ones. Stars
# are a few pixels wide on the ASI533MM Pro stacks (1.9 arcseconds per pixel),
# and nebulae are tens to hundreds of pixels wide. The size was set by eye on
# the Bubble Nebula stack, where the Bubble stayed out of the star light, and
# has not been tuned further.
_BACKGROUND_BOX_PIXELS = 15

# The opening leaves a faint blocky edge. A Gaussian blur of this many pixels
# hides it. It is small next to the box, so real structure is kept.
_BACKGROUND_SMOOTHING_PIXELS = 2.0

# The smallest room left above the background, so a bright background never
# divides by zero.
_MINIMUM_ROOM = 1e-6


def soft_highlights(image: np.ndarray, knee: float = HIGHLIGHT_KNEE) -> np.ndarray:
    """Compress the brightest values with a smooth curve.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched picture with values between 0 and 1.
    knee : `float`, optional
        The value above which compression starts, strictly between 0 and 1.

    Returns
    -------
    toned : `numpy.ndarray`
        The picture with values at or below `knee` unchanged and values above
        it compressed smoothly toward, but never reaching, 1.
    """
    return np.where(image <= knee, image, knee + (1.0 - knee) * np.tanh((image - knee) / (1.0 - knee)))


def dim_stars(image: np.ndarray, power: float = STAR_DIMMING_POWER) -> np.ndarray:
    """Dim the stars in a stretched picture, faint ones most.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D stretched picture with values between 0 and 1.
    power : `float`, optional
        The power the star light is raised to. 1.0 changes nothing.

    Returns
    -------
    toned : `numpy.ndarray`
        The picture with the background unchanged. Light more than
        `STAR_DIMMING_FULL_AT` above the background is scaled by the fraction
        of the available room it fills, raised to `power`. Light less than
        `STAR_DIMMING_STARTS_AT` above it is unchanged, and light between the
        two is a smooth mix of both.
    """
    background = ndimage.gaussian_filter(
        ndimage.grey_opening(image, size=(_BACKGROUND_BOX_PIXELS, _BACKGROUND_BOX_PIXELS)),
        _BACKGROUND_SMOOTHING_PIXELS,
    )
    star_light = np.clip(image - background, 0.0, None)
    room = np.clip(1.0 - background, _MINIMUM_ROOM, None)
    ramp = np.clip(
        (star_light - STAR_DIMMING_STARTS_AT) / (STAR_DIMMING_FULL_AT - STAR_DIMMING_STARTS_AT), 0.0, 1.0
    )
    weight = ramp * ramp * (3.0 - 2.0 * ramp)
    dimmed = room * (star_light / room) ** power
    return background + (1.0 - weight) * star_light + weight * dimmed


def tone_stars(image: np.ndarray) -> np.ndarray:
    """Dim the stars and soften the highlights of a stretched picture.

    Parameters
    ----------
    image : `numpy.ndarray`
        A stretched picture with values between 0 and 1. A colour picture
        (channels first) is toned one channel at a time.

    Returns
    -------
    toned : `numpy.ndarray`
        The toned picture, the same shape, as 32-bit floats between 0 and 1.
    """
    clipped = np.clip(np.asarray(image, dtype=np.float64), 0.0, 1.0)
    if clipped.ndim == 3:
        planes = [soft_highlights(dim_stars(plane)) for plane in clipped]
        return np.stack(planes).astype(np.float32)
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
