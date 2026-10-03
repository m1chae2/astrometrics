"""Tests for the star toning of stretched preview pictures.

The toning has two curves. These tests check what each one promises: the
soft highlights leave the shadows alone and never reach white, and the star
dimming changes stars, faint ones most, while the background and nebulae stay
as they were.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking.post_processing import star_tone

SIZE = 200
BACKGROUND = 0.2


def gaussian_star(centre_y: int, centre_x: int, peak: float, width: float = 1.6) -> np.ndarray:
    """Make an image holding one round star.

    Returns
    -------
    star : `numpy.ndarray`
        A square image that is `peak` at the centre and falls off with `width`.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    return peak * np.exp(-((y - centre_y) ** 2 + (x - centre_x) ** 2) / (2 * width**2))


def test_soft_highlights_leave_values_below_the_knee_alone() -> None:
    """Shadows and midtones pass through unchanged."""
    values = np.linspace(0.0, star_tone.HIGHLIGHT_KNEE, 20)

    assert star_tone.soft_highlights(values) == pytest.approx(values)


def test_soft_highlights_never_reach_white_and_keep_the_order() -> None:
    """The curve rises with its input and tops out below 1.0."""
    values = np.linspace(0.0, 1.0, 200)

    toned = star_tone.soft_highlights(values)

    assert np.all(np.diff(toned) > 0)
    assert toned[-1] < 1.0
    knee = star_tone.HIGHLIGHT_KNEE
    assert toned[-1] == pytest.approx(knee + (1 - knee) * np.tanh(1.0))


def test_dimming_with_power_one_changes_nothing() -> None:
    """A power of 1 is the identity."""
    image = BACKGROUND + gaussian_star(100, 100, 0.6)

    assert star_tone.dim_stars(image, power=1.0) == pytest.approx(image, abs=1e-6)


def test_a_flat_background_is_left_alone() -> None:
    """An image with no stars does not change."""
    image = np.full((SIZE, SIZE), BACKGROUND)

    assert star_tone.dim_stars(image) == pytest.approx(image)


def test_faint_stars_are_dimmed_more_than_bright_ones() -> None:
    """The share of a star's light that is kept grows with its brightness."""
    image = BACKGROUND + gaussian_star(50, 50, 0.15) + gaussian_star(150, 150, 0.7)

    toned = star_tone.dim_stars(image)

    faint_kept = (toned[50, 50] - BACKGROUND) / 0.15
    bright_kept = (toned[150, 150] - BACKGROUND) / 0.7
    assert faint_kept < bright_kept < 1.0 + 1e-6
    assert faint_kept < 0.5


def test_low_contrast_structure_is_left_alone() -> None:
    """A small feature only 0.03 above the background is not dimmed."""
    image = BACKGROUND + gaussian_star(100, 100, 0.03, width=3.0)

    assert star_tone.dim_stars(image) == pytest.approx(image, abs=1e-4)


def test_a_clear_star_is_dimmed_in_full() -> None:
    """A star well above the full-dimming limit follows the power curve."""
    image = BACKGROUND + gaussian_star(100, 100, 0.5)

    toned = star_tone.dim_stars(image)

    room = 1.0 - BACKGROUND
    assert toned[100, 100] == pytest.approx(
        BACKGROUND + room * (0.5 / room) ** star_tone.STAR_DIMMING_POWER, abs=0.01
    )


def test_a_nebula_much_larger_than_a_star_is_barely_dimmed() -> None:
    """A broad glow counts as background and keeps its brightness to 0.02.

    The very top of a smooth glow sits a little above the opening of it, so
    that sliver is treated as star light and dimmed. For this glow, 0.25 above
    the background and 60 pixels wide, the loss is about 0.013.
    """
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    nebula = BACKGROUND + 0.25 * np.exp(-((y - 100) ** 2 + (x - 100) ** 2) / (2 * 30.0**2))

    toned = star_tone.dim_stars(nebula)

    assert toned[100, 100] == pytest.approx(nebula[100, 100], abs=0.02)


def test_tone_stars_keeps_the_shape_and_range_of_a_colour_image() -> None:
    """A channels-first colour image is toned plane by plane."""
    plane = BACKGROUND + gaussian_star(100, 100, 0.8)
    image = np.stack([plane, plane, plane])

    toned = star_tone.tone_stars(image)

    assert toned.shape == image.shape
    assert toned.dtype == np.float32
    assert toned.min() >= 0.0
    assert toned.max() < 1.0


def test_tone_stars_in_file_writes_a_toned_copy_with_the_header(tmp_path: Path) -> None:
    """The saved picture is toned and keeps the input's header."""
    source = tmp_path / "stretched.fits"
    image = (BACKGROUND + gaussian_star(100, 100, 0.8)).astype(np.float32)
    header = fits.Header()
    header["OBJECT"] = "M 52"
    fits.writeto(source, image, header)
    result = tmp_path / "toned.fits"

    assert star_tone.tone_stars_in_file(str(source), str(result))

    with fits.open(result, memmap=False) as hdul:
        assert hdul[0].header["OBJECT"] == "M 52"
        assert hdul[0].data[100, 100] < image[100, 100]
        assert hdul[0].data[0, 0] == pytest.approx(BACKGROUND, abs=1e-4)


def test_tone_stars_in_file_reports_an_unreadable_input(tmp_path: Path) -> None:
    """A missing file gives `False` instead of an exception."""
    assert not star_tone.tone_stars_in_file(str(tmp_path / "missing.fits"), str(tmp_path / "out.fits"))
