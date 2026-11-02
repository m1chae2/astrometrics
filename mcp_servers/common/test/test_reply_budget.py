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

from astrometricslib.reports import MeasurementReference
from mcp_servers.common.reply_budget import (
    MAXIMUM_REPLY_CHARACTERS,
    fit_reply,
    fit_reply_to_budget,
    reply_budget,
    reply_characters,
)
from wayfindinglib.models.planning.target_report import ContextReading, TargetDatasetReport
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
    source = Path(__file__).resolve().parents[1] / "tool_registry.py"
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


def test_the_context_section_goes_first_and_then_the_long_evidence_lists() -> None:
    """A tight budget drops the readings, then evidence, never statements."""
    full = TargetDatasetReport(
        targetId="T",
        statements=[_busy_statement(900) for _ in range(30)],
        needsAttention=NeedsAttention(warning_count=30, worst_severity=Severity.ADVICE),
        generatedAt=datetime(2026, 10, 24, tzinfo=UTC),
        context=[ContextReading(metric=f"metric_{index}", level="stack", value=1.0) for index in range(40)],
    )
    without_context = full.model_copy(update={"context": []})
    size_without_context = reply_characters(without_context)
    assert reply_characters(full) > size_without_context

    only_context = fit_reply_to_budget(full, budget=size_without_context + 500)
    assert only_context.context == []
    assert only_context.context_omitted_reason is not None
    assert only_context.statements == full.statements

    tight = fit_reply_to_budget(full, budget=2_000)
    assert [statement.rule for statement in tight.statements] == [s.rule for s in full.statements]
    assert tight.needs_attention == full.needs_attention
    for kept, original in zip(tight.statements, full.statements, strict=True):
        assert len(kept.evidence) + kept.evidence_omitted_count == len(original.evidence)
        assert len(kept.evidence) <= 1


def test_a_long_ranking_is_shortened_for_the_mcp_reply_and_keeps_the_best() -> None:
    """With 120 made-up targets, the cut fits the limit and keeps the best."""
    from wayfindinglib.models.planning.tonight_ranking import (
        CandidateStatus,
        RankedCandidate,
        RankedList,
        RankedUse,
        TonightRanking,
        TonightSkyModel,
    )

    now = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    candidates = [
        RankedCandidate(
            target_id=f"NGC {1000 + index}",
            use=RankedUse.IMAGING,
            status=CandidateStatus.RANKED,
            rank=index + 1,
            gain_per_hour_fraction=0.5 / (index + 1),
            reason="The hour copies the target's past frames at tonight's modeled sky. " * 3,
        )
        for index in range(120)
    ]
    ranking = TonightRanking(
        at=now,
        lists=[RankedList(use=RankedUse.IMAGING, gain_description="noise", candidates=candidates)],
        sky=TonightSkyModel(at=now),
    )
    assert reply_characters(ranking) > MAXIMUM_REPLY_CHARACTERS
    fitted = fit_reply_to_budget(ranking)
    assert reply_characters(fitted) <= MAXIMUM_REPLY_CHARACTERS
    kept = fitted.lists[0].candidates
    assert [candidate.target_id for candidate in kept] == [c.target_id for c in candidates[: len(kept)]]
    assert any("left out" in note for note in fitted.notes)
    assert fit_reply_to_budget(fitted) is fitted
    assert len(ranking.lists[0].candidates) == 120


def test_a_review_with_long_evidence_lists_is_cut_for_the_mcp_reply_budget() -> None:
    """Statements keep fewer references, count the lost ones, and all stay."""
    from wayfindinglib.models.session.night_quality import (
        GuidingNightSummary,
        NightQuality,
        NightTargetQuality,
    )

    statement = _busy_statement(700)
    entries = [
        NightTargetQuality(
            target_id=f"T{index}",
            goal="faint_point_source",
            frames_taken=700,
            frames_set_aside=0,
            captured_seconds=1.0,
            statements=[statement, statement],
            needs_attention=NeedsAttention(warning_count=2, worst_severity=Severity.ADVICE),
        )
        for index in range(6)
    ]
    review = NightQuality(
        night_id="2026-10-02",
        targets=entries,
        needs_attention=NeedsAttention(warning_count=12, worst_severity=Severity.ADVICE),
        guiding=GuidingNightSummary(available=False, reason="No guiding."),
    )
    assert reply_characters(review) > MAXIMUM_REPLY_CHARACTERS
    fitted = fit_reply_to_budget(review)
    assert reply_characters(fitted) <= MAXIMUM_REPLY_CHARACTERS
    for entry in fitted.targets:
        assert len(entry.statements) == 2
        assert len(entry.statements[0].evidence) + entry.statements[0].evidence_omitted_count == 700
    assert fitted.needs_attention == review.needs_attention
    assert any("cut to fit" in note for note in fitted.notes)
