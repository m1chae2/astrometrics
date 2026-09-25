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
see logs/stack_exposure_groups_20260920.json).

The fix here has three parts:

1. Split the frames into groups that share one exposure length
   (`split_frames_by_exposure`). Each group is stacked on its own, where
   the usual recipe is safe.
2. Turn each group's stack into counts per second, so groups of different
   exposure length can be compared, and put the groups on one brightness
   scale (Siril leaves each stack with its own) after leaving out each
   group's saturated pixels (`estimate_saturation_mask_level`,
   `estimate_group_gains`).
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
from typing import Any

import numpy as np

from astrometricslib.drivers.fits_access import read_data, write_image
from astrometricslib.pipelines.shared.quality.saturation import (
    SATURATION_MASK_FRACTION_OF_CEILING,
    find_saturation_plateau_ceiling,
)

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

# Each group's stack also carries its own overall brightness scale. Against
# the 2 s group, the Vega session's per-second brightness was 0.84-0.88 (1 s),
# 0.71-0.74 (3 s) and 0.65-0.71 (5 s), almost the same for faint and bright
# pixels, so it is a scale factor (a gain), not an offset. Mixing groups at
# their face value puts a step in the spectrum wherever the light source
# switches from one group to another. The gain of each group is therefore
# measured against the group with the highest weight, as the median ratio of
# their per-second brightness over the brightest pixels that neither group
# saturates (the top GAIN_BRIGHTEST_FRACTION of them: high signal, so noise
# barely biases the ratio). Fewer than GAIN_MINIMUM_PIXELS such pixels leaves
# the gain at 1. The fraction and the pixel count are unvalidated choices:
# 1% of a 3008 x 3008 image is about 90,000 pixels.
GAIN_BRIGHTEST_FRACTION = 0.01
GAIN_MINIMUM_PIXELS = 200


@dataclass
class ExposureGroup:
    """Frames that share (nearly) one exposure length.

    Attributes
    ----------
    exposure_seconds : `float`
        The mean exposure length of the frames in the group, in seconds.
    frames : `list`
        The frame records in the group, in their original order.
    """

    exposure_seconds: float
    frames: list[Any] = field(default_factory=list)


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
        by_exposure[round(exposure, 3)].append(frame)
    if len(by_exposure) == 1:
        return [ExposureGroup(next(iter(by_exposure)), list(frames))]

    large = {key: members for key, members in by_exposure.items() if len(members) >= minimum_frames_per_group}
    if not large:
        return [ExposureGroup(_mean_exposure(exposures), list(frames))]
    for key, members in by_exposure.items():
        if key in large:
            continue
        nearest = min(large, key=lambda large_key, small_key=key: abs(math.log(large_key / small_key)))
        large[nearest].extend(members)

    position = {id(frame): index for index, frame in enumerate(frames)}
    groups = []
    for key in sorted(large):
        members = sorted(large[key], key=lambda frame: position[id(frame)])
        groups.append(
            ExposureGroup(_mean_exposure([frame_exposure_seconds(frame) for frame in members]), members)
        )
    return groups


def _mean_exposure(exposures: list[float | None]) -> float:
    """Average the readable exposure lengths.

    Returns
    -------
    mean_exposure : `float`
        The mean in seconds, or 0.0 when none of the lengths is readable.
    """
    readable = [exposure for exposure in exposures if exposure is not None]
    return float(np.mean(readable)) if readable else 0.0


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
    image: np.ndarray, fallback_level: float = SATURATION_FRACTION_OF_FULL_SCALE
) -> float:
    """Find the pixel value above which one group's stack is saturated.

    Saturated pixels pile up at a ceiling, so a saturated stack has many
    pixels at almost the same, highest value. If the image shows such a
    pile, the mask level is a little below its value; if not, the fixed
    fallback level is used.

    Parameters
    ----------
    image : `numpy.ndarray`
        One group's stacked image.
    fallback_level : `float`, optional
        The level to use when no ceiling can be seen.

    Returns
    -------
    level : `float`
        Pixels at or above this value should be treated as saturated.
    """
    ceiling = find_saturation_plateau_ceiling(image)
    if ceiling is None:
        return fallback_level
    return min(fallback_level, ceiling * SATURATION_MASK_FRACTION_OF_CEILING)


def estimate_group_gains(
    per_second_images: list[np.ndarray], usable_masks: list[np.ndarray], reference_index: int
) -> list[float]:
    """Measure each group's brightness scale against a reference group.

    Parameters
    ----------
    per_second_images : `list` [`numpy.ndarray`]
        Each group's image in counts per second.
    usable_masks : `list` [`numpy.ndarray`]
        For each group, `True` where its pixels are not saturated.
    reference_index : `int`
        The group the others are compared with. Its gain is 1.

    Returns
    -------
    gains : `list` [`float`]
        For each group, how many times brighter (per second) it reads than
        the reference. Dividing a group's image by its gain puts it on the
        reference group's scale. A group with too few usable bright pixels
        in common with the reference keeps a gain of 1.
    """
    reference = per_second_images[reference_index]
    gains = []
    for index, image in enumerate(per_second_images):
        if index == reference_index:
            gains.append(1.0)
            continue
        shared = usable_masks[index] & usable_masks[reference_index] & (reference > 0)
        if int(np.count_nonzero(shared)) < GAIN_MINIMUM_PIXELS:
            gains.append(1.0)
            continue
        cut = np.quantile(reference[shared], 1.0 - GAIN_BRIGHTEST_FRACTION)
        bright = shared & (reference >= cut)
        if int(np.count_nonzero(bright)) < GAIN_MINIMUM_PIXELS:
            gains.append(1.0)
            continue
        gain = float(np.median(image[bright] / reference[bright]))
        gains.append(gain if np.isfinite(gain) and gain > 0 else 1.0)
    return gains


def combine_exposure_group_images(
    images: list[np.ndarray],
    exposures_seconds: list[float],
    frame_counts: list[int] | None = None,
    saturation_level: float = SATURATION_FRACTION_OF_FULL_SCALE,
    frame_noises: list[float] | None = None,
    frame_zero_fractions: list[float] | None = None,
    covered_masks: list[np.ndarray] | None = None,
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
    the group with the most weight (see `estimate_group_gains`), because
    Siril leaves every group stack with its own overall scale.

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
        `pipelines/stacking/group_alignment.py`), the border the move filled
        with zeros is left out. A group is left out at pixels its mask marks
        as empty.

    Returns
    -------
    combined : `numpy.ndarray`
        The combined image, as `float32`.

    Raises
    ------
    ValueError
        If the lists do not line up, an exposure is not positive, or a
        group's noise cannot be measured.
    """
    if not images or len(images) != len(exposures_seconds):
        raise ValueError("Each group needs one image and one exposure length.")
    if any(exposure <= 0 for exposure in exposures_seconds):
        raise ValueError("Exposure lengths must be positive.")
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
            raise ValueError("Each group needs one positive frame noise.")
        weights = [
            count * exposure**2 / noise**2
            for count, exposure, noise in zip(frame_counts, exposures_seconds, frame_noises, strict=True)
        ]
    else:
        for index, image in enumerate(per_second):
            noise = estimate_background_noise(image)
            if not np.isfinite(noise) or noise <= 0:
                raise ValueError(f"The background noise of exposure group {index} cannot be measured.")
            weights.append(1.0 / noise**2)

    # Each group is saturated above its own ceiling, which is not the same
    # for every group (see SATURATION_CEILING_SAMPLE_PIXELS).
    usable_masks = [np.asarray(raw) < estimate_saturation_mask_level(raw, saturation_level) for raw in images]
    if covered_masks is not None:
        if len(covered_masks) != len(images):
            raise ValueError("Each group needs one covered mask.")
        usable_masks = [usable & covered for usable, covered in zip(usable_masks, covered_masks, strict=True)]
    trusted_masks = list(usable_masks)
    if frame_noises is not None and frame_zero_fractions is not None:
        if len(frame_zero_fractions) != len(images):
            raise ValueError("Each group needs one zero fraction.")
        for index, (raw, zero_fraction) in enumerate(zip(images, frame_zero_fractions, strict=True)):
            if zero_fraction > CLIPPED_FRAME_ZERO_FRACTION:
                floor = CLIPPING_FLOOR_SIGMAS * frame_noises[index] / FULL_SCALE_COUNTS
                trusted_masks[index] = usable_masks[index] & (np.asarray(raw) >= floor)
    # Put every group on the brightness scale of the group with the most
    # weight. The weights are left as they are: Siril's rescaling multiplies a
    # stack's signal and its noise alike, so a gain removes the same factor
    # from both and the signal-to-noise of each group is unchanged.
    reference_index = int(np.argmax(weights))
    gains = estimate_group_gains(per_second, usable_masks, reference_index)
    logger.info(
        "Exposure groups combined with brightness gains %s relative to the %g s group.",
        [round(gain, 3) for gain in gains],
        exposures_seconds[reference_index],
    )
    per_second = [image / gain for image, gain in zip(per_second, gains, strict=True)]

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
    from astrometricslib.pipelines.stacking.group_alignment import apply_shift

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
