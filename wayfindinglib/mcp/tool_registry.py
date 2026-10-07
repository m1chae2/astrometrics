"""Purpose: Tool registry for the Wayfinding Library MCP server.

Description: Reuses astrometricslib's generic `ToolRegistry` dispatch
machinery and reflection engine with its own, separate registered-tools
instance. The tools pass the client's arguments to the `Wayfinder` methods
as they are: the methods accept target ids, ISO times and position
dictionaries themselves.
"""

from typing import Any

from astrometricslib.mcp.reflection import register_astrometrics_tools
from astrometricslib.mcp.tool_registry import ToolRegistry

registry = ToolRegistry()


def get_wayfinder() -> Any:
    """Fetch the Wayfinder high-level interface API from the library.

    Returns
    -------
    astrometrics : `wayfindinglib.Wayfinder` or `None`
        The Wayfinder high-level interface instance, or `None` if the
        library could not be imported.
    """
    try:
        from wayfindinglib import Wayfinder

        return Wayfinder()
    except ImportError:
        return None


def register_wayfinder_reflected_tools() -> None:
    """Dynamically register public Wayfinder high-level interface methods."""
    wayfinder = get_wayfinder()
    if not wayfinder:
        return

    # `control` itself holds only the driver properties, which are not
    # tools. Its operations live on seven children, so each child gets its
    # own prefix, such as `observatory_mount_park`.
    branch_mapping = {
        **{
            f"control.{child}": f"observatory_{child}"
            for child in ("mount", "imaging", "guiding", "remote", "history", "safety", "equipment")
        },
        "planning": "planning",
        "execution": "execution",
    }
    register_astrometrics_tools(registry, wayfinder, branch_mapping)


register_wayfinder_reflected_tools()
