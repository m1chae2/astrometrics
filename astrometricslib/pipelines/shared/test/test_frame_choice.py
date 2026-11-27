"""Tests for the ``frames=`` argument and the one frame-list check.

`frames_from_choice` takes a page, a list of records or a list of paths and
refuses a page cut short. `check_frame_list` refuses an empty list and a mix
of cameras, optics or kinds.
"""

import pytest

from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.page import Page
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_choice import check_frame_list, frames_from_choice


def _frame(path: str, **fields: object) -> FrameRecord:
    """Build a light frame record.

    Parameters
    ----------
    path : `str`
        The frame's path.
    **fields : `object`
        Other fields of the record.

    Returns
    -------
    frame : `FrameRecord`
        The record.
    """
    return FrameRecord(path=path, camera="ZWO ASI533MM Pro", **fields)


def _target(*frames: FrameRecord) -> Target:
    """Build a target that holds the given frames.

    Parameters
    ----------
    *frames : `FrameRecord`
        The target's frames.

    Returns
    -------
    target : `Target`
        The target.
    """
    return Target(id="M 13", frames=list(frames))


def test_a_complete_page_gives_its_frames() -> None:
    """The page `targets.frames.query` returns is taken as it is."""
    frames = [_frame("/lib/a.fits"), _frame("/lib/b.fits")]
    assert frames_from_choice(Page[FrameRecord](items=frames, total=2)) == frames


def test_a_page_cut_short_is_refused() -> None:
    """A page with a ``next_offset`` does not hold every match."""
    page = Page[FrameRecord].of([_frame("/lib/a.fits"), _frame("/lib/b.fits")], limit=1)
    with pytest.raises(InvalidArgumentError, match="cut short") as caught:
        frames_from_choice(page)
    assert caught.value.details["total"] == 2


def test_a_list_of_records_is_taken_as_it_is() -> None:
    """A list of frame records needs no target."""
    frames = [_frame("/lib/a.fits")]
    assert frames_from_choice(frames) == frames


def test_paths_are_matched_to_the_targets_frames() -> None:
    """Each path gives the target's record for that file, in order."""
    a, b = _frame("/lib/a.fits"), _frame("/lib/b.fits")
    assert frames_from_choice(["/lib/b.fits", "/lib/./a.fits"], _target(a, b)) == [b, a]


def test_a_path_that_is_not_a_frame_of_the_target_is_not_found() -> None:
    """An unknown path is named in the details."""
    with pytest.raises(NotFoundError) as caught:
        frames_from_choice(["/lib/x.fits"], _target(_frame("/lib/a.fits")))
    assert caught.value.details["missing"] == ["/lib/x.fits"]


@pytest.mark.parametrize(
    "frames",
    ["/lib/a.fits", [_frame("/lib/a.fits"), "/lib/b.fits"], [1, 2], 7],
)
def test_other_forms_are_refused(frames: object) -> None:
    """One string, a mix, or another type is not a frame choice."""
    with pytest.raises(InvalidArgumentError):
        frames_from_choice(frames, _target(_frame("/lib/a.fits")))


def test_paths_need_the_target() -> None:
    """Without a target a path cannot be matched to a record."""
    with pytest.raises(InvalidArgumentError, match="target"):
        frames_from_choice(["/lib/a.fits"])


def test_the_frame_list_check_refuses_an_empty_list() -> None:
    """An empty list has nothing to work on."""
    with pytest.raises(InvalidArgumentError, match="empty"):
        check_frame_list([], target_id="M 13")


def test_the_frame_list_check_refuses_two_cameras() -> None:
    """Frames of two cameras are never one set."""
    frames = [_frame("/lib/a.fits"), FrameRecord(path="/lib/b.fits", camera="Nikon DSLR DSC D5300")]
    with pytest.raises(InvalidArgumentError, match="more than one camera"):
        check_frame_list(frames, target_id="M 13")


def test_the_frame_list_check_refuses_two_optics() -> None:
    """Frames at two focal lengths are never one set."""
    frames = [_frame("/lib/a.fits", focalLengthMm=300.0), _frame("/lib/b.fits", focalLengthMm=405.0)]
    with pytest.raises(InvalidArgumentError):
        check_frame_list(frames, target_id="M 13")


def test_the_frame_list_check_refuses_imaging_mixed_with_spectra() -> None:
    """The message counts each kind."""
    frames = [_frame("/lib/a.fits"), _frame("/lib/b.fits", filter=FilterType.SPEC)]
    with pytest.raises(InvalidArgumentError, match="1 imaging, 1 spectral"):
        check_frame_list(frames, target_id="M 13")


def test_the_frame_list_check_accepts_one_set() -> None:
    """Frames of one camera, optic and kind pass."""
    check_frame_list([_frame("/lib/a.fits"), _frame("/lib/b.fits")], target_id="M 13")
