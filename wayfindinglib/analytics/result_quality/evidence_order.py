"""Purpose: Put each statement's most extreme evidence first.

Description: A statement cites the measurements it rests on. When a reply is
shortened for an AI tool, the references are cut from the end of the list
(`reply_budget.py`), so the order of the list decides what survives. This
module sorts a statement's references so the ones that matter most come first:

* The sort works on the rows the references point at. A row's distance beyond
  the limit is ``(value - limit) / |limit|`` when the statement fired because
  its rows were above the limit, and ``(limit - value) / |limit|`` when they
  were below. Which side is "beyond" is read from the rows themselves: it is
  the side the median of the rows lies on. The distance is in units of the
  limit, so it does not depend on the metric's unit.
* The biggest distance comes first. Rows at the same distance go newest first,
  then by row id, so the order never changes between reads.
* The distance is used only when it means something: every cited row is of
  one metric, the statement has a limit with a value, and (when the units of
  the metrics are known) the metric's unit is the limit's unit. Otherwise the
  references go newest first, which is the next best use of a short list.
* A reference with no row in hand (a per-star result, or a row that was not
  read) goes after the rest, in its original order.

Nothing is added or removed; only the order changes.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Mapping, Sequence

from astrometricslib import MeasurementReference, QualityLimit, QualityMeasurement
from wayfindinglib.models.quality.statements import TargetStatement


def distance_beyond_limit(value: float, limit_value: float, *, higher_is_beyond: bool) -> float:
    """Say how far a value is beyond a limit, in units of the limit.

    Parameters
    ----------
    value : `float`
        The measured value.
    limit_value : `float`
        The limit.
    higher_is_beyond : `bool`
        True when values above the limit are the bad side.

    Returns
    -------
    distance : `float`
        ``(value - limit) / |limit|`` for the high side, the negative of that
        for the low side. Positive means beyond the limit; negative means
        inside it. A limit of zero gives the plain difference, because a
        ratio to zero has no meaning.
    """
    difference = value - limit_value if higher_is_beyond else limit_value - value
    return difference / abs(limit_value) if abs(limit_value) > 0.0 else difference


def order_rows_most_extreme_first(
    rows: Sequence[QualityMeasurement], limit_value: float | None
) -> list[QualityMeasurement]:
    """Sort rows of one metric, the furthest beyond a limit first.

    Parameters
    ----------
    rows : `Sequence` [`QualityMeasurement`]
        The rows. Rows with no value go last.
    limit_value : `float` or `None`
        The limit the statement used. With ``None``, or with fewer than one
        row that has a value, the rows go newest first.

    Returns
    -------
    ordered : `list` [`QualityMeasurement`]
        The same rows in the new order.
    """
    values = [row.value for row in rows if row.value is not None]

    def newest_first(row: QualityMeasurement) -> tuple[float, int]:
        """Give the sort key for the newest-first order.

        Parameters
        ----------
        row : `QualityMeasurement`
            The row.

        Returns
        -------
        key : `tuple` [`float`, `int`]
            Minus the time stamp, then minus the row id.
        """
        return (-row.measured_at.timestamp(), -(row.row_id or 0))

    if limit_value is None or not values:
        return sorted(rows, key=newest_first)
    higher_is_beyond = statistics.median(values) >= limit_value

    def key(row: QualityMeasurement) -> tuple[int, float, float, int]:
        """Give the sort key: furthest beyond the limit, then newest.

        Parameters
        ----------
        row : `QualityMeasurement`
            The row.

        Returns
        -------
        key : `tuple`
            A key that sorts the furthest beyond the limit first.
        """
        if row.value is None:
            return (1, 0.0, *newest_first(row))
        distance = distance_beyond_limit(row.value, limit_value, higher_is_beyond=higher_is_beyond)
        return (0, -distance, *newest_first(row))

    return sorted(rows, key=key)


def _first_limit_value(limits: Iterable[QualityLimit]) -> tuple[float, str] | None:
    """Find the first limit that has a value.

    Parameters
    ----------
    limits : `Iterable` [`QualityLimit`]
        A statement's limits.

    Returns
    -------
    found : `tuple` [`float`, `str`] or `None`
        The value and unit of the first limit with a value, or ``None``.
    """
    for limit in limits:
        if limit.value is not None:
            return float(limit.value), limit.unit
    return None


def order_evidence_most_extreme_first(
    evidence: Sequence[MeasurementReference],
    limits: Iterable[QualityLimit],
    rows_by_id: Mapping[int, QualityMeasurement],
    units_by_metric: Mapping[str, str] | None = None,
) -> list[MeasurementReference]:
    """Sort the references of a statement, the most extreme first.

    Parameters
    ----------
    evidence : `Sequence` [`MeasurementReference`]
        The statement's references.
    limits : `Iterable` [`QualityLimit`]
        The statement's limits. The first one with a value is used.
    rows_by_id : `Mapping` [`int`, `QualityMeasurement`]
        The rows the references can point at, by row id.
    units_by_metric : `Mapping` [`str`, `str`], optional
        The unit of each metric. When given, the distance is used only if
        the cited metric's unit equals the limit's unit.

    Returns
    -------
    ordered : `list` [`MeasurementReference`]
        The same references. References with a row come first, in the order
        described in the module description; the others follow in their
        original order.
    """
    with_rows = [
        (reference, rows_by_id[reference.row_id]) for reference in evidence if reference.row_id in rows_by_id
    ]
    without_rows = [reference for reference in evidence if reference.row_id not in rows_by_id]
    if len(with_rows) < 2:
        return list(evidence)
    found = _first_limit_value(limits)
    metrics = {row.metric for _reference, row in with_rows}
    limit_value: float | None = None
    if found is not None and len(metrics) == 1:
        metric = next(iter(metrics))
        known_unit = units_by_metric.get(metric) if units_by_metric is not None else None
        if known_unit is None or known_unit == found[1]:
            limit_value = found[0]
    reference_by_row = {id(row): reference for reference, row in with_rows}
    ordered_rows = order_rows_most_extreme_first([row for _reference, row in with_rows], limit_value)
    return [reference_by_row[id(row)] for row in ordered_rows] + without_rows


def order_statements_evidence(
    statements: Iterable[TargetStatement],
    rows: Iterable[QualityMeasurement],
    units_by_metric: Mapping[str, str] | None = None,
) -> list[TargetStatement]:
    """Sort the evidence of every statement, the most extreme first.

    Parameters
    ----------
    statements : `Iterable` [`TargetStatement`]
        The statements, in order.
    rows : `Iterable` [`QualityMeasurement`]
        The rows the statements may cite. Rows without a row id are ignored.
    units_by_metric : `Mapping` [`str`, `str`], optional
        The unit of each metric, to check a limit is comparable.

    Returns
    -------
    ordered : `list` [`TargetStatement`]
        Copies in the same order, each with its evidence sorted. A statement
        with fewer than two references is returned as it is.
    """
    rows_by_id = {row.row_id: row for row in rows if row.row_id is not None}
    result: list[TargetStatement] = []
    for statement in statements:
        if len(statement.evidence) < 2:
            result.append(statement)
            continue
        ordered = order_evidence_most_extreme_first(
            statement.evidence, statement.limits, rows_by_id, units_by_metric
        )
        result.append(statement.model_copy(update={"evidence": ordered}))
    return result


__all__ = [
    "distance_beyond_limit",
    "order_evidence_most_extreme_first",
    "order_rows_most_extreme_first",
    "order_statements_evidence",
]
