"""Purpose: The target dataset report and its parts.

Description: What Planning tells the user about one target's data: a summary
for each capture setting, the statements (warnings, recommendations, limits
and missing data) in priority order, how many warnings need attention, a
starting point when the target has no frames yet, and whether the earlier
forecasts of "take N more" came true.

The report is recomputed on each read and never stored
(`QUALITY_METRICS_PLAN.md` Section 6.2). The shared statement shapes live in
`wayfindinglib.models.quality`.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from astrometricslib import CaptureSetting, MeasurementReference
from wayfindinglib.models.quality.capture_summary import CaptureSettingSummary
from wayfindinglib.models.quality.statements import Confidence, NeedsAttention, TargetStatement


class ForecastOutcome(StrEnum):
    """How a forecast compares with what happened."""

    AS_FORECAST = "as_forecast"
    SHORT_KNOWN_REASON = "short_known_reason"
    SHORT_UNKNOWN_REASON = "short_unknown_reason"
    NOT_CHECKABLE = "not_checkable"


class ForecastedChange(BaseModel):
    """One predicted or measured change in stack noise, with its tolerance.

    Parameters
    ----------
    noise_change_fraction : `float` or `None`
        The change in noise as a fraction. A fall of 29% is -0.29.
        ``None`` when it could not be found.
    tolerance : `float` or `None`
        How far the number may be off and still agree, as a fraction. It
        comes from the uncertainty of the aperture noise.
    """

    model_config = ConfigDict(populate_by_name=True)

    noise_change_fraction: float | None = Field(default=None, alias="noiseChangeFraction")
    tolerance: float | None = Field(default=None, ge=0.0)


class ForecastCheck(BaseModel):
    """One check of a forecast against the stack that followed it.

    Parameters
    ----------
    stack_run_id : `str` or `None`
        The stack run the measured change comes from.
    forecast_when_planned : `ForecastedChange`
        The forecast made when the session was planned.
    forecast_given_frames_taken : `ForecastedChange`
        The forecast again, using the frames actually taken.
    measured : `ForecastedChange`
        The change that was measured.
    weighting_mode : `str`
        The frame weighting the stack run used, such as ``"equal"``.
    paired : `bool`
        Whether the tolerance is paired (the two stacks measured in the same
        apertures) or unpaired.
    outcome : `ForecastOutcome`
        Whether the gain arrived as forecast.
    reason : `str`
        Why it fell short or could not be checked. Required unless the gain
        arrived as forecast.
    evidence : `list` [`MeasurementReference`]
        The measurements the check used.
    """

    model_config = ConfigDict(populate_by_name=True)

    stack_run_id: str | None = Field(default=None, alias="stackRunId")
    forecast_when_planned: ForecastedChange = Field(alias="forecastWhenPlanned")
    forecast_given_frames_taken: ForecastedChange = Field(alias="forecastGivenFramesTaken")
    measured: ForecastedChange
    weighting_mode: str = Field(alias="weightingMode")
    paired: bool
    outcome: ForecastOutcome
    reason: str = ""
    evidence: list[MeasurementReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_reason_unless_as_forecast(self) -> Self:
        """Check that every outcome but ``as_forecast`` gives a reason.

        Returns
        -------
        check : `ForecastCheck`
            This check, unchanged.

        Raises
        ------
        ValueError
            If the outcome is not ``as_forecast`` and the reason is empty.
        """
        if self.outcome != ForecastOutcome.AS_FORECAST and not self.reason.strip():
            raise ValueError("A forecast check that did not come out as forecast must give a reason.")
        return self


class StartingPoint(BaseModel):
    """A first capture setting for a target with no frames yet.

    It is always a starting point, never a result.

    Parameters
    ----------
    capture_setting : `CaptureSetting` or `None`
        The setting the numbers are for, when it is known.
    exposure_seconds : `float` or `None`
        Exposure long enough that sky noise dominates read noise, in
        seconds. ``None`` when it cannot be computed.
    exposure_reason : `str`
        How it was found, or why it was not computed.
    clipping_cap_seconds : `float` or `None`
        The longest exposure that keeps the target and bright stars from
        clipping, in seconds. ``None`` is "not computed".
    clipping_cap_reason : `str`
        How it was found, or why it was not computed.
    confidence : `Confidence`
        How well the method is backed.
    """

    model_config = ConfigDict(populate_by_name=True)

    capture_setting: CaptureSetting | None = Field(default=None, alias="captureSetting")
    exposure_seconds: float | None = Field(default=None, gt=0.0, alias="exposureSeconds")
    exposure_reason: str = Field(min_length=1, alias="exposureReason")
    clipping_cap_seconds: float | None = Field(default=None, gt=0.0, alias="clippingCapSeconds")
    clipping_cap_reason: str = Field(min_length=1, alias="clippingCapReason")
    confidence: Confidence


class TargetDatasetReport(BaseModel):
    """What the library says about one target's data.

    Parameters
    ----------
    target_id : `str`
        The target.
    capture_settings : `list` [`CaptureSettingSummary`]
        One summary for each capture setting.
    statements : `list` [`TargetStatement`]
        The statements, already in priority order.
    needs_attention : `NeedsAttention`
        The count and worst severity of the warnings.
    starting_point : `StartingPoint` or `None`
        A first setting, for a target with no frames.
    forecast_checks : `list` [`ForecastCheck`]
        How earlier forecasts compare with what was measured.
    generated_at : `datetime`
        When the report was computed.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    capture_settings: list[CaptureSettingSummary] = Field(default_factory=list, alias="captureSettings")
    statements: list[TargetStatement] = Field(default_factory=list)
    needs_attention: NeedsAttention = Field(alias="needsAttention")
    starting_point: StartingPoint | None = Field(default=None, alias="startingPoint")
    forecast_checks: list[ForecastCheck] = Field(default_factory=list, alias="forecastChecks")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
