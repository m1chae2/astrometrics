"""Purpose: Tests of the rule that gives each frame of a target its status.

Description: `build_frame_statuses` says, for each frame of a target, whether
it is included, flagged, not stacked yet or set aside, and lists the frames
in `_excluded` folders (which the target's own frame list does not hold). A
stacked frame is flagged when its stored star elongation is at least the
warning level and the quarantine kept it: the frame lies in the band between
`STRETCHED_STAR_WARNING_ELONGATION` and its session's set-aside threshold
(`set_aside_elongation_limit`), both read from their owner.
The capture time a capture program writes into a file name is read by
`capture_time_from_file_name`, the one place that rule lives. The time a
frame must be older than, the newest stack file's, comes from
`latest_stack_time`.
"""

import os
from pathlib import Path

import pytest

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.excluded_frames import SetAsideFrameRecord
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_scanning import capture_time_from_file_name
from astrometricslib.pipelines.stacking.frame_status import (
    build_frame_statuses,
    latest_stack_time,
    set_aside_limits_by_path,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN,
    ELONGATION_RISE_ABOVE_MEDIAN,
    MINIMUM_FRAMES_TO_JUDGE,
    STRETCHED_STAR_WARNING_ELONGATION,
    TRAILED_ELONGATION_LIMIT,
    set_aside_elongation_limit,
)


def _mono(path: str) -> bool:
    """Say every frame came from a monochrome camera (stands in for a header).

    Returns
    -------
    is_colour : `bool`
        Always `False`.
    """
    return False


def _session(elongations: list[float], start: float = 100.0) -> list[FrameRecord]:
    """Make one session of light frames a minute apart.

    Returns
    -------
    frames : `list` [`FrameRecord`]
        One frame per elongation, at ``/f<i>.fits``.
    """
    return [
        FrameRecord(path=f"/f{index}.fits", timestamp=start + 60.0 * index, camera="ASI533MM", exposure_s=120)
        for index in range(len(elongations))
    ]


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
        exposure_s=120.0,
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


def test_a_stacked_frame_in_the_warning_band_is_flagged_with_its_numbers() -> None:
    """A stacked frame between warning and set-aside threshold is flagged."""
    warning = resolve(STRETCHED_STAR_WARNING_ELONGATION).value
    values = [1.10] * 9 + [warning + 0.05]
    frames = _session(values)
    elongations = {frame.path: value for frame, value in zip(frames, values, strict=True)}
    limit = set_aside_elongation_limit(values, colour_camera=False)
    assert warning + 0.05 <= limit

    report = build_frame_statuses(frames, [], 10_000.0, elongations, colour_camera=_mono)

    states = [status.state for status in report.frames]
    assert states == ["included"] * 9 + ["flagged"]
    flagged = report.frames[-1]
    assert flagged.elongation == pytest.approx(warning + 0.05)
    assert flagged.reason == (
        f"Stacked, but its stars are stretched: elongation {warning + 0.05:.2f}, "
        f"warning level {warning:.2f}, set-aside threshold {limit:.2f}."
    )


def test_a_frame_below_the_warning_level_is_included() -> None:
    """A stacked frame just under the warning level is not flagged."""
    warning = resolve(STRETCHED_STAR_WARNING_ELONGATION).value
    frames = _session([1.1] * 10)
    elongations = {frame.path: 1.1 for frame in frames}
    elongations[frames[0].path] = warning - 0.01

    report = build_frame_statuses(frames, [], 10_000.0, elongations, colour_camera=_mono)

    assert {status.state for status in report.frames} == {"included"}


def test_a_frame_without_a_stored_elongation_or_after_the_stack_is_not_flagged() -> None:
    """Only a stacked frame with a stored elongation can be flagged."""
    frames = _session([1.1] * 10)
    elongations = {frame.path: 1.25 for frame in frames[1:]}

    report = build_frame_statuses(frames, [], frames[5].timestamp, elongations, colour_camera=_mono)

    states = [status.state for status in report.frames]
    assert states[0] == "included"
    assert states[1:6] == ["flagged"] * 5
    assert states[6:] == ["not_stacked"] * 4


def test_a_stacked_frame_above_the_threshold_says_it_was_kept_anyway() -> None:
    """A stacked frame above its threshold is flagged; the reason says so."""
    values = [1.10] * 9 + [1.60]
    frames = _session(values)
    elongations = {frame.path: value for frame, value in zip(frames, values, strict=True)}

    report = build_frame_statuses(frames, [], 10_000.0, elongations, colour_camera=_mono)

    assert report.frames[-1].state == "flagged"
    assert "above the set-aside threshold" in report.frames[-1].reason


def test_a_session_too_small_to_judge_has_no_threshold() -> None:
    """Below the minimum batch a frame is flagged with no threshold."""
    count = resolve(MINIMUM_FRAMES_TO_JUDGE).value - 1
    frames = _session([1.25] * count)
    elongations = {frame.path: 1.25 for frame in frames}

    limits = set_aside_limits_by_path(frames, elongations, colour_camera=_mono)
    report = build_frame_statuses(frames, [], 10_000.0, elongations, colour_camera=_mono)

    assert set(limits.values()) == {None}
    assert all("too small for the quarantine" in status.reason for status in report.frames)


def test_the_threshold_is_the_quarantines_own_for_mono_and_colour_sessions() -> None:
    """The threshold is the owner's: the floor, or the median plus the rise."""
    round_session = [1.10] * 10
    stretched_session = [1.30] * 10
    floor = resolve(TRAILED_ELONGATION_LIMIT).value
    mono_rise = resolve(ELONGATION_RISE_ABOVE_MEDIAN).value
    colour_rise = resolve(COLOUR_CAMERA_ELONGATION_RISE_ABOVE_MEDIAN).value

    frames = _session(round_session)
    limits = set_aside_limits_by_path(frames, {frame.path: 1.10 for frame in frames}, colour_camera=_mono)
    assert list(limits.values()) == [pytest.approx(floor)] * 10

    frames = _session(stretched_session)
    elongations = {frame.path: 1.30 for frame in frames}
    mono = set_aside_limits_by_path(frames, elongations, colour_camera=_mono)
    colour = set_aside_limits_by_path(frames, elongations, colour_camera=lambda path: True)
    assert list(mono.values()) == [pytest.approx(1.30 + mono_rise)] * 10
    assert list(colour.values()) == [pytest.approx(1.30 + colour_rise)] * 10


def test_spectroscopy_frames_and_calibration_frames_are_never_flagged() -> None:
    """The quarantine judges light frames only, so only they can be flagged."""
    frames = [*_session([1.25] * 9), FrameRecord(path="/dark.fits", role="DARK", timestamp=100.0)]
    elongations = {frame.path: 1.25 for frame in frames}

    report = build_frame_statuses(frames, [], 10_000.0, elongations, colour_camera=_mono)

    assert report.frames[-1].state == "included"
    assert report.frames[0].state == "flagged"


def test_set_aside_frames_are_listed_with_their_reason_kind_and_date() -> None:
    """A frame in `_excluded` keeps the stacker's words and its date."""
    report = build_frame_statuses([], [_set_aside("M1_2026-10-03T08-15-30_120s.fits")], stacked_at=None)

    [status] = report.frames
    assert status.state == "set_aside"
    assert status.kind == "clouded"
    assert status.reason == "203 stars against a typical 3348"
    assert status.date == "2026-10-03 08:15:30"
    assert (status.camera, status.filter) == ("ASI533MM", "Luminance")
    assert status.exposure_s == pytest.approx(120.0)
    assert status.restorable is True


def test_the_capture_time_is_read_from_a_file_name() -> None:
    """A capture program's date and time in the name, else nothing."""
    assert capture_time_from_file_name("M1_2026-10-03T08-15-30_120s.fits") == "2026-10-03 08:15:30"
    assert capture_time_from_file_name("M1_Light_001.fits") == ""


def test_the_newest_stack_file_gives_the_stack_time(tmp_path: Path) -> None:
    """The newest of the imaging, spectral and configuration stacks counts."""
    from astrometricslib.models.target import Target

    older, newer = tmp_path / "a_Stacked.fits", tmp_path / "b_Stacked.fits"
    for path, stamp in ((older, 1000.0), (newer, 2000.0)):
        path.write_bytes(b"x")
        os.utime(path, (stamp, stamp))
    target = Target(id="M 1")
    target.stacking.stacked_image = str(older)
    target.spectral_stacking.stacked_image = str(newer)

    assert latest_stack_time(target, None) == pytest.approx(2000.0)
    assert latest_stack_time(Target(id="M 2"), None) is None


def test_an_old_record_finds_its_stack_in_the_stacks_folder(tmp_path: Path) -> None:
    """A record naming the old lights folder finds the moved file by name."""
    from astrometricslib.models.target import Target

    moved = tmp_path / "stacks" / "lights" / "M 1" / "M_1_Stacked.fits"
    moved.parent.mkdir(parents=True)
    moved.write_bytes(b"x")
    os.utime(moved, (1500.0, 1500.0))
    target = Target(id="M 1")
    target.stacking.stacked_image = str(tmp_path / "lights" / "M 1" / "M_1_Stacked.fits")

    assert latest_stack_time(target, str(tmp_path / "stacks")) == pytest.approx(1500.0)
    assert latest_stack_time(target, None) is None
