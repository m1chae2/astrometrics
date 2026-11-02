"""Measuring each exposure group's brightness scale on its pixels.

A stack of 5 s frames and a stack of 0.5 s frames show the same sky at
different brightness. To combine them, each group needs a gain: the factor
that puts its counts per second on the reference group's scale. This
module measures the gain from mid-range pixels, after leaving out the
pixels that are saturated, and it also holds the helpers that estimate a
stack's background noise and saturation level. A group whose brightest
pixels disagree with the gain is left out as nonlinear, and a group whose
sky is far brighter than the reference's is left out as having a bright
sky.

The star-based measurement is in `group_star_gains`. The overview of the
whole method is in the `exposure_groups` module docstring.
"""

import logging
import math
from dataclasses import dataclass

import numpy as np

from astrometricslib.foundation.constants import FULL_SCALE_COUNTS
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.pipelines.shared.quality.saturation import (
    SATURATION_MASK_FRACTION_OF_CEILING,
    find_saturation_plateau_ceiling,
)

logger = logging.getLogger(__name__)


# A raw frame with more than this fraction of its pixels at exactly zero is
# treated as clipped: the camera's offset sits below the read noise, so the
# frame cannot show negative noise and its measured noise is too low. On the
# Vega session (ASI533MM Pro, gain 0) the zero fraction was 71% at 0.5 s, 31%
# at 1 s, 1% at 2 s, 0.2% at 3 s and 0.02% at 5 s, and the measured noise was
# 0 counts, 4.2 counts and 8.4 counts (2, 3 and 5 s all gave 8.4), so the
# clipped frames read low by up to a half or more. 0.2 sits between the 1 s
# group (clipped) and the 2 s group (not). Unvalidated on other cameras or
# offsets.
CLIPPED_FRAME_ZERO_FRACTION = 0.2

# A stacked image with less than this fraction of its pixels holding data
# (finite and not exactly zero) is treated as blank and discarded (see
# `siril_stacking._is_stack_blank`). When the frames will not register, the
# python phase-correlation fallback can write a stack file with nothing in it,
# and because it reports no registration counts it counted as 100% registered
# and beat the real stacks. On the Vega restack of 2026-09-25 the 0.5 s group
# (138 frames from two nights; standard detection registered 43%, relaxed 26%)
# ended up with a blank stack, exactly 0.0% of pixels holding data, which then
# carried the most weight, became the reference every other group was lined up
# with, and turned the combined image into garbage. Each night's 0.5 s frames
# stacked fine on their own (6% and 38% of pixels). The limit is kept tiny
# (0.01%, under 1,000 pixels of a 3008 x 3008 stack) because a real but faint
# stack can hold little: short exposures are clipped at zero, and on the same
# session the good 0.02 s to 0.25 s groups held only 0.1% to 0.8% (the star
# and its spectrum), against 76% to 100% for the 1 s to 5 s groups. Checked on
# that one session only.
MINIMUM_STACK_DATA_FRACTION = 0.0001

# A pixel in a group's stack at or above this fraction of the full scale (1.0
# is the brightest value a 16-bit frame can hold, as Siril writes it) is
# treated as saturated in that group, and is taken from the other groups
# instead. Long exposures of a bright star clip at the top of the range, and
# a clipped value is not a measurement. Unvalidated: chosen a little below
# full scale to catch stack averages that fall just short of 1.0. It has not
# been checked against a saturated star in a stack from this pipeline.
SATURATION_FRACTION_OF_FULL_SCALE = 0.95

# The plateau-ceiling constants (SATURATION_CEILING_SAMPLE_PIXELS and the
# rest) live in astrometricslib.pipelines.shared.quality.saturation, where the
# stack-wide saturation check shares them.

# Each group's stack also carries its own overall brightness scale. The
# constants from here to SKY_CLIP_PASSES belong to the pixel method
# (`measure_group_gains`), the fallback for a group with too few stars and the
# only method for a spectral stack; the star-flux method that imaging stacks
# use first comes after PLAUSIBLE_GAIN_RANGE. Against the 2 s group, the Vega
# session's per-second brightness was 0.84-0.88 (1 s), 0.71-0.74 (3 s) and
# 0.65-0.71 (5 s), almost the same for faint and bright pixels, so it is a
# scale factor (a gain), not an offset. Mixing groups at their face value puts
# a step in the spectrum wherever the light source switches from one group to
# another. The gain of each group is therefore measured against the group with
# the highest weight, as the median ratio of their per-second brightness over
# the pixels in the middle of the reference's brightness range: the 20th to the
# 80th percentile of the pixels that neither group saturates or masks
# (GAIN_MID_RANGE_PERCENTILES). Those pixels sit above the sky floor and below
# the bright end, where a sensor leaves its linear range (near full well) or a
# clipped group reads zero. In the linear range the ratio of two exposures is
# the ratio of their light per second, which is the gain. Fewer than
# GAIN_MINIMUM_PIXELS such pixels leaves the gain at 1. The percentiles and the
# pixel count are unvalidated choices: 60% of a 3008 x 3008 image is about 5
# million pixels.
GAIN_MID_RANGE_PERCENTILES = (20.0, 80.0)

GAIN_MINIMUM_PIXELS = 200

# The ratio above is taken after each group's own sky level is subtracted,
# and only over pixels whose sky-subtracted reference value is at least
# GAIN_SOURCE_SIGMAS times the reference's background noise. Without that,
# the 20th to 80th percentile pixels of a deep-sky field are almost all sky,
# and the "gain" is the ratio of the two nights' sky brightness. Measured on
# the ASI 533MM stacks of 2026-10-10: the old gain equalled the sky-per-second
# ratio to three decimals (M 81 1.297 vs 1.298, NGC 2403 1.048 vs 1.049),
# while aperture photometry of the stars gave 1.40 and 1.12, so the shorter
# group's stars were mis-scaled by 7-8%. At 10 sigma a pixel's noise moves its
# ratio by at most about 10%, and the median over many pixels removes most of
# that; noise alone puts fewer than one pixel of a 9-million-pixel image
# above it. The value is a design choice checked on those two fields.
GAIN_SOURCE_SIGMAS = 10.0

# How far from the median a neighbour difference may be, in units of the
# rough (median absolute deviation) scale, and still count as noise in
# `estimate_background_noise`. 4 cuts only 0.006% of a bell curve, and the
# rough scale is coarse by up to ~20% for a stepped sensor, so the real cut
# is 3.2 to 5 sigma: still leaves the bias under 0.5% while star pixels
# fall outside. Checked on synthetic stepped data by
# test_the_noise_estimate_is_fine_grained_on_stepped_data.
NOISE_CLIP_SIGMAS = 4.0

# The sky level of a group is the median of its trusted pixels after
# repeatedly dropping pixels more than SKY_CLIP_SIGMAS spreads from it (stars
# and the target), as in a sigma-clipped median. Five passes settle it on the
# fields above.
SKY_CLIP_SIGMAS = 3.0

SKY_CLIP_PASSES = 5

# In the pixel method, the brightest pixels give a second, diagnostic ratio:
# the median ratio over the top GAIN_BRIGHTEST_FRACTION of the shared pixels
# (1% of a 3008 x 3008 image is about 90,000 pixels). This was the only
# estimator once. The brightest pixels are where nonlinearity shows first, so
# on a camera that is not linear there this ratio drifts away from the
# mid-range one. On the Vega session it read 0.65-0.88 between groups of one
# camera, which is what a compressed bright end looks like and not what a gain
# looks like. The pipeline records both ratios and compares them (see
# DEFAULT_GAIN_DISAGREEMENT_TOLERANCE).
GAIN_BRIGHTEST_FRACTION = 0.01

# How far, in the pixel method, the bright-end ratio may differ from the
# mid-range gain, as a fraction of the gain, before the group is treated as
# nonlinear and left out of the combined image. The star-flux method uses the
# same tolerance on its faint and bright quarters (next comment). A truly
# linear pair of groups agrees to well under 1% when the photon noise is small
# (the unit tests reach 0.5%). 5% allows for noise in the 90,000 bright pixels
# and for small flat-field residuals, and is far below the 35% disagreement
# seen in the field. It is a design choice, not a validated limit;
# `AppConfiguration.get_exposure_group_gain_tolerance` lets an observer change
# it.
DEFAULT_GAIN_DISAGREEMENT_TOLERANCE = 0.05

# With star fluxes (the star-flux gain below), the same tolerance bounds how
# far the faintest or the brightest quarter of the stars may sit from the
# gain. Measured on 2026-10-13 (the worst quarter's distance from the gain):
# the pairs that combine well read 0.7 to 2.5% (NGC 6888, M 106, NGC 6992,
# NGC 2403, M 81, M 27 L) and 4.5% (M 57 30 s), the quarter medians of 200 to
# 1,500 stars being good to about 1%. M 51's 60 s group, whose bright stars
# read 8% from its gain, is the case it catches. 5% sits between them and is
# still a design choice (see documentation/library_design/validation/
# stacking.md, STK-20).

# A measured gain outside this range is not believed and the group keeps a
# gain of 1. The gains measured on the Vega session were 0.65 to 1 (see
# above), so a group reading four times brighter or dimmer per second than
# the reference is a failed measurement, not a real scale. On Albireo
# (2026-09-25) the two shortest groups, clipped at zero, were measured
# against the reference's brightest pixels, where they hold almost nothing:
# their gains came out 1.4e-18 and 6e-31, dividing by them made the combined
# image peak at 1e29, and only 32 stars were then detected in it. The range
# is a judgement, wide enough for the real spread and far from those failures
# (see documentation/library_design/validation/stacking.md, STK-21). It
# applies to the star-flux gain and to the pixel gain alike.
PLAUSIBLE_GAIN_RANGE = (0.25, 4.0)

# A group whose sky is more than SKY_RATIO_LIMIT times brighter (per second)
# than the reference's is left out. A bright sky means a different night
# (moon, haze, dawn) whose frames are partly flooded toward the camera's
# ceiling, so the assumptions of the gain (the same stars on a flat,
# different offset) stop holding and the group's noise weight cannot be
# trusted. Measured on the Nikon D5300 group stacks of 2026-10-13, as the
# group's sky per second over the reference's: groups that combine well read
# 1.34 and 1.36 (NGC 6888, same night), 1.15 (M 106), 1.98 (NGC 6992 300 s,
# whose stars are linear and agree to 2%), 1.0 (M 51 60 s) and 0.53 (IC 1805
# 600 s); the groups with a flooded or foreign sky read 3.9 (M 51 300 s, a
# different year) and 7.6 (IC 1805 300 s, 28% of the camera's ceiling
# against 4.5% for its reference). 3 lies between the 1.98 that is kept and
# the 3.9 that is not (their geometric middle is 2.8). It rests on those two
# bad groups; the 13 other imaging pairs (ASI533MM and Nikon) read 0.59 to
# 1.58, so it is a judgement from a thin gap, not a validated limit (see
# documentation/library_design/validation/stacking.md, STK-21). Only a
# brighter sky is judged: a darker sky is no harm. The sky is compared only
# for imaging stacks (with `use_star_fluxes`).
SKY_RATIO_LIMIT = 3.0


def describe_group_clipping(
    exposures: list[float], zero_fractions: list[float]
) -> tuple[list[dict[str, float]], list[dict[str, float]]]:
    """Turn measured raw-frame zero fractions into diagnostics entries.

    Parameters
    ----------
    exposures : `list` [`float`]
        The exposure length of each group, in seconds.
    zero_fractions : `list` [`float`]
        The sampled zero fraction of each group's raw frames (see
        `measure_group_frames`).

    Returns
    -------
    measured : `list` [`dict`]
        One ``{"exposure_seconds", "frame_zero_fraction"}`` entry per group.
    clipped : `list` [`dict`]
        The entries above `CLIPPED_FRAME_ZERO_FRACTION`.
    """
    measured = [
        {"exposure_seconds": float(exposure), "frame_zero_fraction": round(float(zero_fraction), 3)}
        for exposure, zero_fraction in zip(exposures, zero_fractions, strict=True)
    ]
    # Judged on the unrounded value, the same as the clipped flag of the
    # group's summary entry.
    clipped = [
        entry
        for entry, zero_fraction in zip(measured, zero_fractions, strict=True)
        if zero_fraction > CLIPPED_FRAME_ZERO_FRACTION
    ]
    return measured, clipped


def stack_data_fraction(image: np.ndarray) -> float:
    """Measure how much of a stacked image holds data.

    Parameters
    ----------
    image : `numpy.ndarray`
        One group's stacked image.

    Returns
    -------
    fraction : `float`
        The fraction of pixels that are finite and not exactly zero, from 0
        to 1. A blank stack gives 0.
    """
    pixels = np.asarray(image)
    if pixels.size == 0:
        return 0.0
    return float(np.count_nonzero(np.isfinite(pixels) & (pixels != 0)) / pixels.size)


def estimate_background_noise(image: np.ndarray) -> float:
    """Measure how noisy an image's background is.

    The noise is estimated from the difference between neighbouring pixels.
    A smooth background and the few pixels that hold stars barely change
    that difference, while noise does, so the answer is not thrown off by
    the stars in the frame.

    The estimate takes two passes. The first pass uses the "median absolute
    deviation" (the middle of the sorted sizes of the differences) as a
    rough scale. It ignores stars but is coarse: a camera that stores its
    values in steps (the ZWO ASI533MM keeps 14-bit values in steps of 4)
    makes every difference a multiple of the step, so the median can only
    land on a multiple of that step. The second pass keeps the differences
    within `NOISE_CLIP_SIGMAS` of the median, using that rough scale as the
    limit, and takes their ordinary standard deviation. A standard deviation
    uses every kept value, so it is fine-grained even when the data are
    stepped. The limit is wide enough that the rough scale's error (up to
    about 20% at one step per sigma) hardly moves it, and tight enough that
    star pixels, whose differences are many sigma, fall outside it.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D image.

    Returns
    -------
    noise : `float`
        The typical noise of one pixel, in the image's own units. It is 0
        when more than half of the differences are identical (for example
        an image that is mostly clipped to zero).
    """
    differences = np.asarray(image[:, 1:], dtype=np.float64) - np.asarray(image[:, :-1], dtype=np.float64)
    differences = differences[np.isfinite(differences)]
    if differences.size == 0:
        return float("nan")
    center = np.median(differences)
    # 1.4826 turns a median absolute deviation into a standard deviation for
    # bell-curve noise.
    rough_scale = 1.4826 * np.median(np.abs(differences - center))
    if rough_scale <= 0:
        return 0.0
    kept = differences[np.abs(differences - center) <= NOISE_CLIP_SIGMAS * rough_scale]
    # Cutting a bell curve at 4 sigma removes 0.05% of its spread (the
    # standard deviation of a normal curve cut at +-4 sigma is 0.99947 of the
    # full one), so no correction is applied. If the rough scale is 20% off,
    # the cut sits at 3.2 to 5 sigma and the bias stays under 0.5% (checked
    # on synthetic data in test_exposure_groups.py).
    # Dividing by the square root of 2 undoes the extra noise from taking a
    # difference of two pixels.
    return float(np.std(kept) / math.sqrt(2.0))


def estimate_saturation_mask_level(
    image: np.ndarray,
    fallback_level: float = SATURATION_FRACTION_OF_FULL_SCALE,
    clip_ceiling_adu: float | None = None,
) -> float:
    """Find the pixel value above which one group's stack is saturated.

    Saturated pixels pile up at a ceiling, so a saturated stack has many
    pixels at almost the same, highest value. If the image shows such a
    pile, the mask level is a little below its value; if not, the fallback
    level is used. The fallback is a fraction of the stack's full scale, and a
    camera whose sensor tops out below 16 bits (a 14-bit Nikon D5300 clips at
    16383 of 65535, a quarter of the scale) can never reach it, so the
    camera's clip ceiling brings it down (see `clip_ceiling_adu`).

    Parameters
    ----------
    image : `numpy.ndarray`
        One group's stacked image.
    fallback_level : `float`, optional
        The level to use when no ceiling can be seen, as a fraction of the
        full scale of a 16-bit stack.
    clip_ceiling_adu : `float`, optional
        The brightest value the camera's raw frames can hold, from its camera
        profile (`CameraProfile.clip_ceiling_adu`). When no plateau is seen,
        the fallback is scaled by this over `FULL_SCALE_COUNTS` (never up). A
        camera whose ceiling is 65532 or 65535 changes the fallback by 0.005%
        at most. Not used when a plateau is found.

    Returns
    -------
    level : `float`
        Pixels at or above this value should be treated as saturated.
    """
    ceiling = find_saturation_plateau_ceiling(image)
    if ceiling is None:
        if clip_ceiling_adu is not None and clip_ceiling_adu > 0:
            return fallback_level * min(1.0, clip_ceiling_adu / FULL_SCALE_COUNTS)
        return fallback_level
    return min(fallback_level, ceiling * SATURATION_MASK_FRACTION_OF_CEILING)


def saturated_pixel_mask(
    images: list[np.ndarray],
    saturation_level: float = SATURATION_FRACTION_OF_FULL_SCALE,
    clip_ceiling_adu: float | None = None,
) -> np.ndarray:
    """Mark the pixels where at least one group is left out as saturated.

    `combine_exposure_group_images` leaves a group out at a pixel where that
    group's stack is saturated, so there the combined image holds another
    group's data. This function applies the same test to the same images, so a
    caller can tell which pixels of the combined image were replaced (a star's
    core, where its longest group clips).

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The group stacks as they are given to `combine_exposure_group_images`,
        all the same shape.
    saturation_level : `float`, optional
        Passed to `estimate_saturation_mask_level` for every group.
    clip_ceiling_adu : `float`, optional
        Passed to `estimate_saturation_mask_level` for every group.

    Returns
    -------
    mask : `numpy.ndarray`
        A 2-D boolean array, `True` where any group is saturated (in any
        colour channel, for a colour stack).

    Raises
    ------
    InvalidArgumentError
        If `images` is empty.
    """
    mask = None
    for image in images:
        raw = np.asarray(image)
        saturated = raw >= estimate_saturation_mask_level(raw, saturation_level, clip_ceiling_adu)
        if saturated.ndim > 2:
            saturated = saturated.any(axis=tuple(range(saturated.ndim - 2)))
        mask = saturated if mask is None else (mask | saturated)
    if mask is None:
        raise InvalidArgumentError("At least one image is needed.")
    return mask


@dataclass
class GroupGainMeasurement:
    """What was measured about one group's brightness scale.

    Attributes
    ----------
    gain : `float`
        The scale applied to the group: how many times brighter (per second)
        it reads than the reference. For ``"stars"`` it is the median
        aperture-flux ratio of the isolated, unsaturated stars the two groups
        share; for ``"pixels"`` it is the median ratio over mid-range pixels.
        It is 1.0 for the reference group and when no believable value could
        be measured.
    bright_end_ratio : `float` or `None`
        The same ratio measured on the brightest quarter of the stars
        (``"stars"``) or on the reference's brightest pixels only
        (``"pixels"``). It is a diagnostic and is never applied. `None` when
        too few bright pixels are shared with the reference.
    disagreement : `float` or `None`
        How far the diagnostic ratios sit from the gain, as a fraction of the
        gain. For ``"stars"`` it is the larger of
        ``abs(faint_ratio - gain)`` and ``abs(bright_end_ratio - gain)``,
        divided by the gain; for ``"pixels"`` it is
        ``abs(bright_end_ratio - gain) / gain``. `None` when a ratio could
        not be measured.
    nonlinear : `bool`
        `True` when `disagreement` is larger than the tolerance. The group's
        brightness is then not one scale factor, and the group should not be
        combined with the reference.
    sky_level : `float`
        The group's own sky level per second (see `estimate_sky_level`). It
        is subtracted before the ratios are taken.
    measured : `bool`
        `True` when `gain` was measured (always for the reference). Only
        then does the combination move the group's sky to the reference's
        sky level; a group left at a gain of 1 is combined as it is.
    method : `str`
        How the numbers above were measured: ``"stars"`` (aperture flux of
        isolated stars, see `measure_star_flux_ratios`), ``"pixels"`` (the
        pixel ratios of `measure_group_gains`) or ``"reference"``. For
        ``"stars"``, `gain` is the median ratio over all stars,
        `bright_end_ratio` is the median over the brightest quarter of the
        stars, `faint_ratio` the median over the faintest quarter, and
        `disagreement` is the larger of their distances from `gain`, as a
        fraction of `gain`: how far the single gain misscales the faint or
        the bright stars.
    stars_used : `int`
        How many stars the star gain was measured on (0 for ``"pixels"``).
    faint_ratio : `float` or `None`
        The ratio over the faintest quarter of the stars (``"stars"`` only).
    sky_ratio : `float` or `None`
        This group's sky level per second divided by the reference's. `None`
        when either sky level is not positive.
    sky_too_bright : `bool`
        `True` when `sky_ratio` is above `SKY_RATIO_LIMIT`. The group should
        not be combined with the reference.
    """

    gain: float = 1.0
    bright_end_ratio: float | None = None
    disagreement: float | None = None
    nonlinear: bool = False
    sky_level: float = 0.0
    measured: bool = False
    method: str = "pixels"
    stars_used: int = 0
    faint_ratio: float | None = None
    sky_ratio: float | None = None
    sky_too_bright: bool = False


def estimate_sky_level(image: np.ndarray, mask: np.ndarray) -> float:
    """Measure the sky level of an image, ignoring stars and the target.

    This is a sigma-clipped median: the median of the trusted pixels, then
    the median of the pixels within `SKY_CLIP_SIGMAS` spreads of it, repeated
    up to `SKY_CLIP_PASSES` times. The spread is the median absolute
    deviation turned into a standard deviation.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D image.
    mask : `numpy.ndarray`
        `True` where the image can be trusted.

    Returns
    -------
    sky_level : `float`
        The sky level, in the image's own units. 0.0 when no pixel is trusted.
    """
    values = np.asarray(image, dtype=np.float64)[np.asarray(mask, dtype=bool)]
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    level = float(np.median(values))
    for _ in range(SKY_CLIP_PASSES):
        spread = 1.4826 * float(np.median(np.abs(values - level)))
        if spread <= 0:
            break
        kept = values[np.abs(values - level) <= SKY_CLIP_SIGMAS * spread]
        if kept.size == 0:
            break
        new_level = float(np.median(kept))
        if new_level == level:
            break
        level = new_level
    return level


def _median_ratio(image: np.ndarray, reference: np.ndarray, selected: np.ndarray) -> float | None:
    """Take the median of image / reference over the selected pixels.

    Returns
    -------
    ratio : `float` or `None`
        The median ratio, or `None` when fewer than `GAIN_MINIMUM_PIXELS`
        pixels are selected or the median is not a finite number.
    """
    if int(np.count_nonzero(selected)) < GAIN_MINIMUM_PIXELS:
        return None
    ratio = float(np.median(image[selected] / reference[selected]))
    return ratio if np.isfinite(ratio) else None


def measure_group_gains(
    per_second_images: list[np.ndarray],
    usable_masks: list[np.ndarray],
    reference_index: int,
    tolerance: float = DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    only: set[int] | None = None,
    sky_levels: list[float] | None = None,
) -> list[GroupGainMeasurement]:
    """Measure each group's brightness scale and check that it is a scale.

    This is the pixel method. `measure_group_gains_with_stars` prefers the
    star-flux method and uses this one for a group with too few stars.

    Each image's own sky level (`estimate_sky_level`) is subtracted first,
    because two nights rarely share one sky brightness. The gain is then the
    median of image / reference over the source pixels: those whose
    sky-subtracted reference value is at least `GAIN_SOURCE_SIGMAS` times the
    reference's background noise, and lies between the 20th and 80th percentile
    (`GAIN_MID_RANGE_PERCENTILES`) of such pixels that both images can be
    trusted at. Why that range: a sensor is linear between the sky floor and
    near full well, and in that range two exposures of one scene differ by a
    single factor, the ratio of their light per second. That factor is the true
    gain. Close to full well the sensor response flattens, and in a group
    clipped at zero the faint end reads high, so at the extremes the ratio
    measures the sensor's nonlinearity and not the gain. The brightest 1% of
    pixels (`GAIN_BRIGHTEST_FRACTION`) lie in that nonlinear zone, so the
    median ratio there is computed too, as a diagnostic, and never applied. A
    group whose two ratios differ by more than `tolerance` (as a fraction of
    the gain) is marked `nonlinear`: one scale factor does not describe it.

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    usable_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels can be trusted: not
        saturated or masked and, for a group clipped at zero, above its
        clipping floor.
    reference_index : `int`
        The group the others are compared with. Its gain is 1.
    tolerance : `float`, optional
        How far the bright-end ratio may differ from the gain, as a fraction
        of the gain, before the group is marked `nonlinear`.
    only : `set` [`int`], optional
        When given, only these groups (and the reference) are measured; the
        others get an empty entry that carries just their sky level.
    sky_levels : `list` [`float`], optional
        The groups' sky levels when the caller already has them.

    Returns
    -------
    measurements : `list` [`GroupGainMeasurement`]
        One entry per group. A group with too few usable pixels in common
        with the reference, or a gain outside `PLAUSIBLE_GAIN_RANGE`, keeps a
        gain of 1 and is never marked `nonlinear`, because there is nothing
        to compare.
    """
    if sky_levels is None:
        sky_levels = [
            estimate_sky_level(image, mask)
            for image, mask in zip(per_second_images, usable_masks, strict=True)
        ]
    reference = per_second_images[reference_index] - sky_levels[reference_index]
    reference_noise = estimate_background_noise(per_second_images[reference_index])
    source_floor = GAIN_SOURCE_SIGMAS * reference_noise if np.isfinite(reference_noise) else 0.0
    measurements = []
    for index, raw_image in enumerate(per_second_images):
        sky_level = sky_levels[index]
        if index == reference_index:
            measurements.append(
                GroupGainMeasurement(
                    gain=1.0,
                    bright_end_ratio=1.0,
                    disagreement=0.0,
                    sky_level=sky_level,
                    measured=True,
                    method="reference",
                )
            )
            continue
        if only is not None and index not in only:
            measurements.append(GroupGainMeasurement(sky_level=sky_level))
            continue
        image = raw_image - sky_level
        shared = (
            usable_masks[index]
            & usable_masks[reference_index]
            & (reference > max(source_floor, 0.0))
            & np.isfinite(image)
        )
        if int(np.count_nonzero(shared)) < GAIN_MINIMUM_PIXELS:
            measurements.append(GroupGainMeasurement(sky_level=sky_level))
            continue
        low, high = np.percentile(reference[shared], GAIN_MID_RANGE_PERCENTILES)
        gain = _median_ratio(image, reference, shared & (reference >= low) & (reference <= high))
        cut = np.quantile(reference[shared], 1.0 - GAIN_BRIGHTEST_FRACTION)
        bright_end_ratio = _median_ratio(image, reference, shared & (reference >= cut))
        if gain is None or not PLAUSIBLE_GAIN_RANGE[0] <= gain <= PLAUSIBLE_GAIN_RANGE[1]:
            logger.warning(
                "Exposure group %d measured a brightness gain of %s, outside %s; using 1 instead.",
                index,
                "nothing" if gain is None else f"{gain:.3g}",
                PLAUSIBLE_GAIN_RANGE,
            )
            measurements.append(GroupGainMeasurement(bright_end_ratio=bright_end_ratio, sky_level=sky_level))
            continue
        disagreement = None if bright_end_ratio is None else abs(bright_end_ratio - gain) / gain
        measurements.append(
            GroupGainMeasurement(
                gain=gain,
                bright_end_ratio=bright_end_ratio,
                disagreement=disagreement,
                nonlinear=disagreement is not None and disagreement > tolerance,
                sky_level=sky_level,
                measured=True,
            )
        )
    return measurements


def describe_nonlinear_group(measurement: GroupGainMeasurement, reference_exposure_seconds: float) -> str:
    """Say in one sentence why a group's brightness is not one scale factor.

    Parameters
    ----------
    measurement : `GroupGainMeasurement`
        A measurement with `nonlinear` set.
    reference_exposure_seconds : `float`
        The exposure length of the group it was compared with.

    Returns
    -------
    reason : `str`
        A sentence that continues "the N s exposure group ...", naming the
        ratios that disagree and how far apart they are.
    """
    if measurement.method == "stars":
        return (
            f"was left out: its star brightness ratio to the {reference_exposure_seconds:g} s group is "
            f"{measurement.gain:.3f} over all {measurement.stars_used} stars measured, but "
            f"{measurement.faint_ratio:.3f} on the faintest quarter of them and "
            f"{measurement.bright_end_ratio:.3f} on the brightest quarter "
            f"(up to {measurement.disagreement:.1%} from the overall ratio), "
            "so its bright stars are nonlinear or clipped and one scale factor does not describe it"
        )
    return (
        f"was left out: its brightness ratio to the {reference_exposure_seconds:g} s group is "
        f"{measurement.gain:.3f} on mid-range pixels but {measurement.bright_end_ratio:.3f} on "
        f"the brightest pixels ({measurement.disagreement:.1%} apart), so its bright end is "
        "nonlinear or clipped and one scale factor does not describe it"
    )


def describe_bright_sky_group(measurement: GroupGainMeasurement, reference_exposure_seconds: float) -> str:
    """Say in one sentence why a group's sky is too bright to combine.

    Parameters
    ----------
    measurement : `GroupGainMeasurement`
        A measurement with `sky_too_bright` set.
    reference_exposure_seconds : `float`
        The exposure length of the group it was compared with.

    Returns
    -------
    reason : `str`
        A sentence that continues "the N s exposure group ...".
    """
    return (
        f"was left out: its sky is {measurement.sky_ratio:.1f} times brighter per second than the "
        f"{reference_exposure_seconds:g} s group's (limit {SKY_RATIO_LIMIT:g}), so its frames were taken "
        "under a flooded or foreign sky and its stars cannot be trusted to scale with the reference"
    )


def is_plausible_gain(gain: float) -> bool:
    """Say whether a measured gain lies inside `PLAUSIBLE_GAIN_RANGE`.

    Returns
    -------
    plausible : `bool`
        `True` when the gain is a finite number inside the range.
    """
    return bool(np.isfinite(gain) and PLAUSIBLE_GAIN_RANGE[0] <= gain <= PLAUSIBLE_GAIN_RANGE[1])


def record_sky_ratios(measurements: list[GroupGainMeasurement], reference_index: int) -> None:
    """Put each group's sky relative to the reference's on its measurement.

    Parameters
    ----------
    measurements : `list` [`GroupGainMeasurement`]
        Filled in place: `sky_ratio` for every group, and
        `sky_too_bright` for a group whose ratio is above `SKY_RATIO_LIMIT`.
    reference_index : `int`
        Which entry is the reference.
    """
    reference_sky = measurements[reference_index].sky_level
    for index, measurement in enumerate(measurements):
        if index == reference_index or reference_sky <= 0 or measurement.sky_level <= 0:
            continue
        ratio = measurement.sky_level / reference_sky
        measurement.sky_ratio = ratio
        measurement.sky_too_bright = ratio > SKY_RATIO_LIMIT
