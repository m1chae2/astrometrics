"""MCP server executable for the Wayfinding Library."""

import asyncio
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from wayfindinglib.mcp.tool_registry import registry

# Configure logging to stderr to avoid corrupting stdio MCP protocol
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Server("wayfindinglib-core")


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
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a registered offline tool.

    Returns
    -------
    content : `list` [`TextContent`]
        Text content blocks produced by the executed tool.
    """
    return await registry.execute(name, arguments)


async def main():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the stdio MCP server loop for the wayfinding library."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
