"""Unit tests for the MCP HTTP helpers.

A refused method or an unreachable backend raises an error category
instead of returning an error dictionary.
"""

import pytest

from astrometricslib import ExternalServiceError, PermissionDeniedError
from mcp_servers.backend import mcp_http


@pytest.mark.anyio
async def test_a_destructive_http_method_is_refused() -> None:
    """DELETE is not an allowed method, so the request is refused."""
    with pytest.raises(PermissionDeniedError, match="DELETE"):
        await mcp_http.request_backend("DELETE", "/api/targets")


@pytest.mark.anyio
async def test_an_unreachable_backend_is_an_external_service_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A backend nobody listens on raises ExternalServiceError."""
    monkeypatch.setattr(mcp_http, "API_BASE", "http://127.0.0.1:9")

    with pytest.raises(ExternalServiceError, match="Could not connect"):
        await mcp_http.post_to_backend("/api/rpc", {}, timeout=2.0)
