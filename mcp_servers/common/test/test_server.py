"""Tests for the shared MCP server loop in ``mcp_servers.common.server``.

Version 2 of the MCP SDK left two jobs to the server: checking a call's
arguments against the tool's schema, and turning a failed call into an
error result. These tests connect an in-memory MCP client to a server built
by `build_server` and check that the client sees the same replies as it did
with version 1.
"""

from typing import Any

import pytest
from mcp.client import Client
from mcp.types import Resource, TextContent, Tool

from mcp_servers.common.server import build_server
from mcp_servers.common.tool_errors import ToolErrorContent

pytestmark = pytest.mark.anyio

SCHEMA = {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}
"""The schema of the one fake tool: a required string ``target``."""


@pytest.fixture
def anyio_backend() -> str:
    """Run the tests in this module on the asyncio backend only.

    Returns
    -------
    backend : `str`
        The anyio backend name.
    """
    return "asyncio"


class FakeTools:
    """A tool source with one tool whose reply depends on its argument."""

    def get_tool_definitions(self) -> list[Tool]:
        """List the one fake tool.

        Returns
        -------
        tools : `list` [`mcp.types.Tool`]
            The ``look_up`` tool.
        """
        return [Tool(name="look_up", description="Look up a target.", input_schema=SCHEMA)]

    async def execute(self, name: str, arguments: dict[str, Any]) -> list[Any]:
        """Answer, refuse or fail, depending on the target.

        Parameters
        ----------
        name : `str`
            The tool's name.
        arguments : `dict`
            The call's arguments.

        Returns
        -------
        content : `list`
            The target's name, or a `ToolErrorContent` for ``"refuse"``.

        Raises
        ------
        RuntimeError
            If the target is ``"crash"``.
        """
        target = arguments.get("target")
        if target == "crash":
            raise RuntimeError("The tool broke.")
        if target == "refuse":
            return ToolErrorContent([TextContent(type="text", text="not_found: No such target.")])
        return [TextContent(type="text", text=f"{name}: {target}")]


class FakeResources:
    """A resource source with one text resource."""

    def list_resources(self) -> list[Resource]:
        """List the one fake resource.

        Returns
        -------
        resources : `list` [`mcp.types.Resource`]
            The ``fake://notes`` resource.
        """
        return [Resource(uri="fake://notes", name="Notes", mime_type="application/json")]

    async def read_resource(self, uri: str) -> str:
        """Read the fake resource.

        Parameters
        ----------
        uri : `str`
            The resource's address.

        Returns
        -------
        content : `str`
            A short JSON text naming the address.
        """
        return f'{{"uri": "{uri}"}}'


def make_client(resources: FakeResources | None = None) -> Client:
    """Connect an in-memory client to a server built from the fakes.

    Parameters
    ----------
    resources : `FakeResources`, optional
        The resources to offer. If `None` (default), the server offers none.

    Returns
    -------
    client : `mcp.client.Client`
        A client that uses the initialize handshake, as stdio clients do.
    """
    server = build_server("fake", "Use the fake tools.", FakeTools(), resources)
    return Client(server, mode="legacy")


async def test_the_tool_list_and_instructions_reach_the_client() -> None:
    """The client sees the tool, its schema and the server's instructions."""
    async with make_client() as client:
        listed = await client.list_tools()
        assert client.instructions == "Use the fake tools."
    assert [tool.name for tool in listed.tools] == ["look_up"]
    assert listed.tools[0].input_schema == SCHEMA


async def test_a_good_call_returns_the_content_unflagged() -> None:
    """A call that works comes back with its text and no error flag."""
    async with make_client() as client:
        result = await client.call_tool("look_up", {"target": "Vega"})
    assert result.is_error is False
    assert result.content[0].text == "look_up: Vega"


async def test_arguments_that_break_the_schema_are_refused_before_the_tool_runs() -> None:
    """A missing required argument gets the same error text as MCP SDK 1.x."""
    async with make_client() as client:
        result = await client.call_tool("look_up", {})
    assert result.is_error is True
    assert result.content[0].text == "Input validation error: 'target' is a required property"


async def test_a_refused_call_is_flagged_as_an_error() -> None:
    """A `ToolErrorContent` reply reaches the client flagged as an error."""
    async with make_client() as client:
        result = await client.call_tool("look_up", {"target": "refuse"})
    assert result.is_error is True
    assert result.content[0].text == "not_found: No such target."


async def test_a_tool_that_raises_returns_an_error_result() -> None:
    """An exception becomes an error result holding its message."""
    async with make_client() as client:
        result = await client.call_tool("look_up", {"target": "crash"})
    assert result.is_error is True
    assert result.content[0].text == "The tool broke."


async def test_resources_are_listed_and_read_as_plain_text() -> None:
    """A resource source's text comes back as a ``text/plain`` resource."""
    async with make_client(FakeResources()) as client:
        listed = await client.list_resources()
        read = await client.read_resource("fake://notes")
    assert [resource.uri for resource in listed.resources] == ["fake://notes"]
    assert read.contents[0].text == '{"uri": "fake://notes"}'
    assert read.contents[0].mime_type == "text/plain"
