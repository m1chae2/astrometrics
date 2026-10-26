"""Purpose: Forward model of a stack's noise as frames are added.

Description: This module is the one place where "how much better will the
stack be with N more frames" is worked out (decision S15). The target report
(forecast and "take N more") and tonight's ranking both use it.

The forward model
-----------------
Each frame measures the sky as a rate. In rate units (electrons per second)
a frame of exposure t, sky rate B and read noise RN has the variance

    v_i = (B_i t_i + RN^2) / t_i^2

The stacker averages the frames with weights w_i applied to these rate
frames, so the stack's variance is

    V = sum(w_i^2 v_i) / (sum(w_i))^2

Which weights the stacker uses depends on its mode, and the mode is always
read from the stack run's record, never assumed:

* ``equal``: every w_i is 1. Frames of different exposure lengths then count
  alike, so a short or moonlit frame costs more than it should.
* ``noise``: w_i = 1 / v_i, each frame by the inverse of its own noise.
* ``wfwhm``: Siril's star-width weight,
  ``w = (1/f^2 - 1/f_max^2) / (1/f_min^2 - 1/f_max^2)`` with f the frame's
  weighted star width and f_min and f_max the smallest and largest in the
  stack. The blurriest frame gets 0. The weights are recomputed over old and
  new frames together. The formula is the one that
  ``astrometricslib/pipelines/stacking/metrics.py`` (`STACKING_FRAME_WEIGHT`,
  validation register entry STK-07) uses to rebuild the weights from the
  sequence file.

* ``group``: a stack the stacker built from exposure groups (see below).
  The old rule "wfwhm over all frames" never describes such a stack.

When the mode was not recorded (Siril does not report the weights of its
noise and star-count modes) the forecast is given as "weighting mode not
recorded", not guessed.

Exposure groups
---------------
The stacker stacks each exposure length on its own (``wfwhm`` inside the
group) and averages the groups with ``W_g = N_g t_g^2 / s_g^2``: ``N_g``
frames, exposure ``t_g``, and ``s_g`` the noise of one raw frame in ADU. Each
group also has a brightness gain ``G_g``. Frame ``i`` of group ``g`` enters
the combined image, in rate units, with the coefficient
``W_g w_i / (sum(W) G_g)``, where ``w_i`` is its share inside the group. The
combine multiplies the result by the mean exposure per frame ``t_bar``, so
the group forecast (`forecast_group_noise`) is in the stack's scale. It is
the same model as ``expected_noise.expected_exposure_group_noise`` of the
library, which works in stack units; a test compares the two.

`plan_group_stack` builds the later stack the way the stacker would:

* an existing group keeps its measured ``s_g`` and gain, and its ``wfwhm``
  weights are rebuilt over old and new frames together;
* a new exposure length with 5 or more frames in all is a new group. Its
  ``s_g`` is predicted in ADU from the sky rate and the read noise (the
  frames are in electrons, so the camera gain converts them), and it keeps
  the gain of the nearest existing group;
* a length with fewer than 5 frames is folded into the nearest group by the
  library's own rule (`fold_exposure_lengths`);
* when no length would have 5 frames the stacker would make one group of
  mixed lengths, and the plan is refused;
* if the new frames make another group the heaviest, the later stack has a
  different brightness scale and the pair is confounded (flagged).

What part 2a stores, and what this needs. The group's ``W_g`` is the sum of
its ``stacking.frame_weight`` rows, ``N_g`` is
``stacking.group_frames_stacked_count``, ``t_g`` is
``stacking.group_exposure_s`` and ``G_g`` is ``stacking.group_gain_applied``;
``s_g`` is not a row of its own, but `frame_noise_from_group_weight` gets it
back from those. The camera gain (electrons per ADU) comes from the camera
profile.

"Take N more"
-------------
`frames_to_cut_noise` (and `frames_to_cut_noise_in_groups` for a group stack)
adds N copies of past frames of the same capture
setting until the predicted noise falls to 1/sqrt(2) of today's (doubling
the effective time cuts noise by about 29 percent). It stays a design
estimate: on M 57 the real subsets came out 9 to 31 percent below the
prediction (validation register entry STK-36), so the answer is labeled
tentative. A new exposure length is never suggested with fewer than 5
frames, because the stacker would fold a smaller set into another group.

The floor
---------
One stack's floor (`measure_stack_noise_floor`). A stack's measured
empty-aperture noise is more than the frames alone predict when something
fixed is left in the apertures (faint stars, a calibration residual). Both
numbers are in the stack's units: the measured noise and the stored
``stacking.expected_empty_aperture_noise_stack_unit``, which includes Siril's
normalization scale (``stacking.normalization_scale``). Then
``floor^2 = measured^2 - random^2``, kept only when it is above 0 by 2
standard errors. The error is that of the measured noise (see below, applied
to the variance); the predicted value has no stored error and is taken as
exact. Validation (plan section 3.6.1): M 27 60 s has a clear floor, M 81 has
none at 2 sigma.

The forecast carries the floor as a fraction ``f`` of the random variance of
today's stack, so the model's rate units need no conversion. Today the
measured variance is ``V_now (1 + f)``; with more frames it is
``V_later + f V_now`` (in the group mode, times the square of the change of
``t_bar``, because the floor is taken as a fixed pattern on the sky). When
the floor is most of the noise (a share of 0.5 or more makes the 0.707
target unreachable), `frames_to_cut_noise` gives no N: it says more frames
will not reach the goal and states the floor.

Which floor wins (`choose_noise_floor`). A fit over two or more stacks
(`fit_noise_floor`, below) also finds a floor and does not rely on the
prediction's scale. A fit with at least one degree of freedom, so that the
stacks' scatter checks it, wins when it finds a floor. Otherwise the
one-stack floor is used. A two-stack fit has no check, so it never overrides
a one-stack result. If there is no one-stack result, a fit that found a
floor is used as it is.

The fit over stacks. A stack stops improving when a fixed pattern or
another systematic is left. `fit_noise_floor` fits
``measured_variance = alpha * expected_variance + b`` over the history of a
target's stacks. A floor is reported only when b is
above 0 by two standard errors. The error of each measured noise is that of
the empty-aperture estimate, 1.17 / sqrt(n) of the noise for n apertures.
These errors are unpaired unless a row says it is paired (only the
leave-one-night-out rows are paired today), and the fit says which it used.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import (
    NOISE_WEIGHT_MODE,
    choose_stack_weight,
    fold_exposure_lengths,
    group_frame_weights,
    wfwhm_weights,
)
from wayfindinglib.models.quality.statements import Confidence

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

NOISE_GROWTH_ENTRY = "WAY-21"
"""Register entry behind the forward model and the floor fit."""
TAKE_MORE_ENTRY = "WAY-D30"
"""Register row behind "take N more" (a design estimate, see STK-36)."""

WEIGHT_MATCH_TOLERANCE = 0.05
"""A recorded weight matches a mode's predicted weight when they agree within
this
fraction (after both are divided by their mean). The recorded weights are
rounded when stored, and the frames' sky rates are measured with a few percent
of error, so exact equality cannot be expected. Design estimate, register row
WAY-D31."""

TARGET_NOISE_RATIO = 1.0 / math.sqrt(2.0)
"""The step "take N more" aims for: the noise falls to 0.707 of today's, which
is
the effect of doubling the effective time (noise falls as the square root of
time) and is the smallest change the eye sees. From the plan, section 6.4."""

MAXIMUM_NEW_FRAMES = 5000
"""The search for N stops here. 5000 frames of 30 s is 42 hours, far more than
one
target needs."""

APERTURE_NOISE_ERROR_FACTOR = 1.17
"""The error of the empty-aperture noise is 1.17 / sqrt(n) of the noise for n
apertures. The noise is 1.4826 times the median absolute deviation, and the
large-sample variance of that estimate is 1.364 sigma^2 / n, so its standard
error is sqrt(1.364) = 1.17 sigma / sqrt(n). Derived; register entry WAY-21."""

FLOOR_SIGMAS = 2.0
"""A floor is reported only when its fitted size is above 0 by this many
standard errors. The test is one-sided (a floor cannot be negative), so two
standard errors is a false alarm of about 2.3 percent, not 5. Design estimate,
register row WAY-D32."""

MINIMUM_GAIN_FRACTION = 0.10
"""The stop signal is raised when doubling the data is predicted to cut the
noise
by less than this fraction. The ideal gain from doubling is 29 percent, and 10
percent is about a third of that. Design estimate, register row WAY-D32."""


MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH = 5
"""A suggestion never asks for fewer frames than this of an exposure length the
stack does not have yet. The stacker folds a length with fewer frames into the
nearest group instead of stacking it on its own
(the stacking library's minimum frames per exposure group, which this repeats
because the library does not export it). Register row WAY-D70."""


class WeightingMode(StrEnum):
    """How the stacker weighted the frames."""

    EQUAL = "equal"
    NOISE = "noise"
    WFWHM = "wfwhm"
    GROUP = "group"
    UNKNOWN = "unknown"


class StackFrame(BaseModel):
    """What the forward model needs to know about one frame of a stack.

    Parameters
    ----------
    frame_id : `str`
        A label for the frame.
    exposure_seconds : `float`
        The exposure time, in seconds.
    sky_rate_e_per_s_per_px : `float`
        The frame's own sky rate, in electrons per second per pixel.
    read_noise_e : `float`
        The camera's read noise, in electrons.
    star_width : `float` or `None`
        The frame's star width, in any one unit used by every frame. Needed
        only for the ``wfwhm`` mode.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, frozen=True)

    frame_id: str
    exposure_seconds: float = Field(gt=0.0)
    sky_rate_e_per_s_per_px: float = Field(ge=0.0)
    read_noise_e: float = Field(ge=0.0)
    star_width: float | None = Field(default=None, gt=0.0)

    @property
    def rate_variance(self) -> float:
        """Give the variance of the frame in rate units.

        Returns
        -------
        variance : `float`
            ``(B t + RN^2) / t^2``, in (electrons per second) squared per
            pixel.
        """
        photon_and_read = self.sky_rate_e_per_s_per_px * self.exposure_seconds + self.read_noise_e**2
        return photon_and_read / self.exposure_seconds**2


class NoiseForecast(BaseModel):
    """The predicted noise of a stack.

    Parameters
    ----------
    variance : `float` or `None`
        The stack's variance in rate units. ``None`` when it cannot be found.
    noise : `float` or `None`
        The square root of `variance`. Only ratios of it are meaningful across
        stacks of the same capture setting.
    frame_count : `int`
        Frames in the stack.
    mode : `WeightingMode`
        The weighting used.
    reason : `str` or `None`
        Why there is no forecast.
    mean_exposure_seconds : `float` or `None`
        Only for the ``group`` mode: the mean exposure per frame, ``t_bar``.
        The combine multiplies its result by it, so `variance` and `noise` of
        that mode are in the stack's own scale (rate units times ``t_bar``).
    """

    model_config = _MODEL_CONFIG

    variance: float | None = None
    noise: float | None = None
    frame_count: int = 0
    mode: WeightingMode
    reason: str | None = None
    mean_exposure_seconds: float | None = None


def predict_weighting_mode(
    configured_weight: str | None, exposure_seconds: Sequence[float], *, is_spectral: bool = False
) -> WeightingMode:
    """Predict the mode the stacker will use for a future stack.

    This calls ``choose_stack_weight`` of the stacker: a configured mode
    wins; with none, the stacker weights by noise when equal weights would
    leave the stack at least 5 percent noisier
    (``sqrt(mean(1/t) * mean(t)) >= 1.05``).

    Parameters
    ----------
    configured_weight : `str` or `None`
        The ``Processing.Siril.stack_weight`` setting, or ``None`` for none.
    exposure_seconds : `Sequence` [`float`]
        The exposure time of every frame the stack will hold.
    is_spectral : `bool`, optional
        Whether it is a spectral stack. Those keep their configured weight.

    Returns
    -------
    mode : `WeightingMode`
        ``group`` when the stacker would split the frames into more than one
        exposure group (`fold_exposure_lengths`); else ``noise``, ``wfwhm`` or
        ``equal``; ``unknown`` for a configured mode the forward model does
        not know (such as star count).
    """
    if len(set(fold_exposure_lengths(list(exposure_seconds)))) > 1:
        return WeightingMode.GROUP
    chosen = choose_stack_weight(list(exposure_seconds), configured_weight, is_spectral=is_spectral)
    if chosen is None:
        return WeightingMode.EQUAL
    if chosen == NOISE_WEIGHT_MODE:
        return WeightingMode.NOISE
    return {"wfwhm": WeightingMode.WFWHM}.get(chosen, WeightingMode.UNKNOWN)


def frame_weights(frames: Sequence[StackFrame], mode: WeightingMode) -> list[float] | None:
    """Give the weight the stacker would give each frame.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        All frames of the stack (old and new together).
    mode : `WeightingMode`
        The weighting mode.

    Returns
    -------
    weights : `list` [`float`] or `None`
        One weight per frame (the ``wfwhm`` weights have mean 1, the others
        are not scaled). ``None`` for the ``unknown``
        mode, for ``wfwhm`` when a frame has no star width, and for ``noise``
        when a frame has no sky and no read noise (its variance is 0, so its
        inverse is undefined).
    """
    if mode == WeightingMode.EQUAL:
        return [1.0] * len(frames)
    if mode == WeightingMode.NOISE:
        if any(frame.rate_variance <= 0.0 for frame in frames):
            return None
        return [1.0 / frame.rate_variance for frame in frames]
    if mode == WeightingMode.WFWHM:
        widths = [frame.star_width for frame in frames]
        if any(width is None for width in widths) or not widths:
            return None
        return wfwhm_weights([float(width) for width in widths if width is not None])
    return None


def forecast_stack_noise(frames: Sequence[StackFrame], mode: WeightingMode) -> NoiseForecast:
    """Predict the noise of a stack of the given frames.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        All frames of the stack.
    mode : `WeightingMode`
        The weighting mode, read from the stack run's record.

    Returns
    -------
    forecast : `NoiseForecast`
        The variance ``sum(w^2 v) / sum(w)^2`` in rate units, or ``None``
        with a reason when the frames, the mode or a star width is missing.
    """
    if not frames:
        return NoiseForecast(mode=mode, reason="The stack has no frames.")
    if mode == WeightingMode.UNKNOWN:
        return NoiseForecast(
            mode=mode, frame_count=len(frames), reason="The stack run's weighting mode was not recorded."
        )
    if mode == WeightingMode.GROUP:
        return NoiseForecast(
            mode=mode,
            frame_count=len(frames),
            reason="A stack of exposure groups needs the groups' facts: use forecast_group_noise.",
        )
    weights = frame_weights(frames, mode)
    if weights is None:
        why = (
            "Some frames have no sky noise and no read noise, so the noise weights are undefined."
            if mode == WeightingMode.NOISE
            else "Some frames have no star width, which the width weights need."
        )
        return NoiseForecast(mode=mode, frame_count=len(frames), reason=why)
    return forecast_noise_with_weights(frames, weights, mode)


def forecast_noise_with_weights(
    frames: Sequence[StackFrame],
    weights: Sequence[float],
    mode: WeightingMode = WeightingMode.UNKNOWN,
    weight_normalization: float | None = None,
) -> NoiseForecast:
    """Predict the noise of a stack from the weights the stacker really used.

    This is the same sum as `forecast_stack_noise`, but the weights are given,
    not rebuilt from a mode. Use it when the run recorded its weights
    (``stacking.frame_weight``): nothing about the mode needs to be guessed.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        All frames of the stack.
    weights : `Sequence` [`float`]
        One recorded weight per frame, in the same order.
    mode : `WeightingMode`, optional
        The mode to label the forecast with (informational only).
    weight_normalization : `float`, optional
        The number the weights are divided by, in place of their sum. The
        exposure-group combine needs it: its frame coefficient is
        ``W_g w_i / (sum(W) G_g)``, which divides by the sum of the group
        weights and not by the sum of the coefficients when a gain is not 1.

    Returns
    -------
    forecast : `NoiseForecast`
        The variance ``sum(w^2 v) / N^2`` in rate units, with ``N`` the
        normalization (the sum of the weights by default), or ``None`` with a
        reason when the lists differ in length or all weights are 0.
    """
    if len(weights) != len(frames):
        return NoiseForecast(
            mode=mode, frame_count=len(frames), reason="The weights and the frames do not match up."
        )
    total_weight = sum(weights) if weight_normalization is None else weight_normalization
    if total_weight <= 0.0:
        return NoiseForecast(mode=mode, frame_count=len(frames), reason="All frames have weight 0.")
    variance = (
        sum(w**2 * frame.rate_variance for w, frame in zip(weights, frames, strict=True)) / total_weight**2
    )
    return NoiseForecast(variance=variance, noise=math.sqrt(variance), frame_count=len(frames), mode=mode)


class WeightingModeEvidence(BaseModel):
    """Which mode the recorded weights of a stack run match.

    Parameters
    ----------
    mode : `WeightingMode`
        The mode that matched, or ``unknown``.
    also_matches : `list` [`WeightingMode`]
        Other modes that match as well (frames so alike that the modes
        cannot be told apart).
    reason : `str`
        One sentence saying what was found.
    """

    model_config = _MODEL_CONFIG

    mode: WeightingMode
    also_matches: list[WeightingMode] = Field(default_factory=list)
    reason: str


def identify_weighting_mode(
    frames: Sequence[StackFrame],
    recorded_weights: Sequence[float | None],
    *,
    group_frame_noises_adu: Mapping[float, float] | None = None,
) -> WeightingModeEvidence:
    """Find which weighting mode explains the weights a stack run recorded.

    The recorded weights are the ``stacking.frame_weight`` rows of the run.
    A row that was not measured (Siril does not report the weights of its
    noise and star-count modes) is ``None``. Both the recorded and the
    predicted weights are divided by their mean before they are compared.

    A stack the stacker split into several exposure groups
    (`fold_exposure_lengths` gives more than one length) is never matched to
    ``equal``, ``noise`` or ``wfwhm`` over all frames: those weights do not
    describe it. It is matched to the ``group`` mode when the group frame
    noises are given, and is ``unknown`` otherwise.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The frames of the run, in the order of `recorded_weights`.
    recorded_weights : `Sequence` [`float` or `None`]
        The run's recorded weights (for a group stack, the raw weights
        ``W_g w_i``, without the gain).
    group_frame_noises_adu : `Mapping` [`float`, `float`], optional
        For a group stack: the measured frame noise ``s_g`` of each group in
        ADU, keyed by the group's exposure length in seconds (the length
        `fold_exposure_lengths` gives it). See `frame_noise_from_group_weight`
        for getting it back from stored rows.

    Returns
    -------
    evidence : `WeightingModeEvidence`
        The matching mode (``equal`` first, then ``noise``, then ``wfwhm``;
        ``group`` for a group stack), or ``unknown`` with the reason.
    """
    if len(frames) != len(recorded_weights) or not frames:
        return WeightingModeEvidence(
            mode=WeightingMode.UNKNOWN, reason="The frames and the weights do not match up."
        )
    if any(weight is None for weight in recorded_weights):
        return WeightingModeEvidence(
            mode=WeightingMode.UNKNOWN,
            reason="The run's weights were not recorded (Siril does not report them for its noise mode).",
        )
    observed = np.asarray(recorded_weights, dtype=float)
    if observed.mean() <= 0.0:
        return WeightingModeEvidence(mode=WeightingMode.UNKNOWN, reason="The recorded weights are all 0.")
    observed = observed / observed.mean()
    group_lengths = fold_exposure_lengths([frame.exposure_seconds for frame in frames])
    if len(set(group_lengths)) > 1:
        return _identify_group_weighting(frames, group_lengths, observed, group_frame_noises_adu)
    matches: list[WeightingMode] = []
    for candidate in (WeightingMode.EQUAL, WeightingMode.NOISE, WeightingMode.WFWHM):
        predicted = frame_weights(frames, candidate)
        if predicted is None:
            continue
        predicted_array = np.asarray(predicted, dtype=float)
        if predicted_array.mean() <= 0.0:
            continue
        predicted_array = predicted_array / predicted_array.mean()
        if float(np.max(np.abs(predicted_array - observed))) <= WEIGHT_MATCH_TOLERANCE:
            matches.append(candidate)
    if not matches:
        return WeightingModeEvidence(
            mode=WeightingMode.UNKNOWN, reason="The recorded weights match none of the known modes."
        )
    return WeightingModeEvidence(
        mode=matches[0],
        also_matches=matches[1:],
        reason=f"The recorded weights match the {matches[0].value} mode.",
    )


class TakeMoreResult(BaseModel):
    """How many more frames cut the noise to the target ratio.

    Parameters
    ----------
    frames_needed : `int` or `None`
        The smallest N that reaches the target. ``None`` when it cannot be
        found.
    predicted_noise_ratio : `float` or `None`
        The predicted noise after adding N frames over today's.
    target_noise_ratio : `float`
        The ratio aimed for.
    added_exposure_seconds : `float` or `None`
        N times the exposure of one new frame, in seconds.
    new_exposure_seconds : `float` or `None`
        The exposure of the new frames.
    confidence : `Confidence`
        Always ``design_estimate`` (decision S15).
    register_entry : `str`
        The register row behind the estimate.
    reason : `str` or `None`
        Why N could not be found, or a remark on the answer.
    floor_noise : `float` or `None`
        The stack's noise floor, in the stack's units, when one was found
        (see `StackNoiseFloor`). The noise cannot go below it however many
        frames are added.
    floor_variance_share : `float` or `None`
        The share of today's noise variance that is floor, from 0 to 1.
    floor_limited : `bool`
        True when more frames of this kind will not reach the target because
        of the floor. `frames_needed` is then ``None``.
    lowest_reachable_ratio : `float` or `None`
        With a floor: the ratio that very many more frames would reach.
    new_exposure_group : `bool`
        True when the new frames make a group of an exposure length the stack
        does not have yet (``group`` mode only).
    heaviest_group_changed : `bool`
        True when the new frames would change which group is heaviest, so the
        pair of stacks is confounded (``group`` mode only).
    """

    model_config = _MODEL_CONFIG

    frames_needed: int | None = None
    predicted_noise_ratio: float | None = None
    target_noise_ratio: float
    added_exposure_seconds: float | None = None
    new_exposure_seconds: float | None = None
    confidence: Confidence = Confidence.DESIGN_ESTIMATE
    register_entry: str = TAKE_MORE_ENTRY
    reason: str | None = None
    floor_noise: float | None = None
    floor_variance_share: float | None = None
    floor_limited: bool = False
    lowest_reachable_ratio: float | None = None
    new_exposure_group: bool = False
    heaviest_group_changed: bool = False


def _new_frames(
    template: Sequence[StackFrame], count: int, new_exposure_seconds: float | None
) -> list[StackFrame]:
    """Make `count` new frames by cycling through the template frames.

    Parameters
    ----------
    template : `Sequence` [`StackFrame`]
        Past frames of the same capture setting, in time order. Their sky
        rates and star widths are the distribution the new frames follow.
    count : `int`
        How many new frames.
    new_exposure_seconds : `float` or `None`
        The exposure of the new frames. ``None`` keeps each template frame's
        own exposure.

    Returns
    -------
    frames : `list` [`StackFrame`]
        The new frames.
    """
    frames = []
    for index in range(count):
        source = template[index % len(template)]
        frames.append(
            source.model_copy(
                update={
                    "frame_id": f"new_{index}",
                    **({"exposure_seconds": new_exposure_seconds} if new_exposure_seconds else {}),
                }
            )
        )
    return frames


def new_frames_like(
    template: Sequence[StackFrame], count: int, new_exposure_seconds: float | None = None
) -> list[StackFrame]:
    """Make the frames a "take N more" plan would add, for a caller to place.

    This is the public form of the frame maker that `frames_to_cut_noise` and
    `frames_to_cut_noise_in_groups` use, so a caller that builds its own plan
    (the forecast check) follows the same rule.

    Parameters
    ----------
    template : `Sequence` [`StackFrame`]
        Past frames of the capture setting. New frame ``k`` copies template
        frame ``k mod len(template)``.
    count : `int`
        How many new frames.
    new_exposure_seconds : `float`, optional
        The exposure of the new frames. ``None`` keeps each template frame's
        own exposure.

    Returns
    -------
    frames : `list` [`StackFrame`]
        The new frames.
    """
    return _new_frames(template, count, new_exposure_seconds)


def noise_ratio_after_adding(
    existing: Sequence[StackFrame],
    template: Sequence[StackFrame],
    count: int,
    mode: WeightingMode,
    new_exposure_seconds: float | None = None,
) -> NoiseForecast | None:
    """Predict the noise after adding frames, over the noise today.

    Parameters
    ----------
    existing : `Sequence` [`StackFrame`]
        The frames in the stack today.
    template : `Sequence` [`StackFrame`]
        Past frames the new ones follow (see `frames_to_cut_noise`).
    count : `int`
        How many frames to add.
    mode : `WeightingMode`
        The weighting mode.
    new_exposure_seconds : `float`, optional
        The exposure of the new frames.

    Returns
    -------
    forecast : `NoiseForecast` or `None`
        The forecast of the larger stack, or ``None`` if it cannot be made.
    """
    combined = [*existing, *_new_frames(template, count, new_exposure_seconds)]
    forecast = forecast_stack_noise(combined, mode)
    return forecast if forecast.noise is not None else None


def _smallest_count_reaching(
    reached: Callable[[int], bool], minimum_count: int, maximum_count: int
) -> int | None:
    """Find the smallest frame count for which `reached` is true.

    The count doubles until the target is reached, then it is bisected.

    Parameters
    ----------
    reached : `Callable` [[`int`], `bool`]
        Whether adding this many frames reaches the target.
    minimum_count : `int`
        The fewest frames that may be asked for (counts below it are not
        tried).
    maximum_count : `int`
        Where the search gives up.

    Returns
    -------
    count : `int` or `None`
        The smallest count that reaches the target, or ``None`` when even
        `maximum_count` does not.
    """
    lower = minimum_count - 1
    upper = minimum_count
    while upper <= maximum_count:
        if reached(upper):
            break
        lower = upper
        upper *= 2
    else:
        return None
    while upper - lower > 1:
        middle = (lower + upper) // 2
        if reached(middle):
            upper = middle
        else:
            lower = middle
    return upper


def frames_to_cut_noise(
    existing: Sequence[StackFrame],
    template: Sequence[StackFrame],
    mode: WeightingMode,
    *,
    new_exposure_seconds: float | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
    maximum_new_frames: int = MAXIMUM_NEW_FRAMES,
    floor: StackNoiseFloor | None = None,
) -> TakeMoreResult:
    """Find how many more frames cut the noise ("take N more").

    New frames follow the past frames of the same capture setting: frame k
    copies template frame ``k mod len(template)``, so the sky rates and star
    widths of the new frames match the target's history. When the new
    exposure differs from the old, the new frames keep their sky rates (per
    second) but change their read-noise share, so a read-noise-limited short
    frame counts for less than a long one.

    The search doubles N until the target is reached, then bisects.

    With a `floor`, the noise after adding frames is
    ``sqrt(floor + random_later)``, in variances (see
    `noise_ratio_with_floor`).
    When the floor makes up so much of today's noise that no number of frames
    reaches the target, no N is given: the result says so and states the
    floor.

    This function does not describe a stack of exposure groups; use
    `frames_to_cut_noise_in_groups` for the ``group`` mode.

    Parameters
    ----------
    existing : `Sequence` [`StackFrame`]
        The frames in the stack today, with the exposure and the sky rate
        each had.
    template : `Sequence` [`StackFrame`]
        Past frames of the capture setting the new frames will use.
    mode : `WeightingMode`
        The weighting mode (`predict_weighting_mode` for a future stack, or
        the recorded mode of the last run).
    new_exposure_seconds : `float`, optional
        The exposure the new frames will have. ``None`` keeps the template's.
    target_noise_ratio : `float`, optional
        The noise after over the noise now. The default is 0.707.
    maximum_new_frames : `int`, optional
        Where the search gives up.
    floor : `StackNoiseFloor`, optional
        The stack's noise floor, when one was found.

    Returns
    -------
    result : `TakeMoreResult`
        N and the predicted ratio, or ``None`` with a reason. The confidence
        is always ``design_estimate``.
    """
    current = forecast_stack_noise(existing, mode)
    if current.noise is None:
        return TakeMoreResult(target_noise_ratio=target_noise_ratio, reason=current.reason)
    if not template:
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason="There are no past frames of this setting to follow.",
        )

    def ratio_for(count: int) -> float | None:
        """Give the predicted noise ratio after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.

        Returns
        -------
        ratio : `float` or `None`
            New noise over current noise.
        """
        forecast = noise_ratio_after_adding(existing, template, count, mode, new_exposure_seconds)
        return noise_ratio_with_floor(current, forecast, floor)

    exposure = new_exposure_seconds or float(np.mean([frame.exposure_seconds for frame in template]))
    return _take_more_result(
        ratio_for,
        minimum_count=1,
        maximum_count=maximum_new_frames,
        target_noise_ratio=target_noise_ratio,
        exposure_seconds=exposure,
        floor=floor,
    )


def _take_more_result(
    ratio_for: Callable[[int], float | None],
    *,
    minimum_count: int,
    maximum_count: int,
    target_noise_ratio: float,
    exposure_seconds: float,
    floor: StackNoiseFloor | None,
) -> TakeMoreResult:
    """Search for N and write the answer, naming the floor when it limits.

    Parameters
    ----------
    ratio_for : `Callable` [[`int`], `float` or `None`]
        The predicted noise ratio after adding this many frames.
    minimum_count, maximum_count : `int`
        The fewest frames that may be asked for and where the search gives up.
    target_noise_ratio : `float`
        The ratio aimed for.
    exposure_seconds : `float`
        The exposure of one new frame.
    floor : `StackNoiseFloor` or `None`
        The stack's noise floor, when one was found.

    Returns
    -------
    result : `TakeMoreResult`
        The answer.
    """
    floor_fields: dict[str, float | None] = {
        "floor_noise": floor.floor_noise if floor is not None and floor.floor_detected else None,
        "floor_variance_share": (
            floor.floor_variance_share if floor is not None and floor.floor_detected else None
        ),
    }

    def reached(count: int) -> bool:
        """Say whether `count` more frames reach the target ratio.

        Parameters
        ----------
        count : `int`
            Frames to add.

        Returns
        -------
        reached : `bool`
            True when the predicted ratio is at or below the target.
        """
        ratio = ratio_for(count)
        return ratio is not None and ratio <= target_noise_ratio

    needed = _smallest_count_reaching(reached, minimum_count, maximum_count)
    if needed is None:
        limit_ratio = ratio_for(maximum_count)
        if floor is not None and floor.floor_detected and limit_ratio is not None:
            return TakeMoreResult(
                target_noise_ratio=target_noise_ratio,
                floor_limited=True,
                lowest_reachable_ratio=limit_ratio,
                reason=(
                    f"More frames of this kind will not reach the target: the stack has a noise floor of "
                    f"{floor.floor_noise:.3g} (stack units), {100 * (floor.floor_variance_share or 0.0):.0f} "
                    f"percent of today's noise variance, and {maximum_count} more frames would only reach "
                    f"{limit_ratio:.2f} of today's noise. The floor has to be found and removed first."
                ),
                **floor_fields,
            )
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason=f"The target was not reached with {maximum_count} more frames.",
            **floor_fields,
        )
    reason = "A design estimate: real subsets of M 57 came out 9 to 31 percent below the prediction (STK-36)."
    if floor_fields["floor_noise"] is not None:
        reason += (
            f" The stack has a noise floor of {floor_fields['floor_noise']:.3g} (stack units) that more "
            "frames do not remove; the answer includes it."
        )
    return TakeMoreResult(
        frames_needed=needed,
        predicted_noise_ratio=ratio_for(needed),
        target_noise_ratio=target_noise_ratio,
        added_exposure_seconds=needed * exposure_seconds,
        new_exposure_seconds=exposure_seconds,
        reason=reason,
        **floor_fields,
    )


# -----------------------------------------------------------------------------
# The noise floor
# -----------------------------------------------------------------------------


def aperture_noise_relative_error(aperture_count: int) -> float:
    """Give the relative error of an empty-aperture noise value.

    Parameters
    ----------
    aperture_count : `int`
        The number of empty apertures the noise came from. At least 2.

    Returns
    -------
    relative_error : `float`
        ``1.17 / sqrt(n)`` of the noise (about 12 percent for 100 apertures).

    Raises
    ------
    ValueError
        If fewer than 2 apertures are given.
    """
    if aperture_count < 2:
        raise ValueError("At least 2 apertures are needed to estimate a noise error.")
    return APERTURE_NOISE_ERROR_FACTOR / math.sqrt(aperture_count)


class NoiseStep(BaseModel):
    """One stack of a target's history, for the floor fit.

    Parameters
    ----------
    label : `str`
        A label for the stack.
    expected_variance : `float`
        The variance the model expects for this stack's frames
        (``stacking.expected_empty_aperture_noise_stack_unit`` squared), in
        the same units as the measured noise squared.
    expected_read_noise_variance : `float` or `None`
        The read-noise part of the expected variance, in the same units. Only
        needed when the read-noise term is free.
    measured_noise : `float`
        The measured empty-aperture noise
        (``stacking.empty_aperture_noise_stack_unit``).
    apertures : `int`
        How many empty apertures the noise came from.
    paired : `bool`
        True only when the error comes from a paired comparison (the
        leave-one-night-out rows). Such an error is smaller; `relative_error`
        then gives it.
    relative_error : `float` or `None`
        The relative error of the noise when it is not the unpaired
        ``1.17 / sqrt(n)``.
    normalized : `bool`
        True when `expected_variance`, `expected_read_noise_variance` and
        `measured_noise` were all divided by the reference flux of the stack
        (``stacking.noise_reference_flux_stack_unit``) and its square. Each
        stack has its own pixel scale (Siril's), so stacks can be fitted
        together only after this division. The relative errors are unchanged
        by it (the small error of the flux itself is not included).
    """

    model_config = _MODEL_CONFIG

    label: str
    expected_variance: float = Field(gt=0.0)
    expected_read_noise_variance: float | None = Field(default=None, ge=0.0)
    measured_noise: float = Field(gt=0.0)
    apertures: int = Field(ge=2)
    paired: bool = False
    relative_error: float | None = Field(default=None, gt=0.0)
    normalized: bool = False

    @property
    def noise_relative_error(self) -> float:
        """Give the relative error of the measured noise.

        Returns
        -------
        relative_error : `float`
            The given error for a paired step, else ``1.17 / sqrt(n)``.
        """
        if self.paired and self.relative_error is not None:
            return self.relative_error
        return aperture_noise_relative_error(self.apertures)


class NoiseFloorFit(BaseModel):
    """The result of fitting the noise floor.

    Parameters
    ----------
    steps_used : `int`
        Stacks in the fit.
    alpha : `float` or `None`
        How many times the expected variance the measured variance grows
        (1 means the model explains the noise).
    read_noise_scale : `float` or `None`
        The free read-noise factor, when that term was fitted.
    floor_variance : `float` or `None`
        The fitted constant b, in measured-noise-squared units.
    floor_variance_error : `float` or `None`
        One standard error of b.
    floor_detected : `bool`
        True when b is above 0 by `FLOOR_SIGMAS` standard errors.
    floor_noise : `float` or `None`
        The noise level the stack cannot go below, ``sqrt(b)``, when
        detected.
    error_kind : `str`
        ``"unpaired"``, ``"paired"`` or ``"mixed"``: how the step errors were
        found. The unpaired error is larger, so the fit is cautious.
    normalized : `bool`
        Whether the fit was made on noise divided by the reference flux.
        Then `floor_variance` and `floor_noise` are fractions of the
        reference stars' brightness, not stack units.
    degrees_of_freedom : `int`
        Steps minus fitted terms. With 0 the line passes through every point
        exactly, so the floor's error comes only from the assumed aperture
        errors and has no check from scatter.
    reason : `str`
        What happened, in one sentence.
    """

    model_config = _MODEL_CONFIG

    steps_used: int
    alpha: float | None = None
    read_noise_scale: float | None = None
    floor_variance: float | None = None
    floor_variance_error: float | None = None
    floor_detected: bool = False
    floor_noise: float | None = None
    error_kind: str = "unpaired"
    normalized: bool = False
    degrees_of_freedom: int = 0
    reason: str


def fit_noise_floor(steps: Sequence[NoiseStep], *, read_noise_term_free: bool = False) -> NoiseFloorFit:
    """Fit the noise floor over the history of a target.

    The model is ``measured_variance = alpha * expected_variance + b``.

    The fit is a weighted least squares. Each stack's measured variance has a
    relative error of twice its noise error (variance is noise squared). The
    weights come from the model, not from the measured variance alone: a
    first fit with the measured values gives a predicted variance for each
    stack, and the fit is repeated with the errors scaled to those predicted
    values. (Weights taken from the measured values pull the line toward the
    stacks that happened to measure low.)

    The stacks must be on one scale. Steps flagged `NoiseStep.normalized` are
    fitted together; if some steps are normalized and some are not, only the
    normalized ones are used. With 2 steps and 2 terms there are zero degrees
    of freedom: the line goes through both points, and nothing in the data
    checks the model.
    With the read-noise term free, the model is
    ``alpha * (expected - read_part) + c * read_part + b`` and needs at least
    3 stacks; without it, at least 2. A floor is never inferred from one
    shortfall.

    Parameters
    ----------
    steps : `Sequence` [`NoiseStep`]
        The target's stacks, in any order.
    read_noise_term_free : `bool`, optional
        Fit the read-noise part separately. Each step then needs
        `expected_read_noise_variance`.

    Returns
    -------
    fit : `NoiseFloorFit`
        The fit, or a result with ``floor_detected`` false and the reason
        ("not enough stack steps") when there are too few.
    """
    parameter_count = 3 if read_noise_term_free else 2
    if any(step.normalized for step in steps):
        steps = [step for step in steps if step.normalized]
    normalized = bool(steps) and all(step.normalized for step in steps)
    kinds = {"paired" if step.paired else "unpaired" for step in steps}
    error_kind = kinds.pop() if len(kinds) == 1 else "mixed"
    if len(steps) < parameter_count:
        return NoiseFloorFit(
            steps_used=len(steps),
            error_kind=error_kind,
            reason=f"Not enough stack steps: the fit needs {parameter_count}, and there are {len(steps)}.",
        )
    if read_noise_term_free and any(step.expected_read_noise_variance is None for step in steps):
        return NoiseFloorFit(
            steps_used=len(steps),
            error_kind=error_kind,
            reason="A free read-noise term needs the expected read-noise variance of every step.",
        )
    columns = []
    for step in steps:
        if read_noise_term_free:
            read_part = step.expected_read_noise_variance or 0.0
            columns.append([step.expected_variance - read_part, read_part, 1.0])
        else:
            columns.append([step.expected_variance, 1.0])
    design = np.asarray(columns, dtype=float)
    measured_variance = np.asarray([step.measured_noise**2 for step in steps], dtype=float)
    relative_errors = np.asarray([2.0 * step.noise_relative_error for step in steps])
    errors = relative_errors * measured_variance
    weighted_design = design / errors[:, None]
    solution, _residual, rank, _singular = np.linalg.lstsq(
        weighted_design, measured_variance / errors, rcond=None
    )
    if rank >= parameter_count:
        # Second pass: errors scaled to the model's predicted variance.
        predicted = design @ solution
        if np.all(predicted > 0.0):
            errors = relative_errors * predicted
            weighted_design = design / errors[:, None]
            solution, _residual, rank, _singular = np.linalg.lstsq(
                weighted_design, measured_variance / errors, rcond=None
            )
    if rank < parameter_count:
        return NoiseFloorFit(
            steps_used=len(steps),
            error_kind=error_kind,
            reason=(
                "The stacks do not differ enough to separate the terms "
                "(the history is too short or too regular)."
            ),
        )
    covariance = np.linalg.inv(weighted_design.T @ weighted_design)
    floor = float(solution[-1])
    floor_error = float(math.sqrt(covariance[-1, -1]))
    detected = floor > FLOOR_SIGMAS * floor_error
    return NoiseFloorFit(
        steps_used=len(steps),
        alpha=float(solution[0]),
        read_noise_scale=float(solution[1]) if read_noise_term_free else None,
        floor_variance=floor,
        floor_variance_error=floor_error,
        floor_detected=detected,
        floor_noise=math.sqrt(floor) if detected else None,
        error_kind=error_kind,
        normalized=normalized,
        degrees_of_freedom=len(steps) - parameter_count,
        reason=(
            (
                "A noise floor was found."
                if detected
                else "No floor: the fitted constant is not above 0 by two standard errors."
            )
            + (
                (
                    " The fit has zero degrees of freedom (the line passes through every step),"
                    " so the data do not check the model."
                )
                if len(steps) == parameter_count
                else ""
            )
            + ("" if normalized else " The steps are not normalized by the reference flux.")
        ),
    )


class StopSignal(BaseModel):
    """Whether more frames are still worth taking.

    Parameters
    ----------
    stopped : `bool` or `None`
        True when doubling the data is predicted to cut the noise by less
        than `MINIMUM_GAIN_FRACTION`. ``None`` when there is no floor fit to
        say.
    predicted_gain_fraction : `float` or `None`
        The predicted cut in measured noise from doubling the effective time.
    reason : `str`
        One sentence.
    """

    model_config = _MODEL_CONFIG

    stopped: bool | None = None
    predicted_gain_fraction: float | None = None
    reason: str


def stop_signal(fit: NoiseFloorFit, current_expected_variance: float) -> StopSignal:
    """Say whether the noise has stopped falling with more frames.

    Doubling the effective time halves the expected variance. With the fit
    ``V = alpha x + b`` the measured noise goes from ``sqrt(alpha x + b)`` to
    ``sqrt(alpha x / 2 + b)``.

    Parameters
    ----------
    fit : `NoiseFloorFit`
        A fit from `fit_noise_floor`.
    current_expected_variance : `float`
        The expected variance of today's stack, in the fit's units.

    Returns
    -------
    signal : `StopSignal`
        The stop decision. It is ``None`` (no signal) when the fit has no
        alpha, so a missing history never looks like a stop.
    """
    if fit.alpha is None or fit.floor_variance is None:
        return StopSignal(reason=f"No signal: {fit.reason}")
    if not fit.floor_detected:
        return StopSignal(stopped=False, reason="No floor was found, so more frames are expected to help.")
    now = fit.alpha * current_expected_variance + fit.floor_variance
    after = fit.alpha * current_expected_variance / 2.0 + fit.floor_variance
    if now <= 0.0 or after <= 0.0:
        return StopSignal(reason="The fit predicts a non-positive variance, so it cannot be used.")
    gain = 1.0 - math.sqrt(after / now)
    stopped = gain < MINIMUM_GAIN_FRACTION
    return StopSignal(
        stopped=stopped,
        predicted_gain_fraction=gain,
        reason=(
            f"Doubling the data would cut the noise by {100 * gain:.0f} percent"
            + (", below the limit; more frames will not help until the floor is fixed." if stopped else ".")
        ),
    )


# -----------------------------------------------------------------------------
# The noise floor of one stack
# -----------------------------------------------------------------------------


class StackNoiseFloor(BaseModel):
    """The part of a stack's measured noise that more frames do not remove.

    The floor is found from one stack: its measured empty-aperture noise
    against the random noise the frames alone predict. Both are in the
    stack's units (``stacking.empty_aperture_noise_stack_unit`` and
    ``stacking.expected_empty_aperture_noise_stack_unit``, the second already
    multiplied by Siril's ``stacking.normalization_scale``).

    Parameters
    ----------
    source : `str`
        ``"single_stack"`` (measured against predicted, one stack) or
        ``"fit"`` (`fit_noise_floor` over two or more stacks).
    measured_noise : `float` or `None`
        The stack's measured noise, in stack units.
    predicted_random_noise : `float` or `None`
        The noise the frames alone predict, in stack units.
    apertures : `int` or `None`
        The empty apertures behind the measured noise.
    floor_variance : `float` or `None`
        ``measured^2 - random^2`` when it is above 0 by `FLOOR_SIGMAS`
        standard errors; else ``None``.
    floor_variance_error : `float` or `None`
        One standard error of the difference, from the error of the measured
        noise only (the predicted value's error is not stored, so it is taken
        as negligible).
    floor_detected : `bool`
        Whether a floor was kept.
    floor_noise : `float` or `None`
        ``sqrt(floor_variance)``. In stack units for a ``single_stack`` floor.
    floor_to_random_variance_ratio : `float` or `None`
        The floor variance over the random variance of the same stack. This
        is how the floor enters a forecast (`noise_ratio_with_floor`): a
        dimensionless fraction of the model's own variance, so it needs no
        unit conversion between the forecast's rate units and the stack's.
    floor_variance_share : `float` or `None`
        The floor's share of the measured variance, from 0 to 1.
    normalized_units : `bool`
        True when `floor_noise` is a fraction of the reference stars' flux
        (a fit result with no single stack to convert it).
    reason : `str`
        One or two sentences saying what was found and why.
    """

    model_config = _MODEL_CONFIG

    source: str
    measured_noise: float | None = None
    predicted_random_noise: float | None = None
    apertures: int | None = None
    floor_variance: float | None = None
    floor_variance_error: float | None = None
    floor_detected: bool = False
    floor_noise: float | None = None
    floor_to_random_variance_ratio: float | None = None
    floor_variance_share: float | None = None
    normalized_units: bool = False
    reason: str


def measure_stack_noise_floor(
    measured_noise: float, predicted_random_noise: float, apertures: int
) -> StackNoiseFloor:
    """Find a stack's noise floor from its measured and predicted noise.

    ``floor^2 = measured^2 - random^2``. It is kept only when it is above 0 by
    `FLOOR_SIGMAS` standard errors. The measured noise has the relative error
    ``1.17 / sqrt(n)`` (`aperture_noise_relative_error`), so its variance has
    twice that, and the error of the difference is
    ``2 * relative_error * measured^2``. The predicted random noise comes from
    the frames' own sky and read noise and the stored weights; no error is
    stored for it, so it is taken as exact. That is the one assumption of this
    test, and it makes the test slightly too ready to find a floor.

    Parameters
    ----------
    measured_noise : `float`
        The stack's measured empty-aperture noise, in stack units.
    predicted_random_noise : `float`
        The stack's predicted noise, in stack units (with Siril's
        normalization scale applied).
    apertures : `int`
        The number of empty apertures behind the measured noise (at least
        2).

    Returns
    -------
    floor : `StackNoiseFloor`
        The floor, or a result with ``floor_detected`` false and the reason.

    Raises
    ------
    ValueError
        If a noise is not positive or there are fewer than 2 apertures.
    """
    if measured_noise <= 0.0 or predicted_random_noise <= 0.0:
        raise ValueError("Both noise values must be positive.")
    relative_error = aperture_noise_relative_error(apertures)
    measured_variance = measured_noise**2
    random_variance = predicted_random_noise**2
    difference = measured_variance - random_variance
    error = 2.0 * relative_error * measured_variance
    common = {
        "source": "single_stack",
        "measured_noise": measured_noise,
        "predicted_random_noise": predicted_random_noise,
        "apertures": apertures,
        "floor_variance_error": error,
    }
    if difference <= FLOOR_SIGMAS * error:
        return StackNoiseFloor(
            **common,
            reason=(
                "No floor: the measured noise does not exceed the predicted random noise by "
                f"{FLOOR_SIGMAS:g} standard errors ({difference / error:.1f} found)."
            ),
        )
    return StackNoiseFloor(
        **common,
        floor_variance=difference,
        floor_detected=True,
        floor_noise=math.sqrt(difference),
        floor_to_random_variance_ratio=difference / random_variance,
        floor_variance_share=difference / measured_variance,
        reason=(
            f"A floor of {math.sqrt(difference):.3g} (stack units) was found, {difference / error:.1f} "
            f"standard errors above 0 and {100 * difference / measured_variance:.0f} percent of the "
            "measured noise variance."
        ),
    )


def choose_noise_floor(
    single_stack: StackNoiseFloor | None,
    fit: NoiseFloorFit | None = None,
    current_expected_variance: float | None = None,
) -> StackNoiseFloor | None:
    """Pick the floor the forecast uses when one or both estimates exist.

    There are two estimates. `measure_stack_noise_floor` uses the one stack
    being extended and the library's own prediction (whose scale was checked
    on the half-stack noise, within 1 to 5 percent). `fit_noise_floor` uses
    two or more stacks and fits the model's scale as well, so it does not
    depend on the prediction being right in scale. The rule:

    1. A fit with at least one degree of freedom (the stacks' scatter checks
       the line) that finds a floor wins. Its floor enters the forecast as a
       fraction ``b / (alpha * expected_variance)`` of today's stack.
    2. Otherwise the single-stack result is used, found or not. A fit that
       found no floor with a degree of freedom, or one that has none, is
       named in the reason but does not override it.
    3. With no single-stack result, a fit that found a floor is used as it
       is (its reason says it has no check when it has none).

    Parameters
    ----------
    single_stack : `StackNoiseFloor` or `None`
        The result of `measure_stack_noise_floor`, or ``None`` when the stack
        has no stored predicted noise.
    fit : `NoiseFloorFit` or `None`
        The result of `fit_noise_floor` over the target's stacks, if any.
    current_expected_variance : `float`, optional
        The expected variance of today's stack, in the fit's units. Needed to
        use a fit.

    Returns
    -------
    floor : `StackNoiseFloor` or `None`
        The chosen floor, or ``None`` when there is no estimate at all.
    """
    fit_floor = None
    fit_note = ""
    if fit is not None and fit.floor_variance is not None and fit.alpha is not None:
        if current_expected_variance is None or current_expected_variance <= 0.0 or fit.alpha <= 0.0:
            fit_note = " The fit could not be used: today's expected variance is missing."
        elif fit.floor_detected and fit.floor_variance > 0.0:
            ratio = fit.floor_variance / (fit.alpha * current_expected_variance)
            share = ratio / (1.0 + ratio)
            random_noise = single_stack.predicted_random_noise if single_stack is not None else None
            stack_units = random_noise is not None
            floor_noise = math.sqrt(ratio) * random_noise if random_noise is not None else fit.floor_noise
            fit_floor = StackNoiseFloor(
                source="fit",
                measured_noise=single_stack.measured_noise if single_stack else None,
                predicted_random_noise=single_stack.predicted_random_noise if single_stack else None,
                apertures=single_stack.apertures if single_stack else None,
                floor_variance=fit.floor_variance,
                floor_variance_error=fit.floor_variance_error,
                floor_detected=True,
                floor_noise=floor_noise,
                floor_to_random_variance_ratio=ratio,
                floor_variance_share=share,
                normalized_units=not stack_units,
                reason=f"The fit over {fit.steps_used} stacks found a floor. {fit.reason}",
            )
            if fit.degrees_of_freedom >= 1 or single_stack is None:
                return fit_floor
            fit_note = " A two-stack fit also found a floor, but it has no degree of freedom to check it."
        else:
            fit_note = (
                f" The fit over {fit.steps_used} stacks found no floor"
                + ("" if fit.degrees_of_freedom >= 1 else " (and has no degree of freedom)")
                + "."
            )
    if single_stack is None:
        return None
    return single_stack.model_copy(update={"reason": single_stack.reason + fit_note})


def noise_ratio_with_floor(
    current: NoiseForecast, later: NoiseForecast | None, floor: StackNoiseFloor | None
) -> float | None:
    """Give the predicted noise after over the noise now, floor included.

    The model gives the random variance of today's stack, ``V_now``, and of
    the later stack, ``V_later``, in one set of units. A floor is carried as a
    fraction ``f`` of ``V_now``
    (`StackNoiseFloor.floor_to_random_variance_ratio`), so no unit
    conversion is needed. The measured noise today is
    ``V_now (1 + f)``; later it is ``V_later + f V_now tau^2``, so the ratio is

        sqrt((V_later + f V_now tau^2) / (V_now (1 + f))) / tau

    where ``tau`` is 1, except in the ``group`` mode, where the combine scales
    the result by the mean exposure ``t_bar`` and ``tau = t_bar_later /
    t_bar_now``. The floor is taken as a fixed pattern on the sky in rate
    units (the cause the validation favors: faint stars), so it follows
    ``t_bar`` in the stack's scale. The last division by ``tau`` takes the
    ratio back out of the stack's scale. The stack's measured noise is
    compared as a fraction of its reference stars' flux (the normalized
    noise), and that flux follows ``t_bar`` as well, so the factor cancels in
    what is measured; the ratio returned here is the one to compare with it.
    Without a mean exposure ``tau`` is 1 and nothing changes.

    Parameters
    ----------
    current : `NoiseForecast`
        The forecast of today's stack.
    later : `NoiseForecast` or `None`
        The forecast of the larger stack.
    floor : `StackNoiseFloor` or `None`
        The floor, if one was found.

    Returns
    -------
    ratio : `float` or `None`
        Later noise over today's, as a fraction of the stars' flux; ``None``
        when a forecast is missing.
    """
    if later is None or later.noise is None or later.variance is None:
        return None
    if current.noise is None or current.variance is None or not current.noise:
        return None
    tau = 1.0
    if current.mean_exposure_seconds and later.mean_exposure_seconds:
        tau = later.mean_exposure_seconds / current.mean_exposure_seconds
    if floor is None or not floor.floor_detected or floor.floor_to_random_variance_ratio is None:
        return later.noise / current.noise / tau
    fraction = floor.floor_to_random_variance_ratio
    later_total = later.variance + fraction * current.variance * tau**2
    now_total = current.variance * (1.0 + fraction)
    return math.sqrt(later_total / now_total) / tau


# -----------------------------------------------------------------------------
# Stacks combined from exposure groups
# -----------------------------------------------------------------------------


class StackGroup(BaseModel):
    """One exposure group of a stack, as the combine sees it.

    The stacker stacks each exposure length on its own (Siril, ``wfwhm``
    weights inside the group) and then averages the groups with
    ``W_g = N_g t_g^2 / s_g^2``. See the module description.

    Parameters
    ----------
    frames : `tuple` [`StackFrame`, ...]
        The frames stacked in the group. A planned group holds its new frames.
    frame_noise_adu : `float` or `None`
        ``s_g``: the noise of one raw frame of the group, in ADU, as the
        combine measured it (the ``frame_noise_counts`` entry of the stack's
        ``exposure_group_weights`` diagnostics).
        `frame_noise_from_group_weight` gets it back from stored rows.
        ``None`` means unknown.
    gain : `float`
        ``G_g``: the brightness gain the combine applied to the group (the
        ``stacking.group_gain_applied`` row; 1 for the reference group).
    electrons_per_adu : `float` or `None`
        The camera's gain at this setting, in electrons per ADU. It converts a
        `StackFrame` (electrons) to the combine's ADU, and is needed only to
        predict the frame noise of a planned group.
    noise_predicted : `bool`
        True when `frame_noise_adu` was predicted (a planned group), not
        measured.
    in_group_weights : `tuple` [`float`, ...] or `None`
        Siril's recorded weights inside the group, one per frame. Used only
        when the frames have no star widths to rebuild ``wfwhm`` from.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, frozen=True)

    frames: tuple[StackFrame, ...]
    frame_noise_adu: float | None = Field(default=None, gt=0.0)
    gain: float = Field(default=1.0, gt=0.0)
    electrons_per_adu: float | None = Field(default=None, gt=0.0)
    noise_predicted: bool = False
    in_group_weights: tuple[float, ...] | None = None

    @property
    def mean_exposure_seconds(self) -> float:
        """Give the mean exposure of the group's frames (``t_g``).

        Returns
        -------
        exposure : `float`
            The mean, in seconds (0 for a group with no frames).
        """
        if not self.frames:
            return 0.0
        return math.fsum(frame.exposure_seconds for frame in self.frames) / len(self.frames)


def frame_noise_from_group_weight(group_weight: float, frame_count: int, exposure_seconds: float) -> float:
    """Get a group's frame noise back from its stored combine weight.

    The combine weight is ``W_g = N_g t_g^2 / s_g^2`` and the stored
    ``stacking.frame_weight`` rows of a group add up to ``W_g``, so
    ``s_g = t_g sqrt(N_g / W_g)``. The part 2a rows do not store ``s_g`` on its
    own; this is how a forecast gets it from rows.

    Parameters
    ----------
    group_weight : `float`
        The sum of the group's ``stacking.frame_weight`` rows.
    frame_count : `int`
        Frames stacked in the group (``stacking.group_frames_stacked_count``).
    exposure_seconds : `float`
        The group's exposure (``stacking.group_exposure_s``).

    Returns
    -------
    frame_noise_adu : `float`
        ``s_g`` in ADU.

    Raises
    ------
    ValueError
        If an input is not positive.
    """
    if group_weight <= 0.0 or frame_count <= 0 or exposure_seconds <= 0.0:
        raise ValueError("The group weight, frame count and exposure must be positive.")
    return exposure_seconds * math.sqrt(frame_count / group_weight)


def predict_group_frame_noise_adu(frames: Sequence[StackFrame], electrons_per_adu: float) -> float:
    """Predict the noise of one raw frame of a new group, in ADU.

    A raw frame holds the sky's photons and the read noise, so its variance is
    ``B t + RN^2`` electrons squared. The combine measures ``s_g`` in ADU, so
    the result is divided by the camera gain: this is the one place where a
    `StackFrame` (electrons) is converted to the combine's units. Validated
    on one 60 s M 27 frame: 35.1 ADU predicted, 37.7 measured (+7 percent,
    plan section 3.6.1). Only ratios of group weights matter, and the
    prediction is used only for planned groups.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The new group's frames (their mean variance is used).
    electrons_per_adu : `float`
        The camera's gain at the setting, in electrons per ADU.

    Returns
    -------
    frame_noise_adu : `float`
        ``s_g`` in ADU.

    Raises
    ------
    ValueError
        If there are no frames or the gain is not positive.
    """
    if not frames or electrons_per_adu <= 0.0:
        raise ValueError("Frames and a positive camera gain are needed.")
    variance_electrons = math.fsum(
        frame.sky_rate_e_per_s_per_px * frame.exposure_seconds + frame.read_noise_e**2 for frame in frames
    ) / len(frames)
    return math.sqrt(variance_electrons) / electrons_per_adu


def _in_group_weights(group: StackGroup) -> list[float] | None:
    """Give Siril's ``wfwhm`` weights inside one group.

    Parameters
    ----------
    group : `StackGroup`
        The group.

    Returns
    -------
    weights : `list` [`float`] or `None`
        Rebuilt from the frames' star widths when all have one, else the
        recorded in-group weights when they match the frames, else ``None``.
    """
    widths = [frame.star_width for frame in group.frames]
    if group.frames and all(width is not None for width in widths):
        return wfwhm_weights([float(width) for width in widths if width is not None])
    if group.in_group_weights is not None and len(group.in_group_weights) == len(group.frames):
        return list(group.in_group_weights)
    return None


class GroupCoefficients(BaseModel):
    """The raw weights and gains of a stack of exposure groups.

    Parameters
    ----------
    raw_weights : `list` [`float`] or `None`
        ``W_g w_i`` for every frame, groups in order (the group rule's output,
        without the gain).
    gains : `list` [`float`]
        ``G_g`` for every frame.
    total_group_weight : `float` or `None`
        ``sum(W_g)``: the number the coefficients are divided by.
    reason : `str` or `None`
        Why there are no weights.
    """

    model_config = _MODEL_CONFIG

    raw_weights: list[float] | None = None
    gains: list[float] = Field(default_factory=list)
    total_group_weight: float | None = None
    reason: str | None = None


def group_coefficients(groups: Sequence[StackGroup]) -> GroupCoefficients:
    """Work out each frame's raw weight with the library's group rule.

    Uses ``astrometricslib.group_frame_weights``, the rule the stacker's own
    records use, so the forecast and the record cannot differ.

    Parameters
    ----------
    groups : `Sequence` [`StackGroup`]
        The groups of the stack.

    Returns
    -------
    coefficients : `GroupCoefficients`
        The weights, or the reason there are none (no frames, a missing frame
        noise, frames with no star width).
    """
    if not groups or any(not group.frames for group in groups):
        return GroupCoefficients(reason="A group has no frames.")
    in_group: list[list[float] | None] = []
    for group in groups:
        weights = _in_group_weights(group)
        if weights is None:
            return GroupCoefficients(
                reason=(
                    f"Some frames of the {group.mean_exposure_seconds:g} s group have no star width, "
                    "which the width weights inside the group need."
                )
            )
        in_group.append(weights)
    noises = [group.frame_noise_adu for group in groups]
    if any(noise is None for noise in noises):
        return GroupCoefficients(reason="A group has no frame noise (s_g), so its weight is unknown.")
    result = group_frame_weights(
        in_group,
        [group.mean_exposure_seconds for group in groups],
        [len(group.frames) for group in groups],
        noises,
    )
    if any(weights is None for weights in result.weights):
        return GroupCoefficients(reason="; ".join(reason for reason in result.reasons if reason))
    raw: list[float] = []
    gains: list[float] = []
    for group, weights in zip(groups, result.weights, strict=True):
        raw.extend(weights or ())
        gains.extend([group.gain] * len(group.frames))
    total = math.fsum(raw)
    if total <= 0.0:
        return GroupCoefficients(reason="All frames have weight 0.")
    return GroupCoefficients(raw_weights=raw, gains=gains, total_group_weight=total)


def forecast_group_noise(groups: Sequence[StackGroup]) -> NoiseForecast:
    """Predict the noise of a stack combined from exposure groups.

    Frame ``i`` of group ``g`` enters the combined image, in rate units, with
    the coefficient ``a_i = W_g w_i / (sum(W) G_g)``; ``w_i`` is its ``wfwhm``
    share inside the group. The variance in rate units is ``sum(a_i^2 v_i)``
    (`forecast_noise_with_weights` with the gain divided out of each weight
    and ``sum(W)`` as the normalization). The combine then scales the result
    by the mean exposure per frame ``t_bar``, so the returned variance and
    noise are the rate-unit values times ``t_bar^2`` and ``t_bar``: they are in
    the stack's scale, and the ratio of two of them is the ratio of the
    stacks' measured noise, up to the models' agreement
    (``expected_noise.expected_exposure_group_noise`` is the same model in
    stack units; a test compares them).

    Parameters
    ----------
    groups : `Sequence` [`StackGroup`]
        The groups, with their frames, frame noises and gains.

    Returns
    -------
    forecast : `NoiseForecast`
        Mode ``group``; no value and a reason when a group's facts are
        missing.
    """
    frame_count = sum(len(group.frames) for group in groups)
    coefficients = group_coefficients(groups)
    if coefficients.raw_weights is None:
        return NoiseForecast(mode=WeightingMode.GROUP, frame_count=frame_count, reason=coefficients.reason)
    frames = [frame for group in groups for frame in group.frames]
    weights = [raw / gain for raw, gain in zip(coefficients.raw_weights, coefficients.gains, strict=True)]
    base = forecast_noise_with_weights(
        frames, weights, WeightingMode.GROUP, weight_normalization=coefficients.total_group_weight
    )
    if base.variance is None:
        return base
    mean_exposure = math.fsum(frame.exposure_seconds for frame in frames) / len(frames)
    variance = base.variance * mean_exposure**2
    return NoiseForecast(
        variance=variance,
        noise=math.sqrt(variance),
        frame_count=frame_count,
        mode=WeightingMode.GROUP,
        mean_exposure_seconds=mean_exposure,
    )


def _identify_group_weighting(
    frames: Sequence[StackFrame],
    group_lengths: Sequence[float | None],
    observed: np.ndarray,
    group_frame_noises_adu: Mapping[float, float] | None,
) -> WeightingModeEvidence:
    """Match the recorded weights of a group stack to the group mode.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The run's frames, in the order of `observed`.
    group_lengths : `Sequence` [`float` or `None`]
        The group length of each frame (`fold_exposure_lengths`).
    observed : `numpy.ndarray`
        The recorded weights divided by their mean.
    group_frame_noises_adu : `Mapping` [`float`, `float`] or `None`
        ``s_g`` of each group, keyed by group length.

    Returns
    -------
    evidence : `WeightingModeEvidence`
        ``group`` when the weights match, else ``unknown`` with the reason.
    """
    if not group_frame_noises_adu:
        return WeightingModeEvidence(
            mode=WeightingMode.UNKNOWN,
            reason=(
                "The stack was combined from exposure groups; its weights can be matched only with "
                "the groups' frame noises (s_g)."
            ),
        )
    lengths = sorted({length for length in group_lengths if length is not None})
    indices_by_length = {
        length: [index for index, group_length in enumerate(group_lengths) if group_length == length]
        for length in lengths
    }
    groups = []
    for length in lengths:
        noise = group_frame_noises_adu.get(length)
        if noise is None:
            return WeightingModeEvidence(
                mode=WeightingMode.UNKNOWN, reason=f"The frame noise of the {length:g} s group is missing."
            )
        groups.append(
            StackGroup(frames=tuple(frames[i] for i in indices_by_length[length]), frame_noise_adu=noise)
        )
    coefficients = group_coefficients(groups)
    if coefficients.raw_weights is None:
        return WeightingModeEvidence(mode=WeightingMode.UNKNOWN, reason=str(coefficients.reason))
    predicted = np.zeros(len(frames))
    position = 0
    for length in lengths:
        for index in indices_by_length[length]:
            predicted[index] = coefficients.raw_weights[position]
            position += 1
    if predicted.mean() <= 0.0:
        return WeightingModeEvidence(mode=WeightingMode.UNKNOWN, reason="The predicted weights are all 0.")
    predicted = predicted / predicted.mean()
    if float(np.max(np.abs(predicted - observed))) <= WEIGHT_MATCH_TOLERANCE:
        return WeightingModeEvidence(
            mode=WeightingMode.GROUP, reason="The recorded weights match the exposure-group weights."
        )
    return WeightingModeEvidence(
        mode=WeightingMode.UNKNOWN, reason="The recorded weights match the exposure-group weights of no mode."
    )


class FramePlacement(BaseModel):
    """Where the stacker would put one new frame.

    Parameters
    ----------
    frame_id : `str`
        The new frame.
    own_exposure_seconds : `float`
        The frame's own exposure.
    group_exposure_seconds : `float` or `None`
        The group length it lands in (`fold_exposure_lengths`); ``None`` when
        the stacker would not split the frames into groups.
    outcome : `str`
        ``"joins_existing_group"``, ``"starts_new_group"`` or
        ``"folded_into_group"`` (its exposure length has fewer than 5 frames).
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    own_exposure_seconds: float
    group_exposure_seconds: float | None = None
    outcome: str


class GroupPlan(BaseModel):
    """The stack that adding frames would give, as the stacker would make it.

    Parameters
    ----------
    groups : `list` [`StackGroup`]
        The groups of the later stack (empty when it cannot be forecast).
    placements : `list` [`FramePlacement`]
        Where each new frame goes.
    forecast : `NoiseForecast`
        The later stack's predicted random noise (`forecast_group_noise`).
    heaviest_exposure_before, heaviest_exposure_after : `float` or `None`
        The group length with the largest combine weight today and later. The
        heaviest group is the reference the combine aligns to and sets the
        brightness scale from.
    heaviest_group_changed : `bool`
        True when the new frames change the heaviest group. The later stack's
        scale then differs from today's, so the two are confounded: the noise
        ratio is not a like-for-like comparison.
    folded_group_lengths : `list` [`float`]
        Groups that hold folded frames of another length. The stacker records
        no weights for such a group, so a stack check cannot confirm the
        forecast.
    reason : `str` or `None`
        Why the later stack cannot be forecast.
    """

    model_config = _MODEL_CONFIG

    groups: list[StackGroup] = Field(default_factory=list)
    placements: list[FramePlacement] = Field(default_factory=list)
    forecast: NoiseForecast
    heaviest_exposure_before: float | None = None
    heaviest_exposure_after: float | None = None
    heaviest_group_changed: bool = False
    folded_group_lengths: list[float] = Field(default_factory=list)
    reason: str | None = None


def _refused_plan(reason: str, placements: list[FramePlacement] | None = None) -> GroupPlan:
    """Build a plan that cannot be forecast.

    Parameters
    ----------
    reason : `str`
        Why.
    placements : `list` [`FramePlacement`], optional
        Placements worked out before the refusal.

    Returns
    -------
    plan : `GroupPlan`
        A plan with no groups and a forecast that carries the reason.
    """
    return GroupPlan(
        placements=placements or [],
        forecast=NoiseForecast(mode=WeightingMode.GROUP, reason=reason),
        reason=reason,
    )


def _heaviest_exposure(groups: Sequence[StackGroup], lengths: Sequence[float]) -> float | None:
    """Find the group length with the largest combine weight.

    Parameters
    ----------
    groups : `Sequence` [`StackGroup`]
        Groups with a frame noise.
    lengths : `Sequence` [`float`]
        The group length of each group.

    Returns
    -------
    length : `float` or `None`
        ``argmax(N t^2 / s^2)``, or ``None`` when a frame noise is missing.
    """
    if not groups or any(group.frame_noise_adu is None for group in groups):
        return None
    weights = [
        len(group.frames) * group.mean_exposure_seconds**2 / (group.frame_noise_adu or 1.0) ** 2
        for group in groups
    ]
    return lengths[int(np.argmax(weights))]


def plan_group_stack(
    existing_groups: Sequence[StackGroup],
    new_frames: Sequence[StackFrame],
    *,
    electrons_per_adu: float | None = None,
) -> GroupPlan:
    """Work out the stack that adding frames would give, group by group.

    The stacker's own fold rule (``fold_exposure_lengths``) decides where
    every frame, old and new, goes:

    * a new frame whose length matches an existing group joins it (the group
      keeps its measured ``s_g`` and gain; its ``wfwhm`` weights are rebuilt
      over old and new frames together, as the stacker does);
    * new frames of a length with 5 or more frames in all start a new group.
      Its ``s_g`` is predicted in ADU from the frames' sky and read noise
      (`predict_group_frame_noise_adu`) and it keeps the gain of the nearest
      existing group (nearest in the ratio of exposures, the fold rule's
      measure);
    * new frames of a length with fewer than 5 frames are folded into the
      nearest large group. That group then holds frames of another length, so
      the stacker records no weights for it (listed in
      `GroupPlan.folded_group_lengths`); the forecast is still made, with the
      folded frames weighted as members of the group;
    * when no length has 5 frames, or the existing groups would be regrouped,
      the stacker would not make the groups the forecast assumes, and the
      plan is refused with a reason.

    Parameters
    ----------
    existing_groups : `Sequence` [`StackGroup`]
        The groups of today's stack, each with its measured ``s_g`` and gain.
    new_frames : `Sequence` [`StackFrame`]
        The frames to add (electrons; the conversion to ADU uses the camera
        gain).
    electrons_per_adu : `float`, optional
        The camera gain for a new group when the existing groups do not carry
        one.

    Returns
    -------
    plan : `GroupPlan`
        The later stack's groups, the placement of every new frame, the
        forecast, and whether the heaviest group changes.
    """
    existing = list(existing_groups)
    new = list(new_frames)
    if not existing:
        return _refused_plan("The stack has no exposure groups.")
    old_exposures = [frame.exposure_seconds for group in existing for frame in group.frames]
    destinations = fold_exposure_lengths(old_exposures + [frame.exposure_seconds for frame in new])
    if destinations[0] is None:
        return _refused_plan(
            "No exposure length would have 5 frames, so the stacker would stack every frame in one "
            "group of mixed lengths; that stack is not forecast."
        )
    existing_lengths: list[float] = []
    cursor = 0
    for group in existing:
        group_destinations = {destinations[cursor + offset] for offset in range(len(group.frames))}
        cursor += len(group.frames)
        if len(group_destinations) != 1 or None in group_destinations:
            return _refused_plan(
                "Adding these frames would regroup the frames already stacked; a forecast cannot follow that."
            )
        existing_lengths.append(next(iter(group_destinations)))
    if len(set(existing_lengths)) != len(existing_lengths):
        return _refused_plan("Two existing groups would fall into one group; a forecast cannot follow that.")
    new_destinations = destinations[cursor:]
    placements = []
    frames_by_length: dict[float, list[StackFrame]] = {}
    for frame, destination in zip(new, new_destinations, strict=True):
        own_length = fold_exposure_lengths([frame.exposure_seconds])[0]
        if destination in existing_lengths:
            outcome = "joins_existing_group" if own_length == destination else "folded_into_group"
        else:
            outcome = "starts_new_group"
        placements.append(
            FramePlacement(
                frame_id=frame.frame_id,
                own_exposure_seconds=frame.exposure_seconds,
                group_exposure_seconds=destination,
                outcome=outcome,
            )
        )
        frames_by_length.setdefault(float(destination), []).append(frame)

    planned: list[StackGroup] = []
    planned_lengths: list[float] = []
    for group, length in zip(existing, existing_lengths, strict=True):
        added = frames_by_length.pop(float(length), [])
        planned.append(group.model_copy(update={"frames": (*group.frames, *added)}) if added else group)
        planned_lengths.append(float(length))
    for length in sorted(frames_by_length):
        added = frames_by_length[length]
        nearest = min(
            range(len(existing)),
            key=lambda position, target=length: abs(
                math.log(max(target, 1e-9) / max(float(existing_lengths[position]), 1e-9))
            ),
        )
        camera_gain = existing[nearest].electrons_per_adu or electrons_per_adu
        if camera_gain is None:
            return _refused_plan(
                f"The camera gain (electrons per ADU) is needed to predict the frame noise of the new "
                f"{length:g} s group.",
                placements,
            )
        planned.append(
            StackGroup(
                frames=tuple(added),
                frame_noise_adu=predict_group_frame_noise_adu(added, camera_gain),
                gain=existing[nearest].gain,
                electrons_per_adu=camera_gain,
                noise_predicted=True,
            )
        )
        planned_lengths.append(length)

    heaviest_before = _heaviest_exposure(existing, [float(length) for length in existing_lengths])
    heaviest_after = _heaviest_exposure(planned, planned_lengths)
    changed = heaviest_before is not None and heaviest_after is not None and heaviest_before != heaviest_after
    folded = sorted({
        float(placement.group_exposure_seconds)
        for placement in placements
        if placement.outcome == "folded_into_group" and placement.group_exposure_seconds is not None
    })
    forecast = forecast_group_noise(planned)
    return GroupPlan(
        groups=planned,
        placements=placements,
        forecast=forecast,
        heaviest_exposure_before=heaviest_before,
        heaviest_exposure_after=heaviest_after,
        heaviest_group_changed=changed,
        folded_group_lengths=folded,
        reason=forecast.reason,
    )


def frames_to_cut_noise_in_groups(
    existing_groups: Sequence[StackGroup],
    template: Sequence[StackFrame],
    *,
    new_exposure_seconds: float | None = None,
    electrons_per_adu: float | None = None,
    floor: StackNoiseFloor | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
    maximum_new_frames: int = MAXIMUM_NEW_FRAMES,
) -> TakeMoreResult:
    """Find how many more frames cut the noise of a group stack.

    Each trial adds N copies of the template frames (cycled, as in
    `frames_to_cut_noise`) and builds the later stack with `plan_group_stack`,
    so the number comes from the model the gate checks. A new exposure length
    is never suggested with fewer than `MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH`
    frames: the search starts there, because the stacker would fold a smaller
    set into another group. A floor is applied as in `frames_to_cut_noise`.

    Parameters
    ----------
    existing_groups : `Sequence` [`StackGroup`]
        The groups of today's stack.
    template : `Sequence` [`StackFrame`]
        Past frames of the capture setting the new frames will use.
    new_exposure_seconds : `float`, optional
        The exposure the new frames will have. ``None`` keeps the template's.
    electrons_per_adu : `float`, optional
        The camera gain, for a new group (see `plan_group_stack`).
    floor : `StackNoiseFloor`, optional
        The stack's noise floor, when one was found.
    target_noise_ratio : `float`, optional
        The noise after over the noise now. The default is 0.707.
    maximum_new_frames : `int`, optional
        Where the search gives up.

    Returns
    -------
    result : `TakeMoreResult`
        N and the predicted ratio, or ``None`` with a reason (a floor that
        limits the answer, a group the stacker would leave out, a missing
        fact). The result also says whether the frames start a new group and
        whether the heaviest group changes.
    """
    current = forecast_group_noise(existing_groups)
    if current.noise is None:
        return TakeMoreResult(target_noise_ratio=target_noise_ratio, reason=current.reason)
    if not template:
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason="There are no past frames of this setting to follow.",
        )
    exposure = new_exposure_seconds or float(np.mean([frame.exposure_seconds for frame in template]))
    existing_lengths = {
        fold_exposure_lengths([frame.exposure_seconds])[0]
        for group in existing_groups
        for frame in group.frames
    }
    new_length = fold_exposure_lengths([exposure])[0]
    starts_new_group = new_length not in existing_lengths
    minimum_count = MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH if starts_new_group else 1

    def plan_for(count: int) -> GroupPlan:
        """Build the later stack after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.

        Returns
        -------
        plan : `GroupPlan`
            The plan.
        """
        return plan_group_stack(
            existing_groups,
            _new_frames(template, count, new_exposure_seconds),
            electrons_per_adu=electrons_per_adu,
        )

    first = plan_for(minimum_count)
    if first.forecast.noise is None:
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason=first.reason,
            new_exposure_group=starts_new_group,
        )

    def ratio_for(count: int) -> float | None:
        """Give the predicted noise ratio after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.

        Returns
        -------
        ratio : `float` or `None`
            Later noise over today's.
        """
        return noise_ratio_with_floor(current, plan_for(count).forecast, floor)

    result = _take_more_result(
        ratio_for,
        minimum_count=minimum_count,
        maximum_count=maximum_new_frames,
        target_noise_ratio=target_noise_ratio,
        exposure_seconds=exposure,
        floor=floor,
    )
    answer_plan = plan_for(result.frames_needed or minimum_count)
    notes = []
    if starts_new_group:
        notes.append(
            f"The new {exposure:g} s frames make a new exposure group; at least "
            f"{MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH} frames are asked for so the stacker keeps it."
        )
    if answer_plan.heaviest_group_changed:
        notes.append(
            "The new frames would make a different group the heaviest, which rescales the stack: "
            "the earlier and later stacks cannot be compared like for like."
        )
    if answer_plan.folded_group_lengths:
        notes.append("Some frames would be folded into another group, which records no weights.")
    return result.model_copy(
        update={
            "new_exposure_group": starts_new_group,
            "heaviest_group_changed": answer_plan.heaviest_group_changed,
            "reason": " ".join(filter(None, [result.reason, *notes])),
        }
    )
