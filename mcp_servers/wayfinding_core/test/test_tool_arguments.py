"""Purpose: Tests for how wayfindinglib-core tools take client arguments.

Description: The tools pass a client's JSON arguments to the `Wayfinder`
methods as they are. These tests check that a coordinate dictionary and an
ISO time with an offset work, and that no tool asks the client whether to
record a job. The library's own tests check the conversions themselves.
"""

import pytest

from mcp_servers.common.tool_registry import ToolRegistry

pytestmark = pytest.mark.anyio


def test_server_only_parameters_are_hidden_from_registered_tool_schemas(registry: ToolRegistry) -> None:
    """Clients are not asked whether to record a job; every run records one."""
    schema = registry.tools["planning_catalogs_predict_deep_catalog"]["tool_def"].input_schema
    assert "register_job" not in schema["properties"]
    assert "magnitude_max" in schema["properties"]
    advisory = registry.tools["review_get_advisory"]["tool_def"].input_schema
    assert advisory["required"] == ["kind", "target"]


async def test_visibility_accepts_coordinates_and_an_offset_time(registry: ToolRegistry) -> None:
    """Visibility runs with a coordinate dictionary and a local ISO time."""
    result = await registry.execute(
        "planning_sky_get_visibility",
        {
            "objects": [{"id": "M 52", "ra_deg": 351.2, "dec_deg": 61.59}],
            "time": "2026-10-02T22:00:00-06:00",
        },
    )
    assert "M 52" in result[0].text
    assert "altitude_deg" in result[0].text
    assert "2026-10-03T04:00:00Z" in result[0].text
