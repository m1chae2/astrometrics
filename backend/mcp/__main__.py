"""MCP server executable for backend."""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

# Ensure project root is in sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.mcp.tool_registry import get_astrometrics, registry

# Configure logging to stderr to avoid corrupting stdio MCP protocol
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logging.getLogger("httpx").setLevel(logging.WARNING)

app = Server("astrometrics-backend")


@app.list_tools()
async def list_tools() -> list[Tool]:  # ruff: ignore[unused-async] -- awaited by mcp Server.list_tools
    """List all registered tools in the registry.

    Returns
    -------
    tools : `list` [`Tool`]
        The `Tool` definition for every registered tool.
    """
    return registry.get_tool_definitions()


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    """Execute a registered tool by forwarding parameters to the registry.

    Returns
    -------
    result : `list` [`TextContent`]
        The tool's result wrapped as MCP text content.
    """
    return await registry.execute(name, arguments)


@app.list_resources()
async def list_resources() -> list[Resource]:  # ruff: ignore[unused-async] -- awaited by mcp Server
    """List the available JSON resources readable via the MCP server.

    Returns
    -------
    resources : `list` [`Resource`]
        The single "astrometrics://notifications" resource
        descriptor.
    """
    return [
        Resource(
            uri="astrometrics://notifications",
            name="System Notifications",
            description="Real-time notifications about background job completions",
            mimeType="application/json",
        )
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:  # ruff: ignore[unused-async] -- awaited by mcp Server.read_resource
    """Read the content of a specific resource URI.

    Returns
    -------
    content : `str`
        The JSON-encoded resource content, or "[]" if unavailable.

    Raises
    ------
    ValueError
        If `uri` does not match a known resource.
    """
    if uri == "astrometrics://notifications":
        astrometrics = get_astrometrics()
        project_root = astrometrics.config.get_project_root() if astrometrics else Path(os.getcwd())
        notifications_path = project_root / "backend" / "notifications.json"

        if notifications_path.exists():
            try:
                with open(notifications_path, encoding="utf-8") as f:
                    data = json.load(f)
                    unread_notifications = [
                        notification for notification in data if not notification.get("read")
                    ]
                    return json.dumps(unread_notifications)
            except Exception as e:
                logging.error(f"Error reading notifications file: {e}")
                return "[]"
        return "[]"

    raise ValueError(f"Unknown resource: {uri}")


async def main():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Run the stdio MCP server loop."""
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
