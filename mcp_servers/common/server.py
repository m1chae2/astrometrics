"""Purpose: Run an MCP server over standard input and output.

Description: Every Python MCP server in this project does the same thing: it
lists its tools, runs a tool when the client asks, and talks to the client
over stdin and stdout. This module holds that loop once. A server passes in
its name, the instructions the client reads at the start of a session, and
an object that lists and runs its tools. A server may also offer resources,
read-only documents the client can fetch by address.

The module imports only the MCP package and ``jsonschema``, which the MCP
package itself needs, so the gap server, which must start without
astrometricslib, can use it too. Each program configures logging
itself, before it calls `run_server`, and logs only to stderr so the MCP
messages on stdout stay clean.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

import jsonschema
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequestParams,
    CallToolResult,
    ListResourcesResult,
    ListToolsResult,
    PaginatedRequestParams,
    ReadResourceRequestParams,
    ReadResourceResult,
    Resource,
    TextContent,
    TextResourceContents,
    Tool,
)

from mcp_servers.common.tool_errors import as_call_tool_result

logger = logging.getLogger(__name__)


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


def _server_version() -> str:
    """Give the version this server reports to the client.

    Returns
    -------
    version : `str`
        The installed version of the ``astrometrics`` package, or an empty
        string if the package is not installed (for example, when the code
        runs from a plain checkout through ``PYTHONPATH``).
    """
    try:
        return version("astrometrics")
    except PackageNotFoundError:
        return ""


def _error_result(text: str) -> CallToolResult:
    """Build a tool result that tells the client the call failed.

    Parameters
    ----------
    text : `str`
        What went wrong.

    Returns
    -------
    result : `mcp.types.CallToolResult`
        One text block, flagged as an error.
    """
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def build_server(
    name: str, instructions: str, tools: ToolSource, resources: ResourceSource | None = None
) -> Server:
    """Build an MCP server that serves the given tools and resources.

    Version 2 of the MCP SDK no longer checks a call's arguments against the
    tool's schema, and no longer turns an exception into an error result.
    The tool handler here does both, the same way version 1 did, so the
    client sees the same replies.

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
    # The last tool list sent to the client, by name. A call uses it to find
    # the schema its arguments must match.
    listed_tools: dict[str, Tool] = {}

    def refresh_listed_tools() -> list[Tool]:
        """Ask the tool source for its tools and remember them by name.

        Returns
        -------
        tools : `list` [`mcp.types.Tool`]
            The tool definitions.
        """
        definitions = tools.get_tool_definitions()
        listed_tools.clear()
        listed_tools.update({tool.name: tool for tool in definitions})
        return definitions

    async def list_tools(
        _context: ServerRequestContext, _params: PaginatedRequestParams | None
    ) -> ListToolsResult:
        """List the tools.

        Returns
        -------
        result : `mcp.types.ListToolsResult`
            The tool definitions.
        """
        await asyncio.sleep(0)
        return ListToolsResult(tools=refresh_listed_tools())

    async def run_tool(tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Check the arguments against the tool's schema, then run the tool.

        Parameters
        ----------
        tool_name : `str`
            The tool's name.
        arguments : `dict`
            The client's arguments.

        Returns
        -------
        result : `mcp.types.CallToolResult`
            The tool's content, or an error result if the arguments do not
            match the schema or the tool failed.
        """
        if tool_name not in listed_tools:
            refresh_listed_tools()
        tool = listed_tools.get(tool_name)
        if tool is None:
            # The tool source reports an unknown tool itself.
            logger.warning("Tool '%s' not listed, no validation will be performed", tool_name)
        else:
            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as error:
                return _error_result(f"Input validation error: {error.message}")
        return as_call_tool_result(await tools.execute(tool_name, arguments))

    async def call_tool(_context: ServerRequestContext, params: CallToolRequestParams) -> CallToolResult:
        """Run a tool and mark a failed call as an error.

        Returns
        -------
        result : `mcp.types.CallToolResult`
            The tool's content, or an error result if it failed.
        """
        try:
            return await run_tool(params.name, params.arguments or {})
        except Exception as error:
            # This is the boundary between the tool and the client. The
            # client must get an error result it can read, not a broken
            # reply, so every failure stops here.
            logger.exception("Tool '%s' failed", params.name)
            return _error_result(str(error))

    resource_handlers: dict[str, Any] = {}
    if resources is not None:

        async def list_resources(
            _context: ServerRequestContext, _params: PaginatedRequestParams | None
        ) -> ListResourcesResult:
            """List the resources.

            Returns
            -------
            result : `mcp.types.ListResourcesResult`
                The resource descriptors.
            """
            await asyncio.sleep(0)
            return ListResourcesResult(resources=resources.list_resources())

        async def read_resource(
            _context: ServerRequestContext, params: ReadResourceRequestParams
        ) -> ReadResourceResult:
            """Read one resource as plain text.

            Returns
            -------
            result : `mcp.types.ReadResourceResult`
                The resource's text.
            """
            text = await resources.read_resource(params.uri)
            return ReadResourceResult(
                contents=[TextResourceContents(uri=params.uri, text=text, mime_type="text/plain")]
            )

        resource_handlers = {"on_list_resources": list_resources, "on_read_resource": read_resource}
    return Server(
        name,
        version=_server_version(),
        instructions=instructions,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
        **resource_handlers,
    )


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
