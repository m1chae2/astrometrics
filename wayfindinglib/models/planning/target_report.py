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


class ScienceOutcomeSummary(BaseModel):
    """Notable science results already made from a target's data.

    Planning uses these to raise the priority of a target whose data has
    already produced something worth following up.

    Parameters
    ----------
    variable_star_candidate_count : `int`
        Variable star candidates the photometry run found. Read from the
        stored measurement ``photometry.variable_candidate_count``.
    asteroid_candidate_count : `int`
        Asteroid candidates the detection run found.
    confirmed_asteroid_candidate_count : `int`
        Asteroid candidates that match a known orbit.
    flagged_pipelines : `list` [`str`]
        Names of the pipelines whose own quality summary is flagged.
    """

    model_config = ConfigDict(populate_by_name=True)

    variable_star_candidate_count: int = Field(default=0, ge=0, alias="variableStarCandidateCount")
    asteroid_candidate_count: int = Field(default=0, ge=0, alias="asteroidCandidateCount")
    confirmed_asteroid_candidate_count: int = Field(default=0, ge=0, alias="confirmedAsteroidCandidateCount")
    flagged_pipelines: list[str] = Field(default_factory=list, alias="flaggedPipelines")


class ContextReading(BaseModel):
    """One context measurement of the target, shown beside the statements.

    A context metric explains other results (the sensor temperature, how a
    stack was made, how many stars a solve matched). It drives no rule, so
    the report shows it here instead.

    Parameters
    ----------
    metric : `str`
        The registered metric name.
    level : `str`
        ``"frame"``, ``"stack"`` or ``"run"``.
    value : `float` or `None`
        The value of the newest row of this metric for the capture setting.
        ``None`` when that row was not measured.
    unit : `str`
        The unit of the metric.
    capture_setting_id : `int` or `None`
        The capture setting the value belongs to. ``None`` when the rows
        belong to no single setting.
    row_count : `int`
        How many stored rows of this metric and setting there are.
    row_id : `int`
        The row the value comes from, so the reading is also a reference.
    """

    model_config = ConfigDict(populate_by_name=True)

    metric: str
    level: str
    value: float | None = None
    unit: str = ""
    capture_setting_id: int | None = Field(default=None, alias="captureSettingId")
    row_count: int = Field(default=1, ge=1, alias="rowCount")
    row_id: int | None = Field(default=None, alias="rowId")


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
    notes : `list` [`str`]
        Remarks on how the report was made: which efficiency reference each
        setting uses (and the other one), where the observing site came from,
        and whether the set-aside frames are recorded.
    forecast_checks : `list` [`ForecastCheck`]
        How earlier forecasts compare with what was measured.
    science_outcomes : `ScienceOutcomeSummary`
        Science results already made from the target's data.
    context : `list` [`ContextReading`]
        The newest value of each context metric the target has, for each
        capture setting. Empty when the report would not fit the reply
        budget; see ``context_omitted_reason``.
    context_omitted_reason : `str` or `None`
        Why ``context`` was left empty. ``None`` when it was not.
    generated_at : `datetime`
        When the report was computed.
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    capture_settings: list[CaptureSettingSummary] = Field(default_factory=list, alias="captureSettings")
    statements: list[TargetStatement] = Field(default_factory=list)
    needs_attention: NeedsAttention = Field(alias="needsAttention")
    starting_point: StartingPoint | None = Field(default=None, alias="startingPoint")
    notes: list[str] = Field(default_factory=list)
    forecast_checks: list[ForecastCheck] = Field(default_factory=list, alias="forecastChecks")
    science_outcomes: ScienceOutcomeSummary = Field(
        default_factory=ScienceOutcomeSummary, alias="scienceOutcomes"
    )
    context: list[ContextReading] = Field(default_factory=list)
    context_omitted_reason: str | None = Field(default=None, alias="contextOmittedReason")
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC), alias="generatedAt")
