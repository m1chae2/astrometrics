"""Tests for the judgement of dark and bias sets.

The stored numbers are written by hand, one frame at a time, so each check can
be driven past its limit on its own. The limits are those of the validation
register entries STK-47 to STK-53.
"""

import pytest

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    SensorSettingNoise,
    SettingBiasLevel,
    ValueProvenance,
)
from astrometricslib.models.quality.gate_result import GateResult, GateStatus
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BLOCK_RANGE,
    FRAME_CLIPPED_MEAN_LEVEL,
    FRAME_NEIGHBOR_NOISE,
    FRAME_PIXEL_CHECKSUM,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_VALUE_STEP,
    FRAME_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.stacking.pre_processing.calibration_assessment import (
    BIAS_LEVEL_CHECK_NAME,
    BIAS_NOISE_CHECK_NAME,
    BIAS_ZERO_PIXEL_CHECK_NAME,
    CALIBRATION_FRAME_COUNT_CHECK_NAME,
    DARK_CEILING_SHARE_CHECK_NAME,
    DARK_LEVEL_CHECK_NAME,
    DARK_LIGHT_LEAK_CHECK_NAME,
    DARK_NOISE_CHECK_NAME,
    BiasReference,
    FrameFacts,
    SetJudgement,
    assess_dark_or_bias_frames,
    build_bias_reference,
    combine_status,
    summarize_checks,
)

SOURCE = ValueProvenance(kind=ProvenanceKind.MEASURED, source="test")
BIAS_LEVEL = 400.0
STEP = 4.0
READ_NOISE_ADU = 4.8
GAIN = 0.8
SETTING = ("0", "10", "1x1")
MINIMUM_FRAMES = 3


def _profile(*, with_bias_level: bool = True, with_noise: bool = True) -> CameraProfile:
    """Build a profile with a bias level and a read noise for the setting.

    Returns
    -------
    profile : `CameraProfile`
        The profile.
    """
    datasheet = ValueProvenance(kind=ProvenanceKind.DATASHEET, source="test")
    noise = (
        (
            SensorSettingNoise(
                gain_setting="0",
                offset="10",
                binning="1x1",
                gain_e_per_adu=ProvenancedValue(value=GAIN, provenance=SOURCE),
                read_noise_e=ProvenancedValue(value=READ_NOISE_ADU * GAIN, provenance=SOURCE),
            ),
        )
        if with_noise
        else ()
    )
    levels = (
        (
            SettingBiasLevel(
                gain_setting="0",
                offset="10",
                binning="1x1",
                bias_level_adu=ProvenancedValue(value=BIAS_LEVEL, provenance=SOURCE),
            ),
        )
        if with_bias_level
        else ()
    )
    return CameraProfile(
        camera_name="Test",
        clip_ceiling_adu=ProvenancedValue(value=65535.0, provenance=datasheet),
        saturation_threshold_adu=ProvenancedValue(value=65000.0, provenance=datasheet),
        sensor_noise=noise,
        bias_levels=levels,
    )


def _frame(index: int, **overrides: float) -> FrameFacts:
    """Make the stored numbers of one clean frame, with some overridden.

    Returns
    -------
    facts : `FrameFacts`
        A frame at the bias level with the right noise and no problem.
    """
    values = {
        FRAME_CLIPPED_MEAN_LEVEL: BIAS_LEVEL,
        FRAME_ZERO_PIXEL_FRACTION: 0.0,
        FRAME_SATURATED_PIXEL_FRACTION: 0.0,
        FRAME_NEIGHBOR_NOISE: READ_NOISE_ADU,
        FRAME_BLOCK_RANGE: 0.0,
        FRAME_VALUE_STEP: STEP,
        FRAME_PIXEL_CHECKSUM: float(1000 + index),
    }
    values.update(overrides)
    return FrameFacts(f"frame_{index}.fits", values)


def _facts(frames: list[FrameFacts]) -> dict[str, FrameFacts]:
    """Index frames by path.

    Returns
    -------
    facts : `dict`
        Path to facts.
    """
    return {frame.path: frame for frame in frames}


def _judge(kind: str, frames: list[FrameFacts], **options: object) -> SetJudgement:
    """Judge a set of frames with the test profile.

    Returns
    -------
    judgement : `SetJudgement`
        The result.
    """
    reference = options.pop("reference", None)
    return assess_dark_or_bias_frames(
        kind,
        [frame.path for frame in frames],
        _facts(frames),
        minimum_frames=MINIMUM_FRAMES,
        profile=options.pop("profile", _profile()),
        setting=SETTING,
        exposure_seconds=options.pop("exposure_seconds", 30.0),
        reference=reference,
    )


def _check(judgement: SetJudgement, name: str) -> GateResult:
    """Pick one check by name.

    Returns
    -------
    check : `GateResult`
        The check.
    """
    return next(check for check in judgement.checks if check.name == name)


def _bias_reference(**overrides: float) -> BiasReference | None:
    """Build the reference from twenty clean bias frames.

    Returns
    -------
    reference : `BiasReference`
        The reference.
    """
    return build_bias_reference([_frame(index, **overrides) for index in range(20)])


def test_a_clean_bias_set_passes_every_check() -> None:
    """Twenty clean frames at the profile's level and noise pass."""
    judgement = _judge("bias", [_frame(index) for index in range(20)])
    assert {check.name: check.status for check in judgement.checks} == {
        CALIBRATION_FRAME_COUNT_CHECK_NAME: GateStatus.PASSED,
        BIAS_ZERO_PIXEL_CHECK_NAME: GateStatus.PASSED,
        BIAS_LEVEL_CHECK_NAME: GateStatus.PASSED,
        BIAS_NOISE_CHECK_NAME: GateStatus.PASSED,
    }
    assert combine_status(judgement.checks) == "good"
    assert judgement.unique_frame_count == 20


def test_a_zero_clipped_set_fails_on_zero_share_noise_and_level() -> None:
    """Offset-0 style frames: mostly zero, little noise, level near zero."""
    frames = [
        _frame(
            index,
            **{FRAME_ZERO_PIXEL_FRACTION: 0.96, FRAME_CLIPPED_MEAN_LEVEL: 0.25, FRAME_NEIGHBOR_NOISE: 1.6},
        )
        for index in range(20)
    ]
    judgement = _judge("bias", frames)
    assert _check(judgement, BIAS_ZERO_PIXEL_CHECK_NAME).status is GateStatus.FAILED
    assert _check(judgement, BIAS_NOISE_CHECK_NAME).status is GateStatus.FAILED
    assert _check(judgement, BIAS_LEVEL_CHECK_NAME).status is GateStatus.FAILED
    assert combine_status(judgement.checks) == "warning"
    assert len(judgement.concerns) == 20


def test_the_zero_share_limit_is_one_tenth_of_a_percent() -> None:
    """Just under the limit passes, at the limit fails."""
    below = _judge("bias", [_frame(i, **{FRAME_ZERO_PIXEL_FRACTION: 0.0009}) for i in range(5)])
    at_limit = _judge("bias", [_frame(i, **{FRAME_ZERO_PIXEL_FRACTION: 0.001}) for i in range(5)])
    assert _check(below, BIAS_ZERO_PIXEL_CHECK_NAME).status is GateStatus.PASSED
    assert _check(at_limit, BIAS_ZERO_PIXEL_CHECK_NAME).status is GateStatus.FAILED


def test_the_bias_level_limit_is_two_value_steps() -> None:
    """A level 2 steps off passes, 3 steps off fails."""
    two = _judge("bias", [_frame(i, **{FRAME_CLIPPED_MEAN_LEVEL: BIAS_LEVEL + 2 * STEP}) for i in range(5)])
    three = _judge("bias", [_frame(i, **{FRAME_CLIPPED_MEAN_LEVEL: BIAS_LEVEL + 3 * STEP}) for i in range(5)])
    assert _check(two, BIAS_LEVEL_CHECK_NAME).status is GateStatus.PASSED
    assert _check(three, BIAS_LEVEL_CHECK_NAME).status is GateStatus.FAILED


def test_a_camera_with_no_bias_level_is_not_checked_not_passed() -> None:
    """With no stored level the check says so and gives the reason."""
    judgement = _judge("bias", [_frame(i) for i in range(5)], profile=_profile(with_bias_level=False))
    check = _check(judgement, BIAS_LEVEL_CHECK_NAME)
    assert check.status is GateStatus.NOT_CHECKED
    assert "no bias level" in check.detail


def test_a_camera_with_no_read_noise_is_not_checked() -> None:
    """With no read noise in the profile the noise checks cannot run."""
    judgement = _judge("bias", [_frame(i) for i in range(5)], profile=_profile(with_noise=False))
    assert _check(judgement, BIAS_NOISE_CHECK_NAME).status is GateStatus.NOT_CHECKED


@pytest.mark.parametrize(
    ("ratio", "expected"), [(0.95, GateStatus.PASSED), (0.85, GateStatus.FAILED), (1.15, GateStatus.FAILED)]
)
def test_the_bias_noise_band_is_nine_to_eleven_tenths(ratio: float, expected: GateStatus) -> None:
    """Noise outside 0.9 to 1.1 of the profile's read noise fails."""
    judgement = _judge(
        "bias", [_frame(i, **{FRAME_NEIGHBOR_NOISE: READ_NOISE_ADU * ratio}) for i in range(5)]
    )
    assert _check(judgement, BIAS_NOISE_CHECK_NAME).status is expected


def test_dark_noise_has_a_lower_limit_only() -> None:
    """Hot pixels raise a dark's noise, so only a low noise fails."""
    high = _judge(
        "dark",
        [_frame(i, **{FRAME_NEIGHBOR_NOISE: READ_NOISE_ADU * 1.6}) for i in range(5)],
        reference=_bias_reference(),
    )
    low = _judge(
        "dark",
        [_frame(i, **{FRAME_NEIGHBOR_NOISE: READ_NOISE_ADU * 0.5}) for i in range(5)],
        reference=_bias_reference(),
    )
    assert _check(high, DARK_NOISE_CHECK_NAME).status is GateStatus.PASSED
    assert _check(low, DARK_NOISE_CHECK_NAME).status is GateStatus.FAILED


def test_a_light_struck_dark_is_found_against_the_bias_range() -> None:
    """One dark with a block range far above the bias range is singled out."""
    reference = _bias_reference(**{FRAME_BLOCK_RANGE: 6.0})
    frames = [_frame(i, **{FRAME_BLOCK_RANGE: 6.0}) for i in range(19)]
    frames.append(_frame(19, **{FRAME_BLOCK_RANGE: 30.0}))
    judgement = _judge("dark", frames, reference=reference)
    check = _check(judgement, DARK_LIGHT_LEAK_CHECK_NAME)
    assert check.status is GateStatus.FAILED
    assert check.measured_value == pytest.approx((30.0 - 6.0) / STEP)
    assert [concern.path for concern in judgement.concerns] == ["frame_19.fits"]


def test_the_light_leak_limit_is_two_steps_above_the_bias_range() -> None:
    """Bias range 6 and step 4 allow a dark range of 14."""
    reference = _bias_reference(**{FRAME_BLOCK_RANGE: 6.0})
    ok = _judge("dark", [_frame(i, **{FRAME_BLOCK_RANGE: 14.0}) for i in range(5)], reference=reference)
    over = _judge("dark", [_frame(i, **{FRAME_BLOCK_RANGE: 15.0}) for i in range(5)], reference=reference)
    assert _check(ok, DARK_LIGHT_LEAK_CHECK_NAME).status is GateStatus.PASSED
    assert _check(over, DARK_LIGHT_LEAK_CHECK_NAME).status is GateStatus.FAILED


def test_a_dark_with_no_bias_frames_at_its_setting_is_not_checked() -> None:
    """The light leak and level checks need a bias reference."""
    judgement = _judge("dark", [_frame(i) for i in range(5)], reference=None)
    for name in (DARK_LIGHT_LEAK_CHECK_NAME, DARK_LEVEL_CHECK_NAME):
        check = _check(judgement, name)
        assert check.status is GateStatus.NOT_CHECKED
        assert "no measured bias frames" in check.detail


def test_a_saturated_dark_fails_the_ceiling_share() -> None:
    """A dark with 35 percent of its pixels saturated was struck by light."""
    frames = [_frame(i) for i in range(9)] + [_frame(9, **{FRAME_SATURATED_PIXEL_FRACTION: 0.35})]
    judgement = _judge("dark", frames, reference=_bias_reference())
    assert _check(judgement, DARK_CEILING_SHARE_CHECK_NAME).status is GateStatus.FAILED


def test_a_dark_far_from_the_bias_level_fails_the_level_check() -> None:
    """A dark already calibrated (level near zero) is found."""
    frames = [_frame(i, **{FRAME_CLIPPED_MEAN_LEVEL: 3.0}) for i in range(5)]
    judgement = _judge("dark", frames, reference=_bias_reference())
    assert _check(judgement, DARK_LEVEL_CHECK_NAME).status is GateStatus.FAILED


def test_the_dark_current_allowance_grows_with_the_exposure() -> None:
    """A long exposure is allowed a larger level difference above the bias."""
    frames = [_frame(i, **{FRAME_CLIPPED_MEAN_LEVEL: BIAS_LEVEL + 2 * STEP + 1.0}) for i in range(5)]
    short = _judge("dark", frames, reference=_bias_reference(), exposure_seconds=1.0)
    long = _judge("dark", frames, reference=_bias_reference(), exposure_seconds=600.0)
    assert _check(short, DARK_LEVEL_CHECK_NAME).status is GateStatus.FAILED
    assert _check(long, DARK_LEVEL_CHECK_NAME).status is GateStatus.PASSED


@pytest.mark.parametrize(
    ("count", "expected"), [(2, GateStatus.FAILED), (9, GateStatus.FAILED), (10, GateStatus.PASSED)]
)
def test_the_frame_count_limits_are_three_and_ten(count: int, expected: GateStatus) -> None:
    """Under the minimum or under ten unique frames fails; ten passes."""
    judgement = _judge("bias", [_frame(i) for i in range(count)])
    assert _check(judgement, CALIBRATION_FRAME_COUNT_CHECK_NAME).status is expected


def test_copies_are_counted_once_and_named() -> None:
    """Twenty files of ten different frames fail, and the copies are listed."""
    frames = [_frame(i % 10) for i in range(20)]
    frames = [FrameFacts(f"copy_{i}.fits", frame.values) for i, frame in enumerate(frames)]
    judgement = _judge("bias", frames)
    check = _check(judgement, CALIBRATION_FRAME_COUNT_CHECK_NAME)
    assert judgement.unique_frame_count == 10
    assert judgement.frame_count == 20
    assert check.status is GateStatus.FAILED
    assert "copies" in check.detail
    assert len(judgement.concerns) == 10


def test_unmeasured_frames_leave_every_other_check_not_checked() -> None:
    """With no stored numbers the count still runs and the rest say why not."""
    judgement = assess_dark_or_bias_frames(
        "dark",
        [f"missing_{i}.fits" for i in range(12)],
        {},
        minimum_frames=MINIMUM_FRAMES,
        profile=_profile(),
        setting=SETTING,
    )
    statuses = {check.name: check.status for check in judgement.checks}
    assert statuses[CALIBRATION_FRAME_COUNT_CHECK_NAME] is GateStatus.PASSED
    assert all(
        status is GateStatus.NOT_CHECKED
        for name, status in statuses.items()
        if name != CALIBRATION_FRAME_COUNT_CHECK_NAME
    )
    assert judgement.measured_frame_count == 0
    assert "measuring job" in summarize_checks(judgement.checks[1:])
