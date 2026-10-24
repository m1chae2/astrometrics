"""Purpose: Find how star width grows with focuser temperature.

Description: A telescope's focus moves as it cools through the night. Right
after an autofocus run the stars are as sharp as the night allows. As the
focuser temperature changes, the focus point moves away, and a defocused star
widens. This module measures that rate from the star widths and focuser
temperatures of the frames, and says how far the temperature may change
before the stars are too wide.

The model, for the frames taken after one autofocus run, is

    width^2 = best_width^2 + slope^2 * (T - T_start)^2 + seeing term

where ``T_start`` is the focuser temperature of the first frame after the
autofocus, ``slope`` is how many pixels of width one degree of focuser
temperature adds once the star is well out of focus, and the seeing term
follows the airmass (seeing width grows about as airmass to the power 0.6, so
its square grows as airmass to the power 1.2). A defocus blur adds to the
sharp star in quadrature, which is why the square of the width, not the width,
is fitted. Each autofocus run starts a new segment with its own best width;
all segments share one slope. Frames before the first autofocus run are not
used, because their best focus is not known.

The allowed width increase comes from an efficiency budget. For a faint star
in a sky-limited frame, the time needed to reach a depth grows as the square
of the star width, so an efficiency of at least 0.9 allows the width to grow
by a factor of 0.9 to the power -0.5, about 5%. The refocus interval is the
temperature change at which the width reaches that limit. The interval is
compared with the error of the width, and its own tolerance (from the error of
the slope) is stated beside it.

The caller supplies the autofocus run times, which it reads from the stored
Ekos session records. Nothing here reads a file or the database.

Register entries: WAY-D09, WAY-D10 and WAY-D11 list the design estimates
below, with a way to measure each on your own data.
"""

import bisect
import math
import statistics
from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from astrometricslib import CaptureSetting, MeasurementReference
from wayfindinglib.analytics.result_quality.rules import rule_by_name
from wayfindinglib.models.quality.statements import (
    ExpectedEffect,
    ProposedAction,
    StatementKind,
    TargetStatement,
)

# The fewest frames a segment (the frames after one autofocus run) needs to
# join the fit. With fewer, the best width and the slope cannot be told
# apart from noise. Design estimate, register entry WAY-D09.
MINIMUM_FRAMES_PER_SEGMENT = 5

# The fewest frames, all segments together, the fit needs. A fit with one
# shared slope, one best width per segment and an airmass term has as many as
# a dozen unknowns, so 20 frames leave a margin. Design estimate, register
# entry WAY-D09.
MINIMUM_FRAMES_TOTAL = 20

# The least change of focuser temperature, in degrees Celsius, that some
# frame must show from the start of its segment. A change smaller than this is
# lost in the width noise. It is about 5 times the 0.2 degree Celsius step of
# a typical focuser temperature probe. Design estimate, register entry WAY-D09.
MINIMUM_TEMPERATURE_CHANGE_DEGC = 1.0

# The lowest efficiency the seeing factor may fall to before a refocus. Batch 2
# (efficiency) names 0.9 as the level at which a factor costs under 10% of the
# time. Design estimate, register entry WAY-D10.
MINIMUM_SEEING_EFFICIENCY = 0.9

# For a faint star in a sky-limited frame, the time to reach a depth grows as
# the square of the star width, so efficiency is (best width / width) squared.
# The width may then grow by this factor before the efficiency falls to
# `MINIMUM_SEEING_EFFICIENCY`: 0.9 to the power -0.5, about 1.054.
ALLOWED_WIDTH_RATIO = MINIMUM_SEEING_EFFICIENCY**-0.5

# Seeing width grows about as airmass to the power 0.6 (the Kolmogorov
# expectation for a thin atmosphere), so the square of the width grows as
# airmass to the power 1.2. Design estimate, register entry WAY-D11.
SEEING_SQUARED_AIRMASS_EXPONENT = 1.2

# The number of resamples of the frames used for the error of the slope, and
# the seed that makes the error repeat. 200 resamples give the error to about
# 5% of itself. Design estimate, register entry WAY-D10.
BOOTSTRAP_RESAMPLES = 200
BOOTSTRAP_SEED = 20261024

# A slope counts as found only when it is this many times its error. Two
# standard errors is the usual level for "not noise". Design estimate,
# register entry WAY-D10.
SIGNIFICANCE_SIGMAS = 2.0

# The share of bootstrap resamples that must give a usable fit. With
# fewer, the error of the slope cannot be trusted. Design estimate,
# register entry WAY-D10.
MINIMUM_VALID_BOOTSTRAP_SHARE = 0.8

FOCUS_DRIFT_RULE_NAME = "focus_drift"


class FocusFrameSample(BaseModel):
    """The star width and focuser temperature of one frame.

    Parameters
    ----------
    captured_at : `datetime`
        When the frame was taken (with a time zone, like the autofocus times).
    focuser_temperature_degc : `float`
        The focuser temperature, in degrees Celsius.
    star_width_px : `float`
        The star width of the frame, in pixels (a pixel-integrated Moffat
        width, ``frame.star_width_moffat_px``).
    star_width_uncertainty_px : `float` or `None`
        The error of the width, in pixels, when known.
    airmass : `float` or `None`
        The airmass of the frame. The fit uses airmass only when every frame
        has it.
    evidence : `MeasurementReference` or `None`
        The stored row of the width, cited by the statement.
    """

    model_config = ConfigDict(populate_by_name=True)

    captured_at: datetime = Field(alias="capturedAt")
    focuser_temperature_degc: float = Field(alias="focuserTemperatureDegc")
    star_width_px: float = Field(gt=0.0, alias="starWidthPx")
    star_width_uncertainty_px: float | None = Field(default=None, ge=0.0, alias="starWidthUncertaintyPx")
    airmass: float | None = Field(default=None, ge=1.0)
    evidence: MeasurementReference | None = None


class FocusDriftStatus(StrEnum):
    """The outcome of a focus drift fit.

    `DRIFT_FOUND` means the width grows with temperature by more than its
    error. `NO_DRIFT_DETECTED` means the data are enough but show no drift
    beyond the error. `INSUFFICIENT_DATA` means the fit could not be made.
    """

    DRIFT_FOUND = "drift_found"
    NO_DRIFT_DETECTED = "no_drift_detected"
    INSUFFICIENT_DATA = "insufficient_data"


class FocusDriftResult(BaseModel):
    """What the focus drift fit found.

    Parameters
    ----------
    status : `FocusDriftStatus`
        The outcome.
    reason : `str`
        Why, in plain words. For missing data, what is missing and
        how to get it.
    slope_px_per_degc : `float` or `None`
        Pixels of width added per degree Celsius, once the star is
        well out of focus.
    slope_error_px_per_degc : `float` or `None`
        The bootstrap error of the slope.
    best_focus_width_px : `float` or `None`
        The median best width over the segments, in pixels.
    allowed_width_increase_fraction : `float`
        The width increase the efficiency budget allows (about 0.054).
    allowed_width_increase_px : `float` or `None`
        The same in pixels.
    width_error_px : `float` or `None`
        The median error of one frame's width, in pixels, when the
        samples had it.
    interval_resolvable : `bool` or `None`
        Whether the allowed increase is larger than the error of one width.
        ``None`` when no width errors were given.
    refocus_interval_degc : `float` or `None`
        The temperature change at which the width reaches its limit.
    interval_tolerance_degc : `float` or `None`
        The error of the interval, from the error of the slope.
    frames_used : `int`
        Frames in the fit.
    segments_used : `int`
        Segments in the fit.
    frames_dropped : `int`
        Frames left out, for being before the first autofocus run or in a
        segment that was too short.
    temperature_change_degc : `float` or `None`
        The largest change of focuser temperature from the start of a segment.
    airmass_used : `bool`
        Whether the airmass term was in the fit.
    evidence : `list` [`MeasurementReference`]
        The stored rows of the frames used.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: FocusDriftStatus
    reason: str
    slope_px_per_degc: float | None = Field(default=None, alias="slopePxPerDegc")
    slope_error_px_per_degc: float | None = Field(default=None, alias="slopeErrorPxPerDegc")
    best_focus_width_px: float | None = Field(default=None, alias="bestFocusWidthPx")
    allowed_width_increase_fraction: float = Field(alias="allowedWidthIncreaseFraction")
    allowed_width_increase_px: float | None = Field(default=None, alias="allowedWidthIncreasePx")
    width_error_px: float | None = Field(default=None, alias="widthErrorPx")
    interval_resolvable: bool | None = Field(default=None, alias="intervalResolvable")
    refocus_interval_degc: float | None = Field(default=None, alias="refocusIntervalDegc")
    interval_tolerance_degc: float | None = Field(default=None, alias="intervalToleranceDegc")
    frames_used: int = Field(default=0, alias="framesUsed")
    segments_used: int = Field(default=0, alias="segmentsUsed")
    frames_dropped: int = Field(default=0, alias="framesDropped")
    temperature_change_degc: float | None = Field(default=None, alias="temperatureChangeDegc")
    airmass_used: bool = Field(default=False, alias="airmassUsed")
    evidence: list[MeasurementReference] = Field(default_factory=list)


def _insufficient(reason: str, dropped: int = 0) -> FocusDriftResult:
    """Build the result for a fit that could not be made.

    Parameters
    ----------
    reason : `str`
        What is missing and how to get it.
    dropped : `int`, optional
        Frames left out so far.

    Returns
    -------
    result : `FocusDriftResult`
        A result with status ``insufficient_data``.
    """
    return FocusDriftResult(
        status=FocusDriftStatus.INSUFFICIENT_DATA,
        reason=reason,
        allowed_width_increase_fraction=ALLOWED_WIDTH_RATIO - 1.0,
        frames_dropped=dropped,
    )


def _fit_squared_width(
    segment_index: np.ndarray,
    segment_count: int,
    temperature_term: np.ndarray,
    airmass_term: np.ndarray | None,
    squared_width: np.ndarray,
) -> tuple[np.ndarray, int] | None:
    """Solve the least-squares fit of the squared widths.

    Parameters
    ----------
    segment_index : `numpy.ndarray`
        The segment number of each frame.
    segment_count : `int`
        The number of segments.
    temperature_term : `numpy.ndarray`
        The squared temperature change from the start of the frame's segment.
    airmass_term : `numpy.ndarray` or `None`
        The airmass term relative to the start of the segment, or ``None``.
    squared_width : `numpy.ndarray`
        The squared width of each frame.

    Returns
    -------
    solution : `tuple` or `None`
        The coefficients (one best squared width for each segment, then the
        squared slope, then the airmass coefficient if used) and the matrix
        rank, or ``None`` when the system has no usable solution.
    """
    columns = [(segment_index == segment).astype(float) for segment in range(segment_count)]
    columns.append(temperature_term)
    if airmass_term is not None:
        columns.append(airmass_term)
    design = np.column_stack(columns)
    coefficients, _, rank, _ = np.linalg.lstsq(design, squared_width, rcond=None)
    if not np.all(np.isfinite(coefficients)):
        return None
    return coefficients, int(rank)


def fit_focus_drift(
    samples: Sequence[FocusFrameSample], autofocus_times: Sequence[datetime]
) -> FocusDriftResult:
    """Fit star width against focuser temperature and find a refocus interval.

    Parameters
    ----------
    samples : `Sequence` [`FocusFrameSample`]
        The frames, in any order.
    autofocus_times : `Sequence` [`datetime`]
        When each autofocus run finished, read from the stored Ekos session
        records (``EkosAutofocusRun``).

    Returns
    -------
    result : `FocusDriftResult`
        The slope, the allowed width increase and the refocus interval, or
        the reason they could not be found.
    """
    if not autofocus_times:
        return _insufficient(
            "No autofocus run times were given. The fit needs the time of each autofocus run to know "
            "where best focus was; read them from the stored Ekos session records."
        )
    run_times = sorted(autofocus_times)
    segments: dict[int, list[FocusFrameSample]] = {}
    dropped = 0
    for sample in sorted(samples, key=lambda item: item.captured_at):
        run_number = bisect.bisect_right(run_times, sample.captured_at) - 1
        if run_number < 0:
            dropped += 1
            continue
        segments.setdefault(run_number, []).append(sample)
    kept = {
        number: frames for number, frames in segments.items() if len(frames) >= MINIMUM_FRAMES_PER_SEGMENT
    }
    dropped += sum(len(frames) for number, frames in segments.items() if number not in kept)
    frame_total = sum(len(frames) for frames in kept.values())
    if frame_total < MINIMUM_FRAMES_TOTAL:
        return _insufficient(
            f"Only {frame_total} frames follow an autofocus run in segments of at least "
            f"{MINIMUM_FRAMES_PER_SEGMENT} frames; the fit needs {MINIMUM_FRAMES_TOTAL}. "
            "Measure the star width of more frames and record the autofocus runs.",
            dropped,
        )
    airmass_used = all(frame.airmass is not None for frames in kept.values() for frame in frames)

    segment_index: list[int] = []
    temperature_term: list[float] = []
    airmass_term: list[float] = []
    squared_width: list[float] = []
    largest_change = 0.0
    used_samples: list[FocusFrameSample] = []
    for position, frames in enumerate(kept.values()):
        start = frames[0]
        for frame in frames:
            change = frame.focuser_temperature_degc - start.focuser_temperature_degc
            largest_change = max(largest_change, abs(change))
            segment_index.append(position)
            temperature_term.append(change**2)
            if airmass_used:
                airmass_term.append(
                    frame.airmass**SEEING_SQUARED_AIRMASS_EXPONENT  # type: ignore[operator]
                    - start.airmass**SEEING_SQUARED_AIRMASS_EXPONENT  # type: ignore[operator]
                )
            squared_width.append(frame.star_width_px**2)
            used_samples.append(frame)
    if largest_change < MINIMUM_TEMPERATURE_CHANGE_DEGC:
        return _insufficient(
            f"The focuser temperature changed by at most {largest_change:.2f} degrees C after an autofocus "
            f"run; the fit needs {MINIMUM_TEMPERATURE_CHANGE_DEGC:g}. A longer stretch of frames after a "
            "temperature change is needed.",
            dropped,
        )

    indices = np.array(segment_index)
    temperature = np.array(temperature_term)
    airmass = np.array(airmass_term) if airmass_used else None
    widths = np.array(squared_width)
    segment_count = len(kept)
    solved = _fit_squared_width(indices, segment_count, temperature, airmass, widths)
    column_count = segment_count + 1 + (1 if airmass_used else 0)
    if solved is None or solved[1] < column_count:
        return _insufficient(
            "The frames do not separate the best width, the temperature slope and the airmass term "
            "(the system is degenerate). More frames over a wider range of temperature are needed.",
            dropped,
        )
    coefficients = solved[0]
    squared_slope = float(coefficients[segment_count])
    slope = math.sqrt(max(squared_slope, 0.0))

    generator = np.random.default_rng(BOOTSTRAP_SEED)
    boot_slopes: list[float] = []
    for _ in range(BOOTSTRAP_RESAMPLES):
        pick = generator.integers(0, len(widths), size=len(widths))
        boot = _fit_squared_width(
            indices[pick],
            segment_count,
            temperature[pick],
            airmass[pick] if airmass is not None else None,
            widths[pick],
        )
        if boot is None or boot[1] < column_count:
            continue
        boot_slopes.append(math.sqrt(max(float(boot[0][segment_count]), 0.0)))
    if len(boot_slopes) < MINIMUM_VALID_BOOTSTRAP_SHARE * BOOTSTRAP_RESAMPLES:
        return _insufficient(
            f"Only {len(boot_slopes)} of {BOOTSTRAP_RESAMPLES} resamples of the frames gave a usable fit, "
            "so the error of the slope cannot be trusted. More frames are needed.",
            dropped,
        )
    slope_error = float(np.std(boot_slopes, ddof=1))

    best_squared = [float(value) for value in coefficients[:segment_count] if value > 0.0]
    best_width = math.sqrt(statistics.median(best_squared)) if best_squared else None
    evidence = [frame.evidence for frame in used_samples if frame.evidence is not None]
    errors = [
        frame.star_width_uncertainty_px
        for frame in used_samples
        if frame.star_width_uncertainty_px is not None
    ]
    width_error = float(statistics.median(errors)) if errors else None
    allowed_fraction = ALLOWED_WIDTH_RATIO - 1.0
    allowed_px = best_width * allowed_fraction if best_width is not None else None
    resolvable = None if (width_error is None or allowed_px is None) else allowed_px > width_error
    common = {
        "slope_px_per_degc": slope,
        "slope_error_px_per_degc": slope_error,
        "best_focus_width_px": best_width,
        "allowed_width_increase_fraction": allowed_fraction,
        "allowed_width_increase_px": allowed_px,
        "width_error_px": width_error,
        "interval_resolvable": resolvable,
        "frames_used": len(used_samples),
        "segments_used": segment_count,
        "frames_dropped": dropped,
        "temperature_change_degc": largest_change,
        "airmass_used": airmass_used,
        "evidence": evidence,
    }
    if squared_slope <= 0.0 or slope < SIGNIFICANCE_SIGMAS * slope_error or best_width is None:
        return FocusDriftResult(
            status=FocusDriftStatus.NO_DRIFT_DETECTED,
            reason=(
                f"The width grows by {slope:.2f} px per degree C, which is less than "
                f"{SIGNIFICANCE_SIGMAS:g} times its error of {slope_error:.2f}: no drift beyond the noise."
            ),
            **common,
        )
    # The width reaches the limit when slope^2 * dT^2 = (ratio^2 - 1) * best^2.
    interval = math.sqrt(ALLOWED_WIDTH_RATIO**2 - 1.0) * best_width / slope
    tolerance = interval * slope_error / slope
    return FocusDriftResult(
        status=FocusDriftStatus.DRIFT_FOUND,
        reason=(
            f"Stars widen by {slope:.2f} px per degree C (error {slope_error:.2f}) beyond a best width "
            f"of {best_width:.2f} px, from {len(used_samples)} frames in {segment_count} segments."
        ),
        refocus_interval_degc=interval,
        interval_tolerance_degc=tolerance,
        **common,
    )


def focus_drift_statement(
    result: FocusDriftResult, *, capture_setting: CaptureSetting | None = None
) -> TargetStatement | None:
    """Turn a focus drift result into a statement.

    Parameters
    ----------
    result : `FocusDriftResult`
        The result of `fit_focus_drift`.
    capture_setting : `CaptureSetting`, optional
        The setting the frames were taken with.

    Returns
    -------
    statement : `TargetStatement` or `None`
        A recommendation with the refocus interval and its tolerance, an
        ``insufficient_data`` statement when the fit could not be made or the
        interval is smaller than the width error, or ``None`` when the data
        show no drift (there is nothing to say).

    Raises
    ------
    ValueError
        If the result says a drift was found but holds no slope, interval or
        tolerance.
    """
    rule = rule_by_name(FOCUS_DRIFT_RULE_NAME)
    if result.status == FocusDriftStatus.NO_DRIFT_DETECTED:
        return None
    if result.status == FocusDriftStatus.INSUFFICIENT_DATA:
        return TargetStatement(
            rule=rule.name,
            kind=StatementKind.INSUFFICIENT_DATA,
            capture_setting=capture_setting,
            message="Cannot find how the focus drifts with temperature.",
            reason=result.reason,
            confidence=rule.confidence,
        )
    if result.interval_resolvable is False:
        return TargetStatement(
            rule=rule.name,
            kind=StatementKind.INSUFFICIENT_DATA,
            capture_setting=capture_setting,
            message=(
                "Cannot tell when to refocus: the allowed change in width is smaller than the width error."
            ),
            reason=(
                f"The allowed increase is {result.allowed_width_increase_px:.2f} px, but one frame's width "
                f"has an error of {result.width_error_px:.2f} px, so a single width cannot show that the "
                "limit was reached. Measure the width from more stars or use a longer exposure."
            ),
            evidence=result.evidence,
            confidence=rule.confidence,
        )
    if (
        result.refocus_interval_degc is None
        or result.interval_tolerance_degc is None
        or result.slope_px_per_degc is None
    ):
        raise ValueError("A drift_found result must hold a slope, an interval and a tolerance.")
    message = rule.wording.format(
        slope=result.slope_px_per_degc,
        allowed_percent=result.allowed_width_increase_fraction * 100.0,
        interval=result.refocus_interval_degc,
        tolerance=result.interval_tolerance_degc,
    )
    return TargetStatement(
        rule=rule.name,
        kind=rule.statement_kind,
        action_class=rule.action_class,
        capture_setting=capture_setting,
        message=message,
        reason=(
            f"{result.reason} The limit is an efficiency of {MINIMUM_SEEING_EFFICIENCY:g} for a faint point "
            "source. The interval is the allowed width increase divided by the measured slope, and its "
            "tolerance comes from the error of the slope."
        ),
        proposed_action=ProposedAction(refocus_every_degc=result.refocus_interval_degc),
        expected_effect=ExpectedEffect(
            description=(
                f"Keeps the star width within {result.allowed_width_increase_fraction:.0%} of best focus."
            )
        ),
        evidence=result.evidence,
        confidence=rule.confidence,
    )
