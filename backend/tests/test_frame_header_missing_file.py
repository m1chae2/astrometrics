"""Purpose: Tests that a missing frame file is not a server error.

Description: A target record can name a stack that is no longer on disk. Asking
for its header must return an empty list instead of raising, so opening such
a target does not log an error or show a failed request each time.
"""

from types import SimpleNamespace

import pytest

from backend.services.data.target_service import TargetService


def _service(get_header) -> TargetService:  # ruff: ignore[missing-type-function-argument]
    """Build a service whose target API uses the given `get_header`.

    Returns
    -------
    service : `TargetService`
        A service with a fake target API.
    """
    service = TargetService.__new__(TargetService)
    targets = SimpleNamespace(get=lambda target_id: SimpleNamespace(id=target_id), get_header=get_header)
    service.astrometrics = SimpleNamespace(targets=targets)
    return service


def test_a_missing_file_gives_an_empty_header() -> None:
    """The header of a file that is gone is an empty list."""

    def missing(path: str, target: object) -> list:
        """Raise as the library does for a file that is not there.

        Raises
        ------
        FileNotFoundError
            Always.
        """
        raise FileNotFoundError(path)

    assert _service(missing).get_frame_header("C 2022 E3 ZTF", "/gone.fits") == []


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
