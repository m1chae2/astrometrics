"""Tests for stack members, their weights and the effective counts."""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.drivers.siril_output_parsing import parse_seq_inclusion
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    determine_stack_members,
    effective_exposure_seconds,
    effective_frame_count,
    normalized_weights,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import merge_registration_sequences


def _frames(count: int, exposure: str = "60") -> list[SimpleNamespace]:
    """Make simple frame stand-ins.

    Parameters
    ----------
    count : `int`
        How many frames.
    exposure : `str`, optional
        Their exposure time.

    Returns
    -------
    frames : `list` of `SimpleNamespace`
        Frames with a path, exposure and timestamp.
    """
    return [
        SimpleNamespace(path=f"/frames/{index}.fits", exposure=exposure, timestamp=1.0e9 + index)
        for index in range(count)
    ]


def test_effective_frame_count_of_equal_weights_is_the_frame_count() -> None:
    """Equal weights are worth exactly their number of frames."""
    assert effective_frame_count([1.0] * 12) == pytest.approx(12.0)


def test_effective_frame_count_of_unequal_weights_is_smaller() -> None:
    """Weights 1 and 3 are worth 16 / 10 = 1.6 frames."""
    assert effective_frame_count([1.0, 3.0]) == pytest.approx(1.6)


def test_effective_time_of_equal_weights_is_the_total_exposure() -> None:
    """Ten equal frames of 60 s are worth 600 s."""
    assert effective_exposure_seconds([1.0] * 10, [60.0] * 10) == pytest.approx(600.0)


def test_effective_time_of_optimal_weights_is_the_total_exposure() -> None:
    """Weights proportional to exposure give the total exposure."""
    exposures = [30.0, 60.0, 120.0]
    assert effective_exposure_seconds(exposures, exposures) == pytest.approx(sum(exposures))


def test_effective_time_of_equal_weights_on_mixed_exposures_is_lower() -> None:
    """Equal weights waste light when exposures differ."""
    exposures = [30.0, 120.0]
    equal = effective_exposure_seconds([1.0, 1.0], exposures)
    assert equal is not None and equal < sum(exposures)
    assert equal == pytest.approx(4.0 / (1 / 30 + 1 / 120))


def test_unknown_weights_or_exposures_give_no_effective_numbers() -> None:
    """A missing number is reported as missing."""
    assert effective_frame_count([1.0, None]) is None
    assert effective_frame_count([]) is None
    assert effective_exposure_seconds([1.0, 1.0], [60.0, None]) is None
    assert effective_exposure_seconds([1.0, 1.0], [60.0, 0.0]) is None


def test_equal_weighting_gives_every_member_weight_one() -> None:
    """With no Siril weighting every member counts 1, normalized to mean 1."""
    membership = determine_stack_members(_frames(4), {}, None)
    assert [member.frame.path for member in membership.members] == [
        f"/frames/{index}.fits" for index in range(4)
    ]
    assert normalized_weights(membership) == [1.0] * 4
    assert membership.weights_reason == ""


def test_corrupt_and_registration_dropped_frames_are_not_members() -> None:
    """Registration-dropped and unreadable frames are left out."""
    frames = _frames(5)
    diagnostics = {
        "corrupt_frames_skipped": ["/frames/4.fits"],
        "symlinked_light_paths": [frame.path for frame in frames[:4]],
        "registration_included": [True, False, True, True],
    }
    membership = determine_stack_members(frames, diagnostics, None)
    assert [member.frame.path for member in membership.members] == [
        "/frames/0.fits",
        "/frames/2.fits",
        "/frames/3.fits",
    ]


def test_mismatched_inclusion_list_drops_nobody() -> None:
    """Inclusion flags that do not line up name no dropped frame."""
    frames = _frames(3)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True, False],
    }
    assert len(determine_stack_members(frames, diagnostics, None).members) == 3


def test_siril_weighting_makes_the_weights_unknown_but_keeps_the_members() -> None:
    """Siril's own weights are not reported; membership is still recorded."""
    membership = determine_stack_members(_frames(3), {}, "noise")
    assert len(membership.members) == 3
    assert normalized_weights(membership) == [None, None, None]
    assert "-weight=noise" in membership.weights_reason


def test_exposure_group_weights_become_per_frame_shares_with_mean_one() -> None:
    """A group's weight is shared by its frames; the mean is 1."""
    frames = _frames(5)
    diagnostics = {
        "combined_frame_paths": [frame.path for frame in frames],
        "exposure_group_weights": [
            {"paths": [frame.path for frame in frames[:2]], "weight": 2.0, "frame_count": 2},
            {"paths": [frame.path for frame in frames[2:]], "weight": 12.0, "frame_count": 3},
        ],
    }
    membership = determine_stack_members(frames, diagnostics, None)
    weights = normalized_weights(membership)
    # Raw shares are 2 / 2 = 1 and 12 / 3 = 4; their mean is 2.8.
    assert weights[0] == weights[1] == pytest.approx(1.0 / 2.8)
    assert weights[2] == weights[3] == weights[4] == pytest.approx(4.0 / 2.8)
    assert math.fsum(weights) == pytest.approx(5.0)


def test_frames_of_a_group_left_out_of_the_combine_are_not_members() -> None:
    """Only the frames of the combined groups count."""
    frames = _frames(4)
    diagnostics = {
        "combined_frame_paths": [frame.path for frame in frames[:3]],
        "exposure_group_weights": [
            {"paths": [frame.path for frame in frames[:3]], "weight": 3.0, "frame_count": 3}
        ],
    }
    assert len(determine_stack_members(frames, diagnostics, None).members) == 3


def test_fewer_images_stacked_than_candidates_marks_the_membership_uncertain() -> None:
    """Unnamed frames dropped by Siril's filters make the list uncertain."""
    membership = determine_stack_members(_frames(5), {"images_stacked": 4}, None)
    assert "4 of 5" in membership.membership_reason


def test_parse_seq_inclusion_reads_the_included_flags(tmp_path: Path) -> None:
    """Image lines of a sequence file give one flag per image."""
    sequence = tmp_path / "stack_Registration.seq"
    sequence.write_text("S 'x' 1 3 3 0 1 1\nR0 1.0 1.0 0.9 0.5 1 1 30\nI 1 1\nI 2 0\nI 3 1 4.5\n")
    assert parse_seq_inclusion(str(sequence)) == [True, False, True]
    assert parse_seq_inclusion(str(tmp_path / "missing.seq")) == []


def test_merged_sequence_keeps_the_image_lines_in_group_order(tmp_path: Path) -> None:
    """Merging group sequences keeps both the R and I lines."""
    first = tmp_path / "a.seq"
    second = tmp_path / "b.seq"
    first.write_text("S header\nR0 1 1 1 1 1 1 10\nI 1 1\n")
    second.write_text("R0 2 2 2 2 2 2 20\nI 1 0\n")
    merged = tmp_path / "merged.seq"
    assert merge_registration_sequences([str(first), str(second)], str(merged))
    assert parse_seq_inclusion(str(merged)) == [True, False]
    assert merged.read_text().count("R0 ") == 2
