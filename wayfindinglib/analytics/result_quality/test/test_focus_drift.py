"""Purpose: Tests for the focus drift fit and its statement.

Description: Builds made-up nights of star widths and focuser temperatures
with a known slope, including one built like the night of 2026-09-25 (the
focuser cooled from 22.3 to 18.3 degrees Celsius and the width grew about 0.7
px per degree), and checks that the fit recovers the slope, splits at
autofocus runs, uses airmass when given, and says what is missing otherwise.
"""

import math
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from astrometricslib import MeasurementReference
from wayfindinglib.analytics.result_quality.focus_drift import (
    ALLOWED_WIDTH_RATIO,
    FocusDriftStatus,
    FocusFrameSample,
    fit_focus_drift,
    focus_drift_statement,
)
from wayfindinglib.models.quality.statements import ActionClass, StatementKind

_START = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)


def _night(
    *,
    slope: float = 0.7,
    best_width: float = 2.2,
    start_temperature: float = 22.3,
    end_temperature: float = 18.3,
    hours: float = 4.0,
    refocus_hour: float | None = None,
    noise: float = 0.03,
    uncertainty: float | None = 0.05,
    with_airmass: bool = False,
    seed: int = 1,
) -> tuple[list[FocusFrameSample], list[datetime]]:
    """Build a night of frames with a known focus drift.

    Returns
    -------
    result : `tuple`
        The frames and the autofocus run times.
    """
    generator = np.random.default_rng(seed)
    frame_count = int(hours * 30)
    runs = [_START - timedelta(minutes=1)]
    if refocus_hour is not None:
        runs.append(_START + timedelta(hours=refocus_hour))
    samples = []
    for number in range(frame_count):
        fraction = number / (frame_count - 1)
        moment = _START + timedelta(hours=hours * fraction)
        temperature = start_temperature + (end_temperature - start_temperature) * fraction
        segment_start = max(run for run in runs if run <= moment)
        segment_start_fraction = (segment_start - _START).total_seconds() / 3600.0 / hours
        reference_temperature = start_temperature + (end_temperature - start_temperature) * max(
            segment_start_fraction, 0.0
        )
        change = temperature - reference_temperature
        width = math.sqrt(best_width**2 + (slope * change) ** 2) + generator.normal(0.0, noise)
        samples.append(
            FocusFrameSample(
                captured_at=moment,
                focuser_temperature_degc=temperature,
                star_width_px=width,
                star_width_uncertainty_px=uncertainty,
                airmass=1.2 + 0.1 * fraction if with_airmass else None,
                evidence=MeasurementReference(row_id=number + 1),
            )
        )
    return samples, runs


def test_recovers_the_slope_of_a_night_built_like_2026_09_25() -> None:
    """Cooling of 4 C with a 0.7 px per C slope is recovered to 15%."""
    samples, runs = _night()
    result = fit_focus_drift(samples, runs)
    assert result.status == FocusDriftStatus.DRIFT_FOUND
    assert result.slope_px_per_degc == pytest.approx(0.7, rel=0.15)
    assert result.best_focus_width_px == pytest.approx(2.2, rel=0.05)
    expected_interval = math.sqrt(ALLOWED_WIDTH_RATIO**2 - 1.0) * 2.2 / 0.7
    assert result.refocus_interval_degc == pytest.approx(expected_interval, rel=0.2)
    assert result.interval_tolerance_degc > 0.0
    assert result.frames_used == 120


def test_allowed_width_increase_is_about_five_percent() -> None:
    """An efficiency budget of 0.9 allows a width increase of about 5%."""
    samples, runs = _night()
    result = fit_focus_drift(samples, runs)
    assert result.allowed_width_increase_fraction == pytest.approx(0.054, abs=0.002)


def test_two_segments_share_one_slope() -> None:
    """A mid-night autofocus starts a second segment with the same slope."""
    samples, runs = _night(refocus_hour=2.0, hours=6.0, end_temperature=16.3)
    result = fit_focus_drift(samples, runs)
    assert result.segments_used == 2
    assert result.slope_px_per_degc == pytest.approx(0.7, rel=0.15)


def test_flat_night_shows_no_drift() -> None:
    """A slope of zero gives no drift beyond the noise and no statement."""
    samples, runs = _night(slope=0.0)
    result = fit_focus_drift(samples, runs)
    assert result.status == FocusDriftStatus.NO_DRIFT_DETECTED
    assert focus_drift_statement(result) is None


def test_airmass_term_is_used_when_every_frame_has_it() -> None:
    """The fit uses airmass when every frame has it and finds the slope."""
    samples, runs = _night(with_airmass=True)
    result = fit_focus_drift(samples, runs)
    assert result.airmass_used
    assert result.slope_px_per_degc == pytest.approx(0.7, rel=0.2)


def test_frames_before_the_first_autofocus_are_dropped_and_counted() -> None:
    """Frames taken before any autofocus run are not used."""
    samples, _ = _night()
    runs = [_START + timedelta(hours=1)]
    result = fit_focus_drift(samples, runs)
    assert result.frames_dropped == 30
    assert result.frames_used == 90


def test_no_autofocus_times_is_insufficient_data() -> None:
    """Without autofocus run times the result says what to read."""
    samples, _ = _night()
    result = fit_focus_drift(samples, [])
    assert result.status == FocusDriftStatus.INSUFFICIENT_DATA
    assert "autofocus" in result.reason
    statement = focus_drift_statement(result)
    assert statement.kind == StatementKind.INSUFFICIENT_DATA


def test_too_few_frames_is_insufficient_data() -> None:
    """A handful of frames is not enough for the fit."""
    samples, runs = _night(hours=0.4)
    result = fit_focus_drift(samples, runs)
    assert result.status == FocusDriftStatus.INSUFFICIENT_DATA
    assert "needs" in result.reason


def test_small_temperature_change_is_insufficient_data() -> None:
    """A change of 0.3 degrees C is lost in the noise."""
    samples, runs = _night(start_temperature=20.0, end_temperature=19.7)
    result = fit_focus_drift(samples, runs)
    assert result.status == FocusDriftStatus.INSUFFICIENT_DATA
    assert "temperature" in result.reason


def test_statement_is_a_session_procedure_recommendation_with_evidence() -> None:
    """The statement proposes the interval and cites the frames it used."""
    samples, runs = _night()
    result = fit_focus_drift(samples, runs)
    statement = focus_drift_statement(result)
    assert statement.kind == StatementKind.RECOMMENDATION
    assert statement.action_class == ActionClass.SESSION_PROCEDURE
    assert statement.proposed_action.refocus_every_degc == pytest.approx(result.refocus_interval_degc)
    assert len(statement.evidence) == 120
    assert "give or take" in statement.message


def test_interval_below_the_width_error_is_insufficient_data() -> None:
    """A width less certain than the allowed increase promises no interval."""
    samples, runs = _night(uncertainty=0.5)
    result = fit_focus_drift(samples, runs)
    assert result.interval_resolvable is False
    statement = focus_drift_statement(result)
    assert statement.kind == StatementKind.INSUFFICIENT_DATA
    assert "error" in statement.reason
