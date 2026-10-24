"""Purpose: Tests for the shared reply budget helper.

Description: Checks that the limit equals the MCP tool registry's, that the
measure of a reply is the registry's own (``model_dump`` with compact
``json.dumps`` and ``default=str``), that the budget keeps 10 percent
headroom, and that ``fit_reply`` shrinks step by step until a reply fits.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from astrometricslib import MeasurementReference
from wayfindinglib.analytics.result_quality.reply_budget import (
    MAXIMUM_REPLY_CHARACTERS,
    fit_reply,
    fit_reply_to_budget,
    reply_budget,
    reply_characters,
)
from wayfindinglib.models.planning.target_report import TargetDatasetReport
from wayfindinglib.models.quality.statements import (
    ActionClass,
    Confidence,
    NeedsAttention,
    Severity,
    StatementKind,
    TargetStatement,
)


class _Reply(BaseModel):
    """A small reply with a date and a list."""

    when: datetime
    items: list[str]


def test_the_limit_equals_the_mcp_tool_registry_limit() -> None:
    """The registry's constant is read from its source, not imported."""
    source = Path(__file__).resolve().parents[4] / "mcp_servers" / "common" / "tool_registry.py"
    match = re.search(r"^MAXIMUM_REPLY_BYTES = (\d+)", source.read_text(), re.MULTILINE)
    assert match is not None
    assert int(match.group(1)) == MAXIMUM_REPLY_CHARACTERS


def test_the_budget_keeps_headroom() -> None:
    """The budget is the limit less ten percent."""
    assert reply_budget() == 36_000
    assert reply_budget(1000) == 900


def test_a_reply_is_measured_like_the_registry_measures_it() -> None:
    """A date is written with ``str``, as ``default=str`` does."""
    reply = _Reply(when=datetime(2026, 10, 1, tzinfo=UTC), items=["a"])
    expected = len(json.dumps(reply.model_dump(), separators=(",", ":"), default=str))
    assert reply_characters(reply) == expected


def test_fit_reply_applies_steps_until_the_reply_fits() -> None:
    """Each round halves the list until it fits or the steps run out."""
    reply = _Reply(when=datetime(2026, 10, 1, tzinfo=UTC), items=["x" * 10] * 64)

    def halve(current: _Reply, _round: int) -> _Reply | None:
        """Halve the list, or give up when one item is left.

        Returns
        -------
        smaller : `_Reply` or `None`
            The shorter reply, or ``None`` when one item is left.
        """
        if len(current.items) <= 1:
            return None
        return current.model_copy(update={"items": current.items[: len(current.items) // 2]})

    fitted = fit_reply(reply, halve, budget=200)
    assert reply_characters(fitted) <= 200
    assert 1 <= len(fitted.items) < 64
    assert fit_reply(reply, halve, budget=10).items == ["x" * 10]
    assert fit_reply(reply, halve, budget=10**6) is reply


def _busy_statement(reference_count: int) -> TargetStatement:
    """Build a statement with many references, the most extreme first.

    Returns
    -------
    statement : `TargetStatement`
        A warning that cites row ids 1 to ``reference_count`` in order.
    """
    return TargetStatement(
        rule="trailed_stars",
        kind=StatementKind.WARNING,
        severity=Severity.ADVICE,
        action_class=ActionClass.SESSION_PROCEDURE,
        message="Stars are trailed.",
        confidence=Confidence.DESIGN_ESTIMATE,
        evidence=[MeasurementReference(row_id=row) for row in range(1, reference_count + 1)],
    )


def test_a_busy_report_is_trimmed_for_the_mcp_and_keeps_the_most_extreme_references() -> None:
    """Trimming gets under budget, keeps first references, drops nothing."""
    statements = [_busy_statement(900) for _ in range(30)]
    report = TargetDatasetReport(
        targetId="T",
        statements=statements,
        needsAttention=NeedsAttention(warning_count=30, worst_severity=Severity.ADVICE),
        generatedAt=datetime(2026, 10, 24, tzinfo=UTC),
    )
    assert reply_characters(report) > MAXIMUM_REPLY_CHARACTERS
    fitted = fit_reply_to_budget(report)
    assert reply_characters(fitted) <= reply_budget()
    assert len(fitted.statements) == 30
    for kept, original in zip(fitted.statements, statements, strict=True):
        assert kept.evidence == original.evidence[: len(kept.evidence)]
        assert len(kept.evidence) + kept.evidence_omitted_count == 900
        assert len(kept.evidence) >= 1
    # The full report (the library's own reply) is not changed by trimming.
    assert all(statement.evidence_omitted_count == 0 for statement in report.statements)
    assert len(report.statements[0].evidence) == 900


def test_a_reply_that_fits_or_is_of_another_kind_is_returned_as_it_is() -> None:
    """Other values and short replies pass through unchanged."""
    report = TargetDatasetReport(
        targetId="T",
        statements=[_busy_statement(3)],
        needsAttention=NeedsAttention(warning_count=1, worst_severity=Severity.ADVICE),
        generatedAt=datetime(2026, 10, 24, tzinfo=UTC),
    )
    assert fit_reply_to_budget(report) is report
    other = {"anything": 1}
    assert fit_reply_to_budget(other) is other
