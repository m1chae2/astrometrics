"""Purpose: Tests that a missing frame file is not a server error.

Description: A target record can name a stack that is no longer on disk. Asking
for its header must return empty entries instead of raising, so opening such
a target does not log an error or show a failed request each time. A header
that is read comes with the library's display turn.
"""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import NotFoundError
from backend.services.data.target_service import TargetService


def _service(get_header: Callable[..., Any]) -> TargetService:
    """Build a service whose target API uses the given `get_header`.

    Returns
    -------
    service : `TargetService`
        A service with a fake target API.
    """
    service = TargetService.__new__(TargetService)
    targets = SimpleNamespace(
        get=lambda target_id: SimpleNamespace(id=target_id),
        get_header=get_header,
        display_turn=lambda path, target: 180,
    )
    service.astrometrics = SimpleNamespace(targets=targets)
    return service


def test_a_missing_file_gives_an_empty_header() -> None:
    """The header of a file that is gone has no entries and no turn."""

    def missing(path: str, target: object) -> list:
        """Raise as the library does for a file that is not there.

        Raises
        ------
        NotFoundError
            Always.
        """
        raise NotFoundError(f"File not found: {path}")

    assert _service(missing).get_frame_header("C 2022 E3 ZTF", "/gone.fits") == {
        "entries": [],
        "displayTurn": 0,
    }


def test_a_header_comes_with_the_display_turn() -> None:
    """The entries come with the turn the library decides."""
    entries = [{"key": "CROTA2", "value": "16.2", "comment": ""}]

    answer = _service(lambda path, target: entries).get_frame_header("NGC 7331", "/frame_001.fits")

    assert answer == {"entries": entries, "displayTurn": 180}


def test_other_failures_still_raise() -> None:
    """Only a missing file is quiet; a corrupt header is still an error."""

    def broken(path: str, target: object) -> list:
        """Raise a different failure.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("corrupt")

    with pytest.raises(OSError, match="corrupt"):
        _service(broken).get_frame_header("M_81", "/bad.fits")
