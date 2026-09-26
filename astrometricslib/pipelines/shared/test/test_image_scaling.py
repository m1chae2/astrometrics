"""Purpose: Unit tests for the display stretch in `ImageScaler`.

Description: `ImageScaler.scale_to_uint8` applies an Autostretch (black
point plus a midtones transfer curve, the same idea as Siril's and
PixInsight's) when no explicit brightness range is given, and plain linear
scaling when one is. These tests pin the curve's maths and the edge cases
found on real data: images that are mostly exact zeros or float dust must
not blow out to white, and an image with no measurable background must
fall back to the old percentile stretch rather than fail.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.shared.image_scaling import (
    ImageScaler,
    _autostretch_parameters,
    _midtones_transfer_function,
    _solve_midtones_balance,
)


def _sky_with_stars(seed: int = 0) -> np.ndarray:
    """Build a noisy sky with two bright point sources.

    Returns
    -------
    image : `numpy.ndarray`
        A 200 x 200 image: sky near 100 with noise of about 5, and two
        stars of about 5000.
    """
    rng = np.random.default_rng(seed)
    image = 100.0 + rng.normal(0.0, 5.0, size=(200, 200))
    image[50, 60] = 5000.0
    image[120, 140] = 4000.0
    return image


def test_midtones_curve_keeps_black_and_white_fixed() -> None:
    """Verify 0 stays 0 and 1 stays 1 for any midtones balance."""
    curve = _midtones_transfer_function(np.array([0.0, 1.0]), 0.05)

    assert curve[0] == pytest.approx(0.0)
    assert curve[1] == pytest.approx(1.0)


def test_midtones_curve_maps_the_balance_point_to_one_half() -> None:
    """Verify the input equal to the midtones balance comes out at 0.5."""
    assert _midtones_transfer_function(np.array([0.2]), 0.2)[0] == pytest.approx(0.5)


def test_solved_midtones_put_the_median_on_the_target_brightness() -> None:
    """Verify the solved balance maps a dark median to the target."""
    midtones = _solve_midtones_balance(0.01, 0.25)

    assert 0.0 < midtones < 1.0
    assert _midtones_transfer_function(np.array([0.01]), midtones)[0] == pytest.approx(0.25)


def test_autostretch_puts_the_sky_near_the_target_brightness() -> None:
    """Verify the typical sky pixel lands near 25% grey and stars at white."""
    image = _sky_with_stars()

    stretched, black_point, white_point = ImageScaler.scale_to_uint8(image)

    assert stretched.dtype == np.uint8
    assert np.median(stretched) == pytest.approx(0.25 * 255.0, abs=6.0)
    assert stretched[50, 60] == 255
    assert black_point < np.median(image) < white_point


def test_an_explicit_range_stays_linear() -> None:
    """Verify a caller-supplied vmin/vmax is a plain linear ramp."""
    image = np.array([[0.0, 50.0, 100.0]])

    stretched, black_point, white_point = ImageScaler.scale_to_uint8(image, vmin=0.0, vmax=100.0)

    assert (black_point, white_point) == (0.0, 100.0)
    assert stretched.tolist() == [[0, 127, 255]]


def test_stretch_off_stays_linear_over_the_full_range() -> None:
    """Verify stretch=False scales between the true minimum and maximum."""
    image = np.array([[10.0, 60.0, 110.0]])

    stretched, black_point, white_point = ImageScaler.scale_to_uint8(image, stretch=False)

    assert (black_point, white_point) == (10.0, 110.0)
    assert stretched.tolist() == [[0, 127, 255]]


def test_float_dust_does_not_blow_the_background_out_to_white() -> None:
    """Verify a mostly-empty stack with 1e-31 dust keeps a dark background.

    Reproduces a real Siril-registered stack: over 99.9% of its pixels were
    exact zeros or interpolation dust near 1e-31, and only star halos held
    real signal. Measuring the background from the dust collapsed the
    stretch and turned the whole frame white.
    """
    rng = np.random.default_rng(1)
    image = rng.normal(0.0, 1e-31, size=(300, 300))
    rows, columns = np.mgrid[0:300, 0:300]
    for star_row, star_column in ((100, 100), (200, 220)):
        image += 0.1 * np.exp(-(((rows - star_row) ** 2 + (columns - star_column) ** 2) / (2 * 3.0**2)))

    stretched, _, _ = ImageScaler.scale_to_uint8(image)

    assert np.median(stretched) < 128
    assert stretched[100, 100] >= 250


def test_an_image_with_no_background_spread_falls_back_to_a_percentile_stretch() -> None:
    """Verify a constant image does not raise and returns a valid array."""
    image = np.full((50, 50), 7.0)

    assert _autostretch_parameters(image) is None
    stretched, _, _ = ImageScaler.scale_to_uint8(image)

    assert stretched.shape == (50, 50)
    assert stretched.dtype == np.uint8


def test_an_empty_image_has_no_autostretch_parameters() -> None:
    """Verify a zero-size array is refused rather than raising."""
    assert _autostretch_parameters(np.empty((0, 0))) is None
