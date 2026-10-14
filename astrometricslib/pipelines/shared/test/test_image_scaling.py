"""Purpose: Unit tests for the display stretch in `ImageScaler`.

Description: `ImageScaler.scale_to_uint8` applies an Autostretch (black
point plus a midtones transfer curve, the same idea as Siril's and
PixInsight's) when no explicit brightness range is given, and plain linear
scaling when one is. These tests pin the curve's maths and the edge cases
found on real data: images that are mostly exact zeros or float dust must
not blow out to white, and an image with no measurable background must
fall back to the old percentile stretch rather than fail.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.shared.image_scaling import (
    PREVIEW_SAMPLE_SEED,
    ImageScaler,
    _autostretch_parameters,
    _midtones_transfer_function,
    _scale_to_uint8_in_blocks,
    _solve_midtones_balance,
    measure_sky,
    midtones_balance_for,
    percentile_sample,
    white_fraction_after_autostretch,
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


def test_the_public_midtones_solver_matches_the_private_one() -> None:
    """The public name gives the balance that maps a value to a brightness."""
    balance = midtones_balance_for(0.3, 0.5)
    assert balance == pytest.approx(_solve_midtones_balance(0.3, 0.5))
    assert float(_midtones_transfer_function(np.array([0.3]), balance)[0]) == pytest.approx(0.5)


def test_a_star_field_has_almost_no_white_after_the_autostretch() -> None:
    """Only the star cores are white, far below one percent of the image."""
    fraction = white_fraction_after_autostretch(_sky_with_stars(), 0.25)
    assert fraction is not None
    assert fraction < 0.01


def test_a_large_bright_disc_is_mostly_white_after_the_autostretch() -> None:
    """A big bright object is pushed to white when the sky is lifted."""
    generator = np.random.default_rng(3)
    y, x = np.mgrid[0:300, 0:300]
    image = 0.0002 + 0.00004 * generator.standard_normal((300, 300))
    image[np.hypot(y - 150, x - 150) < 60] += 0.4
    fraction = white_fraction_after_autostretch(image, 0.11)
    assert fraction is not None
    assert fraction > 0.05


def test_the_white_fraction_is_none_without_a_measurable_sky() -> None:
    """A blank image has nothing to anchor the stretch to."""
    assert white_fraction_after_autostretch(np.zeros((50, 50)), 0.2) is None


def _large_sky_with_stars(seed: int = 0) -> np.ndarray:
    """Build a sky big enough that the sky sample is smaller than the image.

    Returns
    -------
    image : `numpy.ndarray`
        A 1000 x 1000 image (1 million pixels, twice the sample size): sky
        near 1000 with noise of about 20, a faint gradient, and one very
        bright pixel.
    """
    rng = np.random.default_rng(seed)
    image = 1000.0 + rng.normal(0.0, 20.0, size=(1000, 1000))
    image += np.linspace(0.0, 30.0, 1000)[:, None]
    image[10, 10] = 60000.0
    return image


def test_a_sampled_sky_measurement_matches_the_full_one_closely() -> None:
    """Measuring from a sample finds nearly the same sky level and noise."""
    image = _large_sky_with_stars()

    full_median, full_sigma, _ = measure_sky(image)
    sampled_median, sampled_sigma, _ = measure_sky(image, sample_pixels=True)

    assert sampled_median == pytest.approx(full_median, abs=0.05 * full_sigma)
    assert sampled_sigma == pytest.approx(full_sigma, rel=0.02)


def test_a_sampled_sky_measurement_still_finds_the_true_brightest_pixel() -> None:
    """The brightest pixel comes from the whole image, not from the sample."""
    image = _large_sky_with_stars()

    _, _, peak = measure_sky(image, sample_pixels=True)

    assert peak == pytest.approx(60000.0)


def test_a_sampled_sky_measurement_gives_the_same_answer_every_time() -> None:
    """The same image always picks the same sample."""
    image = _large_sky_with_stars()

    assert measure_sky(image, sample_pixels=True) == measure_sky(image, sample_pixels=True)


def test_small_images_are_measured_in_full_even_when_sampling_is_asked_for() -> None:
    """An image smaller than the sample has nothing to sample."""
    image = _sky_with_stars()

    assert measure_sky(image, sample_pixels=True) == measure_sky(image)


def test_a_sampled_stretch_gives_the_same_picture_to_within_one_grey_level() -> None:
    """Sampling the sky must not visibly change the picture."""
    image = _large_sky_with_stars()

    full, _, _ = ImageScaler.scale_to_uint8(image)
    sampled, _, _ = ImageScaler.scale_to_uint8(image, sample_sky=True)

    assert np.abs(full.astype(int) - sampled.astype(int)).max() <= 1


@pytest.mark.parametrize("shape", [(7, 5), (4000, 3), (1, 9), (13, 4, 3)])
@pytest.mark.parametrize("midtones", [None, 0.2])
def test_scaling_in_blocks_matches_scaling_all_at_once(
    shape: tuple[int, ...], midtones: float | None
) -> None:
    """Working a block of rows at a time changes no pixel."""
    rng = np.random.default_rng(1)
    image = rng.uniform(-50.0, 300.0, size=shape)
    vmin, vmax = 0.0, 250.0

    whole = np.clip((image - vmin) / (vmax - vmin), 0.0, 1.0)
    if midtones is not None:
        whole = _midtones_transfer_function(whole, midtones)
    expected = (whole * 255.0).astype(np.uint8)

    np.testing.assert_array_equal(_scale_to_uint8_in_blocks(image, vmin, vmax, midtones), expected)


def test_the_autostretch_parameters_are_the_ones_the_picture_used() -> None:
    """The reported points match the drawing's; the sky lands at 25%."""
    image = _sky_with_stars()

    _, black_point, white_point = ImageScaler.scale_to_uint8(image, sample_sky=True)
    parameters = ImageScaler.autostretch_parameters(image, sample_sky=True)

    assert parameters.black_point == pytest.approx(black_point)
    assert parameters.white_point == pytest.approx(white_point)
    normalized_sky = (np.median(image) - black_point) / (white_point - black_point)
    assert _midtones_transfer_function(np.array([normalized_sky]), parameters.midtones)[0] == pytest.approx(
        0.25, abs=0.02
    )


def test_an_image_without_sky_has_no_autostretch_parameters() -> None:
    """A flat image cannot anchor a stretch, so none is reported."""
    assert ImageScaler.autostretch_parameters(np.zeros((50, 50))) is None


def test_the_viewer_pictures_carry_the_stretch(tmp_path: Path) -> None:
    """Both picture kinds report the stretch; a manual range reports none."""
    from astropy.io import fits

    from astrometricslib.pipelines.shared import image_conversions

    path = str(tmp_path / "sky.fits")
    fits.PrimaryHDU(_sky_with_stars().astype(np.float32)).writeto(path)

    viewable = image_conversions.render_viewable_image(path, 200, True, None, None, None)
    assert viewable.stretch_parameters is not None
    assert 0.0 < viewable.stretch_parameters.midtones < 0.5
    rendered = image_conversions.render_data_url(path, max_dimensions=200)
    assert rendered.model_dump(by_alias=True)["stretchParameters"]["blackPoint"] == pytest.approx(
        viewable.stretch_parameters.black_point
    )
    manual = image_conversions.render_viewable_image(path, 200, True, 100.0, 50.0, None)
    assert manual.stretch_parameters is None


def test_percentile_sample_is_identical_between_calls() -> None:
    """Two calls with the default seed pick exactly the same pixels."""
    image = np.arange(400 * 400, dtype=np.float64).reshape(400, 400)

    assert np.array_equal(percentile_sample(image), percentile_sample(image))


def test_percentile_sample_is_a_random_looking_subset() -> None:
    """The sample is spread over the whole image, not the first N pixels."""
    image = np.arange(400 * 400, dtype=np.float64).reshape(400, 400)

    sample = percentile_sample(image)

    assert sample.size == 10000
    assert not np.array_equal(sample, image.flat[:10000])
    assert sample.max() > image.size * 0.9
    assert sample.min() < image.size * 0.1
    assert percentile_sample(image, seed=PREVIEW_SAMPLE_SEED + 1).tolist() != sample.tolist()


def test_percentile_stretch_of_a_large_image_is_repeatable() -> None:
    """Two plain percentile stretches of one large image give identical output.

    An image of exact zeros plus a few bright pixels has no measurable
    sky, so the scaler falls back to the sampled percentile stretch.
    """
    image = np.zeros((400, 400))
    image.flat[::97] = np.linspace(1.0, 1000.0, image.flat[::97].size)

    first = ImageScaler.scale_to_uint8(image)
    second = ImageScaler.scale_to_uint8(image)

    assert np.array_equal(first[0], second[0])
    assert first[1:] == second[1:]
