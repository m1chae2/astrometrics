"""Purpose: Summaries of a night or a target, computed when read.

Description: Combines the per-frame results of `efficiency` into the
shapes the reports show: the loss account, the summary of each capture
setting, and the summary of a night or a whole target. Nothing here is
stored; the target report and the night view call these functions each
time they are read (decision 18).

The loss account keeps two parts apart: the frames the stacker set aside
(their whole exposure time is lost) and the efficiency deficit of the kept
frames (captured minus effective time, split by cause). The causes add up
exactly to the deficit, because the split shares each frame's loss by the
logarithms of its factors. Whatever the factors cannot explain is listed as
"cause unknown" only where the efficiency is measured independently, from
the stack's measured noise against the noise expected for its frames.
Otherwise it is "not measured".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from astrometricslib import CaptureSetting
from wayfindinglib.analytics.result_quality.efficiency import (
    CAUSE_UNKNOWN,
    TRANSPARENCY_NOT_INCLUDED_REASON,
    EfficiencyAssessment,
    FrameIssue,
    FrameObservation,
    assess_efficiency,
    split_kept_frame_deficit,
)
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.efficiency import (
    EfficiencyReference,
    LossAccount,
    LossShare,
    ScienceGoal,
)

_MODEL_CONFIG = ConfigDict(populate_by_name=True, alias_generator=to_camel)

ReferenceProvider = Callable[[CaptureSetting], EfficiencyReference | None]
"""A function that gives the reference for a capture setting, or ``None`` when
there is none."""

MODEL_AGREEMENT_SIGMAS = 2.0
"""The measured stack noise is said to disagree with the model only when its
variance ratio exceeds 1 by this many standard errors. Two standard errors is a
one-in-twenty false alarm. Design estimate, register row WAY-D34."""


class MeasuredNoiseCheck(BaseModel):
    """A stack's measured noise against the noise expected for its frames.

    This is the independent measure of efficiency (decision S15): the
    expected noise comes from the frames' sky and read noise
    (``stacking.expected_empty_aperture_noise_stack_unit``), and the
    measured noise from empty apertures
    (``stacking.empty_aperture_noise_stack_unit``).

    Parameters
    ----------
    variance_ratio : `float`
        (measured noise / expected noise) squared. Above 1 means the stack is
        noisier than its frames explain.
    variance_ratio_error : `float`
        One standard error of that ratio. For unpaired apertures it is
        ``2 * 1.17 / sqrt(n)`` times the ratio.
    """

    model_config = _MODEL_CONFIG

    variance_ratio: float = Field(gt=0.0)
    variance_ratio_error: float = Field(ge=0.0)


class SetAsideInput(BaseModel):
    """A frame the stacker set aside, for the loss account.

    Parameters
    ----------
    frame_id : `str`
        The frame.
    exposure_seconds : `float`
        Its exposure time, in seconds.
    reason : `str`
        Why it was set aside (``clouded``, ``trailed`` or the stacker's own
        words).
    capture_setting : `CaptureSetting`
        Its capture setting.
    night_id : `str` or `None`
        Its observing night.
    """

    model_config = _MODEL_CONFIG

    frame_id: str
    exposure_seconds: float = Field(ge=0.0)
    reason: str
    capture_setting: CaptureSetting
    night_id: str | None = None


class LossAccountResult(BaseModel):
    """A loss account with the frames that could not be assessed.

    Parameters
    ----------
    account : `LossAccount`
        The account.
    frames_without_efficiency : `list` [`FrameIssue`]
        Kept frames that no factor could be found for. Their time is in
        neither part of the account.
    notes : `list` [`str`]
        Plain remarks (assumed fields, mixed regimes).
    """

    model_config = _MODEL_CONFIG

    account: LossAccount
    frames_without_efficiency: list[FrameIssue] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DataSummary(BaseModel):
    """What was taken in a night or for a target.

    Parameters
    ----------
    scope : `str`
        ``"night"`` or ``"target"``.
    scope_id : `str`
        The night id or the target id.
    nights : `list` [`str`]
        The observing nights in the scope.
    frames_taken : `int`
        Kept and set-aside frames.
    frames_set_aside : `int`
        Frames set aside.
    captured_seconds : `float`
        Exposure time of all frames taken.
    efficiency_effective_seconds : `float` or `None`
        Effective time of the kept frames (eta times captured time). ``None``
        when no frame could be assessed.
    setting_summaries : `list` [`CaptureSettingSummary`]
        One per capture setting.
    loss_account : `LossAccount` or `None`
        Where the time went, for the whole scope. ``None`` when no reference
        was available.
    notes : `list` [`str`]
        Remarks, including settings with no reference and frames that could
        not be assessed.
    """

    model_config = _MODEL_CONFIG

    scope: str
    scope_id: str
    nights: list[str] = Field(default_factory=list)
    frames_taken: int = Field(ge=0)
    frames_set_aside: int = Field(ge=0)
    captured_seconds: float = Field(ge=0.0)
    efficiency_effective_seconds: float | None = Field(default=None, ge=0.0)
    setting_summaries: list[CaptureSettingSummary] = Field(default_factory=list)
    loss_account: LossAccount | None = None
    notes: list[str] = Field(default_factory=list)


def _unknown_part(effective_by_factors: float, check: MeasuredNoiseCheck | None) -> tuple[float | None, str]:
    """Find the part of the efficiency that no listed cause explains.

    Effective time scales as 1 / variance. If the stack's variance is `r`
    times the variance its frames explain, its effective time is the
    factors' effective time divided by `r`, so the unexplained part is
    ``E (1 - 1 / r)``. It is reported only when ``r - 1`` exceeds
    `MODEL_AGREEMENT_SIGMAS` standard errors.

    Parameters
    ----------
    effective_by_factors : `float`
        Effective time from the factors, in seconds.
    check : `MeasuredNoiseCheck` or `None`
        The measured noise check, if a stack was measured.

    Returns
    -------
    seconds, reason : `tuple` [`float` or `None`, `str`]
        The unexplained seconds (``None`` means "not measured") and the
        reason.
    """
    if check is None:
        return None, "Not measured: no stack noise was measured against the noise its frames explain."
    excess = check.variance_ratio - 1.0
    if excess <= MODEL_AGREEMENT_SIGMAS * check.variance_ratio_error:
        return 0.0, "The stack's measured noise agrees with the noise its frames explain, within error."
    return (
        effective_by_factors * (1.0 - 1.0 / check.variance_ratio),
        "The stack is noisier than its frames' sky and read noise explain; no listed cause accounts for it.",
    )


def _set_aside_shares(set_aside: Iterable[SetAsideInput]) -> tuple[float, list[LossShare]]:
    """Total the set-aside time and split it by reason.

    Parameters
    ----------
    set_aside : `Iterable` [`SetAsideInput`]
        The frames set aside.

    Returns
    -------
    total, shares : `tuple` [`float`, `list` [`LossShare`]]
        Seconds in all and by reason. More exposure time replaces them, so
        each share is recoverable with time.
    """
    by_reason: dict[str, float] = {}
    for frame in set_aside:
        by_reason[frame.reason] = by_reason.get(frame.reason, 0.0) + frame.exposure_seconds
    shares = [
        LossShare(cause=reason, seconds=seconds, recoverableWithTime=True, modelDerived=False)
        for reason, seconds in sorted(by_reason.items(), key=lambda item: -item[1])
    ]
    return sum(by_reason.values()), shares


def loss_account(
    observations: Sequence[FrameObservation],
    set_aside: Sequence[SetAsideInput],
    goal: ScienceGoal,
    reference_for: ReferenceProvider,
    *,
    measured_noise_check: MeasuredNoiseCheck | None = None,
    moonless_sky_rate_by_frame: Mapping[str, float] | None = None,
) -> LossAccountResult | None:
    """Build the loss account of a set of frames.

    Frames in `observations` that carry a ``set_aside_reason`` are counted
    with the frames in `set_aside`, so either way of passing them works.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The frames (kept, and optionally set aside).
    set_aside : `Sequence` [`SetAsideInput`]
        More set-aside frames, from the stacker's exclusion records.
    goal : `ScienceGoal`
        The science goal.
    reference_for : `ReferenceProvider`
        Gives the reference for a frame's capture setting. A setting with no
        reference (``None``) is skipped and named in the notes.
    measured_noise_check : `MeasuredNoiseCheck`, optional
        The stack's measured noise against the expected noise. Without it
        the "cause unknown" part is "not measured".
    moonless_sky_rate_by_frame : `Mapping` [`str`, `float`], optional
        The sky model's no-Moon sky rate for each frame. With it the sky
        loss is split into the Moon's part (model-derived) and the rest.

    Returns
    -------
    result : `LossAccountResult` or `None`
        The account, or ``None`` when no setting had a reference.
    """
    groups: dict[CaptureSetting, list[FrameObservation]] = {}
    for observation in observations:
        groups.setdefault(observation.capture_setting, []).append(observation)
    frame_results = []
    issues: list[FrameIssue] = []
    notes: list[str] = []
    reference_names: list[str] = []
    set_aside_inputs = list(set_aside)
    sky_rates: dict[str, float] = {}
    any_reference = False
    for setting, group in groups.items():
        reference = reference_for(setting)
        for observation in group:
            if observation.set_aside_reason is not None:
                set_aside_inputs.append(
                    SetAsideInput(
                        frame_id=observation.frame_id,
                        exposure_seconds=observation.effective_exposure_seconds or 0.0,
                        reason=observation.set_aside_reason,
                        capture_setting=setting,
                        night_id=observation.night_id,
                    )
                )
            elif observation.sky_rate_e_per_s_per_px is not None:
                sky_rates[observation.frame_id] = observation.sky_rate_e_per_s_per_px
        if reference is None:
            notes.append(
                f"No reference for the setting {setting.camera} {setting.filter_name}; "
                "its frames are left out."
            )
            continue
        any_reference = True
        if reference.name not in reference_names:
            reference_names.append(reference.name)
        assessment = assess_efficiency(group, goal, reference)
        frame_results.extend(assessment.frame_results)
        issues.extend(assessment.frames_without_efficiency)
        notes.extend(assessment.notes)
    if not any_reference:
        return None
    set_aside_total, set_aside_shares = _set_aside_shares(set_aside_inputs)
    deficit = split_kept_frame_deficit(
        frame_results,
        moonless_sky_rate_by_frame=moonless_sky_rate_by_frame,
        sky_rate_by_frame=sky_rates,
    )
    effective = sum(item.exposure_seconds * (item.eta or 0.0) for item in frame_results)
    unknown_seconds, unknown_reason = _unknown_part(effective, measured_noise_check)
    account = LossAccount(
        goal=goal,
        referenceName="; ".join(reference_names),
        setAsideSeconds=set_aside_total,
        setAside=set_aside_shares,
        keptFrameDeficit=deficit,
        causeUnknownSeconds=unknown_seconds,
        causeUnknownReason=unknown_reason,
        transparencyIncluded=False,
        transparencyReason=TRANSPARENCY_NOT_INCLUDED_REASON,
    )
    return LossAccountResult(account=account, frames_without_efficiency=issues, notes=sorted(set(notes)))


def capture_setting_summaries(
    observations: Sequence[FrameObservation],
    goal: ScienceGoal,
    reference_for: ReferenceProvider,
    *,
    weight_effective_seconds_by_setting: Mapping[CaptureSetting, float] | None = None,
    set_aside: Sequence[SetAsideInput] = (),
    measured_noise_check_by_setting: Mapping[CaptureSetting, MeasuredNoiseCheck] | None = None,
) -> list[CaptureSettingSummary]:
    """Summarize what was taken with each capture setting.

    Frames taken are the kept frames plus the frames set aside (whether they
    are marked on the observation or passed in `set_aside`). Captured
    seconds count all of them.

    Parameters
    ----------
    observations : `Sequence` [`FrameObservation`]
        The frames.
    goal : `ScienceGoal`
        The science goal.
    reference_for : `ReferenceProvider`
        Gives the reference for a capture setting.
    weight_effective_seconds_by_setting : `Mapping`, optional
        The stacker's weighted effective time of each setting's stack. A
        setting without one gets ``None``.
    set_aside : `Sequence` [`SetAsideInput`], optional
        Frames set aside that are not in `observations`.
    measured_noise_check_by_setting : `Mapping`, optional
        The measured noise check of each setting's stack.

    Returns
    -------
    summaries : `list` [`CaptureSettingSummary`]
        One per setting, in the order the settings first appear.
    """
    kept: dict[CaptureSetting, list[FrameObservation]] = {}
    aside: dict[CaptureSetting, list[SetAsideInput]] = {}
    for observation in observations:
        kept.setdefault(observation.capture_setting, []).append(observation)
        if observation.set_aside_reason is not None:
            aside.setdefault(observation.capture_setting, [])
    for frame in set_aside:
        aside.setdefault(frame.capture_setting, []).append(frame)
        kept.setdefault(frame.capture_setting, [])
    summaries: list[CaptureSettingSummary] = []
    for setting, group in kept.items():
        extra = aside.get(setting, [])
        marked = [o for o in group if o.set_aside_reason is not None]
        kept_group = [o for o in group if o.set_aside_reason is None]
        nights = {o.night_id for o in group if o.night_id} | {f.night_id for f in extra if f.night_id}
        captured = sum(o.effective_exposure_seconds or 0.0 for o in group) + sum(
            f.exposure_seconds for f in extra
        )
        reference = reference_for(setting)
        efficiency_effective = None
        account = None
        if reference is not None:
            assessment: EfficiencyAssessment = assess_efficiency(kept_group, goal, reference)
            efficiency_effective = assessment.result.efficiency_effective_seconds
            result = loss_account(
                group,
                extra,
                goal,
                lambda _setting, reference=reference: reference,
                measured_noise_check=(measured_noise_check_by_setting or {}).get(setting),
            )
            account = result.account if result else None
        summaries.append(
            CaptureSettingSummary(
                captureSetting=setting,
                framesTaken=len(group) + len(extra),
                nights=len(nights),
                framesSetAside=len(marked) + len(extra),
                capturedSeconds=captured,
                weightEffectiveSeconds=(weight_effective_seconds_by_setting or {}).get(setting),
                efficiencyEffectiveSeconds=efficiency_effective,
                lossAccount=account,
            )
        )
    return summaries


def _summary(
    scope: str,
    scope_id: str,
    observations: Sequence[FrameObservation],
    set_aside: Sequence[SetAsideInput],
    goal: ScienceGoal,
    reference_for: ReferenceProvider,
    weight_effective_seconds_by_setting: Mapping[CaptureSetting, float] | None,
    measured_noise_check_by_setting: Mapping[CaptureSetting, MeasuredNoiseCheck] | None,
    moonless_sky_rate_by_frame: Mapping[str, float] | None,
) -> DataSummary:
    """Build a `DataSummary` for already-filtered frames.

    Parameters
    ----------
    scope, scope_id : `str`
        What the summary covers.
    observations : `Sequence` [`FrameObservation`]
        The frames in scope.
    set_aside : `Sequence` [`SetAsideInput`]
        The set-aside frames in scope.
    goal : `ScienceGoal`
        The science goal.
    reference_for : `ReferenceProvider`
        Gives the reference for a setting.
    weight_effective_seconds_by_setting, measured_noise_check_by_setting :
    `Mapping`
        Per-setting stack facts, or ``None``.
    moonless_sky_rate_by_frame : `Mapping`
        The sky model's no-Moon rates, or ``None``.

    Returns
    -------
    summary : `DataSummary`
        The summary.
    """
    settings = capture_setting_summaries(
        observations,
        goal,
        reference_for,
        weight_effective_seconds_by_setting=weight_effective_seconds_by_setting,
        set_aside=set_aside,
        measured_noise_check_by_setting=measured_noise_check_by_setting,
    )
    overall = loss_account(
        observations,
        set_aside,
        goal,
        reference_for,
        moonless_sky_rate_by_frame=moonless_sky_rate_by_frame,
    )
    notes: list[str] = []
    if overall is not None:
        notes.extend(overall.notes)
        notes.extend(f"{issue.frame_id}: {issue.reason}" for issue in overall.frames_without_efficiency)
    else:
        notes.append("No reference could be built, so there is no efficiency or loss account.")
    effective_values = [
        s.efficiency_effective_seconds for s in settings if s.efficiency_effective_seconds is not None
    ]
    nights = sorted(
        {o.night_id for o in observations if o.night_id} | {f.night_id for f in set_aside if f.night_id}
    )
    return DataSummary(
        scope=scope,
        scope_id=scope_id,
        nights=nights,
        framesTaken=sum(s.frames_taken for s in settings),
        framesSetAside=sum(s.frames_set_aside for s in settings),
        capturedSeconds=sum(s.captured_seconds for s in settings),
        efficiencyEffectiveSeconds=sum(effective_values) if effective_values else None,
        settingSummaries=settings,
        lossAccount=overall.account if overall else None,
        notes=notes,
    )


def night_summary(
    night_id: str,
    observations: Sequence[FrameObservation],
    goal: ScienceGoal,
    reference_for: ReferenceProvider,
    *,
    set_aside: Sequence[SetAsideInput] = (),
    weight_effective_seconds_by_setting: Mapping[CaptureSetting, float] | None = None,
    moonless_sky_rate_by_frame: Mapping[str, float] | None = None,
) -> DataSummary:
    """Summarize one observing night.

    Parameters
    ----------
    night_id : `str`
        The night. Only frames of this night are used.
    observations : `Sequence` [`FrameObservation`]
        Frames of any nights; they are filtered by ``night_id``.
    goal : `ScienceGoal`
        The science goal.
    reference_for : `ReferenceProvider`
        Gives the reference for a capture setting.
    set_aside : `Sequence` [`SetAsideInput`], optional
        Set-aside frames of any nights.
    weight_effective_seconds_by_setting : `Mapping`, optional
        The stacker's weighted time of each setting, if known for the night.
    moonless_sky_rate_by_frame : `Mapping`, optional
        The sky model's no-Moon rates, to split the Moon's share.

    Returns
    -------
    summary : `DataSummary`
        The night's summary (scope ``"night"``).
    """
    return _summary(
        "night",
        night_id,
        [o for o in observations if o.night_id == night_id],
        [f for f in set_aside if f.night_id == night_id],
        goal,
        reference_for,
        weight_effective_seconds_by_setting,
        None,
        moonless_sky_rate_by_frame,
    )


def target_summary(
    target_id: str,
    observations: Sequence[FrameObservation],
    goal: ScienceGoal,
    reference_for: ReferenceProvider,
    *,
    set_aside: Sequence[SetAsideInput] = (),
    weight_effective_seconds_by_setting: Mapping[CaptureSetting, float] | None = None,
    measured_noise_check_by_setting: Mapping[CaptureSetting, MeasuredNoiseCheck] | None = None,
    moonless_sky_rate_by_frame: Mapping[str, float] | None = None,
) -> DataSummary:
    """Summarize all the frames of a target.

    Parameters
    ----------
    target_id : `str`
        The target. Frames of other targets are ignored when their
        ``target_id`` is set and differs.
    observations : `Sequence` [`FrameObservation`]
        The frames.
    goal : `ScienceGoal`
        The science goal (from the target's catalog type).
    reference_for : `ReferenceProvider`
        Gives the reference for a capture setting.
    set_aside : `Sequence` [`SetAsideInput`], optional
        Frames the stacker set aside.
    weight_effective_seconds_by_setting : `Mapping`, optional
        The stacker's weighted time of each setting's stack.
    measured_noise_check_by_setting : `Mapping`, optional
        The measured-noise check of each setting's stack, for the "cause
        unknown" part.
    moonless_sky_rate_by_frame : `Mapping`, optional
        The sky model's no-Moon rates.

    Returns
    -------
    summary : `DataSummary`
        The target's summary (scope ``"target"``).
    """
    return _summary(
        "target",
        target_id,
        [o for o in observations if o.target_id in (None, target_id)],
        list(set_aside),
        goal,
        reference_for,
        weight_effective_seconds_by_setting,
        measured_noise_check_by_setting,
        moonless_sky_rate_by_frame,
    )


__all__ = [
    "CAUSE_UNKNOWN",
    "DataSummary",
    "LossAccountResult",
    "MeasuredNoiseCheck",
    "ReferenceProvider",
    "SetAsideInput",
    "capture_setting_summaries",
    "loss_account",
    "night_summary",
    "target_summary",
]
