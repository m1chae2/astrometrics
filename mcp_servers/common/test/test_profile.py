"""Purpose: Tests for the MCP tool profiles.

Description: A profile limits which tools an MCP server offers. These tests
check the rules, the failure cases (missing manifest, unknown tool, unknown
profile). Each server's own tests check that its committed manifest covers
every tool it registers, so a new tool cannot appear without a review.
"""

import asyncio
import json
from pathlib import Path

import pytest

from mcp_servers.common.profile import (
    DEFAULT_PROFILE,
    PROFILE_ENVIRONMENT_VARIABLE,
    current_profile,
    find_withheld_tools,
    load_manifest,
    withheld_reason,
)
from mcp_servers.common.tool_registry import ToolRegistry


def _entry(tool_class: str, disposition: str = "keep", interim_block: str = "") -> dict[str, str]:
    """Build a manifest entry for a test.

    Returns
    -------
    entry : `dict` [`str`, `str`]
        An entry with the class, disposition and interim block.
    """
    return {"tool_class": tool_class, "disposition": disposition, "interim_block": interim_block}


@pytest.mark.parametrize(
    ("entry", "profile", "offered"),
    [
        (_entry("observe"), "investigator", True),
        (_entry("compute", "keep"), "investigator", True),
        (_entry("ingest", "keep"), "investigator", True),
        (_entry("change-data"), "investigator", False),
        (_entry("actuate"), "investigator", False),
        (_entry("unrestricted"), "investigator", False),
        (_entry("develop", "keep"), "investigator", False),
        (_entry("develop", "keep"), "developer", True),
        (_entry("change-data"), "developer", False),
        (_entry("observe", "drop"), "investigator", False),
        (_entry("observe", "withhold"), "investigator", False),
        (_entry("observe", "fix"), "investigator", False),
        (_entry("observe", "undecided"), "investigator", False),
        (_entry("observe", interim_block="Loads the whole catalog."), "investigator", False),
        (None, "investigator", False),
    ],
)
def test_withheld_reason_follows_class_and_disposition(
    entry: dict | None, profile: str, offered: bool
) -> None:
    """A tool is offered only for a served class and disposition."""
    assert (withheld_reason(entry, profile) is None) is offered


def test_withheld_reason_names_the_interim_block() -> None:
    """The reason repeats why a read-only tool is blocked for now."""
    reason = withheld_reason(_entry("observe", interim_block="Loads the whole catalog."), "investigator")
    assert "Loads the whole catalog." in reason


def test_missing_manifest_withholds_every_tool(tmp_path: Path) -> None:
    """With no manifest, nothing is offered."""
    assert load_manifest(tmp_path / "absent.json") is None
    withheld = find_withheld_tools(["a", "b"], None, "investigator")
    assert set(withheld) == {"a", "b"}


def test_unreadable_manifest_is_treated_as_missing(tmp_path: Path) -> None:
    """A file that is not a manifest is rejected, not guessed at."""
    path = tmp_path / "manifest.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert load_manifest(path) is None
    path.write_text("not json", encoding="utf-8")
    assert load_manifest(path) is None


def test_tool_missing_from_the_manifest_is_withheld() -> None:
    """A new tool stays hidden until someone adds it to the manifest."""
    manifest = {"tools": {"known": _entry("observe")}}
    withheld = find_withheld_tools(["known", "new_tool"], manifest, "investigator")
    assert withheld == {"new_tool": "not in the manifest"}


def test_current_profile_uses_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The variable picks a known profile; anything else falls back."""
    monkeypatch.delenv(PROFILE_ENVIRONMENT_VARIABLE, raising=False)
    assert current_profile() == DEFAULT_PROFILE
    monkeypatch.setenv(PROFILE_ENVIRONMENT_VARIABLE, "developer")
    assert current_profile() == "developer"
    monkeypatch.setenv(PROFILE_ENVIRONMENT_VARIABLE, "root")
    assert current_profile() == DEFAULT_PROFILE


def test_apply_profile_removes_tools_and_calls_to_them_fail(tmp_path: Path) -> None:
    """A withheld tool is unlisted, and calling it fails."""
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"tools": {"read_it": _entry("observe"), "write_it": _entry("change-data")}}),
        encoding="utf-8",
    )
    test_registry = ToolRegistry()
    for name in ("read_it", "write_it", "unlisted"):
        test_registry.register(name, "A test tool.")(lambda: "done")
    reasons = test_registry.apply_profile(manifest, "investigator")
    assert set(test_registry.tools) == {"read_it"}
    assert set(test_registry.withheld) == {"write_it", "unlisted"}
    assert set(reasons) == {"write_it", "unlisted"}
    result = asyncio.run(test_registry.execute("write_it", {}))
    assert "Unknown tool" in result[0].text
    assert "change-data" in result[0].text
    assert "report_capability_gap" in result[0].text


def test_a_dropped_tool_is_withheld_and_its_note_is_the_reason() -> None:
    """A dropped tool is withheld, and the reason quotes its note."""
    entry = {"tool_class": "compute", "disposition": "drop", "note": "Replaced by observatory_history_query."}
    assert withheld_reason(entry, "investigator") == "Replaced by observatory_history_query"
