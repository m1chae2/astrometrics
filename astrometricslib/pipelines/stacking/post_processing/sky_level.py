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

from astrometricslib._internal.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
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
from astrometricslib.pipelines.shared.image_scaling import (
    AUTOSTRETCH_SHADOWS_CLIP_SIGMA,
    measure_sky_background,
    sky_level_for_peak,
)

logger = logging.getLogger(__name__)

LIGHTEST_SKY_LEVEL = TunedParameter(
    name="stacking.lightest_sky_level",
    default=0.13,
    unit="fraction",
    description="Brightest sky level the display rule gives, as a fraction of full brightness.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-29",
)
"""The brightest sky the rule gives, as a fraction of full brightness.

A lighter sky shows fainter structure, but it also lifts the grain of the
stack into view. Before the preview was denoised after the stretch, the cap
was 0.20: on the M 27 stack a sky level of 0.234 looked grainier and greyer
than 0.19. With Cosmic Clarity at strength 0.5 running after the stretch, the
sky noise is lower, and the user judged a darker sky of 0.13 best on the
NGC 7331 stack (extended light 2.9 sigma above the sky, which the old cap of
0.20 held at its limit). At 0.13 against 0.20 the sky noise was 5.25 against
7.41 display units and the faint arm stood out more clearly (STK-29).
Siril's own Autostretch uses 0.25. Faint targets that the rule would lift past
0.13 get 0.13.
"""

DARKEST_SKY_LEVEL = TunedParameter(
    name="stacking.darkest_sky_level",
    default=0.11,
    unit="fraction",
    description="Darkest sky level the display rule gives, as a fraction of full brightness.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-29",
)
"""The darkest sky the rule gives, as a fraction of full brightness.

On the M 13, M 42, M 81 and M 101 stacks, a sky at 0.11 (grey 28 out of 255)
gave the strongest contrast on M 42, while M 101's faint arms had almost
disappeared. Targets that would call for a darker sky than this are bright
enough to look good at it.
"""

FALLBACK_SKY_LEVEL = TunedParameter(
    name="stacking.fallback_sky_level",
    default=0.18,
    unit="fraction",
    description="Sky level used when the stack's sky cannot be measured.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-29",
)
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
EXTENDED_LIGHT_BRIGHTNESS = TunedParameter(
    name="stacking.extended_light_brightness",
    default=0.36,
    unit="fraction",
    description="Display brightness at which extended light is placed.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-29",
)

# The stack is cut into blocks of this many pixels on a side. A star is a few
# pixels wide, so the median of such a block ignores it. Real extended light
# (nebulae, galaxy halos) is many blocks wide and survives.
# On the reference setup this stands for 8 pixels; the code scales it as a
# multiple of the star width, so it follows the star width the run measures.
_BLOCK_PIXELS = TunedParameter(
    name="stacking.block_pixels",
    default=factor_for(8, RunBasis.STAR_WIDTH_PX),
    basis=RunBasis.STAR_WIDTH_PX,
    reference_value=8,
    unit="× star width",
    description="Side of the blocks the stack is cut into to find its sky.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="STK-D54",
)

# A second, small median filter (in blocks) removes stars that were bigger
# than one block, such as the halo of a very bright star.
_BLOCK_FILTER_SIZE = TunedParameter(
    name="stacking.block_filter_size",
    default=3,
    unit="blocks",
    description="Size of the small median filter that removes stars bigger than one block.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D54",
)

# The brightest extended light the rule looks at is the 99th percentile of
# the star-free map. That ignores a few extreme blocks (a star's wings) while
# still reaching the bright core of a nebula.
_EXTENDED_LIGHT_PERCENTILE = TunedParameter(
    name="stacking.extended_light_percentile",
    default=99.0,
    unit="percentile",
    description="Percentile of the star-free map taken as the brightest extended light.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D54",
)


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
    block_pixels = resolve(_BLOCK_PIXELS).value
    rows = (arr.shape[0] // block_pixels) * block_pixels
    columns = (arr.shape[1] // block_pixels) * block_pixels
    blocks = arr[:rows, :columns].reshape(
        rows // block_pixels, block_pixels, columns // block_pixels, block_pixels
    )
    with warnings.catch_warnings():
        # A block with no valid pixels gives a missing value, which the
        # percentile below skips. The warning about it is not useful.
        warnings.simplefilter("ignore", RuntimeWarning)
        block_medians = np.nanmedian(blocks, axis=(1, 3))
    extended_light_map = median_filter(block_medians, size=resolve(_BLOCK_FILTER_SIZE).value)
    return float(np.nanpercentile(extended_light_map, resolve(_EXTENDED_LIGHT_PERCENTILE).value))


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
    lightest_sky_level = resolve(LIGHTEST_SKY_LEVEL).value
    fallback_sky_level = resolve(FALLBACK_SKY_LEVEL).value
    arr = np.asarray(data, dtype=np.float64)
    if arr.ndim == 3:
        arr = arr.mean(axis=0 if arr.shape[0] in (1, 3, 4) else 2)
    sky = measure_sky_background(arr)
    if sky is None or arr.ndim != 2:
        return SkyLevelChoice(fallback_sky_level, None, "the sky could not be measured")
    median, sigma = sky
    peak = float(np.nanmax(arr))
    black_point = max(median + AUTOSTRETCH_SHADOWS_CLIP_SIGMA * sigma, float(np.nanmin(arr)))
    if peak <= black_point:
        return SkyLevelChoice(fallback_sky_level, None, "the image has no usable brightness range")
    extended_light = measure_extended_light(arr)
    snr = (extended_light - median) / sigma
    if not np.isfinite(snr) or extended_light <= median:
        return SkyLevelChoice(
            lightest_sky_level, max(snr, 0.0) if np.isfinite(snr) else 0.0, "no extended light above the sky"
        )
    normalized_median = (median - black_point) / (peak - black_point)
    normalized_extended = (extended_light - black_point) / (peak - black_point)
    if not 0.0 < normalized_median < normalized_extended < 1.0:
        return SkyLevelChoice(
            fallback_sky_level, float(snr), "the extended light is as bright as the brightest pixel"
        )
    level = sky_level_for_peak(
        normalized_median, normalized_extended, resolve(EXTENDED_LIGHT_BRIGHTNESS).value
    )
    chosen = min(lightest_sky_level, max(resolve(DARKEST_SKY_LEVEL).value, level))
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
        return SkyLevelChoice(resolve(FALLBACK_SKY_LEVEL).value, None, "the image could not be read")
    return choose_sky_level(data)
