"""Purpose: Unit tests for dynamic MCP astrometrics reflection engine.

Description: Verifies that the high-level interface generates valid
JSON Schemas and executes reflected tools. The equivalent coverage for
wayfindinglib's Wayfinder high-level interface lives in
`wayfindinglib/mcp/test/test_wayfinding_reflection.py` -- split out so
this suite does not require wayfindinglib to be installed.
"""

import pytest

from astrometricslib.mcp.reflection import generate_tool_schema, parse_docstring_params
from astrometricslib.mcp.tool_registry import registry as astrometrics_registry

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


def test_parse_docstring_params_extracts_descriptions():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify parsing of parameters from NumPy/Google style docstring."""
    sample_doc = """
    Perform standard FITS scaling and return raw PNG bytes.

    Parameters
    ----------
    path : `str`
        The absolute path of the target file.
    max_dimensions : `int`
        The maximum resolution bound.
    """
    parsed = parse_docstring_params(sample_doc)
    assert "path" in parsed
    assert "The absolute path of the target file." in parsed["path"]
    assert "max_dimensions" in parsed


def test_generate_tool_schema_builds_json_schema():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify JSON schema generation from a callable signature."""

    def dummy_func(target_id: str, count: int = 10, enable_flag: bool = True) -> str:
        """Process dummy target parameters and return target_id.

        Parameters
        ----------
        target_id : `str`
            Target name or identifier.
        count : `int`
            Number of iterations.
        enable_flag : `bool`
            Toggle feature flag.

        Returns
        -------
        result : `str`
            The target identifier string.
        """
        return target_id

    schema = generate_tool_schema(dummy_func)
    assert schema["type"] == "object"
    assert "target_id" in schema["properties"]
    assert schema["properties"]["target_id"]["type"] == "string"
    assert schema["properties"]["count"]["type"] == "integer"
    assert schema["properties"]["enable_flag"]["type"] == "boolean"
    assert schema["required"] == ["target_id"]


def test_astrometricslib_mcp_reflection_registers_tools():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify astrometricslib MCP server reflects astrometrics tools."""
    tool_defs = astrometrics_registry.get_tool_definitions()
    tool_names = {t.name for t in tool_defs}

    # Verify key reflected tools are present
    assert "target_list" in tool_names
    assert "target_create" in tool_names
    assert "visualization_convert_fits_to_png" in tool_names
    assert "star_get_audit" in tool_names


async def test_astrometrics_reflected_tool_execution():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify executing a reflected tool via Astrometrics registry succeeds."""
    res = await astrometrics_registry.execute("target_list", {})
    assert len(res) > 0
    assert res[0].type == "text"
