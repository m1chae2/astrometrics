"""Measures the scale Siril gave a stack, and puts it into the expected noise.

Siril stacks with ``-norm=addscale``. For each registered frame it adds an
offset so the sky levels match the reference frame's, and multiplies the frame
by a scale chosen so that the frame's pixel spread matches the reference
frame's. Siril does not save these scales. In faint sky the pixel spread is
mostly noise, and the resampling that registration does smooths a frame and
lowers its noise, so Siril scales resampled frames up. On M 57 the scales were
1.00 for the reference frame (it is not resampled), 1.005 for a frame shifted
by a whole number of pixels, 1.32 to 1.34 for the other 60 s frames and about
1.39 for the stack as a whole. A scale multiplies the signal and the noise
alike, so the noise the frames predict (`expected_noise.py`) comes out too low
by that factor. The half-stack difference job measured the random part of the
noise at 1.37 (M 57), 1.34 (M 81) and 1.14 (M 27) times the frame-based
value.

The stack's scale is measured here from its stars. By Siril's definition the
scale of the registration reference frame is 1. The reference stars of the
noise step (see `stack_noise.py`) have a flux in the stack and a flux in that
frame, so the ratio of the two fluxes holds the stack's scale. The scale is the
median over the stars, with an uncertainty from their scatter.

The expected ratio with no Siril scaling at all, for a stack of one exposure
group, is the unit conversion: a star of ``F`` ADU in the reference frame has
``F / 65535`` stack units in the stack. For a stack of exposure groups, the
combine divides each group by its exposure ``t_g``, applies a gain ``G_g`` to
put it on the reference group's brightness and multiplies the average by the
mean exposure per frame ``t_bar`` (see `expected_noise.py`). The reference
group (the one with the most weight, which holds the registration reference
frame) has ``G = 1``. A star then has ``t_bar * F / (t_g * 65535)`` stack
units, with ``t_g`` the reference group's exposure, the number the combine
divided by. In short::

    expected ratio (no Siril scale) = STACK_UNITS_PER_ADU * t_bar / t_g
    scale = measured ratio / expected ratio

and ``t_bar = t_g`` for one group. The reference frame must be one of the
reference group's frames, which is decided by membership. Its own exposure
need not equal ``t_g``: a group that holds folded frames has a mean exposure
``t_g`` (127.8 s for 22 frames of 120 s and one of 300 s), and Siril may pick
a frame of the folded length as the reference. Siril matches every frame of
the group to the reference frame's brightness, so a star has ``F`` ADU in the
group stack whatever the reference frame's exposure, and the combine still
divides by ``t_g``. The measured gains of the other groups
carry each of those groups' own Siril scales. Those do not enter here: the
gain divides them out of the stars, so the stack's stars show the reference
group's scale only.

What the number is not, so a reader does not over-read it:

- Clouds or haze in some frames lower the stack's flux against the reference
  frame, and bias the scale low. A reference frame that is clearer than the
  average frame does the same; a cloudier one biases it high.
- The stack's star flux is the weighted mean of the frames' scales. The
  stack's noise follows the weighted root mean square of the scales. With
  scales between 1.0 and 1.35 the two differ by about 1% (a design estimate;
  M 57's scales above were not measured frame by frame in the library).
- For a stack of several exposure groups, the other groups' scales are inside
  their gains. The correction is exact for one group and approximate for
  several: it takes the reference group's scale for every group.
- A star with a different brightness in the stack than in the reference frame
  for any other reason (a variable star, a different seeing that the large
  aperture does not remove) widens the scatter and is in the uncertainty.
"""

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    STACK_UNITS_PER_ADU,
    ExpectedNoise,
    ExpectedNoiseOutcome,
    ExposureGroupNoiseFacts,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    ReferenceFrameFluxRatio,
    StackNoiseOutcome,
)

logger = logging.getLogger(__name__)

__all__ = [
    "NormalizationScale",
    "NormalizationScaleOutcome",
    "apply_normalization_scale",
    "measure_normalization_scale",
]

# Used only when the reference group's frame paths or the reference frame's
# path are not known (a caller that gives no paths): the registration reference
# frame's exposure must then match the reference group's exposure within this
# fraction before the group arithmetic above is used. Two exposure groups
# differ by far more than this (the Vega groups were 0.5, 1, 2, 3 and 5 s; the
# M 27 groups 30, 60 and 120 s), and the header's exposure is the same number
# the group was made from, so it is an equality check, not a tolerance on a
# measurement. A group that holds folded frames has a mean exposure that this
# check refuses, which is why the stack's own records give paths and the
# membership check below is used instead. A design choice; not tuned.
EXPOSURE_MATCH_FRACTION = 0.01


@dataclass(frozen=True)
class NormalizationScale:
    """The scale Siril's normalization gave a stack, measured from its stars.

    Parameters
    ----------
    value : `float`
        The scale: 1 means Siril left the stack's brightness alone.
    uncertainty : `float`
        Its 1-sigma uncertainty, from the scatter of the stars' flux ratios
        (the standard error of their median), in the same units as `value`.
    star_count : `int`
        How many reference stars it was measured on.
    note : `str`
        How it was measured, for the row's reason.
    """

    value: float
    uncertainty: float
    star_count: int
    note: str


@dataclass(frozen=True)
class NormalizationScaleOutcome:
    """The measured scale, or the reason there is none.

    Parameters
    ----------
    result : `NormalizationScale` or `None`
        The scale, or `None` when it could not be measured.
    reason : `str`
        Why there is no scale. Empty when there is one.
    """

    result: NormalizationScale | None
    reason: str = ""


def _expected_ratio_without_scaling(
    groups: Sequence[ExposureGroupNoiseFacts], flux_ratio: ReferenceFrameFluxRatio
) -> tuple[float | None, str]:
    """Work out the stack-to-frame flux ratio if Siril applied no scale.

    Parameters
    ----------
    groups : `Sequence` [`ExposureGroupNoiseFacts`]
        The exposure groups of a combined stack. Empty for a stack of one
        group.
    flux_ratio : `ReferenceFrameFluxRatio`
        The stars' flux ratio, which names the registration reference frame
        (``reference_frame_path``) and gives its exposure.

    Returns
    -------
    ratio : `float` or `None`
        ``STACK_UNITS_PER_ADU * t_bar / t_g`` in stack units per ADU, with
        ``t_g`` the reference group's exposure (``t_bar = t_g`` for one
        group), or `None` when the arithmetic does not apply.
    reason : `str`
        Why it does not apply. Empty when there is a ratio.
    """
    if not groups:
        return STACK_UNITS_PER_ADU, ""
    if any(
        group.combine_weight <= 0 or group.exposure_seconds <= 0 or group.frame_count <= 0 for group in groups
    ):
        return None, "an exposure group has a weight, exposure or frame count that is not usable"
    reference = max(groups, key=lambda group: group.combine_weight)
    reference_path = flux_ratio.reference_frame_path
    if reference.member_paths and reference_path:
        # Membership decides. A frame of the reference group may have another
        # exposure than the group's mean, when the group holds folded frames.
        if str(reference_path) not in reference.member_paths:
            return None, (
                f"the registration reference frame ({reference_path}) is not one of the frames of the "
                f"reference exposure group ({reference.exposure_seconds:g} s)"
            )
    else:
        reference_exposure_seconds = flux_ratio.reference_exposure_seconds
        if reference_exposure_seconds is None:
            return None, "the registration reference frame has no exposure length in its header"
        if abs(reference_exposure_seconds - reference.exposure_seconds) > EXPOSURE_MATCH_FRACTION * (
            reference.exposure_seconds
        ):
            return None, (
                f"the registration reference frame ({reference_exposure_seconds:g} s) is not in the "
                f"reference exposure group ({reference.exposure_seconds:g} s)"
            )
    mean_exposure = math.fsum(group.frame_count * group.exposure_seconds for group in groups) / math.fsum(
        group.frame_count for group in groups
    )
    return STACK_UNITS_PER_ADU * mean_exposure / reference.exposure_seconds, ""


def measure_normalization_scale(
    noise: StackNoiseOutcome | None, groups: Sequence[ExposureGroupNoiseFacts] = ()
) -> NormalizationScaleOutcome:
    """Measure the scale Siril's normalization gave a stack.

    Parameters
    ----------
    noise : `StackNoiseOutcome` or `None`
        The noise step's outcome, which holds the stars' flux ratio of the
        stack against the registration reference frame. `None` when the noise
        step did not run.
    groups : `Sequence` [`ExposureGroupNoiseFacts`], optional
        The exposure groups of a stack made from several. Left empty for a
        stack of one group.

    Returns
    -------
    outcome : `NormalizationScaleOutcome`
        The scale with its uncertainty, or the reason there is none (no noise
        step, no named reference frame, a frame that does not line up, too few
        unsaturated stars, or a reference frame that is not in the reference
        group).
    """
    if noise is None:
        return NormalizationScaleOutcome(None, "the stack's noise step did not run")
    if noise.result is None:
        return NormalizationScaleOutcome(
            None, f"the stack's reference stars were not measured: {noise.reason or 'no reason was given'}"
        )
    flux_ratio: ReferenceFrameFluxRatio | None = noise.result.reference_frame_flux_ratio
    if flux_ratio is None:
        return NormalizationScaleOutcome(
            None, "the stars' flux ratio against the reference frame was not tried"
        )
    if flux_ratio.ratio is None:
        return NormalizationScaleOutcome(None, flux_ratio.reason or "the stars' flux ratio was not measured")
    expected, reason = _expected_ratio_without_scaling(groups, flux_ratio)
    if expected is None or not expected > 0:
        return NormalizationScaleOutcome(None, reason)
    value = flux_ratio.ratio / expected
    uncertainty = flux_ratio.uncertainty / expected
    left_out = f", {flux_ratio.saturated_count} saturated left out" if flux_ratio.saturated_count else ""
    expected_text = (
        "the unit conversion (1 / 65535)"
        if not groups
        else "the unit conversion times the mean exposure over the reference group's exposure"
    )
    note = (
        f"Siril's normalization scale: the median of {flux_ratio.star_count} reference stars' flux in "
        f"the stack over their flux in the registration reference frame (scale 1 by Siril's "
        f"definition{left_out}), divided by {expected_text}. Clouds or haze in some frames bias it low."
    )
    return NormalizationScaleOutcome(NormalizationScale(value, uncertainty, flux_ratio.star_count, note))


def apply_normalization_scale(
    expected: ExpectedNoiseOutcome, scale: NormalizationScaleOutcome
) -> ExpectedNoiseOutcome:
    """Multiply the expected noise by the stack's measured scale.

    The sky, read and total noise are multiplied by the scale, because Siril's
    scale multiplies a frame's noise as it multiplies its signal. The sky level
    is not: it is checked against the stack's own sky in a separate row. The
    scale is stored in its own row, so the unscaled parts are the stored ones
    divided by it.

    Parameters
    ----------
    expected : `ExpectedNoiseOutcome`
        The noise the frames predict, before any scale.
    scale : `NormalizationScaleOutcome`
        The stack's scale, or the reason there is none.

    Returns
    -------
    outcome : `ExpectedNoiseOutcome`
        The outcome with the numbers scaled and a note saying which scale was
        applied. When there is no scale, the numbers are kept and the note says
        they are not corrected, and why. An outcome with no result is returned
        as it is.
    """
    result = expected.result
    if result is None:
        return expected
    if scale.result is None:
        note = f"Not corrected for Siril's normalization scale: {scale.reason or 'it was not measured'}."
        return ExpectedNoiseOutcome(replace(result, scale_note=note))
    value = scale.result.value
    if not math.isfinite(value) or value <= 0:
        note = "Not corrected for Siril's normalization scale: the measured scale is not usable."
        return ExpectedNoiseOutcome(replace(result, scale_note=note))
    scaled: ExpectedNoise = replace(
        result,
        sky_noise=result.sky_noise * value,
        read_noise=result.read_noise * value,
        total_noise=result.total_noise * value,
        coherent_read_noise=(
            None if result.coherent_read_noise is None else result.coherent_read_noise * value
        ),
        scale_note=(
            f"Multiplied by Siril's normalization scale {value:.3f} +/- {scale.result.uncertainty:.3f} "
            "(see `stacking.normalization_scale`)."
        ),
    )
    return ExpectedNoiseOutcome(scaled)
