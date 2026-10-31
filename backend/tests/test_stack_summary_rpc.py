"""Purpose: Test the ``processing:stack_summary`` RPC method.

Description: The method is served by the library's processing API. The tests
check it is declared and routed there, that the reply is plain JSON, and that
a target with no saved stack becomes a ``not_found`` error reply.
"""

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from astrometricslib import NotFoundError, StackSummary
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers import rpc_router
from backend.routers.rpc_router import rpc_registry
from backend.services.rpc_protocol import RPCRequest, serialize_rpc_result


def test_the_method_is_declared_and_routed_to_the_processing_api() -> None:
    """The method is public and calls the processing API's summary."""
    assert "processing:stack_summary" in RPC_METHODS
    assert rpc_registry._handlers["processing:stack_summary"] == ("astrometrics.processing", "stack_summary")


def test_the_reply_is_plain_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The summary comes back as a dictionary; the call gets its inputs."""
    summary = StackSummary(
        target_id="M 81",
        made_at="2026-10-30T04:00:00+00:00",
        frames_submitted=40,
        frames_stacked=36,
        frames_skipped=4,
    )
    stack_summary = MagicMock(return_value=summary)
    monkeypatch.setattr(container.astrometrics.processing, "stack_summary", stack_summary)
    reply = asyncio.run(
        rpc_registry.execute("processing:stack_summary", {"target": "M 81", "kind": "spectral"})
    )
    stack_summary.assert_called_once_with(target="M 81", kind="spectral")
    plain = serialize_rpc_result(reply)
    assert plain["frames_stacked"] == 36
    assert plain["exposure_groups"] == []


@pytest.mark.anyio
async def test_a_target_with_no_stack_is_a_not_found_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """``NotFoundError`` reaches the caller with the ``not_found`` code."""

    def fail(target: str, kind: str = "imaging") -> None:
        """Raise the error the library raises for a target with no stack.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError(f"Target '{target}' has no saved summary for its {kind} stack.")

    monkeypatch.setattr(container.astrometrics.processing, "stack_summary", fail)
    response = await rpc_router.handle_rpc(
        RPCRequest(method="processing:stack_summary", id=1, params={"target": "M 99"})
    )
    assert json.loads(bytes(response.body))["error"]["data"]["code"] == "not_found"
