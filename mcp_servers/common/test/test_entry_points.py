"""Purpose: Check that each library MCP server starts and lists its tools.

Description: ``python -m mcp_servers.astrometrics_core`` and
``python -m mcp_servers.wayfinding_core`` run the ``main`` function of the
package's ``__main__`` module. These tests call that function in this
process, with the stdin/stdout loop replaced by a stand-in. The stand-in
records what the server would have served, so the test sees the server name
and the tool list without starting a subprocess that waits for a client.
"""

import importlib
from typing import Any

import pytest

SERVERS = [
    ("mcp_servers.astrometrics_core.__main__", "astrometrics-core", "diagnostics_frame_quality"),
    ("mcp_servers.wayfinding_core.__main__", "wayfindinglib-core", "planning_get_visibility"),
]
"""Entry module, server name it announces, and one tool it must offer."""


@pytest.mark.parametrize(("module_name", "server_name", "expected_tool"), SERVERS)
def test_entry_point_builds_its_registry_and_lists_tools(
    module_name: str, server_name: str, expected_tool: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``main()`` builds the registry, applies the profile and serves it."""
    entry_module = importlib.import_module(module_name)
    served: dict[str, Any] = {}

    def fake_run_server(name: str, instructions: str, tools: Any, **options: Any) -> None:
        """Record the server's arguments instead of reading stdin."""
        served.update(name=name, instructions=instructions, tools=tools, options=options)

    monkeypatch.setenv("ASTROMETRICS_MCP_PROFILE", "investigator")
    monkeypatch.setattr(entry_module, "run_server", fake_run_server)
    monkeypatch.setattr(entry_module, "configure_logging", lambda *args, **kwargs: None)

    entry_module.main()

    assert served["name"] == server_name
    assert served["instructions"]
    tool_names = {tool.name for tool in served["tools"].get_tool_definitions()}
    assert expected_tool in tool_names
    assert len(tool_names) > 10
