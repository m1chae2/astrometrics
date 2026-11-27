"""Tests for keeping a stack to the frames of one optic.

M 27 has Nikon D5300 frames through a 300 mm lens and through a 405 mm
telescope. The camera check passed them all, and the stack combined the two
optics as two exposure groups.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError, NotFoundError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.optic_selection import (
    choose_optic_frames,
    ensure_single_optic,
    split_frames_by_optic,
)
from astrometricslib.pipelines.shared.quality.frame_selection import FrameSelection
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.stack_request import choose_frames_to_stack

NIKON = "Nikon DSLR DSC D5300"


def make_frames(*focal_lengths: float | None) -> list[SimpleNamespace]:
    """Make one stand-in frame per focal length given.

    Returns
    -------
    frames : `list` [`SimpleNamespace`]
        Frames with a ``focal_length_mm`` and a numbered ``path``.
    """
    return [
        SimpleNamespace(focal_length_mm=focal_length, path=f"/f/{index}.fits")
        for index, focal_length in enumerate(focal_lengths)
    ]


def make_mixed_target() -> Target:
    """Build a target with one camera's light frames at two focal lengths.

    Returns
    -------
    target : `Target`
        Two frames at 405 mm and one at 300 mm, all from the Nikon.
    """
    frames = [
        FrameRecord(
            path=f"/f/{name}.fits",
            role="LIGHT",
            camera=NIKON,
            exposure="300",
            timestamp=float(index),
            focalLengthMm=focal_length,
        )
        for index, (name, focal_length) in enumerate([("a", 405.0), ("b", 405.0), ("c", 300.0)])
    ]
    return Target(id="M 27", frames=frames)


def test_frames_are_grouped_by_rounded_focal_length() -> None:
    """405.0 and 404.6 are one optic; 300 is another."""
    groups = split_frames_by_optic(make_frames(405.0, 300.0, 404.6, 300.0, 405.0))

    assert {focal_length: len(group) for focal_length, group in groups.items()} == {405: 3, 300: 2}


def test_frames_with_no_focal_length_are_not_counted() -> None:
    """Nothing says a frame with no focal length differs from the others."""
    frames = make_frames(405.0, None, 0.0)

    assert list(split_frames_by_optic(frames)) == [405]
    ensure_single_optic(frames)


def test_frames_from_two_optics_are_refused_with_the_counts() -> None:
    """The error names each focal length and how many frames it has."""
    with pytest.raises(ConflictError, match="more than one optic") as error:
        ensure_single_optic(make_frames(300.0, 300.0, 405.0))

    assert "300 mm (2 frames)" in str(error.value)
    assert "405 mm (1 frames)" in str(error.value)


def test_a_named_target_gets_the_argument_error_with_the_focal_length_hint() -> None:
    """The public stack call's mixed frames raise `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError, match="Pass focal_length_mm") as error:
        ensure_single_optic(make_frames(405.0, 405.0, 300.0), target_id="M 27")

    assert "'M 27'" in str(error.value)
    assert error.value.details == {"target": "M 27", "focal_lengths_mm": {405: 2, 300: 1}}


def test_a_focal_length_picks_its_frames() -> None:
    """405 picks the 405 mm frames, though the 300 mm optic has more."""
    frames = make_frames(300.0, 300.0, 300.0, 405.0, 405.0)

    chosen, problem = choose_optic_frames(frames, 405)

    assert problem is None
    assert [frame.focal_length_mm for frame in chosen] == [405.0, 405.0]


def test_a_focal_length_with_no_frames_is_reported_with_the_optics_there() -> None:
    """The problem sentence lists the focal lengths that are there."""
    chosen, problem = choose_optic_frames(make_frames(300.0, 405.0), 1000)

    assert chosen == []
    assert "300 mm" in problem
    assert "405 mm" in problem


def test_the_stacking_stage_refuses_mixed_optics() -> None:
    """Stacking raises before any work when one camera's frames mix optics."""
    with pytest.raises(ConflictError, match="more than one optic"):
        stacking_tasks.stack_frames(make_mixed_target())


def test_the_frame_chooser_refuses_mixed_optics_and_honours_a_focal_length() -> None:
    """Without a focal length the choice is refused; with one it is kept."""
    target = make_mixed_target()

    with pytest.raises(InvalidArgumentError, match="more than one optic"):
        choose_frames_to_stack(target, "imaging", FrameSelection(), None)

    chosen = choose_frames_to_stack(target, "imaging", FrameSelection(), None, 405)
    assert [frame.path for frame in chosen] == ["/f/a.fits", "/f/b.fits"]

    with pytest.raises(NotFoundError, match="No frames at a focal length of 1000 mm"):
        choose_frames_to_stack(target, "imaging", FrameSelection(), None, 1000)
