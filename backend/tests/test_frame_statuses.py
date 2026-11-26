"""Tests of the File Browser's frame statuses and the Put back call.

`TargetService.get_frame_statuses` gives the library's frame-status rule
(`quality.frame_statuses`, tested in the library) the time of the newest
stack file and passes the library's flagged reason through. `put_back_frames`
moves chosen set-aside frames back through the library.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import Target
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.target_service import TargetService


def _service(tmp_path: Path, target: Target | None) -> TargetService:
    """Build a service whose library holds one target.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.return_value = target
    service.astrometrics.quality.frame_statuses.return_value.model_dump.return_value = {"frames": []}
    service.config = SimpleNamespace(get_stacks_path=lambda: tmp_path / "stacks")
    return service


def test_the_library_rule_receives_the_newest_stack_time(tmp_path: Path) -> None:
    """The newest stack file's time reaches the rule, and nothing else."""
    stack = tmp_path / "M_1_Stacked.fits"
    stack.write_bytes(b"x")
    os.utime(stack, (1000.0, 1000.0))
    target = Target(id="M 1")
    target.stacking.stacked_image = str(stack)
    service = _service(tmp_path, target)

    service.get_frame_statuses("M 1")

    arguments = service.astrometrics.quality.frame_statuses.call_args.args
    assert arguments[0] is target
    assert arguments[1:] == (pytest.approx(1000.0),)


def test_a_target_with_no_stack_passes_no_time(tmp_path: Path) -> None:
    """With no stack file on disk the rule gets `None` for the stack time."""
    service = _service(tmp_path, Target(id="M 1"))

    service.get_frame_statuses("M 1")

    assert service.astrometrics.quality.frame_statuses.call_args.args[1:] == (None,)


def test_the_flagged_reason_reaches_the_answer(tmp_path: Path) -> None:
    """A flagged frame comes back as the library words it."""
    service = _service(tmp_path, Target(id="M 1"))
    flagged = {
        "path": "/a.fits",
        "state": "flagged",
        "elongation": 1.25,
        "reason": "Stacked, but its stars are stretched: elongation 1.25, warning level 1.20, "
        "set-aside threshold 1.32.",
    }
    service.astrometrics.quality.frame_statuses.return_value.model_dump.return_value = {"frames": [flagged]}

    assert service.get_frame_statuses("M 1")["frames"] == [flagged]


def test_service_answers_an_unknown_target_with_an_empty_list(tmp_path: Path) -> None:
    """A target that is not there is an empty answer, not an error."""
    service = _service(tmp_path, None)

    assert service.get_frame_statuses("Nope") == {"frames": [], "stackedAt": None}


def test_put_back_goes_through_the_library_and_reports_what_moved(tmp_path: Path) -> None:
    """The service passes the paths to the library's put-back call."""
    service = _service(tmp_path, Target(id="M 1"))
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
