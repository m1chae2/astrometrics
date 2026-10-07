"""Purpose: Shared fixtures for the wayfindinglib-core server tests."""

import pytest

from mcp_servers.common.tool_registry import ToolRegistry
from mcp_servers.wayfinding_core.definition import build_registry


@pytest.fixture(scope="package")
def registry() -> ToolRegistry:
    """Build the server's registry once for the package.

    Returns
    -------
    registry : `ToolRegistry`
        Every tool, before a profile removes any.
    """
    return build_registry()


@pytest.fixture
def anyio_backend() -> str:
    """Run the asynchronous tests on asyncio only.

    Returns
    -------
    backend : `str`
        ``"asyncio"``.
    """
    return "asyncio"
