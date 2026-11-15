"""Purpose: Tests for the helpers that the wayfindinglib tuners share.

Description: Checks the percentile, the widest-log-gap split and the night
naming on small lists with known answers, and that the record readers refuse
cleanly when the wayfinder has no records.
"""

import math
from types import SimpleNamespace

import pytest

from astrometricslib.parameters import TuningSkippedError
from wayfindinglib.analytics.tuning_support import (
    butler_of,
    largest_log_gap_threshold,
    night_of,
    percentile,
    records_of,
)


def test_percentile_interpolates_between_sorted_values() -> None:
    """The median of 1 to 5 is 3 and the 100th percentile is the largest."""
    assert percentile([5, 1, 3, 2, 4], 50.0) == pytest.approx(3.0)
    assert percentile([5, 1, 3, 2, 4], 100.0) == pytest.approx(5.0)


def test_percentile_of_nothing_is_refused() -> None:
    """An empty list has no percentile."""
    with pytest.raises(ValueError, match="at least one"):
        percentile([], 50.0)


def test_largest_log_gap_threshold_sits_in_the_middle_of_the_widest_gap() -> None:
    """Values near 1 and near 100 split at their geometric middle."""
    threshold = largest_log_gap_threshold([1.0, 1.1, 1.2, 100.0, 110.0], 3.0)
    assert threshold == pytest.approx(math.sqrt(1.2 * 100.0))


def test_largest_log_gap_threshold_is_none_without_a_clear_gap() -> None:
    """Values spread evenly have no gap as wide as the minimum ratio."""
    assert largest_log_gap_threshold([1.0, 1.5, 2.0, 2.5], 3.0) is None
    assert largest_log_gap_threshold([1.0], 3.0) is None


def test_largest_log_gap_threshold_ignores_values_that_are_not_positive() -> None:
    """Zero and negative values are dropped before the gaps are measured."""
    assert largest_log_gap_threshold([0.0, -1.0, 1.0, 100.0], 3.0) == pytest.approx(10.0)


def test_night_of_prefers_the_session_id() -> None:
    """A row's session id names its night; a bare row gives None."""
    assert night_of({"session_id": "2026-10-02", "timestamp": 1.0}) == "2026-10-02"
    assert night_of({}) is None
    assert night_of({"timestamp": 1_790_000_000.0}) is not None


def test_record_readers_refuse_a_wayfinder_without_records() -> None:
    """A wayfinder without storage raises `TuningSkippedError`."""
    with pytest.raises(TuningSkippedError):
        records_of(SimpleNamespace())
    with pytest.raises(TuningSkippedError):
        butler_of(SimpleNamespace())
