"""Purpose: Tests for the astrometrics-backend MCP server's tool profile.

Description: The backend server must not offer an AI client the code runner
or the generic RPC call. These tests check that the committed manifest covers
every tool the server registers, and that the profile offers
only the four reviewed backend tools.
"""

from mcp_servers.backend.definition import MANIFEST_PATH, build_registry
from mcp_servers.common.profile import load_manifest


def test_backend_manifest_covers_every_registered_tool() -> None:
    """A new backend tool is not served until it is in the manifest."""
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest is not None
    assert set(build_registry().tools) == set(manifest["tools"])


def test_profile_gets_only_the_reviewed_backend_tools() -> None:
    """The code runner and the generic RPC call are withheld."""
    registry = build_registry()
    registry.apply_profile(MANIFEST_PATH)
    served = set(registry.tools)
    assert served == {
        "app_status",
        "app_controls",
        "docs_get",
        "terminal_add_script",
        "terminal_get_recent_runs",
    }
    assert "electron_run_python" in registry.withheld
    assert "backend_call_rpc" in registry.withheld
