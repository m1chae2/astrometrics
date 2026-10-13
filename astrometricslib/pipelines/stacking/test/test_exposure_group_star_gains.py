"""Purpose: Tests that exposure-group gains are measured from star fluxes.

Description: Two synthetic group images hold the same isolated Gaussian stars,
one scaled by a known gain. These tests check that the star-flux method
recovers the gain through the things that fooled the old pixel-ratio method (a
sky gradient, a different star width), that it leaves out clipped stars and
groups with too few stars, that it flags a group whose bright stars are
compressed, and that the camera profile sets the clip level of a stack that
shows no saturation plateau.
"""

from collections.abc import Callable

import numpy as np
import pytest

from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    SATURATION_FRACTION_OF_FULL_SCALE,
    SKY_RATIO_LIMIT,
    STAR_GAIN_CLIP_FRACTION_OF_CEILING,
    STAR_GAIN_MINIMUM_STARS,
    GroupGainMeasurement,
    estimate_saturation_mask_level,
    find_isolated_stars,
    measure_exposure_group_gains,
    measure_group_gains,
    star_clip_level,
)

IMAGE_SIZE = 1200
TRUE_GAIN = 0.6
NOISE = 1e-4
# Stars sit on a grid 80 pixels apart, away from the 60 pixel edge margin of
# the star detector, so every star is isolated.
GRID_SPACING = 80


def make_star_positions() -> list[tuple[float, float]]:
    """Lay out the star positions on a jittered grid.

    Returns
    -------
    positions : `list` [`tuple` [`float`, `float`]]
        (row, column) of each star, the same on every call.
    """
    generator = np.random.default_rng(3)
    grid = range(100, IMAGE_SIZE - 100, GRID_SPACING)
    return [
        (row + generator.uniform(-8, 8), column + generator.uniform(-8, 8)) for row in grid for column in grid
    ]


def make_amplitudes(count: int) -> np.ndarray:
    """Give each star a peak height, spread evenly in brightness.

    Returns
    -------
    amplitudes : `numpy.ndarray`
        Peak heights from 0.01 to 0.4 (counts per second as a fraction of
        full scale), in a fixed random order so faint and bright stars mix.
    """
    amplitudes = np.linspace(0.01, 0.4, count)
    np.random.default_rng(4).shuffle(amplitudes)
    return amplitudes


def make_image(
    gain: float = 1.0,
    width: float = 2.5,
    sky: float = 0.01,
    gradient: float = 0.0,
    seed: int = 1,
    response: Callable[[float], float] | None = None,
    star_count: int | None = None,
) -> np.ndarray:
    """Build one group image: a sky, a gradient and Gaussian stars.

    Parameters
    ----------
    gain : `float`, optional
        Scale applied to the stars (and to nothing else).
    width : `float`, optional
        The stars' Gaussian sigma in pixels. The star's total light is kept
        the same as the width changes, so a wider star is lower and broader.
    sky : `float`, optional
        The flat sky level.
    gradient : `float`, optional
        A sky ramp across the image, from 0 to this value.
    seed : `int`, optional
        Seed of the noise.
    response : `callable`, optional
        Maps the star's peak height to the height recorded (a nonlinear
        camera). Applied to the final star peak, in place of the plain gain.
    star_count : `int`, optional
        Use only this many of the stars.

    Returns
    -------
    image : `numpy.ndarray`
        A 2-D float32 image.
    """
    rows, columns = np.mgrid[0:IMAGE_SIZE, 0:IMAGE_SIZE]
    image = (
        sky
        + gradient * (rows + columns) / (2.0 * IMAGE_SIZE)
        + np.random.default_rng(seed).normal(0.0, NOISE, (IMAGE_SIZE, IMAGE_SIZE))
    )
    positions = make_star_positions()
    amplitudes = make_amplitudes(len(positions))
    if star_count is not None:
        positions, amplitudes = positions[:star_count], amplitudes[:star_count]
    for (row, column), amplitude in zip(positions, amplitudes, strict=True):
        peak = gain * amplitude if response is None else response(amplitude) * gain
        scaled_peak = peak * (2.5 / width) ** 2
        window = (slice(int(row) - 30, int(row) + 31), slice(int(column) - 30, int(column) + 31))
        image[window] += scaled_peak * np.exp(
            -((rows[window] - row) ** 2 + (columns[window] - column) ** 2) / (2 * width**2)
        )
    return image.astype(np.float32)


def measure(
    group_b: np.ndarray, group_a: np.ndarray | None = None, **options: float | None
) -> GroupGainMeasurement:
    """Measure group B against a reference group A with the combination's code.

    Returns
    -------
    measurement : `GroupGainMeasurement`
        The entry for group B. Group A carries more frames, so it is the
        reference.
    """
    group_a = make_image(seed=2) if group_a is None else group_a
    reference_index, measurements = measure_exposure_group_gains(
        [group_a, group_b], [1.0, 1.0], frame_counts=[2, 1], frame_noises=[1.0, 1.0], **options
    )
    assert reference_index == 0
    return measurements[1]


def test_the_star_gain_recovers_a_known_gain() -> None:
    """A group with every star scaled by 0.6 reads a gain of 0.6 on stars."""
    measurement = measure(make_image(gain=TRUE_GAIN, seed=5))
    assert measurement.method == "stars"
    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.01)
    assert measurement.stars_used >= STAR_GAIN_MINIMUM_STARS
    assert not measurement.nonlinear
    assert measurement.disagreement is not None
    assert measurement.disagreement < DEFAULT_GAIN_DISAGREEMENT_TOLERANCE


def test_a_sky_gradient_that_differs_between_groups_does_not_move_the_gain() -> None:
    """A different sky level and tilt do not move the star gain."""
    group_b = make_image(gain=TRUE_GAIN, sky=0.03, gradient=0.02, seed=6)
    measurement = measure(group_b)
    assert measurement.method == "stars"
    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.01)
    assert not measurement.nonlinear


def test_the_star_gain_ignores_a_wider_star_profile() -> None:
    """Stars 40% wider hold the same light, so the gain is unchanged."""
    measurement = measure(make_image(gain=TRUE_GAIN, width=3.5, seed=7))
    assert measurement.method == "stars"
    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.015)
    assert not measurement.nonlinear


def test_clipped_bright_stars_are_left_out_of_the_gain() -> None:
    """Stars past the clip level are dropped, so flat tops cannot bias it."""
    ceiling = 65535.0
    clip_level = star_clip_level(ceiling)

    def flat_topped(amplitude: float) -> float:
        """Record a star's peak, cut off at the clip level.

        Returns
        -------
        recorded : `float`
            The recorded peak height.
        """
        return min(amplitude, 1.02 * clip_level / TRUE_GAIN)

    clipped_group = make_image(gain=TRUE_GAIN, seed=8, response=flat_topped)
    measurement = measure(clipped_group, clip_ceiling_adu=ceiling)
    assert measurement.method == "stars"
    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.01)
    assert not measurement.nonlinear


def test_a_group_with_too_few_stars_falls_back_to_the_pixel_ratios() -> None:
    """With too few clean stars, the pixel method is used."""
    few = STAR_GAIN_MINIMUM_STARS // 2
    group_a = make_image(seed=2, star_count=few)
    measurement = measure(make_image(gain=TRUE_GAIN, seed=9, star_count=few), group_a)
    assert measurement.method == "pixels"
    assert measurement.stars_used == 0


def test_a_compressed_bright_end_marks_the_group_nonlinear() -> None:
    """Bright stars that read low make the bright quarter disagree."""
    knee = 0.1

    def compress(amplitude: float) -> float:
        """Record a star's peak, with a quarter slope above the knee.

        Returns
        -------
        recorded : `float`
            The recorded peak height.
        """
        return amplitude if amplitude <= knee else knee + 0.25 * (amplitude - knee)

    measurement = measure(make_image(gain=TRUE_GAIN, seed=10, response=compress))
    assert measurement.method == "stars"
    assert measurement.nonlinear
    assert measurement.disagreement is not None
    assert measurement.disagreement > DEFAULT_GAIN_DISAGREEMENT_TOLERANCE
    assert measurement.bright_end_ratio is not None
    assert measurement.faint_ratio is not None
    assert measurement.bright_end_ratio < measurement.faint_ratio


def test_a_pixel_gradient_fools_the_pixel_method_but_not_the_star_method() -> None:
    """A tilted sky misleads the pixel gain but not the star gain."""
    group_a = make_image(seed=2, sky=0.01, gradient=0.0)
    group_b = make_image(gain=TRUE_GAIN, seed=11, sky=0.01, gradient=0.004)
    masks = [np.ones(group_a.shape, dtype=bool)] * 2
    pixel = measure_group_gains([group_a, group_b], masks, reference_index=0)[1]
    star = measure(group_b, group_a)
    assert abs(star.gain - TRUE_GAIN) / TRUE_GAIN < 0.01
    assert abs(star.gain - TRUE_GAIN) < abs(pixel.gain - TRUE_GAIN) or pixel.gain == pytest.approx(
        TRUE_GAIN, rel=0.01
    )


def test_stars_with_a_neighbour_closer_than_the_sky_ring_are_not_isolated() -> None:
    """Two stars 10 pixels apart are both dropped; a lone star stays."""
    stars = np.array([[100.0, 100.0], [100.0, 110.0], [400.0, 400.0]])
    isolated = find_isolated_stars(stars)
    assert isolated.tolist() == [[400.0, 400.0]]


def test_the_camera_clip_ceiling_sets_the_level_of_a_stack_with_no_plateau() -> None:
    """A 14-bit stack with no plateau is judged against 16383."""
    smooth = np.random.default_rng(12).normal(0.01, 1e-4, (200, 200))
    nikon_level = estimate_saturation_mask_level(smooth, clip_ceiling_adu=16383.0)
    assert nikon_level == pytest.approx(SATURATION_FRACTION_OF_FULL_SCALE * 16383.0 / 65535.0)
    assert nikon_level < 0.25
    assert estimate_saturation_mask_level(smooth) == SATURATION_FRACTION_OF_FULL_SCALE


def test_a_sixteen_bit_camera_keeps_the_old_fallback_level() -> None:
    """A 16-bit ceiling leaves the fallback level as it was."""
    smooth = np.random.default_rng(13).normal(0.01, 1e-4, (200, 200))
    asi_level = estimate_saturation_mask_level(smooth, clip_ceiling_adu=65532.0)
    assert asi_level == pytest.approx(SATURATION_FRACTION_OF_FULL_SCALE, rel=1e-4)


def test_a_found_plateau_ignores_the_camera_ceiling() -> None:
    """A found plateau sets the level, not the camera profile."""
    image = np.random.default_rng(14).normal(0.01, 1e-4, (200, 200))
    image[:20, :20] = 0.8
    assert estimate_saturation_mask_level(image, clip_ceiling_adu=16383.0) == estimate_saturation_mask_level(
        image
    )


def test_the_star_clip_level_is_a_fraction_of_the_camera_ceiling() -> None:
    """The star level is a set fraction of the ceiling in stack units."""
    assert star_clip_level(16383.0) == pytest.approx(STAR_GAIN_CLIP_FRACTION_OF_CEILING * 16383.0 / 65535.0)
    assert star_clip_level(None) == pytest.approx(STAR_GAIN_CLIP_FRACTION_OF_CEILING)
    assert SKY_RATIO_LIMIT > 1.0


def test_a_much_brighter_sky_marks_the_group_too_bright() -> None:
    """A sky four times the reference's is flagged; stars stay linear."""
    group_b = make_image(gain=TRUE_GAIN, sky=0.04, seed=15)
    measurement = measure(group_b, make_image(sky=0.01, seed=2))
    assert measurement.sky_ratio == pytest.approx(4.0, rel=0.05)
    assert measurement.sky_too_bright
    assert not measurement.nonlinear


def test_a_moderately_brighter_sky_is_not_flagged() -> None:
    """A sky twice the reference's, as on a good NGC 6992 pair, passes."""
    group_b = make_image(gain=TRUE_GAIN, sky=0.02, seed=16)
    measurement = measure(group_b, make_image(sky=0.01, seed=2))
    assert measurement.sky_ratio == pytest.approx(2.0, rel=0.05)
    assert not measurement.sky_too_bright
