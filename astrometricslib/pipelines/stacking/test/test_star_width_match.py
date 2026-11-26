"""Tests for the star width matching of a color stack's preview.

Each test builds a small synthetic linear color stack: a grid of round
Gaussian stars on a noisy sky, where each channel can have its own star
width. The step must sharpen a channel whose stars are clearly wider than
green's toward green's width, keep each star's light and the sky level, leave
green and blue alone, leave the bright stars' surroundings alone, and change
nothing when the widths already agree.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing.star_width_match import (
    match_star_widths,
    match_star_widths_in_file,
    measure_channel_widths,
)

SIZE = 600
SPACING = 40
SKY = 0.01


def make_stack(
    sigmas: tuple[float, float, float],
    spacing: int = SPACING,
    saturated: tuple[tuple[int, int], ...] = (),
) -> np.ndarray:
    """Make a linear color stack with round Gaussian stars on a grid.

    Parameters
    ----------
    sigmas : `tuple` [`float`, `float`, `float`]
        The red, green and blue star sigma, in pixels.
    spacing : `int`, optional
        The distance between neighboring stars, in pixels.
    saturated : `tuple` [`tuple` [`int`, `int`], ...], optional
        Rows and columns of more stars whose cores are cut flat at 1.0,
        besides the one every stack has.

    Returns
    -------
    image : `numpy.ndarray`
        Shape (3, SIZE, SIZE), 32-bit floats.
    """
    generator = np.random.default_rng(7)
    rows, columns = np.mgrid[:SIZE, :SIZE]
    image = np.full((3, SIZE, SIZE), SKY, dtype=np.float32)
    centers = [
        (row + generator.uniform(-0.5, 0.5), column + generator.uniform(-0.5, 0.5))
        for row in range(spacing, SIZE - spacing + 1, spacing)
        for column in range(spacing, SIZE - spacing + 1, spacing)
    ]
    for row, column in centers:
        flux = generator.uniform(2.0, 6.0)
        distance_squared = (rows - row) ** 2 + (columns - column) ** 2
        for channel, sigma in enumerate(sigmas):
            image[channel] += flux / (2 * np.pi * sigma**2) * np.exp(-distance_squared / (2 * sigma**2))
    # One saturated star is always there, as in a real stack: the brightest
    # value of each channel is what the step takes as saturation.
    for row, column in ((SPACING * 2 + SPACING // 2, SPACING * 2 + SPACING // 2), *saturated):
        distance_squared = (rows - row) ** 2 + (columns - column) ** 2
        for channel, sigma in enumerate(sigmas):
            image[channel] += 400.0 / (2 * np.pi * sigma**2) * np.exp(-distance_squared / (2 * sigma**2))
    image += 0.0005 * generator.standard_normal(image.shape).astype(np.float32)
    return np.minimum(image, 1.0)


def test_wider_red_is_sharpened_to_green() -> None:
    """A red channel 40% wider than green ends within 5% of green."""
    image = make_stack((2.9, 2.1, 2.1))
    matched, result = match_star_widths(image)
    assert matched is not None and result.applied
    assert result.sharpened == ("red",)
    red_before, blue_before = result.ratios(result.widths_before)
    red_after, blue_after = result.ratios(result.widths_after)
    assert red_before > 1.3
    assert abs(red_after - 1.0) < 0.05
    assert blue_after == blue_before
    assert np.array_equal(matched[1], image[1]) and np.array_equal(matched[2], image[2])


def test_star_light_and_sky_level_are_kept() -> None:
    """The filter adds up to one: a star's light and the sky do not change."""
    image = make_stack((2.9, 2.1, 2.1))
    matched, _ = match_star_widths(image)
    assert matched is not None
    row, column = SPACING * 7, SPACING * 7
    window = (slice(row - 19, row + 20), slice(column - 19, column + 20))
    before = float(np.sum(image[0][window] - SKY))
    after = float(np.sum(matched[0][window] - SKY))
    assert abs(after - before) < 0.02 * before
    empty = (slice(SPACING * 3 + 18, SPACING * 4 - 18), slice(SPACING * 3 + 18, SPACING * 4 - 18))
    assert abs(float(np.median(matched[0][empty])) - SKY) < 0.0005


def test_widths_that_agree_are_left_alone() -> None:
    """Equal widths, or a red narrower than green, change nothing."""
    for sigmas in ((2.1, 2.1, 2.1), (1.9, 2.1, 2.15)):
        matched, result = match_star_widths(make_stack(sigmas))
        assert matched is None and not result.applied
        assert "already agree" in result.reason
        assert len(result.widths_before) == 3


def test_wider_blue_is_sharpened_too() -> None:
    """Any channel wider than green is sharpened, not only red."""
    matched, result = match_star_widths(make_stack((2.1, 2.1, 2.6)))
    assert matched is not None and result.sharpened == ("blue",)
    # The noise term keeps a little of the width of a pure Gaussian star.
    assert abs(result.ratios(result.widths_after)[1] - 1.0) < 0.08


def test_bright_star_takes_green_shape_without_a_ring() -> None:
    """Around a saturated core red follows green's shape, so no ring is made.

    The star has the same light in every channel, so after the step red
    over green stays the same going out from the flat core (the star's color,
    raised by the clipping), where the wide red skirt made it climb before.
    """
    bright = (SPACING * 5 + SPACING // 2, SPACING * 5 + SPACING // 2)
    image = make_stack((2.9, 2.1, 2.1), saturated=(bright,))
    matched, result = match_star_widths(image)
    assert matched is not None and result.applied
    edge = int(np.flatnonzero(image[1, bright[0]] >= 1.0).max())
    columns = np.arange(edge + 1, edge + 5)
    row = bright[0]
    before = (image[0, row, columns] - SKY) / (image[1, row, columns] - SKY)
    after = (matched[0, row, columns] - SKY) / (matched[1, row, columns] - SKY)
    assert before.max() > 3.0 * before.min()
    assert after.max() < 1.05 * after.min()


def test_too_few_stars_change_nothing() -> None:
    """With too few stars the widths are not trusted."""
    matched, result = match_star_widths(make_stack((2.9, 2.1, 2.1), spacing=150))
    assert matched is None and not result.applied
    assert "fewer than" in result.reason


def test_single_channel_is_not_touched() -> None:
    """A monochrome image gives no result image and says why."""
    matched, result = match_star_widths(np.full((SIZE, SIZE), SKY, dtype=np.float32))
    assert matched is None and "not a color image" in result.reason


def test_file_is_written_only_when_a_channel_is_sharpened(tmp_path: Path) -> None:
    """The file step keeps the header, and writes nothing when not needed."""
    header = fits.Header()
    header["OBJECT"] = "TEST"
    wide = tmp_path / "wide.fits"
    fits.PrimaryHDU(make_stack((2.9, 2.1, 2.1)), header=header).writeto(wide)
    result = match_star_widths_in_file(str(wide), str(tmp_path / "wide_out.fits"))
    assert result.applied and result.record()["sharpened"] == ["red"]
    with fits.open(tmp_path / "wide_out.fits") as hdul:
        assert hdul[0].header["OBJECT"] == "TEST"
    even = tmp_path / "even.fits"
    fits.PrimaryHDU(make_stack((2.1, 2.1, 2.1)), header=header).writeto(even)
    result = match_star_widths_in_file(str(even), str(tmp_path / "even_out.fits"))
    assert not result.applied and not (tmp_path / "even_out.fits").exists()
    missing = match_star_widths_in_file(str(tmp_path / "missing.fits"), str(tmp_path / "out.fits"))
    assert not missing.applied and "could not be read" in missing.reason


def test_the_width_of_a_known_gaussian_star_is_read_without_bias() -> None:
    """Round Gaussian stars of known width on a noisy sky read that width.

    The sky noise is large next to the stars' wings, so clipping the pixels
    below the sky to zero would read the stars too wide. A star drawn on
    pixels has a second moment larger by one twelfth of a pixel squared (the
    pixel's own spread), which the expected width includes.
    """
    size, sigma = 330, 1.5
    yy, xx = np.mgrid[0:size, 0:size]
    generator = np.random.default_rng(5)
    plane = generator.normal(100.0, 2.0, (size, size))
    positions = np.array([[25.0 + 35.0 * i, 25.0 + 35.0 * j] for i in range(9) for j in range(9)])
    for x, y in positions:
        plane += 300.0 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sigma**2))

    (width,) = measure_channel_widths(plane, positions)

    expected = 2.0 * np.sqrt(2.0 * np.log(2.0)) * np.sqrt(sigma**2 + 1.0 / 12.0)
    assert width == pytest.approx(expected, rel=0.03)
