"""Purpose: Tests of the rule that gives each frame of a target its status.

Description: `build_frame_statuses` says, for each frame of a target, whether
it is included, flagged, not stacked yet or set aside, and lists the frames
in `_excluded` folders (which the target's own frame list does not hold).
The capture time a capture program writes into a file name is read by
`capture_time_from_file_name`, the one place that rule lives.
"""

import pytest

from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_scanning import capture_time_from_file_name
from astrometricslib.pipelines.stacking.frame_status import build_frame_statuses


def _set_aside(file_name: str, kind: str | None = "clouded") -> SetAsideFrameRecord:
    """Make a set-aside record as the library lists it.

    Returns
    -------
    record : `SetAsideFrameRecord`
        A record of one frame in an `_excluded` folder.
    """
    return SetAsideFrameRecord(
        file=file_name,
        path=f"/frames/lights/M 1/_excluded/{file_name}",
        kind=kind,
        reason="203 stars against a typical 3348",
        reason_source="stored_row",
        exposure_seconds=120.0,
        original_path=f"/frames/lights/M 1/{file_name}",
        restorable=True,
        camera="ASI533MM",
        filter="Luminance",
        date="2026-10-03 08:15:30",
    )


def test_frames_before_the_stack_are_included_and_later_ones_are_not_stacked() -> None:
    """A frame taken after the latest stack file is "not stacked yet"."""
    frames = [FrameRecord(path="/a.fits", timestamp=100.0), FrameRecord(path="/b.fits", timestamp=300.0)]

    report = build_frame_statuses(frames, [], stacked_at=200.0)

    assert [(status.name, status.state) for status in report.frames] == [
        ("a.fits", "included"),
        ("b.fits", "not_stacked"),
    ]
    assert report.stacked_at == pytest.approx(200.0)


def test_with_no_stack_every_frame_is_not_stacked_yet() -> None:
    """A target that has no stack has nothing included."""
    report = build_frame_statuses([FrameRecord(path="/a.fits", timestamp=1.0)], [], stacked_at=None)

    assert report.frames[0].state == "not_stacked"


def test_a_frame_with_no_time_counts_as_taken_before_the_stack() -> None:
    """Without a recorded time the frame is not called new."""
    report = build_frame_statuses([FrameRecord(path="/a.fits")], [], stacked_at=200.0)

    assert report.frames[0].state == "included"


def test_a_flagged_frame_in_the_stack_keeps_its_elongation() -> None:
    """A standout frame in the stack is flagged; a later one is not stacked."""
    frames = [
        FrameRecord(path="/a.fits", timestamp=100.0),
        FrameRecord(path="/b.fits", timestamp=150.0),
        FrameRecord(path="/c.fits", timestamp=300.0),
    ]

    report = build_frame_statuses(
        frames, [], stacked_at=200.0, flagged={"/b.fits": 1.43, "/c.fits": 1.9}, typical_elongation=1.08
    )

    assert [(status.state, status.elongation) for status in report.frames] == [
        ("included", None),
        ("flagged", pytest.approx(1.43)),
        ("not_stacked", None),
    ]
    assert report.typical_elongation == pytest.approx(1.08)


def test_set_aside_frames_are_listed_with_their_reason_kind_and_date() -> None:
    """A frame in `_excluded` keeps the stacker's words and its date."""
    report = build_frame_statuses([], [_set_aside("M1_2026-10-03T08-15-30_120s.fits")], stacked_at=None)

    [status] = report.frames
    assert status.state == "set_aside"
    assert status.kind == "clouded"
    assert status.reason == "203 stars against a typical 3348"
    assert status.date == "2026-10-03 08:15:30"
    assert (status.camera, status.filter) == ("ASI533MM", "Luminance")
    assert status.exposure == "120"
    assert status.restorable is True


def test_the_capture_time_is_read_from_a_file_name() -> None:
    """A capture program's date and time in the name, else nothing."""
    assert capture_time_from_file_name("M1_2026-10-03T08-15-30_120s.fits") == "2026-10-03 08:15:30"
    assert capture_time_from_file_name("M1_Light_001.fits") == ""
