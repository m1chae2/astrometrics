"""Purpose: Say what the wayfindinglib-core MCP server offers.

Description: The server reflects the public methods of `Wayfinder`. The
tools pass the client's arguments to the methods as they are: the methods
accept target ids, ISO times and position dictionaries themselves.
``control`` itself holds only the driver properties, which are not tools. Its
operations live on seven children, so each child gets its own prefix, such
as ``observatory_mount_park``. The server's manifest, ``tool_manifest.json``
in this folder, decides which tools a profile may use.
"""

from pathlib import Path

from mcp_servers.common.reflection import register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry
from wayfindinglib import Wayfinder

SERVER_NAME = "wayfindinglib-core"
"""The server's name in the client configurations."""

MANIFEST_PATH = Path(__file__).resolve().parent / "tool_manifest.json"
"""The reviewed list of this server's tools."""

CONTROL_CHILDREN = ("mount", "imaging", "guiding", "remote", "history", "safety", "equipment")
"""The children of `Wayfinder.control` that hold its operations."""

BRANCH_PREFIXES = {
    **{f"control.{child}": f"observatory_{child}" for child in CONTROL_CHILDREN},
    "planning": "planning",
    "execution": "execution",
}
"""Sub-API of `Wayfinder` -> the prefix of its tools' names."""


def build_registry() -> ToolRegistry:
    """Build the registry with every tool this server can offer.

    Returns
    -------
    registry : `ToolRegistry`
        All tools, before a profile removes any. Path arguments are checked
        against the library folders.
    """
    registry = ToolRegistry(sandbox_paths=True)
    register_reflected_tools(registry, Wayfinder(), BRANCH_PREFIXES)
    return registry
