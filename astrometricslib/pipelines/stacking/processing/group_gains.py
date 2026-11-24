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

from astrometricslib.foundation.config import EXPOSURE_GROUP_GAIN_TOLERANCE
from astrometricslib.foundation.constants import FULL_SCALE_COUNTS
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.clipped_frames import CLIPPED_FRAME_ZERO_FRACTION
from astrometricslib.pipelines.shared.quality.saturation import (
    SATURATION_MASK_FRACTION_OF_CEILING,
    find_saturation_plateau_ceiling,
)

logger = logging.getLogger(__name__)


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
MINIMUM_STACK_DATA_FRACTION = TunedParameter(
    name="stacking.minimum_stack_data_fraction",
    default=0.0001,
    unit="fraction",
    description="Share of pixels holding data below which a stacked image is treated as blank.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D69",
)

# A pixel in a group's stack at or above this fraction of the full scale (1.0
# is the brightest value a 16-bit frame can hold, as Siril writes it) is
# treated as saturated in that group, and is taken from the other groups
# instead. Long exposures of a bright star clip at the top of the range, and
# a clipped value is not a measurement. Unvalidated: chosen a little below
# full scale to catch stack averages that fall just short of 1.0. It has not
# been checked against a saturated star in a stack from this pipeline.
SATURATION_FRACTION_OF_FULL_SCALE = TunedParameter(
    name="stacking.saturation_fraction_of_full_scale",
    default=0.95,
    unit="fraction of full scale",
    description="Share of full scale at or above which a pixel of a group's stack counts as saturated.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D11",
)

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
GAIN_MID_RANGE_PERCENTILES = TunedParameter(
    name="stacking.gain_mid_range_percentiles",
    default=(20.0, 80.0),
    unit="percentiles",
    description="Percentile range of the reference stack used for the mid-range gain.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

GAIN_MINIMUM_PIXELS = TunedParameter(
    name="stacking.gain_minimum_pixels",
    default=200,
    unit="pixels",
    description="Fewest shared pixels the pixel method needs to measure a gain.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

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
GAIN_SOURCE_SIGMAS = TunedParameter(
    name="stacking.gain_source_sigmas",
    default=10.0,
    unit="noise sigmas",
    description="Height, in background noise sigmas, a reference pixel must have to be used for the gain.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

# How far from the median a neighbour difference may be, in units of the
# rough (median absolute deviation) scale, and still count as noise in
# `estimate_background_noise`. 4 cuts only 0.006% of a bell curve, and the
# rough scale is coarse by up to ~20% for a stepped sensor, so the real cut
# is 3.2 to 5 sigma: still leaves the bias under 0.5% while star pixels
# fall outside. Checked on synthetic stepped data by
# test_the_noise_estimate_is_fine_grained_on_stepped_data.
NOISE_CLIP_SIGMAS = TunedParameter(
    name="stacking.noise_clip_sigmas",
    default=4.0,
    unit="robust sigmas",
    description="Distance, in robust sigmas, beyond which a neighbour difference no longer counts as noise.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D26",
)

# The sky level of a group is the median of its trusted pixels after
# repeatedly dropping pixels more than SKY_CLIP_SIGMAS spreads from it (stars
# and the target), as in a sigma-clipped median. Five passes settle it on the
# fields above.
SKY_CLIP_SIGMAS = TunedParameter(
    name="stacking.sky_clip_sigmas",
    default=3.0,
    unit="spreads",
    description="Distance, in spreads, beyond which pixels are dropped when the sky level is found.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

SKY_CLIP_PASSES = TunedParameter(
    name="stacking.sky_clip_passes",
    default=5,
    unit="passes",
    description="Number of clipping passes used when the sky level is found.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

# In the pixel method, the brightest pixels give a second, diagnostic ratio:
# the median ratio over the top GAIN_BRIGHTEST_FRACTION of the shared pixels
# (1% of a 3008 x 3008 image is about 90,000 pixels). This was the only
# estimator once. The brightest pixels are where nonlinearity shows first, so
# on a camera that is not linear there this ratio drifts away from the
# mid-range one. On the Vega session it read 0.65-0.88 between groups of one
# camera, which is what a compressed bright end looks like and not what a gain
# looks like. The pipeline records both ratios and compares them (see
# DEFAULT_GAIN_DISAGREEMENT_TOLERANCE).
GAIN_BRIGHTEST_FRACTION = TunedParameter(
    name="stacking.gain_brightest_fraction",
    default=0.01,
    unit="fraction",
    description="Share of the brightest shared pixels used for the diagnostic bright-end gain.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D10",
)

# How far, in the pixel method, the bright-end ratio may differ from the
# mid-range gain, as a fraction of the gain, before the group is treated as
# nonlinear and left out of the combined image. The star-flux method uses the
# same tolerance on its faint and bright quarters (next comment). A truly
# linear pair of groups agrees to well under 1% when the photon noise is small
# (the unit tests reach 0.5%). 5% allows for noise in the 90,000 bright pixels
# and for small flat-field residuals, and is far below the 35% disagreement
# seen in the field. It is a design choice, not a validated limit. It is the
# processing setting `[Processing.Siril] exposure_group_gain_tolerance`
# (`foundation.config.EXPOSURE_GROUP_GAIN_TOLERANCE`), so an observer can
# change it.
DEFAULT_GAIN_DISAGREEMENT_TOLERANCE = EXPOSURE_GROUP_GAIN_TOLERANCE

# With star fluxes (the star-flux gain below), the same tolerance bounds how
# far the faintest or the brightest quarter of the stars may sit from the
# gain. Measured on 2026-10-13 (the worst quarter's distance from the gain):
# the pairs that combine well read 0.7 to 2.5% (NGC 6888, M 106, NGC 6992,
# NGC 2403, M 81, M 27 L) and 4.5% (M 57 30 s), the quarter medians of 200 to
# 1,500 stars being good to about 1%. M 51's 60 s group, whose bright stars
# read 8% from its gain, is the case it catches. 5% sits between them and is
# still a design choice (see documentation/library_design/validation/
# stacking.md, STK-20).

# A measured gain outside this range is not believed, and the group is left
# out of the combined stack: its scale is unknown, and combining it at an
# assumed gain of 1 weights it wrongly (an IC 1805 group measured 0.245
# against its reference and 14 frames were combined at the wrong scale). The
# measured value is kept for the summary. The gains measured on the Vega
# session were 0.65 to 1 (see
# above), so a group reading four times brighter or dimmer per second than
# the reference is a failed measurement, not a real scale. On Albireo
# (2026-09-25) the two shortest groups, clipped at zero, were measured
# against the reference's brightest pixels, where they hold almost nothing:
# their gains came out 1.4e-18 and 6e-31, dividing by them made the combined
# image peak at 1e29, and only 32 stars were then detected in it. The range
# is a judgement, wide enough for the real spread and far from those failures
# (see documentation/library_design/validation/stacking.md, STK-21). It
# applies to the star-flux gain and to the pixel gain alike.
PLAUSIBLE_GAIN_RANGE = TunedParameter(
    name="stacking.plausible_gain_range",
    default=(0.25, 4.0),
    unit="ratio",
    description=("Range (low, high) of measured gains that are believed; outside it the group is left out."),
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-21",
)

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
# 1.58, so it is a judgement from a thin gap, not a validated limit. The
# ratio is ISO-normalized (`normalize_sky_ratios_by_iso`): the 3.9 and 7.6
# above were raw ratios, and the ISO of those groups was 4 times the
# reference's (M 51 300 s at ISO 800 against ISO 200, IC 1805 300 s the same),
# so the earlier "flooded" evidence was confounded with the ISO (see
# documentation/library_design/validation/stacking.md, STK-21). Only a
# brighter sky is judged: a darker sky is no harm. The sky is compared only
# for imaging stacks (with `use_star_fluxes`).
SKY_RATIO_LIMIT = TunedParameter(
    name="stacking.sky_ratio_limit",
    default=3.0,
    unit="ratio",
    description="Sky brightness ratio to the reference above which a group is left out.",
    role=ParameterRole.JUDGMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-21",
)


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
    clipped_frame_zero_fraction = resolve(CLIPPED_FRAME_ZERO_FRACTION).value
    measured = [
        {"exposure_seconds": float(exposure), "frame_zero_fraction": round(float(zero_fraction), 3)}
        for exposure, zero_fraction in zip(exposures, zero_fractions, strict=True)
    ]
    # Judged on the unrounded value, the same as the clipped flag of the
    # group's summary entry.
    clipped = [
        entry
        for entry, zero_fraction in zip(measured, zero_fractions, strict=True)
        if zero_fraction > clipped_frame_zero_fraction
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
    kept = differences[np.abs(differences - center) <= resolve(NOISE_CLIP_SIGMAS).value * rough_scale]
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
    fallback_level: float | None = None,
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
    if fallback_level is None:
        fallback_level = resolve(SATURATION_FRACTION_OF_FULL_SCALE).value
    ceiling = find_saturation_plateau_ceiling(image)
    if ceiling is None:
        if clip_ceiling_adu is not None and clip_ceiling_adu > 0:
            return fallback_level * min(1.0, clip_ceiling_adu / resolve(FULL_SCALE_COUNTS).value)
        return fallback_level
    return min(fallback_level, ceiling * resolve(SATURATION_MASK_FRACTION_OF_CEILING).value)


def saturated_pixel_mask(
    images: list[np.ndarray],
    saturation_level: float | None = None,
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
    if saturation_level is None:
        saturation_level = resolve(SATURATION_FRACTION_OF_FULL_SCALE).value
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
        sky level; a group left at a gain of 1 is not combined (see
        `gain_rejected`).
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
    clipping_seen : `bool` or `None`
        For the pixel method, `True` when the group's stack or the reference's
        holds a pixel at or above the clip level (see `star_clip_level`) and
        `False` when neither does. For the star method, `False`: a star with
        such a pixel is never measured. `None` when the caller did not pass
        the clip masks. It decides the wording of the reason a `nonlinear`
        group is left out: only a group with clipping seen is said to be
        possibly clipped.
    reference_clipped : `bool`
        `True` when the bright-end ratio of the pixel method read higher than
        the gain by more than the tolerance and the reference holds more
        pixels at or above the clip level than this group does. A clipped
        reference reads too low at its bright end, so the difference belongs
        to the reference and not to this group: `nonlinear` stays `False` and
        `disagreement` is `None`.
    measured_gain : `float` or `None`
        The ratio that was measured, kept even when it was not believed. For
        a group with a believable gain it equals `gain`. For a group whose
        ratio is outside `PLAUSIBLE_GAIN_RANGE` it is that ratio, so the
        summary shows what was measured and not 1.0. `None` when nothing
        could be measured.
    gain_rejected : `bool`
        `True` when a ratio was measured but it is outside
        `PLAUSIBLE_GAIN_RANGE`. Its scale is then unknown, so the group
        should not be combined at an assumed gain of 1
        (`describe_unbelievable_gain_group`). A group with nothing to compare
        (too few shared stars or pixels, or no ratio could be formed) is not
        marked: it keeps `measured` `False` and is still combined as it is,
        because short spectral groups clipped at zero often have nothing to
        compare and would otherwise be lost.
    not_judged_reason : `str` or `None`
        The sentence that says so when `reference_clipped` is set ("not
        judged: the reference group's bright end is clipped (N near-clip
        pixels vs M)", N for the reference and M for this group), else
        `None`. It reaches the group's summary, the gate and the stack's
        warnings, so the choice is never silent.
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
    clipping_seen: bool | None = None
    reference_clipped: bool = False
    measured_gain: float | None = None
    gain_rejected: bool = False
    not_judged_reason: str | None = None


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
    sky_clip_passes = resolve(SKY_CLIP_PASSES).value
    sky_clip_sigmas = resolve(SKY_CLIP_SIGMAS).value
    values = np.asarray(image, dtype=np.float64)[np.asarray(mask, dtype=bool)]
    values = values[np.isfinite(values)]
    if values.size == 0:
        return 0.0
    level = float(np.median(values))
    for _ in range(sky_clip_passes):
        spread = 1.4826 * float(np.median(np.abs(values - level)))
        if spread <= 0:
            break
        kept = values[np.abs(values - level) <= sky_clip_sigmas * spread]
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
    if int(np.count_nonzero(selected)) < resolve(GAIN_MINIMUM_PIXELS).value:
        return None
    ratio = float(np.median(image[selected] / reference[selected]))
    return ratio if np.isfinite(ratio) else None


def measure_group_gains(
    per_second_images: list[np.ndarray],
    usable_masks: list[np.ndarray],
    reference_index: int,
    tolerance: float | None = None,
    only: set[int] | None = None,
    sky_levels: list[float] | None = None,
    near_clip_masks: list[np.ndarray] | None = None,
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

    A clipped group cannot serve as the reference for judging a clean one.
    The longer exposure of a pair clips first and is usually the reference.
    A clipped reference reads too low at its bright end, so the group reads
    higher than the gain there. When `near_clip_masks` is given and the
    reference holds more pixels at or above the clip level than the group
    does, a group that reads higher than the gain at the bright end is not
    marked `nonlinear`: the reference is the compressed one
    (`reference_clipped`). A group that reads lower than the gain is still
    marked, because compression in the group itself shows that way. The
    bright-end ratio keeps every shared pixel: the clip level is half the
    ceiling, and the knee of a real camera lies above it (the ASI533MM Pro
    leaves its linear range near 60,000 of 65,532 ADU), so leaving out the
    pixels above it would hide the nonlinearity the ratio exists to find.

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
    near_clip_masks : `list` [`numpy.ndarray`], optional
        For each group, `True` where its stack is at or above the clip level
        (`star_clip_level`, half the camera's clip ceiling). Without it no
        clipping is seen and no reference counts as clipped.

    Returns
    -------
    measurements : `list` [`GroupGainMeasurement`]
        One entry per group. A group with too few usable pixels in common
        with the reference keeps a gain of 1 and is never marked `nonlinear`.
        A gain outside `PLAUSIBLE_GAIN_RANGE` also keeps a gain of 1 but is
        marked `gain_rejected` with its value in `measured_gain`, so the
        caller leaves it out instead of combining it at an assumed gain of
        1.
    """
    if tolerance is None:
        tolerance = resolve(DEFAULT_GAIN_DISAGREEMENT_TOLERANCE).value
    gain_minimum_pixels = resolve(GAIN_MINIMUM_PIXELS).value
    gain_mid_range_percentiles = resolve(GAIN_MID_RANGE_PERCENTILES).value
    gain_brightest_fraction = resolve(GAIN_BRIGHTEST_FRACTION).value
    plausible_gain_range = resolve(PLAUSIBLE_GAIN_RANGE).value
    if sky_levels is None:
        sky_levels = [
            estimate_sky_level(image, mask)
            for image, mask in zip(per_second_images, usable_masks, strict=True)
        ]
    reference = per_second_images[reference_index] - sky_levels[reference_index]
    reference_noise = estimate_background_noise(per_second_images[reference_index])
    source_floor = (
        resolve(GAIN_SOURCE_SIGMAS).value * reference_noise if np.isfinite(reference_noise) else 0.0
    )
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
        clipping_seen = None
        reference_more_clipped = False
        if near_clip_masks is not None:
            near_clip_counts = (
                int(np.count_nonzero(near_clip_masks[index])),
                int(np.count_nonzero(near_clip_masks[reference_index])),
            )
            clipping_seen = max(near_clip_counts) > 0
            reference_more_clipped = near_clip_counts[1] > near_clip_counts[0]
        shared = (
            usable_masks[index]
            & usable_masks[reference_index]
            & (reference > max(source_floor, 0.0))
            & np.isfinite(image)
        )
        if int(np.count_nonzero(shared)) < gain_minimum_pixels:
            measurements.append(GroupGainMeasurement(sky_level=sky_level))
            continue
        low, high = np.percentile(reference[shared], gain_mid_range_percentiles)
        gain = _median_ratio(image, reference, shared & (reference >= low) & (reference <= high))
        cut = np.quantile(reference[shared], 1.0 - gain_brightest_fraction)
        bright_end_ratio = _median_ratio(image, reference, shared & (reference >= cut))
        if gain is None or not plausible_gain_range[0] <= gain <= plausible_gain_range[1]:
            logger.warning(
                "Exposure group %d measured a brightness gain of %s, outside %s; its scale is unknown.",
                index,
                "nothing" if gain is None else f"{gain:.3g}",
                plausible_gain_range,
            )
            measurements.append(
                GroupGainMeasurement(
                    bright_end_ratio=bright_end_ratio,
                    sky_level=sky_level,
                    measured_gain=gain,
                    gain_rejected=gain is not None,
                )
            )
            continue
        disagreement = None if bright_end_ratio is None else abs(bright_end_ratio - gain) / gain
        not_judged_reason = None
        reference_clipped = bool(
            reference_more_clipped
            and bright_end_ratio is not None
            and bright_end_ratio > gain
            and disagreement is not None
            and disagreement > tolerance
        )
        if reference_clipped:
            logger.warning(
                "Exposure group %d reads %.3f against the reference on its brightest pixels but %.3f on "
                "mid-range pixels, and the reference holds more pixels at the clip level, so the reference's "
                "bright end is the compressed one; the group is not judged by it.",
                index,
                bright_end_ratio,
                gain,
            )
            disagreement = None
            not_judged_reason = (
                "not judged: the reference group's bright end is clipped "
                f"({near_clip_counts[1]} near-clip pixels vs {near_clip_counts[0]})"
            )
        measurements.append(
            GroupGainMeasurement(
                gain=gain,
                bright_end_ratio=bright_end_ratio,
                disagreement=disagreement,
                nonlinear=disagreement is not None and disagreement > tolerance,
                sky_level=sky_level,
                measured=True,
                measured_gain=gain,
                clipping_seen=clipping_seen,
                reference_clipped=reference_clipped,
                not_judged_reason=not_judged_reason,
            )
        )
    return measurements


def describe_nonlinear_group(
    measurement: GroupGainMeasurement,
    reference_exposure_seconds: float,
    tolerance: float | None = None,
) -> str:
    """Say in one sentence why a group's brightness is not one scale factor.

    The sentence says only what was measured. A group whose stack holds pixels
    at or above the clip level (`GroupGainMeasurement.clipping_seen`) is said
    to be possibly clipped. A group with none is not: its ratio to the
    reference changes with star brightness for a reason the numbers do not
    name (for example a reference made from several nights).

    Parameters
    ----------
    measurement : `GroupGainMeasurement`
        A measurement with `nonlinear` set.
    reference_exposure_seconds : `float`
        The exposure length of the group it was compared with.
    tolerance : `float`, optional
        The limit the disagreement was judged against. The configured
        `exposure_group_gain_tolerance` when omitted.

    Returns
    -------
    reason : `str`
        A sentence that continues "the N s exposure group ...", naming the
        ratios that disagree, how far they are from the overall ratio and
        the limit.
    """
    if tolerance is None:
        tolerance = resolve(DEFAULT_GAIN_DISAGREEMENT_TOLERANCE).value
    reference = f"{reference_exposure_seconds:g} s"
    if measurement.method == "stars":
        measured = (
            f"{measurement.faint_ratio:.3f} on the faintest quarter of the {measurement.stars_used} stars "
            f"measured and {measurement.bright_end_ratio:.3f} on the brightest quarter, against "
            f"{measurement.gain:.3f} overall"
        )
        what = "star brightness"
        changes = "changes with star brightness"
    else:
        measured = (
            f"{measurement.gain:.3f} on mid-range pixels and {measurement.bright_end_ratio:.3f} on the "
            "brightest pixels"
        )
        what = "brightness"
        changes = "changes with pixel brightness"
    limit = f"{measurement.disagreement:.1%} from the overall ratio, limit {tolerance * 100:g}%"
    if measurement.clipping_seen:
        ending = (
            "and pixels at or above the clip level are in this group or the reference, "
            "so its bright end may be clipped"
        )
    elif measurement.clipping_seen is False and measurement.method == "stars":
        ending = "and no star measured reaches the clip level"
    elif measurement.clipping_seen is False:
        ending = "and no pixel of either stack reaches the clip level"
    else:
        ending = ""
    reason = (
        f"was left out because one scale factor does not describe it: its {what} ratio to the "
        f"{reference} group {changes} ({measured}; {limit})"
    )
    return f"{reason}, {ending}" if ending else reason


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
        f"{reference_exposure_seconds:g} s group's (limit {resolve(SKY_RATIO_LIMIT).value:g}), "
        "so its frames were taken "
        "under a flooded or foreign sky and its stars cannot be trusted to scale with the reference"
    )


def describe_unbelievable_gain_group(
    measurement: GroupGainMeasurement, reference_exposure_seconds: float
) -> str:
    """Say in one sentence why a group has no believable brightness gain.

    Parameters
    ----------
    measurement : `GroupGainMeasurement`
        A measurement with `gain_rejected` set.
    reference_exposure_seconds : `float`
        The exposure length of the group it was compared with.

    Returns
    -------
    reason : `str`
        A sentence that continues "the N s exposure group ...". It gives the
        measured gain and the believable range, or says that nothing could be
        compared, and that the group's scale is unknown.
    """
    reference = f"{reference_exposure_seconds:g} s"
    if measurement.measured_gain is None:
        return (
            f"was left out: it shares too few stars or pixels with the {reference} group to measure a "
            "brightness gain, so its scale is unknown"
        )
    low, high = resolve(PLAUSIBLE_GAIN_RANGE).value
    return (
        f"was left out: its brightness gain of {measurement.measured_gain:.3g} against the {reference} group "
        f"lies outside the believable range {low:g} to {high:g}, so its scale is unknown"
    )


def is_plausible_gain(gain: float) -> bool:
    """Say whether a measured gain lies inside `PLAUSIBLE_GAIN_RANGE`.

    Returns
    -------
    plausible : `bool`
        `True` when the gain is a finite number inside the range.
    """
    plausible_gain_range = resolve(PLAUSIBLE_GAIN_RANGE).value
    return bool(np.isfinite(gain) and plausible_gain_range[0] <= gain <= plausible_gain_range[1])


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
    sky_ratio_limit = resolve(SKY_RATIO_LIMIT).value
    reference_sky = measurements[reference_index].sky_level
    for index, measurement in enumerate(measurements):
        if index == reference_index or reference_sky <= 0 or measurement.sky_level <= 0:
            continue
        ratio = measurement.sky_level / reference_sky
        measurement.sky_ratio = ratio
        measurement.sky_too_bright = ratio > sky_ratio_limit


def normalize_sky_ratios_by_iso(
    measurements: list[GroupGainMeasurement],
    isos: list[float | None],
    reference_index: int,
) -> None:
    """Divide each group's sky ratio by its ISO ratio to the reference.

    A camera's raw counts scale with its ISO (or gain setting), so a group
    shot at four times the reference's ISO has a four times brighter raw sky
    with no change in the sky itself. Groups of different ISO are kept apart
    (`split_frames_by_exposure`), so the ISO of each group is known here. The
    sky ratio is then the sky per second per unit ISO, and only the excess
    beyond the ISO counts against `SKY_RATIO_LIMIT`. The gain measurement
    needs no such correction, because it measures the actual scale between
    the stacks.

    Parameters
    ----------
    measurements : `list` [`GroupGainMeasurement`]
        As filled by `record_sky_ratios`. `sky_ratio` and `sky_too_bright`
        are changed in place for a group with a ratio.
    isos : `list` [`float` or `None`]
        The ISO of each group. A group, or a reference, with no ISO is left
        as it is.
    reference_index : `int`
        Which entry is the reference.
    """
    reference_iso = isos[reference_index]
    if reference_iso is None or reference_iso <= 0:
        return
    sky_ratio_limit = resolve(SKY_RATIO_LIMIT).value
    for index, measurement in enumerate(measurements):
        iso = isos[index]
        if index == reference_index or measurement.sky_ratio is None or iso is None or iso <= 0:
            continue
        measurement.sky_ratio = measurement.sky_ratio * reference_iso / iso
        measurement.sky_too_bright = measurement.sky_ratio > sky_ratio_limit
