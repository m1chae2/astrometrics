"""Purpose: Tests of the stamped JSON files kept next to a stack.

Description: Checks that a saved record reads back while the stack is
unchanged, that a changed stack or a new layout version makes it unreadable,
and that a failed write leaves no temporary file behind. The solved star
list's own check is in the spectroscopy tests
(`test_registration_reference_field.py`).
"""

import json
import os
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared import stack_sidecar
from astrometricslib.pipelines.shared.stack_sidecar import (
    file_stamp,
    read_stamped_record,
    sidecar_path,
    write_stamped_record,
)


def _stack(tmp_path: Path) -> str:
    """Write a small stack of three frames.

    Returns
    -------
    path : `str`
        The stack's path.
    """
    path = tmp_path / "stack.fits"
    header = fits.Header()
    header["STACKCNT"] = 3
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32), header).writeto(path)
    return str(path)


def _touch_later(path: str) -> None:
    """Change a file's modification time, as a restack would."""
    status = os.stat(path)
    os.utime(path, ns=(status.st_atime_ns, status.st_mtime_ns + 1_000_000_000))


def test_the_sidecar_sits_next_to_the_stack() -> None:
    """The file name is the stack's name with the suffix."""
    assert sidecar_path("/data/M31/stack.fits", "_names.json") == "/data/M31/stack_names.json"


def test_a_record_reads_back_while_the_stack_is_unchanged(tmp_path: Path) -> None:
    """A saved record comes back with its version and stamp."""
    stack = _stack(tmp_path)
    saved = sidecar_path(stack, "_test.json")

    assert write_stamped_record(saved, stack, 2, "stack_stamp", {"value": 7})
    record = read_stamped_record(saved, stack, 2, "stack_stamp")

    assert record is not None
    assert record["value"] == 7
    assert record["stack_stamp"] == file_stamp(stack)


def test_a_changed_stack_or_version_makes_the_record_stale(tmp_path: Path) -> None:
    """A newer stack or another layout version reads as no record."""
    stack = _stack(tmp_path)
    saved = sidecar_path(stack, "_test.json")
    write_stamped_record(saved, stack, 2, "stack_stamp", {"value": 7})

    assert read_stamped_record(saved, stack, 3, "stack_stamp") is None
    _touch_later(stack)
    assert read_stamped_record(saved, stack, 2, "stack_stamp") is None


def test_a_broken_file_reads_as_no_record(tmp_path: Path) -> None:
    """A file that is not JSON, or not an object, is ignored."""
    stack = _stack(tmp_path)
    saved = sidecar_path(stack, "_test.json")
    Path(saved).write_text("not json")
    assert read_stamped_record(saved, stack, 1, "stack_stamp") is None
    Path(saved).write_text(json.dumps([1, 2]))
    assert read_stamped_record(saved, stack, 1, "stack_stamp") is None


def test_a_failed_write_leaves_no_temporary_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the final move fails, the temporary file is removed."""
    stack = _stack(tmp_path)
    saved = sidecar_path(stack, "_test.json")

    def refuse(source: str, target: str) -> None:
        """Stand in for a move that the disk refuses.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("disk full")

    monkeypatch.setattr(stack_sidecar.os, "replace", refuse)

    assert not write_stamped_record(saved, stack, 1, "stack_stamp", {"value": 1})
    assert sorted(os.listdir(tmp_path)) == ["stack.fits"]


def test_a_missing_stack_is_not_saved(tmp_path: Path) -> None:
    """With no stack there is no stamp, so nothing is written."""
    missing = str(tmp_path / "missing.fits")
    assert not write_stamped_record(sidecar_path(missing, "_test.json"), missing, 1, "stack_stamp", {})
    assert os.listdir(tmp_path) == []
