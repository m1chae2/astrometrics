"""Purpose: Tests for the capability gap server and the refusal messages.

Description: An AI client reports what its tools cannot do through
``report_capability_gap``. These tests check the report rules, that a repeat
is counted instead of copied, that the store survives two writers, the
review command, and that every refusal tells the client to file a report.
"""

import asyncio
import json
from pathlib import Path

import pytest

from astrometricslib.mcp.profile import GAP_REPORT_GUIDANCE, refusal_message
from backend.mcp.gaps import __main__ as gap_server
from backend.mcp.gaps import review
from backend.mcp.gaps.gap_store import (
    MAXIMUM_TEXT_LENGTH,
    GapReportError,
    GapStore,
    validate_report,
)
from backend.mcp.tool_registry import ToolRegistry

VALID_REPORT = {
    "tier": "astrometricslib",
    "goal": "Compare the stack quality at a different rejection threshold",
    "tools_tried": [{"tool": "processing_trial_stack", "result": "Unknown tool"}],
    "why_insufficient": "No tool runs a stack without writing to the live stacks.",
}


@pytest.fixture
def store(tmp_path: Path) -> GapStore:
    """Make a gap store in a temporary folder.

    Returns
    -------
    store : `GapStore`
        An empty store.
    """
    return GapStore(tmp_path / "gaps.db")


def test_a_valid_report_is_saved_and_listed(store: GapStore) -> None:
    """A good report gets an id and shows up in the list."""
    saved = store.add_gap(validate_report(VALID_REPORT))
    assert saved == {"id": 1, "duplicate": False, "occurrences": 1}
    gaps = store.list_gaps()
    assert len(gaps) == 1
    assert gaps[0]["goal"] == VALID_REPORT["goal"]
    assert gaps[0]["status"] == "open"
    assert gaps[0]["tools_tried"][0]["tool"] == "processing_trial_stack"


def test_a_repeated_report_is_counted_not_copied(store: GapStore) -> None:
    """The same goal, with other case and punctuation, counts as a repeat."""
    store.add_gap(validate_report(VALID_REPORT))
    repeat = dict(VALID_REPORT, goal="compare the stack quality, at a different rejection threshold!")
    saved = store.add_gap(validate_report(repeat))
    assert saved["duplicate"] is True
    assert saved["occurrences"] == 2
    assert len(store.list_gaps()) == 1


def test_a_repeat_of_a_built_gap_is_a_new_report(store: GapStore) -> None:
    """Once a gap is built, the same goal can be reported again."""
    first = store.add_gap(validate_report(VALID_REPORT))
    store.set_status(first["id"], "built", "Shipped")
    saved = store.add_gap(validate_report(VALID_REPORT))
    assert saved["duplicate"] is False
    assert len(store.list_gaps()) == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"tier": "nowhere"}, "tier must be one of"),
        ({"goal": ""}, "Missing required"),
        ({"tools_tried": "none"}, "tools_tried must be a list"),
        ({"tools_tried": [{"result": "x"}]}, "needs a 'tool'"),
        ({"goal": "x" * (MAXIMUM_TEXT_LENGTH + 1)}, "longer than"),
        ({"proposed_tool": 5}, "must be text"),
        ({"category": "made-up"}, "category must be one of"),
    ],
)
def test_bad_reports_are_refused_with_a_reason(change: dict, message: str) -> None:
    """Each kind of bad input gets a plain message."""
    with pytest.raises(GapReportError, match=message):
        validate_report(dict(VALID_REPORT, **change), categories=("stars", "targets"))


def test_unknown_fields_are_dropped() -> None:
    """Extra fields do not reach the database."""
    clean = validate_report(dict(VALID_REPORT, status="built", id=99))
    assert "status" not in clean
    assert "id" not in clean


def test_list_filters_and_limits(store: GapStore) -> None:
    """The list can be filtered by status and tier, and is capped."""
    store.add_gap(validate_report(VALID_REPORT))
    store.add_gap(validate_report(dict(VALID_REPORT, tier="ui", goal="See the current view")))
    assert [gap["tier"] for gap in store.list_gaps(tier="ui")] == ["ui"]
    assert store.list_gaps(status="built") == []
    assert len(store.list_gaps(limit=1)) == 1
    with pytest.raises(GapReportError, match="status must be"):
        store.list_gaps(status="maybe")


def test_two_stores_can_write_to_one_file(tmp_path: Path) -> None:
    """Two AI sessions writing at once both get saved."""
    first, second = GapStore(tmp_path / "gaps.db"), GapStore(tmp_path / "gaps.db")
    first.add_gap(validate_report(VALID_REPORT))
    second.add_gap(validate_report(dict(VALID_REPORT, goal="A different goal")))
    assert len(first.list_gaps()) == 2


def test_the_server_tools_report_and_list(monkeypatch: pytest.MonkeyPatch, store: GapStore) -> None:
    """A report saves, tells the client to stop, and can be read back."""
    monkeypatch.setattr(gap_server, "store", store)
    result = gap_server.handle_call("report_capability_gap", dict(VALID_REPORT))
    assert result["id"] == 1
    assert "Stop here" in result["message"]
    listed = gap_server.handle_call("list_capability_gaps", {})
    assert listed["gaps"][0]["goal"] == VALID_REPORT["goal"]
    with pytest.raises(GapReportError, match="Missing required"):
        gap_server.handle_call("report_capability_gap", {"tier": "ui"})
    with pytest.raises(GapReportError, match="Unknown tool"):
        gap_server.handle_call("delete_everything", {})


def test_a_refused_call_is_an_mcp_error_result(monkeypatch: pytest.MonkeyPatch, store: GapStore) -> None:
    """A bad report comes back flagged isError with code invalid_argument."""
    monkeypatch.setattr(gap_server, "store", store)
    result = asyncio.run(gap_server.call_tool("report_capability_gap", {"tier": "ui"}))
    assert result.isError is True
    assert result.content[0].text.startswith("invalid_argument: Missing required")


def test_the_server_has_no_tool_that_changes_a_status() -> None:
    """The AI can report and list, nothing more."""
    names = {tool.name for tool in asyncio.run(gap_server.list_tools())}
    assert names == {"report_capability_gap", "list_capability_gaps"}


def test_review_command_lists_shows_and_sets_status(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """A person can list, read and resolve a report."""
    monkeypatch.setenv("ASTROMETRICS_GAP_DATABASE", str(tmp_path / "review.db"))
    GapStore().add_gap(validate_report(VALID_REPORT))
    assert review.main(["list"]) == 0
    assert "Compare the stack quality" in capsys.readouterr().out
    assert review.main(["show", "1"]) == 0
    assert "Why they fell short" in capsys.readouterr().out
    assert review.main(["set-status", "1", "accepted", "--note", "Design together"]) == 0
    assert GapStore().get_gap(1)["status"] == "accepted"
    assert review.main(["show", "99"]) == 1


def test_brief_lists_overlapping_tools_and_the_steps(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """The brief names overlapping tools and gives the steps."""
    monkeypatch.setenv("ASTROMETRICS_GAP_DATABASE", str(tmp_path / "brief.db"))
    GapStore().add_gap(validate_report(VALID_REPORT))
    assert review.main(["brief", "1"]) == 0
    output = capsys.readouterr().out
    assert "Existing tools that may overlap" in output
    assert "diagnostics_stack_quality" in output
    assert "Steps:" in output
    assert "9. Update the README" in output
    assert review.main(["brief", "99"]) == 1


def test_refusal_message_tells_the_client_to_file_a_report() -> None:
    """Every refusal starts with the old error and points to the gap tool."""
    withheld = refusal_message("observatory_park", "class 'actuate' is not offered")
    assert withheld.startswith("Error: Unknown tool observatory_park.")
    assert "class 'actuate'" in withheld
    assert "report_capability_gap" in withheld
    assert "report_capability_gap" in refusal_message("nothing_like_it")
    assert "report_capability_gap" in GAP_REPORT_GUIDANCE


def test_a_withheld_backend_tool_call_returns_the_refusal(tmp_path: Path) -> None:
    """Calling a withheld tool explains why and points to the gap server."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"tools": {"write_it": {"tool_class": "change-data", "disposition": "keep"}}})
    )
    registry = ToolRegistry()
    registry.register("write_it", "A test tool.")(lambda: "done")
    registry.apply_profile(manifest, "investigator")
    text = asyncio.run(registry.execute("write_it", {}))[0].text
    assert "Unknown tool write_it" in text
    assert "change-data" in text
    assert "report_capability_gap" in text
