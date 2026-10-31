"""Purpose: Test the stack review RPC methods and the service behind them.

Description: ``processing:remake_preview`` starts the library's preview remake
as a background job, and ``quality:set_aside_frames`` reads the set-aside
listing. The tests check that both are declared and routed, that a quick job
gives back its result, that a slow job gives back a job id, that a quick
failure reaches the caller as an error, and that the service never asks the
library to skip keeping the old pictures. The science library is a stand-in.
"""

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import InvalidArgumentError, PreviewRemakeResult
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.processing.stack_review_service import StackReviewService


def _result() -> PreviewRemakeResult:
    """Make a small preview remake result.

    Returns
    -------
    result : `PreviewRemakeResult`
        A result for a target called ``M 81``.
    """
    return PreviewRemakeResult(
        target_id="M 81", stack_path="/s/M_81_Stacked.fits", preview_path="/s/M_81_Stacked_preview.jpg"
    )


def test_the_methods_are_declared_and_routed() -> None:
    """Both methods are public and go to the right place."""
    assert "processing:remake_preview" in RPC_METHODS
    assert "quality:set_aside_frames" in RPC_METHODS
    assert rpc_registry._handlers["processing:remake_preview"] == (
        "stack_review_service",
        "start_preview_remake",
    )
    assert rpc_registry._handlers["quality:set_aside_frames"] == ("astrometrics.quality", "set_aside_frames")


def test_a_quick_remake_gives_its_result_and_keeps_the_old_pictures() -> None:
    """The settings reach the library, and nothing turns off keep_previous."""
    calls: list[dict[str, Any]] = []

    def remake_preview(target: str, **options: Any) -> PreviewRemakeResult:
        """Record the call and give a result.

        Returns
        -------
        result : `PreviewRemakeResult`
            A small result.
        """
        calls.append({"target": target, **options})
        return _result()

    service = StackReviewService(SimpleNamespace(processing=SimpleNamespace(remake_preview=remake_preview)))

    reply = service.start_preview_remake("M 81", "spectral", denoise=True, denoise_strength=0.4)

    assert reply.status == "completed"
    assert reply.result is not None
    assert reply.result["preview_path"].endswith("_preview.jpg")
    assert calls == [
        {
            "target": "M 81",
            "kind": "spectral",
            "denoise": True,
            "denoise_strength": 0.4,
            "star_toning": None,
            "register_job": True,
        }
    ]
    assert "keep_previous" not in calls[0]


def test_a_slow_remake_gives_back_to_the_caller_while_it_runs() -> None:
    """A job that outlasts the wait answers ``running``; it still finishes."""
    release = threading.Event()
    finished = threading.Event()

    def remake_preview(target: str, **options: Any) -> PreviewRemakeResult:
        """Wait until the test lets go.

        Returns
        -------
        result : `PreviewRemakeResult`
            A small result.
        """
        release.wait(timeout=10)
        finished.set()
        return _result()

    service = StackReviewService(
        SimpleNamespace(processing=SimpleNamespace(remake_preview=remake_preview)), wait_seconds=0.05
    )

    reply = service.start_preview_remake("M 81")

    assert reply.status == "running"
    assert reply.result is None
    release.set()
    assert finished.wait(timeout=10)


def test_a_quick_failure_reaches_the_caller() -> None:
    """A refused value is raised, not hidden in a job."""

    def remake_preview(target: str, **options: Any) -> PreviewRemakeResult:
        """Refuse the strength.

        Raises
        ------
        InvalidArgumentError
            Always.
        """
        raise InvalidArgumentError("denoise_strength must be between 0 and 1.")

    service = StackReviewService(SimpleNamespace(processing=SimpleNamespace(remake_preview=remake_preview)))

    with pytest.raises(InvalidArgumentError, match="between 0 and 1"):
        service.start_preview_remake("M 81", denoise_strength=3.0)
