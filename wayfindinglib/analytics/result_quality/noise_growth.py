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

When the mode was not recorded (Siril does not report the weights of its
noise and star-count modes) the forecast is given as "weighting mode not
recorded", not guessed.

"Take N more"
-------------
`frames_to_cut_noise` adds N copies of past frames of the same capture
setting until the predicted noise falls to 1/sqrt(2) of today's (doubling
the effective time cuts noise by about 29 percent). It stays a design
estimate: on M 57 the real subsets came out 9 to 31 percent below the
prediction (validation register entry STK-36), so the answer is labeled
tentative.

The floor
---------
A stack stops improving when a fixed pattern or another systematic is left.
`fit_noise_floor` fits ``measured_variance = alpha * expected_variance + b``
over the history of a target's stacks. A floor is reported only when b is
above 0 by two standard errors. The error of each measured noise is that of
the empty-aperture estimate, 1.17 / sqrt(n) of the noise for n apertures.
These errors are unpaired unless a row says it is paired (only the
leave-one-night-out rows are paired today), and the fit says which it used.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import NOISE_WEIGHT_MODE, choose_stack_weight, wfwhm_weights
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


class WeightingMode(StrEnum):
    """How the stacker weighted the frames."""

    EQUAL = "equal"
    NOISE = "noise"
    WFWHM = "wfwhm"
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
    """

    model_config = _MODEL_CONFIG

    variance: float | None = None
    noise: float | None = None
    frame_count: int = 0
    mode: WeightingMode
    reason: str | None = None


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
        ``noise``, ``wfwhm`` or ``equal``; ``unknown`` for a configured mode
        the forward model does not know (such as star count).
    """
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
    frames: Sequence[StackFrame], weights: Sequence[float], mode: WeightingMode = WeightingMode.UNKNOWN
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

    Returns
    -------
    forecast : `NoiseForecast`
        The variance ``sum(w^2 v) / sum(w)^2`` in rate units, or ``None`` with
        a reason when the lists differ in length or all weights are 0.
    """
    if len(weights) != len(frames):
        return NoiseForecast(
            mode=mode, frame_count=len(frames), reason="The weights and the frames do not match up."
        )
    total_weight = sum(weights)
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
    frames: Sequence[StackFrame], recorded_weights: Sequence[float | None]
) -> WeightingModeEvidence:
    """Find which weighting mode explains the weights a stack run recorded.

    The recorded weights are the ``stacking.frame_weight`` rows of the run.
    A row that was not measured (Siril does not report the weights of its
    noise and star-count modes) is ``None``. Both the recorded and the
    predicted weights are divided by their mean before they are compared.

    Parameters
    ----------
    frames : `Sequence` [`StackFrame`]
        The frames of the run, in the order of `recorded_weights`.
    recorded_weights : `Sequence` [`float` or `None`]
        The run's recorded weights.

    Returns
    -------
    evidence : `WeightingModeEvidence`
        The matching mode (``equal`` first, then ``noise``, then ``wfwhm``),
        or ``unknown`` with the reason.
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


def frames_to_cut_noise(
    existing: Sequence[StackFrame],
    template: Sequence[StackFrame],
    mode: WeightingMode,
    *,
    new_exposure_seconds: float | None = None,
    target_noise_ratio: float = TARGET_NOISE_RATIO,
    maximum_new_frames: int = MAXIMUM_NEW_FRAMES,
) -> TakeMoreResult:
    """Find how many more frames cut the noise ("take N more").

    New frames follow the past frames of the same capture setting: frame k
    copies template frame ``k mod len(template)``, so the sky rates and star
    widths of the new frames match the target's history. When the new
    exposure differs from the old, the new frames keep their sky rates (per
    second) but change their read-noise share, so a read-noise-limited short
    frame counts for less than a long one.

    The search doubles N until the target is reached, then bisects. Width
    weights make the noise slightly uneven in N, so the bisection result is
    checked one step lower.

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
        if forecast is None or forecast.noise is None or not current.noise:
            return None
        return forecast.noise / current.noise

    upper = 1
    while upper <= maximum_new_frames:
        ratio = ratio_for(upper)
        if ratio is not None and ratio <= target_noise_ratio:
            break
        upper *= 2
    else:
        return TakeMoreResult(
            target_noise_ratio=target_noise_ratio,
            reason=f"The target was not reached with {maximum_new_frames} more frames.",
        )
    lower = upper // 2
    while upper - lower > 1:
        middle = (lower + upper) // 2
        ratio = ratio_for(middle)
        if ratio is not None and ratio <= target_noise_ratio:
            upper = middle
        else:
            lower = middle
    final_ratio = ratio_for(upper)
    exposure = new_exposure_seconds or float(np.mean([frame.exposure_seconds for frame in template]))
    return TakeMoreResult(
        frames_needed=upper,
        predicted_noise_ratio=final_ratio,
        target_noise_ratio=target_noise_ratio,
        added_exposure_seconds=upper * exposure,
        new_exposure_seconds=exposure,
        reason=(
            "A design estimate: real subsets of M 57 came out 9 to 31 percent below the prediction (STK-36)."
        ),
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
