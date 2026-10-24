"""Purpose: Find the findings that repeat across nights.

Description: The per-night analyses each say what went wrong that night. A
finding that appears on several nights is a pattern, not weather, and is the
kind worth acting on. This function reads the nights' recommendations and
lists each rule's warning or advice that appears on at least two
nights. Findings that only state a fact (information level) are not patterns
of a problem and are skipped.

It is a pure function of the recommendations passed in.
"""

from collections import defaultdict
from collections.abc import Iterable

from wayfindinglib.models.quality.statements import Severity, TargetStatement
from wayfindinglib.models.session.recurring_issue import RecurringIssue

MINIMUM_NIGHTS_FOR_A_PATTERN = 2
"""Fewest nights a finding must appear on to be called recurring."""

_SEVERITY_RANK = {
    Severity.INFO: 0,
    Severity.ADVICE: 1,
    Severity.WARNING: 2,
}


def find_recurring_issues(
    nights: Iterable[tuple[str, str, list[TargetStatement]]],
) -> list[RecurringIssue]:
    """List the findings that repeat across nights.

    Parameters
    ----------
    nights : `Iterable` [`tuple` [`str`, `str`, `list` [`TargetStatement`]]]
        One entry per analysed night and analysis: the pipeline name, the
        night, and that night's recommendations.

    Returns
    -------
    issues : `list` [`RecurringIssue`]
        Each finding of advice or warning level on at least two nights, most
        frequent first, then most serious first.
    """
    analysed: dict[str, set[str]] = defaultdict(set)
    found: dict[tuple[str, str], dict[str, TargetStatement]] = defaultdict(dict)
    for pipeline, night, recommendations in nights:
        analysed[pipeline].add(night)
        for recommendation in recommendations:
            if recommendation.severity in (None, Severity.INFO):
                continue
            found[pipeline, recommendation.rule][night] = recommendation

    issues = []
    for (pipeline, rule), by_night in found.items():
        if len(by_night) < MINIMUM_NIGHTS_FOR_A_PATTERN:
            continue
        ordered = sorted(by_night)
        worst = max((r.severity or Severity.INFO for r in by_night.values()), key=_SEVERITY_RANK.__getitem__)
        issues.append(
            RecurringIssue(
                pipeline=pipeline,
                rule=rule,
                worst_severity=worst,
                nights=ordered,
                nights_analysed=len(analysed[pipeline]),
                share=len(ordered) / len(analysed[pipeline]),
                example=by_night[ordered[-1]].message,
            )
        )
    return sorted(issues, key=lambda issue: (-issue.share, -_SEVERITY_RANK[issue.worst_severity], issue.rule))
