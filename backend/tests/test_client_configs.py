"""Purpose: Tests for the generated MCP client configurations.

Description: The Claude and Gemini configs, and the investigator agent, are
written by ``build/mcp/generate_client_configs.py`` from the tool manifests.
These tests check that the committed files match what the script would
write, and that the companion gets no shell, no file access, no UI test
server and no withheld tool. No server config chooses a profile, because
there is only one.
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
    # Folding the narrow `control` reads into one read per child left about
    # 60 tools, and folding the astrometricslib ones about 45; the check
    # guards against an empty or truncated list.
    assert len(tools) > 40
    assert all(tool.startswith("mcp__") for tool in tools)
    assert not set(tools) & set(BUILT_IN_TOOLS)
    for forbidden in (
        "electron_run_python",
        "backend_call_rpc",
        "observatory_mount_park",
        "observatory_mount_slew",
        "ui_run_tests",
        "target_save",
        "star_update",
    ):
        assert not any(tool.endswith(f"__{forbidden}") for tool in tools), forbidden


def test_investigator_agent_includes_the_gap_tools() -> None:
    """The agent can report a gap and check for an existing report."""
    tools = _agent_tools()
    assert "mcp__astrometrics-gaps__report_capability_gap" in tools
    assert "mcp__astrometrics-gaps__list_capability_gaps" in tools


def test_companion_config_leaves_out_the_ui_server() -> None:
    """The companion leaves out the UI server and chooses no profile."""
    config = json.loads((PROJECT_ROOT / ".claude" / "companion.mcp.json").read_text(encoding="utf-8"))
    servers = config["mcpServers"]
    assert set(servers) == {
        "astrometricslib-core",
        "wayfindinglib-core",
        "astrometrics-backend",
        "astrometrics-gaps",
    }
    for name, entry in servers.items():
        assert "ASTROMETRICS_MCP_PROFILE" not in entry["env"], name


def test_claude_config_adds_the_ui_server_and_chooses_no_profile() -> None:
    """A normal session gets the UI server, and no entry chooses a profile."""
    servers = json.loads((PROJECT_ROOT / ".mcp.json").read_text(encoding="utf-8"))["mcpServers"]
    assert "astrometrics-ui" in servers
    assert "astrometrics-gaps" in servers
    for name, entry in servers.items():
        assert "ASTROMETRICS_MCP_PROFILE" not in entry["env"], name


def test_python_servers_start_through_the_launcher_not_a_system_specific_path() -> None:
    """Python servers start through the Node launcher, on Windows and Linux."""
    for root_text in (generator.RELATIVE_ROOT, generator.EXAMPLE_ROOT, generator.CLAUDE_ROOT_VARIABLE):
        servers = generator.build_claude_config(companion=False, root_text=root_text)["mcpServers"]
        for spec in generator.SERVERS:
            if not spec.python_module:
                continue
            entry = servers[spec.name]
            assert entry["command"] == "node", spec.name
            assert entry["args"][1] == spec.python_module, spec.name
            assert entry["args"][0].endswith(generator.PYTHON_LAUNCHER), spec.name
            assert "bin/python" not in json.dumps(entry), spec.name
            assert "/tmp" not in json.dumps(entry), spec.name
    assert (PROJECT_ROOT / generator.PYTHON_LAUNCHER).is_file()


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


def test_gemini_settings_load_the_context_file_from_the_agent_folder() -> None:
    """Gemini is pointed at ``.agent`` so it finds ``.agent/GEMINI.md``."""
    context = generator.build_gemini_settings()["context"]
    assert context["includeDirectories"] == [str(generator.PROJECT_ROOT / ".agent")]
    assert context["loadMemoryFromIncludeDirectories"] is True
    assert (generator.PROJECT_ROOT / ".agent" / "GEMINI.md").is_file()
