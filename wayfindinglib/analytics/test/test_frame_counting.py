"""Purpose: Unit tests for the frame counting.

Description: Checks each matching rule one at a time (camera, telescope,
filter spelling, gain, offset, binning, length within 0.5 s, frame type), that
set-aside and too-early frames are left out, that flagged frames count with
the flag, that frames not yet judged do not complete a row, that a reopen
baseline is taken off, and that carry-over keeps only the missing frames. Uses
in-memory frames and rows, never real data.
"""

from datetime import timedelta

import pytest

from astrometricslib import FilterType
from wayfindinglib.analytics.frame_counting import (
    EquipmentNames,
    TargetFrameReader,
    TargetFrames,
    count_request_frames,
    frame_matches_request,
    missing_requests,
    normalize_filter,
    package_is_complete,
)
from wayfindinglib.analytics.test.frame_counting_helpers import (
    EQUIPMENT,
    NIGHT_START,
    FakeLibrary,
    judgment_row,
    make_frame,
)
from wayfindinglib.models.planning.observation_package import ExposureRequest, FrameType

START = NIGHT_START - timedelta(hours=2)


def _request(**changes: object) -> ExposureRequest:
    """Build a light row of 60 s frames, ten of them.

    Parameters
    ----------
    **changes : `object`
        Fields to change.

    Returns
    -------
    request : `ExposureRequest`
        The row.
    """
    fields = {"frame_type": FrameType.LIGHT, "filter": FilterType.L, "exposure_sec": 60.0, "count": 10}
    fields.update(changes)
    return ExposureRequest(**fields)


def _judged(frames: list, target_id: str = "M 81") -> TargetFrames:
    """Build target frames in which every frame has a stored judgment.

    Parameters
    ----------
    frames : `list` [`FrameRecord`]
        The frames.
    target_id : `str`, optional
        The target.

    Returns
    -------
    target_frames : `TargetFrames`
        The frames with a passing judgment for each.
    """
    from wayfindinglib.analytics.frame_counting import judgments_by_path

    rows = [judgment_row(frame, target_id, row_id=index) for index, frame in enumerate(frames)]
    return TargetFrames(frames=frames, judgments=judgments_by_path(target_id, frames, rows))


def test_a_matching_frame_counts() -> None:
    """A light frame with the right equipment and settings counts."""
    frames = [make_frame(index) for index in range(4)]
    count = count_request_frames(_request(), _judged(frames), EQUIPMENT, START)
    assert (count.captured, count.judged, count.not_judged, count.flagged) == (4, 4, 0, 0)
    assert not count.met
    assert count.missing == 6


@pytest.mark.parametrize(
    ("frame_changes", "reason"),
    [
        ({"camera": "Other Camera"}, "camera"),
        ({"telescope": "Other Scope"}, "telescope"),
        ({"filter_value": FilterType.R}, "filter"),
        ({"exposure": 61.0}, "length beyond 0.5 s"),
        ({"iso": "200"}, "gain"),
        ({"offset": "20"}, "offset"),
        ({"binning": 2}, "binning"),
        ({"role": "DARK"}, "frame type"),
    ],
)
def test_a_frame_that_differs_in_one_field_does_not_count(frame_changes: dict, reason: str) -> None:
    """Each field of the match rule excludes a frame that differs in it."""
    request = _request(gain_setting=120, offset=10, binning=1)
    frame = make_frame(0, **frame_changes)
    assert not frame_matches_request(frame, request, EQUIPMENT), reason


def test_a_length_within_half_a_second_counts() -> None:
    """The length may differ by 0.5 s, the width the calibration match uses."""
    request = _request()
    assert frame_matches_request(make_frame(0, exposure=60.5), request, EQUIPMENT)
    assert not frame_matches_request(make_frame(0, exposure=60.6), request, EQUIPMENT)


def test_an_unset_gain_offset_and_binning_match_any() -> None:
    """A row that leaves gain, offset and binning unset matches any value."""
    frame = make_frame(0, iso="999", offset="77", binning=4)
    assert frame_matches_request(frame, _request(), EQUIPMENT)


def test_a_set_gain_offset_or_binning_needs_the_frame_to_say() -> None:
    """A row that sets binning needs a frame that records it."""
    assert not frame_matches_request(make_frame(0, binning=None), _request(binning=1), EQUIPMENT)


@pytest.mark.parametrize(
    "spelling", ["L", "Lum", "Luminance", "LUMINANCE", FilterType.L, FilterType.LUMINANCE]
)
def test_luminance_is_one_filter_however_it_is_spelled(spelling: object) -> None:
    """The filter names compare equal after normalizing."""
    assert normalize_filter(spelling) == normalize_filter(FilterType.L)


def test_a_filter_name_the_library_does_not_know_still_compares() -> None:
    """An unknown filter name is compared as folded text."""
    assert normalize_filter("My Filter") == normalize_filter("my filter")
    assert normalize_filter("My Filter") != normalize_filter(FilterType.L)


def test_the_session_equipment_matches_by_id_or_by_name() -> None:
    """Camera and telescope match by id or by name."""
    by_id = EquipmentNames.from_names(["cam1"], ["scope1"])
    assert frame_matches_request(make_frame(0, camera="CAM1", telescope="scope1"), _request(), by_id)
    assert not frame_matches_request(make_frame(0), _request(), by_id)
    assert frame_matches_request(make_frame(0), _request(), EQUIPMENT)


def test_a_frame_in_an_excluded_folder_does_not_count() -> None:
    """A set-aside frame (in `_excluded`) is left out."""
    frames = [make_frame(0), make_frame(1, folder="_excluded")]
    count = count_request_frames(_request(), _judged(frames), EQUIPMENT, START)
    assert count.captured == 1


def test_frames_before_the_row_time_do_not_count() -> None:
    """Only frames taken at or after the row's counting time count."""
    frames = [make_frame(index) for index in range(6)]
    request = _request(counted_from=NIGHT_START + timedelta(minutes=3))
    count = count_request_frames(request, _judged(frames), EQUIPMENT, START)
    assert count.captured == 3


def test_the_caller_time_is_used_when_the_row_has_none() -> None:
    """With no counting time on the row, the time the caller gives is used."""
    frames = [make_frame(index) for index in range(6)]
    count = count_request_frames(_request(), _judged(frames), EQUIPMENT, NIGHT_START + timedelta(minutes=4))
    assert count.captured == 2


def test_a_flagged_frame_counts_and_carries_the_flag() -> None:
    """A frame whose self-check failed still counts, as flagged."""
    from wayfindinglib.analytics.frame_counting import judgments_by_path

    frames = [make_frame(0), make_frame(1)]
    rows = [judgment_row(frames[0], failed=True, row_id=1), judgment_row(frames[1], row_id=2)]
    target_frames = TargetFrames(frames=frames, judgments=judgments_by_path("M 81", frames, rows))
    count = count_request_frames(_request(count=2), target_frames, EQUIPMENT, START)
    assert (count.judged, count.flagged) == (2, 1)
    assert count.met
    assert target_frames.judgments[frames[0].path].flags == ("frame.quarantine_roundness",)


def test_frames_not_yet_judged_do_not_complete_a_row() -> None:
    """Captured frames with no judgment are counted but do not meet the row."""
    frames = [make_frame(index) for index in range(3)]
    count = count_request_frames(_request(count=3), TargetFrames(frames=frames), EQUIPMENT, START)
    assert (count.captured, count.judged, count.not_judged) == (3, 0, 3)
    assert not count.met


def test_a_calibration_row_asks_for_no_frames() -> None:
    """Only a light row is counted; a dark row has nothing requested."""
    count = count_request_frames(_request(frame_type=FrameType.DARK), TargetFrames(), EQUIPMENT, START)
    assert count.requested == 0
    assert not package_is_complete([count])


def test_a_package_is_complete_only_when_every_light_row_is_met() -> None:
    """Complete needs at least one light row and all of them met."""
    frames = [make_frame(index) for index in range(3)]
    target_frames = _judged(frames)
    met = count_request_frames(_request(count=3), target_frames, EQUIPMENT, START)
    unmet = count_request_frames(_request(count=5), target_frames, EQUIPMENT, START)
    assert package_is_complete([met])
    assert not package_is_complete([met, unmet])
    assert not package_is_complete([])


def test_the_reopen_baseline_is_taken_off_the_count() -> None:
    """The oldest frames up to the baseline do not count again."""
    frames = [make_frame(index) for index in range(5)]
    count = count_request_frames(_request(baseline_captured=3, count=2), _judged(frames), EQUIPMENT, START)
    assert (count.captured, count.judged) == (2, 2)
    assert count.met


def test_carry_over_drops_met_rows_and_keeps_the_missing_count() -> None:
    """A later session asks only for the frames still missing."""
    frames = [make_frame(index) for index in range(4)] + [
        make_frame(10 + index, filter_value=FilterType.R) for index in range(5)
    ]
    rows = [
        _request(count=3),
        _request(count=10),
        _request(filter=FilterType.R, count=5),
        _request(frame_type=FrameType.DARK, count=20),
    ]
    kept = missing_requests(rows, _judged(frames), EQUIPMENT, START)
    assert [(row.frame_type, row.filter, row.count) for row in kept] == [
        (FrameType.LIGHT, FilterType.L, 6),
        (FrameType.DARK, FilterType.L, 20),
    ]


def test_the_reader_joins_frames_to_their_judgments() -> None:
    """The reader joins the target frames to the quality rows."""
    frames = [make_frame(0), make_frame(1)]
    library = FakeLibrary({"M 81": frames}, [judgment_row(frames[0], failed=True)])
    result = TargetFrameReader(library).read("M 81")
    assert len(result.frames) == 2
    assert result.judgments[frames[0].path].flags
    assert frames[1].path not in result.judgments


def test_the_reader_says_when_it_could_not_read() -> None:
    """A target that cannot be read gives a note, not a silent zero."""
    library = FakeLibrary({"M 81": [make_frame(0)]})
    library.read_failures.add("M 81")
    unreadable = TargetFrameReader(library).read("M 81")
    assert unreadable.frames == [] and "could not be read" in unreadable.note
    missing = TargetFrameReader(library).read("NGC 1")
    assert missing.frames == [] and "No target" in missing.note
