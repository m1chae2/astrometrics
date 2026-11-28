"""Purpose: Test the ``processing:stack_summary`` RPC method.

Description: The method is served by `StackReviewService.get_stack_summary`,
which turns the target id into the target and asks the library's
``processing.stacking.get_summary``. The tests check it is declared and routed
there, that the reply is plain JSON, and that a target with no saved stack
becomes a ``not_found`` error reply.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import Target
from astrometricslib.exceptions import NotFoundError
from astrometricslib.reports import StackSummary
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers import rpc_router
from backend.routers.rpc_router import rpc_registry
from backend.services.processing.stack_review_service import StackReviewService
from backend.services.rpc_protocol import RPCRequest, serialize_rpc_result


def make_service(get_summary: object) -> StackReviewService:
    """Build the service over a stand-in library that holds M 81 and M 99.

    Parameters
    ----------
    get_summary : `object`
        The stand-in for ``processing.stacking.get_summary``.

    Returns
    -------
    service : `StackReviewService`
        The service.
    """
    library = SimpleNamespace(
        targets=SimpleNamespace(get=lambda target_id: Target(id=target_id)),
        processing=SimpleNamespace(stacking=SimpleNamespace(get_summary=get_summary)),
    )
    return StackReviewService(library, wait_seconds=0.1)


def test_the_method_is_declared_and_routed_to_the_stack_review_service() -> None:
    """The method is public and served by the stack review service."""
    assert "processing:stack_summary" in RPC_METHODS
    assert rpc_registry._handlers["processing:stack_summary"] == ("stack_review_service", "get_stack_summary")


def test_the_reply_is_plain_json() -> None:
    """The summary comes back as a dictionary; the call gets its inputs."""
    summary = StackSummary(
        target_id="M 81",
        made_at="2026-10-30T04:00:00+00:00",
        frames_submitted=40,
        frames_stacked=36,
        frames_skipped=4,
    )
    stack_summary = MagicMock(return_value=summary)
    reply = make_service(stack_summary).get_stack_summary("M 81", kind="spectral")
    (target, kind), _ = stack_summary.call_args
    assert (target.id, kind) == ("M 81", "spectral")
    plain = serialize_rpc_result(reply)
    assert plain["frames_stacked"] == 36
    assert plain["exposure_groups"] == []
    assert "flagged" not in plain
    assert "flag_reasons" not in plain


def test_a_stored_summary_with_the_removed_flag_keys_still_loads() -> None:
    """An older saved summary holds `flagged`; it is ignored."""
    summary = StackSummary.model_validate({
        "target_id": "M 81",
        "made_at": "2026-10-30T04:00:00+00:00",
        "frames_submitted": 40,
        "frames_stacked": 36,
        "frames_skipped": 4,
        "flagged": True,
        "flag_reasons": ["single-frame stack: no rejection/registration quality processing applied"],
    })
    assert summary.frames_stacked == 36
    assert "flagged" not in summary.model_dump()


@pytest.mark.anyio
async def test_a_target_with_no_stack_is_a_not_found_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    """``NotFoundError`` reaches the caller with the ``not_found`` code."""

    def fail(target: Target, kind: str = "imaging") -> None:
        """Raise the error the library raises for a target with no stack.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError(f"Target '{target.id}' has no saved summary for its {kind} stack.")

    monkeypatch.setattr(container, "stack_review_service", make_service(fail))
    response = await rpc_router.handle_rpc(
        RPCRequest(method="processing:stack_summary", id=1, params={"target": "M 99"})
    )
    assert json.loads(bytes(response.body))["error"]["data"]["code"] == "not_found"
