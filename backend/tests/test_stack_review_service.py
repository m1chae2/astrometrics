"""Purpose: Test the stack review RPC methods and the service behind them.

Description: ``processing:remake_preview`` starts the library's preview remake
as a background job, and ``quality:set_aside_frames`` reads the set-aside
listing. The tests check that both are declared and routed, that a quick job
gives back its result, that a slow job gives back a job id, that a quick
failure reaches the caller as an error, and that the service never asks the
library to skip keeping the old pictures. The science library is a stand-in.
"""

import asyncio
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib._internal.results import PreviewRemakeResult
from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.reports import SetAsideFrameRecord
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.processing.stack_review_service import StackReviewService
from backend.services.rpc_protocol import serialize_rpc_result


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


def test_the_set_aside_reply_keeps_the_stored_kind_and_the_missing_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reply keeps the stored kind and invents no reason."""
    frames = [
        SetAsideFrameRecord(
            file="a.fits",
            path="/x/_excluded/a.fits",
            kind="trailed",
            reason="3 stars against a typical 40",
            reason_source="stored_row",
        ),
        SetAsideFrameRecord(file="b.fits", path="/x/_excluded/b.fits", reason_source="folder_only"),
    ]
    calls: list[str] = []

    def set_aside_frames(target: str) -> list[SetAsideFrameRecord]:
        """Record the call and give the frames.

        Returns
        -------
        frames : `list` [`SetAsideFrameRecord`]
            The two frames.
        """
        calls.append(target)
        return frames

    monkeypatch.setattr(container.astrometrics.quality, "set_aside_frames", set_aside_frames)

    reply = asyncio.run(rpc_registry.execute("quality:set_aside_frames", {"target": "M 81"}))

    plain = serialize_rpc_result(reply)
    assert calls == ["M 81"]
    assert plain[0]["kind"] == "trailed"
    assert plain[0]["reasonSource"] == "stored_row"
    assert plain[1]["kind"] is None
    assert plain[1]["reason"] == ""


def test_the_comparison_asks_for_the_previous_stack_only() -> None:
    """The comparison is with the previous stack, run as a job."""
    from astrometricslib.reports import StackQualityReport

    assert "quality:measure_stack" in RPC_METHODS
    assert rpc_registry._handlers["quality:measure_stack"] == (
        "stack_review_service",
        "start_stack_comparison",
    )
    calls: list[dict[str, Any]] = []

    def measure_stack(target: str, **options: Any) -> StackQualityReport:
        """Record the call and give an empty report.

        Returns
        -------
        report : `StackQualityReport`
            A report with a note and no comparison.
        """
        calls.append({"target": target, **options})
        return StackQualityReport(
            stack_path="/s/M_81_Stacked.fits",
            notes=["No previous stack is kept, so there is nothing to compare with."],
        )

    service = StackReviewService(SimpleNamespace(quality=SimpleNamespace(measure_stack=measure_stack)))

    reply = service.start_stack_comparison("M 81", "spectral")

    assert reply.status == "completed"
    assert reply.result is not None
    assert reply.result["comparison"] is None
    assert "No previous stack" in reply.result["notes"][0]
    assert calls == [
        {"target": "M 81", "kind": "spectral", "include": [], "compare_to": "previous", "register_job": True}
    ]
