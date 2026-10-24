"""Purpose: Check that the report shows every context metric.

Description: A context metric drives no rule; it explains other results. The
plan (section 6.7) says each one must be shown by at least one report
section. The report's context section shows every registered context metric
the target has a stored row for. This test feeds one made-up row of every
registered context metric to the section and checks that each comes back, so
a metric added later cannot be left out unnoticed. It also checks how a frame
metric (median over frames) and a stack metric (newest row) are shown, and
that metrics with other purposes are left out.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    Astrometrics,
    MeasurementOrigin,
    MeasurementStatus,
    MetricPurpose,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from wayfindinglib.tasks.planning_tasks.target_report_tasks import context_readings

NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)

_next_row_id = 0


def _row(
    metric: str, level: SubjectLevel, value: float | None, subject_id: str, minutes: int = 0
) -> QualityMeasurement:
    """Build one stored row with a fresh row id.

    Parameters
    ----------
    metric : `str`
        The metric name.
    level : `SubjectLevel`
        The subject level.
    value : `float` or `None`
        The value.
    subject_id : `str`
        The subject.
    minutes : `int`, optional
        Minutes after the base time that it was measured.

    Returns
    -------
    row : `QualityMeasurement`
        The row.
    """
    global _next_row_id
    _next_row_id += 1
    return QualityMeasurement(
        metric=metric,
        definition_version=1,
        subject=QualitySubject(level=level, subject_id=subject_id, target_id="T"),
        status=MeasurementStatus.MEASURED if value is not None else MeasurementStatus.NOT_MEASURED,
        value=value,
        reason="" if value is not None else "not measured in this test",
        origin=MeasurementOrigin.MEASURED,
        run_id="run",
        measured_at=NOW + timedelta(minutes=minutes),
        row_id=_next_row_id,
    )


@pytest.fixture(scope="module")
def summaries() -> dict:
    """Read the registered metrics through the public API.

    Returns
    -------
    summaries : `dict` [`str`, `MetricSummary`]
        Every registered metric, by name.
    """
    return {summary.name: summary for summary in Astrometrics().quality.metrics()}


def test_every_registered_context_metric_is_shown_by_the_context_section(summaries: dict) -> None:
    """A row of each context metric comes back as a reading of that metric."""
    context_metrics = [
        name for name, summary in summaries.items() if summary.purpose == MetricPurpose.CONTEXT
    ]
    assert len(context_metrics) > 100
    rows = [_row(name, summaries[name].levels[0], 1.5, f"T/{name}") for name in context_metrics]
    shown = {reading.metric for reading in context_readings(rows, summaries)}
    assert shown == set(context_metrics)


def test_decision_and_exploratory_metrics_are_not_in_the_context_section(summaries: dict) -> None:
    """Only the context purpose is shown there."""
    others = [name for name, summary in summaries.items() if summary.purpose != MetricPurpose.CONTEXT][:20]
    rows = [_row(name, summaries[name].levels[0], 1.0, f"T/{name}") for name in others]
    assert context_readings(rows, summaries) == []


def test_a_frame_metric_is_shown_as_its_newest_value_for_each_setting(summaries: dict) -> None:
    """Frames of two settings give one reading each, the newest value."""
    rows = []
    for setting_id, base in ((1, -12.0), (2, -5.0)):
        for index in range(4):
            row = _row(
                "frame.sensor_temperature_degc",
                SubjectLevel.FRAME,
                base + index,
                f"T/s{setting_id}f{index}",
                minutes=index,
            )
            rows.append(
                row.model_copy(
                    update={"subject": row.subject.model_copy(update={"capture_setting_id": setting_id})}
                )
            )
    readings = context_readings(rows, summaries)
    assert [(reading.capture_setting_id, reading.value) for reading in readings] == [(1, -9.0), (2, -2.0)]
    assert all(reading.row_count == 4 and reading.row_id is not None for reading in readings)
    assert readings[0].unit == "degC"


def test_a_stack_metric_is_shown_as_its_newest_row(summaries: dict) -> None:
    """Two measurements of one stack metric show the newer one."""
    older = _row("stacking.duration_s", SubjectLevel.STACK, 100.0, "T/stack.fits", minutes=0)
    newer = _row("stacking.duration_s", SubjectLevel.STACK, 250.0, "T/stack.fits", minutes=5)
    (reading,) = context_readings([older, newer], summaries)
    assert reading.level == "stack"
    assert reading.value == pytest.approx(250.0)
    assert reading.row_id == newer.row_id
    assert reading.row_count == 2


def test_a_context_row_that_was_not_measured_is_shown_with_no_value(summaries: dict) -> None:
    """A not-measured row is shown, with a value of None, never hidden."""
    row = _row("stacking.duration_s", SubjectLevel.STACK, None, "T/stack.fits")
    (reading,) = context_readings([row], summaries)
    assert reading.value is None
