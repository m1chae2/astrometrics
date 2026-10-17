"""Stacking frames of different exposure lengths without wasting the light.

Some spectroscopy sessions are shot as a bracketed set: many exposure
lengths of the same target, for example 0.5 s, 1 s, 2 s, 3 s and 5 s, so
that a very bright star is not saturated in every frame. Frames like that
must not be stacked as one batch by the usual recipe.

Why not: the recipe first scales every frame to the same noise level
("additive + scaling" normalization) and then throws away pixel values
that stand too far from the rest ("sigma clipping"). With one exposure
length, that is safe. With several, a 5 s frame and a 0.5 s frame hold
very different amounts of light in the same pixel, and the clipping then
discards the best frames' values as if they were outliers. On the Vega
session (140 frames, 0.5-5 s) this made a faint star's spectrum about 8
times noisier than a plain sum of the same frames (measured 2026-09-20,
see documentation/evidence/stack_exposure_groups_20260920.json).

The fix here has three parts:

1. Split the frames into groups that share one exposure length
   (`split_frames_by_exposure`). Each group is stacked on its own, where
   the usual recipe is safe.
2. Turn each group's stack into counts per second, so groups of different
   exposure length can be compared, and put the groups on one brightness
   scale (Siril leaves each stack with its own) after leaving out each
   group's saturated pixels (`estimate_saturation_mask_level`,
   `measure_exposure_group_gains`). For an imaging stack the scale is the
   median ratio of the aperture fluxes of isolated, unsaturated stars
   (`measure_group_gains_with_stars`), and a group whose faintest or
   brightest quarter of stars disagrees with it is left out. A group with
   too few such stars, and every group of a spectral stack, is measured on
   mid-range pixels instead, and left out when its brightest pixels
   disagree (`measure_group_gains`). A group whose sky is far brighter
   than the reference's is also left out (`SKY_RATIO_LIMIT`).
3. Combine the groups with inverse-variance weights, measured from each
   stack's own background noise (`combine_exposure_group_images`). A group
   that is quieter per second counts for more, so long exposures and
   frames with low read noise are favoured automatically, without
   assuming whether the noise comes from the sky or from the camera.
"""

import logging
import math
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from astrometricslib.drivers.fits_access import read_data, write_image
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import measure_aperture_photometry
from astrometricslib.pipelines.shared.quality.saturation import (
    SATURATION_MASK_FRACTION_OF_CEILING,
    find_saturation_plateau_ceiling,
)
from astrometricslib.pipelines.shared.target_sessions import compute_session_night
from astrometricslib.pipelines.stacking.processing.group_alignment import detect_star_centroids

logger = logging.getLogger(__name__)

# A per-frame registration line in Siril's ``.seq`` file starts with R and the
# frame's number (the same pattern `parse_seq_file` reads).
_REGISTRATION_LINE_PATTERN = re.compile(r"^R\d+ ")

# The fewest frames a group of one exposure length may have and still be
# stacked on its own. A group smaller than this cannot be registered or
# clipped reliably (registration needs a reference and other frames to match
# it against, and clipping needs several values per pixel), so its frames are
# added to the group with the nearest exposure length instead. Unvalidated:
# on the Vega session every group has at least 20 frames, and the smallest
# groups in the library (1-2 frames each, from short calibration sets) only
# need to be small enough to be folded away. It has not been tested at 5.
MINIMUM_FRAMES_PER_EXPOSURE_GROUP = 5

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

# What clipping does to a stack. A frame that cannot go below zero turns
# every negative bit of noise into 0, so the average of many such frames sits
# ABOVE the true value wherever the true value is close to zero. For noise
# with a standard deviation s and a true value of m = k*s, the average is too
# high by s * (pdf(k) - k * (1 - cdf(k))) of a standard normal curve: 3% of s
# at k = 1.5, 0.9% of s at k = 2 (0.4% of the value) and under 0.1% of s at
# k = 3. So a clipped group's pixels are trusted only where its stack is at
# least CLIPPING_FLOOR_SIGMAS frame noises above zero, and the other groups
# supply the fainter pixels. 2 leaves under half a percent of bias in what is
# kept. On the Vega session this has been checked only by this arithmetic and
# by the unit tests, not against a clean group.
CLIPPING_FLOOR_SIGMAS = 2.0

# The value a stack pixel has when the frames were full up, as Siril writes
# it: the frames are 16-bit, so a count of 65535 is 1.0. Frame noise is
# measured in counts and the stacks are in these units, so this number joins
# the two. It matches the saturation comment above and holds for the ASI533MM
# Pro frames of the Vega session (14-bit data stored as 16-bit would put every
# value the same factor lower, and the floor with it).
FULL_SCALE_COUNTS = 65535.0

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

# How many raw frames of a group are read to measure the frame noise. The
# noise of a frame hardly varies from one frame to the next (the spread over
# three frames per exposure was under 0.01 counts on the Vega session), so a
# few are enough, and reading a full 3008 x 3008 frame is not free.
FRAMES_SAMPLED_PER_GROUP = 3

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

# Star-flux gains (`measure_group_gains_with_stars`): the way an imaging
# group's brightness scale is measured first. The pixel ratios above are the
# fallback for a group that shares too few clean stars with the reference
# (STAR_GAIN_MINIMUM_STARS) and the method for every spectral stack, which has
# no field of stars. The pixel method above looks at pixels at least
# GAIN_SOURCE_SIGMAS noises above the sky. On a Nikon D5300 stack that floor is
# only 5-8% of the sky level, so those pixels are mostly sky gradients and the
# difference in star width between two nights, and the mid-range and bright-end
# ratios disagree even when the camera is linear (investigated 2026-10-13:
# NGC 6888 60 s read 0.96 mid-range and 1.06 at the bright end, and a real
# aperture measurement of the same stars gave 1.07 at every brightness). So the
# gain is the median ratio of each star's own flux: the light inside a circle
# of STAR_GAIN_APERTURE_RADIUS_PIXELS minus the sky measured in the ring
# between STAR_GAIN_SKY_INNER_PIXELS and STAR_GAIN_SKY_OUTER_PIXELS, from the
# same isolated stars in both groups. A circle that large keeps the same
# fraction of a star's light whether the star is a pixel wider or narrower in
# one group, which the pixel ratio cannot (see
# documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_APERTURE_RADIUS_PIXELS = 14.0
STAR_GAIN_SKY_INNER_PIXELS = 19.0
STAR_GAIN_SKY_OUTER_PIXELS = 25.0

# How many of the reference's brightest stars are looked for. A star is
# isolated when no other detected star lies within the sky ring's outer
# radius, so a neighbour's light does not leak into its sky measurement or
# aperture. The brightest stars are the ones most likely to be clipped, so the
# search is wide enough that plenty of unclipped ones are left.
STAR_GAIN_DETECTION_COUNT = 3000

# A star is measured only when no pixel in its circle or sky ring, in either
# group, reaches STAR_GAIN_CLIP_FRACTION_OF_CEILING of the camera's clip
# ceiling (as a fraction of the stack's 65535 full scale, see
# `star_clip_level`). A stack's clip level cannot be read off the stack: the
# flat field and Siril's normalisation scale every pixel, so the Nikon D5300
# group stacks of NGC 6888 saturate anywhere from 0.21 to 0.38 where the
# camera's 16383 ADU ceiling is 0.25. And a star close to the ceiling is
# already biased: with the clip level swept on NGC 6888, the 120 s group's
# gain read 0.757, 0.774, 0.778, 0.782 and 0.795 for levels of 0.10, 0.15,
# 0.20, 0.237 and 0.34 (stars whose pixels all stay under it), and its
# bright quarter drifted from 0.759 to 0.833 while the faint quarter stayed
# at 0.76. At 0.10 (0.4 of the ceiling) the quarters agree to 0.4%, at 0.15
# (0.6) to 1.3-2.7%. Half the ceiling sits in that quiet range and still
# leaves 1,000 or more unclipped stars on the Nikon fields tried (see the
# tests and documentation of the validation of 2026-10-13).
STAR_GAIN_CLIP_FRACTION_OF_CEILING = 0.5

# A star's flux must stand STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE times above the
# noise of an empty aperture in each group (see
# `_measure_empty_aperture_noise`). Why it matters: a faint star's ratio is
# noisy, and ranking stars by the noisy reference flux makes the faintest
# quarter look too bright. On M 106 (10 frames of 120 s against 51 frames of
# 182 s) the faintest quarter of 1,600 stars read 1.62 against an overall
# 1.137, and in the faintest octiles half the stars' ratios spread over 0.9
# to 2.8. Dropping stars below 10 times the noise removes that bias (see
# documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE = 10.0

# How many empty apertures are measured to find the noise, how far from every
# detected star each must lie (the sky ring's outer radius twice, so no ring
# touches a star's wings), and the seed that keeps the choice repeatable.
STAR_GAIN_EMPTY_APERTURES = 200
STAR_GAIN_NOISE_PROBE_STARS = 50
STAR_GAIN_EMPTY_APERTURE_ATTEMPTS = 3000
STAR_GAIN_EMPTY_APERTURE_SEED = 20261013

# The fewest stars two groups must share (isolated, with no pixel at or above
# the saturation level in the circle or its sky ring, in either group) for
# the star gain to be used, and STAR_GAIN_MINIMUM_STARS_PER_QUARTILE the fewest
# in the faint quarter and the bright quarter for the linearity test. A group
# below either count falls back to the pixel method. The counts are design
# choices (see documentation/library_design/validation/stacking.md, STK-19).
STAR_GAIN_MINIMUM_STARS = 16
STAR_GAIN_MINIMUM_STARS_PER_QUARTILE = 4

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


@dataclass
class ExposureGroup:
    """Frames that share (nearly) one exposure length.

    Attributes
    ----------
    exposure_seconds : `float`
        The mean exposure length of the frames in the group, in seconds.
    frames : `list`
        The frame records in the group, in their original order.
    night : `str` or `None`
        The observing night of the group's frames (``YYYY-MM-DD``, see
        `split_groups_by_night`), or `None` when the group was not split by
        night.
    """

    exposure_seconds: float
    frames: list[Any] = field(default_factory=list)
    night: str | None = None


def frame_exposure_seconds(frame: Any) -> float | None:
    """Read a frame's exposure length in seconds.

    Parameters
    ----------
    frame : `Any`
        A frame record, or a dictionary of one, with an ``exposure`` (a
        number or a string such as ``"0.5"``).

    Returns
    -------
    exposure_seconds : `float` or `None`
        The exposure length, or `None` when it is missing, not a number, or
        not positive.
    """
    raw = frame.get("exposure") if isinstance(frame, dict) else getattr(frame, "exposure", None)
    try:
        value = float(raw)
    except TypeError, ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def split_frames_by_exposure(
    frames: list[Any], minimum_frames_per_group: int = MINIMUM_FRAMES_PER_EXPOSURE_GROUP
) -> list[ExposureGroup]:
    """Split frames into groups that share one exposure length.

    Frames whose exposure lengths match (to three decimal places) go in one
    group. A group with fewer than `minimum_frames_per_group` frames is
    added to the group with the nearest exposure length (nearest in ratio,
    so 2 s is nearer to 1 s than to 5 s). If any frame has no readable
    exposure length, or no group is large enough, everything stays in one
    group, which is the same as stacking the frames together as before.

    Parameters
    ----------
    frames : `list`
        The frame records to split.
    minimum_frames_per_group : `int`, optional
        The fewest frames a group needs to be stacked on its own.

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The groups, shortest exposure first. Always at least one.
    """
    exposures = [frame_exposure_seconds(frame) for frame in frames]
    if not frames or any(exposure is None for exposure in exposures):
        return [ExposureGroup(_mean_exposure(exposures), list(frames))]

    by_exposure: dict[float, list[Any]] = defaultdict(list)
    for frame, exposure in zip(frames, exposures, strict=True):
        # Three significant figures, not three decimal places: the camera's
        # shortest exposure is 0.000032 s, which rounds to 0.0 at three
        # decimals and made the nearest-group ratio below divide by zero.
        by_exposure[float(f"{exposure:.3g}")].append(frame)
    if len(by_exposure) == 1:
        return [ExposureGroup(next(iter(by_exposure)), list(frames))]

    large = {key: members for key, members in by_exposure.items() if len(members) >= minimum_frames_per_group}
    if not large:
        return [ExposureGroup(_mean_exposure(exposures), list(frames))]
    for key, members in by_exposure.items():
        if key in large:
            continue
        # An exposure of exactly 0 has no ratio; a tiny stand-in keeps it
        # nearest to the shortest group instead of raising.
        nearest = min(
            large,
            key=lambda large_key, small_key=key: abs(math.log(max(large_key, 1e-9) / max(small_key, 1e-9))),
        )
        large[nearest].extend(members)

    position = {id(frame): index for index, frame in enumerate(frames)}
    groups = []
    for key in sorted(large):
        members = sorted(large[key], key=lambda frame: position[id(frame)])
        groups.append(
            ExposureGroup(_mean_exposure([frame_exposure_seconds(frame) for frame in members]), members)
        )
    return groups


def _frame_night(frame: Any) -> date | None:
    """Read which observing night a frame belongs to.

    Returns
    -------
    night : `datetime.date` or `None`
        The night (noon to noon, see `compute_session_night`), or `None` when
        the frame has no readable timestamp.
    """
    raw = frame.get("timestamp") if isinstance(frame, dict) else getattr(frame, "timestamp", None)
    try:
        return compute_session_night(float(raw))
    except TypeError, ValueError, OverflowError, OSError:
        return None


def split_groups_by_night(
    groups: list[ExposureGroup], minimum_frames_per_group: int = MINIMUM_FRAMES_PER_EXPOSURE_GROUP
) -> list[ExposureGroup]:
    """Split each exposure group into one group per observing night.

    The star sits on a slightly different pixel each night (a few to tens of
    pixels for a spectroscopy target), and registering frames from two nights
    as one batch can fail for most of them: on the Vega session the 138 frames
    of the 0.5 s group, from two nights, registered only 43% together, while
    each night's frames stacked cleanly alone. Stacking each night on its own
    and letting the group alignment line the nights up (see
    `group_alignment`) keeps every frame.

    A night with fewer than `minimum_frames_per_group` frames of an exposure
    is added to the nearest night's group of that exposure. A group whose
    frames are all from one night, or that has a frame without a readable
    timestamp, is left as it is.

    Parameters
    ----------
    groups : `list` [`ExposureGroup`]
        The groups from `split_frames_by_exposure`.
    minimum_frames_per_group : `int`, optional
        The fewest frames a night needs to be stacked on its own.

    Returns
    -------
    groups : `list` [`ExposureGroup`]
        The groups, shortest exposure first and then earliest night first.
    """
    result: list[ExposureGroup] = []
    for group in groups:
        nights = [_frame_night(frame) for frame in group.frames]
        if any(night is None for night in nights) or len(set(nights)) < 2:
            result.append(group)
            continue
        by_night: dict[date, list[Any]] = defaultdict(list)
        for frame, night in zip(group.frames, nights, strict=True):
            by_night[night].append(frame)
        large = {
            night: members for night, members in by_night.items() if len(members) >= minimum_frames_per_group
        }
        if len(large) < 2:
            result.append(group)
            continue
        for night, members in by_night.items():
            if night not in large:
                nearest = min(
                    large, key=lambda large_night, small_night=night: abs((large_night - small_night).days)
                )
                large[nearest].extend(members)
        position = {id(frame): index for index, frame in enumerate(group.frames)}
        for night in sorted(large):
            members = sorted(large[night], key=lambda frame: position[id(frame)])
            result.append(ExposureGroup(group.exposure_seconds, members, night.isoformat()))
    return result


def _mean_exposure(exposures: list[float | None]) -> float:
    """Average the readable exposure lengths.

    Returns
    -------
    mean_exposure : `float`
        The mean in seconds, or 0.0 when none of the lengths is readable.
    """
    readable = [exposure for exposure in exposures if exposure is not None]
    return float(np.mean(readable)) if readable else 0.0


def _frames_of_representative_exposure(frames: list[Any]) -> list[Any]:
    """Pick the frames that share the most common exposure length.

    Exposure lengths are compared to three significant figures, the same way
    `split_frames_by_exposure` compares them. When two lengths are equally
    common, the one nearest the median exposure of all the frames wins (the
    shorter one if both are equally near).

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    members : `list`
        The frames of the winning length, in their original order. Empty
        when no frame has a readable exposure length.
    """
    readable = [
        (float(f"{exposure:.3g}"), frame)
        for frame in frames
        if (exposure := frame_exposure_seconds(frame)) is not None
    ]
    if not readable:
        return []
    counts: dict[float, int] = defaultdict(int)
    for key, _ in readable:
        counts[key] += 1
    most_frames = max(counts.values())
    median_exposure = float(np.median([key for key, _ in readable]))
    winner = min(
        (key for key, count in counts.items() if count == most_frames),
        key=lambda key: (abs(key - median_exposure), key),
    )
    return [frame for key, frame in readable if key == winner]


def representative_exposure_seconds(frames: list[Any]) -> float:
    """Name the exposure length that best describes a set of frames.

    A group that `split_frames_by_exposure` could not split can still hold a
    few frames of another length: a lone 5 s frame is added to a group of 59
    frames of 0.5 s because a group needs at least
    `MINIMUM_FRAMES_PER_EXPOSURE_GROUP` frames. Reading the first frame would
    then name the group after that stray frame, so the most common length is
    used instead (see `_frames_of_representative_exposure` for ties).

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    exposure_seconds : `float`
        The mean exposure of the frames of the most common length, in
        seconds, or 0.0 when no frame has a readable exposure length.
    """
    members = _frames_of_representative_exposure(frames)
    return _mean_exposure([frame_exposure_seconds(frame) for frame in members])


def representative_exposure_frames(frames: list[Any]) -> list[Any]:
    """Return the frames that have the representative exposure length.

    Used to measure raw-frame clipping on the frames that make up the group,
    not on a stray frame of another length that was added to it.

    Parameters
    ----------
    frames : `list`
        Frame records (or dictionaries of them).

    Returns
    -------
    members : `list`
        The frames of the most common length, or all the frames when none
        has a readable exposure length.
    """
    return _frames_of_representative_exposure(frames) or list(frames)


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
    the stars in the frame. The "median absolute deviation" (the middle of
    the sorted sizes of the differences) makes it robust to the star pixels
    that do get in.

    Parameters
    ----------
    image : `numpy.ndarray`
        A 2-D image.

    Returns
    -------
    noise : `float`
        The typical noise of one pixel, in the image's own units.
    """
    differences = np.asarray(image[:, 1:], dtype=np.float64) - np.asarray(image[:, :-1], dtype=np.float64)
    differences = differences[np.isfinite(differences)]
    if differences.size == 0:
        return float("nan")
    deviation = np.median(np.abs(differences - np.median(differences)))
    # 1.4826 turns a median absolute deviation into a standard deviation for
    # bell-curve noise. Dividing by the square root of 2 undoes the extra
    # noise from taking a difference of two pixels.
    return float(1.4826 * deviation / math.sqrt(2.0))


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


@dataclass
class StarFluxRatios:
    """The star-flux ratios of one group against the reference group.

    Attributes
    ----------
    gain : `float`
        The median of the group's flux per second over the reference's, over
        all the stars used.
    faint_ratio : `float`
        The same median over the faintest quarter of the stars (by reference
        flux).
    bright_ratio : `float`
        The same median over the brightest quarter.
    stars_used : `int`
        How many stars both groups measured cleanly.
    """

    gain: float
    faint_ratio: float
    bright_ratio: float
    stars_used: int


def _photometry_plane(image: np.ndarray) -> np.ndarray:
    """Reduce an image to the one 2-D plane the star fluxes are measured on.

    Returns
    -------
    plane : `numpy.ndarray`
        The image itself when it is 2-D, otherwise the mean over its first
        (colour) axis, as `float32`.
    """
    array = np.asarray(image, dtype=np.float32)
    return array if array.ndim == 2 else array.mean(axis=0)


def _usable_plane(mask: np.ndarray) -> np.ndarray:
    """Reduce a usable-pixel mask to 2-D: a pixel is usable in every colour.

    Returns
    -------
    usable : `numpy.ndarray`
        A 2-D boolean array.
    """
    mask = np.asarray(mask, dtype=bool)
    return mask if mask.ndim == 2 else mask.all(axis=0)


def find_isolated_stars(stars: np.ndarray) -> np.ndarray:
    """Keep the stars that have no detected neighbour nearby.

    Parameters
    ----------
    stars : `numpy.ndarray`
        Star centres, shape ``(n, 2)`` as (row, column), as from
        `detect_star_centroids`.

    Returns
    -------
    isolated : `numpy.ndarray`
        The stars with no other star within `STAR_GAIN_SKY_OUTER_PIXELS` of
        them, so a neighbour's light does not leak into the aperture or the
        sky ring.
    """
    if len(stars) < 2:
        return stars
    neighbour_distance = cKDTree(stars).query(stars, k=2)[0][:, 1]
    return stars[neighbour_distance > STAR_GAIN_SKY_OUTER_PIXELS]


def _measure_star_fluxes(plane: np.ndarray, usable: np.ndarray, stars: np.ndarray) -> np.ndarray:
    """Measure the sky-subtracted flux of each star in one image.

    Parameters
    ----------
    plane : `numpy.ndarray`
        A 2-D image in counts per second.
    usable : `numpy.ndarray`
        2-D, `True` where a pixel can be trusted (covered and below the
        saturation level).
    stars : `numpy.ndarray`
        Star centres, shape ``(n, 2)``, as (row, column).

    Returns
    -------
    fluxes : `numpy.ndarray`
        One flux per star in the image's own units, or NaN for a star with any
        untrusted pixel inside its sky ring's outer radius, which includes the
        aperture. That is how a star with a clipped core is left out.
    """
    reach = math.ceil(STAR_GAIN_SKY_OUTER_PIXELS)
    offsets = np.arange(-reach, reach + 1)
    inside_ring = np.hypot(*np.meshgrid(offsets, offsets)) <= STAR_GAIN_SKY_OUTER_PIXELS
    fluxes = np.full(len(stars), np.nan)
    for index, (row, column) in enumerate(stars):
        center_row, center_column = round(row), round(column)
        window = usable[
            center_row - reach : center_row + reach + 1, center_column - reach : center_column + reach + 1
        ]
        if window.shape != inside_ring.shape or not window[inside_ring].all():
            continue
        measurement = measure_aperture_photometry(
            plane,
            float(column),
            float(row),
            radius=STAR_GAIN_APERTURE_RADIUS_PIXELS,
            annulus_inner=STAR_GAIN_SKY_INNER_PIXELS,
            annulus_outer=STAR_GAIN_SKY_OUTER_PIXELS,
            cutout_radius=reach + 1,
            saturation_threshold_adu=math.inf,
        )
        fluxes[index] = measurement.net_flux_adu
    return fluxes


def _measure_empty_aperture_noise(
    difference_plane: np.ndarray, usable: np.ndarray, stars: np.ndarray, random_generator: np.random.Generator
) -> float:
    """Measure the noise a star's flux ratio carries, from empty apertures.

    The same circle and sky ring as a star's are placed at random positions at
    least twice the sky ring's outer radius from every detected star, on the
    difference of the two groups' images (the group minus the reference times
    the gain), and the flux in each is measured. In that difference the sky
    structure and faint stars that both groups show cancel, and what is left is
    the noise that is not shared, which is what makes a star's ratio uncertain.
    The flux of an empty aperture is noise with a mean of zero (the ring's
    median is subtracted), but `measure_aperture_photometry` reports negative
    fluxes as 0, so the spread is read from the 84.13th percentile of the
    fluxes, which is one standard deviation above the mean of a bell curve and
    is not touched by the zeros.

    Parameters
    ----------
    difference_plane : `numpy.ndarray`
        A 2-D image: the group minus the reference scaled by the gain.
    usable : `numpy.ndarray`
        2-D, `True` where a pixel can be trusted in both groups.
    stars : `numpy.ndarray`
        Detected star centres, shape ``(n, 2)`` as (row, column), which the
        empty apertures stay away from.
    random_generator : `numpy.random.Generator`
        Source of the random positions.

    Returns
    -------
    noise : `float`
        The noise of an aperture flux in the image's units, or NaN when fewer
        than half of `STAR_GAIN_EMPTY_APERTURES` positions could be found.
    """
    margin = math.ceil(STAR_GAIN_SKY_OUTER_PIXELS) + 1
    height, width = difference_plane.shape
    tree = cKDTree(stars) if len(stars) else None
    positions = []
    for _ in range(STAR_GAIN_EMPTY_APERTURE_ATTEMPTS):
        row = random_generator.uniform(margin, height - margin)
        column = random_generator.uniform(margin, width - margin)
        if tree is not None and tree.query([row, column])[0] <= 2.0 * STAR_GAIN_SKY_OUTER_PIXELS:
            continue
        positions.append((row, column))
        if len(positions) >= 4 * STAR_GAIN_EMPTY_APERTURES:
            break
    fluxes = _measure_star_fluxes(difference_plane, usable, np.array(positions).reshape(-1, 2))
    fluxes = fluxes[np.isfinite(fluxes)][:STAR_GAIN_EMPTY_APERTURES]
    if fluxes.size < STAR_GAIN_EMPTY_APERTURES // 2:
        return float("nan")
    return float(np.percentile(fluxes, 84.13))


def measure_star_flux_ratios(
    per_second_images: list[np.ndarray], star_masks: list[np.ndarray], reference_index: int
) -> list[StarFluxRatios | None]:
    """Compare each group's stars with the reference group's, star by star.

    Each isolated star of the reference (`find_isolated_stars`) is measured in
    every group with a fixed-size circle and a sky ring (see
    `STAR_GAIN_APERTURE_RADIUS_PIXELS`). A star counts for a group only when it
    is clean in both that group and the reference and stands at least
    `STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE` above the noise of the two groups'
    difference (`_measure_empty_aperture_noise`; a faint star's ratio is noisy,
    and ranking stars by a noisy flux makes the faint ones look too bright).
    The group's gain is the median flux ratio. The faint and bright quarters of
    the stars (ranked by reference flux) give two more medians: a linear camera
    gives the same ratio for faint and bright stars, while a bright end that is
    compressed or clipped does not.

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second, all the same shape.
    star_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels are covered, not saturated and
        under `star_clip_level`.
    reference_index : `int`
        The group the others are compared with.

    Returns
    -------
    ratios : `list` [`StarFluxRatios` or `None`]
        One entry per group. `None` for the reference and for a group that
        shares fewer than `STAR_GAIN_MINIMUM_STARS` clean stars with it, or
        too few per quarter (`STAR_GAIN_MINIMUM_STARS_PER_QUARTILE`), so the
        caller falls back to another method.
    """
    reference_plane = _photometry_plane(per_second_images[reference_index])
    detected = detect_star_centroids(reference_plane, count=STAR_GAIN_DETECTION_COUNT)
    stars = find_isolated_stars(detected)
    results: list[StarFluxRatios | None] = [None] * len(per_second_images)
    if len(stars) < STAR_GAIN_MINIMUM_STARS:
        return results
    random_generator = np.random.default_rng(STAR_GAIN_EMPTY_APERTURE_SEED)
    reference_usable = _usable_plane(star_masks[reference_index])
    reference_fluxes = _measure_star_fluxes(reference_plane, reference_usable, stars)
    for index, image in enumerate(per_second_images):
        if index == reference_index:
            continue
        plane = _photometry_plane(image)
        usable = _usable_plane(star_masks[index])
        fluxes = _measure_star_fluxes(plane, usable, stars)
        measured = np.isfinite(reference_fluxes) & np.isfinite(fluxes) & (reference_fluxes > 0) & (fluxes > 0)
        if int(np.count_nonzero(measured)) < STAR_GAIN_MINIMUM_STARS:
            continue
        # A first gain from the brightest stars, whose noise hardly matters,
        # lets the difference image be formed.
        brightest = np.flatnonzero(measured)[np.argsort(reference_fluxes[measured])[::-1]]
        brightest = brightest[:STAR_GAIN_NOISE_PROBE_STARS]
        first_gain = float(np.median(fluxes[brightest] / reference_fluxes[brightest]))
        noise = _measure_empty_aperture_noise(
            plane - first_gain * reference_plane, usable & reference_usable, detected, random_generator
        )
        if not np.isfinite(noise) or noise <= 0:
            continue
        clean = measured & (reference_fluxes >= STAR_GAIN_MINIMUM_SIGNAL_TO_NOISE * noise)
        if int(np.count_nonzero(clean)) < STAR_GAIN_MINIMUM_STARS:
            continue
        order = np.argsort(reference_fluxes[clean])
        ratios = (fluxes[clean] / reference_fluxes[clean])[order]
        quarter = len(ratios) // 4
        if quarter < STAR_GAIN_MINIMUM_STARS_PER_QUARTILE:
            continue
        results[index] = StarFluxRatios(
            gain=float(np.median(ratios)),
            faint_ratio=float(np.median(ratios[:quarter])),
            bright_ratio=float(np.median(ratios[-quarter:])),
            stars_used=len(ratios),
        )
    return results


def measure_group_gains_with_stars(
    per_second_images: list[np.ndarray],
    star_masks: list[np.ndarray],
    trusted_masks: list[np.ndarray],
    reference_index: int,
    tolerance: float = DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
) -> list[GroupGainMeasurement]:
    """Measure each group's brightness scale from its stars, else its pixels.

    The gain of a group is the median star-flux ratio (see
    `measure_star_flux_ratios`). The group is marked `nonlinear` when the
    faint-star or bright-star ratio is more than `tolerance` from the gain,
    as a fraction of the gain. The `GroupGainMeasurement` fields keep their old
    names: `bright_end_ratio` is the bright-star ratio and `disagreement` is
    the larger distance of the two quarter ratios from the gain. A group with
    too few clean stars is measured with the pixel ratios of
    `measure_group_gains` instead. A gain outside `PLAUSIBLE_GAIN_RANGE` is
    not believed and the group keeps a gain of 1, as in that function. Every
    group also gets its sky level relative to the reference's (see
    `SKY_RATIO_LIMIT`).

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    star_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels are covered and not saturated.
        The stars are judged on these.
    trusted_masks : `list` [`numpy.ndarray`]
        `star_masks` with a clipped-at-zero group's near-zero pixels removed.
        The sky level and the pixel fallback use these.
    reference_index : `int`
        The group the others are compared with. Its gain is 1.
    tolerance : `float`, optional
        How far the faint-star or bright-star ratio may be from the gain, as
        a fraction of the gain.

    Returns
    -------
    measurements : `list` [`GroupGainMeasurement`]
        One entry per group.
    """
    sky_levels = [
        estimate_sky_level(image, mask) for image, mask in zip(per_second_images, trusted_masks, strict=True)
    ]
    star_ratios = measure_star_flux_ratios(per_second_images, star_masks, reference_index)
    fallback_indices = {
        index for index, ratios in enumerate(star_ratios) if index != reference_index and ratios is None
    }
    if fallback_indices:
        measurements = measure_group_gains(
            per_second_images,
            trusted_masks,
            reference_index,
            tolerance,
            only=fallback_indices,
            sky_levels=sky_levels,
        )
    else:
        measurements = [GroupGainMeasurement(sky_level=sky_level) for sky_level in sky_levels]
        measurements[reference_index] = GroupGainMeasurement(
            gain=1.0,
            bright_end_ratio=1.0,
            disagreement=0.0,
            sky_level=sky_levels[reference_index],
            measured=True,
            method="reference",
        )
    for index, ratios in enumerate(star_ratios):
        if ratios is None:
            continue
        if not _is_plausible_gain(ratios.gain):
            logger.warning(
                "Exposure group %d measured a star-flux gain of %.3g, outside %s; using 1 instead.",
                index,
                ratios.gain,
                PLAUSIBLE_GAIN_RANGE,
            )
            measurements[index] = GroupGainMeasurement(
                bright_end_ratio=ratios.bright_ratio,
                sky_level=sky_levels[index],
                method="stars",
                stars_used=ratios.stars_used,
                faint_ratio=ratios.faint_ratio,
            )
            continue
        disagreement = (
            max(abs(ratios.bright_ratio - ratios.gain), abs(ratios.faint_ratio - ratios.gain)) / ratios.gain
        )
        measurements[index] = GroupGainMeasurement(
            gain=ratios.gain,
            bright_end_ratio=ratios.bright_ratio,
            disagreement=disagreement,
            nonlinear=disagreement > tolerance,
            sky_level=sky_levels[index],
            measured=True,
            method="stars",
            stars_used=ratios.stars_used,
            faint_ratio=ratios.faint_ratio,
        )
    _record_sky_ratios(measurements, reference_index)
    return measurements


def star_clip_level(clip_ceiling_adu: float | None = None) -> float:
    """Give the pixel value above which a star is not trusted for the gain.

    Parameters
    ----------
    clip_ceiling_adu : `float`, optional
        The camera's clip ceiling in ADU (from its camera profile). Without
        it the ceiling is taken to be the 16-bit full scale.

    Returns
    -------
    level : `float`
        `STAR_GAIN_CLIP_FRACTION_OF_CEILING` of the ceiling, as a fraction of
        `FULL_SCALE_COUNTS` (the units of a Siril stack).
    """
    ceiling = FULL_SCALE_COUNTS if clip_ceiling_adu is None or clip_ceiling_adu <= 0 else clip_ceiling_adu
    return STAR_GAIN_CLIP_FRACTION_OF_CEILING * min(1.0, ceiling / FULL_SCALE_COUNTS)


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


def _is_plausible_gain(gain: float) -> bool:
    """Say whether a measured gain lies inside `PLAUSIBLE_GAIN_RANGE`.

    Returns
    -------
    plausible : `bool`
        `True` when the gain is a finite number inside the range.
    """
    return bool(np.isfinite(gain) and PLAUSIBLE_GAIN_RANGE[0] <= gain <= PLAUSIBLE_GAIN_RANGE[1])


def _record_sky_ratios(measurements: list[GroupGainMeasurement], reference_index: int) -> None:
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


def estimate_group_gains(
    per_second_images: list[np.ndarray], usable_masks: list[np.ndarray], reference_index: int
) -> list[float]:
    """Measure each group's brightness scale against a reference group.

    This is the gain half of `measure_group_gains`: the median ratio over the
    mid-range pixels, with the bright-end diagnostic left out.

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    usable_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels can be trusted: not
        saturated and, for a group clipped at zero, above its clipping floor.
    reference_index : `int`
        The group the others are compared with. Its gain is 1.

    Returns
    -------
    gains : `list` [`float`]
        For each group, how many times brighter (per second) it reads than
        the reference. Dividing a group's image by its gain puts it on the
        reference group's scale. A group with too few usable mid-range pixels
        in common with the reference keeps a gain of 1.
    """
    measurements = measure_group_gains(per_second_images, usable_masks, reference_index)
    return [measurement.gain for measurement in measurements]


def _prepare_group_inputs(
    images: list[np.ndarray],
    exposures_seconds: list[float],
    frame_counts: list[int] | None,
    saturation_level: float,
    frame_noises: list[float] | None,
    frame_zero_fractions: list[float] | None,
    covered_masks: list[np.ndarray] | None,
    clip_ceiling_adu: float | None = None,
) -> tuple[list[np.ndarray], list[float], list[np.ndarray], list[np.ndarray], list[np.ndarray]]:
    """Check the group inputs and work out what the combination needs.

    Parameters are those of `combine_exposure_group_images`.

    Returns
    -------
    per_second : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    weights : `list` [`float`]
        Each group's inverse-variance weight.
    usable_masks : `list` [`numpy.ndarray`]
        `True` where a group is neither saturated nor outside its covered
        area.
    trusted_masks : `list` [`numpy.ndarray`]
        `usable_masks` with a clipped-at-zero group's near-zero pixels also
        removed.
    star_masks : `list` [`numpy.ndarray`]
        `usable_masks` with every pixel at or above `star_clip_level` also
        removed. The star fluxes of the gain are measured only where these
        hold.

    Raises
    ------
    InvalidArgumentError
        If the lists do not line up, or an exposure or frame noise is not
        positive.
    ProcessingError
        If a group's background noise cannot be measured.
    """
    if not images or len(images) != len(exposures_seconds):
        raise InvalidArgumentError("Each group needs one image and one exposure length.")
    if any(exposure <= 0 for exposure in exposures_seconds):
        raise InvalidArgumentError("Exposure lengths must be positive.")
    if frame_counts is None:
        frame_counts = [1] * len(images)

    per_second = [
        np.asarray(image, dtype=np.float64) / exposure
        for image, exposure in zip(images, exposures_seconds, strict=True)
    ]
    weights = []
    if frame_noises is not None:
        if len(frame_noises) != len(images) or any(
            not np.isfinite(noise) or noise <= 0 for noise in frame_noises
        ):
            raise InvalidArgumentError("Each group needs one positive frame noise.")
        weights = [
            count * exposure**2 / noise**2
            for count, exposure, noise in zip(frame_counts, exposures_seconds, frame_noises, strict=True)
        ]
    else:
        for index, image in enumerate(per_second):
            noise = estimate_background_noise(image)
            if not np.isfinite(noise) or noise <= 0:
                raise ProcessingError(f"The background noise of exposure group {index} cannot be measured.")
            weights.append(1.0 / noise**2)

    # Each group is saturated above its own ceiling, which is not the same
    # for every group (see SATURATION_CEILING_SAMPLE_PIXELS).
    usable_masks = [
        np.asarray(raw) < estimate_saturation_mask_level(raw, saturation_level, clip_ceiling_adu)
        for raw in images
    ]
    if covered_masks is not None:
        if len(covered_masks) != len(images):
            raise InvalidArgumentError("Each group needs one covered mask.")
        usable_masks = [usable & covered for usable, covered in zip(usable_masks, covered_masks, strict=True)]
    trusted_masks = list(usable_masks)
    if frame_noises is not None and frame_zero_fractions is not None:
        if len(frame_zero_fractions) != len(images):
            raise InvalidArgumentError("Each group needs one zero fraction.")
        for index, (raw, zero_fraction) in enumerate(zip(images, frame_zero_fractions, strict=True)):
            if zero_fraction > CLIPPED_FRAME_ZERO_FRACTION:
                floor = CLIPPING_FLOOR_SIGMAS * frame_noises[index] / FULL_SCALE_COUNTS
                trusted_masks[index] = usable_masks[index] & (np.asarray(raw) >= floor)
    star_level = star_clip_level(clip_ceiling_adu)
    star_masks = [
        usable & (np.asarray(raw) < star_level) for usable, raw in zip(usable_masks, images, strict=True)
    ]
    return per_second, weights, usable_masks, trusted_masks, star_masks


def measure_exposure_group_gains(
    images: list[np.ndarray],
    exposures_seconds: list[float],
    frame_counts: list[int] | None = None,
    saturation_level: float = SATURATION_FRACTION_OF_FULL_SCALE,
    frame_noises: list[float] | None = None,
    frame_zero_fractions: list[float] | None = None,
    covered_masks: list[np.ndarray] | None = None,
    tolerance: float = DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    clip_ceiling_adu: float | None = None,
    use_star_fluxes: bool = True,
) -> tuple[int, list[GroupGainMeasurement]]:
    """Measure the gain of every group exactly as the combination will.

    This builds the same per-second images, weights and trusted-pixel masks as
    `combine_exposure_group_images` and runs `measure_group_gains_with_stars`
    on them (or `measure_group_gains` when `use_star_fluxes` is `False`). A
    caller uses it before combining, to leave out the groups marked
    `nonlinear`. Parameters are those of `combine_exposure_group_images`, plus
    `tolerance`. Lists that do not line up, or an exposure or frame noise that
    is not positive, raise `InvalidArgumentError`. A group whose background
    noise cannot be measured raises `ProcessingError`.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The stacked image of each group, all the same shape.
    exposures_seconds : `list` [`float`]
        The exposure length of each group, in seconds.
    frame_counts : `list` [`int`], optional
        How many frames each group holds.
    saturation_level : `float`, optional
        The pixel value at or above which a group counts as saturated when
        its stack shows no measurable ceiling.
    frame_noises : `list` [`float`], optional
        The noise of one raw frame of each group, in counts.
    frame_zero_fractions : `list` [`float`], optional
        The fraction of pixels at zero in the raw frames of each group.
    covered_masks : `list` [`numpy.ndarray`], optional
        For each group, a 2-D mask of the pixels that hold real data.
    tolerance : `float`, optional
        How far the faint-star or bright-star ratio may be from the gain (the
        bright-end pixel ratio on the pixel method), as a fraction of the
        gain, before the group is marked `nonlinear`.
    clip_ceiling_adu : `float`, optional
        The camera's clip ceiling in ADU, used to set the saturation level of
        a group whose stack shows no plateau (see
        `estimate_saturation_mask_level`).
    use_star_fluxes : `bool`, optional
        When `False`, the gains are measured with the pixel ratios only and
        the sky is not compared. A slitless spectrum has no field of stars to
        measure, and its short groups are clipped at zero, so their sky per
        second is not a sky brightness.

    Returns
    -------
    reference_index : `int`
        The group the others were compared with (the one with the most
        weight).
    measurements : `list` [`GroupGainMeasurement`]
        One entry per group.

    """
    per_second, weights, _, trusted_masks, star_masks = _prepare_group_inputs(
        images,
        exposures_seconds,
        frame_counts,
        saturation_level,
        frame_noises,
        frame_zero_fractions,
        covered_masks,
        clip_ceiling_adu,
    )
    reference_index = int(np.argmax(weights))
    if not use_star_fluxes:
        return reference_index, measure_group_gains(per_second, trusted_masks, reference_index, tolerance)
    return reference_index, measure_group_gains_with_stars(
        per_second, star_masks, trusted_masks, reference_index, tolerance
    )


def combine_exposure_group_images(
    images: list[np.ndarray],
    exposures_seconds: list[float],
    frame_counts: list[int] | None = None,
    saturation_level: float = SATURATION_FRACTION_OF_FULL_SCALE,
    frame_noises: list[float] | None = None,
    frame_zero_fractions: list[float] | None = None,
    covered_masks: list[np.ndarray] | None = None,
    clip_ceiling_adu: float | None = None,
    use_star_fluxes: bool = True,
) -> np.ndarray:
    """Combine one stacked image per exposure group into a single image.

    Each image is first divided by its exposure length to give counts per
    second. The images are then averaged with weights of one over the
    square of each group's noise in counts per second. That noise is either
    predicted from the raw frames (when `frame_noises` is given: a stack of
    N frames of exposure t and frame noise s has a noise of s / (t sqrt(N))
    per second, so its weight is N t^2 / s^2) or measured from the stack image
    itself. Where a group's stack is saturated (above a ceiling measured from
    that stack, see `estimate_saturation_mask_level`), that group is left out
    at that pixel; if every group is saturated there, the shortest exposure is
    used. A group whose raw frames are clipped at zero is likewise left out at
    pixels too close to zero to be trusted (see `CLIPPING_FLOOR_SIGMAS`); if
    that leaves no group at a pixel, the clipped group is used after all.
    Before averaging, each group is put on the same brightness scale as
    the group with the most weight (see `measure_group_gains_with_stars`, which
    measures the scale on star fluxes and falls back to the mid-range pixels
    of `measure_group_gains`), because Siril leaves every group stack
    with its own overall scale. This function applies the scale to every group
    it is given. A caller that wants to refuse groups whose brightness is not
    one scale factor checks them first with `measure_exposure_group_gains`.
    The input errors are the same as for that function.

    The result is scaled back up by the average exposure length per frame,
    so its brightness is comparable with a normal stack of the same frames.

    Parameters
    ----------
    images : `list` [`numpy.ndarray`]
        The stacked image of each group, all the same shape.
    exposures_seconds : `list` [`float`]
        The exposure length of each group, in seconds.
    frame_counts : `list` [`int`], optional
        How many frames each group holds, used to set the output brightness.
        Defaults to equal counts.
    saturation_level : `float`, optional
        The pixel value at or above which a group counts as saturated when
        its stack shows no measurable ceiling.
    frame_noises : `list` [`float`], optional
        The noise of one raw frame of each group, in counts. When given, the
        weights come from these and `frame_counts` instead of from the stack
        images.
    frame_zero_fractions : `list` [`float`], optional
        The fraction of pixels at zero in the raw frames of each group.
        With `frame_noises`, a group above `CLIPPED_FRAME_ZERO_FRACTION` is
        left out at pixels less than `CLIPPING_FLOOR_SIGMAS` frame noises
        above zero, where clipping biases its average upward.
    covered_masks : `list` [`numpy.ndarray`], optional
        For each group, a 2-D mask of the pixels that hold real data. When the
        group stacks have been moved to line up (see
        `stacking/processing/group_alignment.py`), the border the move
        filled with zeros is left out. A group is left out at pixels its
        mask marks as empty.
    clip_ceiling_adu : `float`, optional
        The camera's clip ceiling in ADU, used to set the saturation level of
        a group whose stack shows no plateau (see
        `estimate_saturation_mask_level`).
    use_star_fluxes : `bool`, optional
        When `False`, the gains are measured with the pixel ratios only and
        the sky is not compared. A slitless spectrum has no field of stars to
        measure, and its short groups are clipped at zero, so their sky per
        second is not a sky brightness.

    Returns
    -------
    combined : `numpy.ndarray`
        The combined image, as `float32`.

    """
    if frame_counts is None:
        frame_counts = [1] * len(images)
    per_second, weights, usable_masks, trusted_masks, star_masks = _prepare_group_inputs(
        images,
        exposures_seconds,
        frame_counts,
        saturation_level,
        frame_noises,
        frame_zero_fractions,
        covered_masks,
        clip_ceiling_adu,
    )
    # Put every group on the brightness scale of the group with the most
    # weight. The weights are left as they are: Siril's rescaling multiplies a
    # stack's signal and its noise alike, so a gain removes the same factor
    # from both and the signal-to-noise of each group is unchanged.
    reference_index = int(np.argmax(weights))
    if use_star_fluxes:
        measurements = measure_group_gains_with_stars(per_second, star_masks, trusted_masks, reference_index)
    else:
        measurements = measure_group_gains(per_second, trusted_masks, reference_index)
    gains = [measurement.gain for measurement in measurements]
    logger.info(
        "Exposure groups combined with brightness gains %s (bright-star or bright-end ratios %s; "
        "measured on %s) relative to the %g s group.",
        [round(gain, 3) for gain in gains],
        [None if m.bright_end_ratio is None else round(m.bright_end_ratio, 3) for m in measurements],
        [m.method for m in measurements],
        exposures_seconds[reference_index],
    )
    # A group with a measured gain has its stars put on the reference's scale
    # and its sky moved to the reference's sky level, so groups from nights
    # of different sky brightness do not leave steps where the weights
    # change. A group whose gain could not be measured is used as it is.
    reference_sky = measurements[reference_index].sky_level
    per_second = [
        (image - measurement.sky_level) / measurement.gain + reference_sky if measurement.measured else image
        for image, measurement in zip(per_second, measurements, strict=True)
    ]

    numerator = np.zeros_like(per_second[0])
    denominator = np.zeros_like(per_second[0])
    for index, image in enumerate(per_second):
        pixel_weight = weights[index] * trusted_masks[index]
        numerator += pixel_weight * image
        denominator += pixel_weight
    # Where only clipped groups had a pixel below their floor, use them
    # anyway (their value is biased but it is a measurement); saturation is
    # dealt with after that.
    nothing_trusted = denominator == 0
    if nothing_trusted.any():
        for index, image in enumerate(per_second):
            pixel_weight = weights[index] * usable_masks[index]
            numerator[nothing_trusted] += (pixel_weight * image)[nothing_trusted]
            denominator[nothing_trusted] += pixel_weight[nothing_trusted]
    all_saturated = denominator == 0
    if all_saturated.any():
        # Every group is saturated (or not covered) here: use the shortest
        # exposure that has data at the pixel.
        for index in np.argsort(exposures_seconds):
            covered = (
                np.ones(all_saturated.shape[-2:], dtype=bool)
                if covered_masks is None
                else covered_masks[index]
            )
            fill = all_saturated & (denominator == 0) & covered
            numerator[fill] = per_second[index][fill]
            denominator[fill] = 1.0
        # Nothing at all covers these pixels (only possible outside every
        # group's field): leave them at zero.
        denominator[denominator == 0] = 1.0

    mean_exposure = float(np.average(exposures_seconds, weights=frame_counts))
    return (numerator / denominator * mean_exposure).astype(np.float32)


def _frame_path(frame: Any) -> str:
    """Read a frame's file path.

    Returns
    -------
    path : `str`
        The path from a frame record or a dictionary of one.

    Raises
    ------
    OSError
        If the frame has no path, the same as if its file cannot be read.
    """
    path = frame.get("path") if isinstance(frame, dict) else getattr(frame, "path", None)
    if not path:
        raise OSError("A frame has no file path.")
    return str(path)


def measure_frame_noise(frame_path: str) -> tuple[float, float]:
    """Measure the noise of one raw frame and how much of it is clipped.

    Parameters
    ----------
    frame_path : `str`
        A FITS file.

    Returns
    -------
    noise : `float`
        The typical noise of one pixel, in counts (see
        `estimate_background_noise`).
    zero_fraction : `float`
        The fraction of pixels at exactly zero, which is high when the frame
        is clipped at zero.
    """
    image = np.asarray(read_data(frame_path), dtype=np.float64)
    return estimate_background_noise(image), float(np.mean(image == 0))


def group_frame_noises(groups: list[ExposureGroup]) -> list[float]:
    """Work out the noise of one raw frame in each exposure group.

    This is the first result of `measure_group_frames`; see it for details.

    Returns
    -------
    noises : `list` [`float`]
        The noise to use for one frame of each group, in counts.
    """
    return measure_group_frames(groups)[0]


def measure_group_frames(groups: list[ExposureGroup]) -> tuple[list[float], list[float]]:
    """Measure the noise and the clipping of the raw frames of each group.

    A few frames of each group are read. For a group whose frames are
    clipped at zero (see `CLIPPED_FRAME_ZERO_FRACTION`) the measured noise
    is too low, because the negative side of the noise was cut off. Read
    noise does not shrink when the exposure gets shorter, so such a group
    is given the largest noise measured in a group that is not clipped. If
    no group is clean, every group gets the same noise (the weights then
    depend only on how many frames each group has and how long they are).
    A frame that has no path or cannot be read raises `OSError`; the caller
    decides what to do about it.

    Parameters
    ----------
    groups : `list` [`ExposureGroup`]
        The exposure groups, whose frames have a ``path`` (or dictionary
        key ``"path"``).

    Returns
    -------
    noises : `list` [`float`]
        The noise to use for one frame of each group, in counts.
    zero_fractions : `list` [`float`]
        The average fraction of pixels at exactly zero in the sampled frames
        of each group. Above `CLIPPED_FRAME_ZERO_FRACTION` means the group's
        frames are clipped at zero.
    """
    measured = []
    for group in groups:
        paths = [_frame_path(frame) for frame in group.frames]
        step = max(1, len(paths) // FRAMES_SAMPLED_PER_GROUP)
        samples = [measure_frame_noise(path) for path in paths[::step][:FRAMES_SAMPLED_PER_GROUP]]
        measured.append((
            float(np.median([noise for noise, _ in samples])),
            float(np.mean([zeros for _, zeros in samples])),
        ))
    clean = [
        noise
        for noise, zero_fraction in measured
        if zero_fraction <= CLIPPED_FRAME_ZERO_FRACTION and noise > 0
    ]
    zero_fractions = [zero_fraction for _, zero_fraction in measured]
    if not clean:
        return [1.0] * len(groups), zero_fractions
    reference = max(clean)
    noises = [
        noise if zero_fraction <= CLIPPED_FRAME_ZERO_FRACTION and noise > 0 else reference
        for noise, zero_fraction in measured
    ]
    return noises, zero_fractions


def merge_rejection_maps(
    map_paths: list[str],
    frame_counts: list[int],
    output_path: str,
    header: Any | None = None,
    shifts: list[tuple[float, float] | None] | None = None,
) -> bool:
    """Combine each group's rejection map into one map.

    A rejection map holds, for each pixel, the fraction of that pixel's
    values that the stacker threw away. The combined map is the average of
    the groups' maps weighted by how many frames each group stacked, which
    is the fraction thrown away overall.

    Parameters
    ----------
    map_paths : `list` [`str`]
        The rejection map file of each group. Groups without one are
        skipped.
    frame_counts : `list` [`int`]
        The number of frames each group stacked, in the same order.
    output_path : `str`
        Where to write the combined map.
    header : `astropy.io.fits.Header`, optional
        A FITS header to give the combined map.
    shifts : `list` [`tuple` [`float`, `float`] or `None`], optional
        For each group, the (rows, columns) shift that lined its stack up with
        the reference group. The group's map is moved the same way, so the
        maps line up with the combined image. `None` for a group that was not
        moved.

    Returns
    -------
    written : `bool`
        `True` when a map was written, `False` if no group had one.
    """
    from astrometricslib.pipelines.stacking.processing.group_alignment import apply_shift

    total = None
    weight_sum = 0.0
    for position, (path, count) in enumerate(zip(map_paths, frame_counts, strict=True)):
        if not path or not os.path.exists(path):
            continue
        data = np.asarray(read_data(path), dtype=np.float64)
        shift = shifts[position] if shifts is not None else None
        if shift is not None:
            data = apply_shift(data, shift[0], shift[1], order=1)[0].astype(np.float64)
        total = data * count if total is None else total + data * count
        weight_sum += count
    if total is None or weight_sum == 0:
        return False
    write_image(output_path, (total / weight_sum).astype(np.float32), header)
    return True


def merge_registration_sequences(sequence_paths: list[str], output_path: str) -> bool:
    """Join the groups' registration sequence files into one.

    Only the per-frame registration lines are kept, in group order. Those
    are the lines the stack quality checks read (see `parse_seq_file`).

    Parameters
    ----------
    sequence_paths : `list` [`str`]
        Each group's registration ``.seq`` file, in group order.
    output_path : `str`
        Where to write the joined file.

    Returns
    -------
    written : `bool`
        `True` when at least one registration line was written.
    """
    lines = []
    for path in sequence_paths:
        if not path or not os.path.exists(path):
            continue
        with open(path) as sequence_file:
            lines.extend(line for line in sequence_file if _REGISTRATION_LINE_PATTERN.match(line))
    if not lines:
        return False
    with open(output_path, "w") as output_file:
        output_file.writelines(lines)
    return True
