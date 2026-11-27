"""Tests of the File Browser's frame statuses and the Put back call.

`TargetService.get_frame_statuses` looks the target up with the shared
resolver and passes it to the library's
frame-status rule (`targets.frames.get_statuses`, tested in the library, which
finds the newest stack file itself) and passes its answer through.
`put_back_frames` moves chosen set-aside frames back through the library.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib import FrameRecord, Target
from astrometricslib.exceptions import NotFoundError
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.target_service import TargetService
from backend.services.subject_resolver import SubjectResolver


def _service() -> TargetService:
    """Build a service whose library is a stand-in.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library that holds every target but "Nope".
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.side_effect = lambda target_id: (
        None if target_id == "Nope" else Target(id=target_id)
    )
    service.subjects = SubjectResolver(service.astrometrics)
    service.astrometrics.targets.frames.get_statuses.return_value.model_dump.return_value = {"frames": []}
    return service


def test_the_library_rule_gets_only_the_target() -> None:
    """The library finds the newest stack; the service sends the target."""
    service = _service()

    service.get_frame_statuses("M 1")

    service.astrometrics.targets.frames.get_statuses.assert_called_once_with(Target(id="M 1"))
    service.astrometrics.targets.frames.get_statuses.return_value.model_dump.assert_called_once_with(
        by_alias=True
    )


def test_the_flagged_reason_reaches_the_answer() -> None:
    """A flagged frame comes back as the library words it."""
    service = _service()
    flagged = {
        "path": "/a.fits",
        "state": "flagged",
        "elongation": 1.25,
        "reason": "Stacked, but its stars are stretched: elongation 1.25, warning level 1.20, "
        "set-aside threshold 1.32.",
    }
    statuses = service.astrometrics.targets.frames.get_statuses
    statuses.return_value.model_dump.return_value = {"frames": [flagged]}

    assert service.get_frame_statuses("M 1")["frames"] == [flagged]


def test_an_unknown_target_is_the_librarys_not_found_error() -> None:
    """A target that is not there is a `NotFoundError` reported as an error."""
    service = _service()

    with pytest.raises(NotFoundError, match="Nope") as raised:
        service.get_frame_statuses("Nope")
    assert raised.value.report == "error"
    service.astrometrics.targets.frames.get_statuses.assert_not_called()


def test_put_back_goes_through_the_library_and_reports_what_moved() -> None:
    """The service passes the paths to the library's restore call."""
    service = _service()
    service.astrometrics.targets.frames.restore.return_value = [FrameRecord(path="/frames/lights/M 1/c.fits")]

    result = service.put_back_frames("M 1", ["/frames/lights/M 1/_excluded/c.fits"])

    assert result == {"restored": ["/frames/lights/M 1/c.fits"]}
    service.astrometrics.targets.frames.restore.assert_called_once_with(
        Target(id="M 1"), ["/frames/lights/M 1/_excluded/c.fits"]
    )


def test_the_two_calls_are_declared_and_wired() -> None:
    """Both RPC names are public and reach the target service."""
    for method, handler in (
        ("target:get_frame_statuses", "get_frame_statuses"),
        ("target:put_back_frames", "put_back_frames"),
    ):
        assert method in RPC_METHODS
        assert rpc_registry._handlers[method] == ("target_service", handler)
