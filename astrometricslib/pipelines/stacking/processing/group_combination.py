"""Combining the exposure groups into one image.

Each group's stack is turned into counts per second, put on the
reference group's brightness scale, and averaged with inverse-variance
weights. A group that is quieter per second counts for more, so long
exposures and frames with low read noise are favoured without assuming
whether the noise comes from the sky or from the camera. This module also
measures each group's frame noise, which the weights need.

The gains come from `group_gains` and `group_star_gains`. The overview of
the whole method is in the `exposure_groups` module docstring.
"""

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from astrometricslib.drivers.fits_access import read_data
from astrometricslib.foundation.constants import FULL_SCALE_COUNTS
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.pipelines.stacking.processing.exposure_groups import ExposureGroup
from astrometricslib.pipelines.stacking.processing.group_gains import (
    CLIPPED_FRAME_ZERO_FRACTION,
    DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    SATURATION_FRACTION_OF_FULL_SCALE,
    GroupGainMeasurement,
    estimate_background_noise,
    estimate_saturation_mask_level,
    measure_group_gains,
)
from astrometricslib.pipelines.stacking.processing.group_star_gains import (
    measure_group_gains_with_stars,
    star_clip_level,
)

logger = logging.getLogger(__name__)


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

# How many raw frames of a group are read to measure the frame noise. The
# noise of a frame hardly varies from one frame to the next (the spread over
# three frames per exposure was under 0.01 counts on the Vega session), so a
# few are enough, and reading a full 3008 x 3008 frame is not free.
FRAMES_SAMPLED_PER_GROUP = 3


def group_weight(count: int, exposure_seconds: float, frame_noise: float) -> float:
    """Work out how much one exposure group counts when groups are combined.

    A group's stack carries information in proportion to its frame count
    times the square of its exposure length, divided by the square of the
    noise of one frame. The same number picks the alignment reference and
    sets the combination weight, so both must come from this one function.

    Parameters
    ----------
    count : `int`
        How many frames the group holds.
    exposure_seconds : `float`
        The exposure length of one frame, in seconds.
    frame_noise : `float`
        The noise of one raw frame, in counts.

    Returns
    -------
    weight : `float`
        ``count * exposure_seconds**2 / frame_noise**2``.
    """
    return count * exposure_seconds**2 / frame_noise**2


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
            group_weight(count, exposure, noise)
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


@dataclass(frozen=True, eq=False)
class ExposureGroupCombination:
    """The combined image and how `combine_exposure_group_images` made it.

    Everything here is what the combination used, so a later step (the frame
    weights, the predicted noise, the choice of empty apertures) can use the
    same numbers instead of measuring them again. Each tuple has one entry per
    group, in the order the groups were given.

    Attributes
    ----------
    image : `numpy.ndarray`
        The combined image, as `float32`.
    gains : `tuple` [`float`]
        The brightness gain applied to each group (1.0 for the reference group
        and for a group whose gain could not be measured).
    gains_measured : `tuple` [`bool`]
        Whether each group's gain was measured. When `False` the group was
        combined as it was and its gain of 1.0 is not a measurement.
    sky_levels : `tuple` [`float`]
        Each group's own sky level in counts per second.
    weights : `tuple` [`float`]
        The weight of each group in the average, set before the gain.
    reference_index : `int`
        The group with the most weight, whose brightness scale and sky the
        others were moved to.
    trusted_masks : `tuple` [`numpy.ndarray`]
        For each group, `True` where its pixels counted in the average (not
        saturated, not outside its covered area, and above the clipping floor
        when its frames are clipped at zero).
    covered_masks : `tuple` [`numpy.ndarray`]
        For each group, `True` where the group holds real data. All `True`
        when no covered masks were given.
    """

    image: np.ndarray
    gains: tuple[float, ...]
    gains_measured: tuple[bool, ...]
    sky_levels: tuple[float, ...]
    weights: tuple[float, ...]
    reference_index: int
    trusted_masks: tuple[np.ndarray, ...]
    covered_masks: tuple[np.ndarray, ...]


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
) -> ExposureGroupCombination:
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
    combination : `ExposureGroupCombination`
        The combined image (as `float32`) with the gain applied to each group,
        whether it was measured, the sky levels, the group weights and the
        trusted and covered masks.

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
    if covered_masks is None:
        group_covered_masks = tuple(np.ones(images[0].shape[-2:], dtype=bool) for _ in images)
    else:
        group_covered_masks = tuple(np.asarray(mask, dtype=bool) for mask in covered_masks)
    return ExposureGroupCombination(
        image=(numerator / denominator * mean_exposure).astype(np.float32),
        gains=tuple(float(gain) for gain in gains),
        gains_measured=tuple(bool(measurement.measured) for measurement in measurements),
        sky_levels=tuple(float(measurement.sky_level) for measurement in measurements),
        weights=tuple(float(weight) for weight in weights),
        reference_index=reference_index,
        trusted_masks=tuple(trusted_masks),
        covered_masks=group_covered_masks,
    )


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
