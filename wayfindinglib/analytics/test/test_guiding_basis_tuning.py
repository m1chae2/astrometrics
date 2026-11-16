"""Purpose: Tests for the guide-cycle and periodic-error-period tuners.

Description: The guide cycle is the median gap between consecutive guide
samples of a session, leaving out pauses; the periodic-error period is the
median dominant period of the stored guiding-spectrum analyses, ignoring those
with none.
"""

from types import SimpleNamespace

from wayfindinglib.analytics.basis_tuning import cycle_gaps, dominant_periods


def test_gaps_are_taken_within_each_session_and_pauses_are_left_out() -> None:
    """A 90 s pause and the jump between sessions are not guide cycles."""
    sessions = [[0.0, 3.0, 6.0, 96.0, 99.0], [1000.0, 1002.0]]
    assert sorted(cycle_gaps(sessions, pause_limit=60.0)) == [2.0, 3.0, 3.0, 3.0]


def test_only_analyses_with_a_period_count() -> None:
    """An analysis with no dominant period, or a zero one, is ignored."""
    analyses = [
        SimpleNamespace(dominant_period_seconds=480.0),
        SimpleNamespace(dominant_period_seconds=None),
        SimpleNamespace(dominant_period_seconds=0.0),
        SimpleNamespace(dominant_period_seconds=640.0),
    ]
    assert dominant_periods(analyses) == [480.0, 640.0]
