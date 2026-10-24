"""Purpose: Tests for putting a statement's most extreme evidence first.

Description: Checks the distance beyond a limit (high side and low side), the
tie order (newest first), the cases where the distance is not used (rows of
several metrics, a limit of another unit, no limit), the references with no
row, and that a statement keeps everything but the order.
"""

from datetime import UTC, datetime, timedelta

import pytest

from astrometricslib import (
    LimitTier,
    MeasurementReference,
    QualityLimit,
    QualityMeasurement,
    QualitySubject,
)
from wayfindinglib.analytics.result_quality.evidence_order import (
    distance_beyond_limit,
    order_evidence_most_extreme_first,
    order_rows_most_extreme_first,
    order_statements_evidence,
)
from wayfindinglib.models.quality.statements import Confidence, StatementKind, TargetStatement

_NOW = datetime(2026, 10, 20, 22, 0, tzinfo=UTC)


def _row(
    row_id: int, value: float | None, *, metric: str = "frame.m", minutes: int = 0
) -> QualityMeasurement:
    """Build one stored measurement.

    Returns
    -------
    row : `QualityMeasurement`
        A measured row with the given id, value and age.
    """
    return QualityMeasurement(
        metric=metric,
        definition_version=1,
        subject=QualitySubject(level="frame", subject_id=f"f{row_id}", target_id="T"),
        status="measured" if value is not None else "not_measured",
        reason="" if value is not None else "none",
        value=value,
        origin="measured",
        run_id="run",
        measured_at=_NOW + timedelta(minutes=minutes),
        row_id=row_id,
    )


def _limit(value: float | None, unit: str = "ratio") -> QualityLimit:
    """Build a design-estimate limit.

    Returns
    -------
    limit : `QualityLimit`
        The limit.
    """
    return QualityLimit(value=value, unit=unit, tier=LimitTier.DESIGN_ESTIMATE, derivation="test")


def _ids(references: list[MeasurementReference]) -> list[int | None]:
    """List the row ids of some references.

    Returns
    -------
    ids : `list` [`int` or `None`]
        The row ids in order.
    """
    return [reference.row_id for reference in references]


def test_distance_is_in_units_of_the_limit_on_either_side() -> None:
    """Above a limit of 2 by 1 is 0.5; below it by 1 (low side) is 0.5."""
    assert distance_beyond_limit(3.0, 2.0, higher_is_beyond=True) == pytest.approx(0.5)
    assert distance_beyond_limit(1.0, 2.0, higher_is_beyond=False) == pytest.approx(0.5)
    assert distance_beyond_limit(1.0, 2.0, higher_is_beyond=True) == pytest.approx(-0.5)
    assert distance_beyond_limit(1.0, 0.0, higher_is_beyond=True) == pytest.approx(1.0)


def test_rows_above_the_limit_go_furthest_first() -> None:
    """The side of the limit is read from the median of the rows."""
    rows = [_row(1, 1.2), _row(2, 3.0), _row(3, 2.0), _row(4, 2.5)]
    assert [row.row_id for row in order_rows_most_extreme_first(rows, 1.0)] == [2, 4, 3, 1]


def test_rows_below_the_limit_go_lowest_first() -> None:
    """When the rows sit below the limit, the lowest is the most extreme."""
    rows = [_row(1, 0.8), _row(2, 0.2), _row(3, 0.5), _row(4, 0.4)]
    assert [row.row_id for row in order_rows_most_extreme_first(rows, 0.9)] == [2, 4, 3, 1]


def test_equal_distances_go_newest_first() -> None:
    """Ties are broken by the time of the measurement, then the row id."""
    rows = [_row(1, 3.0, minutes=0), _row(2, 3.0, minutes=10), _row(3, 3.0, minutes=5), _row(4, 9.0)]
    assert [row.row_id for row in order_rows_most_extreme_first(rows, 1.0)] == [4, 2, 3, 1]


def test_without_a_limit_the_rows_go_newest_first() -> None:
    """No limit means no distance; the newest rows are the next best."""
    rows = [_row(1, 3.0, minutes=0), _row(2, 1.0, minutes=10), _row(3, 2.0, minutes=5)]
    assert [row.row_id for row in order_rows_most_extreme_first(rows, None)] == [2, 3, 1]


def test_a_row_with_no_value_goes_last() -> None:
    """A row without a value cannot be compared."""
    rows = [_row(1, None, minutes=99), _row(2, 3.0), _row(3, 5.0)]
    assert [row.row_id for row in order_rows_most_extreme_first(rows, 1.0)] == [3, 2, 1]


def test_references_follow_their_rows_and_unknown_references_go_last() -> None:
    """A reference with no row in hand keeps its place after the rest."""
    rows = {row.row_id: row for row in (_row(1, 1.2), _row(2, 3.0), _row(3, 2.0))}
    evidence = [
        MeasurementReference(row_id=1),
        MeasurementReference(row_id=99),
        MeasurementReference(row_id=3),
        MeasurementReference(row_id=2),
    ]
    ordered = order_evidence_most_extreme_first(evidence, [_limit(1.0)], rows)
    assert _ids(ordered) == [2, 3, 1, 99]


def test_rows_of_several_metrics_are_not_ranked_by_distance() -> None:
    """Rows of two metrics have no common distance; they go newest first."""
    rows = {
        1: _row(1, 100.0, metric="a", minutes=0),
        2: _row(2, 0.1, metric="b", minutes=10),
        3: _row(3, 5.0, metric="a", minutes=5),
    }
    evidence = [MeasurementReference(row_id=row_id) for row_id in (1, 2, 3)]
    assert _ids(order_evidence_most_extreme_first(evidence, [_limit(1.0)], rows)) == [2, 3, 1]


def test_a_limit_of_another_unit_is_not_used() -> None:
    """A limit in a different unit from the metric is ignored."""
    rows = {1: _row(1, 9.0, minutes=0), 2: _row(2, 2.0, minutes=10)}
    evidence = [MeasurementReference(row_id=1), MeasurementReference(row_id=2)]
    limit = _limit(1.0, unit="arcsec")
    assert _ids(order_evidence_most_extreme_first(evidence, [limit], rows, {"frame.m": "px"})) == [2, 1]
    assert _ids(order_evidence_most_extreme_first(evidence, [limit], rows, {"frame.m": "arcsec"})) == [1, 2]


def test_a_statement_keeps_everything_but_the_order() -> None:
    """Only the evidence order changes; other statements pass through."""
    rows = [_row(1, 1.2), _row(2, 3.0), _row(3, 2.0)]
    statement = TargetStatement(
        rule="trailed_stars",
        kind=StatementKind.INSUFFICIENT_DATA,
        message="m",
        reason="r",
        evidence=[MeasurementReference(row_id=row.row_id) for row in rows],
        limits=[_limit(1.0)],
        confidence=Confidence.DESIGN_ESTIMATE,
    )
    short = statement.model_copy(update={"evidence": statement.evidence[:1]})
    ordered, untouched = order_statements_evidence([statement, short], rows)
    assert _ids(ordered.evidence) == [2, 3, 1]
    assert ordered.model_copy(update={"evidence": statement.evidence}) == statement
    assert untouched is short
