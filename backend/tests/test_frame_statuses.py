"""Tests of the File Browser's frame statuses and the Put back call.

`TargetService.get_frame_statuses` says, for each frame of a target, whether
it is included, not stacked yet or set aside, and lists the frames in
`_excluded` folders (which the target's own frame list does not hold).
`put_back_frames` moves chosen set-aside frames back through the library.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import FrameRecord, Target
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.frame_status import build_frame_statuses
from backend.services.data.target_service import TargetService


def _set_aside(file_name: str, kind: str | None = "clouded") -> SimpleNamespace:
    """Make a stand-in for the library's set-aside record.

    Returns
    -------
    record : `SimpleNamespace`
        The fields `build_frame_statuses` reads.
    """
    return SimpleNamespace(
        path=f"/frames/lights/M 1/_excluded/{file_name}",
        file=file_name,
        kind=kind,
        reason="203 stars against a typical 3348",
        exposure_seconds=120.0,
        original_path=f"/frames/lights/M 1/{file_name}",
        restorable=True,
        restore_note="",
    )


def test_frames_before_the_stack_are_included_and_later_ones_are_not_stacked() -> None:
    """A frame taken after the latest stack file is "not stacked yet"."""
    frames = [FrameRecord(path="/a.fits", timestamp=100.0), FrameRecord(path="/b.fits", timestamp=300.0)]

    report = build_frame_statuses(frames, [], stacked_at=200.0)

    assert [(status.name, status.state) for status in report.frames] == [
        ("a.fits", "included"),
        ("b.fits", "not_stacked"),
    ]
    assert report.stacked_at == pytest.approx(200.0)


def test_with_no_stack_every_frame_is_not_stacked_yet() -> None:
    """A target that has no stack has nothing included."""
    report = build_frame_statuses([FrameRecord(path="/a.fits", timestamp=1.0)], [], stacked_at=None)

    assert report.frames[0].state == "not_stacked"


def test_a_frame_with_no_time_counts_as_taken_before_the_stack() -> None:
    """Without a recorded time the frame is not called new."""
    report = build_frame_statuses([FrameRecord(path="/a.fits")], [], stacked_at=200.0)

    assert report.frames[0].state == "included"


def test_set_aside_frames_are_listed_with_their_reason_kind_and_date() -> None:
    """A frame in `_excluded` keeps the stacker's words and its name's date."""
    record = _set_aside("M1_2026-10-03T08-15-30_120s.fits")

    report = build_frame_statuses([], [record], stacked_at=None)

    [status] = report.frames
    assert status.state == "set_aside"
    assert status.kind == "clouded"
    assert status.reason == "203 stars against a typical 3348"
    assert status.date == "2026-10-03 08:15:30"
    assert status.exposure == "120"
    assert status.restorable is True


def _service(tmp_path: Path, target: Target, set_aside: list[SimpleNamespace]) -> TargetService:
    """Build a service whose library holds one target and its set-aside frames.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.return_value = target
    service.astrometrics.quality.set_aside_frames.return_value = set_aside
    service.config = SimpleNamespace(get_stacks_path=lambda: tmp_path / "stacks")
    return service


def test_service_reads_the_stack_time_and_lists_set_aside_frames(tmp_path: Path) -> None:
    """Frames taken after the stack file's time are "not stacked yet"."""
    stack = tmp_path / "M_1_Stacked.fits"
    stack.write_bytes(b"x")
    os.utime(stack, (1000.0, 1000.0))
    target = Target(
        id="M 1",
        frames=[FrameRecord(path="/a.fits", timestamp=500.0), FrameRecord(path="/b.fits", timestamp=2000.0)],
    )
    target.stacking.stacked_image = str(stack)

    report = _service(tmp_path, target, [_set_aside("c.fits", "trailed")]).get_frame_statuses("M 1")

    assert report["stackedAt"] == pytest.approx(1000.0)
    assert [(row["name"], row["state"]) for row in report["frames"]] == [
        ("a.fits", "included"),
        ("b.fits", "not_stacked"),
        ("c.fits", "set_aside"),
    ]
    assert report["frames"][2]["kind"] == "trailed"
    assert report["frames"][2]["originalPath"] == "/frames/lights/M 1/c.fits"


def test_service_answers_an_unknown_target_with_an_empty_list(tmp_path: Path) -> None:
    """A target that is not there is an empty answer, not an error."""
    service = _service(tmp_path, None, [])
    service.astrometrics.targets.get.return_value = None

    assert service.get_frame_statuses("Nope") == {"frames": [], "stackedAt": None}


def test_put_back_goes_through_the_library_and_reports_what_moved(tmp_path: Path) -> None:
    """The service passes the paths to the library's put-back call."""
    service = _service(tmp_path, Target(id="M 1"), [])
    service.astrometrics.quality.put_back_set_aside_frames.return_value = ["/frames/lights/M 1/c.fits"]

    result = service.put_back_frames("M 1", ["/frames/lights/M 1/_excluded/c.fits"])

    assert result == {"restored": ["/frames/lights/M 1/c.fits"]}
    service.astrometrics.quality.put_back_set_aside_frames.assert_called_once_with(
        "M 1", ["/frames/lights/M 1/_excluded/c.fits"]
    )


def test_the_two_calls_are_declared_and_wired() -> None:
    """Both RPC names are public and reach the target service."""
    for method, handler in (
        ("target:get_frame_statuses", "get_frame_statuses"),
        ("target:put_back_frames", "put_back_frames"),
    ):
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method] == ("target_service", handler)
