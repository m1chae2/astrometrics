"""Purpose: Find how long an exposure the guiding lets stay sharp.

Description: A longer exposure gives the guiding more time to wander, so a
smaller share of frames stays sharp. This module answers, for one capture
setting: "At 120 s only 40% of frames stay sharp; at 60 s, 85%." (plan section
6.6, the item "Guiding limits exposure").

The user decided on 2026-10-24 (issue #95, decision U3) that this rule is
built now. The steps are:

1. The star-width budget gives the largest guide error a frame can have and
   still be sharp (`allowed_guide_rms_arcsec`). Guiding blurs a star by
   adding to its width as squared widths, so a star of width ``w`` that may
   grow by the fraction ``b`` may take a guide blur of
   ``w * sqrt((1 + b)**2 - 1)``. A Gaussian of standard deviation ``s`` has a
   full width at half maximum of 2.3548 ``s``, so that blur is turned into a
   guide error by dividing by 2.3548.
2. Each night the setting was used is cut into windows of the exposure
   length, one after the other. The guide error inside each window is
   summarized with the same function the frame-to-guiding join uses
   (`window_statistics`). A window is sharp when its larger axis error is at
   or below the allowed error.
3. The share of sharp windows is found for the exposure the setting used and
   for a ladder of other exposures. The longest exposure whose share reaches
   the target share is the exposure the guiding supports.

Windows tile the spans in which the setting was imaging, so one exposure can
be judged for lengths the user never tried. The guiding record is read by the
caller and passed in; nothing is read or stored here. Missing guide data is
said as a reason, never turned into a number.

The numbers are design estimates (register rows WAY-D78 to WAY-D80, entry
WAY-75).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.analytics.result_quality.guiding_join import (
    FrameWindow,
    join_frames_to_guiding,
)

GUIDING_EXPOSURE_ENTRY = "WAY-75"
"""The register entry behind the rule."""

GAUSSIAN_FWHM_PER_SIGMA = 2.0 * math.sqrt(2.0 * math.log(2.0))
"""The full width at half maximum of a Gaussian, in standard deviations
(about 2.3548). A mathematical fact, not a tuned value."""

STAR_WIDTH_BUDGET_FRACTION = 0.10
"""The most guiding may widen a star: 10%. Squared widths add, so 10% of width
takes a guide blur of 0.46 of the star width. Design estimate, register row
WAY-D78. A star that is 10% wider is about 1 dB less peak signal."""

STAR_WIDTH_QUANTILE = 0.25
"""The star width used as "what the stars look like when guiding is not the
limit": the lower quartile of the setting's frame widths. The median already
includes the guiding blur and would allow too much. Design estimate, register
row WAY-D78."""

TARGET_SHARP_FRACTION = 0.80
"""The share of windows that must stay sharp for an exposure to be called
supported by the guiding: 80%. The rule fires when the exposure the setting
used falls below it. Design estimate, register row WAY-D79."""

MINIMUM_WINDOW_SAMPLES = 3
"""Fewest guide samples a window must hold to be judged. Guide samples come
every 1 to 5 seconds, so three samples is the least that gives an error
spread. Design estimate, register row WAY-D80."""

MINIMUM_WINDOW_COVERAGE = 0.5
"""Least share of a window the samples must cover (`window_statistics`
``coverage_fraction``). A window with a big gap in the log is not judged.
Design estimate, register row WAY-D80."""

MINIMUM_WINDOWS_PER_EXPOSURE = 10
"""Fewest judged windows for an exposure to be given a share. Ten windows
gives a share to about 15 percentage points (binomial, 1 / sqrt(10) at its
widest is 16%). Design estimate, register row WAY-D80."""

EXPOSURE_LADDER_SECONDS: tuple[float, ...] = (
    10.0,
    15.0,
    20.0,
    30.0,
    45.0,
    60.0,
    90.0,
    120.0,
    180.0,
    240.0,
    300.0,
    600.0,
)
"""The exposures tried besides the one the setting used. A familiar ladder of
imaging exposures (steps of about 1.4 to 1.5). Design estimate, register row
WAY-D80."""


MAXIMUM_GAP_WITHIN_IMAGING_SECONDS = 600.0
"""Two frames of one setting closer than this (end to start) are one stretch of
imaging; a longer gap (another target, a meridian flip, a stop) ends the
stretch, so windows are not cut across time that was not imaging with this
setting. Ten minutes is the longest gap a dither, a download and an autofocus
run add up to in the user's Ekos records. Design estimate, register row
WAY-D80."""


def imaging_spans(
    frame_windows: Sequence[tuple[float, float]],
    maximum_gap_seconds: float = MAXIMUM_GAP_WITHIN_IMAGING_SECONDS,
) -> list[tuple[float, float]]:
    """Join the frames of one night into stretches of imaging.

    Parameters
    ----------
    frame_windows : `Sequence` [`tuple` [`float`, `float`]]
        The (start, end) of each frame, in seconds since the Unix epoch, in any
        order.
    maximum_gap_seconds : `float`, optional
        The largest gap between one frame's end and the next frame's start
        that still counts as the same stretch.

    Returns
    -------
    spans : `list` [`tuple` [`float`, `float`]]
        The stretches, earliest first.
    """
    spans: list[tuple[float, float]] = []
    for start, end in sorted(frame_windows):
        if spans and start - spans[-1][1] <= maximum_gap_seconds:
            spans[-1] = (spans[-1][0], max(spans[-1][1], end))
        else:
            spans.append((start, end))
    return spans


class ExposureSharpness(BaseModel):
    """The share of windows that stay sharp at one exposure.

    Parameters
    ----------
    exposure_seconds : `float`
        The window length.
    windows_judged : `int`
        How many windows had enough guide samples to be judged.
    windows_sharp : `int`
        How many of them had a guide error at or below the allowed error.
    sharp_fraction : `float` or `None`
        ``windows_sharp / windows_judged``. ``None`` when fewer than
        `MINIMUM_WINDOWS_PER_EXPOSURE` windows were judged.
    """

    model_config = ConfigDict(populate_by_name=True)

    exposure_seconds: float = Field(gt=0.0, alias="exposureSeconds")
    windows_judged: int = Field(ge=0, alias="windowsJudged")
    windows_sharp: int = Field(ge=0, alias="windowsSharp")
    sharp_fraction: float | None = Field(default=None, ge=0.0, le=1.0, alias="sharpFraction")


class GuidingExposureResult(BaseModel):
    """What the guiding lets a capture setting do.

    Parameters
    ----------
    used_exposure_seconds : `float`
        The exposure the setting used.
    star_width_arcsec : `float`
        The star width the allowed error was worked out from.
    allowed_guide_rms_arcsec : `float`
        The largest guide error, per axis, of a sharp frame.
    ladder : `list` [`ExposureSharpness`]
        The share at the used exposure and at each ladder exposure, shortest
        first.
    used : `ExposureSharpness`
        The entry of the used exposure.
    supported_exposure_seconds : `float` or `None`
        The longest exposure whose share reaches `TARGET_SHARP_FRACTION`.
        ``None`` when none does.
    frames_sharp_fraction : `float` or `None`
        The share of the setting's own frames whose guide error was at or
        below the allowed error. ``None`` when too few frames had guiding.
    nights : `list` [`str`]
        The nights the guide samples came from.
    """

    model_config = ConfigDict(populate_by_name=True)

    used_exposure_seconds: float = Field(alias="usedExposureSeconds")
    star_width_arcsec: float = Field(alias="starWidthArcsec")
    allowed_guide_rms_arcsec: float = Field(alias="allowedGuideRmsArcsec")
    ladder: list[ExposureSharpness]
    used: ExposureSharpness
    supported_exposure_seconds: float | None = Field(default=None, alias="supportedExposureSeconds")
    frames_sharp_fraction: float | None = Field(default=None, alias="framesSharpFraction")
    nights: list[str] = Field(default_factory=list)


class GuidingExposureOutcome(BaseModel):
    """The result, or the reason there is none.

    Parameters
    ----------
    result : `GuidingExposureResult` or `None`
        The result.
    reason : `str`
        Why there is no result. Empty when there is one.
    """

    model_config = ConfigDict(populate_by_name=True)

    result: GuidingExposureResult | None = None
    reason: str = ""


def allowed_guide_rms_arcsec(
    star_width_arcsec: float, budget_fraction: float = STAR_WIDTH_BUDGET_FRACTION
) -> float:
    """Find the largest guide error that keeps a star within its width budget.

    Parameters
    ----------
    star_width_arcsec : `float`
        The star width without a guiding limit, in arcseconds.
    budget_fraction : `float`, optional
        The most the star may widen, as a fraction of its width.

    Returns
    -------
    allowed : `float`
        The largest standard deviation of the guide error on one axis, in
        arcseconds.
    """
    blur_fwhm = star_width_arcsec * math.sqrt((1.0 + budget_fraction) ** 2 - 1.0)
    return blur_fwhm / GAUSSIAN_FWHM_PER_SIGMA


def _is_sharp(statistics_of_window: Mapping[str, Any], allowed_rms_arcsec: float) -> bool | None:
    """Judge one window.

    Parameters
    ----------
    statistics_of_window : `Mapping` [`str`, `Any`]
        The output of `window_statistics`.
    allowed_rms_arcsec : `float`
        The largest sharp error on one axis.

    Returns
    -------
    sharp : `bool` or `None`
        ``None`` when the window has too few samples or too little coverage
        to judge.
    """
    if statistics_of_window.get("guide_samples", 0) < MINIMUM_WINDOW_SAMPLES:
        return None
    coverage = statistics_of_window.get("coverage_fraction")
    if coverage is None or coverage < MINIMUM_WINDOW_COVERAGE:
        return None
    worst_axis = max(statistics_of_window["rms_ra_arcsec"], statistics_of_window["rms_dec_arcsec"])
    return worst_axis <= allowed_rms_arcsec


def sharpness_at_exposure(
    spans: Sequence[tuple[float, float]],
    samples: Sequence[Mapping[str, Any]],
    exposure_seconds: float,
    allowed_rms_arcsec: float,
) -> ExposureSharpness:
    """Find the share of sharp windows of one length.

    Parameters
    ----------
    spans : `Sequence` [`tuple` [`float`, `float`]]
        The (start, end) of each stretch of imaging, in seconds since the
        Unix epoch. Windows tile each stretch from its start; a part shorter
        than one window is dropped.
    samples : `Sequence` [`Mapping`]
        Guide samples with ``timestamp``, ``dra`` and ``ddec`` (arcseconds).
    exposure_seconds : `float`
        The window length.
    allowed_rms_arcsec : `float`
        The largest sharp error on one axis.

    Returns
    -------
    sharpness : `ExposureSharpness`
        The counts, and the share when enough windows were judged.
    """
    windows: list[FrameWindow] = []
    for start, end in spans:
        count = int((end - start) // exposure_seconds)
        windows.extend(
            FrameWindow(
                frame_id=f"{start:.0f}-{index}",
                start_epoch_seconds=start + index * exposure_seconds,
                exposure_seconds=exposure_seconds,
            )
            for index in range(count)
        )
    judgments = []
    ordered = sorted(samples, key=lambda row: row["timestamp"])
    for joined in join_frames_to_guiding(windows, ordered):
        verdict = _is_sharp(joined, allowed_rms_arcsec)
        if verdict is not None:
            judgments.append(verdict)
    sharp = sum(judgments)
    fraction = sharp / len(judgments) if len(judgments) >= MINIMUM_WINDOWS_PER_EXPOSURE else None
    return ExposureSharpness(
        exposureSeconds=exposure_seconds,
        windowsJudged=len(judgments),
        windowsSharp=sharp,
        sharpFraction=fraction,
    )


def assess_guiding_exposure(
    used_exposure_seconds: float,
    star_widths_arcsec: Sequence[float],
    spans_by_night: Mapping[str, Sequence[tuple[float, float]]],
    samples_by_night: Mapping[str, Sequence[Mapping[str, Any]]],
    frame_windows: Sequence[FrameWindow] = (),
    *,
    budget_fraction: float = STAR_WIDTH_BUDGET_FRACTION,
) -> GuidingExposureOutcome:
    """Find the share of sharp windows at each exposure for one setting.

    Parameters
    ----------
    used_exposure_seconds : `float`
        The exposure the setting used.
    star_widths_arcsec : `Sequence` [`float`]
        The effective star widths of the setting's kept frames.
    spans_by_night : `Mapping` [`str`, `Sequence`]
        The (start, end) of each stretch of imaging with this setting, by
        night.
    samples_by_night : `Mapping` [`str`, `Sequence`]
        The measured guide samples of each night.
    frame_windows : `Sequence` [`FrameWindow`], optional
        The setting's own frames, to find the share of them that was sharp.
    budget_fraction : `float`, optional
        The most guiding may widen a star.

    Returns
    -------
    outcome : `GuidingExposureOutcome`
        The result, or the reason it cannot be made.
    """
    if used_exposure_seconds <= 0.0:
        return GuidingExposureOutcome(reason="The setting has no exposure time.")
    if len(star_widths_arcsec) < MINIMUM_WINDOWS_PER_EXPOSURE:
        return GuidingExposureOutcome(
            reason=(
                f"Only {len(star_widths_arcsec)} frames have a star width; the rule needs "
                f"{MINIMUM_WINDOWS_PER_EXPOSURE} to set the allowed guide error. "
                "Measure the target's frames (QualityRecords.measure_frames)."
            )
        )
    ranked_widths = sorted(star_widths_arcsec)
    width = float(ranked_widths[int(STAR_WIDTH_QUANTILE * (len(ranked_widths) - 1))])
    if width <= 0.0:
        return GuidingExposureOutcome(reason="The star widths are not positive, so no allowed error exists.")
    nights = [night for night, samples in samples_by_night.items() if samples and spans_by_night.get(night)]
    if not nights:
        return GuidingExposureOutcome(
            reason=(
                "No guide samples were stored for the nights of this setting. Copy the guide logs from the "
                "telescope computer (sync logs), then ask again."
            )
        )
    allowed = allowed_guide_rms_arcsec(width, budget_fraction)
    spans = [span for night in nights for span in spans_by_night[night]]
    samples = [sample for night in nights for sample in samples_by_night[night]]
    exposures = sorted({*EXPOSURE_LADDER_SECONDS, used_exposure_seconds})
    ladder = [sharpness_at_exposure(spans, samples, exposure, allowed) for exposure in exposures]
    used = next(item for item in ladder if item.exposure_seconds == used_exposure_seconds)
    if used.sharp_fraction is None:
        return GuidingExposureOutcome(
            reason=(
                f"Only {used.windows_judged} windows of {used_exposure_seconds:g} s had enough guide samples "
                f"(at least {MINIMUM_WINDOWS_PER_EXPOSURE} are needed). The guide log may not cover the "
                "imaging time; sync the logs."
            )
        )
    supported = [
        item.exposure_seconds
        for item in ladder
        if item.sharp_fraction is not None and item.sharp_fraction >= TARGET_SHARP_FRACTION
    ]
    frames_fraction = None
    if frame_windows:
        verdicts = [
            _is_sharp(row, allowed)
            for row in join_frames_to_guiding(
                frame_windows, [s for sample_list in samples_by_night.values() for s in sample_list]
            )
        ]
        judged = [verdict for verdict in verdicts if verdict is not None]
        if len(judged) >= MINIMUM_WINDOWS_PER_EXPOSURE:
            frames_fraction = sum(judged) / len(judged)
    return GuidingExposureOutcome(
        result=GuidingExposureResult(
            usedExposureSeconds=used_exposure_seconds,
            starWidthArcsec=width,
            allowedGuideRmsArcsec=allowed,
            ladder=ladder,
            used=used,
            supportedExposureSeconds=max(supported) if supported else None,
            framesSharpFraction=frames_fraction,
            nights=sorted(nights),
        )
    )


__all__ = [
    "EXPOSURE_LADDER_SECONDS",
    "GAUSSIAN_FWHM_PER_SIGMA",
    "GUIDING_EXPOSURE_ENTRY",
    "MAXIMUM_GAP_WITHIN_IMAGING_SECONDS",
    "MINIMUM_WINDOWS_PER_EXPOSURE",
    "MINIMUM_WINDOW_COVERAGE",
    "MINIMUM_WINDOW_SAMPLES",
    "STAR_WIDTH_BUDGET_FRACTION",
    "STAR_WIDTH_QUANTILE",
    "TARGET_SHARP_FRACTION",
    "ExposureSharpness",
    "GuidingExposureOutcome",
    "GuidingExposureResult",
    "allowed_guide_rms_arcsec",
    "assess_guiding_exposure",
    "imaging_spans",
    "sharpness_at_exposure",
]
