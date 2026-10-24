"""Purpose: Tests for the target dataset report models.

Description: Checks that `ForecastCheck` demands a reason unless the gain
arrived as forecast, that `StartingPoint` states why a value was not
computed, and that `TargetDatasetReport` survives a JSON round trip.
"""

import pytest
from pydantic import ValidationError

from wayfindinglib.models.planning.target_report import (
    ForecastCheck,
    ForecastedChange,
    ForecastOutcome,
    StartingPoint,
    TargetDatasetReport,
)
from wayfindinglib.models.quality.statements import Confidence, NeedsAttention


def _check(**overrides: object) -> ForecastCheck:
    """Build a check that came out as forecast.

    Returns
    -------
    check : `ForecastCheck`
        A valid check.
    """
    fields: dict = {
        "forecast_when_planned": ForecastedChange(noise_change_fraction=-0.29, tolerance=0.05),
        "forecast_given_frames_taken": ForecastedChange(noise_change_fraction=-0.27, tolerance=0.05),
        "measured": ForecastedChange(noise_change_fraction=-0.26, tolerance=0.05),
        "weighting_mode": "equal",
        "paired": False,
        "outcome": ForecastOutcome.AS_FORECAST,
    }
    fields.update(overrides)
    return ForecastCheck(**fields)


def test_forecast_check_needs_a_reason_unless_as_forecast() -> None:
    """Falling short, or not being checkable, must be explained."""
    assert _check().reason == ""
    for outcome in (
        ForecastOutcome.SHORT_KNOWN_REASON,
        ForecastOutcome.SHORT_UNKNOWN_REASON,
        ForecastOutcome.NOT_CHECKABLE,
    ):
        with pytest.raises(ValidationError, match="reason"):
            _check(outcome=outcome)
        assert _check(outcome=outcome, reason="The new frames were moonlit.").outcome == outcome


def test_forecast_tolerance_cannot_be_negative() -> None:
    """A tolerance is a size, so it is not below zero."""
    with pytest.raises(ValidationError):
        ForecastedChange(noise_change_fraction=-0.1, tolerance=-0.01)


def test_starting_point_states_why_a_value_is_not_computed() -> None:
    """The clipping cap may be missing, but never without a reason."""
    point = StartingPoint(
        exposure_seconds=90.0,
        exposure_reason="Sky noise is nine times the read noise squared.",
        clipping_cap_reason="Not computed: no usable zero point exists.",
        confidence=Confidence.DESIGN_ESTIMATE,
    )
    assert point.clipping_cap_seconds is None
    with pytest.raises(ValidationError):
        StartingPoint(exposure_reason="", clipping_cap_reason="x", confidence=Confidence.MEASURED)


def test_target_dataset_report_round_trips_through_json() -> None:
    """The report dumps with camelCase aliases and loads back equal."""
    report = TargetDatasetReport(
        target_id="M 81",
        needs_attention=NeedsAttention(warning_count=0),
        forecast_checks=[_check()],
    )
    dumped = report.model_dump(by_alias=True, mode="json")
    assert dumped["targetId"] == "M 81"
    assert dumped["forecastChecks"][0]["forecastGivenFramesTaken"]["noiseChangeFraction"] == pytest.approx(
        -0.27
    )
    assert TargetDatasetReport.model_validate(dumped) == report
