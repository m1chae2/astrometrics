"""Purpose: Tests that the wayfindinglib-core server reflects `Wayfinder`.

Description: Checks that the expected `Wayfinder` methods become tools with
the documented arguments, and that a reflected tool runs.
"""

import pytest

from mcp_servers.common.tool_registry import ToolRegistry

pytestmark = pytest.mark.anyio


def test_the_wayfinder_sub_apis_are_reflected(registry: ToolRegistry) -> None:
    """Tools of the control children and planning are registered."""
    names = set(registry.tools)
    assert "observatory_mount_status" in names
    assert "observatory_remote_list" in names
    assert not any(name.startswith("observatory_get_") for name in names)
    assert "planning_get_visibility" in names
    assert "planning_calculate_panels" in names


def test_history_query_has_the_documented_arguments(registry: ToolRegistry) -> None:
    """`observatory_history_query` needs a kind and takes the documented arguments."""
    schema = registry.tools["observatory_history_query"]["tool_def"].inputSchema
    assert schema["required"] == ["kind"]
    assert set(schema["properties"]) == {"kind", "session_id", "ekos_file_id", "include", "limit"}


async def test_a_reflected_tool_runs(registry: ToolRegistry) -> None:
    """Running a reflected tool returns text content."""
    reply = await registry.execute(
        "planning_calculate_panels",
        {
            "center_ra": "16:41:41.24",
            "center_dec": "+36:27:35.5",
            "rows": 2,
            "cols": 2,
            "overlap_percent": 10.0,
        },
    )
    assert reply
    assert reply[0].type == "text"
    assert "panel" in reply[0].text.lower()
