"""Tests for choosing the sky level of a preview from a stack's data.

The images are synthetic: a flat sky with random noise, an optional smooth
glow standing in for a galaxy or nebula, and optional bright stars. The tests
check that the level follows the glow's strength, that stars do not move it,
and that an image with no measurable sky gets the fallback.
"""

from itertools import pairwise
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared.image_scaling import measure_sky, sky_level_for_peak
from astrometricslib.pipelines.stacking.sky_level import (
    DARKEST_SKY_LEVEL,
    EXTENDED_LIGHT_BRIGHTNESS,
    FALLBACK_SKY_LEVEL,
    LIGHTEST_SKY_LEVEL,
    choose_sky_level,
    choose_sky_level_for_file,
)

_SIZE = 384
_SKY = 0.005
_NOISE = 0.0002


def _image(glow_sigma: float = 0.0, stars: int = 0, seed: int = 1) -> np.ndarray:
    """Make a stack stand-in: sky, noise, a glow, and stars.

    Parameters
    ----------
    glow_sigma : `float`
        Height of the broad glow above the sky, in units of the noise.
    stars : `int`
        How many bright single-pixel stars to add.
    seed : `int`
        Seed of the random generator.

    Returns
    -------
    image : `numpy.ndarray`
        The image, with one very bright star in it as well, so the brightest
        pixel is far above the sky as in a real stack.
    """
    generator = np.random.default_rng(seed)
    image = _SKY + generator.normal(0.0, _NOISE, (_SIZE, _SIZE))
    rows, columns = np.mgrid[:_SIZE, :_SIZE]
    glow = np.exp(-((rows - _SIZE / 2) ** 2 + (columns - _SIZE / 2) ** 2) / (2.0 * 40.0**2))
    image += glow_sigma * _NOISE * glow
    for _ in range(stars):
        image[generator.integers(0, _SIZE), generator.integers(0, _SIZE)] = 0.4
    image[5, 5] = 1.0
    return image


def test_a_faint_glow_gets_the_lightest_sky() -> None:
    """Verify a target barely above the sky lifts the sky as far as allowed."""
    choice = choose_sky_level(_image(glow_sigma=0.5))

    assert choice.sky_level == pytest.approx(LIGHTEST_SKY_LEVEL)
    assert choice.extended_light_snr is not None
    assert choice.extended_light_snr < 1.0


def test_a_bright_glow_gets_the_darkest_sky() -> None:
    """Verify a target far above the sky leaves the sky as dark as allowed."""
    choice = choose_sky_level(_image(glow_sigma=300.0))

    assert choice.sky_level == pytest.approx(DARKEST_SKY_LEVEL)


def test_a_stronger_glow_never_lightens_the_sky() -> None:
    """Verify the level falls as the glow rises, and stays within bounds."""
    levels = [
        choose_sky_level(_image(glow_sigma=strength)).sky_level for strength in (0.5, 2, 5, 10, 30, 100, 300)
    ]

    assert all(later <= earlier + 1e-9 for earlier, later in pairwise(levels))
    assert levels[0] > levels[-1]
    assert all(DARKEST_SKY_LEVEL <= level <= LIGHTEST_SKY_LEVEL for level in levels)


def test_a_middle_glow_gets_a_middle_sky() -> None:
    """Verify a glow of a few noise units lands between the two limits."""
    level = choose_sky_level(_image(glow_sigma=4.0)).sky_level

    assert DARKEST_SKY_LEVEL < level < LIGHTEST_SKY_LEVEL


def test_stars_do_not_move_the_level() -> None:
    """Verify bright stars are ignored when judging the extended light."""
    plain = choose_sky_level(_image(glow_sigma=4.0)).sky_level
    starry = choose_sky_level(_image(glow_sigma=4.0, stars=200)).sky_level

    assert starry == pytest.approx(plain, abs=0.01)


def test_a_field_of_stars_alone_gets_the_lightest_sky() -> None:
    """Verify an empty field with stars has no extended light to protect."""
    choice = choose_sky_level(_image(glow_sigma=0.0, stars=200))

    assert choice.sky_level == pytest.approx(LIGHTEST_SKY_LEVEL)


def test_an_image_with_no_measurable_sky_gets_the_fallback() -> None:
    """Verify a blank image is reported, not guessed at."""
    choice = choose_sky_level(np.zeros((64, 64)))

    assert choice.sky_level == pytest.approx(FALLBACK_SKY_LEVEL)
    assert choice.extended_light_snr is None
    assert "could not be measured" in choice.reason


def test_an_image_with_missing_pixels_is_still_measured() -> None:
    """Verify not-a-number pixels do not break the choice."""
    image = _image(glow_sigma=300.0)
    image[:20, :20] = np.nan

    assert choose_sky_level(image).sky_level == pytest.approx(DARKEST_SKY_LEVEL)


def test_a_colour_image_is_averaged_across_its_channels() -> None:
    """Verify a three-channel image gives the same answer as its mean."""
    mono = _image(glow_sigma=4.0)
    colour = np.stack([mono, mono, mono])

    assert choose_sky_level(colour).sky_level == pytest.approx(choose_sky_level(mono).sky_level)


def test_a_fits_file_is_read_and_a_bad_file_gets_the_fallback(tmp_path: Path) -> None:
    """Verify the file route works and fails safe."""
    good = tmp_path / "stack.fits"
    fits.writeto(good, _image(glow_sigma=300.0).astype(np.float32))
    bad = tmp_path / "broken.fits"
    bad.write_bytes(b"not a fits file")

    assert choose_sky_level_for_file(str(good)).sky_level == pytest.approx(DARKEST_SKY_LEVEL)
    assert choose_sky_level_for_file(str(bad)).sky_level == pytest.approx(FALLBACK_SKY_LEVEL)
    assert choose_sky_level_for_file(str(tmp_path / "absent.fits")).sky_level == pytest.approx(
        FALLBACK_SKY_LEVEL
    )


def test_the_sky_measurement_ignores_blank_images_and_finds_the_noise() -> None:
    """Verify `measure_sky` skips blank images and finds the noise."""
    assert measure_sky(np.zeros((32, 32))) is None

    median, sigma, peak = measure_sky(_image())
    assert median == pytest.approx(_SKY, abs=1e-4)
    assert sigma == pytest.approx(_NOISE, rel=0.1)
    assert peak == pytest.approx(1.0)


def test_a_brighter_feature_leaves_the_sky_darker() -> None:
    """Verify the curve maths: more room above the sky means a darker sky."""
    near = sky_level_for_peak(0.001, 0.002, EXTENDED_LIGHT_BRIGHTNESS)
    far = sky_level_for_peak(0.001, 0.05, EXTENDED_LIGHT_BRIGHTNESS)

    assert far < near
    assert sky_level_for_peak(0.01, 0.01, EXTENDED_LIGHT_BRIGHTNESS) == pytest.approx(
        EXTENDED_LIGHT_BRIGHTNESS
    )
