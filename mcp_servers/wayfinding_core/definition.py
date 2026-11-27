"""Purpose: Say what the wayfindinglib-core MCP server offers.

Description: The server reflects the public methods of `Wayfinder`. The
shared adapter parts turn the client's ids into objects and its JSON into
input models. ``control`` itself holds only the driver properties, which are
not tools. Its operations live on six children, so each child gets its own
prefix, such as ``observatory_mount_park``. ``planning`` holds four objects,
each with its own prefix, such as ``planning_sessions_get``. ``review`` is a
sub-API of its own, with the prefix ``review``, such as ``review_query``.
The server's manifest, ``tool_manifest.json`` in this folder, decides which
tools a profile may use.
"""

from pathlib import Path

from mcp_servers.common.app_job_tools import APP_JOB_TOOLS
from mcp_servers.common.reflection import ClientLimits, register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry
from wayfindinglib import Wayfinder

SERVER_NAME = "wayfindinglib-core"
"""The server's name in the client configurations."""

MANIFEST_PATH = Path(__file__).resolve().parent / "tool_manifest.json"
"""The reviewed list of this server's tools."""

CONTROL_CHILDREN = ("mount", "imaging", "guiding", "remote", "safety", "equipment")
"""The children of `Wayfinder.control` that hold its operations."""

BRANCH_PREFIXES = {
    **{f"control.{child}": f"observatory_{child}" for child in CONTROL_CHILDREN},
    "planning.sessions": "planning_sessions",
    "planning.packages": "planning_packages",
    "planning.sky": "planning_sky",
    "planning.catalogs": "planning_catalogs",
    "execution": "execution",
    "review": "review",
}
"""Sub-API of `Wayfinder` -> the prefix of its tools' names."""

SOURCES_DEFAULT_STARS = 100
"""How many stars one ``planning_catalogs_query_sources`` call lists when the
client gives no limit. The library lists every match by default."""

SOURCES_MAXIMUM_STARS = 300
"""Most stars one ``planning_catalogs_query_sources`` call lists for an AI
client."""

CLIENT_LIMITS = {
    "planning_catalogs_query_sources": ClientLimits(
        maximums={"limit": SOURCES_MAXIMUM_STARS}, defaults={"limit": SOURCES_DEFAULT_STARS}
    ),
}
"""By tool name, the reply caps an AI client gets."""


def build_registry() -> ToolRegistry:
    """Build the registry with every tool this server can offer.

    Returns
    -------
    registry : `ToolRegistry`
        All tools, before a profile removes any. Path arguments are checked
        against the library folders. The tools in `APP_JOB_TOOLS` are left
        out: the astrometrics-backend server serves them, so the app runs
        their work as its own job.
    """
    registry = ToolRegistry(sandbox_paths=True)
    register_reflected_tools(registry, Wayfinder(), BRANCH_PREFIXES, skip=APP_JOB_TOOLS, limits=CLIENT_LIMITS)
    return registry
