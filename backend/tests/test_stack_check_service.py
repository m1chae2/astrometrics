"""Purpose: Test the ``processing:check_stack`` RPC method and its service.

Description: The check reads every frame, so the service runs the library's
`predict_stack` as a background job. The tests check that the method is
declared and routed, that ticked frames (paths or dictionaries) reach the
library as the target's own frame records, that a frame choice reaches it
unchanged, that mixing the two or naming a frame the target does not have is
refused, and that the finished result is plain data. The library is a
stand-in.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.models.processing_results import StackPrediction
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.processing.stack_review_service import StackReviewService


def make_service(calls: list[dict[str, Any]]) -> StackReviewService:
    """Build a service over a stand-in library that records its calls.

    Parameters
    ----------
    calls : `list` [`dict`]
        Receives the arguments of each `predict_stack` call.

    Returns
    -------
    service : `StackReviewService`
        The service under test.
    """
    frames = [SimpleNamespace(path=f"/lights/L_{number}.fits") for number in (1, 2, 3)]

    def predict_stack(target: str, **options: Any) -> StackPrediction:
        """Record the call and give a small prediction.

        Returns
        -------
        prediction : `StackPrediction`
            A prediction with three frames selected.
        """
        calls.append({"target": target, **options})
        return StackPrediction(target_id=target, kind="imaging", frames_selected=3, frames_kept=3)

    astrometrics = SimpleNamespace(
        processing=SimpleNamespace(predict_stack=predict_stack),
        targets=SimpleNamespace(get=lambda target_id: SimpleNamespace(frames=frames) if target_id else None),
    )
    return StackReviewService(astrometrics)


def test_the_method_is_declared_and_routed() -> None:
    """The check is public and goes to the review service."""
    assert "processing:check_stack" in RPC_METHODS
    assert rpc_registry._handlers["processing:check_stack"] == ("stack_review_service", "start_stack_check")


def test_ticked_frames_reach_the_library_as_the_targets_records() -> None:
    """Paths and dictionaries become the target's own frame records."""
    calls: list[dict[str, Any]] = []
    service = make_service(calls)

    reply = service.start_stack_check("M 57", image_files=["/lights/L_3.fits", {"path": "/lights/L_1.fits"}])

    assert reply.status == "completed"
    assert reply.result is not None
    assert reply.result["frames_selected"] == 3
    assert [frame.path for frame in calls[0]["frames"]] == ["/lights/L_3.fits", "/lights/L_1.fits"]
    assert calls[0]["register_job"] is True
    assert calls[0]["kind"] == "imaging"


def test_a_frame_choice_reaches_the_library_unchanged() -> None:
    """Without a frame list, the choices go straight to the library."""
    calls: list[dict[str, Any]] = []
    service = make_service(calls)

    service.start_stack_check(
        "M 57", kind="spectral", filter_name="L", camera_id="ASI", focal_length_mm=530.0
    )

    assert calls[0]["frames"] is None
    assert calls[0]["kind"] == "spectral"
    assert calls[0]["filter_name"] == "L"
    assert calls[0]["camera_id"] == "ASI"
    assert calls[0]["focal_length_mm"] == pytest.approx(530.0)


def test_a_list_and_a_choice_together_are_refused() -> None:
    """The caller must pass one or the other, as for the stack itself."""
    service = make_service([])
    with pytest.raises(InvalidArgumentError, match="not both"):
        service.start_stack_check("M 57", image_files=["/lights/L_1.fits"], filter_name="L")


def test_a_frame_the_target_does_not_have_is_refused() -> None:
    """A path that is not one of the target's frames is a not-found error."""
    service = make_service([])
    with pytest.raises(NotFoundError, match="not frames of"):
        service.start_stack_check("M 57", image_files=["/lights/other.fits"])
