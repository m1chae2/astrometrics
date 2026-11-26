"""Purpose: Say what the astrometricslib-core MCP server offers.

Description: The server reflects the public methods of `Astrometrics`: each
method of a sub-API becomes a tool named ``<prefix>_<method>``, such as
``target_query`` for ``Astrometrics.targets.query``. It also offers one
developer tool, the model contract check. The server's manifest,
``tool_manifest.json`` in this folder, decides which tools a profile may use.
"""

from pathlib import Path

from astrometricslib import Astrometrics
from mcp_servers.common.app_job_tools import APP_JOB_TOOLS
from mcp_servers.common.reflection import ClientLimits, register_reflected_tools
from mcp_servers.common.tool_registry import ToolRegistry
from mcp_servers.devtools import contract_validator

SERVER_NAME = "astrometricslib-core"
"""The server's name in the client configurations."""

MANIFEST_PATH = Path(__file__).resolve().parent / "tool_manifest.json"
"""The reviewed list of this server's tools."""

BRANCH_PREFIXES = {
    "targets": "target",
    "stars": "star",
    "processing": "processing",
    "quality": "quality",
    "quality.blocks": "quality_blocks",
    "processing.calibration": "calibration",
    "visualization": "visualization",
    "jobs": "jobs",
}
"""Sub-API of `Astrometrics` -> the prefix of its tools' names."""

CLIENT_LIMITS = {
    "visualization_render_fits": ClientLimits(
        hidden_parameters=("pixels", "figsize"), withheld_choices={"kind": ("figure",)}
    ),
}
"""What an AI client may not send to a tool. `render_fits` draws a
Matplotlib figure for a script or a notebook, and an array of pixels cannot
travel in JSON, so a client gets the PNG and the data URL kinds only."""


def build_registry() -> ToolRegistry:
    """Build the registry with every tool this server can offer.

    Returns
    -------
    registry : `ToolRegistry`
        All tools, before a profile removes any. Path arguments are checked
        against the library folders. The tools in `APP_JOB_TOOLS` are left
        out: the astrometrics-backend server serves them, so the app runs
        their work as its own job. `CLIENT_LIMITS` narrows what a client
        may send to some tools.
    """
    registry = ToolRegistry(sandbox_paths=True)
    register_reflected_tools(
        registry, Astrometrics(), BRANCH_PREFIXES, skip=APP_JOB_TOOLS, limits=CLIENT_LIMITS
    )
    contract_validator.register(registry)
    return registry
