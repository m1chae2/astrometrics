"""Tests for stack members, their weights and the effective counts."""

import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.drivers.siril_output_parsing import parse_seq_inclusion
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    count_frames_stacked,
    determine_stack_members,
    effective_exposure_seconds,
    effective_frame_count,
    normalized_weights,
    reference_group_frame_paths,
    registration_reference_from_diagnostics,
    wfwhm_weights,
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
        SimpleNamespace(
            path=f"/frames/{index}.fits",
            exposure=exposure,
            timestamp=1.0e9 + index,
            camera="cam",
            iso="800",
            offset="0",
        )
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


def _group_entry(frames: list[SimpleNamespace], exposure: float = 60.0) -> dict:
    """Make an ``exposure_group_weights`` entry with equal in-group weights.

    Returns
    -------
    entry : `dict`
        An entry whose gain was measured and whose frame noise was read.
    """
    return {
        "paths": [frame.path for frame in frames],
        "weight": len(frames) * exposure**2 / 25.0,
        "frame_count": len(frames),
        "exposure_seconds": exposure,
        "frame_noise_counts": 5.0,
        "gain": 1.0,
        "gain_measured": True,
        "stack_weight": None,
        "holds_folded_frames": False,
    }


def test_exposure_group_weights_become_per_frame_shares_with_mean_one() -> None:
    """A group's weight is shared by its frames; the mean is 1."""
    frames = _frames(5)
    diagnostics = {
        "combined_frame_paths": [frame.path for frame in frames],
        "exposure_group_weights": [_group_entry(frames[:2], 60.0), _group_entry(frames[2:], 120.0)],
    }
    membership = determine_stack_members(frames, diagnostics, None)
    weights = normalized_weights(membership)
    # Group weights N t^2 / s^2 are 2 * 3600 / 25 = 288 and
    # 3 * 14400 / 25 = 1728, shared per frame as 144 and 576.
    # The mean is (2 * 144 + 3 * 576) / 5 = 403.2.
    assert weights[0] == weights[1] == pytest.approx(144.0 / 403.2)
    assert weights[2] == weights[3] == weights[4] == pytest.approx(576.0 / 403.2)
    assert math.fsum(weights) == pytest.approx(5.0)


def test_frames_of_a_group_left_out_of_the_combine_are_not_members() -> None:
    """Only the frames of the combined groups count."""
    frames = _frames(4)
    diagnostics = {
        "combined_frame_paths": [frame.path for frame in frames[:3]],
        "exposure_group_weights": [_group_entry(frames[:3])],
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


# The weighted star widths (pixels) of the 61 frames of the M 57 stack of the
# night of 2026-10-03, copied from the `R0` lines of that stack's Siril
# registration sequence file.
_M57_WEIGHTED_FWHM_PX = [
    3.17027, 2.58072, 4.1292, 2.35677, 2.96505, 4.16645, 2.96505, 3.39337, 4.84284, 6.93452, 4.84626,
    5.26169, 4.9182, 4.05684, 4.57619, 2.53662, 4.87738, 5.57078, 2.5715, 4.80236, 3.25744, 4.15744,
    3.32299, 3.46115, 3.55113, 4.2603, 3.65225, 3.36534, 4.44963, 5.61601, 4.45397, 6.90815, 2.57225,
    3.04412, 2.18096, 3.13592, 2.76012, 2.37718, 3.79153, 2.09047, 2.57651, 2.93731, 5.28048, 3.32539,
    2.57537, 3.00473, 3.08008, 3.78447, 3.74241, 3.73712, 3.70004, 3.62633, 3.79721, 4.94621, 3.68446,
    3.48391, 3.92725, 4.6504, 3.07033, 3.91401, 4.14655,
]  # fmt: skip


def test_wfwhm_weights_follow_siril_formula_by_hand() -> None:
    """Widths 2, 4 and 8 px give the weights worked out below."""
    weights = wfwhm_weights([2.0, 4.0, 8.0])
    middle = (1 / 16 - 1 / 64) / (1 / 4 - 1 / 64)
    mean = (1.0 + middle + 0.0) / 3
    assert weights == pytest.approx([1.0 / mean, middle / mean, 0.0])
    assert sum(weights) / 3 == pytest.approx(1.0)


def test_wfwhm_weights_give_a_frame_without_a_width_weight_zero() -> None:
    """A frame with no positive width counts for nothing."""
    weights = wfwhm_weights([2.0, 0.0, 4.0])
    assert weights[1] == pytest.approx(0.0)
    assert sum(weights) / 3 == pytest.approx(1.0)


def test_wfwhm_weights_of_equal_widths_are_equal() -> None:
    """The formula divides by zero for equal widths; the weights are equal."""
    assert wfwhm_weights([3.0, 3.0, 3.0]) == pytest.approx([1.0, 1.0, 1.0])


def test_wfwhm_weights_of_the_real_m57_sequence() -> None:
    """The sharpest frame counts most and the blurriest 0."""
    weights = wfwhm_weights(_M57_WEIGHTED_FWHM_PX)
    assert len(weights) == 61
    assert sum(weights) / 61 == pytest.approx(1.0)
    assert weights.index(min(weights)) == _M57_WEIGHTED_FWHM_PX.index(max(_M57_WEIGHTED_FWHM_PX))
    assert min(weights) == pytest.approx(0.0)
    assert weights.index(max(weights)) == _M57_WEIGHTED_FWHM_PX.index(min(_M57_WEIGHTED_FWHM_PX))
    assert effective_frame_count(weights) == pytest.approx(40.78, abs=0.01)


def test_siril_star_width_weighting_is_rebuilt_from_the_sequence_widths() -> None:
    """With -weight=wfwhm each weight comes from the sequence widths."""
    frames = _frames(3)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_frames": [{"weighted_fwhm_px": width} for width in (2.0, 4.0, 8.0)],
    }
    membership = determine_stack_members(frames, diagnostics, "wfwhm")
    assert membership.weights_reason == ""
    assert normalized_weights(membership) == pytest.approx(wfwhm_weights([2.0, 4.0, 8.0]))


def test_a_frame_dropped_at_registration_does_not_enter_the_width_weights() -> None:
    """Siril scales the weights over the stacked frames only."""
    frames = _frames(3)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True, True, False],
        "registration_frames": [{"weighted_fwhm_px": width} for width in (2.0, 4.0, 8.0)],
    }
    membership = determine_stack_members(frames, diagnostics, "wfwhm")
    assert normalized_weights(membership) == pytest.approx(wfwhm_weights([2.0, 4.0]))


def test_star_width_weights_are_unknown_when_the_sequence_lines_do_not_match() -> None:
    """Without matching sequence lines the weights stay unknown."""
    frames = _frames(3)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_frames": [{"weighted_fwhm_px": 2.0}],
    }
    membership = determine_stack_members(frames, diagnostics, "wfwhm")
    assert normalized_weights(membership) == [None, None, None]
    assert "-weight=wfwhm" in membership.weights_reason


def test_noise_weights_say_what_is_missing() -> None:
    """The reason for -weight=noise names the scale Siril does not save."""
    membership = determine_stack_members(_frames(2), {}, "noise")
    assert "pscale" in membership.weights_reason


def _group_entry_with_a_dropped_frame() -> dict:
    """Build a group entry like the heavy M 27 group: 5 frames, one dropped.

    Siril keeps a line for the dropped frame, with a transform of all zeros,
    which reads as no shift just as the reference frame's identity does.

    Returns
    -------
    entry : `dict`
        An ``exposure_group_weights`` entry with the dropped frame second.
    """
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    moved = [1.0, 0.0, 4.5, 0.0, 1.0, -2.0, 0.0, 0.0, 1.0]
    lines = [
        {"dx": 4.5, "dy": -2.0, "star_count": 90, "homography": moved},
        {"dx": 0.0, "dy": 0.0, "star_count": 0, "homography": [0.0] * 9},
        {"dx": 0.0, "dy": 0.0, "star_count": 120, "homography": identity},
        {"dx": 4.5, "dy": -2.0, "star_count": 95, "homography": moved},
        {"dx": 4.5, "dy": -2.0, "star_count": 99, "homography": moved},
    ]
    return {
        "weight": 9.0,
        "paths": [f"/raw/{index}.fits" for index in range(5)],
        "frame_paths": [f"/raw/{index}.fits" for index in range(5)],
        "registration_frames": lines,
        "registration_included": [True, False, True, True, True],
    }


def test_a_dropped_frames_empty_line_is_not_taken_for_a_second_reference() -> None:
    """A dropped frame must not hide the heavy group's identity frame."""
    light = {
        "weight": 1.0,
        "paths": ["/raw/x.fits"],
        "frame_paths": ["/raw/x.fits"],
        "registration_frames": [{"dx": 0.0, "dy": 0.0, "star_count": 5}],
        "registration_included": [True],
    }
    diagnostics = {"exposure_group_weights": [light, _group_entry_with_a_dropped_frame()]}

    assert registration_reference_from_diagnostics(diagnostics) == ("/raw/2.fits", 120)


def test_a_dropped_frame_with_a_plain_zero_shift_line_is_skipped_by_its_flag() -> None:
    """Even a dropped frame's line that looks like an identity is ignored."""
    entry = _group_entry_with_a_dropped_frame()
    entry["registration_frames"][1].pop("homography")
    diagnostics = {"exposure_group_weights": [entry]}

    assert registration_reference_from_diagnostics(diagnostics) == ("/raw/2.fits", 120)


def test_lines_only_for_the_kept_frames_are_paired_with_the_kept_paths() -> None:
    """A dropped frame with no line: kept frames take the lines in order."""
    entry = _group_entry_with_a_dropped_frame()
    del entry["registration_frames"][1]
    diagnostics = {"exposure_group_weights": [entry]}

    assert registration_reference_from_diagnostics(diagnostics) == ("/raw/2.fits", 120)


def test_lines_that_cannot_be_paired_with_the_paths_name_no_reference() -> None:
    """A line count that fits neither the frames nor the kept ones: refused."""
    entry = _group_entry_with_a_dropped_frame()
    del entry["registration_frames"][0:2]
    diagnostics = {"exposure_group_weights": [entry]}

    assert registration_reference_from_diagnostics(diagnostics) is None


def test_two_real_identity_lines_still_name_no_reference() -> None:
    """Two kept frames with no shift cannot name one reference."""
    entry = _group_entry_with_a_dropped_frame()
    entry["registration_frames"][0].update(dx=0.0, dy=0.0, homography=[1.0, 0, 0, 0, 1.0, 0, 0, 0, 1.0])
    diagnostics = {"exposure_group_weights": [entry]}

    assert registration_reference_from_diagnostics(diagnostics) is None


def test_the_reference_group_frames_leave_out_the_dropped_frame() -> None:
    """The fallback frames are the heaviest group's kept frames."""
    light = {"weight": 1.0, "paths": ["/raw/x.fits"], "frame_paths": ["/raw/x.fits"]}
    diagnostics = {"exposure_group_weights": [light, _group_entry_with_a_dropped_frame()]}

    assert reference_group_frame_paths(diagnostics) == [f"/raw/{index}.fits" for index in (0, 2, 3, 4)]
    assert reference_group_frame_paths({}) == []


def _three_group_diagnostics(frames: list[SimpleNamespace], sizes: list[int], left_out: set[int]) -> dict:
    """Build the diagnostics of an exposure-group stack.

    Parameters
    ----------
    frames : `list` of `SimpleNamespace`
        All the frames, in group order.
    sizes : `list` of `int`
        The number of frames in each group.
    left_out : `set` of `int`
        Positions in `frames` that Siril marked ``I n 0`` while registering.

    Returns
    -------
    diagnostics : `dict`
        The joined top-level lists (the inclusion flags are the first group's
        only, as in the real merge) and one ``exposure_group_weights`` entry
        per group.
    """
    entries = []
    start = 0
    for size in sizes:
        group_paths = [frame.path for frame in frames[start : start + size]]
        flags = [position not in left_out for position in range(start, start + size)]
        entries.append({
            "weight": 1.0,
            "paths": group_paths,
            "frame_paths": group_paths,
            "registration_included": flags,
            "frame_count": sum(flags),
            "exposure_seconds": 30.0,
            "frame_noise_counts": 1.0,
            "stack_weight": None,
        })
        start += size
    return {
        "combined_frame_paths": [frame.path for frame in frames],
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": entries[0]["registration_included"],
        "images_stacked": len(frames) - len(left_out),
        "exposure_group_weights": entries,
    }


def test_group_stack_count_equals_member_count_when_siril_left_a_frame_out() -> None:
    """M 27: groups of 26, 33 and 18, one left out, counts 76 of 77."""
    frames = _frames(77)
    diagnostics = _three_group_diagnostics(frames, [26, 33, 18], left_out={70})
    summary = stage._base_stack_quality_summary(
        SimpleNamespace(id="M 27"), False, 77, frames, [], diagnostics
    )
    membership = determine_stack_members(frames, diagnostics, None)
    assert summary.stacking_metrics.frames_stacked == 76
    assert len(membership.members) == 76
    assert count_frames_stacked(frames, diagnostics) == len(membership.members)
    assert "/frames/70.fits" not in [member.frame.path for member in membership.members]
    assert membership.membership_reason == ""


def test_group_stack_count_leaves_out_a_group_that_was_not_combined() -> None:
    """A group missing from the combine contributes 0 frames."""
    frames = _frames(77)
    diagnostics = _three_group_diagnostics(frames, [26, 33, 18], left_out={30})
    diagnostics["combined_frame_paths"] = [frame.path for frame in frames[:59]]
    diagnostics["exposure_group_weights"] = diagnostics["exposure_group_weights"][:2]
    diagnostics["images_stacked"] = 58
    assert count_frames_stacked(frames, diagnostics) == 58
    assert len(determine_stack_members(frames, diagnostics, None).members) == 58


def test_single_group_count_leaves_out_the_frame_siril_marked_not_included() -> None:
    """A single-group stack with one ``I n 0`` frame counts one fewer."""
    frames = _frames(12)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [position != 5 for position in range(12)],
        "images_stacked": 11,
    }
    summary = stage._base_stack_quality_summary(
        SimpleNamespace(id="M 27"), False, 12, frames, [], diagnostics
    )
    assert summary.stacking_metrics.frames_stacked == 11
    assert len(determine_stack_members(frames, diagnostics, None).members) == 11


def test_count_does_not_guess_when_the_inclusion_flags_do_not_line_up() -> None:
    """Flags that do not match the paths name no frame, so none is dropped."""
    frames = _frames(6)
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True, False],
    }
    assert count_frames_stacked(frames, diagnostics) == 6


def test_count_uses_siril_number_when_its_own_filters_left_more_out() -> None:
    """Siril's stacked count wins when it is below the named candidates."""
    frames = _frames(6)
    assert count_frames_stacked(frames, {"images_stacked": 4}) == 4
