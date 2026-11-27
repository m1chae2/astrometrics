"""Purpose: Test the ``processing:check_stack`` RPC method and its service.

Description: The check reads every frame, so the service runs the library's
``processing.stacking.predict`` as a background job. The tests check that the
method is declared and routed, that ticked frames (paths or dictionaries)
reach the library as the target's own frame records, that a frame choice is
turned into the frames of the library's frame query, that mixing the two or
naming a frame the target does not have is refused, that a mixed frame list
is refused by the library's frame check before a job starts, and that the
finished result is plain data. The library is a stand-in.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.models.processing_results import StackPrediction
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.processing import stack_review_service
from backend.services.processing.stack_review_service import StackReviewService


def make_target() -> Target:
    """Make the target M 57 with three imaging frames and one spectrum.

    Returns
    -------
    target : `Target`
        The target.
    """
    frames = [
        FrameRecord(path=f"/lights/L_{number}.fits", role="LIGHT", filter="Luminance", camera="Cam")
        for number in (1, 2, 3)
    ]
    frames.append(FrameRecord(path="/lights/SPEC_1.fits", role="LIGHT", filter="SPEC", camera="Cam"))
    return Target(id="M 57", frames=frames)


def make_service(calls: list[dict[str, Any]]) -> StackReviewService:
    """Build a service over a stand-in library that records its calls.

    Parameters
    ----------
    calls : `list` [`dict`]
        Receives the arguments of each ``stacking.predict`` call.

    Returns
    -------
    service : `StackReviewService`
        The service under test.
    """
    target = make_target()

    def predict(subject: Target, frames: Any = None) -> StackPrediction:
        """Record the call.

        Returns
        -------
        prediction : `StackPrediction`
            A prediction with three frames selected.
        """
        calls.append({"target": subject, "frames": frames})
        return StackPrediction(target_id=subject.id, kind="imaging", frames_selected=3, frames_kept=3)

    astrometrics = SimpleNamespace(
        processing=SimpleNamespace(stacking=SimpleNamespace(predict=predict)),
        targets=SimpleNamespace(get=lambda target_id: target if target_id == "M 57" else None),
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
    assert calls[0]["target"].id == "M 57"
    assert [frame.path for frame in calls[0]["frames"]] == ["/lights/L_3.fits", "/lights/L_1.fits"]


def test_a_frame_choice_becomes_the_frames_of_the_frame_query(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a frame list, the choices pick the frames the check is given."""
    calls: list[dict[str, Any]] = []
    asked: dict[str, Any] = {}
    chosen = make_target().frames[3:]

    def frames_for_selection(astrometrics: Any, target: Target, **choice: Any) -> list[FrameRecord]:
        """Record the choice and give the spectrum.

        Returns
        -------
        frames : `list` [`FrameRecord`]
            The spectral frame.
        """
        asked.update(choice)
        return chosen

    monkeypatch.setattr(stack_review_service, "frames_for_selection", frames_for_selection)
    make_service(calls).start_stack_check(
        "M 57", kind="spectral", filter_name="L", camera_id="ASI", focal_length_mm=530.0
    )

    assert calls[0]["frames"] == chosen
    assert asked["kind"] == "spectral"
    assert asked["filter_name"] == "L"
    assert asked["camera_id"] == "ASI"
    assert asked["focal_length_mm"] == pytest.approx(530.0)


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


def test_a_mixed_frame_list_is_refused_before_any_check_job_starts() -> None:
    """A mixed list is refused by the frame check and never checked."""
    calls: list[dict[str, Any]] = []
    service = make_service(calls)
    with pytest.raises(InvalidArgumentError, match=r"3 imaging, 1 spectral"):
        service.start_stack_check(
            "M 57",
            image_files=["/lights/L_1.fits", "/lights/L_2.fits", "/lights/L_3.fits", "/lights/SPEC_1.fits"],
        )
    assert calls == []
