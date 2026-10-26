"""Purpose: Tests for the noise-growth forward model.

Description: Checks the stack variance for equal weights, noise weights and
star-width weights on made-up frames, the known penalty of equal weights, the
moonlit case, mode identification, the take-N-more search, the stacker
constant it copies, and the noise-floor fit.
"""

import math

import numpy as np
import pytest

from wayfindinglib.analytics.result_quality import noise_growth as ng
from wayfindinglib.analytics.result_quality.noise_growth import StackFrame, WeightingMode


def _frame(
    index: int = 0, exposure: float = 60.0, sky: float = 1.0, read: float = 0.0, width: float | None = None
) -> StackFrame:
    """Build one stack frame.

    Parameters
    ----------
    index : `int`
        Frame number.
    exposure, sky, read : `float`
        Exposure, sky rate and read noise.
    width : `float` or `None`
        Star width.

    Returns
    -------
    frame : `StackFrame`
        The frame.
    """
    return StackFrame(
        frame_id=f"f{index}",
        exposure_seconds=exposure,
        sky_rate_e_per_s_per_px=sky,
        read_noise_e=read,
        star_width=width,
    )


def test_equal_frames_give_variance_over_n() -> None:
    """N identical frames have 1/N of one frame's variance in every mode."""
    frames = [_frame(i, width=3.0) for i in range(8)]
    one = frames[0].rate_variance
    for mode in (WeightingMode.EQUAL, WeightingMode.NOISE, WeightingMode.WFWHM):
        assert ng.forecast_stack_noise(frames, mode).variance == pytest.approx(one / 8)


def test_equal_weights_penalty_matches_the_stacker_formula() -> None:
    """With no read noise.

    Equal weights cost sqrt(mean(1/t) mean(t)) in noise over noise weights.
    """
    times = [30.0, 60.0, 120.0, 120.0]
    frames = [_frame(i, exposure=t) for i, t in enumerate(times)]
    equal = ng.forecast_stack_noise(frames, WeightingMode.EQUAL).noise
    weighted = ng.forecast_stack_noise(frames, WeightingMode.NOISE).noise
    expected = math.sqrt(np.mean(1 / np.array(times)) * np.mean(times))
    assert equal / weighted == pytest.approx(expected)


def test_moonlit_frames_cost_more_with_equal_weights_than_noise_weights() -> None:
    """Noise weights down-weight moonlit frames.

    Equal weights give a smaller improvement.
    """
    dark = [_frame(i, sky=0.5) for i in range(10)]
    moonlit = [_frame(10 + i, sky=3.0) for i in range(10)]
    equal = ng.forecast_stack_noise(dark + moonlit, WeightingMode.EQUAL).noise
    noise = ng.forecast_stack_noise(dark + moonlit, WeightingMode.NOISE).noise
    dark_only = ng.forecast_stack_noise(dark, WeightingMode.EQUAL).noise
    assert noise < equal
    gain_equal = 1 - equal / dark_only
    gain_noise = 1 - noise / dark_only
    assert gain_noise > gain_equal


def test_wfwhm_weights_follow_the_siril_formula_and_zero_the_blurriest_frame() -> None:
    """The weights are (1/f^2 - 1/fmax^2) / (1/fmin^2 - 1/fmax^2)."""
    frames = [_frame(0, width=2.0), _frame(1, width=3.0), _frame(2, width=4.0)]
    weights = ng.frame_weights(frames, WeightingMode.WFWHM)
    assert weights is not None
    assert sum(weights) / len(weights) == pytest.approx(1.0)  # the stacker's weights have mean 1
    assert weights[2] == pytest.approx(0.0)
    assert weights[1] / weights[0] == pytest.approx((1 / 9 - 1 / 16) / (1 / 4 - 1 / 16))


def test_wfwhm_weights_are_recomputed_over_old_and_new_frames() -> None:
    """Adding a sharper frame changes the old frames' weights."""
    old = [_frame(0, width=3.0), _frame(1, width=4.0)]
    before = ng.frame_weights(old, WeightingMode.WFWHM)
    after = ng.frame_weights([*old, _frame(2, width=2.0)], WeightingMode.WFWHM)
    assert before is not None and after is not None
    assert before[0] == pytest.approx(2.0)  # mean 1 over two frames: weights 1 and 0
    assert after[0] < before[0]


def test_wfwhm_without_widths_and_unknown_mode_give_reasons() -> None:
    """A missing width or an unrecorded mode gives no forecast and a reason."""
    assert ng.forecast_stack_noise([_frame(0)], WeightingMode.WFWHM).reason
    unknown = ng.forecast_stack_noise([_frame(0)], WeightingMode.UNKNOWN)
    assert unknown.noise is None
    assert "not recorded" in (unknown.reason or "")
    assert ng.forecast_stack_noise([], WeightingMode.EQUAL).noise is None


def test_predict_weighting_mode_follows_the_stacker_rule() -> None:
    """Equal exposures stay equal.

    Mixed exposures switch to noise; a configured mode wins.
    """
    assert ng.predict_weighting_mode(None, [60.0] * 10) == WeightingMode.EQUAL
    assert ng.predict_weighting_mode(None, [30.0, 60.0, 120.0]) == WeightingMode.NOISE
    assert ng.predict_weighting_mode("wfwhm", [60.0] * 3) == WeightingMode.WFWHM
    assert ng.predict_weighting_mode("nbstars", [60.0] * 3) == WeightingMode.UNKNOWN


# -----------------------------------------------------------------------------
# Exposure-group stacks (decision D7)
# -----------------------------------------------------------------------------

CAMERA_GAIN = 0.8
"""Electrons per ADU of the made-up camera."""


def _group(
    exposure: float,
    count: int,
    noise_adu: float | None = 30.0,
    gain: float = 1.0,
    sky: float = 2.0,
    read: float = 3.5,
    camera_gain: float | None = CAMERA_GAIN,
) -> ng.StackGroup:
    """Build one exposure group of equally exposed frames.

    Parameters
    ----------
    exposure : `float`
        The group's exposure, in seconds.
    count : `int`
        Frames in the group.
    noise_adu : `float` or `None`
        The group's frame noise ``s_g``, in ADU.
    gain : `float`
        The group's applied gain.
    sky, read : `float`
        Sky rate and read noise of every frame.
    camera_gain : `float` or `None`
        The camera gain, in electrons per ADU.

    Returns
    -------
    group : `StackGroup`
        The group. Star widths vary a little so the width weights differ.
    """
    frames = tuple(
        _frame(index, exposure=exposure, sky=sky, read=read, width=3.0 + 0.1 * (index % 4))
        for index in range(count)
    )
    return ng.StackGroup(frames=frames, frame_noise_adu=noise_adu, gain=gain, electrons_per_adu=camera_gain)


def test_predict_weighting_mode_names_the_group_mode_for_several_groups() -> None:
    """Two lengths with 5 frames each are a group stack, not noise or wfwhm.

    A length with too few frames is folded away, so one length is left and the
    old rule applies.
    """
    assert ng.predict_weighting_mode("wfwhm", [30.0] * 6 + [60.0] * 10) == WeightingMode.GROUP
    assert ng.predict_weighting_mode("wfwhm", [30.0] * 3 + [60.0] * 10) == WeightingMode.WFWHM


def test_identify_weighting_mode_does_not_call_a_group_stack_wfwhm() -> None:
    """A two-group stack is group or unknown, never wfwhm over all frames."""
    groups = [_group(30.0, 6, noise_adu=20.0), _group(60.0, 8, noise_adu=30.0)]
    frames = [frame for group in groups for frame in group.frames]
    raw = ng.group_coefficients(groups).raw_weights
    assert raw is not None
    # Without the groups' frame noises the weights cannot be matched.
    unknown = ng.identify_weighting_mode(frames, raw)
    assert unknown.mode == WeightingMode.UNKNOWN
    matched = ng.identify_weighting_mode(frames, raw, group_frame_noises_adu={30.0: 20.0, 60.0: 30.0})
    assert matched.mode == WeightingMode.GROUP
    # Weights that are wfwhm over all frames do not match the group weights.
    over_all = ng.frame_weights(frames, WeightingMode.WFWHM)
    assert over_all is not None
    wrong = ng.identify_weighting_mode(frames, over_all, group_frame_noises_adu={30.0: 20.0, 60.0: 30.0})
    assert wrong.mode == WeightingMode.UNKNOWN


def test_one_group_with_gain_one_equals_the_wfwhm_forecast() -> None:
    """One group is the single-stack formula, times t_bar squared."""
    group = _group(60.0, 8)
    forecast = ng.forecast_group_noise([group])
    plain = ng.forecast_stack_noise(list(group.frames), WeightingMode.WFWHM)
    assert forecast.mode == WeightingMode.GROUP
    assert forecast.variance == pytest.approx(plain.variance * 60.0**2)
    assert forecast.mean_exposure_seconds == pytest.approx(60.0)


def test_group_forecast_needs_the_frame_noise_and_widths() -> None:
    """A missing s_g or star width gives a reason, not a number."""
    assert ng.forecast_group_noise([_group(60.0, 8, noise_adu=None)]).noise is None
    bare = ng.StackGroup(frames=(_frame(0), _frame(1)), frame_noise_adu=10.0)
    assert "star width" in str(ng.forecast_group_noise([bare]).reason)


def test_group_forecast_agrees_with_the_stack_unit_noise_model() -> None:
    """The rate-unit and stack-unit models give the same noise ratio.

    Two stacks of the same frames, weights and gains (the second holds more
    60 s frames), gains other than 1. `forecast_noise_with_weights` (rate
    units) and `expected_exposure_group_noise` (stack units) must give the
    same ratio, and their absolute values differ by one constant.
    """
    from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
        ExposureGroupNoiseFacts,
        FrameNoiseInput,
        effective_aperture_area_px,
        expected_exposure_group_noise,
    )

    def both(counts: tuple[int, int, int]) -> tuple[float, float]:
        """Give the rate-unit and stack-unit noise of one stack.

        Parameters
        ----------
        counts : `tuple` [`int`, `int`, `int`]
            Frames in the 30 s, 60 s and 120 s groups.

        Returns
        -------
        noises : `tuple` [`float`, `float`]
            ``forecast_group_noise`` and the stack-unit noise.
        """
        groups = [
            _group(30.0, counts[0], noise_adu=20.0, gain=0.9, sky=2.5),
            _group(60.0, counts[1], noise_adu=30.0, gain=1.0, sky=2.0),
            _group(120.0, counts[2], noise_adu=45.0, gain=1.3, sky=1.8),
        ]
        coefficients = ng.group_coefficients(groups)
        assert coefficients.raw_weights is not None
        frames = [frame for group in groups for frame in group.frames]
        rate_units = ng.forecast_noise_with_weights(
            frames,
            [raw / gain for raw, gain in zip(coefficients.raw_weights, coefficients.gains, strict=True)],
            WeightingMode.GROUP,
            weight_normalization=coefficients.total_group_weight,
        )
        group_weights = [len(g.frames) * g.mean_exposure_seconds**2 / g.frame_noise_adu**2 for g in groups]
        inputs = [
            FrameNoiseInput(
                weight=raw,
                sky_electrons=frame.sky_rate_e_per_s_per_px * frame.exposure_seconds,
                read_noise_electrons=frame.read_noise_e,
                gain_electrons_per_adu=CAMERA_GAIN,
            )
            for raw, frame in zip(coefficients.raw_weights, frames, strict=True)
        ]
        indices = [position for position, group in enumerate(groups) for _ in group.frames]
        facts = [
            ExposureGroupNoiseFacts(
                combine_weight=weight,
                exposure_seconds=group.mean_exposure_seconds,
                frame_count=len(group.frames),
                gain=group.gain,
                gain_measured=True,
                sky_level_per_second=1.0,
            )
            for weight, group in zip(group_weights, groups, strict=True)
        ]
        outcome = expected_exposure_group_noise(inputs, indices, facts)
        assert outcome.result is not None
        mean_exposure = sum(len(g.frames) * g.mean_exposure_seconds for g in groups) / len(frames)
        # The rate-unit noise is times t_bar here (the forecast's scale).
        return rate_units.noise * mean_exposure, outcome.result.total_noise

    rate_a, stack_a = both((10, 20, 12))
    rate_b, stack_b = both((10, 40, 12))
    assert rate_b / rate_a == pytest.approx(stack_b / stack_a, rel=1e-9)
    assert stack_b / stack_a != pytest.approx(1.0, abs=0.01)
    constant = math.sqrt(effective_aperture_area_px()) / 65535.0 / CAMERA_GAIN
    assert stack_a / rate_a == pytest.approx(constant, rel=1e-9)


def test_forecast_group_noise_matches_forecast_with_weights_and_t_bar() -> None:
    """`forecast_group_noise` is the rate-unit forecast scaled by t_bar."""
    groups = [_group(30.0, 6, 20.0, gain=0.9), _group(60.0, 8, 30.0)]
    forecast = ng.forecast_group_noise(groups)
    coefficients = ng.group_coefficients(groups)
    assert coefficients.raw_weights is not None
    frames = [frame for group in groups for frame in group.frames]
    base = ng.forecast_noise_with_weights(
        frames,
        [raw / gain for raw, gain in zip(coefficients.raw_weights, coefficients.gains, strict=True)],
        weight_normalization=coefficients.total_group_weight,
    )
    t_bar = (6 * 30.0 + 8 * 60.0) / 14
    assert forecast.noise == pytest.approx(base.noise * t_bar)


def test_frame_noise_comes_back_from_the_stored_group_weight() -> None:
    """``s_g = t sqrt(N / W)`` undoes ``W = N t^2 / s^2``."""
    weight = 33 * 60.0**2 / 37.74**2
    assert ng.frame_noise_from_group_weight(weight, 33, 60.0) == pytest.approx(37.74)
    with pytest.raises(ValueError, match="positive"):
        ng.frame_noise_from_group_weight(0.0, 33, 60.0)


def test_predicted_group_frame_noise_converts_electrons_to_adu() -> None:
    """The M 27 60 s frame: 933 ADU of sky, gain 0.783, read noise 3.8 e-.

    The plan (section 3.6.1) gives 35.1 ADU; the function gives 34.9, within
    the 1 percent the sky level is known to.
    """
    frame = _frame(sky=933.0 * 0.783 / 60.0, read=3.8)
    assert ng.predict_group_frame_noise_adu([frame], 0.783) == pytest.approx(35.1, abs=0.5)
    with pytest.raises(ValueError, match="gain"):
        ng.predict_group_frame_noise_adu([frame], 0.0)


def test_new_frames_of_an_existing_length_join_that_group() -> None:
    """New 60 s frames join the 60 s group, which keeps its measured s_g."""
    existing = [_group(30.0, 8, 20.0), _group(60.0, 10, 30.0, gain=1.1)]
    plan = ng.plan_group_stack(existing, [_frame(100 + i, exposure=60.0, width=3.0) for i in range(4)])
    assert {placement.outcome for placement in plan.placements} == {"joins_existing_group"}
    assert plan.groups[1].frame_noise_adu == pytest.approx(30.0)
    assert len(plan.groups[1].frames) == 14
    assert plan.forecast.noise is not None
    assert not plan.heaviest_group_changed


def test_five_new_frames_of_a_new_length_make_a_group_with_a_predicted_noise() -> None:
    """A new 120 s group is predicted in ADU, with the nearest gain."""
    existing = [_group(30.0, 8, 20.0, gain=0.9), _group(60.0, 10, 30.0, gain=1.1)]
    new = [_frame(100 + i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(5)]
    plan = ng.plan_group_stack(existing, new)
    planned = plan.groups[-1]
    assert {placement.outcome for placement in plan.placements} == {"starts_new_group"}
    assert planned.noise_predicted
    assert planned.gain == pytest.approx(1.1)
    assert planned.frame_noise_adu == pytest.approx(math.sqrt(2.0 * 120.0 + 3.5**2) / CAMERA_GAIN)
    assert plan.forecast.noise is not None


def test_a_new_group_without_a_camera_gain_is_refused() -> None:
    """The ADU conversion cannot be guessed."""
    existing = [_group(60.0, 10, 30.0, camera_gain=None)]
    new = [_frame(100 + i, exposure=120.0, width=3.0) for i in range(5)]
    plan = ng.plan_group_stack(existing, new)
    assert plan.forecast.noise is None
    assert "camera gain" in str(plan.reason)
    assert ng.plan_group_stack(existing, new, electrons_per_adu=0.8).forecast.noise is not None


def test_few_new_frames_are_folded_where_the_stacker_would_fold_them() -> None:
    """Three 100 s frames go into the 120 s group, the nearest in ratio."""
    existing = [_group(60.0, 10, 30.0), _group(120.0, 10, 45.0)]
    new = [_frame(100 + i, exposure=100.0, width=3.0) for i in range(3)]
    plan = ng.plan_group_stack(existing, new)
    assert {placement.outcome for placement in plan.placements} == {"folded_into_group"}
    assert {placement.group_exposure_seconds for placement in plan.placements} == {120.0}
    assert plan.folded_group_lengths == [120.0]
    assert len(plan.groups[1].frames) == 13


def test_a_stack_the_stacker_would_not_split_is_refused() -> None:
    """With no length at 5 frames the stacker makes one mixed group."""
    existing = [_group(60.0, 3, 30.0)]
    new = [_frame(100 + i, exposure=120.0, width=3.0) for i in range(2)]
    plan = ng.plan_group_stack(existing, new)
    assert plan.forecast.noise is None
    assert "mixed lengths" in str(plan.reason)


def test_new_frames_that_change_the_heaviest_group_are_flagged() -> None:
    """Many new 120 s frames outweigh the 60 s group: confounded."""
    existing = [_group(30.0, 8, 20.0), _group(60.0, 60, 30.0)]
    few = ng.plan_group_stack(
        existing, [_frame(100 + i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(5)]
    )
    many = ng.plan_group_stack(
        existing, [_frame(100 + i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(60)]
    )
    assert few.heaviest_exposure_before == pytest.approx(60.0)
    assert not few.heaviest_group_changed
    assert many.heaviest_exposure_after == pytest.approx(120.0)
    assert many.heaviest_group_changed


def test_take_more_in_groups_never_asks_for_fewer_than_five_of_a_new_length() -> None:
    """A new exposure length starts at 5 frames, even when 1 would do."""
    existing = [_group(60.0, 6, 30.0)]
    template = [_frame(i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(6)]
    result = ng.frames_to_cut_noise_in_groups(
        existing, template, new_exposure_seconds=120.0, target_noise_ratio=0.99
    )
    assert result.new_exposure_group
    assert result.frames_needed is not None
    assert result.frames_needed >= ng.MINIMUM_FRAMES_NEW_EXPOSURE_LENGTH


def test_take_more_in_groups_for_an_existing_length_matches_the_plan() -> None:
    """The answer reaches the target, and one frame fewer does not.

    The ratio is taken out of the combine's scale (the mean exposure per
    frame changes when frames are added), as the measured normalized noise
    is.
    """
    existing = [_group(30.0, 8, 20.0), _group(60.0, 10, 30.0, gain=1.1)]
    template = [_frame(i, exposure=60.0, sky=2.0, read=3.5, width=3.0 + 0.1 * (i % 4)) for i in range(8)]
    result = ng.frames_to_cut_noise_in_groups(existing, template, new_exposure_seconds=60.0)
    assert result.frames_needed is not None
    current = ng.forecast_group_noise(existing)

    def ratio(count: int) -> float:
        """Give the predicted ratio after adding `count` frames.

        Parameters
        ----------
        count : `int`
            Frames to add.

        Returns
        -------
        ratio : `float`
            Later noise over today's.
        """
        later = ng.plan_group_stack(existing, _new_copies(template, count, 60.0)).forecast
        value = ng.noise_ratio_with_floor(current, later, None)
        assert value is not None
        return value

    assert ratio(result.frames_needed) <= ng.TARGET_NOISE_RATIO
    assert ratio(result.frames_needed - 1) > ng.TARGET_NOISE_RATIO


def _new_copies(template: list[StackFrame], count: int, exposure: float) -> list[StackFrame]:
    """Copy template frames as new frames, as the take-more search does.

    Parameters
    ----------
    template : `list` [`StackFrame`]
        The frames to cycle through.
    count : `int`
        How many new frames.
    exposure : `float`
        Their exposure, in seconds.

    Returns
    -------
    frames : `list` [`StackFrame`]
        The new frames.
    """
    return [
        template[i % len(template)].model_copy(update={"frame_id": f"new_{i}", "exposure_seconds": exposure})
        for i in range(count)
    ]


# -----------------------------------------------------------------------------
# The noise floor of one stack (decision D8)
# -----------------------------------------------------------------------------

# Measured and random noise of the validation stacks, in stack units
# (plan section 3.6.1, after the D9 normalization scale). The aperture count
# is the one that gives the plan's 11.7 percent error.
APERTURES_FOR_11_7_PERCENT = 100


def test_m27_has_a_clear_noise_floor() -> None:
    """M 27, 60 s: 3.097e-3 measured against 1.048e-3 random."""
    floor = ng.measure_stack_noise_floor(3.097e-3, 1.048e-3, APERTURES_FOR_11_7_PERCENT)
    assert floor.floor_detected
    assert floor.floor_noise == pytest.approx(math.sqrt(3.097e-3**2 - 1.048e-3**2))
    assert floor.floor_variance_share is not None
    assert floor.floor_variance_share > 0.85
    assert floor.floor_to_random_variance_ratio == pytest.approx((3.097e-3**2 - 1.048e-3**2) / 1.048e-3**2)


def test_m81_has_no_floor_at_two_sigma() -> None:
    """M 81: 1.126e-3 measured against 0.921e-3 random is within 2 sigma."""
    floor = ng.measure_stack_noise_floor(1.126e-3, 0.921e-3, APERTURES_FOR_11_7_PERCENT)
    assert not floor.floor_detected
    assert floor.floor_noise is None
    assert "No floor" in floor.reason


def test_m57_floor_is_found() -> None:
    """M 57: 1.057e-3 against 0.596e-3 is a floor, more than 2 sigma up."""
    floor = ng.measure_stack_noise_floor(1.057e-3, 0.596e-3, APERTURES_FOR_11_7_PERCENT)
    assert floor.floor_detected
    assert floor.floor_noise == pytest.approx(math.sqrt(1.057e-3**2 - 0.596e-3**2))


def test_a_stack_quieter_than_predicted_has_no_floor() -> None:
    """Measured below predicted is no floor, and bad input is refused."""
    assert not ng.measure_stack_noise_floor(0.9e-3, 1.0e-3, 100).floor_detected
    with pytest.raises(ValueError, match="positive"):
        ng.measure_stack_noise_floor(0.0, 1.0e-3, 100)
    with pytest.raises(ValueError, match="2 apertures"):
        ng.measure_stack_noise_floor(1.0e-3, 0.5e-3, 1)


def test_the_floor_slows_the_noise_ratio() -> None:
    """With the M 27 floor, doubling the frames barely lowers the noise."""
    frames = [_frame(i, exposure=60.0, sky=1.0, read=0.0, width=3.0) for i in range(20)]
    current = ng.forecast_stack_noise(frames, WeightingMode.WFWHM)
    doubled = ng.forecast_stack_noise(frames * 2, WeightingMode.WFWHM)
    bare = ng.noise_ratio_with_floor(current, doubled, None)
    floor = ng.measure_stack_noise_floor(3.097e-3, 1.048e-3, APERTURES_FOR_11_7_PERCENT)
    with_floor = ng.noise_ratio_with_floor(current, doubled, floor)
    assert bare == pytest.approx(1 / math.sqrt(2))
    assert with_floor is not None
    assert 0.95 < with_floor < 1.0
    share = floor.floor_variance_share
    assert with_floor == pytest.approx(math.sqrt(share + (1 - share) / 2))


def test_take_more_states_the_floor_instead_of_a_huge_count() -> None:
    """The M 27 floor is most of the noise: no count reaches 0.707."""
    frames = [_frame(i, exposure=60.0, sky=1.0, read=0.0, width=3.0) for i in range(20)]
    floor = ng.measure_stack_noise_floor(3.097e-3, 1.048e-3, APERTURES_FOR_11_7_PERCENT)
    result = ng.frames_to_cut_noise(frames, frames, WeightingMode.WFWHM, floor=floor)
    assert result.frames_needed is None
    assert result.floor_limited
    assert result.floor_noise == pytest.approx(floor.floor_noise)
    assert result.lowest_reachable_ratio is not None
    assert result.lowest_reachable_ratio > ng.TARGET_NOISE_RATIO
    assert "floor" in str(result.reason)
    # M 81 has no floor, so the same frames give a number.
    no_floor = ng.measure_stack_noise_floor(1.126e-3, 0.921e-3, APERTURES_FOR_11_7_PERCENT)
    assert ng.frames_to_cut_noise(frames, frames, WeightingMode.WFWHM, floor=no_floor).frames_needed == 20


def test_a_small_floor_raises_the_count_and_is_stated() -> None:
    """A floor of 30 percent of the variance needs more frames."""
    frames = [_frame(i, exposure=60.0, sky=1.0, read=0.0, width=3.0) for i in range(20)]
    floor = ng.StackNoiseFloor(
        source="single_stack",
        floor_detected=True,
        floor_noise=1.0e-3,
        floor_to_random_variance_ratio=0.3 / 0.7,
        floor_variance_share=0.3,
        reason="made up",
    )
    plain = ng.frames_to_cut_noise(frames, frames, WeightingMode.WFWHM)
    floored = ng.frames_to_cut_noise(frames, frames, WeightingMode.WFWHM, floor=floor)
    assert plain.frames_needed == 20
    assert floored.frames_needed is not None
    assert floored.frames_needed > 20
    assert floored.floor_noise == pytest.approx(1.0e-3)
    assert "noise floor" in str(floored.reason)


def test_the_single_stack_floor_wins_over_a_fit_with_no_check() -> None:
    """A two-stack fit has no check; the single-stack result decides."""
    single = ng.measure_stack_noise_floor(1.126e-3, 0.921e-3, APERTURES_FOR_11_7_PERCENT)
    steps = [
        ng.NoiseStep(label="a", expected_variance=1.0, measured_noise=1.2, apertures=2000),
        ng.NoiseStep(label="b", expected_variance=0.5, measured_noise=1.0, apertures=2000),
    ]
    fit = ng.fit_noise_floor(steps)
    assert fit.floor_detected
    assert fit.degrees_of_freedom == 0
    chosen = ng.choose_noise_floor(single, fit, current_expected_variance=0.5)
    assert chosen is not None
    assert chosen.source == "single_stack"
    assert not chosen.floor_detected
    assert "no degree of freedom" in chosen.reason


def test_a_checked_fit_wins_over_the_single_stack_result() -> None:
    """With a degree of freedom the fit decides."""
    steps = [
        ng.NoiseStep(
            label=name,
            expected_variance=expected,
            measured_noise=math.sqrt(0.9 * expected + 0.4),
            apertures=400,
        )
        for name, expected in (("a", 1.0), ("b", 0.5), ("c", 0.25))
    ]
    fit = ng.fit_noise_floor(steps)
    assert fit.floor_detected
    assert fit.degrees_of_freedom == 1
    single = ng.measure_stack_noise_floor(1.126e-3, 0.921e-3, APERTURES_FOR_11_7_PERCENT)
    chosen = ng.choose_noise_floor(single, fit, current_expected_variance=0.25)
    assert chosen is not None
    assert chosen.source == "fit"
    assert chosen.floor_to_random_variance_ratio == pytest.approx(0.4 / (0.9 * 0.25), rel=0.05)
    assert ng.choose_noise_floor(None, None) is None
    only_single = ng.choose_noise_floor(single)
    assert only_single is not None
    assert only_single.source == "single_stack"


def test_the_group_ratio_leaves_the_mean_exposure_scale_out() -> None:
    """The ratio compares noise as a fraction of the stars' flux.

    The combine multiplies the stack by the mean exposure per frame, and the
    reference stars' flux follows it, so the factor cancels in the measured
    normalized noise. Adding frames of the longer exposure raises the mean
    exposure; the stack-scale noise ratio is then higher than the ratio to
    compare, by exactly that rise.
    """
    existing = [_group(30.0, 8, 20.0), _group(60.0, 10, 30.0)]
    template = [_frame(i, exposure=60.0, sky=2.0, read=3.5, width=3.0 + 0.1 * (i % 4)) for i in range(8)]
    current = ng.forecast_group_noise(existing)
    later = ng.plan_group_stack(existing, _new_copies(template, 12, 60.0)).forecast

    ratio = ng.noise_ratio_with_floor(current, later, None)

    assert ratio is not None
    tau = later.mean_exposure_seconds / current.mean_exposure_seconds
    assert tau > 1.05
    assert ratio == pytest.approx(later.noise / current.noise / tau)


# ---------------------------------------------------------------------------
# Folded groups (decision D10, option B)
# ---------------------------------------------------------------------------


def _folded_group(reference: float | None, sky: float = 2.0, read: float = 3.5) -> ng.StackGroup:
    """Build a 120 s group that holds one folded 300 s frame.

    Parameters
    ----------
    reference : `float` or `None`
        The exposure of the group's reference frame.
    sky, read : `float`
        Sky rate and read noise of every frame.

    Returns
    -------
    group : `StackGroup`
        Eight 120 s frames and one 300 s frame with equal in-group weights.
    """
    frames = (
        *(_frame(index, exposure=120.0, sky=sky, read=read) for index in range(8)),
        _frame(8, exposure=300.0, sky=sky, read=read),
    )
    return ng.StackGroup(
        frames=frames,
        frame_noise_adu=30.0,
        in_group_weights=(1.0 / 9.0,) * 9,
        reference_exposure_seconds=reference,
        noise_length_seconds=120.0,
    )


def test_a_folded_group_forecast_matches_the_hand_computation() -> None:
    """One group of 8 x 120 s and 1 x 300 s, the reference a 120 s frame.

    The coefficient of each frame is 1/9. The 300 s frame is multiplied by
    k = 120/300 = 0.4, so its variance (600 + 12.25 electrons squared) counts
    0.16 times. The combine divides by t_g = 140 s and multiplies by
    t_bar = 140 s, so for one group the two cancel.
    """
    forecast = ng.forecast_group_noise([_folded_group(120.0)])
    expected = ((8 * 252.25) + 0.16 * 612.25) / 81.0
    assert forecast.variance == pytest.approx(expected, rel=1e-12)
    assert forecast.mean_exposure_seconds == pytest.approx(140.0)


def test_a_folded_group_with_the_long_frame_as_reference_scales_the_short_frames_up() -> None:
    """With a 300 s reference each 120 s frame is multiplied by 2.5."""
    forecast = ng.forecast_group_noise([_folded_group(300.0)])
    expected = ((8 * 2.5**2 * 252.25) + 612.25) / 81.0
    assert forecast.variance == pytest.approx(expected, rel=1e-12)


def test_the_noise_factors_use_the_electron_form_and_fall_back_to_the_sky_limited_form() -> None:
    """The factor uses electrons, or the sky-limited form with none known."""
    factors = _folded_group(120.0).frame_noise_factors
    assert factors[:8] == pytest.approx((1.0,) * 8)
    assert factors[8] == pytest.approx(0.4 * math.sqrt(612.25 / 252.25))
    sky_limited = _folded_group(120.0, sky=0.0, read=0.0).frame_noise_factors
    assert sky_limited[8] == pytest.approx(0.4 * math.sqrt(300.0 / 120.0))
    assert _folded_group(None).frame_noise_factors == (1.0,) * 9


def test_an_unfolded_group_gives_the_same_numbers_with_or_without_a_reference() -> None:
    """A group of one length ignores a reference exposure (to 1e-12)."""
    plain = [_group(30.0, 8, 20.0, gain=0.9), _group(60.0, 10, 30.0, gain=1.1)]
    referenced = [
        group.model_copy(update={"reference_exposure_seconds": group.frames[0].exposure_seconds})
        for group in plain
    ]
    assert ng.forecast_group_noise(referenced).variance == pytest.approx(
        ng.forecast_group_noise(plain).variance, rel=1e-12
    )
    assert all(factor == pytest.approx(1.0) for group in referenced for factor in group.frame_noise_factors)


def test_the_folded_group_enters_the_model_agreement_with_the_library_formula() -> None:
    """The model equals the library's ``(a_i k_i / t_g)^2 v_i`` sum.

    This is the hand computation of the library's group noise with the
    per-frame scale. Point it at ``expected_exposure_group_noise`` once the
    library's version with the per-frame scale has landed.
    """
    groups = [
        _group(30.0, 8, 20.0, gain=0.9),
        _folded_group(300.0).model_copy(update={"frame_noise_adu": 40.0}),
    ]
    coefficients = ng.group_coefficients(groups)
    assert coefficients.raw_weights is not None and coefficients.total_group_weight is not None
    frames = [frame for group in groups for frame in group.frames]
    scales = [
        (group.reference_exposure_seconds or frame.exposure_seconds) / frame.exposure_seconds
        for group in groups
        for frame in group.frames
    ]
    exposures = [group.mean_exposure_seconds for group in groups for _ in group.frames]
    variance = 0.0
    for raw, gain, frame, scale, group_exposure in zip(
        coefficients.raw_weights, coefficients.gains, frames, scales, exposures, strict=True
    ):
        coefficient = raw / (coefficients.total_group_weight * gain)
        variance += (coefficient * scale / group_exposure) ** 2 * (
            frame.sky_rate_e_per_s_per_px * frame.exposure_seconds + frame.read_noise_e**2
        )
    t_bar = sum(frame.exposure_seconds for frame in frames) / len(frames)
    assert ng.forecast_group_noise(groups).variance == pytest.approx(variance * t_bar**2, rel=1e-12)


def test_new_frames_folded_into_a_group_are_forecast_with_their_scaling() -> None:
    """Three 200 s frames folded into the 120 s group are scaled to it."""
    existing = [_group(60.0, 10, 30.0), _group(120.0, 10, 45.0)]
    new = [_frame(100 + i, exposure=200.0, sky=2.0, read=3.5, width=3.0) for i in range(3)]
    plan = ng.plan_group_stack(existing, new)
    assert plan.folded_group_lengths == [120.0]
    assert plan.forecast.noise is not None
    group = plan.groups[1]
    assert group.reference_exposure_seconds == pytest.approx(120.0)
    assert group.frame_noise_factors[-1] == pytest.approx(0.6 * math.sqrt((400 + 12.25) / (240 + 12.25)))
    assert group.frame_noise_factors[0] == pytest.approx(1.0)
    # Frames of the group's own length give a different forecast, so the
    # folded frames' scaling matters.
    same_length = ng.plan_group_stack(
        existing, [_frame(100 + i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(3)]
    )
    assert same_length.forecast.noise != pytest.approx(plan.forecast.noise)


def test_a_folded_existing_group_with_no_reference_refuses_new_frames_for_it() -> None:
    """A mixed-length group with no known reference takes no new frames."""
    existing = [_group(60.0, 10, 30.0), _folded_group(None)]
    new = [_frame(100 + i, exposure=120.0, sky=2.0, read=3.5, width=3.0) for i in range(2)]
    plan = ng.plan_group_stack(existing, new)
    assert plan.forecast.noise is None
    assert "reference frame's exposure is not known" in str(plan.reason)


def test_take_more_does_not_say_a_folded_group_records_no_weights() -> None:
    """The old flag text is gone; the answer is still given."""
    existing = [_group(60.0, 10, 30.0), _group(120.0, 10, 45.0)]
    template = [_frame(i, exposure=200.0, sky=2.0, read=3.5, width=3.0) for i in range(4)]
    result = ng.frames_to_cut_noise_in_groups(existing, template, new_exposure_seconds=200.0)
    assert "records no weights" not in (result.reason or "")
    assert result.frames_needed is not None


def test_a_folded_group_agrees_with_the_library_noise_model() -> None:
    """The rate-unit model matches the library's stack-unit model, folded case.

    One stack holds a 60 s group and a 120 s group that has one folded 300 s
    frame. The library takes the frame's scale ``k = t_ref / t`` in
    ``FrameNoiseInput.exposure_scale``. Both models must give the same noise
    ratio between two stacks and differ by one constant in absolute value.
    """
    from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
        ExposureGroupNoiseFacts,
        FrameNoiseInput,
        effective_aperture_area_px,
        expected_exposure_group_noise,
    )

    def both(extra_sixty: int, reference: float) -> tuple[float, float]:
        """Give the rate-unit and stack-unit noise of one stack.

        Parameters
        ----------
        extra_sixty : `int`
            Frames in the 60 s group.
        reference : `float`
            The exposure of the folded group's reference frame.

        Returns
        -------
        noises : `tuple` [`float`, `float`]
            The model's noise times ``t_bar``, and the library's noise.
        """
        folded = _folded_group(reference).model_copy(update={"in_group_weights": None})
        folded = folded.model_copy(
            update={
                "frames": tuple(
                    frame.model_copy(update={"star_width": 3.0 + 0.1 * (index % 4)})
                    for index, frame in enumerate(folded.frames)
                )
            }
        )
        groups = [_group(60.0, extra_sixty, 30.0, gain=0.9), folded]
        coefficients = ng.group_coefficients(groups)
        assert coefficients.raw_weights is not None
        frames = [frame for group in groups for frame in group.frames]
        model = ng.forecast_group_noise(groups)
        scales = [
            (group.reference_exposure_seconds or frame.exposure_seconds) / frame.exposure_seconds
            for group in groups
            for frame in group.frames
        ]
        inputs = [
            FrameNoiseInput(
                weight=raw,
                sky_electrons=frame.sky_rate_e_per_s_per_px * frame.exposure_seconds,
                read_noise_electrons=frame.read_noise_e,
                gain_electrons_per_adu=CAMERA_GAIN,
                exposure_scale=scale,
            )
            for raw, frame, scale in zip(coefficients.raw_weights, frames, scales, strict=True)
        ]
        indices = [position for position, group in enumerate(groups) for _ in group.frames]
        facts = [
            ExposureGroupNoiseFacts(
                combine_weight=len(group.frames) * group.mean_exposure_seconds**2 / group.frame_noise_adu**2,
                exposure_seconds=group.mean_exposure_seconds,
                frame_count=len(group.frames),
                gain=group.gain,
                gain_measured=True,
                sky_level_per_second=1.0,
            )
            for group in groups
        ]
        outcome = expected_exposure_group_noise(inputs, indices, facts)
        assert outcome.result is not None
        assert model.noise is not None
        return model.noise, outcome.result.total_noise

    for reference in (120.0, 300.0):
        model_a, library_a = both(10, reference)
        model_b, library_b = both(20, reference)
        assert model_b / model_a == pytest.approx(library_b / library_a, rel=1e-9)
        assert model_b / model_a != pytest.approx(1.0, abs=0.01)
    constant = math.sqrt(effective_aperture_area_px()) / 65535.0 / CAMERA_GAIN
    assert library_a / model_a == pytest.approx(constant, rel=1e-9)


def test_a_single_group_of_mixed_lengths_needs_no_per_frame_factor() -> None:
    """Scaling each frame by ``k = t_ref / t`` only rescales the whole stack.

    In a stack that is one group, Siril multiplies frame ``i`` by ``k_i``, so
    its counts become ``t_ref`` times its rate. The stack is then ``t_ref``
    times
    the rate stack, for the same weights. The noise ratio between two stacks,
    and the normalized noise (taken over the stars' flux, which scales alike),
    do not change. Checked with explicit factors for 21 x 30 s and one 300 s
    frame, for several reference exposures.
    """
    frames = [_frame(i, exposure=30.0, sky=1.5, read=3.0, width=3.0 + 0.05 * (i % 5)) for i in range(21)]
    frames.append(_frame(21, exposure=300.0, sky=1.5, read=3.0, width=3.1))
    weights = ng.frame_weights(frames, WeightingMode.WFWHM)
    assert weights is not None
    plain = ng.forecast_stack_noise(frames, WeightingMode.WFWHM)
    assert plain.variance is not None
    for reference in (30.0, 300.0):
        scales = [reference / frame.exposure_seconds for frame in frames]
        counts_variance = (
            sum(
                (w * k) ** 2 * frame.rate_variance * frame.exposure_seconds**2
                for w, k, frame in zip(weights, scales, frames, strict=True)
            )
            / sum(weights) ** 2
        )
        # The scaled stack is in counts of the reference exposure; divide by
        # it to get back to rate units.
        scaled = counts_variance / reference**2
        assert scaled == pytest.approx(plain.variance, rel=1e-12)
