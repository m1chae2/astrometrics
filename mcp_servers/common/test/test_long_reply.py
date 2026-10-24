"""Tests that a reply too long for the size limit stays valid JSON.

Cutting the text of a JSON list part way through leaves text that does not
parse. These tests check that a long list keeps whole items and says how
many were left out, and that a short reply is unchanged.
"""

import json
from datetime import UTC, datetime

from mcp_servers.common.tool_registry import MAXIMUM_REPLY_BYTES, result_content
from wayfindinglib import TargetDatasetReport


def test_a_long_list_keeps_whole_items_and_notes_the_rest() -> None:
    """A list over the limit sends whole items as JSON, then a note."""
    items = [{"name": f"metric_{index}", "text": "x" * 500} for index in range(200)]
    content = result_content(items)
    kept = json.loads(content[0].text)
    assert 0 < len(kept) < len(items)
    assert kept == items[: len(kept)]
    assert len(content[0].text) <= MAXIMUM_REPLY_BYTES
    assert f"first {len(kept)} of {len(items)}" in content[1].text


def test_a_short_list_is_sent_whole() -> None:
    """A list under the limit comes back as one JSON text with every item."""
    items = [{"name": "one"}, {"name": "two"}]
    content = result_content(items)
    assert len(content) == 1
    assert json.loads(content[0].text) == items


def _busy_report(statement_count: int, reference_count: int) -> TargetDatasetReport:
    """Build a target report whose statements each cite many rows.

    Returns
    -------
    report : `TargetDatasetReport`
        A report whose statements cite row ids 1 to ``reference_count``, in
        that order (the order puts the most extreme reference first).
    """
    statement = {
        "rule": "trailed_stars",
        "kind": "warning",
        "severity": "advice",
        "actionClass": "session_procedure",
        "message": "Stars are trailed.",
        "confidence": "design_estimate",
        "evidence": [{"rowId": row} for row in range(1, reference_count + 1)],
    }
    return TargetDatasetReport.model_validate({
        "targetId": "T",
        "statements": [statement] * statement_count,
        "needsAttention": {"warningCount": statement_count, "worstSeverity": "advice"},
        "generatedAt": datetime(2026, 10, 24, tzinfo=UTC),
    })


def test_a_busy_report_is_trimmed_to_valid_json_that_keeps_the_most_extreme_references() -> None:
    """The MCP reply is whole JSON under the limit.

    Every statement is kept, and the first references are kept.
    """
    report = _busy_report(30, 900)
    assert len(json.dumps(report.model_dump(), separators=(",", ":"), default=str)) > MAXIMUM_REPLY_BYTES
    content = result_content(report)
    assert len(content) == 1
    assert len(content[0].text) <= MAXIMUM_REPLY_BYTES
    reply = json.loads(content[0].text)
    assert len(reply["statements"]) == 30
    for statement in reply["statements"]:
        kept = [reference["row_id"] for reference in statement["evidence"]]
        assert kept == list(range(1, len(kept) + 1))
        assert len(kept) + statement["evidence_omitted_count"] == 900
    # The library's own report is still whole.
    assert len(report.statements[0].evidence) == 900
