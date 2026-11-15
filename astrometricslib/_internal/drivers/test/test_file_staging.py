"""Purpose: Tests for `stage_file`, which puts a frame into a work folder.

Description: Linux always allows a symbolic link, so the first test uses the
real system. The fallbacks (hard link, then copy) are tested by making the
system calls fail, the way Windows does without Developer Mode or on a
drive that cannot hold a hard link.
"""

import os
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.file_staging import STAGED_BY_COPY, STAGED_BY_LINK, stage_file


def _refuse(*_arguments: object) -> None:
    """Fail like a system call the system does not allow.

    Raises
    ------
    OSError
        Always.
    """
    raise OSError("not allowed")


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


def test_stage_file_makes_a_symbolic_link_when_it_can(tmp_path: Path) -> None:
    """The first choice is a symbolic link to the original."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    assert stage_file(str(source), str(destination)) == STAGED_BY_LINK
    assert destination.read_bytes() == b"frame data"
    assert os.path.islink(destination)


def test_stage_file_makes_a_hard_link_when_the_symbolic_link_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no symbolic link allowed, the same file gets a second name."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    monkeypatch.setattr(os, "symlink", _refuse)
    assert stage_file(str(source), str(destination)) == STAGED_BY_LINK
    assert not os.path.islink(destination)
    assert os.path.samefile(source, destination)


def test_stage_file_copies_when_no_link_is_possible(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With neither link allowed, the file is copied."""
    source = _make_source(tmp_path)
    destination = tmp_path / "staged.fits"
    monkeypatch.setattr(os, "symlink", _refuse)
    monkeypatch.setattr(os, "link", _refuse)
    assert stage_file(str(source), str(destination)) == STAGED_BY_COPY
    assert destination.read_bytes() == b"frame data"
    assert not os.path.samefile(source, destination)


def test_stage_file_raises_when_the_original_is_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that cannot be linked or copied is an error."""
    monkeypatch.setattr(os, "symlink", _refuse)
    monkeypatch.setattr(os, "link", _refuse)
    with pytest.raises(OSError):
        stage_file(str(tmp_path / "missing.fits"), str(tmp_path / "staged.fits"))
