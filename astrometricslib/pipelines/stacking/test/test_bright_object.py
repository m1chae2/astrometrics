"""Tests for finding stacks of bright extended objects and stretching them.

The normal preview stretch whites out a large bright object such as the Moon.
These tests build small synthetic stacks (a star field, a bright disc on a
dark sky) and check that only the disc is recognised, that its stretch keeps
the sky dark and the disc inside the brightness range, and that unreadable or
blank input falls back to the normal stretch.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.stacking import bright_object
from astrometricslib.pipelines.stacking.bright_object import (
    choose_bright_object_stretch,
    choose_bright_object_stretch_for_file,
)

SIZE = 400


def star_field(seed: int = 0) -> np.ndarray:
    """Build a noisy sky with a handful of small Gaussian stars.

    Returns
    -------
    image : `numpy.ndarray`
        A square image: sky near 0.01 with 1% noise and 12 bright stars.
    """
    generator = np.random.default_rng(seed)
    image = 0.01 + 0.0001 * generator.standard_normal((SIZE, SIZE))
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    for _ in range(12):
        cy, cx = generator.integers(20, SIZE - 20, 2)
        image += generator.uniform(0.2, 0.8) * np.exp(-((y - cy) ** 2 + (x - cx) ** 2) / (2 * 2.0**2))
    return image


def bright_disc(brightness: float = 0.4, sky: float = 0.0002) -> np.ndarray:
    """Build a dark sky with a large disc with a little surface texture.

    Parameters
    ----------
    brightness : `float`
        The disc's mean level.
    sky : `float`
        The sky level. The noise is a fifth of it.

    Returns
    -------
    image : `numpy.ndarray`
        A square image with a disc 160 pixels across (about 12% of it).
    """
    generator = np.random.default_rng(1)
    y, x = np.mgrid[0:SIZE, 0:SIZE]
    inside = np.hypot(y - SIZE / 2, x - SIZE / 2) < 80
    texture = 1.0 + 0.3 * np.sin(x / 7.0) * np.cos(y / 9.0)
    image = sky + 0.2 * sky * generator.standard_normal((SIZE, SIZE))
    image[inside] += brightness * texture[inside]
    return image


def test_a_star_field_uses_the_normal_stretch() -> None:
    """A few small stars do not white out enough of the picture."""
    assert choose_bright_object_stretch(star_field()) is None


def test_a_bright_disc_gets_its_own_stretch() -> None:
    """A large bright disc is recognised and the stretch fits the disc."""
    stretch = choose_bright_object_stretch(bright_disc())
    assert stretch is not None
    assert stretch.white_fraction >= bright_object.SATURATED_FRACTION_LIMIT
    assert stretch.black_point == pytest.approx(0.0002, abs=0.0002)
    assert 0.3 < stretch.white_point <= 1.0
    assert 0.0 < stretch.midtones < 1.0
    assert stretch.scale == pytest.approx(1.0)


def test_the_typical_disc_pixel_lands_at_the_chosen_brightness() -> None:
    """The stretch puts the object's median at the display brightness."""
    image = bright_disc()
    stretch = choose_bright_object_stretch(image)
    assert stretch is not None
    disc = image[np.hypot(*(np.mgrid[0:SIZE, 0:SIZE] - SIZE / 2)) < 80]
    x = np.clip((np.median(disc) - stretch.black_point) / (stretch.white_point - stretch.black_point), 0, 1)
    m = stretch.midtones
    brightness = (m - 1.0) * x / ((2.0 * m - 1.0) * x - m)
    assert brightness == pytest.approx(bright_object.BRIGHT_OBJECT_BRIGHTNESS, abs=0.1)


def test_a_stack_with_values_above_one_is_scaled_down() -> None:
    """The white point is kept at or below 1 by multiplying the image first."""
    stretch = choose_bright_object_stretch(bright_disc(brightness=3.0))
    assert stretch is not None
    assert stretch.scale < 1.0
    assert stretch.white_point == pytest.approx(1.0)
    assert 0.0 <= stretch.black_point < stretch.white_point


def test_a_colour_stack_is_averaged_first() -> None:
    """A three-channel disc gives the same answer as its mean image."""
    disc = bright_disc()
    colour = np.stack([disc, disc, disc])
    plain = choose_bright_object_stretch(disc)
    assert plain is not None
    stretched = choose_bright_object_stretch(colour)
    assert stretched is not None
    assert stretched.white_point == pytest.approx(plain.white_point)


def test_an_image_with_no_measurable_sky_uses_the_normal_stretch() -> None:
    """A blank image cannot be anchored, so it is left to the normal route."""
    assert choose_bright_object_stretch(np.zeros((SIZE, SIZE))) is None


def test_a_one_dimensional_input_is_refused() -> None:
    """Only images are accepted."""
    assert choose_bright_object_stretch(np.arange(100.0)) is None


def test_a_small_bright_patch_is_not_an_extended_object() -> None:
    """A few hundred bright pixels are too few to measure as an object."""
    image = bright_disc(brightness=0.4)
    image[:] = 0.0002 + 0.00004 * np.random.default_rng(2).standard_normal(image.shape)
    image[10:20, 10:20] += 0.5
    assert choose_bright_object_stretch(image) is None


def test_a_file_is_read_and_judged(tmp_path: Path) -> None:
    """The file version reads a FITS stack and judges it the same way."""
    path = tmp_path / "moon.fits"
    fits.writeto(path, bright_disc().astype(np.float32))
    assert choose_bright_object_stretch_for_file(str(path)) is not None
    fits.writeto(tmp_path / "stars.fits", star_field().astype(np.float32))
    assert choose_bright_object_stretch_for_file(str(tmp_path / "stars.fits")) is None


def test_an_unreadable_file_uses_the_normal_stretch(tmp_path: Path) -> None:
    """A file that is not FITS is not an error."""
    path = tmp_path / "broken.fits"
    path.write_bytes(b"not fits")
    assert choose_bright_object_stretch_for_file(str(path)) is None
