"""Purpose: Tests for the wayfindinglib-core server's tool profile.

Description: The server must offer an AI client only read-only tools. These
tests check that the committed manifest covers every tool the server
registers, and that the investigator profile offers no tool that commands a
device, writes data, or is marked dropped.
"""

from mcp_servers.common.profile import load_manifest
from mcp_servers.common.tool_registry import ToolRegistry
from mcp_servers.wayfinding_core.definition import MANIFEST_PATH


def _investigator_copy(registry: ToolRegistry) -> ToolRegistry:
    """Copy the registry and apply the investigator profile to the copy.

    Parameters
    ----------
    registry : `ToolRegistry`
        The full registry.

    Returns
    -------
    copy : `ToolRegistry`
        A registry holding only the tools the investigator may use.
    """
    copy = ToolRegistry()
    copy.tools = dict(registry.tools)
    copy.apply_profile(MANIFEST_PATH, "investigator")
    return copy


def test_manifest_covers_every_registered_tool(registry: ToolRegistry) -> None:
    """A new public method is not served until it is in the manifest."""
    manifest = load_manifest(MANIFEST_PATH)
    assert manifest is not None
    registered = set(registry.tools)
    assert registered - set(manifest["tools"]) == set(), "Tools missing from the manifest"
    assert set(manifest["tools"]) - registered == set(), "Manifest entries with no tool"


def test_investigator_profile_offers_no_device_command_or_writer(registry: ToolRegistry) -> None:
    """Hardware commands, writers and dropped tools are all withheld."""
    copy = _investigator_copy(registry)
    manifest = load_manifest(MANIFEST_PATH)
    assert copy.tools
    for name in copy.tools:
        entry = manifest["tools"][name]
        assert entry["tool_class"] in ("observe", "compute", "ingest", "process"), name
        assert entry["category"] != "observatory-control", name
    for name in ("observatory_mount_slew", "observatory_mount_park", "observatory_imaging_capture_image"):
        assert name not in copy.tools


def test_tools_with_a_known_hazard_are_blocked_for_now(registry: ToolRegistry) -> None:
    """A read-only tool with an interim block is not offered."""
    copy = _investigator_copy(registry)
    assert "observatory_mount_status" not in copy.tools
    assert "observatory_mount_status" in copy.withheld
