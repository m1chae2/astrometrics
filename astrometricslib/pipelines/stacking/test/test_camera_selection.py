"""Tests for keeping a stack to the frames of one camera.

Capella once had 3 Nikon and 2 ASI spectra. The gain check kept the most
common gain, which was the Nikon's, so a request for the ASI stack made a
stack of the wrong camera's frames with no error.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.errors import ConflictError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.pre_processing.camera_selection import (
    choose_camera_frames,
    ensure_single_camera,
    split_frames_by_camera,
)

ASI = "ZWO ASI 533MM Pro"
NIKON = "Nikon DSLR DSC D5300"


def make_frames(*cameras: str) -> list[SimpleNamespace]:
    """Make one stand-in frame per camera name given.

    Returns
    -------
    frames : `list` [`SimpleNamespace`]
        Frames with a ``camera`` and a numbered ``path``.
    """
    return [SimpleNamespace(camera=camera, path=f"/f/{index}.fits") for index, camera in enumerate(cameras)]


def test_frames_are_grouped_by_camera() -> None:
    """Each camera gets its own group, in the order the frames came."""
    frames = make_frames(NIKON, ASI, NIKON, ASI, NIKON)

    groups = split_frames_by_camera(frames)

    assert {name: len(group) for name, group in groups.items()} == {NIKON: 3, ASI: 2}


def test_two_spellings_of_one_camera_are_one_camera() -> None:
    """A listed alias of a camera is the same camera."""
    frames = make_frames("ZWO CCD ASI533MM Pro", ASI)

    assert len(split_frames_by_camera(frames)) == 1
    ensure_single_camera(frames)


def test_frames_with_no_camera_recorded_are_not_counted() -> None:
    """Nothing says an unknown-camera frame differs from the others."""
    frames = make_frames(ASI, "Unknown", "", None)

    assert len(split_frames_by_camera(frames)) == 1
    ensure_single_camera(frames)


def test_frames_from_two_cameras_are_refused_with_the_counts() -> None:
    """The error names each camera and how many frames it took."""
    with pytest.raises(ConflictError, match="more than one camera") as error:
        ensure_single_camera(make_frames(NIKON, NIKON, NIKON, ASI, ASI))

    assert f"'{NIKON}' (3 frames)" in str(error.value)
    assert f"'{ASI}' (2 frames)" in str(error.value)


def test_a_part_of_the_camera_name_picks_its_frames() -> None:
    """``ASI 533MM`` picks the ASI frames, though the Nikon has more."""
    frames = make_frames(NIKON, NIKON, NIKON, ASI, ASI)

    chosen, problem = choose_camera_frames(frames, "ASI 533MM")

    assert problem is None
    assert [frame.camera for frame in chosen] == [ASI, ASI]


def test_a_camera_that_took_no_frames_is_reported_with_the_cameras_there() -> None:
    """The problem sentence lists what is there."""
    chosen, problem = choose_camera_frames(make_frames(NIKON, ASI), "QHY")

    assert chosen == []
    assert NIKON in problem
    assert ASI in problem


def test_a_name_that_fits_two_cameras_is_not_guessed() -> None:
    """``ZWO`` fits two ZWO cameras, so the caller must say which."""
    chosen, problem = choose_camera_frames(make_frames("ZWO ASI 533MM Pro", "ZWO ASI 2600MM Pro"), "ZWO")

    assert chosen == []
    assert "more than one camera" in problem


def test_the_stacking_stage_refuses_mixed_cameras() -> None:
    """Stacking raises before any work when the frames mix cameras."""
    target = Target(
        id="T 1",
        frames=[
            FrameRecord(path="/f/a.fits", role="LIGHT", camera=NIKON, exposure="60", timestamp=1.0),
            FrameRecord(path="/f/b.fits", role="LIGHT", camera=ASI, exposure="60", timestamp=2.0),
        ],
    )

    with pytest.raises(ConflictError, match="more than one camera"):
        stacking_tasks.stack_frames(target)
