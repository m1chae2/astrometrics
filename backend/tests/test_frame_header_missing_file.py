"""Purpose: Tests that a missing frame file is not a server error.

Description: A target record can name a stack that is no longer on disk. Asking
for its header must return empty entries instead of raising, so opening such
a target does not log an error or show a failed request each time. A header
that is read comes with the library's display turn. Without a target, the
file must be inside the library folders, and there is no turn.
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.data.target_service import TargetService


def _service(read_cards: Callable[..., Any], library: Path | None = None) -> TargetService:
    """Build a service whose target API reads cards with the given function.

    Parameters
    ----------
    read_cards : `Callable`
        Called with the path and the target; gives the cards.
    library : `pathlib.Path`, optional
        The library folder the settings name.

    Returns
    -------
    service : `TargetService`
        A service with a fake target API.
    """
    service = TargetService.__new__(TargetService)
    targets = SimpleNamespace(
        get=lambda target_id, required=False: SimpleNamespace(id=target_id),
        read_frame_header=lambda path, target, as_cards: read_cards(path, target),
        display_turn=lambda path, target: 180,
    )
    service.astrometrics = SimpleNamespace(targets=targets)
    service.config = SimpleNamespace(get_library_path=lambda: str(library or "/nowhere"))
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

    assert _service(missing).get_frame_header("/gone.fits", target_id="C 2022 E3 ZTF") == {
        "entries": [],
        "displayTurn": 0,
    }


def test_a_header_comes_with_the_display_turn() -> None:
    """The entries come with the turn the library decides."""
    entries = [{"key": "CROTA2", "value": "16.2", "comment": ""}]

    answer = _service(lambda path, target: entries).get_frame_header("/frame_001.fits", target_id="NGC 7331")

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
        _service(broken).get_frame_header("/bad.fits", target_id="M_81")


def test_without_a_target_the_file_must_be_in_the_library(tmp_path: Path) -> None:
    """With no target, a library file is read with no turn; others are not."""
    entries = [{"key": "FILTER", "value": "L", "comment": ""}]
    seen: list[object] = []

    def read(path: str, target: object) -> list:
        """Record the target the library was given.

        Returns
        -------
        cards : `list`
            The cards.
        """
        seen.append(target)
        return entries

    service = _service(read, library=tmp_path)
    answer = service.get_frame_header(str(tmp_path / "frame.fits"))
    assert answer == {"entries": entries, "displayTurn": 0}
    assert seen == [None]
    with pytest.raises(InvalidArgumentError):
        service.get_frame_header("/etc/hostname")
