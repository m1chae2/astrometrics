"""Purpose: Unit tests for stacking frames of different exposure lengths.

Description: A spectroscopy session shot at several exposure lengths must
not be stacked as one batch, or the usual normalization and clipping throw
away the best frames. These tests check that frames are split by exposure
length (with tiny groups folded into their nearest neighbour), that the
noise of a stack is measured sensibly, and that the group stacks combine
into an image that is closer to the truth than an equal-weight average,
leaves out saturated pixels, and rejects nonsense input.
"""

from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.exposure_groups import (
    CLIPPED_FRAME_ZERO_FRACTION,
    SATURATION_FRACTION_OF_FULL_SCALE,
    SATURATION_MASK_FRACTION_OF_CEILING,
    ExposureGroup,
    combine_exposure_group_images,
    estimate_background_noise,
    estimate_group_gains,
    estimate_saturation_mask_level,
    frame_exposure_seconds,
    group_frame_noises,
    measure_frame_noise,
    merge_registration_sequences,
    merge_rejection_maps,
    split_frames_by_exposure,
)


def _frames(*exposures: object) -> list[SimpleNamespace]:
    """Build frame records with the given exposure lengths.

    Returns
    -------
    frames : `list`
        One frame per exposure, numbered in order.
    """
    return [
        SimpleNamespace(name=f"frame_{index}", exposure=exposure) for index, exposure in enumerate(exposures)
    ]


def test_exposure_is_read_from_strings_numbers_and_dictionaries() -> None:
    """The exposure length can be a string, a number or a dictionary entry."""
    assert frame_exposure_seconds(SimpleNamespace(exposure="0.5")) == pytest.approx(0.5)
    assert frame_exposure_seconds(SimpleNamespace(exposure=5)) == pytest.approx(5.0)
    assert frame_exposure_seconds({"exposure": "2.0"}) == pytest.approx(2.0)


@pytest.mark.parametrize("bad_value", [None, "", "abc", 0, -1.0, float("nan"), float("inf")])
def test_unreadable_exposures_give_none(bad_value: object) -> None:
    """A missing, non-numeric, non-positive or infinite exposure is `None`."""
    assert frame_exposure_seconds(SimpleNamespace(exposure=bad_value)) is None


def test_frames_with_one_exposure_stay_in_one_group() -> None:
    """A session shot at a single exposure length is not split."""
    groups = split_frames_by_exposure(_frames("30.0", "30", 30.0, "30.0", "30.0", "30.0"))

    assert len(groups) == 1
    assert groups[0].exposure_seconds == pytest.approx(30.0)
    assert len(groups[0].frames) == 6


def test_a_bracketed_session_is_split_by_exposure_shortest_first() -> None:
    """The Vega session (0.5, 1, 2, 3 and 5 s) becomes five groups."""
    exposures = ["0.5"] * 60 + ["1.0"] * 20 + ["2.0"] * 20 + ["3.0"] * 20 + ["5.0"] * 20
    groups = split_frames_by_exposure(_frames(*exposures))

    assert [group.exposure_seconds for group in groups] == pytest.approx([0.5, 1.0, 2.0, 3.0, 5.0])
    assert [len(group.frames) for group in groups] == [60, 20, 20, 20, 20]


def test_frames_keep_their_original_order_inside_a_group() -> None:
    """Frames stay in the order they were given, even when interleaved."""
    frames = _frames("1.0", "5.0", "1.0", "5.0", "1.0", "5.0", "1.0", "5.0", "1.0", "5.0")

    groups = split_frames_by_exposure(frames)

    assert [frame.name for frame in groups[0].frames] == [
        "frame_0",
        "frame_2",
        "frame_4",
        "frame_6",
        "frame_8",
    ]
    assert [frame.name for frame in groups[1].frames] == [
        "frame_1",
        "frame_3",
        "frame_5",
        "frame_7",
        "frame_9",
    ]


def test_a_tiny_group_is_folded_into_the_nearest_exposure() -> None:
    """A lone 2 s frame joins the 1 s group (2x away), not the 5 s (2.5x)."""
    frames = _frames(*(["1.0"] * 20), *(["5.0"] * 20), "2.0")

    groups = split_frames_by_exposure(frames)

    assert len(groups) == 2
    assert len(groups[0].frames) == 21
    assert len(groups[1].frames) == 20
    assert any(frame.exposure == "2.0" for frame in groups[0].frames)


def test_when_no_group_is_big_enough_everything_stays_together() -> None:
    """A handful of frames at different exposures is stacked as one batch."""
    groups = split_frames_by_exposure(_frames("0.1", "0.25", "1.0"))

    assert len(groups) == 1
    assert len(groups[0].frames) == 3


def test_one_unreadable_exposure_keeps_everything_in_one_group() -> None:
    """If a frame's exposure is unknown, the frames are not split."""
    groups = split_frames_by_exposure(_frames(*(["1.0"] * 10), *(["5.0"] * 10), None))

    assert len(groups) == 1
    assert len(groups[0].frames) == 21


def test_the_noise_estimate_recovers_the_true_noise() -> None:
    """Gaussian noise of 0.01 is measured as about 0.01."""
    image = np.random.default_rng(1).normal(0.2, 0.01, (300, 300))

    assert estimate_background_noise(image) == pytest.approx(0.01, rel=0.05)


def test_the_noise_estimate_ignores_a_few_bright_stars() -> None:
    """Bright stars do not inflate the measured background noise."""
    image = np.random.default_rng(2).normal(0.2, 0.01, (300, 300))
    for row, column in ((50, 60), (120, 200), (250, 90)):
        image[row - 3 : row + 4, column - 3 : column + 4] += 0.6

    assert estimate_background_noise(image) == pytest.approx(0.01, rel=0.08)


def test_the_noise_estimate_ignores_a_smooth_gradient() -> None:
    """A smooth background gradient is not mistaken for noise."""
    gradient = np.tile(np.linspace(0.0, 0.5, 300), (300, 1))
    image = gradient + np.random.default_rng(3).normal(0.0, 0.01, (300, 300))

    assert estimate_background_noise(image) == pytest.approx(0.01, rel=0.05)


def _bracketed_images(read_noise: float) -> tuple[np.ndarray, list[np.ndarray], list[float]]:
    """Build the same sky at 0.5 s and 5 s with the same camera noise.

    Returns
    -------
    signal_per_second, images, exposures : `tuple`
        The true counts-per-second image, the two stacked images, and their
        exposure lengths.
    """
    generator = np.random.default_rng(4)
    rows, columns = np.mgrid[0:200, 0:200]
    signal = 0.02 + 0.06 * np.exp(-(((rows - 100) ** 2 + (columns - 100) ** 2) / (2 * 25.0**2)))
    exposures = [0.5, 5.0]
    images = [signal * exposure + generator.normal(0.0, read_noise, signal.shape) for exposure in exposures]
    return signal, images, exposures


def test_combined_image_is_closer_to_the_truth_than_an_equal_weight_average() -> None:
    """Weighting by measured noise beats an equal-weight average."""
    signal, images, exposures = _bracketed_images(read_noise=0.01)
    mean_exposure = float(np.mean(exposures))

    combined = combine_exposure_group_images(images, exposures) / mean_exposure
    equal_weight = np.mean(
        [image / exposure for image, exposure in zip(images, exposures, strict=True)], axis=0
    )

    combined_error = float(np.std(combined - signal))
    equal_weight_error = float(np.std(equal_weight - signal))
    assert combined_error < 0.3 * equal_weight_error
    assert float(np.mean(combined)) == pytest.approx(float(np.mean(signal)), rel=0.02)


def test_output_is_scaled_to_the_mean_exposure_per_frame() -> None:
    """The result is comparable in brightness with a normal stack."""
    signal, images, exposures = _bracketed_images(read_noise=0.001)

    combined = combine_exposure_group_images(images, exposures, frame_counts=[60, 20])

    expected_mean_exposure = (60 * 0.5 + 20 * 5.0) / 80
    assert float(np.mean(combined)) == pytest.approx(
        float(np.mean(signal)) * expected_mean_exposure, rel=0.02
    )
    assert combined.dtype == np.float32


def test_a_saturated_group_is_left_out_where_it_is_saturated() -> None:
    """At a saturated pixel the other group's measurement is used."""
    generator = np.random.default_rng(5)
    short = 0.02 + generator.normal(0.0, 0.001, (60, 60))
    long = 0.2 + generator.normal(0.0, 0.001, (60, 60))
    long[30, 30] = 1.0  # clipped at the top of the range
    short[30, 30] = 0.04  # the short frame measured it properly

    combined = combine_exposure_group_images([short, long], [0.5, 5.0])

    mean_exposure = 2.75
    assert combined[30, 30] == pytest.approx(0.04 / 0.5 * mean_exposure, rel=1e-4)
    assert combined[10, 10] == pytest.approx(0.02 / 0.5 * mean_exposure, rel=0.3)  # blended elsewhere


def test_a_pixel_saturated_in_every_group_uses_the_shortest_exposure() -> None:
    """When no group has a clean value, the shortest exposure is least bad."""
    generator = np.random.default_rng(6)
    short = 0.5 + generator.normal(0.0, 0.001, (40, 40))
    long = 0.5 + generator.normal(0.0, 0.001, (40, 40))
    short[5, 5] = 0.99
    long[5, 5] = 1.0

    combined = combine_exposure_group_images([short, long], [0.5, 5.0])

    assert combined[5, 5] == pytest.approx(0.99 / 0.5 * 2.75, rel=1e-4)


def test_the_saturation_threshold_sits_below_full_scale() -> None:
    """The saturation cut-off is a fraction of the full 0-1 range."""
    assert 0.5 < SATURATION_FRACTION_OF_FULL_SCALE < 1.0


def test_combining_rejects_input_that_does_not_line_up() -> None:
    """Empty input, mismatched lists and bad exposures raise `ValueError`."""
    image = np.random.default_rng(7).normal(0.2, 0.01, (20, 20))

    with pytest.raises(ValueError, match="one image and one exposure"):
        combine_exposure_group_images([], [])
    with pytest.raises(ValueError, match="one image and one exposure"):
        combine_exposure_group_images([image, image], [1.0])
    with pytest.raises(ValueError, match="positive"):
        combine_exposure_group_images([image], [0.0])


def test_combining_rejects_an_image_with_no_measurable_noise() -> None:
    """A perfectly flat image has no noise to weight by, which is an error."""
    with pytest.raises(ValueError, match="noise"):
        combine_exposure_group_images([np.full((20, 20), 0.3)], [1.0])


def test_rejection_maps_combine_weighted_by_frame_count(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """The combined map is the overall fraction of values rejected."""
    first, second = tmp_path / "first.fits", tmp_path / "second.fits"
    fits.writeto(first, np.full((4, 4), 0.1, np.float32))
    fits.writeto(second, np.full((4, 4), 0.4, np.float32))

    written = merge_rejection_maps([str(first), str(second)], [1, 3], str(tmp_path / "merged.fits"))

    assert written is True
    assert fits.getdata(tmp_path / "merged.fits")[0, 0] == pytest.approx(0.325, rel=1e-4)


def test_rejection_maps_skip_missing_files(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """A group without a rejection map is left out, not an error."""
    only = tmp_path / "only.fits"
    fits.writeto(only, np.full((4, 4), 0.2, np.float32))

    assert merge_rejection_maps([str(only), str(tmp_path / "missing.fits")], [2, 2], str(tmp_path / "m.fits"))
    assert fits.getdata(tmp_path / "m.fits")[0, 0] == pytest.approx(0.2, rel=1e-4)
    assert not merge_rejection_maps([str(tmp_path / "none.fits")], [1], str(tmp_path / "n.fits"))


def test_registration_sequences_keep_only_the_per_frame_lines(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """Only Siril's per-frame R lines are joined, in group order."""
    first, second = tmp_path / "a.seq", tmp_path / "b.seq"
    first.write_text(
        "#Siril sequence file\nS 'x' 0 2 2 0 0 6 0 0 0\nR0 3.6 3.6 0.9 0 8e-05 19 H 1 0 0 0 1 0 0 0 1\n"
    )
    second.write_text("#Siril sequence file\nR0 2.9 3.8 0.9 0 7e-05 20 H 1 0 0.3 0 1 0.4 0 0 1\nI 0 1\n")

    written = merge_registration_sequences([str(first), str(second)], str(tmp_path / "merged.seq"))

    lines = (tmp_path / "merged.seq").read_text().splitlines()
    assert written is True
    assert len(lines) == 2
    assert lines[0].startswith("R0 3.6") and lines[1].startswith("R0 2.9")
    assert not merge_registration_sequences([str(tmp_path / "gone.seq")], str(tmp_path / "x.seq"))


def _write_raw_frames(folder, count: int, mean: float, noise: float, seed: int) -> list[SimpleNamespace]:  # ruff: ignore[missing-type-function-argument]
    """Write raw frames the way a camera with a low offset would.

    Returns
    -------
    frames : `list`
        Frame records pointing at the written files. Values below zero are
        clipped to zero, like a real 16-bit frame.
    """
    generator = np.random.default_rng(seed)
    frames = []
    for index in range(count):
        path = folder / f"raw_{seed}_{index}.fits"
        data = np.clip(generator.normal(mean, noise, (120, 120)), 0, None)
        fits.writeto(path, np.round(data).astype(np.uint16))
        frames.append(SimpleNamespace(path=str(path), exposure="1.0"))
    return frames


def test_frame_noise_is_measured_from_a_raw_frame(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """An unclipped frame gives its true noise and almost no zeros."""
    frame = _write_raw_frames(tmp_path, 1, mean=50.0, noise=8.0, seed=1)[0]

    noise, zero_fraction = measure_frame_noise(frame.path)

    assert noise == pytest.approx(8.0, rel=0.1)
    assert zero_fraction < CLIPPED_FRAME_ZERO_FRACTION


def test_a_clipped_frame_reports_many_zeros(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """A frame whose background sits below the noise is mostly zeros."""
    frame = _write_raw_frames(tmp_path, 1, mean=0.0, noise=8.0, seed=2)[0]

    _, zero_fraction = measure_frame_noise(frame.path)

    assert zero_fraction > CLIPPED_FRAME_ZERO_FRACTION


def test_a_clipped_group_takes_the_noise_of_the_cleanest_group(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """Clipped frames read low, so they are given a clean group's noise."""
    clipped = ExposureGroup(0.5, _write_raw_frames(tmp_path, 3, mean=0.0, noise=8.0, seed=3))
    clean_quiet = ExposureGroup(2.0, _write_raw_frames(tmp_path, 3, mean=60.0, noise=6.0, seed=4))
    clean_noisy = ExposureGroup(5.0, _write_raw_frames(tmp_path, 3, mean=100.0, noise=9.0, seed=5))

    noises = group_frame_noises([clipped, clean_quiet, clean_noisy])

    assert noises[1] == pytest.approx(6.0, rel=0.15)
    assert noises[2] == pytest.approx(9.0, rel=0.15)
    assert noises[0] == pytest.approx(noises[2])  # the largest clean noise


def test_when_every_group_is_clipped_all_groups_get_the_same_noise(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """With no clean group to learn from, the noise is the same for all."""
    groups = [
        ExposureGroup(0.5, _write_raw_frames(tmp_path, 2, mean=0.0, noise=8.0, seed=6)),
        ExposureGroup(1.0, _write_raw_frames(tmp_path, 2, mean=2.0, noise=8.0, seed=7)),
    ]

    assert group_frame_noises(groups) == [1.0, 1.0]


def test_a_frame_that_cannot_be_read_raises_an_os_error(tmp_path) -> None:  # ruff: ignore[missing-type-function-argument]
    """A missing file or a frame without a path is an `OSError`."""
    missing = ExposureGroup(1.0, [SimpleNamespace(path=str(tmp_path / "gone.fits"))])
    pathless = ExposureGroup(1.0, [SimpleNamespace()])

    with pytest.raises(OSError):
        group_frame_noises([missing])
    with pytest.raises(OSError, match="no file path"):
        group_frame_noises([pathless])


def test_frame_noise_weights_are_frames_times_exposure_squared() -> None:
    """With equal frame noise, weight goes as frames x exposure^2."""
    generator = np.random.default_rng(8)
    short = 0.05 * 0.5 + generator.normal(0.0, 0.001, (80, 80))
    long = 0.05 * 5.0 + generator.normal(0.0, 0.001, (80, 80))

    combined = combine_exposure_group_images([short, long], [0.5, 5.0], [60, 20], frame_noises=[1.0, 1.0])

    long_weight, short_weight = 20 * 5.0**2, 60 * 0.5**2
    expected_per_second = (short_weight * 0.05 + long_weight * 0.05) / (short_weight + long_weight)
    mean_exposure = (60 * 0.5 + 20 * 5.0) / 80
    assert float(np.mean(combined)) == pytest.approx(expected_per_second * mean_exposure, rel=0.02)


def test_frame_noises_must_be_one_positive_number_per_group() -> None:
    """A wrong count or a non-positive noise raises `ValueError`."""
    image = np.random.default_rng(9).normal(0.2, 0.01, (20, 20))

    with pytest.raises(ValueError, match="frame noise"):
        combine_exposure_group_images([image, image], [1.0, 2.0], frame_noises=[1.0])
    with pytest.raises(ValueError, match="frame noise"):
        combine_exposure_group_images([image], [1.0], frame_noises=[0.0])


def _stack_with_saturated_core(ceiling: float, seed: int) -> np.ndarray:
    """Make a smooth image whose brightest pixels are clipped at a ceiling.

    The ceiling varies by 1% from pixel to pixel, as a real one does.

    Returns
    -------
    image : `numpy.ndarray`
        The image, 200 x 200, with a clipped core of a few hundred pixels.
    """
    generator = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:200, 0:200]
    radius_squared = (rows - 100) ** 2 + (columns - 100) ** 2
    image = 2.0 * ceiling * np.exp(-radius_squared / (2 * 8.0**2)) + generator.normal(0.0, 0.002, (200, 200))
    return np.minimum(image, ceiling * (1.0 + generator.normal(0.0, 0.01, image.shape)))


def test_a_ceiling_below_the_fixed_level_is_found_from_the_pixels() -> None:
    """A core clipped at 0.81 (as in Vega's 5 s stack) is found."""
    image = _stack_with_saturated_core(ceiling=0.81, seed=11)

    level = estimate_saturation_mask_level(image)

    assert level == pytest.approx(0.81 * SATURATION_MASK_FRACTION_OF_CEILING, rel=0.03)
    assert level < SATURATION_FRACTION_OF_FULL_SCALE


def test_a_clipped_core_at_full_scale_is_masked_a_little_below_it() -> None:
    """A ceiling at 1.0 gives a mask level just below it."""
    image = _stack_with_saturated_core(ceiling=1.0, seed=12)

    assert estimate_saturation_mask_level(image) == pytest.approx(
        SATURATION_MASK_FRACTION_OF_CEILING, rel=0.03
    )


def test_a_real_bright_peak_is_not_mistaken_for_a_ceiling() -> None:
    """An unsaturated star keeps the fixed level."""
    rows, columns = np.mgrid[0:200, 0:200]
    star = 0.7 * np.exp(-(((rows - 100) ** 2 + (columns - 100) ** 2) / (2 * 2.0**2)))

    assert estimate_saturation_mask_level(star) == SATURATION_FRACTION_OF_FULL_SCALE


def test_group_gains_recover_the_scale_each_stack_was_given() -> None:
    """Groups scaled by 0.7 and 1.2 are measured as such."""
    generator = np.random.default_rng(13)
    truth = generator.uniform(0.0, 1.0, (300, 300)) ** 3
    per_second = [truth * 1.0, truth * 0.7 + generator.normal(0, 0.001, truth.shape), truth * 1.2]
    usable = [np.ones(truth.shape, dtype=bool)] * 3

    gains = estimate_group_gains(per_second, usable, reference_index=0)

    assert gains == pytest.approx([1.0, 0.7, 1.2], rel=0.02)


def test_too_few_bright_shared_pixels_leave_the_gain_at_one() -> None:
    """A tiny image is not enough to measure a gain from."""
    small = np.random.default_rng(14).uniform(0.1, 1.0, (10, 10))

    assert estimate_group_gains([small, small * 0.5], [np.ones(small.shape, bool)] * 2, 0) == [1.0, 1.0]


def test_saturated_pixels_do_not_enter_the_gain() -> None:
    """A clipped core in one group must not drag its measured gain down."""
    generator = np.random.default_rng(15)
    truth = generator.uniform(0.0, 1.0, (300, 300)) ** 3
    clipped = np.minimum(truth * 2.0, 1.0)
    usable_clipped = clipped < 0.95

    gains = estimate_group_gains([truth, clipped], [np.ones(truth.shape, bool), usable_clipped], 0)

    assert gains[1] == pytest.approx(2.0, rel=0.02)


def test_groups_with_different_scales_and_saturation_combine_to_one_spectrum() -> None:
    """A scaled, clipped long group must not bend the result.

    This is the Vega failure in miniature: the long group is scaled down by
    0.7 and saturates at 0.8, the short one is clean. The combined image must
    follow the true per-second signal everywhere, including at the core.
    """
    generator = np.random.default_rng(16)
    rows, columns = np.mgrid[0:200, 0:200]
    truth = 0.02 + 0.5 * np.exp(-(((rows - 100) ** 2 + (columns - 100) ** 2) / (2 * 12.0**2)))
    exposures = [1.0, 4.0]
    short = truth * exposures[0] + generator.normal(0.0, 0.002, truth.shape)
    long_unscaled = truth * exposures[1] + generator.normal(0.0, 0.002, truth.shape)
    long = np.minimum(0.7 * long_unscaled, 0.8)  # own scale, clipped core

    combined = combine_exposure_group_images([short, long], exposures)

    mean_exposure = float(np.mean(exposures))
    relative_error = combined / mean_exposure / truth
    core = relative_error[95:105, 95:105]
    edge = relative_error[10:30, 10:30]
    # The scale is that of the reference (heaviest) group, which is the long
    # one here (0.7 of the truth); what matters is that it is the same
    # everywhere, core included.
    assert float(np.median(core)) == pytest.approx(float(np.median(edge)), rel=0.05)
