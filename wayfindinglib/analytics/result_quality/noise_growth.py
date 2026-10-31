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
  library's own rule (`fold_exposure_lengths`). The folded frame is forecast
  as a member of that group, scaled to the group's reference frame (below);
* when no length would have 5 frames the stacker would make one group of
  mixed lengths, and the plan is refused;
* if the new frames make another group the heaviest, the later stack has a
  different brightness scale and the pair is confounded (flagged).

Folded groups. Siril normalizes each frame of a group to the group's
registration reference frame, so frame ``i`` of length ``t_i`` enters the
group stack multiplied by ``k_i = t_ref / t_i`` (1 for a group of one length),
and its noise in the group's units is ``k_i`` times its own. The variance term
of the frame is ``(a_i k_i / t_g)^2 v_i``, with ``t_g`` the group's mean
exposure that the combine used. `StackGroup.frame_noise_factors` carries the
noise of each frame relative to a frame of the group's own length, which is the
frame the stored ``s_g`` stands for (`frame_noise_from_group_weight`): the
stacker measures ``s_g`` as the median noise of a few raw frames sampled across
the group, and the folded frames are fewer than 5 in a larger group, so the
median is that of a frame of the group's own length. The stored rows do not say
which frames were sampled, so this is an approximation; it is exact when no
sampled frame was folded. The electron form of the factor,
``k_i sqrt((B_i t_i + RN^2) / (B_i t_len + RN^2))``, is used because the read
noise does not scale with the exposure and the folded frames are short; where
the sky rate and read noise are both unknown it falls back to the sky-limited
form ``k_i sqrt(t_i / t_len)``. The group's reference exposure is
``stacking.group_reference_exposure_s``; with no row for it the group cannot
be forecast.

What part 2a stores, and what this needs. The group's ``W_g`` is the sum of
its ``stacking.frame_weight`` rows, ``N_g`` is
``stacking.group_frames_stacked_count``, ``t_g`` is
``stacking.group_exposure_s`` (the mean exposure of a folded group) and
``G_g`` is ``stacking.group_gain_applied``;
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

The practical limit. With fixed-pattern noise, the count that reaches the
target can be very large for a small last gain (M 13: fixed-pattern noise of 49
percent of the noise variance asks for 1748 frames to cut the noise by 29
percent, and doubling alone cuts it by 14 percent). A count is given only when
it needs at most `PRACTICAL_TIME_FACTOR` times the stack's exposure time.
Beyond that, or when the target cannot be reached, the result gives the
diminishing-returns answer instead: the frames that double the stack's time
(`TakeMoreResult.doubling_frames`), the noise cut that doubling gives, and the
most cut any number of frames gives. When doubling would cut the noise by less
than `MINIMUM_DOUBLING_GAIN_FRACTION`, no frames are suggested.

The fixed-pattern noise
---------
One stack's fixed-pattern noise (`measure_stack_fixed_pattern_noise`). A
stack's measured empty-aperture noise is more than the frames alone predict
when something fixed is left in the apertures (faint stars, a calibration
residual). Both numbers are in the stack's units: the measured noise and the
stored
``stacking.expected_empty_aperture_noise_stack_unit``, which includes Siril's
normalization scale (``stacking.normalization_scale``). Then ``fixed_pattern^2
= measured^2 - random^2``, kept only when the variance ratio is above 1 by 2
standard errors. The error is the loss account's
(`total_variance_ratio_error`): the aperture error of the measured noise (see
below, applied to the variance) and the expected-noise model's own error (11
percent, STK-36), added in quadrature, so one error budget serves both.
Validation (plan section 3.6.1): M 27 60 s has clear fixed-pattern noise, M 81
has none at 2 sigma.

The forecast carries the fixed-pattern noise as a fraction ``f`` of the random
variance of today's stack, so the model's rate units need no conversion. Today
the measured variance is ``V_now (1 + f)``; with more frames it is
``V_later + f V_now`` (in the group mode, times the square of the change of
``t_bar``, because the fixed-pattern noise is taken as a fixed pattern on the
sky). When the fixed-pattern noise is most of the noise (a share of 0.5 or more
makes the 0.707 target unreachable), `frames_to_cut_noise` gives no N: it says
more frames will not reach the goal and states the fixed-pattern noise.

The fixed-pattern noise from a direct measurement
(`measure_stack_fixed_pattern_noise_from_odd_even_difference`). When the
library stack has an odd–even difference measurement, the fixed-pattern noise's
random part is that measurement, not the model. The odd-minus-even difference
of two half stacks cancels what is fixed on the sky, so, divided by the factor
that turns a difference of halves into the full stack's variance, it is the
full stack's random noise. With no fixed-pattern noise the measured full-stack
variance and this random variance are two independent estimates of the same
number. The test is their ratio ``F`` on the log scale: ``ln F`` has the
standard error ``sqrt(e1^2 + e2^2)`` with ``e = 2 x 1.17 / sqrt(n)`` for each,
and fixed-pattern noise is kept when ``ln F`` is above 0 by 2 standard errors
(one-sided). The model's own error is not in this test, because the model is
not in it. The fixed-pattern noise is
``measured^2 - random^2``. The measurement is used only when it is made from
this library stack (newer than the stack's noise row, in the same aperture
set); otherwise the fixed-pattern noise falls back to the model test above.

Which fixed-pattern noise estimate wins (`choose_fixed_pattern_noise`). A fit
over two or more stacks (`fit_fixed_pattern_noise`, below) also finds
fixed-pattern noise and does not rely on the prediction's scale. A fit with at
least one degree of freedom, so that the stacks' scatter checks it, wins when
it finds fixed-pattern noise. Otherwise the one-stack fixed-pattern noise is
used. A two-stack fit has no check, so it never overrides a one-stack result.
If there is no one-stack result, a fit that found a fixed-pattern noise is used
as it is.

The fit over stacks. A stack stops improving when a fixed pattern or
another systematic is left. `fit_fixed_pattern_noise` fits
``measured_variance = alpha * expected_variance + b`` over the history of a
target's stacks. Fixed-pattern noise is reported only when b is
above 0 by two standard errors. The error of each measured noise is that of
the empty-aperture estimate, 1.17 / sqrt(n) of the noise for n apertures.
These errors are unpaired unless a row says it is paired (only the
leave-one-night-out rows are paired today), and the fit says which it used.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

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
"""Register entry behind the forward model and the fixed-pattern noise fit."""
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

PRACTICAL_TIME_FACTOR = 4.0
"""A "take N more" count is given only when reaching the target needs at most
this many times the stack's present exposure time (so at most 3 times as much
again). The time is the sum of the exposure of every frame in the stack, not
the weighted (effective) time, and the new frames are priced at their own
exposure. Design estimate, register row WAY-D30: with fixed-pattern noise of
about half the noise variance, doubling the time cuts the noise by 14 percent,
4 times by 21 percent, 10 times by 26 percent and 40 times by 29 percent (the
most any number of frames gives is 30 percent), so the gain flattens past
about 4 times (M 13, 44 frames of 30 s, a copy of the library)."""

MINIMUM_DOUBLING_GAIN_FRACTION = 0.05
"""Below this cut in the noise, doubling the stack's time is not worth asking
for, and no frames are suggested. It is half of `MINIMUM_GAIN_FRACTION` (the
cut below which a target counts as stopped, 10 percent): a doubling between the
two is still offered, with the diminishing-returns wording, and one below 5
percent (fixed-pattern noise of about 80 percent of the noise variance or more)
is
not. Design estimate, register row WAY-D30."""

MAXIMUM_NEW_FRAMES = 5000
"""The search for N stops here. 5000 frames of 30 s is 42 hours, far more than
one
target needs."""

APERTURE_NOISE_ERROR_FACTOR = 1.17
"""The error of the empty-aperture noise is 1.17 / sqrt(n) of the noise for n
apertures. The noise is 1.4826 times the median absolute deviation, and the
large-sample variance of that estimate is 1.364 sigma^2 / n, so its standard
error is sqrt(1.364) = 1.17 sigma / sqrt(n). Derived; register entry WAY-21."""

FIXED_PATTERN_SIGMAS = 2.0
"""Fixed-pattern noise is reported only when its fitted size is above 0 by this
many standard errors. The test is one-sided (fixed-pattern noise cannot be
negative), so two standard errors is a false alarm of about 2.3 percent, not 5.
Design estimate,
register row WAY-D32."""

POSSIBLE_FIXED_PATTERN_SIGMAS = 1.0
"""Between this many standard errors and `FIXED_PATTERN_SIGMAS` the
fixed-pattern noise is "possible", not found. It is not proven, so it is not
assumed; the forecast gives the gain both with it and without it, and the
advice is a range. Below this, the stack agrees with the random noise, as
before. One standard error is the usual "worth a second look" level: chance
alone gives it about 16 percent of the time (one-sided), so most stacks with no
pattern fall below it. Design estimate (user decision D18, 2026-10-26);
evidence M 13, whose significance moved from 2.2 to 1.9 between two runs on
the same frames and moved the advice from about 13 to about 29 percent
(validation register entry WAY-76). Register row to be added."""

MINIMUM_MODEL_ERROR_TARGETS = 5
"""The noise model's relative error is measured from odd–even differences only
when at least this many targets of one capture setting group have one;
otherwise the stand-in `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` is used and the
statement says so. Five samples give the scatter of the log ratios a relative
error of about 1 / sqrt(2 x 4) = 35 percent, enough to tell 0.05 from 0.15 but
not to claim more. User decision D17, 2026-10-26; design estimate, register row
to be added."""

MODEL_AGREEMENT_SIGMAS = 2.0
"""The measured stack noise is said to disagree with the model only when its
variance ratio exceeds 1 by this many standard errors. The test is one-sided
(only a noisier stack counts), so two standard errors is a false alarm of
about 2.3 percent, not 5. Design estimate, register row WAY-D34."""

EXPECTED_NOISE_MODEL_RELATIVE_ERROR = 0.11
"""The relative error of the expected noise itself, as noise. On M 57 the real
subsets measured 9 to 31 percent below the prediction (validation register
entry STK-36); half of that spread, 11 percent, is taken as the model's own
one-standard-error. Doubled, it is the relative error of the variance. It is
added in quadrature to the aperture error before "cause unknown" is reported,
so the model's own error is not counted as an unexplained loss. Design
estimate from STK-36, register row WAY-D34."""


FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE = "odd_even_difference"
FIXED_PATTERN_BASIS_MODEL = "model"
FIXED_PATTERN_BASIS_FIT = "fit"
FIXED_PATTERN_BASIS_SETUP_HISTORY = "setup_history"
"""The ways fixed-pattern noise is found (`StackFixedPatternNoise.basis`). The
first three measure the target's own stack. ``setup_history`` measures nothing
on the target: the share is the median of other targets of the same camera,
optic and flat set (decision D23)."""

MINIMUM_HISTORY_TARGETS = 3
"""A target with no fixed-pattern measurement of its own borrows the median
fixed-pattern share of other targets of its setup (camera, optic and flat set)
only when at least this many other targets have one. The median of 3 is the
smallest median that a single odd target cannot move; with 2 it is their mean.
User decision D23, 2026-10-26; design estimate, register row to be added."""

FIXED_PATTERN_BASIS_TEXT = {
    FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE: "measured from the odd and even half stacks",
    FIXED_PATTERN_BASIS_MODEL: "found from the noise model",
    FIXED_PATTERN_BASIS_FIT: "found from a fit over the earlier stacks",
    FIXED_PATTERN_BASIS_SETUP_HISTORY: (
        "assumed to be like other targets of this setup, not measured on this one"
    ),
}
"""Words for each basis, used in the statements that name the fixed-pattern
noise."""

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
    rate_variances: Sequence[float] | None = None,
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
    rate_variances : `Sequence` [`float`], optional
        One variance per frame, in rate units, in place of each frame's own
        `StackFrame.rate_variance`. The group combine needs it for a folded
        group, where Siril rescales a frame to the group's reference frame
        (`group_frame_rate_variances`).

    Returns
    -------
    forecast : `NoiseForecast`
        The variance ``sum(w^2 v) / N^2`` in rate units, with ``N`` the
        normalization (the sum of the weights by default), or ``None`` with a
        reason when the lists differ in length or all weights are 0.
    """
    if len(weights) != len(frames) or (rate_variances is not None and len(rate_variances) != len(frames)):
        return NoiseForecast(
            mode=mode, frame_count=len(frames), reason="The weights and the frames do not match up."
        )
    variances = [frame.rate_variance for frame in frames] if rate_variances is None else list(rate_variances)
    total_weight = sum(weights) if weight_normalization is None else weight_normalization
    if total_weight <= 0.0:
        return NoiseForecast(mode=mode, frame_count=len(frames), reason="All frames have weight 0.")
    variance = sum(w**2 * v for w, v in zip(weights, variances, strict=True)) / total_weight**2
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
    fixed_pattern_noise : `float` or `None`
        The stack's fixed-pattern noise, in the stack's units, when one was
        found (see `StackFixedPatternNoise`). The noise cannot go below it
        however many frames are added.
    fixed_pattern_variance_share : `float` or `None`
        The share of today's noise variance that is fixed_pattern, from 0 to 1.
    fixed_pattern_basis : `str` or `None`
        Where the fixed-pattern noise's random part came from
        (`StackFixedPatternNoise.basis`), when fixed-pattern noise was found.
    fixed_pattern_history_targets : `int` or `None`
        With the ``setup_history`` basis, how many other targets of the setup
        the assumed share is the median of.
    fixed_pattern_limited : `bool`
        True when more frames of this kind will not reach the target because
        of the fixed-pattern noise. `frames_needed` is then ``None``.
    lowest_reachable_ratio : `float` or `None`
        With fixed-pattern noise: the ratio that very many more frames would
        reach.
    new_exposure_group : `bool`
        True when the new frames make a group of an exposure length the stack
        does not have yet (``group`` mode only).
    heaviest_group_changed : `bool`
        True when the new frames would change which group is heaviest, so the
        pair of stacks is confounded (``group`` mode only).
    practical_limit_applied : `bool`
        True when fixed-pattern noise is present and the target needs more than
        `PRACTICAL_TIME_FACTOR` times the stack's exposure time (or cannot be
        reached). `frames_needed` is then ``None`` and the answer is the
        diminishing-returns one: `doubling_frames`, `doubling_noise_cut` and
        `most_reachable_cut`.
    doubling_frames : `int` or `None`
        The frames that double the stack's exposure time at the new frames'
        exposure (as many frames again as the stack holds, when the exposure
        is unchanged). Set with `practical_limit_applied`.
    doubling_noise_cut : `float` or `None`
        The fraction by which the noise falls after `doubling_frames`, from
        the same forecast as the count (0.14 is 14 percent).
    most_reachable_cut : `float` or `None`
        The fraction by which the noise falls after the most frames the search
        tries (`MAXIMUM_NEW_FRAMES`): the most the fixed-pattern noise lets
        frames give.
    doubling_worthwhile : `bool`
        False when `doubling_noise_cut` is below
        `MINIMUM_DOUBLING_GAIN_FRACTION`: no frames are then suggested.
    possible_fixed_pattern_sigmas : `float` or `None`
        Set when the stack has possible, unproven fixed-pattern noise
        (`StackFixedPatternNoise.possible`): the standard errors it was found
        at. The count and the main cut (`predicted_noise_ratio`) are then for
        no fixed-pattern noise, and the fields below give the low end.
    possible_low_noise_cut : `float` or `None`
        The fraction by which the noise falls after `frames_needed` frames if
        the possible fixed-pattern noise is real. The high end is
        ``1 - predicted_noise_ratio``.
    possible_low_doubling_cut : `float` or `None`
        With the possible fixed-pattern noise real: the cut that doubling the
        stack's time gives.
    possible_low_end_not_worthwhile : `bool`
        True when, with the possible fixed-pattern noise real, the answer would
        be the "more frames will not help" warning (the practical-limit logic
        of `_take_more_result` finds a doubling below
        `MINIMUM_DOUBLING_GAIN_FRACTION`). The count is still given.
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
    fixed_pattern_noise: float | None = None
    fixed_pattern_variance_share: float | None = None
    fixed_pattern_basis: str | None = None
    fixed_pattern_history_targets: int | None = None
    fixed_pattern_limited: bool = False
    lowest_reachable_ratio: float | None = None
    new_exposure_group: bool = False
    heaviest_group_changed: bool = False
    practical_limit_applied: bool = False
    doubling_frames: int | None = None
    doubling_noise_cut: float | None = None
    most_reachable_cut: float | None = None
    doubling_worthwhile: bool = True
    possible_fixed_pattern_sigmas: float | None = None
    possible_low_noise_cut: float | None = None
    possible_low_doubling_cut: float | None = None
    possible_low_end_not_worthwhile: bool = False


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
    fixed_pattern: StackFixedPatternNoise | None = None,
) -> TakeMoreResult:
    """Find how many more frames cut the noise ("take N more").

    New frames follow the past frames of the same capture setting: frame k
    copies template frame ``k mod len(template)``, so the sky rates and star
    widths of the new frames match the target's history. When the new
    exposure differs from the old, the new frames keep their sky rates (per
    second) but change their read-noise share, so a read-noise-limited short
    frame counts for less than a long one.

    The search doubles N until the target is reached, then bisects.

    With fixed-pattern noise (`fixed_pattern`), the noise after adding frames
    is
    ``sqrt(fixed_pattern + random_later)``, in variances (see
    `noise_ratio_with_fixed_pattern_noise`). When the fixed-pattern noise makes
    up so much of today's noise that no number of frames reaches the target, no
    N is given: the result says so and states the fixed-pattern noise.

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
    fixed_pattern : `StackFixedPatternNoise`, optional
        The stack's fixed-pattern noise, when one was found.

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

    def ratio_for(count: int, pattern: StackFixedPatternNoise | None) -> float | None:
        """Give the predicted noise ratio after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.
        pattern : `StackFixedPatternNoise` or `None`
            The fixed-pattern noise to apply.

        Returns
        -------
        ratio : `float` or `None`
            New noise over current noise.
        """
        forecast = noise_ratio_after_adding(existing, template, count, mode, new_exposure_seconds)
        return noise_ratio_with_fixed_pattern_noise(current, forecast, pattern)

    exposure = new_exposure_seconds or float(np.mean([frame.exposure_seconds for frame in template]))
    return _take_more_result_graded(
        ratio_for,
        minimum_count=1,
        maximum_count=maximum_new_frames,
        target_noise_ratio=target_noise_ratio,
        exposure_seconds=exposure,
        fixed_pattern=fixed_pattern,
        held_exposure_seconds=sum(frame.exposure_seconds for frame in existing),
    )


def _take_more_result_graded(
    ratio_for_pattern: Callable[[int, StackFixedPatternNoise | None], float | None],
    *,
    fixed_pattern: StackFixedPatternNoise | None,
    **search: Any,
) -> TakeMoreResult:
    """Search for N, giving a range for a possible fixed-pattern noise.

    Above `FIXED_PATTERN_SIGMAS` or below `POSSIBLE_FIXED_PATTERN_SIGMAS` this
    is `_take_more_result` with the fixed-pattern noise applied or not, as
    always.
    Between them (``fixed_pattern.possible``) the forecast is made twice, with
    no fixed-pattern noise and with the estimated one (decision D18):

    * The count and the practical-limit decision come from the case without
      it, so a possible pattern never withholds or inflates the count by
      itself. The main cut (``predicted_noise_ratio``) is that case's, the high
      end of the range.
    * The low end is the cut the same count gives if the pattern is real.
    * If the case with the pattern would be the "more frames will not help"
      answer (a doubling below `MINIMUM_DOUBLING_GAIN_FRACTION`),
      ``possible_low_end_not_worthwhile`` is set and the statement adds one
      sentence; the count is still given.

    Parameters
    ----------
    ratio_for_pattern : `Callable`
        Takes the frames to add and a `StackFixedPatternNoise` or ``None``;
        gives a `float` or ``None``.
        The predicted noise ratio after adding this many frames, with the given
        fixed-pattern noise applied.
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The stack's fixed-pattern noise, found, possible or neither.
    **search
        The remaining arguments of `_take_more_result`.

    Returns
    -------
    result : `TakeMoreResult`
        The answer, with the ``possible_*`` fields set for a possible pattern.
    """
    if fixed_pattern is None or not fixed_pattern.possible:
        return _take_more_result(
            lambda count: ratio_for_pattern(count, fixed_pattern), fixed_pattern=fixed_pattern, **search
        )
    real = fixed_pattern.if_real()
    without = _take_more_result(lambda count: ratio_for_pattern(count, None), fixed_pattern=None, **search)
    if without.frames_needed is None:
        return without
    with_pattern = _take_more_result(
        lambda count: ratio_for_pattern(count, real), fixed_pattern=real, **search
    )
    low_ratio = ratio_for_pattern(without.frames_needed, real)
    if low_ratio is None:
        return without
    return without.model_copy(
        update={
            "possible_fixed_pattern_sigmas": fixed_pattern.sigmas,
            "possible_low_noise_cut": 1.0 - low_ratio,
            "possible_low_doubling_cut": with_pattern.doubling_noise_cut,
            "possible_low_end_not_worthwhile": with_pattern.practical_limit_applied
            and not with_pattern.doubling_worthwhile,
            "fixed_pattern_basis": fixed_pattern.basis,
        }
    )


def _noise_in_stack_units_text(noise: float | None) -> str:
    """Write a fixed-pattern noise for a reason, when it may be unknown.

    Parameters
    ----------
    noise : `float` or `None`
        The noise in stack units, or ``None`` when only its share is known (a
        share assumed from other targets of the setup has no size of its own).

    Returns
    -------
    text : `str`
        For example ``"0.000583 (stack units)"``, or ``"an unmeasured size"``.
    """
    return f"{noise:.3g} (stack units)" if noise is not None else "an unmeasured size"


def _take_more_result(
    ratio_for: Callable[[int], float | None],
    *,
    minimum_count: int,
    maximum_count: int,
    target_noise_ratio: float,
    exposure_seconds: float,
    fixed_pattern: StackFixedPatternNoise | None,
    held_exposure_seconds: float | None = None,
) -> TakeMoreResult:
    """Search for N and write the answer, naming fixed-pattern noise.

    With fixed-pattern noise, the count is given only when it is within the
    practical limit (`PRACTICAL_TIME_FACTOR` times the stack's exposure time).
    Beyond it, or when the target cannot be reached, the answer is the
    diminishing-returns one: the frames that double the stack's time, what that
    cuts, and the most any frames cut.

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
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The stack's fixed-pattern noise, when one was found.
    held_exposure_seconds : `float`, optional
        The sum of the exposure of every frame the stack holds today. Without
        it the practical limit is not applied.

    Returns
    -------
    result : `TakeMoreResult`
        The answer.
    """
    fixed_pattern_fields: dict[str, float | int | str | None] = {
        "fixed_pattern_basis": fixed_pattern.basis
        if fixed_pattern is not None and fixed_pattern.fixed_pattern_noise_detected
        else None,
        "fixed_pattern_history_targets": fixed_pattern.history_target_count
        if fixed_pattern is not None and fixed_pattern.fixed_pattern_noise_detected
        else None,
        "fixed_pattern_noise": fixed_pattern.fixed_pattern_noise
        if fixed_pattern is not None and fixed_pattern.fixed_pattern_noise_detected
        else None,
        "fixed_pattern_variance_share": (
            fixed_pattern.fixed_pattern_variance_share
            if fixed_pattern is not None and fixed_pattern.fixed_pattern_noise_detected
            else None
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
    fixed_pattern_present = fixed_pattern is not None and fixed_pattern.fixed_pattern_noise_detected
    beyond_practical_limit = needed is None or (
        held_exposure_seconds is not None
        and needed * exposure_seconds > (PRACTICAL_TIME_FACTOR - 1.0) * held_exposure_seconds
    )
    if fixed_pattern_present and beyond_practical_limit and held_exposure_seconds:
        doubling_frames = max(minimum_count, round(held_exposure_seconds / exposure_seconds))
        doubling_ratio = ratio_for(doubling_frames)
        most_ratio = ratio_for(maximum_count)
        if doubling_ratio is not None and most_ratio is not None:
            doubling_cut = 1.0 - doubling_ratio
            return TakeMoreResult(
                target_noise_ratio=target_noise_ratio,
                added_exposure_seconds=doubling_frames * exposure_seconds,
                new_exposure_seconds=exposure_seconds,
                fixed_pattern_limited=needed is None,
                lowest_reachable_ratio=most_ratio,
                practical_limit_applied=True,
                doubling_frames=doubling_frames,
                doubling_noise_cut=doubling_cut,
                most_reachable_cut=1.0 - most_ratio,
                doubling_worthwhile=doubling_cut >= MINIMUM_DOUBLING_GAIN_FRACTION,
                reason=(
                    "A design estimate: real subsets of M 57 came out 9 to 31 percent below the prediction "
                    f"(STK-36). Reaching the target of {target_noise_ratio:.2f} of today's noise "
                    + (
                        "is out of reach"
                        if needed is None
                        else f"needs {needed} frames, more than {PRACTICAL_TIME_FACTOR - 1.0:g} times the "
                        "stack's exposure time again"
                    )
                    + f" because of the fixed-pattern noise, {fixed_pattern.basis_text} (practical limit "
                    f"{PRACTICAL_TIME_FACTOR:g} times the stack's exposure time, WAY-D30)."
                ),
                **fixed_pattern_fields,
            )
    if needed is None:
        limit_ratio = ratio_for(maximum_count)
        if (
            fixed_pattern is not None
            and fixed_pattern.fixed_pattern_noise_detected
            and limit_ratio is not None
        ):
            return TakeMoreResult(
                target_noise_ratio=target_noise_ratio,
                fixed_pattern_limited=True,
                lowest_reachable_ratio=limit_ratio,
                reason=(
                    f"More frames of this kind will not reach the target: the stack has fixed-pattern noise "
                    f"(noise that stays at the same place on the sky in every frame, so more frames do not "
                    f"reduce it), {fixed_pattern.basis_text}, of "
                    f"{_noise_in_stack_units_text(fixed_pattern.fixed_pattern_noise)}, "
                    f"{100 * (fixed_pattern.fixed_pattern_variance_share or 0.0):.0f} "
                    f"percent of today's noise variance, and {maximum_count} more frames would only reach "
                    f"{limit_ratio:.2f} of today's noise. The fixed-pattern noise has to be found and "
                    f"removed first."
                ),
                **fixed_pattern_fields,
            )
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason=f"The target was not reached with {maximum_count} more frames.",
            **fixed_pattern_fields,
        )
    reason = "A design estimate: real subsets of M 57 came out 9 to 31 percent below the prediction (STK-36)."
    if fixed_pattern_fields["fixed_pattern_basis"] is not None and fixed_pattern is not None:
        reason += (
            " The stack has fixed-pattern noise of "
            f"{_noise_in_stack_units_text(fixed_pattern.fixed_pattern_noise)}, "
            f"{fixed_pattern.basis_text}, that more frames do not remove; the answer includes it."
        )
    return TakeMoreResult(
        frames_needed=needed,
        predicted_noise_ratio=ratio_for(needed),
        target_noise_ratio=target_noise_ratio,
        added_exposure_seconds=needed * exposure_seconds,
        new_exposure_seconds=exposure_seconds,
        reason=reason,
        **fixed_pattern_fields,
    )


# -----------------------------------------------------------------------------
# The fixed-pattern noise
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


def total_variance_ratio_error(
    variance_ratio: float,
    aperture_error: float,
    model_relative_error: float = EXPECTED_NOISE_MODEL_RELATIVE_ERROR,
) -> float:
    """Give the error of a measured-over-predicted variance ratio.

    The expected-noise model's own error is included.

    This is the one error budget. The loss account's "cause unknown" test and
    the one-stack fixed-pattern noise test both compare ``variance_ratio - 1``
    with `MODEL_AGREEMENT_SIGMAS` times this error, so a stack the loss account
    calls "within error" never has a significant fixed_pattern, and the other
    way round.

    Parameters
    ----------
    variance_ratio : `float`
        (measured noise / expected noise) squared.
    aperture_error : `float`
        One standard error of the ratio from the apertures alone, for n
        unpaired apertures ``2 * 1.17 / sqrt(n) * variance_ratio``.
    model_relative_error : `float`, optional
        The expected-noise model's own relative error, as noise. Doubled for
        the variance.

    Returns
    -------
    error : `float`
        The aperture error and ``2 * model_relative_error * variance_ratio``
        added in quadrature.
    """
    return math.hypot(aperture_error, 2.0 * model_relative_error * variance_ratio)


NOISE_MODEL_ERROR_STAND_IN_TEXT = (
    "the stand-in value, as too few targets of this capture setting group are measured"
)
"""How a statement names the stand-in model error (fewer than
`MINIMUM_MODEL_ERROR_TARGETS` targets in the group have the measurement)."""


class NoiseModelErrorSample(BaseModel):
    """One target's check of the noise model against its odd–even difference.

    The odd–even difference measures the stack's random noise directly. The
    noise model predicts the same noise from the frames' sky and read noise.
    Their log ratio is one sample of how far off the model is.

    Parameters
    ----------
    target_id : `str`
        The target the sample comes from. A target gives one sample to its
        capture setting group.
    log_variance_ratio : `float`
        ``ln((random noise / predicted noise)^2)``, with both noises of the
        same stack in the same unit. Zero means the model was right; a negative
        value
        means the stack was quieter than predicted.
    apertures : `int`
        The empty apertures behind the measured random noise (at least 2).
    """

    model_config = _MODEL_CONFIG

    target_id: str
    log_variance_ratio: float
    apertures: int = Field(ge=2)

    @classmethod
    def from_noises(
        cls, target_id: str, random_noise: float, predicted_noise: float, apertures: int
    ) -> NoiseModelErrorSample:
        """Build a sample from the two noises of one stack.

        Parameters
        ----------
        target_id : `str`
            The target.
        random_noise : `float`
            The full-stack equivalent of the odd–even difference noise.
        predicted_noise : `float`
            The noise model's prediction for the same stack, in the same unit.
        apertures : `int`
            The apertures behind ``random_noise``.

        Returns
        -------
        sample : `NoiseModelErrorSample`
            The sample.

        Raises
        ------
        ValueError
            If a noise is not positive.
        """
        if random_noise <= 0.0 or predicted_noise <= 0.0:
            raise ValueError("Both noise values must be positive.")
        return cls(
            target_id=target_id,
            log_variance_ratio=2.0 * math.log(random_noise / predicted_noise),
            apertures=apertures,
        )

    @property
    def measurement_error(self) -> float:
        """Give the expected scatter of this sample from measuring alone.

        Returns
        -------
        error : `float`
            The standard error of ``ln(variance)`` of the measured random
            noise, ``2 x 1.17 / sqrt(n)`` (the error the odd–even test uses for
            each of its sides). The prediction has no aperture error, so this
            is the only measurement term.
        """
        return 2.0 * aperture_noise_relative_error(self.apertures)


class NoiseModelError(BaseModel):
    """The noise model's own relative error, and where it came from.

    Parameters
    ----------
    relative_error : `float`
        The model's one-standard-error relative error of the predicted noise
        (not of the variance), as `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` is.
    target_count : `int`
        The targets whose odd–even differences were available for this setting
        group (0 when none were looked for).
    measured : `bool`
        True when ``relative_error`` was measured from at least
        `MINIMUM_MODEL_ERROR_TARGETS` targets; False when it is the stand-in.
    mean_log_ratio : `float` or `None`
        The mean of ``ln(random noise / predicted noise)`` over the targets: a
        bias report only. Negative means the model predicts more noise than the
        stacks have. It is never applied to a prediction. ``None`` with fewer
        targets than the minimum.
    """

    model_config = _MODEL_CONFIG

    relative_error: float = Field(default=EXPECTED_NOISE_MODEL_RELATIVE_ERROR, ge=0.0)
    target_count: int = Field(default=0, ge=0)
    measured: bool = False
    mean_log_ratio: float | None = None

    @property
    def basis_text(self) -> str:
        """Say where the relative error came from, for a statement.

        Returns
        -------
        text : `str`
            ``"measured from N targets' odd–even differences"`` or the stand-in
            wording (`NOISE_MODEL_ERROR_STAND_IN_TEXT`).
        """
        if self.measured:
            return f"measured from {self.target_count} targets' odd–even differences"
        return NOISE_MODEL_ERROR_STAND_IN_TEXT


def measure_noise_model_error(
    samples: Sequence[NoiseModelErrorSample], *, minimum_targets: int = MINIMUM_MODEL_ERROR_TARGETS
) -> NoiseModelError:
    """Measure the noise model's relative error from odd–even differences.

    The samples of one capture setting group, one per target, scatter around
    their mean for two reasons: the model's own error, and the error of each
    measurement. The expected scatter from measuring is known for each sample
    (`NoiseModelErrorSample.measurement_error`), so it is taken out in
    quadrature (as variances):

        var(model) = var(ln F values) - mean(measurement_error^2)

    floored at zero. ``F`` is a variance ratio, so the log of the noise ratio
    is half of ``ln F`` and the model's relative error as noise is
    ``sqrt(var(model)) / 2``. The scatter is taken about the mean, so a steady
    bias (the model always a little high) is reported separately and does not
    count as error; the bias is not applied. Planck (ESA) checked its noise
    model against half-ring differences the same way; plan section 3.13, D17.

    Parameters
    ----------
    samples : `Sequence` [`NoiseModelErrorSample`]
        One sample per target of one capture setting group.
    minimum_targets : `int`, optional
        The fewest samples that may replace the stand-in
        (`MINIMUM_MODEL_ERROR_TARGETS`).

    Returns
    -------
    model_error : `NoiseModelError`
        The measured error, or the stand-in
        (`EXPECTED_NOISE_MODEL_RELATIVE_ERROR`) with ``measured`` false when
        there are fewer samples than the minimum.
    """
    count = len(samples)
    if count < max(minimum_targets, 2):
        return NoiseModelError(target_count=count)
    values = np.asarray([sample.log_variance_ratio for sample in samples], dtype=float)
    observed_variance = float(np.var(values, ddof=1))
    measurement_variance = float(np.mean([sample.measurement_error**2 for sample in samples]))
    model_variance = max(0.0, observed_variance - measurement_variance)
    return NoiseModelError(
        relative_error=0.5 * math.sqrt(model_variance),
        target_count=count,
        measured=True,
        mean_log_ratio=0.5 * float(np.mean(values)),
    )


class NoiseStep(BaseModel):
    """One stack of a target's history, for the fixed-pattern noise fit.

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


class FixedPatternNoiseFit(BaseModel):
    """The result of fitting the fixed-pattern noise.

    Parameters
    ----------
    steps_used : `int`
        Stacks in the fit.
    alpha : `float` or `None`
        How many times the expected variance the measured variance grows
        (1 means the model explains the noise).
    read_noise_scale : `float` or `None`
        The free read-noise factor, when that term was fitted.
    fixed_pattern_variance : `float` or `None`
        The fitted constant b, in measured-noise-squared units.
    fixed_pattern_variance_error : `float` or `None`
        One standard error of b.
    fixed_pattern_noise_detected : `bool`
        True when b is above 0 by `FIXED_PATTERN_SIGMAS` standard errors.
    fixed_pattern_noise : `float` or `None`
        The noise level the stack cannot go below, ``sqrt(b)``, when
        detected.
    error_kind : `str`
        ``"unpaired"``, ``"paired"`` or ``"mixed"``: how the step errors were
        found. The unpaired error is larger, so the fit is cautious.
    normalized : `bool`
        Whether the fit was made on noise divided by the reference flux. Then
        `fixed_pattern_variance` and `fixed_pattern_noise` are fractions of the
        reference stars' brightness, not stack units.
    degrees_of_freedom : `int`
        Steps minus fitted terms. With 0 the line passes through every point
        exactly, so the fixed-pattern noise's error comes only from the assumed
        aperture errors and has no check from scatter.
    reason : `str`
        What happened, in one sentence.
    """

    model_config = _MODEL_CONFIG

    steps_used: int
    alpha: float | None = None
    read_noise_scale: float | None = None
    fixed_pattern_variance: float | None = None
    fixed_pattern_variance_error: float | None = None
    fixed_pattern_noise_detected: bool = False
    fixed_pattern_noise: float | None = None
    error_kind: str = "unpaired"
    normalized: bool = False
    degrees_of_freedom: int = 0
    reason: str


def fit_fixed_pattern_noise(
    steps: Sequence[NoiseStep], *, read_noise_term_free: bool = False
) -> FixedPatternNoiseFit:
    """Fit the fixed-pattern noise over the history of a target.

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
    3 stacks; without it, at least 2. Fixed-pattern noise is never inferred
    from one shortfall.

    Parameters
    ----------
    steps : `Sequence` [`NoiseStep`]
        The target's stacks, in any order.
    read_noise_term_free : `bool`, optional
        Fit the read-noise part separately. Each step then needs
        `expected_read_noise_variance`.

    Returns
    -------
    fit : `FixedPatternNoiseFit`
        The fit, or a result with ``fixed_pattern_noise_detected`` false and
        the reason ("not enough stack steps") when there are too few.
    """
    parameter_count = 3 if read_noise_term_free else 2
    if any(step.normalized for step in steps):
        steps = [step for step in steps if step.normalized]
    normalized = bool(steps) and all(step.normalized for step in steps)
    kinds = {"paired" if step.paired else "unpaired" for step in steps}
    error_kind = kinds.pop() if len(kinds) == 1 else "mixed"
    if len(steps) < parameter_count:
        return FixedPatternNoiseFit(
            steps_used=len(steps),
            error_kind=error_kind,
            reason=f"Not enough stack steps: the fit needs {parameter_count}, and there are {len(steps)}.",
        )
    if read_noise_term_free and any(step.expected_read_noise_variance is None for step in steps):
        return FixedPatternNoiseFit(
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
        return FixedPatternNoiseFit(
            steps_used=len(steps),
            error_kind=error_kind,
            reason=(
                "The stacks do not differ enough to separate the terms "
                "(the history is too short or too regular)."
            ),
        )
    covariance = np.linalg.inv(weighted_design.T @ weighted_design)
    fixed_pattern = float(solution[-1])
    fixed_pattern_error = float(math.sqrt(covariance[-1, -1]))
    detected = fixed_pattern > FIXED_PATTERN_SIGMAS * fixed_pattern_error
    return FixedPatternNoiseFit(
        steps_used=len(steps),
        alpha=float(solution[0]),
        read_noise_scale=float(solution[1]) if read_noise_term_free else None,
        fixed_pattern_variance=fixed_pattern,
        fixed_pattern_variance_error=fixed_pattern_error,
        fixed_pattern_noise_detected=detected,
        fixed_pattern_noise=math.sqrt(fixed_pattern) if detected else None,
        error_kind=error_kind,
        normalized=normalized,
        degrees_of_freedom=len(steps) - parameter_count,
        reason=(
            (
                "Fixed-pattern noise was found."
                if detected
                else "No fixed-pattern noise: the fitted constant is not above 0 by two standard errors."
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
        True when doubling the data is predicted to cut the noise by less than
        `MINIMUM_GAIN_FRACTION`. ``None`` when there is no fixed-pattern noise
        fit to say.
    predicted_gain_fraction : `float` or `None`
        The predicted cut in measured noise from doubling the effective time.
    reason : `str`
        One sentence.
    """

    model_config = _MODEL_CONFIG

    stopped: bool | None = None
    predicted_gain_fraction: float | None = None
    reason: str


def stop_signal(fit: FixedPatternNoiseFit, current_expected_variance: float) -> StopSignal:
    """Say whether the noise has stopped falling with more frames.

    Doubling the effective time halves the expected variance. With the fit
    ``V = alpha x + b`` the measured noise goes from ``sqrt(alpha x + b)`` to
    ``sqrt(alpha x / 2 + b)``.

    Parameters
    ----------
    fit : `FixedPatternNoiseFit`
        A fit from `fit_fixed_pattern_noise`.
    current_expected_variance : `float`
        The expected variance of today's stack, in the fit's units.

    Returns
    -------
    signal : `StopSignal`
        The stop decision. It is ``None`` (no signal) when the fit has no
        alpha, so a missing history never looks like a stop.
    """
    if fit.alpha is None or fit.fixed_pattern_variance is None:
        return StopSignal(reason=f"No signal: {fit.reason}")
    if not fit.fixed_pattern_noise_detected:
        return StopSignal(
            stopped=False, reason="No fixed-pattern noise was found, so more frames are expected to help."
        )
    now = fit.alpha * current_expected_variance + fit.fixed_pattern_variance
    after = fit.alpha * current_expected_variance / 2.0 + fit.fixed_pattern_variance
    if now <= 0.0 or after <= 0.0:
        return StopSignal(reason="The fit predicts a non-positive variance, so it cannot be used.")
    gain = 1.0 - math.sqrt(after / now)
    stopped = gain < MINIMUM_GAIN_FRACTION
    return StopSignal(
        stopped=stopped,
        predicted_gain_fraction=gain,
        reason=(
            f"Doubling the data would cut the noise by {100 * gain:.0f} percent"
            + (
                ", below the limit; more frames will not help until the fixed-pattern noise is fixed."
                if stopped
                else "."
            )
        ),
    )


# -----------------------------------------------------------------------------
# The fixed-pattern noise of one stack
# -----------------------------------------------------------------------------


class StackFixedPatternNoise(BaseModel):
    """The part of a stack's measured noise that more frames do not remove.

    The fixed-pattern noise is found from one stack: its measured
    empty-aperture noise against the random noise the frames alone predict.
    Both are in the stack's units (``stacking.empty_aperture_noise_stack_unit``
    and
    ``stacking.expected_empty_aperture_noise_stack_unit``, the second already
    multiplied by Siril's ``stacking.normalization_scale``).

    Parameters
    ----------
    source : `str`
        ``"single_stack"`` (measured against predicted, one stack) or
        ``"fit"`` (`fit_fixed_pattern_noise` over two or more stacks).
    measured_noise : `float` or `None`
        The stack's measured noise, in stack units.
    predicted_random_noise : `float` or `None`
        The noise the frames alone predict, in stack units.
    apertures : `int` or `None`
        The empty apertures behind the measured noise.
    fixed_pattern_variance : `float` or `None`
        ``measured^2 - random^2`` when it is above 0 by `FIXED_PATTERN_SIGMAS`
        standard errors; else ``None``.
    fixed_pattern_variance_error : `float` or `None`
        One standard error of the difference, from the error of the measured
        noise only (the predicted value's error is not stored, so it is taken
        as negligible).
    fixed_pattern_noise_detected : `bool`
        Whether fixed-pattern noise was kept.
    fixed_pattern_noise : `float` or `None`
        ``sqrt(fixed_pattern_variance)``. In stack units for a ``single_stack``
        fixed_pattern.
    fixed_pattern_to_random_variance_ratio : `float` or `None`
        The fixed-pattern noise variance over the random variance of the same
        stack. This is how the fixed-pattern noise enters a forecast
        (`noise_ratio_with_fixed_pattern_noise`): a dimensionless fraction of
        the model's own variance, so it needs no unit conversion between the
        forecast's rate units and the stack's.
    fixed_pattern_variance_share : `float` or `None`
        The fixed-pattern noise's share of the measured variance, from 0 to 1.
    normalized_units : `bool`
        True when `fixed_pattern_noise` is a fraction of the reference stars'
        flux (a fit result with no single stack to convert it).
    basis : `str`
        What the fixed-pattern noise's random part came from:
        ``"odd_even_difference"`` (the direct measurement from the odd and even
        half stacks, errors of measurements only), ``"model"`` (the noise
        model's prediction, its own error included) or ``"fit"``
        (`fit_fixed_pattern_noise`).
    sigmas : `float` or `None`
        How many standard errors the test found: ``ln F`` over its error for
        ``odd_even_difference``, ``(ratio - 1)`` over its error for ``model``.
    possible : `bool`
        True when the test found fixed-pattern noise between
        `POSSIBLE_FIXED_PATTERN_SIGMAS` and `MODEL_AGREEMENT_SIGMAS`: possible,
        not proven. ``fixed_pattern_noise_detected`` is then false, so nothing
        that reads only "detected" changes, but the size fields
        (`fixed_pattern_variance`, `fixed_pattern_noise`,
        `fixed_pattern_to_random_variance_ratio`,
        `fixed_pattern_variance_share`)
        are filled so a forecast can also be made as if it were real
        (`if_real`).
    model_error_basis : `str` or `None`
        On the ``model`` basis, where the model's relative error came from
        (`NoiseModelError.basis_text`); ``None`` on other bases.
    history_target_count : `int` or `None`
        On the ``setup_history`` basis, how many other targets of the setup
        the median share came from; ``None`` on other bases.
    reason : `str`
        One or two sentences saying what was found and why.
    """

    model_config = _MODEL_CONFIG

    source: str
    measured_noise: float | None = None
    predicted_random_noise: float | None = None
    apertures: int | None = None
    fixed_pattern_variance: float | None = None
    fixed_pattern_variance_error: float | None = None
    fixed_pattern_noise_detected: bool = False
    fixed_pattern_noise: float | None = None
    fixed_pattern_to_random_variance_ratio: float | None = None
    fixed_pattern_variance_share: float | None = None
    normalized_units: bool = False
    basis: str = FIXED_PATTERN_BASIS_MODEL
    sigmas: float | None = None
    possible: bool = False
    model_error_basis: str | None = None
    history_target_count: int | None = None
    reason: str

    def if_real(self) -> StackFixedPatternNoise:
        """Give this result as it would be if its possible pattern were real.

        Returns
        -------
        fixed_pattern : `StackFixedPatternNoise`
            A copy with ``fixed_pattern_noise_detected`` true and ``possible``
            false, for the "with fixed-pattern noise" end of a range. A result
            that is not possible is returned unchanged.
        """
        if not self.possible:
            return self
        return self.model_copy(update={"fixed_pattern_noise_detected": True, "possible": False})

    @property
    def basis_text(self) -> str:
        """Say where the fixed-pattern noise's random part came from.

        Returns
        -------
        text : `str`
            A phrase that completes "The fixed-pattern noise is ...".
        """
        if self.basis == FIXED_PATTERN_BASIS_SETUP_HISTORY and self.history_target_count:
            return (
                "assumed to be like other targets of this setup "
                f"(median of {self.history_target_count} targets), not measured on this one"
            )
        return FIXED_PATTERN_BASIS_TEXT.get(self.basis, FIXED_PATTERN_BASIS_TEXT[FIXED_PATTERN_BASIS_MODEL])


def measure_stack_fixed_pattern_noise(
    measured_noise: float,
    predicted_random_noise: float,
    apertures: int,
    model_error: NoiseModelError | None = None,
) -> StackFixedPatternNoise:
    """Find a stack's fixed-pattern noise from measured and predicted noise.

    ``fixed_pattern^2 = measured^2 - random^2``. It is kept only when the
    variance ratio ``measured^2 / random^2`` is above 1 by
    `MODEL_AGREEMENT_SIGMAS` standard errors, where the error is
    `total_variance_ratio_error`: the aperture error (the measured noise has
    the relative error
    ``1.17 / sqrt(n)``, `aperture_noise_relative_error`, so its variance has
    twice that) and the expected-noise model's own error (validation register
    entry STK-36), added in quadrature. This is the same test the loss account
    uses before it reports "cause unknown", so a stack that account calls
    "within error" has no fixed-pattern noise here. The fixed-pattern noise
    value itself is unchanged by the test.

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
    model_error : `NoiseModelError`, optional
        The noise model's own relative error and where it came from. The
        stand-in `EXPECTED_NOISE_MODEL_RELATIVE_ERROR` when omitted.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise`
        The fixed-pattern noise, or a result with
        ``fixed_pattern_noise_detected`` false and the reason. Between
        `POSSIBLE_FIXED_PATTERN_SIGMAS` and `MODEL_AGREEMENT_SIGMAS` the result
        is ``possible`` (not detected, sizes filled).
        ``fixed_pattern_variance_error`` is the total error in variance units.

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
    variance_ratio = measured_variance / random_variance
    model_error = model_error or NoiseModelError()
    ratio_error = total_variance_ratio_error(
        variance_ratio, 2.0 * relative_error * variance_ratio, model_error.relative_error
    )
    error = ratio_error * random_variance
    common = {
        "source": "single_stack",
        "measured_noise": measured_noise,
        "predicted_random_noise": predicted_random_noise,
        "apertures": apertures,
        "fixed_pattern_variance_error": error,
        "basis": FIXED_PATTERN_BASIS_MODEL,
        "sigmas": (variance_ratio - 1.0) / ratio_error,
        "model_error_basis": model_error.basis_text,
    }
    if variance_ratio - 1.0 <= MODEL_AGREEMENT_SIGMAS * ratio_error:
        if variance_ratio - 1.0 >= POSSIBLE_FIXED_PATTERN_SIGMAS * ratio_error:
            return StackFixedPatternNoise(
                **common,
                possible=True,
                fixed_pattern_variance=difference,
                fixed_pattern_noise=math.sqrt(difference),
                fixed_pattern_to_random_variance_ratio=difference / random_variance,
                fixed_pattern_variance_share=difference / measured_variance,
                reason=(
                    f"Possible fixed-pattern noise of {math.sqrt(difference):.3g} (stack units), not proven: "
                    f"the measured noise exceeds the predicted random noise by {common['sigmas']:.1f} "
                    f"standard errors, below the {MODEL_AGREEMENT_SIGMAS:g} needed, counting the "
                    f"prediction's own error ({model_error.relative_error:.0%}, {model_error.basis_text})."
                ),
            )
        return StackFixedPatternNoise(
            **common,
            reason=(
                "No fixed-pattern noise: the measured noise does not exceed the predicted random noise by "
                f"{MODEL_AGREEMENT_SIGMAS:g} standard errors, counting the prediction's own error "
                f"({difference / error:.1f} found; the error is {model_error.relative_error:.0%}, "
                f"{model_error.basis_text})."
            ),
        )
    return StackFixedPatternNoise(
        **common,
        fixed_pattern_variance=difference,
        fixed_pattern_noise_detected=True,
        fixed_pattern_noise=math.sqrt(difference),
        fixed_pattern_to_random_variance_ratio=difference / random_variance,
        fixed_pattern_variance_share=difference / measured_variance,
        reason=(
            f"Fixed-pattern noise of {math.sqrt(difference):.3g} (stack units) was found, "
            f"{difference / error:.1f} standard errors above 0 (counting the prediction's own error, "
            f"{model_error.relative_error:.0%}, {model_error.basis_text}) and "
            f"{100 * difference / measured_variance:.0f} percent of the measured noise variance."
        ),
    )


@dataclass(frozen=True)
class OddEvenDifferenceTest:
    """The result of comparing a stack's noise with its odd–even difference.

    Parameters
    ----------
    variance_ratio : `float`
        ``F = measured^2 / random^2``.
    log_ratio : `float`
        ``ln F``.
    log_error : `float`
        The standard error of ``ln F``, ``sqrt(e1^2 + e2^2)``.
    measured_variance_error, random_variance_error : `float`
        The relative error of each variance, ``2 x 1.17 / sqrt(n)``.
    sigmas : `float`
        ``ln F`` over its error.
    significant : `bool`
        ``True`` when ``ln F`` exceeds `MODEL_AGREEMENT_SIGMAS` times its
        error (one-sided).
    """

    variance_ratio: float
    log_ratio: float
    log_error: float
    measured_variance_error: float
    random_variance_error: float
    sigmas: float
    significant: bool


def compare_stack_noise_with_odd_even_difference(
    measured_noise: float,
    measured_apertures: int,
    random_noise: float,
    random_apertures: int,
) -> OddEvenDifferenceTest:
    """Compare a stack's noise with the random noise its halves measured.

    This is the one test behind both the fixed-pattern noise and the loss
    account's "cause unknown" part when an odd–even difference exists, so the
    two cannot disagree. With no fixed pattern, the full stack and the
    full-stack equivalent of the difference have the same variance, and the
    aperture-to-aperture scatters of the two are independent estimates of it.
    Each variance has the relative error ``2 x 1.17 / sqrt(n)``
    (`aperture_noise_relative_error` doubled), so ``ln F`` has the standard
    error ``sqrt(e1^2 + e2^2)``. Only measurement errors count: the noise model
    plays no part.

    Parameters
    ----------
    measured_noise : `float`
        The full stack's measured empty-aperture noise.
    measured_apertures : `int`
        The apertures behind it (at least 2).
    random_noise : `float`
        The full-stack equivalent of the difference noise, in the same unit
        as ``measured_noise``.
    random_apertures : `int`
        The apertures behind the difference noise (at least 2).

    Returns
    -------
    result : `OddEvenDifferenceTest`
        The ratio, its errors and whether it is significant.

    Raises
    ------
    ValueError
        If a noise is not positive or an aperture count is below 2.
    """
    if measured_noise <= 0.0 or random_noise <= 0.0:
        raise ValueError("Both noise values must be positive.")
    measured_error = 2.0 * aperture_noise_relative_error(measured_apertures)
    random_error = 2.0 * aperture_noise_relative_error(random_apertures)
    log_error = math.hypot(measured_error, random_error)
    variance_ratio = (measured_noise / random_noise) ** 2
    log_ratio = math.log(variance_ratio)
    return OddEvenDifferenceTest(
        variance_ratio=variance_ratio,
        log_ratio=log_ratio,
        log_error=log_error,
        measured_variance_error=measured_error,
        random_variance_error=random_error,
        sigmas=log_ratio / log_error,
        significant=log_ratio > MODEL_AGREEMENT_SIGMAS * log_error,
    )


def measure_stack_fixed_pattern_noise_from_odd_even_difference(
    measured_noise: float,
    measured_apertures: int,
    random_noise: float,
    random_apertures: int,
) -> StackFixedPatternNoise:
    """Find a stack's fixed-pattern noise from a direct random noise.

    The random noise comes from the odd-minus-even difference of two half
    stacks, which cancels everything fixed on the sky (the library stack's own
    full-stack equivalent of it, see `StackRunRecord.odd_even_difference`).
    With no fixed-pattern noise, the full stack (odd plus even) and the
    difference have the same variance, and the aperture-to-aperture scatters of
    the two are independent estimates of it. The test is the ratio of the two
    variances,
    ``F = measured^2 / random^2``, on the log scale. Each variance has the
    relative error ``2 x 1.17 / sqrt(n)`` (`aperture_noise_relative_error`
    doubled), so ``ln F`` has the standard error ``sqrt(e1^2 + e2^2)``. A
    fixed-pattern noise is kept when ``ln F`` exceeds `MODEL_AGREEMENT_SIGMAS`
    times that error (one-sided). Only measurement errors count: the noise
    model plays no part, so its own error is not added, unlike in
    `measure_stack_fixed_pattern_noise`. The fixed-pattern noise's value is
    ``measured^2 - random^2``.

    Parameters
    ----------
    measured_noise : `float`
        The full stack's measured empty-aperture noise.
    measured_apertures : `int`
        The apertures behind ``measured_noise`` (at least 2).
    random_noise : `float`
        The full-stack equivalent of the odd–even difference noise, in the same
        unit as ``measured_noise``.
    random_apertures : `int`
        The apertures behind the difference noise (at least 2).

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise`
        The fixed-pattern noise, or a result with
        ``fixed_pattern_noise_detected`` false and the reason. ``basis`` is
        ``"odd_even_difference"``; ``fixed_pattern_variance_error`` is the
        error of ``measured^2 - random^2`` in variance units, ``sigmas`` is
        ``ln F`` over its error.
    """
    test = compare_stack_noise_with_odd_even_difference(
        measured_noise, measured_apertures, random_noise, random_apertures
    )
    measured_variance = measured_noise**2
    random_variance = random_noise**2
    measured_error = test.measured_variance_error
    random_error = test.random_variance_error
    sigmas = test.sigmas
    difference = measured_variance - random_variance
    error = math.hypot(measured_variance * measured_error, random_variance * random_error)
    common = {
        "source": "single_stack",
        "measured_noise": measured_noise,
        "predicted_random_noise": random_noise,
        "apertures": measured_apertures,
        "fixed_pattern_variance_error": error,
        "basis": FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE,
        "sigmas": sigmas,
    }
    if not test.significant and sigmas >= POSSIBLE_FIXED_PATTERN_SIGMAS:
        return StackFixedPatternNoise(
            **common,
            possible=True,
            fixed_pattern_variance=difference,
            fixed_pattern_noise=math.sqrt(difference),
            fixed_pattern_to_random_variance_ratio=difference / random_variance,
            fixed_pattern_variance_share=difference / measured_variance,
            reason=(
                f"Possible fixed-pattern noise of {math.sqrt(difference):.3g} (stack units), not proven: "
                f"the full stack's noise exceeds the random noise measured from the odd and even half "
                f"stacks by {sigmas:.1f} standard errors, below the {MODEL_AGREEMENT_SIGMAS:g} needed."
            ),
        )
    if not test.significant:
        return StackFixedPatternNoise(
            **common,
            reason=(
                "No fixed-pattern noise: the full stack's noise does not exceed the random noise "
                f"measured from the odd and even half stacks by {MODEL_AGREEMENT_SIGMAS:g} "
                f"standard errors ({sigmas:.1f} found)."
            ),
        )
    return StackFixedPatternNoise(
        **common,
        fixed_pattern_variance=difference,
        fixed_pattern_noise_detected=True,
        fixed_pattern_noise=math.sqrt(difference),
        fixed_pattern_to_random_variance_ratio=difference / random_variance,
        fixed_pattern_variance_share=difference / measured_variance,
        reason=(
            f"Fixed-pattern noise of {math.sqrt(difference):.3g} (stack units) was found, "
            f"measured from the odd and even half stacks: the full stack's variance is "
            f"{measured_variance / random_variance:.2f} times the random variance, {sigmas:.1f} "
            f"standard errors above 1 on the log scale, and the fixed-pattern noise is "
            f"{100 * difference / measured_variance:.0f} percent of the measured noise variance."
        ),
    )


def choose_fixed_pattern_noise(
    single_stack: StackFixedPatternNoise | None,
    fit: FixedPatternNoiseFit | None = None,
    current_expected_variance: float | None = None,
) -> StackFixedPatternNoise | None:
    """Pick the fixed-pattern noise the forecast uses from the estimates.

    There are two estimates. `measure_stack_fixed_pattern_noise` uses the one
    stack being extended and the library's own prediction (whose scale was
    checked on the half-stack noise, within 1 to 5 percent).
    `fit_fixed_pattern_noise` uses two or more stacks and fits the model's
    scale as well, so it does not depend on the prediction being right in
    scale. The rule:

    0. Fixed-pattern noise measured from the odd and even half stacks
       (`measure_stack_fixed_pattern_noise_from_odd_even_difference`) is used
       as it is. It measures the random noise directly, so neither the noise
       model nor a fit is consulted.
    1. A fit with at least one degree of freedom (the stacks' scatter checks
       the line) that finds fixed-pattern noise wins. Its fixed-pattern noise
       enters the forecast as a fraction ``b / (alpha * expected_variance)`` of
       today's stack.
    2. Otherwise the single-stack result is used, found or not. A fit that
       found no fixed-pattern noise with a degree of freedom, or one that has
       none, is named in the reason but does not override it.
    3. With no single-stack result, a fit that found fixed-pattern noise is
    used as it
       is (its reason says it has no check when it has none).

    Parameters
    ----------
    single_stack : `StackFixedPatternNoise` or `None`
        The result of `measure_stack_fixed_pattern_noise`, or ``None`` when the
        stack has no stored predicted noise.
    fit : `FixedPatternNoiseFit` or `None`
        The result of `fit_fixed_pattern_noise` over the target's stacks, if
        any.
    current_expected_variance : `float`, optional
        The expected variance of today's stack, in the fit's units. Needed to
        use a fit.

    Returns
    -------
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The chosen fixed_pattern, or ``None`` when there is no estimate at all.
    """
    if single_stack is not None and single_stack.basis == FIXED_PATTERN_BASIS_ODD_EVEN_DIFFERENCE:
        # The direct measurement of the random noise decides (user decision
        # 2026-10-26); a fit that disagrees is not allowed to override it.
        return single_stack
    fit_fixed_pattern = None
    fit_note = ""
    if fit is not None and fit.fixed_pattern_variance is not None and fit.alpha is not None:
        if current_expected_variance is None or current_expected_variance <= 0.0 or fit.alpha <= 0.0:
            fit_note = " The fit could not be used: today's expected variance is missing."
        elif fit.fixed_pattern_noise_detected and fit.fixed_pattern_variance > 0.0:
            ratio = fit.fixed_pattern_variance / (fit.alpha * current_expected_variance)
            share = ratio / (1.0 + ratio)
            random_noise = single_stack.predicted_random_noise if single_stack is not None else None
            stack_units = random_noise is not None
            fixed_pattern_noise = (
                math.sqrt(ratio) * random_noise if random_noise is not None else fit.fixed_pattern_noise
            )
            fit_fixed_pattern = StackFixedPatternNoise(
                source="fit",
                measured_noise=single_stack.measured_noise if single_stack else None,
                predicted_random_noise=single_stack.predicted_random_noise if single_stack else None,
                apertures=single_stack.apertures if single_stack else None,
                fixed_pattern_variance=fit.fixed_pattern_variance,
                fixed_pattern_variance_error=fit.fixed_pattern_variance_error,
                fixed_pattern_noise_detected=True,
                fixed_pattern_noise=fixed_pattern_noise,
                fixed_pattern_to_random_variance_ratio=ratio,
                fixed_pattern_variance_share=share,
                normalized_units=not stack_units,
                basis=FIXED_PATTERN_BASIS_FIT,
                reason=f"The fit over {fit.steps_used} stacks found fixed-pattern noise. {fit.reason}",
            )
            if fit.degrees_of_freedom >= 1 or single_stack is None:
                return fit_fixed_pattern
            fit_note = (
                " A two-stack fit also found fixed-pattern noise, but it has no degree of freedom "
                "to check it."
            )
        else:
            fit_note = (
                f" The fit over {fit.steps_used} stacks found no fixed-pattern noise"
                + ("" if fit.degrees_of_freedom >= 1 else " (and has no degree of freedom)")
                + "."
            )
    if single_stack is None:
        return None
    return single_stack.model_copy(update={"reason": single_stack.reason + fit_note})


def noise_ratio_with_fixed_pattern_noise(
    current: NoiseForecast, later: NoiseForecast | None, fixed_pattern: StackFixedPatternNoise | None
) -> float | None:
    """Give the predicted noise over the noise now, with fixed-pattern noise.

    The model gives the random variance of today's stack, ``V_now``, and of the
    later stack, ``V_later``, in one set of units. Fixed-pattern noise is
    carried as a fraction ``f`` of ``V_now``
    (`StackFixedPatternNoise.fixed_pattern_to_random_variance_ratio`), so no
    unit conversion is needed. The measured noise today is
    ``V_now (1 + f)``; later it is ``V_later + f V_now tau^2``, so the ratio is

        sqrt((V_later + f V_now tau^2) / (V_now (1 + f))) / tau

    where ``tau`` is 1, except in the ``group`` mode, where the combine scales
    the result by the mean exposure ``t_bar`` and ``tau = t_bar_later /
    t_bar_now``. The fixed-pattern noise is taken as a fixed pattern on the sky
    in rate units (the cause the validation favors: faint stars), so it follows
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
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The fixed-pattern noise, if one was found.

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
    if (
        fixed_pattern is None
        or not fixed_pattern.fixed_pattern_noise_detected
        or fixed_pattern.fixed_pattern_to_random_variance_ratio is None
    ):
        return later.noise / current.noise / tau
    fraction = fixed_pattern.fixed_pattern_to_random_variance_ratio
    later_total = later.variance + fraction * current.variance * tau**2
    now_total = current.variance * (1.0 + fraction)
    return math.sqrt(later_total / now_total) / tau


def noise_ratio_for_ranking(
    current: NoiseForecast, later: NoiseForecast | None, fixed_pattern: StackFixedPatternNoise | None
) -> float | None:
    """Give the one noise ratio the tonight ranking uses.

    With found or no fixed-pattern noise this is
    `noise_ratio_with_fixed_pattern_noise`. With a possible one
    (`StackFixedPatternNoise.possible`) it is the midpoint of the two cases on
    the log scale: the geometric mean of the ratio without the pattern and the
    ratio with it. Why the midpoint and not the low end: the low end would drop
    a target's gain by the whole range the moment its significance crossed 1
    standard error, the same cliff the range was made to remove, and would rank
    unproven noise as if it were found. The midpoint halves each step (at 1 and
    at 2 standard errors) and is symmetric in the evidence, so a target is
    neither favored nor penalized for a pattern that is not proven. The target
    report shows the full range.

    Parameters
    ----------
    current : `NoiseForecast`
        The forecast of today's stack.
    later : `NoiseForecast` or `None`
        The forecast of the larger stack.
    fixed_pattern : `StackFixedPatternNoise` or `None`
        The stack's fixed-pattern noise, found, possible or neither.

    Returns
    -------
    ratio : `float` or `None`
        Later noise over today's; ``None`` when a forecast is missing.
    """
    if fixed_pattern is None or not fixed_pattern.possible:
        return noise_ratio_with_fixed_pattern_noise(current, later, fixed_pattern)
    without = noise_ratio_with_fixed_pattern_noise(current, later, None)
    with_pattern = noise_ratio_with_fixed_pattern_noise(current, later, fixed_pattern.if_real())
    if without is None or with_pattern is None:
        return None
    return math.sqrt(without * with_pattern)


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
    reference_exposure_seconds : `float` or `None`
        ``t_ref``: the exposure of the group's registration reference frame
        (``stacking.group_reference_exposure_s``). Siril scales every frame
        of a group to that frame, so a frame of length ``t_i`` enters the
        group stack multiplied by ``k_i = t_ref / t_i``. ``None`` means the
        frames are not rescaled (every ``k_i`` is 1), which is right for a
        group of one length and is today's model.
    noise_length_seconds : `float` or `None`
        The exposure length that `frame_noise_adu` stands for. The stacker
        measures ``s_g`` as the median noise of a few raw frames sampled from
        the group, so for a folded group (a few frames of another length in a
        group of one main length) it is the noise of a frame of the group's
        own length, not of the reference frame and not of the folded frames.
        ``None`` means the most common exposure among the frames.
    """

    model_config = ConfigDict(populate_by_name=True, alias_generator=to_camel, frozen=True)

    frames: tuple[StackFrame, ...]
    frame_noise_adu: float | None = Field(default=None, gt=0.0)
    gain: float = Field(default=1.0, gt=0.0)
    electrons_per_adu: float | None = Field(default=None, gt=0.0)
    noise_predicted: bool = False
    in_group_weights: tuple[float, ...] | None = None
    reference_exposure_seconds: float | None = Field(default=None, gt=0.0)
    noise_length_seconds: float | None = Field(default=None, gt=0.0)

    @property
    def noise_length(self) -> float:
        """Give the exposure length that the group's frame noise stands for.

        Returns
        -------
        length : `float`
            `noise_length_seconds`, or the most common exposure of the frames
            (the shortest one when two are equally common); 0 for a group
            with no frames.
        """
        if self.noise_length_seconds is not None:
            return self.noise_length_seconds
        if not self.frames:
            return 0.0
        counts: dict[float, int] = {}
        for frame in self.frames:
            counts[frame.exposure_seconds] = counts.get(frame.exposure_seconds, 0) + 1
        return min(counts, key=lambda exposure: (-counts[exposure], exposure))

    @property
    def frame_noise_factors(self) -> tuple[float, ...]:
        """Give each frame's noise relative to the `frame_noise_adu` frame.

        Frame ``i`` enters the group stack scaled by ``k_i = t_ref / t_i``,
        so its noise in the group's units is ``k_i`` times its own raw noise.
        Its own raw noise, in electrons, is ``sqrt(B_i t_i + RN^2)`` (``B_i``
        the frame's sky rate, ``RN`` its read noise). The factor compares
        that with a frame of the group's noise length ``t_len`` (what
        `frame_noise_adu` measures) under the same sky and read noise::

            factor_i = k_i sqrt((B_i t_i + RN^2) / (B_i t_len + RN^2))

        When a frame has no sky rate and no read noise (both 0, which is how
        "not known" reaches this model) the electron form has nothing to
        divide, and the factor is the sky-limited form
        ``k_i sqrt(t_i / t_len)``, where the sky photons alone set the noise.
        A group with no reference exposure is not rescaled: every factor is
        1, and an unfolded group (one length) gives 1 either way.

        Returns
        -------
        factors : `tuple` [`float`, ...]
            One factor per frame, in frame order.
        """
        if self.reference_exposure_seconds is None:
            return (1.0,) * len(self.frames)
        length = self.noise_length
        factors = []
        for frame in self.frames:
            scale = self.reference_exposure_seconds / frame.exposure_seconds
            own = frame.sky_rate_e_per_s_per_px * frame.exposure_seconds + frame.read_noise_e**2
            at_length = frame.sky_rate_e_per_s_per_px * length + frame.read_noise_e**2
            if own > 0.0 and at_length > 0.0:
                factors.append(scale * math.sqrt(own / at_length))
            else:
                factors.append(scale * math.sqrt(frame.exposure_seconds / length))
        return tuple(factors)

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

    The stacker measures ``s_g`` as the median noise of a few raw frames
    sampled from the group, and takes ``t_g`` as the group's mean exposure. For
    a folded group the rows therefore give the noise of a typical frame of the
    group's main length, not of the reference frame or of a folded frame; the
    rows cannot say which frames were sampled.
    `StackGroup.noise_length_seconds`
    records the length the result stands for.

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


def group_frame_rate_variances(groups: Sequence[StackGroup]) -> list[float]:
    """Give each frame's variance in rate units, as the group combine sees it.

    A group with no reference exposure is not rescaled, so its frames keep
    their own `StackFrame.rate_variance` (the model before folded groups).
    For a group with one, frame ``i`` is multiplied by ``k_i`` inside Siril,
    so its noise is ``factor_i`` times that of a frame of the group's noise
    length (`StackGroup.frame_noise_factors`). The combine then divides the
    group stack by the group's exposure ``t_g``, so the variance in rate units
    is ``factor_i^2 (B_i t_len + RN^2) / t_g^2``. For a group of one length
    this is the frame's own rate variance.

    Parameters
    ----------
    groups : `Sequence` [`StackGroup`]
        The groups, in the order of the frames.

    Returns
    -------
    variances : `list` [`float`]
        One variance per frame, groups in order.
    """
    variances: list[float] = []
    for group in groups:
        if group.reference_exposure_seconds is None:
            variances.extend(frame.rate_variance for frame in group.frames)
            continue
        length = group.noise_length
        group_exposure = group.mean_exposure_seconds
        for frame, factor in zip(group.frames, group.frame_noise_factors, strict=True):
            at_length = frame.sky_rate_e_per_s_per_px * length + frame.read_noise_e**2
            variances.append(factor**2 * at_length / group_exposure**2)
    return variances


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
    and ``sum(W)`` as the normalization). For a folded group ``v_i`` includes
    the frame's scaling to the group's reference frame
    (`group_frame_rate_variances`); an unfolded group is unchanged. The
    combine then scales the result
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
        frames,
        weights,
        WeightingMode.GROUP,
        weight_normalization=coefficients.total_group_weight,
        rate_variances=group_frame_rate_variances(groups),
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
        Groups that hold folded frames of another length. This is information
        only: such a group is forecast like any other, with the folded frames
        scaled to the group's reference frame
        (`StackGroup.frame_noise_factors`).
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


def _length_key(exposure_seconds: float) -> float | None:
    """Give the exposure length a frame is grouped under.

    Parameters
    ----------
    exposure_seconds : `float`
        A frame's exposure.

    Returns
    -------
    length : `float` or `None`
        The exposure rounded the way `fold_exposure_lengths` rounds it (lengths
        that match to three significant figures are one length).
    """
    return fold_exposure_lengths([exposure_seconds])[0]


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
      nearest large group (listed in `GroupPlan.folded_group_lengths`). The
      group's registration reference frame keeps the exposure it had, and
      each folded frame enters the group stack scaled to it, so its noise is
      scaled by ``k = t_ref / t`` (`StackGroup.frame_noise_factors`). A group
      whose reference exposure is not known cannot take folded frames, and
      the plan is refused with that reason;
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
        if own_length != destination:
            outcome = "folded_into_group"
        elif destination in existing_lengths:
            outcome = "joins_existing_group"
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
        lengths_held = {_length_key(frame.exposure_seconds) for frame in (*group.frames, *added)}
        if group.reference_exposure_seconds is None and len(lengths_held) > 1:
            if len({_length_key(frame.exposure_seconds) for frame in group.frames}) == 1:
                # The old frames share one length, so the reference frame was
                # one of them; the new frames of another length scale to it.
                group = group.model_copy(
                    update={
                        "reference_exposure_seconds": group.frames[0].exposure_seconds,
                        "noise_length_seconds": group.frames[0].exposure_seconds,
                    }
                )
            else:
                return _refused_plan(
                    f"The {float(length):g} s group holds frames of more than one length and its "
                    "reference frame's exposure is not known.",
                    placements,
                )
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
        own_length_frames = [frame for frame in added if _length_key(frame.exposure_seconds) == length]
        planned.append(
            StackGroup(
                frames=tuple(added),
                # The noise stands for a frame of the group's own length.
                frame_noise_adu=predict_group_frame_noise_adu(own_length_frames or added, camera_gain),
                gain=existing[nearest].gain,
                electrons_per_adu=camera_gain,
                noise_predicted=True,
                reference_exposure_seconds=length,
                noise_length_seconds=length,
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
    fixed_pattern: StackFixedPatternNoise | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
    maximum_new_frames: int = MAXIMUM_NEW_FRAMES,
) -> TakeMoreResult:
    """Find how many more frames cut the noise of a group stack.

    Each trial adds N copies of the template frames (cycled, as in
    `frames_to_cut_noise`) and builds the later stack with `plan_group_stack`,
    so the number comes from the model the forecast check tests. A new
    exposure length is never suggested with fewer than
    `MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH` frames: the search starts there,
    because the stacker would fold a smaller set into another group.
    Fixed-pattern noise is applied as in `frames_to_cut_noise`.

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
    fixed_pattern : `StackFixedPatternNoise`, optional
        The stack's fixed-pattern noise, when one was found.
    target_noise_ratio : `float`, optional
        The noise after over the noise now. The default is 0.707.
    maximum_new_frames : `int`, optional
        Where the search gives up.

    Returns
    -------
    result : `TakeMoreResult`
        N and the predicted ratio, or ``None`` with a reason (fixed-pattern
        noise that limits the answer, a group the stacker would leave out, a
        missing fact). The result also says whether the frames start a new
        group and whether the heaviest group changes.
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

    def ratio_for(count: int, pattern: StackFixedPatternNoise | None) -> float | None:
        """Give the predicted noise ratio after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.
        pattern : `StackFixedPatternNoise` or `None`
            The fixed-pattern noise to apply.

        Returns
        -------
        ratio : `float` or `None`
            Later noise over today's.
        """
        return noise_ratio_with_fixed_pattern_noise(current, plan_for(count).forecast, pattern)

    result = _take_more_result_graded(
        ratio_for,
        minimum_count=minimum_count,
        maximum_count=maximum_new_frames,
        target_noise_ratio=target_noise_ratio,
        exposure_seconds=exposure,
        fixed_pattern=fixed_pattern,
        held_exposure_seconds=sum(
            frame.exposure_seconds for group in existing_groups for frame in group.frames
        ),
    )
    answer_plan = plan_for(result.frames_needed or result.doubling_frames or minimum_count)
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
    return result.model_copy(
        update={
            "new_exposure_group": starts_new_group,
            "heaviest_group_changed": answer_plan.heaviest_group_changed,
            "reason": " ".join(filter(None, [result.reason, *notes])),
        }
    )
