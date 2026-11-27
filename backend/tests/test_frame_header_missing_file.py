"""Purpose: Tests that a missing frame file is not a server error.

Description: A target record can name a stack that is no longer on disk. Asking
for its header must return empty entries instead of raising, so opening such
a target does not log an error or show a failed request each time. A header
that is read comes back as text cards with the library's display turn. With a
target, the file must be one of its frames or stacks; without one, the file
must be inside the library folders, and there is no turn.
"""

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
from astropy.io import fits

from astrometricslib import FrameRecord, Target
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.services.data.target_service import TargetService

KNOWN_PATHS = ("/gone.fits", "/frame_001.fits", "/bad.fits")
"""The frames each fake target holds."""


def _service(read_header: Callable[[str], fits.Header], library: Path | None = None) -> TargetService:
    """Build a service whose target API reads headers with the given function.

    Parameters
    ----------
    read_header : `Callable`
        Called with the path; gives the header.
    library : `pathlib.Path`, optional
        The library folder the settings name.

    Returns
    -------
    service : `TargetService`
        A service with a fake target API.
    """
    service = TargetService.__new__(TargetService)
    frames = SimpleNamespace(get_header=read_header, get_rotation=lambda target, path: 180)
    targets = SimpleNamespace(
        get=lambda target_id, required=False: Target(
            id=target_id, frames=[FrameRecord(path=path) for path in KNOWN_PATHS]
        ),
        frames=frames,
    )
    service.astrometrics = SimpleNamespace(targets=targets)
    service.config = SimpleNamespace(get_library_path=lambda: str(library or "/nowhere"))
    return service


def _header(**cards: object) -> fits.Header:
    """Make a header with the given cards.

    Returns
    -------
    header : `astropy.io.fits.Header`
        The header.
    """
    header = fits.Header()
    for key, value in cards.items():
        header[key] = value
    return header


def test_a_missing_file_gives_an_empty_header() -> None:
    """The header of a file that is gone has no entries and no turn."""

    def missing(path: str) -> fits.Header:
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


def test_a_header_comes_as_cards_with_the_display_turn() -> None:
    """The cards come as text, with the turn the library decides."""
    answer = _service(lambda path: _header(CROTA2=16.2)).get_frame_header(
        "/frame_001.fits", target_id="NGC 7331"
    )

    assert answer == {"entries": [{"key": "CROTA2", "value": "16.2", "comment": ""}], "displayTurn": 180}


def test_a_file_of_another_target_is_refused() -> None:
    """With a target, only its own frames and stacks are read."""
    with pytest.raises(InvalidArgumentError, match="does not belong"):
        _service(lambda path: _header()).get_frame_header("/elsewhere.fits", target_id="M 81")


def test_other_failures_still_raise() -> None:
    """Only a missing file is quiet; a corrupt header is still an error."""

    def broken(path: str) -> fits.Header:
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
    service = _service(lambda path: _header(FILTER="L"), library=tmp_path)
    answer = service.get_frame_header(str(tmp_path / "frame.fits"))
    assert answer == {"entries": [{"key": "FILTER", "value": "L", "comment": ""}], "displayTurn": 0}
    with pytest.raises(InvalidArgumentError):
        service.get_frame_header("/etc/hostname")
