"""Tests for the weights and reference frame of an exposure-group stack.

A stack combined from exposure groups gives each frame the weight
``W_g * w_i / sum(w)`` (the group's combine weight times the frame's share of
its group). These tests cover the public rule (`group_frame_weights`), the
fold rule (`fold_exposure_lengths`), how `determine_stack_members` applies
them, the reference frame of a group stack, the combine's result object, the
empty-aperture rule at a group's uncovered border, and a whole run of the
runner's group combination on synthetic group stacks.
"""

import math
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

import astrometricslib
from astrometricslib.pipelines.stacking import stack_runner
from astrometricslib.pipelines.stacking.post_processing.empty_apertures import (
    EDGE_MARGIN_PX,
    UNCOVERED_CLEARANCE_PX,
    place_empty_apertures,
)
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    determine_stack_members,
    group_frame_weights,
    normalized_weights,
    registration_reference_from_diagnostics,
    wfwhm_weights,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    MINIMUM_FRAMES_PER_EXPOSURE_GROUP,
    ExposureGroup,
    combine_exposure_group_images,
    exposure_group_holds_folded_frames,
    fold_exposure_lengths,
    split_frames_by_exposure,
)
from astrometricslib.pipelines.stacking.test.test_exposure_group_linearity import (
    TRUE_GAIN,
    make_group_a,
    make_linear_group_b,
    write_fits,
)


def _frames(prefix: str, count: int, exposure: str) -> list[SimpleNamespace]:
    """Make frame stand-ins that share an exposure length.

    Returns
    -------
    frames : `list` of `SimpleNamespace`
        Frames with a path, an exposure and a timestamp.
    """
    return [
        SimpleNamespace(path=f"/frames/{prefix}{index}.fits", exposure=exposure, timestamp=1.0e9 + index)
        for index in range(count)
    ]


def _entry(frames: list[SimpleNamespace], exposure: float, **overrides: object) -> dict:
    """Build one ``exposure_group_weights`` entry for a group of frames.

    The group has a frame noise of 10 counts, a measured gain of 1 and equal
    in-group weights, unless an override says otherwise.

    Returns
    -------
    entry : `dict`
        The entry, with the keys the stack runner writes.
    """
    count = len(frames)
    entry = {
        "paths": [frame.path for frame in frames],
        "frame_paths": [frame.path for frame in frames],
        "registration_frames": [
            {"dx": 0.0 if index == 0 else 1.5, "dy": 0.0 if index == 0 else -0.5, "star_count": 50 + index}
            for index in range(count)
        ],
        "registration_included": [True] * count,
        "weight": count * exposure**2 / 10.0**2,
        "frame_count": count,
        "exposure_seconds": exposure,
        "frame_noise_counts": 10.0,
        "gain": 1.0,
        "gain_measured": True,
        "sky_level_per_second": 0.01,
        "stack_weight": None,
        "holds_folded_frames": False,
    }
    entry.update(overrides)
    return entry


def _two_group_diagnostics(group_a: list, group_b: list, entry_a: dict, entry_b: dict) -> dict:
    """Join two group entries into the diagnostics a run reports.

    Returns
    -------
    diagnostics : `dict`
        The flat lists in group order and the two entries.
    """
    frames = [*group_a, *group_b]
    return {
        "combined_frame_paths": [frame.path for frame in frames],
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [*entry_a["registration_included"], *entry_b["registration_included"]],
        "images_stacked": len(frames),
        "exposure_group_weights": [entry_a, entry_b],
    }


def test_the_rule_gives_each_frame_the_group_weight_times_its_share() -> None:
    """Equal in-group weights split ``N t^2 / s^2`` over the frames."""
    result = group_frame_weights([[1.0, 1.0], [1.0, 1.0, 1.0]], [1.0, 10.0], [2, 3], [10.0, 20.0])

    # W = 2 * 1 / 100 = 0.02 for the first group.
    # W = 3 * 100 / 400 = 0.75 for the second.
    assert result.weights[0] == pytest.approx((0.01, 0.01))
    assert result.weights[1] == pytest.approx((0.25, 0.25, 0.25))
    assert result.reasons == ("", "")


def test_the_rule_splits_a_group_weight_by_the_in_group_weights() -> None:
    """Frame weights follow the in-group weights and sum to the group's."""
    in_group = wfwhm_weights([2.0, 3.0, 4.0])
    result = group_frame_weights([in_group], [5.0], [3], [10.0])

    group_weight = 3 * 5.0**2 / 10.0**2
    weights = result.weights[0]
    assert weights is not None
    assert math.fsum(weights) == pytest.approx(group_weight)
    assert weights[0] / weights[1] == pytest.approx(in_group[0] / in_group[1])


def test_the_rule_has_no_gain_in_it() -> None:
    """A gain other than 1 does not change the weights."""
    entry_frames = _frames("g", 2, "5")
    entry = _entry(entry_frames, 5.0, gain=0.6)
    other = _entry(entry_frames, 5.0, gain=1.0)

    first = determine_stack_members(entry_frames, _single_group(entry_frames, entry), None)
    second = determine_stack_members(entry_frames, _single_group(entry_frames, other), None)

    assert [member.raw_weight for member in first.members] == [member.raw_weight for member in second.members]


def _single_group(frames: list[SimpleNamespace], entry: dict) -> dict:
    """Make the diagnostics of a stack with one group entry.

    Returns
    -------
    diagnostics : `dict`
        The diagnostics.
    """
    return {
        "combined_frame_paths": [frame.path for frame in frames],
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True] * len(frames),
        "exposure_group_weights": [entry],
    }


@pytest.mark.parametrize(
    ("overrides", "expected_text"),
    [
        ({"holds_folded_frames": True}, "folded frames of another exposure length"),
        ({"stack_weight": "noise"}, "-weight=noise"),
        ({"stack_weight": "nbstars"}, "-weight=nbstars"),
        ({"gain_measured": False}, "could not measure the brightness gain"),
        ({"frame_noise_counts": None}, "frame noise"),
    ],
)
def test_a_group_with_an_unknown_weight_gives_unknown_weights_and_a_reason(
    overrides: dict, expected_text: str
) -> None:
    """Each unknown case gives unknown weights and a reason."""
    group_a = _frames("a", 3, "1")
    group_b = _frames("b", 2, "5")
    diagnostics = _two_group_diagnostics(
        group_a, group_b, _entry(group_a, 1.0), _entry(group_b, 5.0, **overrides)
    )

    membership = determine_stack_members([*group_a, *group_b], diagnostics, None)

    assert len(membership.members) == 5
    assert all(member.raw_weight is None for member in membership.members)
    assert normalized_weights(membership) == [None] * 5
    assert expected_text in membership.weights_reason
    assert "5 s exposure group" in membership.weights_reason


def test_a_group_whose_star_widths_siril_did_not_report_is_unknown() -> None:
    """With ``-weight=wfwhm`` and no widths the weights are unknown."""
    group_a = _frames("a", 3, "1")
    group_b = _frames("b", 2, "5")
    entry_a = _entry(group_a, 1.0, stack_weight="wfwhm")
    entry_b = _entry(group_b, 5.0, stack_weight="wfwhm")
    diagnostics = _two_group_diagnostics(group_a, group_b, entry_a, entry_b)

    membership = determine_stack_members([*group_a, *group_b], diagnostics, "wfwhm")

    assert normalized_weights(membership) == [None] * 5
    assert "did not report the frame weights" in membership.weights_reason


def test_a_group_entry_without_the_new_facts_is_unknown() -> None:
    """An entry with only a weight and a frame count is unknown."""
    frames = _frames("a", 2, "1")
    diagnostics = {
        "combined_frame_paths": [frame.path for frame in frames],
        "exposure_group_weights": [
            {"paths": [frame.path for frame in frames], "weight": 2.0, "frame_count": 2}
        ],
    }

    membership = determine_stack_members(frames, diagnostics, None)

    assert normalized_weights(membership) == [None, None]
    assert membership.weights_reason


def test_a_two_group_run_stores_weights_equal_to_the_rule() -> None:
    """Stored weights equal the rule, over registered frames only."""
    group_a = _frames("a", 4, "1")
    group_b = _frames("b", 3, "5")
    widths_a = [2.0, 3.0, 4.0, 2.5]
    widths_b = [3.0, 2.0, 5.0]
    entry_a = _entry(group_a, 1.0, stack_weight="wfwhm", gain=0.9)
    entry_b = _entry(group_b, 5.0, stack_weight="wfwhm", frame_noise_counts=12.0)
    for entry, widths in ((entry_a, widths_a), (entry_b, widths_b)):
        for facts, width in zip(entry["registration_frames"], widths, strict=True):
            facts["weighted_fwhm_px"] = width
    # Siril dropped the last frame of group A, so 3 were stacked.
    entry_a["registration_included"][3] = False
    entry_a["frame_count"] = 3
    diagnostics = _two_group_diagnostics(group_a, group_b, entry_a, entry_b)

    membership = determine_stack_members([*group_a, *group_b], diagnostics, "wfwhm")

    assert membership.weights_reason == ""
    assert [member.frame.path for member in membership.members] == [
        frame.path for frame in [*group_a[:3], *group_b]
    ]
    in_a = wfwhm_weights(widths_a[:3])
    in_b = wfwhm_weights(widths_b)
    weight_a = 3 * 1.0**2 / 10.0**2
    weight_b = 3 * 5.0**2 / 12.0**2
    expected = [weight_a * w / sum(in_a) for w in in_a] + [weight_b * w / sum(in_b) for w in in_b]
    assert [member.raw_weight for member in membership.members] == pytest.approx(expected)
    # The raw weights add up to the sum of the group weights.
    # The gain is not in them.
    assert math.fsum(expected) == pytest.approx(weight_a + weight_b)


def test_equal_group_weighting_shares_the_group_weight_equally() -> None:
    """With no Siril weighting every frame of a group counts ``W_g / N_g``."""
    group_a = _frames("a", 2, "1")
    group_b = _frames("b", 3, "5")
    entry_a = _entry(group_a, 1.0)
    entry_b = _entry(group_b, 5.0)
    membership = determine_stack_members(
        [*group_a, *group_b], _two_group_diagnostics(group_a, group_b, entry_a, entry_b), None
    )

    expected = [entry_a["weight"] / 2] * 2 + [entry_b["weight"] / 3] * 3
    assert [member.raw_weight for member in membership.members] == pytest.approx(expected)


def test_a_group_stack_names_the_heaviest_groups_identity_frame_as_its_reference() -> None:
    """The reference is the identity line of the heaviest group."""
    group_a = _frames("a", 3, "1")
    group_b = _frames("b", 2, "5")
    entry_a = _entry(group_a, 1.0)
    entry_b = _entry(group_b, 5.0)
    # Group B is the heavier one; its identity line is its first frame.
    entry_b["registration_frames"][0]["star_count"] = 77
    diagnostics = _two_group_diagnostics(group_a, group_b, entry_a, entry_b)
    # The merged lists have one zero-shift line per group.
    diagnostics["registration_frames"] = [*entry_a["registration_frames"], *entry_b["registration_frames"]]

    assert entry_b["weight"] > entry_a["weight"]
    assert registration_reference_from_diagnostics(diagnostics) == (group_b[0].path, 77)


def test_the_group_reference_is_none_when_the_heaviest_group_has_no_single_identity_line() -> None:
    """Two zero-shift lines in the heaviest group cannot name one reference."""
    group_a = _frames("a", 3, "1")
    group_b = _frames("b", 2, "5")
    entry_b = _entry(group_b, 5.0)
    entry_b["registration_frames"][1].update(dx=0.0, dy=0.0)

    diagnostics = _two_group_diagnostics(group_a, group_b, _entry(group_a, 1.0), entry_b)

    assert registration_reference_from_diagnostics(diagnostics) is None


def test_a_single_group_stack_keeps_its_one_identity_line_reference() -> None:
    """Without group entries the rule is the old one."""
    diagnostics = {
        "symlinked_light_paths": ["a", "b", "c"],
        "registration_frames": [
            {"dx": 1.0, "dy": 2.0, "star_count": 10},
            {"dx": 0.0, "dy": 0.0, "star_count": 33},
            {"dx": 3.0, "dy": 1.0, "star_count": 12},
        ],
    }
    assert registration_reference_from_diagnostics(diagnostics) == ("b", 33)


EXPOSURE_LISTS = [
    [1.0] * 10 + [5.0] * 10,
    [1.0] * 10 + [5.0] * 10 + [2.0, 2.0],
    [0.5] * 6 + [1.0] * 3 + [5.0] * 6 + [30.0],
    [1.0] * 10 + [1.0004] * 2 + [5.0] * 5,
    [3.2e-05] * 2 + [1.0] * 20,
    [1.0] * 3 + [5.0] * 4,
    [60.0] * 12,
    [120.0, 182.0, 182.0, 182.0, 182.0, 182.0, 120.0],
]


@pytest.mark.parametrize("exposures", EXPOSURE_LISTS)
def test_the_fold_rule_matches_how_frames_are_split(exposures: list[float]) -> None:
    """Each frame lands in the group `split_frames_by_exposure` puts it in."""
    frames = [
        SimpleNamespace(path=f"/f/{index}.fits", exposure=str(exposure))
        for index, exposure in enumerate(exposures)
    ]
    group_lengths = fold_exposure_lengths([float(frame.exposure) for frame in frames])
    groups = split_frames_by_exposure(frames)

    assert len(groups) == len(set(group_lengths))
    for group in groups:
        landed = {
            group_lengths[index]
            for index, frame in enumerate(frames)
            if any(frame is member for member in group.frames)
        }
        assert len(landed) == 1
    # A group holds folded frames when the rule sent a length there.
    for group in groups:
        keys = {float(f"{float(frame.exposure):.3g}") for frame in group.frames}
        assert exposure_group_holds_folded_frames(group.frames) is (len(keys) > 1)


def test_the_fold_rule_folds_a_short_list_into_the_nearest_length_by_ratio() -> None:
    """Frames of 2 s join the 1 s group, not the 5 s group."""
    exposures = [1.0] * 10 + [5.0] * 10 + [2.0] * (MINIMUM_FRAMES_PER_EXPOSURE_GROUP - 1)

    group_lengths = fold_exposure_lengths(exposures)

    assert group_lengths[-1] == pytest.approx(1.0)
    assert group_lengths[0] == pytest.approx(1.0)
    assert group_lengths[10] == pytest.approx(5.0)


def test_the_fold_rule_leaves_one_group_when_nothing_is_large_enough_or_a_length_is_unreadable() -> None:
    """With no large group or an unreadable length, all stay together."""
    assert fold_exposure_lengths([1.0, 2.0, 3.0]) == [None, None, None]
    assert fold_exposure_lengths([1.0] * 10 + [None]) == [None] * 11
    assert fold_exposure_lengths([]) == []
    assert fold_exposure_lengths([2.0] * 7) == [2.0] * 7


def test_the_two_rules_are_public() -> None:
    """The group weight rule and the fold rule are public."""
    assert astrometricslib.group_frame_weights is group_frame_weights
    assert astrometricslib.fold_exposure_lengths is fold_exposure_lengths


def test_the_combine_result_carries_gains_skies_weights_and_masks() -> None:
    """The result holds what the combination used, group by group."""
    group_a = make_group_a()
    group_b = make_linear_group_b(group_a) * 0.9
    covered_b = np.ones(group_a.shape, dtype=bool)
    covered_b[:2, :] = False

    combination = combine_exposure_group_images(
        [group_a, group_b],
        [1.0, 0.9],
        [20, 10],
        frame_noises=[1.0, 1.0],
        covered_masks=[np.ones(group_a.shape, dtype=bool), covered_b],
    )

    assert combination.image.dtype == np.float32
    assert combination.image.shape == group_a.shape
    assert combination.reference_index == 0
    assert combination.weights == pytest.approx((20 * 1.0**2, 10 * 0.9**2))
    assert combination.gains[0] == pytest.approx(1.0)
    assert combination.gains_measured == (True, True)
    assert combination.gains[1] == pytest.approx(TRUE_GAIN, rel=0.02)
    assert len(combination.sky_levels) == 2
    assert not combination.covered_masks[1][:2].any()
    assert combination.covered_masks[1][2:].all()
    # A pixel outside a group's covered area is not trusted in that group.
    assert not combination.trusted_masks[1][:2].any()


def test_the_combine_result_has_all_true_covered_masks_when_none_were_given() -> None:
    """Without covered masks every group is covered everywhere."""
    group_a = make_group_a()
    combination = combine_exposure_group_images([group_a], [1.0], [10], frame_noises=[1.0])

    assert combination.covered_masks[0].all()
    assert combination.gains == (1.0,)
    assert combination.gains_measured == (True,)


def test_empty_apertures_keep_out_of_a_groups_uncovered_border() -> None:
    """No aperture is drawn near a pixel a group does not cover."""
    generator = np.random.default_rng(3)
    plane = 1.0 + generator.normal(0.0, 0.01, (900, 900))
    covered = np.ones(plane.shape, dtype=bool)
    covered[:, :300] = False
    no_sources = np.full(plane.shape, np.inf)
    no_stars = np.empty((0, 2))

    without = place_empty_apertures(plane, no_stars, no_sources)
    with_mask = place_empty_apertures(plane, no_stars, no_sources, covered)

    assert min(column for _, column in without) < 300
    assert len(with_mask) > 0
    assert min(column for _, column in with_mask) > 300 + UNCOVERED_CLEARANCE_PX
    assert min(row for row, _ in with_mask) >= EDGE_MARGIN_PX


def _run_two_group_stack(tmp_path: Path, read_noise_fails: bool = False) -> tuple[str, dict]:
    """Run the runner's group combination on two synthetic group stacks.

    Group A has 20 frames of 1 s, group B 10 frames of 0.9 s, so A is the
    heaviest. Stacking, frame reading and alignment are replaced by stand-ins.
    Each group's engine facts are made up: one identity line, star widths and
    inclusion flags.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Where the group stacks are written.
    read_noise_fails : `bool`, optional
        Make the frame noise reading fail, as when a raw frame cannot be read.

    Returns
    -------
    result : `tuple`
        The combined stack's path and the run's diagnostics.
    """
    group_a_image = make_group_a()
    frames_a = [SimpleNamespace(path=f"/raw/a{index}.fits", camera=None, exposure="1") for index in range(20)]
    frames_b = [
        SimpleNamespace(path=f"/raw/b{index}.fits", camera=None, exposure="0.9") for index in range(10)
    ]
    groups = [ExposureGroup(1.0, frames_a), ExposureGroup(0.9, frames_b)]
    stack_paths = {
        "a0": write_fits(tmp_path / "stack_a.fits", group_a_image),
        "b0": write_fits(tmp_path / "stack_b.fits", make_linear_group_b(group_a_image) * 0.9),
    }
    fits.setval(stack_paths["a0"], "OBJECT", value="group-a")
    fits.setval(stack_paths["b0"], "OBJECT", value="group-b")

    def fake_stack_one_batch(
        _engine: object, frames: list[object], *_args: object, **_kwargs: object
    ) -> tuple[str, dict]:
        """Give back the prepared stack of the group the frames belong to.

        Returns
        -------
        result : `tuple`
            The stack's path and the group's diagnostics.
        """
        key = frames[0].path.split("/")[-1][:2]
        return stack_paths[key], {
            "images_stacked": len(frames),
            "symlinked_light_paths": [frame.path for frame in frames],
        }

    def fake_read_artifacts(path: str) -> dict:
        """Give a group's registration facts, as the engine would read them.

        Returns
        -------
        artifacts : `dict`
            One registration line per frame, the first one the identity.
        """
        count = 20 if path.endswith("stack_a.fits") else 10
        return {
            "registration_frames": [
                {
                    "dx": 0.0 if index == 0 else 2.0,
                    "dy": 0.0 if index == 0 else 1.0,
                    "star_count": 40 + index,
                    "weighted_fwhm_px": 2.0 + 0.1 * index,
                }
                for index in range(count)
            ],
            "registration_included": [True] * count,
        }

    def fake_align(
        images: list[np.ndarray], _reference_index: int, **_kwargs: object
    ) -> tuple[list[np.ndarray], list[np.ndarray], list[None]]:
        """Leave the images where they are; group B misses its top two rows.

        Returns
        -------
        result : `tuple`
            The images, their covered masks and no alignment records.
        """
        covered = [np.ones(image.shape, dtype=bool) for image in images]
        covered[1][:2, :] = False
        return list(images), covered, [None] * len(images)

    measure = (
        patch(
            "astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_frames",
            side_effect=OSError("unreadable"),
        )
        if read_noise_fails
        else patch(
            "astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_frames",
            return_value=([1.0, 1.0], [0.0, 0.0]),
        )
    )
    engine = SimpleNamespace(read_stack_artifacts=fake_read_artifacts)
    with (
        patch.object(stack_runner, "_stack_one_batch", fake_stack_one_batch),
        measure,
        patch(
            "astrometricslib.pipelines.stacking.processing.group_alignment.align_images_to_reference",
            fake_align,
        ),
        patch(
            "astrometricslib.pipelines.stacking.post_processing.exposure_group_report.measure_group_saturation",
            return_value=[],
        ),
    ):
        return stack_runner._stack_exposure_groups(
            engine, groups, "Synthetic", "combined.fits", None, False, stack_weight="wfwhm"
        )


def test_the_runner_keeps_each_groups_own_siril_data_and_the_applied_gain(tmp_path: Path) -> None:
    """Each entry holds its group's frames, facts, gain and noise."""
    path, diagnostics = _run_two_group_stack(tmp_path)

    entries = {entry["exposure_seconds"]: entry for entry in diagnostics["exposure_group_weights"]}
    assert set(entries) == {1.0, 0.9}
    heavy, light = entries[1.0], entries[0.9]
    assert heavy["frame_count"] == 20
    assert heavy["weight"] == pytest.approx(20 * 1.0**2 / 1.0**2)
    assert light["weight"] == pytest.approx(10 * 0.9**2 / 1.0**2)
    assert heavy["frame_noise_counts"] == pytest.approx(1.0)
    assert heavy["gain"] == pytest.approx(1.0) and heavy["gain_measured"] is True
    assert light["gain"] == pytest.approx(TRUE_GAIN, rel=0.02) and light["gain_measured"] is True
    assert heavy["stack_weight"] == "wfwhm"
    assert heavy["holds_folded_frames"] is False
    assert len(heavy["registration_frames"]) == 20 and len(light["registration_frames"]) == 10
    assert heavy["frame_paths"] == [f"/raw/a{index}.fits" for index in range(20)]
    assert light["registration_included"] == [True] * 10
    # The merged diagnostics keep their joined lists.
    assert len(diagnostics["symlinked_light_paths"]) == 30
    assert diagnostics["combined_frame_paths"][:20] == heavy["frame_paths"]
    # The combined header comes from the heaviest group.
    assert fits.getheader(path)["OBJECT"] == "group-a"
    # The covered mask is the area every group has data in.
    covered = diagnostics["covered_in_every_group"]
    assert not covered[:2].any() and covered[2:].all()


def test_the_runner_group_stack_membership_follows_the_rule_and_names_the_reference(tmp_path: Path) -> None:
    """The runner's diagnostics give rule weights and the reference."""
    _, diagnostics = _run_two_group_stack(tmp_path)
    frames = [
        SimpleNamespace(path=path, exposure="1" if "/a" in path else "0.9", timestamp=1.0e9)
        for entry in diagnostics["exposure_group_weights"]
        for path in entry["paths"]
    ]
    # The joined sequence's inclusion flags, as read for the final stack.
    diagnostics["registration_included"] = [True] * 30

    membership = determine_stack_members(frames, diagnostics, "wfwhm")

    assert membership.weights_reason == ""
    heavy_widths = [2.0 + 0.1 * index for index in range(20)]
    heavy_in_group = wfwhm_weights(heavy_widths)
    heavy_weight = 20.0
    expected_first = heavy_weight * heavy_in_group[0] / sum(heavy_in_group)
    assert membership.members[0].raw_weight == pytest.approx(expected_first)
    assert registration_reference_from_diagnostics(diagnostics) == ("/raw/a0.fits", 40)


def test_the_runner_stores_the_applied_gain_in_the_group_summaries(tmp_path: Path) -> None:
    """A combined group's summary holds the gain that was applied."""
    _, diagnostics = _run_two_group_stack(tmp_path)

    summaries = {entry["exposure_seconds"]: entry for entry in diagnostics["exposure_group_summaries"]}
    assert summaries[1.0]["gain_applied"] == pytest.approx(1.0)
    assert summaries[0.9]["gain_applied"] == pytest.approx(TRUE_GAIN, rel=0.02)


def test_an_unread_frame_noise_makes_the_weights_unknown_with_a_reason(tmp_path: Path) -> None:
    """When raw frames cannot be read the stand-in noise is not a weight."""
    _, diagnostics = _run_two_group_stack(tmp_path, read_noise_fails=True)

    assert all(entry["frame_noise_counts"] is None for entry in diagnostics["exposure_group_weights"])
    frames = [
        SimpleNamespace(path=path, exposure="1" if "/a" in path else "0.9", timestamp=1.0e9)
        for entry in diagnostics["exposure_group_weights"]
        for path in entry["paths"]
    ]
    diagnostics["registration_included"] = [True] * 30
    membership = determine_stack_members(frames, diagnostics, "wfwhm")

    assert normalized_weights(membership) == [None] * 30
    assert "frame noise" in membership.weights_reason
