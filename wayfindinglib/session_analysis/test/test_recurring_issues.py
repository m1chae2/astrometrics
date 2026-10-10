"""Purpose: Unit tests for finding findings that repeat across nights.

Description: Verifies that a finding counts as recurring only at advice or
warning level on at least two nights, that nights are counted per analysis,
and that the most frequent and most serious come first.
"""

import pytest

from wayfindinglib.models.session.session_quality import (
    Recommendation,
    RecommendationKind,
    RecommendationSeverity,
)
from wayfindinglib.session_analysis.recurring_issues import find_recurring_issues


def _finding(
    kind: RecommendationKind, severity: RecommendationSeverity, message: str = "m"
) -> Recommendation:
    """Build a recommendation.

    Returns
    -------
    recommendation : `Recommendation`
        A recommendation of the given kind and severity.
    """
    return Recommendation(kind=kind, severity=severity, message=message, confidence="high")


def test_a_finding_on_two_nights_is_recurring() -> None:
    """Verify the nights, the share and the latest message are reported."""
    weak = RecommendationKind.CHECK_GUIDE_SIGNAL
    nights = [
        ("guiding", "2026-01-01", [_finding(weak, RecommendationSeverity.ADVICE, "first")]),
        ("guiding", "2026-01-02", []),
        ("guiding", "2026-01-03", [_finding(weak, RecommendationSeverity.WARNING, "latest")]),
    ]

    (issue,) = find_recurring_issues(nights)

    assert issue.nights == ["2026-01-01", "2026-01-03"]
    assert issue.nights_analysed == 3
    assert issue.share == pytest.approx(2 / 3)
    assert issue.worst_severity == RecommendationSeverity.WARNING
    assert issue.example == "latest"


def test_a_finding_on_one_night_is_not_a_pattern() -> None:
    """Verify a single bad night is left out."""
    nights = [
        (
            "guiding",
            "2026-01-01",
            [_finding(RecommendationKind.RECALIBRATE_GUIDER, RecommendationSeverity.WARNING)],
        ),
        ("guiding", "2026-01-02", []),
    ]

    assert find_recurring_issues(nights) == []


def test_information_is_never_a_problem_pattern() -> None:
    """Verify facts stated at information level are skipped."""
    info = _finding(RecommendationKind.GUIDING_WITHIN_LIMIT, RecommendationSeverity.INFO)
    nights = [("guiding", "2026-01-01", [info]), ("guiding", "2026-01-02", [info])]

    assert find_recurring_issues(nights) == []


def test_the_two_analyses_are_counted_separately() -> None:
    """Verify capture nights do not dilute guiding nights, and vice versa."""
    gap = RecommendationKind.CAPTURES_NOT_IN_LIBRARY
    advice = RecommendationSeverity.ADVICE
    nights = [
        ("guiding", "2026-01-01", []),
        ("guiding", "2026-01-02", []),
        ("capture", "2026-01-01", [_finding(gap, advice)]),
        ("capture", "2026-01-02", [_finding(gap, advice)]),
    ]

    (issue,) = find_recurring_issues(nights)

    assert issue.pipeline == "capture"
    assert issue.nights_analysed == 2
    assert issue.share == pytest.approx(1.0)


def test_the_most_frequent_then_most_serious_come_first() -> None:
    """Verify the ordering puts the pattern that matters most on top."""
    often = RecommendationKind.CAPTURES_NOT_IN_LIBRARY
    rarely = RecommendationKind.RECALIBRATE_GUIDER
    advice, warning = RecommendationSeverity.ADVICE, RecommendationSeverity.WARNING
    nights = [
        ("capture", "n1", [_finding(often, advice)]),
        ("capture", "n2", [_finding(often, advice)]),
        ("capture", "n3", [_finding(often, advice), _finding(rarely, warning)]),
        ("capture", "n4", [_finding(rarely, warning)]),
    ]

    issues = find_recurring_issues(nights)

    assert [issue.kind for issue in issues] == [often, rarely]
