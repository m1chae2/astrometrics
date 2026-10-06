"""Chooses how bright the sky should be in a stack's preview picture.

A stretched picture needs a decision: how bright should the empty sky be? A
dark sky gives strong contrast, but it also pushes faint structure (the outer
arms of a galaxy, thin nebulosity) down into black. A brighter sky keeps that
faint structure visible, at the cost of a grey, washed-out look. The right
choice depends on the target, so this module makes it from the stack's own
data.

The rule has three steps.

1. Measure the *extended light*: the brightest light that is not a star. The
   stack is cut into small blocks and each block is replaced by its median,
   which removes stars. A second small median filter removes what is left of
   them. The extended light is the 99th percentile of the result.
2. Express it as a signal-to-noise ratio: how far the extended light sits
   above the sky, in units of the sky's noise. A faint galaxy in a shallow
   stack has a ratio near 0.5. A bright nebula in a deep stack has a ratio of
   50 or more.
3. Choose the stretch curve so that the extended light lands on a fixed
   display brightness (`EXTENDED_LIGHT_BRIGHTNESS`). This fixes where the sky
   lands. A bright target needs little lift, so the sky stays dark. A faint
   target needs a lot of lift, so the sky comes up. The result is kept
   between `DARKEST_SKY_LEVEL` and `LIGHTEST_SKY_LEVEL`.

If the sky cannot be measured (a blank stack, for instance), the module
returns `FALLBACK_SKY_LEVEL` and says so.
"""

import logging
import warnings
from dataclasses import dataclass

import numpy as np
from scipy.ndimage import median_filter

from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.pipelines.shared.image_scaling import (
    AUTOSTRETCH_SHADOWS_CLIP_SIGMA,
    measure_sky,
    sky_level_for_peak,
)

logger = logging.getLogger(__name__)

LIGHTEST_SKY_LEVEL = 0.20
"""The brightest sky the rule gives, as a fraction of full brightness.

A lighter sky shows fainter structure, but it also lifts the grain of the
stack into view. On the M 27 stack (82 frames over three nights), a sky level
of 0.234 gave a visibly grainier and greyer picture than 0.19, and weakening
the denoise made the grain worse, not better. 0.20 sits close to the 0.18 that
was judged best on M 81 and to the 0.193 chosen on the Bubble Nebula. Siril's
own Autostretch uses 0.25. Faint targets that the rule would lift past 0.20
get 0.20.
"""

DARKEST_SKY_LEVEL = 0.11
"""The darkest sky the rule gives, as a fraction of full brightness.

On the M 13, M 42, M 81 and M 101 stacks, a sky at 0.11 (grey 28 out of 255)
gave the strongest contrast on M 42, while M 101's faint arms had almost
disappeared. Targets that would call for a darker sky than this are bright
enough to look good at it.
"""

FALLBACK_SKY_LEVEL = 0.18
"""The sky level used when the stack's sky cannot be measured.

A middle value: 0.18 was the level that kept the faint arms of M 81 and M 101
visible while giving a visibly darker, cleaner sky than 0.25.
"""

# The display brightness the extended light is placed at. It is the rule's
# only tuned number. It was first set to 0.30 so that the M 81 stack came out
# at a sky level of 0.18. On the Bubble Nebula stack that gave a sky level of
# 0.15, which the user found too black, and the user chose a sky level near
# 0.20 from rendered options. 0.36 gives the Bubble Nebula stack about 0.19.
# The same change lifts M 81 from 0.19 to about 0.23 and puts the faintest
# fields (M 101, M 57) at the lightest level; M 42 stays at the darkest level.
# Calibrated on one target by eye, then checked on 22 other ASI533MM Pro
# stacks, where the levels stayed in the expected order (fainter targets
# lighter). Not validated on stacks from other cameras.
EXTENDED_LIGHT_BRIGHTNESS = 0.36

# The stack is cut into blocks of this many pixels on a side. A star is a few
# pixels wide, so the median of such a block ignores it. Real extended light
# (nebulae, galaxy halos) is many blocks wide and survives.
_BLOCK_PIXELS = 8

# A second, small median filter (in blocks) removes stars that were bigger
# than one block, such as the halo of a very bright star.
_BLOCK_FILTER_SIZE = 3

# The brightest extended light the rule looks at is the 99th percentile of
# the star-free map. That ignores a few extreme blocks (a star's wings) while
# still reaching the bright core of a nebula.
_EXTENDED_LIGHT_PERCENTILE = 99.0


@dataclass(frozen=True)
class SkyLevelChoice:
    """The sky level chosen for one stack, and how it was reached.

    Attributes
    ----------
    sky_level : `float`
        The brightness, between 0 and 1, the sky should land on.
    extended_light_snr : `float` or `None`
        How far the extended light sits above the sky, in units of the sky's
        noise. `None` if it could not be measured.
    reason : `str`
        A short plain-language statement of how the level was chosen.
    """

    sky_level: float
    extended_light_snr: float | None
    reason: str


def measure_extended_light(arr: np.ndarray) -> float:
    """Find the brightest extended (non-star) light in an image.

    Parameters
    ----------
    arr : `numpy.ndarray`
        The image, 2-D.

    Returns
    -------
    level : `float`
        The 99th percentile of the star-free block map, in the image's own
        units.
    """
    rows = (arr.shape[0] // _BLOCK_PIXELS) * _BLOCK_PIXELS
    columns = (arr.shape[1] // _BLOCK_PIXELS) * _BLOCK_PIXELS
    blocks = arr[:rows, :columns].reshape(
        rows // _BLOCK_PIXELS, _BLOCK_PIXELS, columns // _BLOCK_PIXELS, _BLOCK_PIXELS
    )
    with warnings.catch_warnings():
        # A block with no valid pixels gives a missing value, which the
        # percentile below skips. The warning about it is not useful.
        warnings.simplefilter("ignore", RuntimeWarning)
        block_medians = np.nanmedian(blocks, axis=(1, 3))
    extended_light_map = median_filter(block_medians, size=_BLOCK_FILTER_SIZE)
    return float(np.nanpercentile(extended_light_map, _EXTENDED_LIGHT_PERCENTILE))


def choose_sky_level(data: np.ndarray) -> SkyLevelChoice:
    """Choose the sky level for a stack's preview from its data.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image the stretch will be applied to, in linear units. A colour
        image is averaged across its channels first.

    Returns
    -------
    choice : `SkyLevelChoice`
        The sky level, between `DARKEST_SKY_LEVEL` and `LIGHTEST_SKY_LEVEL`,
        or `FALLBACK_SKY_LEVEL` if the sky cannot be measured.
    """
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=0 if arr.shape[0] in (1, 3, 4) else 2)
    sky = measure_sky(arr)
    if sky is None or arr.ndim != 2:
        return SkyLevelChoice(FALLBACK_SKY_LEVEL, None, "the sky could not be measured")
    median, sigma, peak = sky
    black_point = max(median + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return SkyLevelChoice(FALLBACK_SKY_LEVEL, None, "the image has no usable brightness range")
    extended_light = measure_extended_light(arr)
    snr = (extended_light - median) / sigma
    if not np.isfinite(snr) or extended_light <= median:
        return SkyLevelChoice(
            LIGHTEST_SKY_LEVEL, max(snr, 0.0) if np.isfinite(snr) else 0.0, "no extended light above the sky"
        )
    normalized_median = (median - black_point) / (peak - black_point)
    normalized_extended = (extended_light - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < normalized_extended < 1.0:
        return SkyLevelChoice(
            FALLBACK_SKY_LEVEL, float(snr), "the extended light is as bright as the brightest pixel"
        )
    level = sky_level_for_peak(normalized_median, normalized_extended, EXTENDED_LIGHT_BRIGHTNESS)
    chosen = min(LIGHTEST_SKY_LEVEL, max(DARKEST_SKY_LEVEL, level))
    return SkyLevelChoice(chosen, float(snr), f"extended light is {snr:.1f} sigma above the sky")


def choose_sky_level_for_file(path: str) -> SkyLevelChoice:
    """Choose the sky level for a FITS image on disk.

    Parameters
    ----------
    path : `str`
        Path of the FITS image the stretch will be applied to.

    Returns
    -------
    choice : `SkyLevelChoice`
        The choice for the image, or `FALLBACK_SKY_LEVEL` if the file cannot
        be read. The reason is logged at debug level.
    """
    try:
        data = AstrometricsImage(path).data
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read '%s' to choose a sky level: %s", path, error)
        return SkyLevelChoice(FALLBACK_SKY_LEVEL, None, "the image could not be read")
    return choose_sky_level(data)
