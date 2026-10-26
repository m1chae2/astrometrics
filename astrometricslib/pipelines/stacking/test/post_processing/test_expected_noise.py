"""Tests for the noise a stack's frames predict in an empty aperture.

A synthetic stack is built from frames with a known gain, read noise, sky and
weights. The spread of its empty-aperture sums, measured the way the pipeline
measures them, must match the expected noise within a stated tolerance. The
tests also check the paths where a value is missing: those give a reason and
never a default.
"""

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.camera_profile import ProvenancedValue, ProvenanceKind, ValueProvenance
from astrometricslib.models.quality.measurement import (
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    NO_FRAME_ROWS_REASON,
    STACK_UNITS_PER_ADU,
    ExposureGroupNoiseFacts,
    FrameNoiseInput,
    effective_aperture_area_px,
    expected_exposure_group_noise,
    expected_stack_noise,
    gather_frame_noise_inputs,
    measure_stack_sky_level,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import (
    MAD_TO_SIGMA,
    signed_aperture_sum,
    sky_level_of_plane,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import FULL_SCALE_COUNTS

GAIN = 0.8
READ_NOISE = 3.8
SKY_ELECTRONS = 400.0

# How close the simulated spread must come to the expected noise. The spread
# of about 1500 apertures has a relative error of 1.17 / sqrt(1500) = 3%, and
# the model treats the median of the ring as a bell-curve median; 8% leaves
# room for both without hiding a missing factor (the ring term alone is 10%).
TOLERANCE = 0.08


def _provenanced(value: float, kind: ProvenanceKind = ProvenanceKind.MEASURED) -> ProvenancedValue:
    """Build a camera profile number with a provenance.

    Parameters
    ----------
    value : `float`
        The number.
    kind : `ProvenanceKind`, optional
        How it was found.

    Returns
    -------
    number : `ProvenancedValue`
        The number with its provenance.
    """
    return ProvenancedValue(value=value, provenance=ValueProvenance(kind=kind, source="test"))


def _simulated_spread(weights: list[float], sky_electrons: list[float], seed: int) -> float:
    """Stack simulated frames and measure the spread of empty-aperture sums.

    Parameters
    ----------
    weights : `list` [`float`]
        The frames' weights.
    sky_electrons : `list` [`float`]
        Each frame's sky, in electrons per pixel.
    seed : `int`
        The random seed.

    Returns
    -------
    spread : `float`
        1.4826 times the median absolute deviation of the sky-subtracted
        sums, in the stack's units (ADU divided by 65535).
    """
    generator = np.random.default_rng(seed)
    size = 900
    total = np.zeros((size, size))
    for weight, sky in zip(weights, sky_electrons, strict=True):
        electrons = generator.poisson(sky, (size, size)) + generator.normal(0.0, READ_NOISE, (size, size))
        total += weight * electrons / GAIN
    stack = total / sum(weights) * STACK_UNITS_PER_ADU
    sums = [
        signed_aperture_sum(stack, float(column), float(row))
        for column in range(14, size - 14, 24)
        for row in range(14, size - 14, 24)
    ]
    values = np.array([value for value in sums if value is not None])
    assert values.size > 1000
    return MAD_TO_SIGMA * float(np.median(np.abs(values - np.median(values))))


def test_expected_noise_is_recovered_from_a_simulated_stack() -> None:
    """The expected noise matches the spread of a simulated stack's sums."""
    weights = [1.0, 1.0, 2.0, 0.5, 1.5, 1.0]
    skies = [SKY_ELECTRONS] * len(weights)
    inputs = [
        FrameNoiseInput(weight, sky, READ_NOISE, GAIN) for weight, sky in zip(weights, skies, strict=True)
    ]
    outcome = expected_stack_noise(inputs)
    assert outcome.result is not None
    simulated = _simulated_spread(weights, skies, seed=11)
    assert simulated == pytest.approx(outcome.result.total_noise, rel=TOLERANCE)


def test_the_sky_and_read_parts_add_in_quadrature_and_follow_their_inputs() -> None:
    """The two parts are separate, and each scales with its own input only."""
    weights = [1.0, 3.0]
    base = expected_stack_noise([FrameNoiseInput(w, 100.0, 4.0, 1.0) for w in weights]).result
    more_sky = expected_stack_noise([FrameNoiseInput(w, 400.0, 4.0, 1.0) for w in weights]).result
    more_read = expected_stack_noise([FrameNoiseInput(w, 100.0, 8.0, 1.0) for w in weights]).result
    assert base is not None and more_sky is not None and more_read is not None
    assert base.total_noise == pytest.approx(math.hypot(base.sky_noise, base.read_noise))
    assert more_sky.sky_noise == pytest.approx(2.0 * base.sky_noise)
    assert more_sky.read_noise == pytest.approx(base.read_noise)
    assert more_read.read_noise == pytest.approx(2.0 * base.read_noise)
    assert more_read.sky_noise == pytest.approx(base.sky_noise)


def test_equal_weights_give_the_one_over_root_n_rule() -> None:
    """Four equal frames halve the noise of one."""
    one = expected_stack_noise([FrameNoiseInput(1.0, 250.0, 3.0, 0.9)]).result
    four = expected_stack_noise([FrameNoiseInput(1.0, 250.0, 3.0, 0.9)] * 4).result
    assert one is not None and four is not None
    assert four.total_noise == pytest.approx(one.total_noise / 2.0)


def test_the_aperture_area_holds_the_circle_and_the_ring() -> None:
    """The multiplier is the circle's squared overlaps plus the ring noise."""
    area = effective_aperture_area_px()
    circle_area = math.pi * 4.0**2
    assert 0.85 * circle_area < area < 1.35 * circle_area


def test_no_frames_and_bad_weights_give_a_reason() -> None:
    """An empty stack or weights that add to nothing are reported."""
    assert expected_stack_noise([]).result is None
    assert "no member frames" in expected_stack_noise([]).reason
    assert expected_stack_noise([FrameNoiseInput(0.0, 100.0, 3.0, 1.0)]).result is None
    assert expected_stack_noise([FrameNoiseInput(1.0, 100.0, 3.0, 0.0)]).result is None


def test_the_unit_scale_is_the_one_shared_full_scale_count() -> None:
    """The expected noise and the exposure-group combine use one constant."""
    assert pytest.approx(1.0 / FULL_SCALE_COUNTS) == STACK_UNITS_PER_ADU
    result = expected_stack_noise([FrameNoiseInput(1.0, 100.0, 3.0, 1.0)]).result
    assert result is not None
    assert result.stack_units_per_adu == STACK_UNITS_PER_ADU


def test_the_predicted_sky_level_is_the_weighted_mean_sky_in_stack_units() -> None:
    """Two frames of 200 and 400 ADU, weighted 1 and 3, predict 350 ADU."""
    result = expected_stack_noise([
        FrameNoiseInput(1.0, 160.0, 3.0, 0.8),
        FrameNoiseInput(3.0, 320.0, 3.0, 0.8),
    ]).result
    assert result is not None
    assert result.predicted_sky_level == pytest.approx(350.0 * STACK_UNITS_PER_ADU)


def test_assumed_inputs_are_named_in_the_result() -> None:
    """A frame with an assumed input makes the result carry the reason."""
    outcome = expected_stack_noise([
        FrameNoiseInput(1.0, 100.0, 3.0, 1.0, assumed_reason="gain is a design estimate")
    ])
    assert outcome.result is not None
    assert outcome.result.assumed_reason == "gain is a design estimate"


def _sky_row(
    subject_id: str, value: float | None, status: MeasurementStatus = MeasurementStatus.MEASURED
) -> QualityMeasurement:
    """Build a stored sky rate row.

    Parameters
    ----------
    subject_id : `str`
        The frame's subject id.
    value : `float` or `None`
        The sky rate, or `None` for a row that was not measured.
    status : `MeasurementStatus`, optional
        The row's status when it has a value.

    Returns
    -------
    row : `QualityMeasurement`
        A frame-level row of ``frame.sky_rate_e_per_s_per_px``.
    """
    from datetime import UTC, datetime

    return QualityMeasurement(
        metric="frame.sky_rate_e_per_s_per_px",
        definitionVersion=1,
        subject=QualitySubject(level=SubjectLevel.FRAME, subjectId=subject_id, targetId="T"),
        status=MeasurementStatus.NOT_MEASURED if value is None else status,
        value=value,
        reason="no bias level" if value is None or status == MeasurementStatus.ASSUMED else "",
        origin="measured",
        pipelineVersion="1",
        runId="r",
        measuredAt=datetime(2026, 10, 1, tzinfo=UTC),
    )


def _members() -> list[SimpleNamespace]:
    """Build two member frames of 60 s each.

    Returns
    -------
    members : `list` [`SimpleNamespace`]
        Frames with a path and an exposure.
    """
    return [SimpleNamespace(path=f"f{index}", exposure="60") for index in range(2)]


def _setting(_frame: object) -> tuple[ProvenancedValue, ProvenancedValue, str]:
    """Give a measured gain and read noise for any frame.

    Parameters
    ----------
    _frame : `object`
        Unused.

    Returns
    -------
    setting : `tuple`
        The gain, the read noise and an empty reason.
    """
    return _provenanced(GAIN), _provenanced(READ_NOISE), ""


def test_gathering_turns_rates_into_electrons_per_frame() -> None:
    """The sky of a frame is its stored rate times its exposure."""
    members = _members()
    rows = {"f0": _sky_row("f0", 2.0), "f1": _sky_row("f1", 3.0)}
    inputs = gather_frame_noise_inputs(members, [1.0, 1.0], rows, lambda frame: frame.path, _setting)
    assert isinstance(inputs, list)
    assert [item.sky_electrons for item in inputs] == [120.0, 180.0]
    assert all(item.assumed_reason == "" for item in inputs)


def test_gathering_carries_the_exposure_scales_and_defaults_to_one() -> None:
    """Each frame gets its scale; without scales every frame gets 1."""
    members = _members()
    rows = {"f0": _sky_row("f0", 2.0), "f1": _sky_row("f1", 3.0)}
    scaled = gather_frame_noise_inputs(
        members, [1.0, 1.0], rows, lambda frame: frame.path, _setting, exposure_scales=[1.0, 0.4]
    )
    default = gather_frame_noise_inputs(members, [1.0, 1.0], rows, lambda frame: frame.path, _setting)
    assert isinstance(scaled, list) and isinstance(default, list)
    assert [item.exposure_scale for item in scaled] == [1.0, 0.4]
    assert [item.exposure_scale for item in default] == [1.0, 1.0]
    mismatch = gather_frame_noise_inputs(
        members, [1.0, 1.0], rows, lambda frame: frame.path, _setting, exposure_scales=[1.0]
    )
    assert not isinstance(mismatch, list) and "exposure scales" in mismatch.reason


def test_gathering_reports_every_missing_input_without_a_default() -> None:
    """A missing weight, row, rate, exposure or gain gives a reason."""
    members = _members()
    rows = {"f0": _sky_row("f0", 2.0), "f1": _sky_row("f1", 3.0)}
    unknown_weights = gather_frame_noise_inputs(
        members, [1.0, None], rows, lambda f: f.path, _setting, "wfwhm needs..."
    )
    assert unknown_weights.result is None
    assert unknown_weights.reason == "wfwhm needs..."
    missing_row = gather_frame_noise_inputs(
        members, [1.0, 1.0], {"f0": rows["f0"]}, lambda f: f.path, _setting
    )
    assert missing_row.reason == NO_FRAME_ROWS_REASON
    no_rate = gather_frame_noise_inputs(
        members, [1.0, 1.0], {"f0": rows["f0"], "f1": _sky_row("f1", None)}, lambda f: f.path, _setting
    )
    assert "not measured" in no_rate.reason
    no_exposure = gather_frame_noise_inputs(
        [SimpleNamespace(path="f0", exposure="")], [1.0], {"f0": rows["f0"]}, lambda f: f.path, _setting
    )
    assert "exposure" in no_exposure.reason
    no_gain = gather_frame_noise_inputs(
        members,
        [1.0, 1.0],
        rows,
        lambda f: f.path,
        lambda _f: (None, None, "profile has no entry for gain 5"),
    )
    assert no_gain.reason == "profile has no entry for gain 5"


def test_gathering_marks_unmeasured_gain_and_assumed_sky_as_assumed() -> None:
    """An assumed sky row or an assumed gain is carried into the input."""
    members = _members()[:1]
    rows = {"f0": _sky_row("f0", 2.0, MeasurementStatus.ASSUMED)}
    inputs = gather_frame_noise_inputs(
        members,
        [1.0],
        rows,
        lambda f: f.path,
        lambda _f: (_provenanced(GAIN, ProvenanceKind.ASSUMED), _provenanced(READ_NOISE), ""),
    )
    assert isinstance(inputs, list)
    assert "no bias level" in inputs[0].assumed_reason
    assert "gain" in inputs[0].assumed_reason


def test_the_stack_sky_level_is_the_median_of_the_nonzero_pixels(
    tmp_path: Path,
) -> None:
    """The empty zero edge of a stack does not pull the level down."""
    from astropy.io import fits

    plane = np.full((100, 100), 0.006, dtype=np.float32)
    plane[:, :30] = 0.0
    plane[50, 50] = 1.0
    path = tmp_path / "stack.fits"
    fits.PrimaryHDU(plane).writeto(path)
    assert measure_stack_sky_level(str(path)) == pytest.approx(0.006, rel=1e-6)
    empty = tmp_path / "empty.fits"
    fits.PrimaryHDU(np.zeros((10, 10), dtype=np.float32)).writeto(empty)
    assert measure_stack_sky_level(str(empty)) is None


def test_the_sky_level_of_a_plane_matches_the_file_reader_on_a_smooth_sky(tmp_path: Path) -> None:
    """The plane function and the sampled file reader agree on a smooth sky."""
    from astropy.io import fits

    generator = np.random.default_rng(3)
    plane = (0.006 + generator.normal(0.0, 1e-4, (200, 200))).astype(np.float32)
    plane[:, :20] = 0.0
    path = tmp_path / "stack.fits"
    fits.PrimaryHDU(plane).writeto(path)
    assert sky_level_of_plane(plane) == pytest.approx(0.006, rel=2e-3)
    assert measure_stack_sky_level(str(path)) == pytest.approx(0.006, rel=2e-3)
    assert sky_level_of_plane(np.zeros((5, 5))) is None
    assert sky_level_of_plane(np.full((5, 5), np.nan)) is None


def _group_facts(
    weight: float, exposure: float, count: int, gain: float, sky_per_second: float, measured: bool = True
) -> ExposureGroupNoiseFacts:
    """Build the facts of one exposure group.

    Parameters
    ----------
    weight, exposure, count, gain, sky_per_second : `float`
        The group's combine weight, exposure, frame count, applied gain and
        sky per second.
    measured : `bool`, optional
        Whether the gain was measured.

    Returns
    -------
    facts : `ExposureGroupNoiseFacts`
        The group's facts.
    """
    return ExposureGroupNoiseFacts(weight, exposure, count, gain, measured, sky_per_second)


def _frame(raw_weight: float, sky: float, read: float, gain: float) -> FrameNoiseInput:
    """Build a frame input with a raw group weight.

    Parameters
    ----------
    raw_weight, sky, read, gain : `float`
        The weight, sky electrons, read noise and gain of the frame.

    Returns
    -------
    frame : `FrameNoiseInput`
        The frame.
    """
    return FrameNoiseInput(raw_weight, sky, read, gain)


def test_one_group_with_gain_one_reduces_to_the_single_group_noise() -> None:
    """With G = 1 and t_bar = t, the group formula is the plain one."""
    frames = [_frame(1.0, 120.0, 3.8, 0.8), _frame(2.5, 100.0, 3.8, 0.8), _frame(0.5, 140.0, 3.8, 0.8)]
    plain = expected_stack_noise(frames)
    group = expected_exposure_group_noise(frames, [0, 0, 0], [_group_facts(9.0, 60.0, 3, 1.0, 1e-4)])
    assert plain.result is not None and group.result is not None
    assert group.result.sky_noise == pytest.approx(plain.result.sky_noise, rel=1e-12)
    assert group.result.read_noise == pytest.approx(plain.result.read_noise, rel=1e-12)
    assert group.result.total_noise == pytest.approx(plain.result.total_noise, rel=1e-12)


def test_two_groups_with_gains_match_the_formula_worked_out_apart() -> None:
    """Gains other than 1 and two exposures: compare with a direct sum."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4), _group_facts(5.0, 120.0, 2, 1.25, 2e-4)]
    frames = [
        _frame(1.0, 60.0, 3.0, 0.9),
        _frame(3.0, 70.0, 3.0, 0.9),
        _frame(2.0, 240.0, 3.0, 0.9),
        _frame(2.0, 250.0, 3.0, 0.9),
    ]
    indices = [0, 0, 1, 1]
    outcome = expected_exposure_group_noise(frames, indices, groups)
    assert outcome.result is not None
    total_weight = 8.0
    mean_exposure = (4 * 30.0 + 2 * 120.0) / 6
    shares = [1 / 4, 3 / 4, 1 / 2, 1 / 2]
    group_weights = [3.0, 3.0, 5.0, 5.0]
    gains = [1.0, 1.0, 1.25, 1.25]
    exposures = [30.0, 30.0, 120.0, 120.0]
    variance = 0.0
    for frame, share, group_weight, gain, exposure in zip(
        frames, shares, group_weights, gains, exposures, strict=True
    ):
        coefficient = group_weight * share / (total_weight * gain)
        frame_variance = (
            frame.sky_electrons + frame.read_noise_electrons**2
        ) / frame.gain_electrons_per_adu**2
        variance += coefficient**2 * frame_variance / exposure**2
    expected = mean_exposure / 65535.0 * math.sqrt(effective_aperture_area_px() * variance)
    assert outcome.result.total_noise == pytest.approx(expected, rel=1e-12)
    assert outcome.result.assumed_reason == ""


def test_the_predicted_sky_is_the_heaviest_groups_sky_times_the_mean_exposure() -> None:
    """The reference group is the one with the largest combine weight."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4), _group_facts(5.0, 120.0, 2, 1.0, 2e-4)]
    frames = [_frame(3.0, 60.0, 3.0, 0.9), _frame(5.0, 240.0, 3.0, 0.9)]
    outcome = expected_exposure_group_noise(frames, [0, 1], groups)
    assert outcome.result is not None
    assert outcome.result.predicted_sky_level == pytest.approx(2e-4 * (4 * 30.0 + 2 * 120.0) / 6)


def test_an_unmeasured_group_gain_uses_the_applied_gain_and_is_marked_assumed() -> None:
    """The combine's gain of 1 is used, and the result says so."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4, measured=False)]
    outcome = expected_exposure_group_noise([_frame(3.0, 60.0, 3.0, 0.9)], [0], groups)
    assert outcome.result is not None
    assert "could not measure the gain" in outcome.result.assumed_reason


def test_group_noise_gives_a_reason_for_unusable_inputs() -> None:
    """Empty groups, unplaced frames and bad numbers each say why."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4), _group_facts(5.0, 120.0, 2, 1.0, 2e-4)]
    frame = _frame(1.0, 60.0, 3.0, 0.9)
    assert expected_exposure_group_noise([], [], groups).result is None
    assert "no member frame" in expected_exposure_group_noise([frame], [0], groups).reason
    assert "no combined" in expected_exposure_group_noise([frame], [5], groups).reason
    assert expected_exposure_group_noise([_frame(1.0, 60.0, 3.0, 0.0)], [0], groups[:1]).result is None
    assert expected_exposure_group_noise([frame], [0], [_group_facts(3.0, 30.0, 4, 0.0, 1e-4)]).result is None


def _folded_group_hand_value(
    frames: list[FrameNoiseInput], scales: list[float], group_weight: float, exposure: float
) -> tuple[float, float]:
    """Work out the sky and read noise of one group by hand, with scales.

    One group, gain 1 and equal shares: the combined image is the weighted mean
    of the frames' counts per second, each frame multiplied by its scale ``k``.

    Parameters
    ----------
    frames : `list` [`FrameNoiseInput`]
        The group's frames, with equal weights.
    scales : `list` [`float`]
        ``k_i`` of each frame.
    group_weight : `float`
        The group's combine weight (it cancels with one group).
    exposure : `float`
        The group's exposure, ``t_g``, which is also ``t_bar`` here.

    Returns
    -------
    sky_noise, read_noise : `tuple` [`float`, `float`]
        The two noise parts in stack units.
    """
    share = 1.0 / len(frames)
    sky_variance = math.fsum(
        (share * k / (f.gain_electrons_per_adu * exposure)) ** 2 * f.sky_electrons
        for f, k in zip(frames, scales, strict=True)
    )
    read_variance = math.fsum(
        (share * k / (f.gain_electrons_per_adu * exposure)) ** 2 * f.read_noise_electrons**2
        for f, k in zip(frames, scales, strict=True)
    )
    area = effective_aperture_area_px()
    return (
        exposure / 65535.0 * math.sqrt(area * sky_variance),
        exposure / 65535.0 * math.sqrt(area * read_variance),
    )


def test_a_folded_frame_adds_its_scaled_noise_to_the_group_noise() -> None:
    """Four 120 s frames and one 300 s frame, referenced on a 120 s frame.

    The 300 s frame has k = 120 / 300 = 0.4. Its sky is 2.5 times a 120 s
    frame's (400 electrons for 120 s, 1000 for 300 s).
    """
    exposure = (4 * 120.0 + 300.0) / 5
    frames = [FrameNoiseInput(1.0, 400.0, 3.8, 1.0, exposure_scale=1.0) for _ in range(4)]
    frames.append(FrameNoiseInput(1.0, 1000.0, 3.8, 1.0, exposure_scale=0.4))
    group = _group_facts(5.0 * exposure**2 / 100.0, exposure, 5, 1.0, 1e-4)
    outcome = expected_exposure_group_noise(frames, [0] * 5, [group])
    sky_noise, read_noise = _folded_group_hand_value(frames, [1, 1, 1, 1, 0.4], 1.0, exposure)
    assert outcome.result is not None
    assert outcome.result.sky_noise == pytest.approx(sky_noise, rel=1e-12)
    assert outcome.result.read_noise == pytest.approx(read_noise, rel=1e-12)


def test_a_folded_group_referenced_on_the_long_frame_scales_the_short_ones_up() -> None:
    """With the 300 s frame as the reference, the 120 s frames have k = 2.5."""
    exposure = (4 * 120.0 + 300.0) / 5
    frames = [FrameNoiseInput(1.0, 400.0, 3.8, 1.0, exposure_scale=2.5) for _ in range(4)]
    frames.append(FrameNoiseInput(1.0, 1000.0, 3.8, 1.0, exposure_scale=1.0))
    group = _group_facts(5.0 * exposure**2 / 100.0, exposure, 5, 1.0, 1e-4)
    outcome = expected_exposure_group_noise(frames, [0] * 5, [group])
    sky_noise, read_noise = _folded_group_hand_value(frames, [2.5] * 4 + [1.0], 1.0, exposure)
    assert outcome.result is not None
    assert outcome.result.sky_noise == pytest.approx(sky_noise, rel=1e-12)
    assert outcome.result.read_noise == pytest.approx(read_noise, rel=1e-12)
    # The short frames are scaled up, so this is above a 120 s reference's.
    reference_120 = [FrameNoiseInput(1.0, 400.0, 3.8, 1.0, exposure_scale=1.0)] * 5
    unscaled = expected_exposure_group_noise(reference_120, [0] * 5, [group])
    assert unscaled.result is not None
    assert outcome.result.read_noise > unscaled.result.read_noise


def test_a_scale_of_one_leaves_a_stack_unchanged_to_twelve_digits() -> None:
    """A group of one exposure length has k = 1, the default, so no change."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4), _group_facts(5.0, 120.0, 2, 1.25, 2e-4)]
    plain = [
        _frame(1.0, 60.0, 3.0, 0.9),
        _frame(3.0, 70.0, 3.0, 0.9),
        _frame(2.0, 240.0, 3.0, 0.9),
        _frame(2.0, 250.0, 3.0, 0.9),
    ]
    explicit = [
        FrameNoiseInput(f.weight, f.sky_electrons, f.read_noise_electrons, f.gain_electrons_per_adu, "", 1.0)
        for f in plain
    ]
    before = expected_exposure_group_noise(plain, [0, 0, 1, 1], groups)
    after = expected_exposure_group_noise(explicit, [0, 0, 1, 1], groups)
    assert before.result is not None and after.result is not None
    assert plain[0].exposure_scale == pytest.approx(1.0, abs=0.0)
    assert after.result.sky_noise == pytest.approx(before.result.sky_noise, rel=1e-12, abs=0.0)
    assert after.result.read_noise == pytest.approx(before.result.read_noise, rel=1e-12, abs=0.0)


def test_an_unusable_exposure_scale_gives_a_reason() -> None:
    """A zero or non-finite scale is refused, not used."""
    groups = [_group_facts(3.0, 30.0, 4, 1.0, 1e-4)]
    for bad in (0.0, float("nan")):
        frame = FrameNoiseInput(1.0, 60.0, 3.0, 0.9, exposure_scale=bad)
        outcome = expected_exposure_group_noise([frame], [0], groups)
        assert outcome.result is None
        assert "exposure scale" in outcome.reason


def test_a_single_group_stack_scales_a_folded_frame_by_hand() -> None:
    """Plain path: equal weights, one frame with k = 0.4, hand computed."""
    frames = [FrameNoiseInput(1.0, 400.0, 3.8, 0.8, exposure_scale=1.0) for _ in range(4)]
    frames.append(FrameNoiseInput(1.0, 1000.0, 3.8, 0.8, exposure_scale=0.4))
    outcome = expected_stack_noise(frames)
    assert outcome.result is not None
    area = effective_aperture_area_px()
    sky_variance = sum((k / 5) ** 2 * s / 0.8**2 for k, s in [(1, 400)] * 4 + [(0.4, 1000)])
    read_variance = sum((k / 5) ** 2 * 3.8**2 / 0.8**2 for k in [1] * 4 + [0.4])
    assert outcome.result.sky_noise == pytest.approx(math.sqrt(area * sky_variance) / 65535.0, rel=1e-12)
    assert outcome.result.read_noise == pytest.approx(math.sqrt(area * read_variance) / 65535.0, rel=1e-12)
    expected_sky = sum(k / 5 * s / 0.8 for k, s in [(1, 400)] * 4 + [(0.4, 1000)]) / 65535.0
    assert outcome.result.predicted_sky_level == pytest.approx(expected_sky, rel=1e-12)


def test_a_single_group_stack_with_scale_one_is_unchanged_to_twelve_digits() -> None:
    """Default and explicit k = 1 give the old formula's numbers."""
    frames = [_frame(1.0, 120.0, 3.8, 0.8), _frame(2.5, 100.0, 3.8, 0.8), _frame(0.5, 140.0, 3.8, 0.8)]
    outcome = expected_stack_noise(frames)
    assert outcome.result is not None
    total = 4.0
    area = effective_aperture_area_px()
    sky = sum((f.weight / total) ** 2 * f.sky_electrons / 0.8**2 for f in frames)
    read = sum((f.weight / total) ** 2 * 3.8**2 / 0.8**2 for f in frames)
    assert outcome.result.sky_noise == pytest.approx(math.sqrt(area * sky) / 65535.0, rel=1e-12)
    assert outcome.result.read_noise == pytest.approx(math.sqrt(area * read) / 65535.0, rel=1e-12)
