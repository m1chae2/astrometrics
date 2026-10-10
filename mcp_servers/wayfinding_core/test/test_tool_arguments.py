"""Purpose: Tests for how wayfindinglib-core tools take client arguments.

Description: The tools pass a client's JSON arguments to the `Wayfinder`
methods as they are. These tests check that a coordinate dictionary and an
ISO time with an offset work, and that the server, not the client, decides
`register_job`. The library's own tests check the conversions themselves.
"""

import pytest

from mcp_servers.common.tool_registry import ToolRegistry

pytestmark = pytest.mark.anyio


def test_server_only_parameters_are_hidden_from_registered_tool_schemas(registry: ToolRegistry) -> None:
    """Clients are not asked for `register_job`; the server decides it."""
    schema = registry.tools["planning_deep_catalog_status"]["tool_def"].inputSchema
    assert "register_job" not in schema["properties"]
    assert "include" in schema["properties"]
    advisory = registry.tools["planning_get_advisory"]["tool_def"].inputSchema
    assert advisory["required"] == ["kind"]


async def test_visibility_accepts_coordinates_and_an_offset_time(registry: ToolRegistry) -> None:
    """Visibility runs with a coordinate dictionary and a local ISO time."""
    result = await registry.execute(
        "planning_get_visibility",
        {
            "objects": [{"id": "M 52", "ra_deg": 351.2, "dec_deg": 61.59}],
            "time": "2026-10-02T22:00:00-06:00",
        },
    )
    assert "M 52" in result[0].text
    assert "altitude_deg" in result[0].text
    assert "2026-10-03T04:00:00Z" in result[0].text
