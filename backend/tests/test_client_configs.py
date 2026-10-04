"""Purpose: Tests for the generated MCP client configurations.

Description: The Claude and Gemini configs, and the read-only investigator
agent, are written by ``build/mcp/generate_client_configs.py`` from the tool
manifests. These tests check that the committed files match what the script
would write, and that the read-only companion gets no shell, no file access,
no developer tools and no withheld tool.
"""

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT_PATH = PROJECT_ROOT / "build" / "mcp" / "generate_client_configs.py"


def _load_generator() -> ModuleType:
    """Import the generator script, which is not in a package.

    Returns
    -------
    module : `types.ModuleType`
        The loaded script.
    """
    spec = importlib.util.spec_from_file_location("generate_client_configs", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generator = _load_generator()

BUILT_IN_TOOLS = (
    "Bash",
    "Read",
    "Write",
    "Edit",
    "NotebookEdit",
    "Grep",
    "Glob",
    "WebFetch",
    "WebSearch",
    "Agent",
)


def _agent_tools() -> list[str]:
    """Read the tool names from the generated investigator agent.

    Returns
    -------
    tools : `list` [`str`]
        The entries of the ``tools:`` list.
    """
    text = (PROJECT_ROOT / ".claude" / "agents" / "investigator.md").read_text(encoding="utf-8")
    return [line[4:] for line in text.splitlines() if line.startswith("  - ")]


@pytest.mark.parametrize("relative_path", sorted(generator.render_all()))
def test_committed_file_matches_the_generator(relative_path: str) -> None:
    """A generated file is current, so a manifest change is not missed."""
    path = PROJECT_ROOT / relative_path
    if relative_path == ".gemini/settings.json" and not path.is_file():
        pytest.skip("The Gemini settings file is per-clone and may not exist.")
    if relative_path == "mcp_servers.json" and not path.is_file():
        pytest.skip("mcp_servers.json is per-clone and may not exist.")
    assert path.read_text(encoding="utf-8") == generator.render_all()[relative_path]


def test_investigator_agent_has_only_offered_mcp_tools() -> None:
    """The agent lists offered MCP tools only, and no built-in tool."""
    tools = _agent_tools()
    assert len(tools) > 60
    assert all(tool.startswith("mcp__") for tool in tools)
    assert not set(tools) & set(BUILT_IN_TOOLS)
    for forbidden in (
        "electron_run_python",
        "backend_call_rpc",
        "observatory_park",
        "observatory_slew_to_target",
        "ui_run_tests",
        "typegen_contract_validator",
        "target_save",
        "star_list_objects",
    ):
        assert not any(tool.endswith(f"__{forbidden}") for tool in tools), forbidden


def test_investigator_agent_includes_the_gap_tools() -> None:
    """The agent can report a gap and check for an existing report."""
    tools = _agent_tools()
    assert "mcp__astrometrics-gaps__report_capability_gap" in tools
    assert "mcp__astrometrics-gaps__list_capability_gaps" in tools


def test_companion_config_leaves_out_the_ui_server_and_uses_the_read_only_profile() -> None:
    """Companion servers are read-only and use the investigator profile."""
    config = json.loads((PROJECT_ROOT / ".claude" / "companion.mcp.json").read_text(encoding="utf-8"))
    servers = config["mcpServers"]
    assert set(servers) == {
        "astrometricslib-core",
        "wayfindinglib-core",
        "astrometrics-backend",
        "astrometrics-gaps",
    }
    for name, entry in servers.items():
        if name != "astrometrics-gaps":
            assert entry["env"]["ASTROMETRICS_MCP_PROFILE"] == "investigator", name


def test_developer_config_gives_developer_tools_only_where_they_exist() -> None:
    """A developer session gets the UI server and developer profile."""
    servers = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert servers["astrometrics-ui"]["env"]["ASTROMETRICS_MCP_PROFILE"] == "developer"
    assert servers["astrometricslib-core"]["env"]["ASTROMETRICS_MCP_PROFILE"] == "developer"
    assert "astrometrics-gaps" in servers


def test_gemini_settings_list_the_same_tools_and_trust_none() -> None:
    """Gemini gets the same allowlist as Claude, and trusts no server."""
    settings = generator.build_gemini_settings()
    gemini_tools = {
        f"mcp__{name}__{tool}"
        for name, entry in settings["mcpServers"].items()
        for tool in entry["includeTools"]
    }
    assert gemini_tools == set(_agent_tools())
    assert all(entry["trust"] is False for entry in settings["mcpServers"].values())


def test_every_investigator_tool_is_actually_served() -> None:
    """Every allowlisted tool exists in its manifest."""
    for spec in generator.SERVERS:
        if not spec.manifest:
            continue
        manifest = json.loads((PROJECT_ROOT / spec.manifest).read_text(encoding="utf-8"))
        for tool in generator.investigator_tools(spec):
            assert tool in manifest["tools"], tool


def test_gemini_settings_exclude_the_built_in_shell_file_and_web_tools() -> None:
    """Gemini's own shell, file and web tools are removed by name."""
    tools = generator.build_gemini_settings()["tools"]
    assert {"run_shell_command", "write_file", "replace", "read_file", "web_fetch"} <= set(tools["exclude"])
    assert tools["shell"]["enableInteractiveShell"] is False
