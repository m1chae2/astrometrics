"""Purpose: Tests for how the backend MCP server calls the running backend.

Description: Every backend tool reaches the backend through ``/api/rpc``.
These tests replace the HTTP call with canned replies and check that an
error record comes back as its error category, that the router's success
wrapper is removed, that a destructive method is refused before any call,
and that the notifications resource and the active jobs are read through
RPC methods.
"""

import asyncio
import json

import pytest

from astrometricslib import ConflictError, NotFoundError, PermissionDeniedError
from mcp_servers.backend import definition


def fake_backend(monkeypatch: pytest.MonkeyPatch, reply: dict) -> list[dict]:
    """Make every backend call answer with `reply`.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Pytest's monkeypatch fixture.
    reply : `dict`
        The JSON-RPC reply to send back.

    Returns
    -------
    payloads : `list` [`dict`]
        Every JSON-RPC request the tools sent, in order.
    """
    payloads: list[dict] = []

    async def fake_post(endpoint: str, payload: dict | None = None, timeout: float = 120.0) -> dict:
        """Record the request and send the canned reply.

        Returns
        -------
        reply : `dict`
            The canned JSON-RPC reply.
        """
        await asyncio.sleep(0)
        assert endpoint == "/api/rpc"
        payloads.append(payload or {})
        return reply

    monkeypatch.setattr(definition, "post_to_backend", fake_post)
    return payloads


def success(data: object) -> dict:
    """Wrap a result the way the RPC route does.

    Returns
    -------
    reply : `dict`
        A JSON-RPC success reply.
    """
    return {"jsonrpc": "2.0", "id": "mcp-proxy", "result": {"status": "success", "data": data}}


@pytest.mark.anyio
async def test_the_proxy_raises_the_category_the_backend_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    """The backend's error record is raised as its category."""
    fake_backend(
        monkeypatch,
        {
            "jsonrpc": "2.0",
            "id": "mcp-proxy",
            "error": {
                "code": -32002,
                "message": "Device in use.",
                "data": {"code": "conflict", "message": "Device in use.", "details": {"device": "mount"}},
            },
        },
    )
    with pytest.raises(ConflictError, match="Device in use") as caught:
        await definition.execute_rpc("telescope:slew", {})
    assert caught.value.details == {"device": "mount"}


@pytest.mark.anyio
async def test_the_proxy_unwraps_a_successful_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """The router's success wrapper comes off the result."""
    payloads = fake_backend(monkeypatch, success([1, 2]))
    assert await definition.execute_rpc("target:list", {}) == [1, 2]
    assert payloads[0]["method"] == "target:list"


@pytest.mark.anyio
async def test_backend_call_rpc_refuses_a_destructive_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """A delete-style method is refused before anything is sent."""
    payloads = fake_backend(monkeypatch, success(None))
    with pytest.raises(PermissionDeniedError):
        await definition.tool_backend_call_rpc("target:delete", {"target_id": "M31"})
    assert payloads == []


@pytest.mark.anyio
async def test_the_notifications_resource_reads_the_rpc_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resource asks ``system:notifications`` for the unread ones."""
    unread = [{"id": "M31_1", "message": "Stack done", "read": False}]
    payloads = fake_backend(monkeypatch, success(unread))
    resource = definition.NotificationResource()
    assert [str(item.uri) for item in resource.list_resources()] == [definition.NOTIFICATIONS_URI]
    text = await resource.read_resource(definition.NOTIFICATIONS_URI)
    assert json.loads(text) == unread
    assert payloads[0]["method"] == "system:notifications"
    assert payloads[0]["params"] == {"unread_only": True}
    with pytest.raises(NotFoundError):
        await resource.read_resource("astrometrics://nothing")


@pytest.mark.anyio
async def test_active_jobs_come_from_the_rpc_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """The active_jobs section asks ``processing:active_jobs``."""
    payloads = fake_backend(monkeypatch, success([{"id": 7, "status": "running"}]))
    answer = await definition.tool_app_status(["active_jobs"])
    assert answer["active_jobs"] == [{"id": 7, "status": "running"}]
    assert [payload["method"] for payload in payloads] == ["processing:active_jobs"]
