"""Purpose: Unit tests for matching the sky background of exposure groups.

Description: Two group stacks of one field carry different sky backgrounds
(a different level in each color channel and a different tilt). When a
shifted group covers only part of the frame, the combined image must not
step in level or color where its coverage starts, and must not darken next
to that edge. These tests build two synthetic color groups with different
sky, one shifted so it covers only part of the frame, and measure the
combined image on both sides of the coverage edge.
"""

import numpy as np
from scipy.ndimage import gaussian_filter

from astrometricslib.pipelines.stacking.processing.group_combination import combine_exposure_group_images
from astrometricslib.pipelines.stacking.processing.group_sky_matching import (
    evaluate_sky_plane,
    fit_sky_plane,
    match_sky_to_reference,
)

ROWS = 512
COLUMNS = 384
# The shifted group covers rows from this one down.
EDGE_ROW = 160
# Stack pixels are fractions of full scale, so a 300 s stack must stay below 1.
# Every per-second value below is multiplied by this to keep it there.
UNIT = 1e-3
NOISE = 0.0004 * UNIT
# Frame noises that make the 180 s group, which covers the whole frame, the
# heavier one, so it is the reference the other group is moved onto.
FRAME_NOISES = [1.0, 3.0]


def _scene(random_generator: np.random.Generator) -> np.ndarray:
    """Make a smooth nebula-like signal with stars, in three color channels.

    Parameters
    ----------
    random_generator : `numpy.random.Generator`
        The source of the faint structure.

    Returns
    -------
    scene : `numpy.ndarray`
        A color image (channels first) of the signal alone.
    """
    rows, columns = np.mgrid[0:ROWS, 0:COLUMNS]
    blob = np.exp(-(((rows - 440) / 90.0) ** 2 + ((columns - 200) / 70.0) ** 2))
    scene = UNIT * np.stack([0.03 * blob, 0.05 * blob, 0.04 * blob])
    stars = np.zeros((ROWS, COLUMNS))
    for _ in range(150):
        row, column = random_generator.integers(20, ROWS - 20), random_generator.integers(20, COLUMNS - 20)
        stars[row, column] = random_generator.uniform(0.05, 0.3)
    # A Gaussian of width 2 pixels has a peak of 1 / (2 pi 2^2), about 1/25, of
    # its total, so 25 times the total gives each star the peak chosen above.
    stars = 25.0 * gaussian_filter(stars, 2.0)
    return scene + UNIT * stars[None]


def _sky(levels: tuple[float, float, float], row_slope: float, column_slope: float) -> np.ndarray:
    """Make a tilted sky background with its own level in each color channel.

    Parameters
    ----------
    levels : `tuple` [`float`, `float`, `float`]
        The sky level of each channel at the image center.
    row_slope : `float`
        How much the sky changes per row.
    column_slope : `float`
        How much the sky changes per column.

    Returns
    -------
    sky : `numpy.ndarray`
        A color image (channels first) of the sky alone.
    """
    rows, columns = np.mgrid[0:ROWS, 0:COLUMNS]
    tilt = row_slope * (rows - ROWS / 2.0) + column_slope * (columns - COLUMNS / 2.0)
    return UNIT * np.stack([level + tilt for level in levels])


def _two_groups() -> tuple[list[np.ndarray], list[np.ndarray], np.ndarray]:
    """Build the two groups, in counts, and their covered masks.

    Returns
    -------
    images : `list` [`numpy.ndarray`]
        The 180 s reference group and the 300 s group, which has its own
        sky and only the rows from `EDGE_ROW` down.
    covered : `list` [`numpy.ndarray`]
        The covered mask of each group.
    truth : `numpy.ndarray`
        The signal and sky of the reference group, per second.
    """
    random_generator = np.random.default_rng(7)
    scene = _scene(random_generator)
    reference_sky = _sky((0.002, 0.004, 0.003), 2e-6, -1e-6)
    other_sky = _sky((0.0035, 0.0085, 0.0041), -3e-6, 2e-6)
    noise = [random_generator.normal(0.0, NOISE, scene.shape) for _ in range(2)]
    reference = (scene + reference_sky + noise[0]) * 180.0
    other_per_second = scene + other_sky + noise[1]
    other = other_per_second * 300.0
    other_covered = np.zeros((ROWS, COLUMNS), dtype=bool)
    other_covered[EDGE_ROW:] = True
    other = np.where(other_covered[None], other, 0.0)
    return [reference, other], [np.ones((ROWS, COLUMNS), dtype=bool), other_covered], scene + reference_sky


def test_a_plane_is_recovered_from_a_difference_with_a_bright_object() -> None:
    """A tilted offset is fitted even with a bright object in it."""
    rows, columns = np.mgrid[0:ROWS, 0:COLUMNS]
    difference = 0.01 + 1e-5 * (rows - ROWS / 2.0) - 2e-5 * (columns - COLUMNS / 2.0)
    difference[100:180, 100:180] += 1.0
    plane = fit_sky_plane(difference, np.ones((ROWS, COLUMNS), dtype=bool))
    assert plane is not None
    assert np.allclose(plane, [0.01, 1e-5, -2e-5], atol=2e-4)
    assert evaluate_sky_plane(plane, (ROWS, COLUMNS)).shape == (ROWS, COLUMNS)


def test_no_plane_is_fitted_when_the_groups_barely_overlap() -> None:
    """Too few usable blocks give no plane and an unchanged image."""
    valid = np.zeros((ROWS, COLUMNS), dtype=bool)
    valid[:32, :32] = True
    assert fit_sky_plane(np.ones((ROWS, COLUMNS)), valid) is None
    image = np.full((ROWS, COLUMNS), 2.0)
    matched, fitted = match_sky_to_reference(image, np.zeros((ROWS, COLUMNS)), valid)
    assert not fitted
    assert np.array_equal(matched, image)


def test_the_combined_image_has_no_step_where_the_shifted_group_starts() -> None:
    """The level is the same on both sides of the coverage edge."""
    images, covered, truth = _two_groups()
    combined = combine_exposure_group_images(
        images, [180.0, 300.0], [10, 10], frame_noises=FRAME_NOISES, covered_masks=covered
    ).image
    per_second = combined / 240.0
    # Compare the band just above the coverage edge with the band just below
    # it, after removing the true signal and sky of the reference group.
    error = per_second - truth
    above = error[:, EDGE_ROW - 60 : EDGE_ROW - 6, 40:-40]
    below = error[:, EDGE_ROW + 6 : EDGE_ROW + 60, 40:-40]
    for channel in range(3):
        step = abs(float(np.median(below[channel])) - float(np.median(above[channel])))
        assert step < 0.25 * NOISE, f"channel {channel} steps by {step}"


def test_the_combined_image_has_no_dark_band_next_to_the_edge() -> None:
    """The first rows of the shifted group are not darker than later rows."""
    images, covered, truth = _two_groups()
    combined = combine_exposure_group_images(
        images, [180.0, 300.0], [10, 10], frame_noises=FRAME_NOISES, covered_masks=covered
    ).image
    error = combined / 240.0 - truth
    for channel in range(3):
        near = float(np.median(error[channel, EDGE_ROW : EDGE_ROW + 20, 40:-40]))
        far = float(np.median(error[channel, EDGE_ROW + 40 : EDGE_ROW + 80, 40:-40]))
        assert near > far - 0.25 * NOISE
