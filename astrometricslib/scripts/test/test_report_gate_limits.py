"""Tests for the report of how often each quality limit fires.

Artificial target documents with known values check the reading of nested
summaries, the share beyond a limit, tolerance of missing data, and the
combined ratios.
"""

import json
import sqlite3

import pytest

from astrometricslib.scripts import report_gate_limits as report


def stack(rejected: float, stacked_fwhm: float = 3.0, expected: float = 3.0) -> dict:
    """Build a target document with a stack summary.

    Returns
    -------
    document : `dict`
        A target with the given stacking metrics.
    """
    return {
        "stacking": {
            "qualitySummary": {
                "stackingMetrics": {
                    "rejectedPixelFraction": rejected,
                    "stackedFwhmPx": stacked_fwhm,
                    "expectedStackFwhmPx": expected,
                    "framesSubmitted": 10,
                    "framesStacked": 8,
                }
            }
        }
    }


def metric_named(name: str) -> report.Metric:
    """Find a metric by name.

    Returns
    -------
    metric : `report.Metric`
        The metric with that name.
    """
    return next(metric for metric in report.METRICS if metric.name == name)


def test_the_share_beyond_a_limit_is_counted_over_the_targets_that_have_a_value() -> None:
    """Two of four rejected fractions are at or above 0.15; gaps skipped."""
    targets = [stack(0.01), stack(0.15), stack(0.30), stack(0.05), {"stacking": None}]

    summary = report.summarize(metric_named("rejected pixel fraction"), targets)

    assert summary.count == 4
    assert summary.beyond_limit == 2
    assert summary.minimum == pytest.approx(0.01)
    assert summary.maximum == pytest.approx(0.30)
    assert summary.median == pytest.approx(0.10)


def test_a_ratio_metric_combines_two_stored_numbers_and_tolerates_gaps() -> None:
    """FWHM is judged as stacked over expected; a zero divisor is skipped."""
    targets = [stack(0.0, 3.9, 3.0), stack(0.0, 3.0, 3.0), stack(0.0, 3.0, 0.0)]

    summary = report.summarize(metric_named("stacked FWHM / expected FWHM"), targets)

    assert summary.count == 2
    assert summary.beyond_limit == 1  # 1.3 is above 1.2


def test_a_metric_with_no_limit_reports_only_its_spread() -> None:
    """The astrometric residual has no limit yet, so nothing can fire."""
    targets = [
        {"quality": {"astrometry": {"astrometryMetrics": {"astrometricResidualRmsArcsec": value}}}}
        for value in (1.0, 2.0, 9.0)
    ]

    summary = report.summarize(metric_named("plate-solve residual RMS (arcsec)"), targets)

    assert summary.beyond_limit == 0
    assert summary.metric.limit is None
    assert summary.maximum == pytest.approx(9.0)


def test_a_library_with_no_values_gives_an_empty_summary() -> None:
    """No data is a count of zero, not an error."""
    summary = report.summarize(metric_named("zero pixel fraction"), [{}, {"stacking": {}}])

    assert summary.count == 0
    assert summary.median is None


def test_the_report_names_each_limit_and_how_often_it_fires() -> None:
    """The table shows the count over the targets that have the value."""
    summaries = [report.summarize(metric_named("rejected pixel fraction"), [stack(0.01), stack(0.3)])]

    text = report.format_report(summaries, target_count=2)

    assert "2 targets read." in text
    assert "rejected pixel fraction" in text
    assert "1/2" in text


def test_targets_are_read_from_the_database_and_bad_rows_are_skipped() -> None:
    """A row that is not valid JSON is skipped, not fatal."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE targets (id TEXT, data_json TEXT)")
    connection.execute("INSERT INTO targets VALUES ('good', ?)", (json.dumps(stack(0.1)),))
    connection.execute("INSERT INTO targets VALUES ('bad', 'not json')")
    connection.execute("INSERT INTO targets VALUES ('list', '[1, 2]')")

    assert len(report.load_targets(connection)) == 1
