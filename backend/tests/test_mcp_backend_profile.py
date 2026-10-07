"""Purpose: Tests for the backend and UI MCP servers' tool profiles.

Description: The backend server must not offer an AI client the code runner
or the generic RPC call. These tests check that the committed manifests cover
every tool each server declares, and that the investigator profile offers
only the four reviewed backend tools.
"""

import json
from pathlib import Path

from backend.mcp.tool_inventory import collect_ui_server_tools
from backend.mcp.tool_registry import ToolRegistry, registry
from mcp_servers.common.profile import load_manifest

BACKEND_MANIFEST = Path(__file__).resolve().parent.parent / "mcp" / "tool_manifest.json"
UI_MANIFEST = Path(__file__).resolve().parent.parent.parent / "ui" / "mcp" / "tool_manifest.json"


def _investigator_registry() -> ToolRegistry:
    """Apply the investigator profile to a copy of the backend registry.

    Returns
    -------
    copy : `ToolRegistry`
        A registry with the profile applied. The real registry is left alone.
    """
    copy = ToolRegistry()
    copy.tools = dict(registry.tools)
    copy.apply_profile(BACKEND_MANIFEST, "investigator")
    return copy


def test_backend_manifest_covers_every_registered_tool() -> None:
    """A new backend tool is not served until it is in the manifest."""
    manifest = load_manifest(BACKEND_MANIFEST)
    assert manifest is not None
    assert set(registry.tools) == set(manifest["tools"])


def test_investigator_gets_only_the_reviewed_backend_tools() -> None:
    """The code runner and the generic RPC call are withheld."""
    served = set(_investigator_registry().tools)
    assert served == {"app_status", "app_controls", "docs_get"}
    assert "electron_run_python" not in served
    assert "backend_call_rpc" not in served


def test_pause_and_resume_are_closed_to_the_investigator() -> None:
    """Pausing jobs is not read-only, so it stays withheld with a reason."""
    copy = _investigator_registry()
    for name in ("ui_pause_pipelines", "ui_resume_pipelines"):
        assert name in copy.withheld


def test_ui_manifest_covers_every_declared_ui_tool() -> None:
    """The UI server's manifest names exactly the tools its source declares."""
    declared = {record.name for record in collect_ui_server_tools()}
    manifest = json.loads(UI_MANIFEST.read_text(encoding="utf-8"))
    assert set(manifest["tools"]) == declared
    assert {entry["tool_class"] for entry in manifest["tools"].values()} == {"develop"}
