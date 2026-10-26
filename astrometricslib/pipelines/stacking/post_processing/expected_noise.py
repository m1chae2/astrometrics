"""Works out the noise a stack should have in empty sky, from its frames.

A stack's measured noise in empty apertures (see `stack_noise.py`) says how
noisy the stack is. This module says how noisy it should be if the only noise
were the light of the sky (photon noise) and the camera's read noise. The two
numbers are stored side by side. Their ratio is never stored: a reader divides
them, and the parts stay visible.

The model, one frame at a time. Frame ``i`` has ``S_i`` sky electrons in a
pixel (the sky rate times the exposure), a read noise of ``R_i`` electrons and
a gain of ``g_i`` electrons per ADU (ADU, analog-to-digital unit, is the
number the camera stores). Photon noise is Poisson, so its variance is ``S_i``
electrons squared. The variance in ADU squared is ``(S_i + R_i^2) / g_i^2``.

The stack is a weighted mean, ``sum(w_i x_i) / sum(w_i)``. Noise of different
frames is independent, so one stack pixel has the variance
``sum(w_i^2 v_i) / (sum w_i)^2``. The sky part and the read part are summed
separately, so they can be stored apart.

From one pixel to an aperture. The stack's noise is the spread of sums over a
circle of radius 4 px minus the sky taken from a ring around it. With
independent pixels the circle's sum has the variance of one pixel times
``sum(d^2)``, where ``d`` is each pixel's overlap with the circle (a
partly covered pixel counts for less). That piece is about 46 px. The ring's
median is itself noisy: for ``n`` independent pixels with bell-curve noise the
median has a variance of about ``(pi / 2) / n`` times the pixel variance (the
``pi / 2`` assumes Gaussian noise in the ring), and it is subtracted
``sum(d)`` times. That piece is about 13 px. Together they give
`effective_aperture_area_px`, about 59 px: the number that one pixel's
variance is multiplied by.

From electrons to the stack's units. Siril divides each frame by 65535 when it
reads it and writes the stack as numbers near 0 to 1. It does so for any
integer frame, so a 14-bit raw frame (stored without scaling up) is divided by
65535 too, and one camera ADU is ``1 / 65535`` stack unit
(`STACK_UNITS_PER_ADU`, from `exposure_groups.FULL_SCALE_COUNTS`). A frame
stored as floating-point numbers would not follow this, and nothing here
checks for one. The noise is converted with that factor and the factor is
stored. The check is the stack's own sky: the weighted mean of the frames'
sky, in the same units, must be close to the median of the stack. Both
numbers are stored (`ExpectedNoise.predicted_sky_level` and the stack's sky
level), so a reader can repeat the check on every stack.

A stack of several exposure groups. The combine divides each group's stack by
its exposure (counts per second), puts it on the reference group's brightness
scale with a gain ``G_g``, averages the groups with the weights ``W_g`` and
multiplies the result by ``t_bar``, the mean exposure per frame. Frame ``i`` of
group ``g`` therefore enters with the coefficient ``a_i = W_g w_i / (W G_g)``
in counts per second, where ``w_i`` is its weight inside the group (the
weights of a group add to 1) and ``W`` is the sum of the group weights. The
variance of the combined image, in counts per second squared, is
``sum(a_i^2 v_i) / t_g^2`` over all frames, and the noise in stack units is
``t_bar * scale * sqrt(area * variance)``. With one group, ``G = 1`` and
``t_bar = t``, this is the single-group formula above. The predicted sky is
the reference group's sky per second times ``t_bar``. A stack with a group
that is clipped at zero gets no prediction: the combine drops sky pixels near
zero there, which these weights do not describe (see
`expected_exposure_group_noise`).

The numbers this module returns are the frames' own noise, with no Siril
scaling. Siril multiplies each frame by a scale it does not save, which
multiplies the noise as well. `normalization_scale.py` measures the stack's
scale from its stars and multiplies the three noise parts by it. The scale is
stored in its own row, so the unscaled parts can always be found again.

What the model leaves out, so a measured noise above the expected one is
expected and has these causes:

- Siril normalizes every frame (it matches each frame's background level and
  scale to the reference) before it averages. The scale is corrected for the
  stack as a whole (see above). What stays is that the frames' scales differ
  from one another, which changes the weights slightly (under about 3% on a
  noise ratio, a design estimate).
- A color stack is debayered: the one-shot color camera's mosaic is filled in
  by interpolation, so neighboring pixels are correlated, and the planes are
  averaged here.
- The calibration frames add noise of their own. The master dark and the
  master bias hold a little read noise, and the master flat adds a fixed
  pattern (see `flat_noise_contribution.py`).
- Fixed patterns that do not average away (hot pixels, amp glow edges).
- Pixel rejection while stacking throws away some of the data, which makes the
  stack noisier than the weights alone say.
- Faint stars and nebulosity in the apertures. The apertures are chosen away
  from detected stars, but not from the faintest ones.

Registration (resampling each frame onto the reference) is not on this list
as a cause. A kernel whose weights add to 1 keeps the sum over an aperture,
so the variance of an aperture sum barely changes: the variance of one pixel
falls, but neighboring pixels become correlated, and a sum over many pixels
sees both.

Dark current is not left out. The sky rate of each frame is measured on the
raw frame with no dark subtracted, so it holds the sky, the dark current and
the amp glow together (see `frame_measurement.py`), and the Poisson term
counts all three.
"""

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache

import numpy as np
from photutils.aperture import CircularAnnulus, CircularAperture

from astrometricslib.drivers.fits_access import collapse_to_2d, read_data
from astrometricslib.models.camera_profile import ProvenancedValue, ProvenanceKind
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    APERTURE_RADIUS_PX,
    MEDIAN_ERROR_FACTOR,
    RING_INNER_PX,
    RING_OUTER_PX,
    sky_level_of_plane,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import FULL_SCALE_COUNTS

logger = logging.getLogger(__name__)

__all__ = [
    "STACK_UNITS_PER_ADU",
    "ExpectedNoise",
    "ExpectedNoiseOutcome",
    "ExposureGroupNoiseFacts",
    "FrameNoiseInput",
    "effective_aperture_area_px",
    "expected_exposure_group_noise",
    "expected_stack_noise",
    "gather_frame_noise_inputs",
    "measure_stack_sky_level",
]

# Siril divides an integer frame by 65535 when it reads it and writes its
# stacks as 32-bit numbers on that scale, so one camera ADU is 1 / 65535 of a
# stack unit. This is the same constant the exposure-group combine uses
# (`FULL_SCALE_COUNTS`), so the two cannot drift. The Nikon D5300's 14-bit raw
# frames are divided by 65535 as well, not by 16383. Seen on the ASI533MM Pro
# library stack of M 57 (2026-10-03, 61 frames): the stack's median of 0.00628
# is 411 ADU, and the frames' own sky (the sky rate times the exposure divided
# by the gain, bias level taken off) is of the same size (see register entry
# STK-38). Frames stored as floating-point numbers would break the scale.
STACK_UNITS_PER_ADU = 1.0 / FULL_SCALE_COUNTS

# The median of n independent pixels has a variance of this many times the
# pixel variance divided by n (for a bell curve; the standard error factor
# `MEDIAN_ERROR_FACTOR`, squared, is pi / 2).
_MEDIAN_VARIANCE_FACTOR = MEDIAN_ERROR_FACTOR**2

# The aperture's centre sits at an arbitrary place inside a pixel. The pixel
# weights of the circle depend on that place, so the sums below are averaged
# over this many places along each pixel axis.
_SUBPIXEL_PLACES = 4

# The step between the rows and columns sampled when a stack file is read only
# for its sky level (see `measure_stack_sky_level`).
_SKY_SAMPLE_STEP = 4

# The reason shown when the stack was made with equal-looking inputs that carry
# no per-frame facts.
NO_FRAME_ROWS_REASON = (
    "no stored sky measurement was found for the stack's frames; run processing.record_frame_measurements "
    "for the target first"
)


@dataclass(frozen=True)
class FrameNoiseInput:
    """What the noise model needs to know about one frame of the stack.

    Parameters
    ----------
    weight : `float`
        The frame's weight in the stack, in any scale (only ratios matter).
    sky_electrons : `float`
        The sky in one pixel of this frame, in electrons.
    read_noise_electrons : `float`
        The camera's read noise at this frame's setting, in electrons.
    gain_electrons_per_adu : `float`
        The camera's conversion gain at this frame's setting.
    assumed_reason : `str`
        Why one of these numbers is assumed (an unmeasured gain, an uncooled
        camera). Empty when all are measured.
    """

    weight: float
    sky_electrons: float
    read_noise_electrons: float
    gain_electrons_per_adu: float
    assumed_reason: str = ""


@dataclass(frozen=True)
class ExposureGroupNoiseFacts:
    """What the noise model needs to know about one exposure group.

    Parameters
    ----------
    combine_weight : `float`
        The group's weight in the combine, ``W_g = N_g t_g^2 / s_g^2``.
    exposure_seconds : `float`
        The group's exposure length, ``t_g``, in seconds.
    frame_count : `int`
        The number of frames stacked in the group, ``N_g``.
    gain : `float`
        The brightness gain the combine applied to the group, ``G_g`` (1 when
        the combine could not measure one).
    gain_measured : `bool`
        Whether the combine measured the gain.
    sky_level_per_second : `float`
        The group stack's sky level in counts per second, in stack units.
    """

    combine_weight: float
    exposure_seconds: float
    frame_count: int
    gain: float
    gain_measured: bool
    sky_level_per_second: float


@dataclass(frozen=True)
class ExpectedNoise:
    """The noise a stack should show in an empty aperture, and its parts.

    Parameters
    ----------
    sky_noise : `float`
        The photon noise part, in stack units.
    read_noise : `float`
        The read noise part, in stack units.
    total_noise : `float`
        The two parts added in quadrature, in stack units.
    aperture_area_px : `float`
        The number of pixels that one pixel's variance was multiplied by
        (see `effective_aperture_area_px`).
    stack_units_per_adu : `float`
        The conversion from one camera ADU to the stack's units.
    frame_count : `int`
        The number of frames behind the numbers.
    predicted_sky_level : `float`
        The sky the frames predict in one pixel of the stack, in stack units:
        the weighted mean of the frames' sky in ADU times
        `stack_units_per_adu`. It is compared with the stack's own sky level
        to check the unit scale.
    assumed_reason : `str`
        Why the numbers are assumed, when they are. Empty when all inputs were
        measured.
    scale_note : `str`
        Which Siril normalization scale the noise parts were multiplied by, or
        why they were not corrected (see `normalization_scale.py`). Empty
        until the scale step has run.
    """

    sky_noise: float
    read_noise: float
    total_noise: float
    aperture_area_px: float
    stack_units_per_adu: float
    frame_count: int
    predicted_sky_level: float
    assumed_reason: str = ""
    scale_note: str = ""


@dataclass(frozen=True)
class ExpectedNoiseOutcome:
    """The expected noise, or the reason there is none.

    Parameters
    ----------
    result : `ExpectedNoise` or `None`
        The numbers, or `None` when they could not be worked out.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    result: ExpectedNoise | None
    reason: str = ""


@cache
def effective_aperture_area_px(
    radius_px: float = APERTURE_RADIUS_PX,
    ring_inner_px: float = RING_INNER_PX,
    ring_outer_px: float = RING_OUTER_PX,
) -> float:
    """Count the pixels a pixel's variance is multiplied by in an aperture sum.

    The aperture's sum of independent pixels, each with variance ``v``, has the
    variance ``v * sum(d^2)`` for the pixel overlaps ``d``. The ring's median
    is subtracted ``sum(d)`` times, and has the variance
    ``(pi / 2) * v / n`` for ``n`` ring pixels. The two errors are
    independent, so the result is ``sum(d^2) + (pi / 2) * sum(d)^2 / n``.

    Parameters
    ----------
    radius_px : `float`, optional
        The aperture radius. The noise aperture's by default.
    ring_inner_px, ring_outer_px : `float`, optional
        The sky ring's radii.

    Returns
    -------
    area_px : `float`
        The multiplier, averaged over places of the aperture's centre inside a
        pixel. About 50 for the sum itself and 13 more for the ring with the
        default radii.
    """
    size = math.ceil(ring_outer_px) * 2 + 3
    centre = size // 2
    totals = []
    for column_step in range(_SUBPIXEL_PLACES):
        for row_step in range(_SUBPIXEL_PLACES):
            x = centre + (column_step + 0.5) / _SUBPIXEL_PLACES - 0.5
            y = centre + (row_step + 0.5) / _SUBPIXEL_PLACES - 0.5
            overlap = np.asarray(CircularAperture((x, y), r=radius_px).to_mask(method="exact").data).ravel()
            ring_pixels = int(
                np.count_nonzero(
                    np.asarray(
                        CircularAnnulus((x, y), r_in=ring_inner_px, r_out=ring_outer_px)
                        .to_mask(method="center")
                        .data
                    )
                )
            )
            totals.append(
                float(np.sum(overlap**2))
                + _MEDIAN_VARIANCE_FACTOR * float(np.sum(overlap)) ** 2 / ring_pixels
            )
    return float(np.mean(totals))


def expected_stack_noise(
    frames: Sequence[FrameNoiseInput],
    stack_units_per_adu: float = STACK_UNITS_PER_ADU,
) -> ExpectedNoiseOutcome:
    """Work out the noise the model expects in an empty aperture of a stack.

    Parameters
    ----------
    frames : `Sequence` [`FrameNoiseInput`]
        One entry for each member frame.
    stack_units_per_adu : `float`, optional
        The stack's units for one camera ADU.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The sky part, the read part and their sum in quadrature, or the reason
        there is none (no frames, or weights that add to nothing).
    """
    if not frames:
        return ExpectedNoiseOutcome(None, "the stack has no member frames")
    weight_total = math.fsum(frame.weight for frame in frames)
    if weight_total <= 0 or any(frame.weight < 0 for frame in frames):
        return ExpectedNoiseOutcome(None, "the frames' weights are not positive")
    if any(
        frame.gain_electrons_per_adu <= 0 or frame.sky_electrons < 0 or frame.read_noise_electrons < 0
        for frame in frames
    ):
        return ExpectedNoiseOutcome(None, "a frame has a gain, sky or read noise that is not usable")
    sky_variance = math.fsum(
        (frame.weight / weight_total) ** 2 * frame.sky_electrons / frame.gain_electrons_per_adu**2
        for frame in frames
    )
    read_variance = math.fsum(
        (frame.weight / weight_total) ** 2 * frame.read_noise_electrons**2 / frame.gain_electrons_per_adu**2
        for frame in frames
    )
    area = effective_aperture_area_px()
    scale = stack_units_per_adu
    predicted_sky_level = scale * math.fsum(
        frame.weight / weight_total * frame.sky_electrons / frame.gain_electrons_per_adu for frame in frames
    )
    sky_noise = scale * math.sqrt(area * sky_variance)
    read_noise = scale * math.sqrt(area * read_variance)
    reasons = sorted({frame.assumed_reason for frame in frames if frame.assumed_reason})
    return ExpectedNoiseOutcome(
        ExpectedNoise(
            sky_noise=sky_noise,
            read_noise=read_noise,
            total_noise=math.hypot(sky_noise, read_noise),
            aperture_area_px=area,
            stack_units_per_adu=scale,
            frame_count=len(frames),
            predicted_sky_level=predicted_sky_level,
            assumed_reason="; ".join(reasons),
        )
    )


def expected_exposure_group_noise(
    frames: Sequence[FrameNoiseInput],
    frame_group_indices: Sequence[int],
    groups: Sequence[ExposureGroupNoiseFacts],
    stack_units_per_adu: float = STACK_UNITS_PER_ADU,
) -> ExpectedNoiseOutcome:
    """Work out the expected noise of a stack combined from exposure groups.

    Frame ``i`` of group ``g`` enters the combined image, in counts per
    second, with the coefficient ``a_i = W_g w_i / (W G_g)``. Here ``w_i`` is
    the frame's share inside its group (its weight over the sum of the weights
    of the group's members, so a group's shares add to 1) and ``W`` is the sum
    of all the groups' combine weights. The variance in counts per second
    squared is ``sum(a_i^2 v_i) / t_g^2``, with ``v_i`` the frame's variance in
    ADU squared (sky electrons plus read noise squared, over the gain
    squared). The noise in stack units is
    ``t_bar * scale * sqrt(area * variance)``, with ``t_bar`` the mean exposure
    per frame, weighted by frame count, that the combine scales back by. The
    sky part and the read part are summed apart.

    The predicted sky is the reference group's sky per second times ``t_bar``.
    The reference group is the one with the largest combine weight, which the
    combine aligns every group to.

    The frames' weights are the stored raw weights ``W_g w_i`` (see
    `stack_membership.group_frame_weights`); only their ratios inside a group
    are used here, and `groups` supplies ``W_g``.

    A stack with a group clipped at zero is not handled here. The caller
    refuses it, because the combine drops that group's sky pixels near zero
    and these weights do not describe that. That includes a clipped stack of
    one group, on purpose.

    Parameters
    ----------
    frames : `Sequence` [`FrameNoiseInput`]
        One entry for each member frame; ``weight`` is the raw weight.
    frame_group_indices : `Sequence` [`int`]
        For each frame, the position of its group in `groups`.
    groups : `Sequence` [`ExposureGroupNoiseFacts`]
        One entry for each combined group.
    stack_units_per_adu : `float`, optional
        The stack's units for one camera ADU.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The noise, or the reason there is none (no frames, a group without
        member frames, or values that are not usable). A group whose gain was
        not measured is computed with the combine's gain of 1 and marked
        assumed.
    """
    if not frames or not groups or len(frames) != len(frame_group_indices):
        return ExpectedNoiseOutcome(None, "the stack has no member frames")
    if any(
        group.combine_weight <= 0
        or group.exposure_seconds <= 0
        or group.frame_count <= 0
        or group.gain <= 0
        or not math.isfinite(group.sky_level_per_second)
        for group in groups
    ):
        return ExpectedNoiseOutcome(
            None, "an exposure group has a weight, exposure, gain or sky that is not usable"
        )
    if any(not 0 <= index < len(groups) for index in frame_group_indices):
        return ExpectedNoiseOutcome(None, "a member frame belongs to no combined exposure group")
    if any(
        frame.weight < 0
        or frame.gain_electrons_per_adu <= 0
        or frame.sky_electrons < 0
        or frame.read_noise_electrons < 0
        for frame in frames
    ):
        return ExpectedNoiseOutcome(None, "a frame has a weight, gain, sky or read noise that is not usable")
    raw_totals = [0.0] * len(groups)
    for frame, index in zip(frames, frame_group_indices, strict=True):
        raw_totals[index] += frame.weight
    if any(total <= 0 for total in raw_totals):
        return ExpectedNoiseOutcome(None, "a combined exposure group has no member frame with a weight")
    weight_total = math.fsum(group.combine_weight for group in groups)
    mean_exposure = math.fsum(group.frame_count * group.exposure_seconds for group in groups) / math.fsum(
        group.frame_count for group in groups
    )
    sky_variance = 0.0
    read_variance = 0.0
    for frame, index in zip(frames, frame_group_indices, strict=True):
        group = groups[index]
        coefficient = group.combine_weight * frame.weight / raw_totals[index] / (weight_total * group.gain)
        scale_to_rate = coefficient / (frame.gain_electrons_per_adu * group.exposure_seconds)
        sky_variance += scale_to_rate**2 * frame.sky_electrons
        read_variance += scale_to_rate**2 * frame.read_noise_electrons**2
    area = effective_aperture_area_px()
    scale = stack_units_per_adu
    sky_noise = mean_exposure * scale * math.sqrt(area * sky_variance)
    read_noise = mean_exposure * scale * math.sqrt(area * read_variance)
    reference = max(groups, key=lambda group: group.combine_weight)
    reasons = {frame.assumed_reason for frame in frames if frame.assumed_reason}
    for group in groups:
        if not group.gain_measured:
            reasons.add(
                f"the combine could not measure the gain of the {group.exposure_seconds:g} s group and used 1"
            )
    return ExpectedNoiseOutcome(
        ExpectedNoise(
            sky_noise=sky_noise,
            read_noise=read_noise,
            total_noise=math.hypot(sky_noise, read_noise),
            aperture_area_px=area,
            stack_units_per_adu=scale,
            frame_count=len(frames),
            predicted_sky_level=reference.sky_level_per_second * mean_exposure,
            assumed_reason="; ".join(sorted(reasons)),
        )
    )


def gather_frame_noise_inputs(
    members: Sequence[object],
    weights: Sequence[float | None],
    sky_rate_rows: dict[str, QualityMeasurement],
    subject_id_of: Callable[[object], str],
    noise_setting_of: Callable[[object], tuple[ProvenancedValue | None, ProvenancedValue | None, str]],
    weights_reason: str = "",
) -> ExpectedNoiseOutcome | list[FrameNoiseInput]:
    """Collect each member frame's sky, read noise, gain and weight.

    Parameters
    ----------
    members : `Sequence`
        The stack's member frames (they have ``exposure``).
    weights : `Sequence` [`float` or `None`]
        Each member's weight, in the order of `members`. `None` means unknown.
    sky_rate_rows : `dict` [`str`, `QualityMeasurement`]
        The newest ``frame.sky_rate_e_per_s_per_px`` row of each frame, by the
        frame's subject id. A row for a color plane is not used.
    subject_id_of : `Callable`
        Gives a frame's subject id.
    noise_setting_of : `Callable`
        Gives a frame's gain, read noise and the reason one is missing (the
        camera profile's lookup).
    weights_reason : `str`, optional
        Why the weights are unknown.

    Returns
    -------
    inputs : `list` [`FrameNoiseInput`] or `ExpectedNoiseOutcome`
        One entry for each member, or an outcome with the reason when any
        member lacks a weight, a sky measurement, a gain, a read noise or an
        exposure time. A missing value is never replaced by another frame's.
    """
    if not members:
        return ExpectedNoiseOutcome(None, "the stack has no member frames")
    if any(weight is None for weight in weights):
        return ExpectedNoiseOutcome(None, weights_reason or "the frames' weights are unknown")
    inputs: list[FrameNoiseInput] = []
    for member, weight in zip(members, weights, strict=True):
        row = sky_rate_rows.get(subject_id_of(member))
        if row is None:
            return ExpectedNoiseOutcome(None, NO_FRAME_ROWS_REASON)
        if row.value is None:
            return ExpectedNoiseOutcome(None, f"a frame's sky rate was not measured: {row.reason}")
        try:
            exposure_seconds = float(member.exposure)
        except TypeError, ValueError:
            exposure_seconds = 0.0
        if not exposure_seconds > 0:
            return ExpectedNoiseOutcome(None, "a member frame has no usable exposure time")
        gain, read_noise, gap = noise_setting_of(member)
        if gain is None or read_noise is None:
            return ExpectedNoiseOutcome(
                None, gap or "a frame's setting has no gain or read noise in its camera profile"
            )
        reasons = []
        if row.status == MeasurementStatus.ASSUMED and row.reason:
            reasons.append(row.reason)
        for label, value in (("gain", gain), ("read noise", read_noise)):
            if value.provenance.kind != ProvenanceKind.MEASURED:
                reasons.append(f"the camera profile's {label} is {value.provenance.kind.value}, not measured")
        inputs.append(
            FrameNoiseInput(
                weight=float(weight),
                sky_electrons=float(row.value) * exposure_seconds,
                read_noise_electrons=read_noise.value,
                gain_electrons_per_adu=gain.value,
                assumed_reason="; ".join(reasons),
            )
        )
    return inputs


def measure_stack_sky_level(stack_path: str) -> float | None:
    """Read the sky level of a stack file that the noise step did not read.

    The noise step reads each stack once and gives its plane to
    `sky_level_of_plane`. This is for the stacks it does not read (a spectral
    stack). It reads the file as 32-bit numbers and takes the median of every
    fourth row and column, which is a 1/16 sample of a smooth sky and keeps
    the memory low.

    Parameters
    ----------
    stack_path : `str`
        The stack file.

    Returns
    -------
    level : `float` or `None`
        The median in the stack's own units, or `None` when the file holds no
        usable pixel or the median is not above zero.
    """
    data = read_data(stack_path)
    if data is None:
        return None
    plane = collapse_to_2d(np.asarray(data, dtype=np.float32))
    return sky_level_of_plane(plane[::_SKY_SAMPLE_STEP, ::_SKY_SAMPLE_STEP])
