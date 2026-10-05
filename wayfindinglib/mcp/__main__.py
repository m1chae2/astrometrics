"""MCP server executable for the Wayfinding Library."""

import asyncio
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, TextContent, Tool

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from astrometricslib import configure_logging
from astrometricslib.mcp.profile import GAP_REPORT_GUIDANCE
from astrometricslib.mcp.tool_errors import as_call_tool_result
from wayfindinglib.mcp.tool_registry import registry

# Configure logging to stderr to avoid corrupting stdio MCP protocol
configure_logging("mcp_wayfindinglib", level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Server("wayfindinglib-core", instructions=GAP_REPORT_GUIDANCE)

# Offer only the tools the manifest allows for the chosen profile
# (see astrometricslib.mcp.profile).
registry.apply_profile(Path(__file__).resolve().parent / "tool_manifest.json")


@app.list_tools()
async def list_tools() -> list[Tool]:  # ruff: ignore[unused-async] -- awaited
    # by the mcp.server.lowlevel.Server request-handler dispatch loop
    """List all offline tools registered in the wayfinding registry.

    Returns
    -------
    tools : `list` [`Tool`]
        Definitions of every tool registered in the wayfinding registry.
    """
    return registry.get_tool_definitions()


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent] | CallToolResult:
    """Execute a registered offline tool.

    Returns
    -------
    content : `list` [`TextContent`] or `CallToolResult`
        Text content blocks produced by the executed tool, or an error
        result if the tool failed.
    """
    return as_call_tool_result(await registry.execute(name, arguments))


async def main():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the stdio MCP server loop for the wayfinding library."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
