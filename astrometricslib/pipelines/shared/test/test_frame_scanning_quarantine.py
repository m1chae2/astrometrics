"""Tests that the frame scanner leaves the `_excluded` folder alone.

The stacking pipeline moves clouded and trailed frames into an `_excluded`
folder inside the target's folder. A scan that walked into it would add the
frames straight back to the target.
"""

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.pipelines.shared import frame_scanning
from astrometricslib.pipelines.shared.previous_stack_path import PREVIOUS_STACK_FOLDER_NAME
from astrometricslib.pipelines.shared.quarantine_path import QUARANTINE_FOLDER_NAME


def test_scan_target_directory_skips_the_quarantine_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Frames in `_excluded` are not added; frames beside it are."""
    camera_directory = tmp_path / "lights" / "M 1" / "Scope" / "Camera"
    (camera_directory / QUARANTINE_FOLDER_NAME).mkdir(parents=True)
    (camera_directory / "kept.fits").write_bytes(b"x")
    (camera_directory / QUARANTINE_FOLDER_NAME / "moved.fits").write_bytes(b"x")

    monkeypatch.setattr(frame_scanning, "is_stacked_output", lambda *args, **kwargs: False)
    monkeypatch.setattr(frame_scanning, "_read_header_or_none", lambda path: None)
    monkeypatch.setattr(
        frame_scanning, "create_frame_record_from_fits", lambda path: SimpleNamespace(path=path)
    )
    target = SimpleNamespace(id="M 1", frames=[], recalculate_total_exposure=lambda: None)

    frame_scanning.scan_target_directory(target, str(tmp_path))

    assert [os.path.basename(frame.path) for frame in target.frames] == ["kept.fits"]


def test_scan_target_directory_skips_the_previous_stack_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Files in `_previous` are not added; files beside it are."""
    camera_directory = tmp_path / "lights" / "M 1" / "Scope" / "Camera"
    (camera_directory / PREVIOUS_STACK_FOLDER_NAME).mkdir(parents=True)
    (camera_directory / "kept.fits").write_bytes(b"x")
    (camera_directory / PREVIOUS_STACK_FOLDER_NAME / "old_stack.fits").write_bytes(b"x")

    monkeypatch.setattr(frame_scanning, "is_stacked_output", lambda *args, **kwargs: False)
    monkeypatch.setattr(frame_scanning, "_read_header_or_none", lambda path: None)
    monkeypatch.setattr(
        frame_scanning, "create_frame_record_from_fits", lambda path: SimpleNamespace(path=path)
    )
    target = SimpleNamespace(id="M 1", frames=[], recalculate_total_exposure=lambda: None)

    frame_scanning.scan_target_directory(target, str(tmp_path))

    assert [os.path.basename(frame.path) for frame in target.frames] == ["kept.fits"]
