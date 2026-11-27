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


def test_review_reads_take_a_night_or_a_subject_id(registry: ToolRegistry) -> None:
    """A night read needs the night; a target read takes an id and a filter."""
    night = registry.tools["review_nights_get_capture"]["tool_def"].input_schema
    assert night["required"] == ["night"]
    guiding = registry.tools["review_targets_get_frame_guiding"]["tool_def"].input_schema
    assert guiding["required"] == ["target"]
    assert guiding["properties"]["target"]["type"] == "string"
    assert guiding["properties"]["frames"]["type"] == "object"


async def test_a_reflected_tool_runs(registry: ToolRegistry) -> None:
    """Running a reflected tool returns text content."""
    reply = await registry.execute(
        "planning_calculate_panels",
        {
            "ra_deg": 250.172,
            "dec_deg": 36.4599,
            "rows": 2,
            "cols": 2,
            "overlap_percent": 10.0,
        },
    )
    assert reply
    assert reply[0].type == "text"
    assert "panel" in reply[0].text.lower()
