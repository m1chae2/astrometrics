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
    "targets.frames": "target_frames",
    "stars": "star",
    "processing": "processing",
    "processing.stacking": "processing_stacking",
    "processing.enhancement": "processing_enhancement",
    "processing.astrometry": "processing_astrometry",
    "processing.photometry": "processing_photometry",
    "processing.spectroscopy": "processing_spectroscopy",
    "processing.asteroids": "processing_asteroids",
    "quality": "quality",
    "quality.blocks": "quality_blocks",
    "calibration": "calibration",
    "calibration.frames": "calibration_frames",
    "visualization": "visualization",
    "jobs": "jobs",
    "observatories.configurations": "observatory_configuration",
}
"""Sub-API of `Astrometrics` -> the prefix of its tools' names."""

STAR_QUERY_DEFAULT_STARS = 50
"""How many stars one ``star_query`` call lists when the client gives no
limit. The library lists every match by default."""

STAR_QUERY_MAXIMUM_STARS = 200
"""Most stars one ``star_query`` call lists for an AI client."""

JOBS_QUERY_DEFAULT_JOBS = 20
"""How many jobs one ``jobs_query`` call lists when the client gives no
limit. The library lists every match by default."""

JOBS_QUERY_MAXIMUM_JOBS = 50
"""Most jobs one ``jobs_query`` call lists for an AI client."""

JOBS_LOG_MAXIMUM_LINES = 200
"""Most log lines one ``jobs_get_log`` call returns to an AI client."""

RENDER_DEFAULT_SIDE = 1200
"""Longest side of a ``visualization_render`` picture when the client gives
none, in pixels. The library's default (2000) suits the app's viewer; a
smaller picture keeps an AI reply light."""

CLIENT_LIMITS = {
    "visualization_render": ClientLimits(defaults={"max_dimensions": RENDER_DEFAULT_SIDE}),
    "observatory_configuration_query": ClientLimits(hidden_parameters=("camera", "telescope")),
    "star_query": ClientLimits(
        maximums={"limit": STAR_QUERY_MAXIMUM_STARS}, defaults={"limit": STAR_QUERY_DEFAULT_STARS}
    ),
    "jobs_query": ClientLimits(
        hidden_parameters=("include",),
        maximums={"limit": JOBS_QUERY_MAXIMUM_JOBS},
        defaults={"limit": JOBS_QUERY_DEFAULT_JOBS},
    ),
    "jobs_get_log": ClientLimits(maximums={"lines": JOBS_LOG_MAXIMUM_LINES}),
}
"""What an AI client may not send to a tool. A picture from
`visualization_render` is 1200 pixels on its longest side unless the client
asks; an image is sent as a path (an array of pixels cannot travel in JSON).
`star_query` lists 50 stars unless asked for more, and at most 200.
`jobs_query` lists 20 jobs unless asked, at most 50, without their stored
numbers (`jobs_get` reads one job whole); `jobs_get_log` returns at most 200
lines. A camera or telescope is an object that a client cannot send as JSON
yet, so those filters are hidden until the server can build them from an id.
An observatory configuration is named by its id."""


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
