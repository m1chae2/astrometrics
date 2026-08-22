"""Tool registry for the Wayfinding Library MCP Server.

Reuses astrometricslib's generic `ToolRegistry` dispatch machinery
and reflection engine with its own, separate registered-tools instance.
"""

from astrometricslib.mcp.reflection import register_astrometrics_tools
from astrometricslib.mcp.tool_registry import ToolRegistry

registry = ToolRegistry()


def get_wayfinder():  # ruff: ignore[missing-return-type-undocumented-public-function]
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


def register_wayfinder_reflected_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Dynamically register public Wayfinder high-level interface methods."""
    wayfinder = get_wayfinder()
    if not wayfinder:
        return

    branch_mapping = {
        "control": "observatory",
        "planning": "planning",
        "execution": "execution",
    }
    register_astrometrics_tools(registry, wayfinder, branch_mapping)


register_wayfinder_reflected_tools()
