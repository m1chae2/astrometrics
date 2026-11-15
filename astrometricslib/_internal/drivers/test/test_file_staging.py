"""Purpose: Tests for `stage_file`, which puts a frame into a work folder.

Description: Each system has one fixed way to stage a frame: a symbolic link
on Linux (`_staging_posix`) and a hard link on Windows (`_staging_windows`).
Both modules import on any system, and a hard link works on Linux too, so
both are tested directly here; nothing pretends to be another system. A
failed hard link is simulated by making ``os.link`` fail, as it does on
Windows for a frame on another drive.
"""

import errno
import os
import sys
from pathlib import Path

import pytest

from astrometricslib._internal.drivers import _staging_posix, _staging_windows, file_staging
from astrometricslib.foundation.errors import ConfigurationError


def _make_source(tmp_path: Path) -> Path:
    """Write a small original frame.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        The test folder.

    Returns
    -------
    source : `pathlib.Path`
        The original file.
    """
    source = tmp_path / "frame.fits"
    source.write_bytes(b"frame data")
    return source


def test_file_staging_uses_this_systems_rule() -> None:
    """`file_staging` gives this system's rule, chosen once at import."""
    expected = _staging_windows if sys.platform == "win32" else _staging_posix
    assert file_staging.stage_file is expected.stage_file


@pytest.mark.skipif(sys.platform == "win32", reason="Windows makes symbolic links only in Developer Mode")
def test_linux_stages_a_frame_as_a_symbolic_link(tmp_path: Path) -> None:
    """The Linux rule makes a symbolic link to the original."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    _staging_posix.stage_file(str(source), str(destination))
    assert destination.is_symlink()
    assert os.path.realpath(destination) == os.path.realpath(source)


@pytest.mark.skipif(sys.platform == "win32", reason="Windows makes symbolic links only in Developer Mode")
def test_linux_does_not_fall_back_when_the_link_fails(tmp_path: Path) -> None:
    """An existing name is an error, not a reason to copy."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    destination.write_bytes(b"in the way")
    with pytest.raises(FileExistsError):
        _staging_posix.stage_file(str(source), str(destination))
    assert destination.read_bytes() == b"in the way"


def test_windows_stages_a_frame_as_a_hard_link(tmp_path: Path) -> None:
    """The Windows rule gives the original a second name: the same file."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    _staging_windows.stage_file(str(source), str(destination))
    assert not destination.is_symlink()
    assert os.path.samefile(destination, source)


def test_windows_stops_with_a_clear_reason_when_no_hard_link_is_possible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame on another drive stops the run; it is not copied or skipped."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"

    def refuse_link(*_arguments: object) -> None:
        """Fail the way Windows does for a frame on another drive.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError(errno.EXDEV, "The system cannot move the file to a different disk drive")

    monkeypatch.setattr(_staging_windows.os, "link", refuse_link)
    with pytest.raises(ConfigurationError, match="same NTFS drive"):
        _staging_windows.stage_file(str(source), str(destination))
    assert not destination.exists()


def test_windows_passes_on_a_missing_frame_as_it_is(tmp_path: Path) -> None:
    """A frame that does not exist is reported as missing, as it is."""
    with pytest.raises(FileNotFoundError):
        _staging_windows.stage_file(str(tmp_path / "missing.fits"), str(tmp_path / "staged.fits"))
