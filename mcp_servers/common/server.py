"""Purpose: Run an MCP server over standard input and output.

Description: Every Python MCP server in this project does the same thing: it
lists its tools, runs a tool when the client asks, and talks to the client
over stdin and stdout. This module holds that loop once. A server passes in
its name, the instructions the client reads at the start of a session, and
an object that lists and runs its tools. A server may also offer resources,
read-only documents the client can fetch by address.

The module imports only the MCP package, so the gap server, which must start
without astrometricslib, can use it too. Each program configures logging
itself, before it calls `run_server`, and logs only to stderr so the MCP
messages on stdout stay clean.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolResult, Resource, Tool

from mcp_servers.common.tool_errors import as_call_tool_result


class ToolSource(Protocol):
    """What a server needs from its tools: a list and a way to run one."""

    def get_tool_definitions(self) -> list[Tool]:
        """List the tools the server offers.

        Returns
        -------
        tools : `list` [`mcp.types.Tool`]
            One definition per tool.
        """

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[Any]:
        """Run one tool.

        Parameters
        ----------
        name : `str`
            The tool's name.
        arguments : `dict`
            The client's arguments.

        Returns
        -------
        content : `list`
            The result as MCP content, or a `ToolErrorContent` on failure.
        """


class ResourceSource(Protocol):
    """What a server needs to offer resources: a list and a way to read one."""

    def list_resources(self) -> list[Resource]:
        """List the resources the server offers.

        Returns
        -------
        resources : `list` [`mcp.types.Resource`]
            One descriptor per resource.
        """

    async def read_resource(self, uri: str) -> str:
        """Read one resource.

        Parameters
        ----------
        uri : `str`
            The resource's address.

        Returns
        -------
        content : `str`
            The resource's text.
        """


def build_server(
    name: str, instructions: str, tools: ToolSource, resources: ResourceSource | None = None
) -> Server:
    """Build an MCP server that serves the given tools and resources.

    Parameters
    ----------
    name : `str`
        The server's name, sent to the client.
    instructions : `str`
        What the client is told at the start of a session.
    tools : `ToolSource`
        Lists and runs the tools.
    resources : `ResourceSource`, optional
        Lists and reads the resources. If `None` (default), the server
        offers none.

    Returns
    -------
    server : `mcp.server.Server`
        The server, ready to run.
    """
    server = Server(name, instructions=instructions)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """List the tools.

        Returns
        -------
        tools : `list` [`mcp.types.Tool`]
            The tool definitions.
        """
        await asyncio.sleep(0)
        return tools.get_tool_definitions()

    @server.call_tool()
    async def call_tool(tool_name: str, arguments: dict) -> list[Any] | CallToolResult:
        """Run a tool and mark a failed call as an error.

        Returns
        -------
        result : `list` or `mcp.types.CallToolResult`
            The tool's content, or an error result if it failed.
        """
        return as_call_tool_result(await tools.execute(tool_name, arguments))

    if resources is not None:

        @server.list_resources()
        async def list_resources() -> list[Resource]:
            """List the resources.

            Returns
            -------
            resources : `list` [`mcp.types.Resource`]
                The resource descriptors.
            """
            await asyncio.sleep(0)
            return resources.list_resources()

        @server.read_resource()
        async def read_resource(uri: Any) -> str:
            """Read one resource.

            Returns
            -------
            content : `str`
                The resource's text.
            """
            return await resources.read_resource(str(uri))

    return server


def run_server(
    name: str,
    instructions: str,
    tools: ToolSource,
    resources: ResourceSource | None = None,
    before_start: Callable[[], Awaitable[None] | None] | None = None,
) -> None:
    """Build the server and serve one client over stdin and stdout.

    Parameters
    ----------
    name : `str`
        The server's name, sent to the client.
    instructions : `str`
        What the client is told at the start of a session.
    tools : `ToolSource`
        Lists and runs the tools.
    resources : `ResourceSource`, optional
        Lists and reads the resources.
    before_start : `Callable`, optional
        Work to do once before serving, such as closing jobs a stopped
        server left open.
    """
    server = build_server(name, instructions, tools, resources)

    async def serve() -> None:
        """Run the stdio loop until the client disconnects."""
        if before_start is not None:
            outcome = before_start()
            if outcome is not None:
                await outcome
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(serve())
