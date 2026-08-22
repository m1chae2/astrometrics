"""Purpose: Unit tests for wayfindinglib's MCP astrometrics reflection.

Description: Verifies the Wayfinder high-level interface generates a
valid MCP tool registry and executes reflected tools. Split out of
`astrometricslib/mcp/test/test_mcp_reflection.py` so astrometricslib's
own test suite does not require wayfindinglib to be installed --
astrometricslib.mcp.reflection is the shared engine both libraries'
registries are built on, and is covered by that suite directly.
"""

import pytest

from wayfindinglib.mcp.tool_registry import registry as wayfinding_registry

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Restrict anyio-marked tests in this module to the asyncio backend.

    Returns
    -------
    backend : `str`
        The anyio backend name to run these tests under.
    """
    return "asyncio"


def test_wayfindinglib_mcp_reflection_registers_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify wayfindinglib MCP server reflects Wayfinder tools."""
    tool_defs = wayfinding_registry.get_tool_definitions()
    tool_names = {t.name for t in tool_defs}

    # Verify key reflected tools are present
    assert "observatory_get_telescope_status" in tool_names
    assert "planning_get_visibility" in tool_names
    assert "planning_calculate_panels" in tool_names


async def test_wayfinding_reflected_tool_execution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify executing a reflected tool via Wayfinding registry succeeds."""
    res = await wayfinding_registry.execute(
        "planning_calculate_panels",
        {
            "center_ra": "16:41:41.24",
            "center_dec": "+36:27:35.5",
            "rows": 2,
            "cols": 2,
            "overlap_percent": 10.0,
        },
    )
    assert len(res) > 0
    assert res[0].type == "text"
    assert "panel" in res[0].text.lower()
