"""Purpose: Unit tests for the display stretch in `ImageScaler`.

Description: `ImageScaler.scale_to_uint8` applies an Autostretch (black
point plus a midtones transfer curve, exactly Siril's) when no explicit
brightness range is given, and plain linear scaling when one is. These tests
pin the curve's maths against numbers computed here with numpy, and the edge
cases found on real data: images that are mostly exact zeros or float dust
must not blow out to white, and an image Siril's curve cannot be drawn for
must fall back to the old percentile stretch rather than fail.
"""

from pathlib import Path

import numpy as np
import pytest

from astrometricslib.pipelines.shared.image_scaling import (
    PREVIEW_SAMPLE_SEED,
    ImageScaler,
    _autostretch_parameters,
    _full_scale_of,
    _midtones_transfer_function,
    _scale_to_uint8_in_blocks,
    _solve_midtones_balance,
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
    assert stretched[50, 60] >= 240
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


def test_an_image_of_one_brightness_falls_back_to_a_percentile_stretch() -> None:
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

    _, black_point, white_point = ImageScaler.scale_to_uint8(image)
    parameters = ImageScaler.autostretch_parameters(image)

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
    """Whole and zoomed pictures report the stretch; a manual range not."""
    from astropy.io import fits

    from astrometricslib.pipelines.shared import image_conversions

    path = str(tmp_path / "sky.fits")
    fits.PrimaryHDU(_sky_with_stars().astype(np.float32)).writeto(path)

    zoomed = image_conversions.render_image(path, crop=(25, 25, 40), max_dimensions=200)
    assert zoomed.stretch_parameters is not None
    assert 0.0 < zoomed.stretch_parameters.midtones < 0.5
    whole = image_conversions.render_image(path, max_dimensions=200)
    assert whole.stretch_parameters is not None
    assert 0.0 < whole.stretch_parameters.midtones < 0.5
    manual = image_conversions.render_image(path, brightness=(75.0, 125.0), max_dimensions=200)
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


def _sky_with_optional_galaxy(with_galaxy: bool) -> np.ndarray:
    """Make a noisy flat sky, with a smooth galaxy over its left half if asked.

    Parameters
    ----------
    with_galaxy : `bool`
        Whether to add the galaxy.

    Returns
    -------
    image : `numpy.ndarray`
        The 600 by 600 image.
    """
    rng = np.random.default_rng(3)
    sky = 0.01 + 2e-4 * rng.standard_normal((600, 600))
    if with_galaxy:
        columns = np.arange(600)
        # A smooth ramp, 0.004 at the left edge falling to 0 at the middle.
        sky[:, :300] += 0.004 * (1.0 - columns[:300] / 300.0)
    return sky


def test_measure_sky_background_ignores_a_galaxy_over_half_the_frame() -> None:
    """A galaxy over half the frame leaves the sky level and noise alone."""
    from astrometricslib.pipelines.shared.image_scaling import measure_sky_background

    bare = measure_sky_background(_sky_with_optional_galaxy(False))
    galaxy = measure_sky_background(_sky_with_optional_galaxy(True))
    assert bare is not None and galaxy is not None
    assert galaxy[0] == pytest.approx(bare[0], abs=0.3 * bare[1])
    assert galaxy[1] == pytest.approx(bare[1], rel=0.1)
    assert bare[1] == pytest.approx(2e-4, rel=0.1)


def _siril_median_and_spread(values: np.ndarray, full_scale: float) -> tuple[float, float]:
    """Compute Siril's median and scaled MAD of the non-zero pixels.

    Returns
    -------
    median_and_spread : `tuple` [`float`, `float`]
        Both as fractions of `full_scale`; the MAD is multiplied by 1.4826.
    """
    nonzero = values[(values != 0) & np.isfinite(values)].astype(np.float64)
    median = np.median(nonzero)
    mad = np.median(np.abs(nonzero - median))
    return float(median / full_scale), float(mad / full_scale * 1.4826)


def _siril_midtones(median_above_black: float) -> float:
    """Compute Siril's midtones balance for a target of 0.25, independently.

    Returns
    -------
    midtones : `float`
        Siril's `MTF(median - black, 0.25)`.
    """
    return median_above_black * (0.25 - 1.0) / ((2 * 0.25 - 1.0) * median_above_black - 0.25)


def _noisy_sky(median: float, noise: float, shape: tuple[int, ...] = (200, 200), seed: int = 5) -> np.ndarray:
    """Make a Gaussian sky, with a few stars at the top left.

    Returns
    -------
    image : `numpy.ndarray`
        The float64 image.
    """
    image = median + noise * np.random.default_rng(seed).standard_normal(shape)
    image[:3, :3] = median * 20.0
    return image


def test_float_image_uses_siril_white_black_and_midtones() -> None:
    """A float image gets white 1.0 and black median - 2.8 spreads."""
    image = _noisy_sky(0.02, 0.002)
    image[10:13, 10:13] = 2.0
    median, spread = _siril_median_and_spread(image, 1.0)

    parameters = ImageScaler.autostretch_parameters(image)

    assert parameters is not None
    assert parameters.white_point == pytest.approx(1.0)
    assert parameters.black_point == pytest.approx(median - 2.8 * spread, rel=1e-9)
    assert parameters.midtones == pytest.approx(_siril_midtones(median - parameters.black_point))


def test_black_point_is_clamped_at_zero() -> None:
    """A spread so wide that median - 2.8 spreads is negative gives black 0."""
    image = np.abs(_noisy_sky(0.01, 0.02))
    median, spread = _siril_median_and_spread(image, 1.0)
    assert median - 2.8 * spread < 0.0

    parameters = ImageScaler.autostretch_parameters(image)

    assert parameters is not None
    assert parameters.black_point == pytest.approx(0.0)


def test_uint16_image_uses_the_16_bit_full_scale() -> None:
    """Counts are scaled by 65535: white is 65535 and black is in counts."""
    counts = np.clip(_noisy_sky(2000.0, 60.0), 0, 65535).astype(np.uint16)
    median, spread = _siril_median_and_spread(counts, 65535.0)

    parameters = ImageScaler.autostretch_parameters(counts)

    assert _full_scale_of(counts) == pytest.approx(65535.0)
    assert parameters is not None
    assert parameters.white_point == pytest.approx(65535.0)
    assert parameters.black_point == pytest.approx((median - 2.8 * spread) * 65535.0, rel=1e-9)
    assert parameters.midtones == pytest.approx(_siril_midtones(median - parameters.black_point / 65535.0))


def test_full_scale_by_data_type() -> None:
    """Floats are 1.0, 8-bit integers 255 and other integers 65535."""
    assert _full_scale_of(np.zeros(3, dtype=np.float32)) == pytest.approx(1.0)
    assert _full_scale_of(np.zeros(3, dtype=np.uint8)) == pytest.approx(255.0)
    assert _full_scale_of(np.zeros(3, dtype=np.uint16)) == pytest.approx(65535.0)


def test_exact_zeros_do_not_count_in_the_statistics() -> None:
    """Exact zeros are left out, so a black border changes nothing."""
    image = _noisy_sky(0.02, 0.002)
    bordered = np.zeros((300, 300))
    bordered[50:250, 50:250] = image

    plain = ImageScaler.autostretch_parameters(image)
    framed = ImageScaler.autostretch_parameters(bordered)

    assert plain is not None and framed is not None
    assert framed.black_point == pytest.approx(plain.black_point)
    assert framed.midtones == pytest.approx(plain.midtones)


def test_bright_background_takes_the_inverted_case() -> None:
    """With the median above 0.5, black is 0 and white is above the median."""
    image = np.clip(_noisy_sky(0.7, 0.02), 0.0, 1.0)
    median, spread = _siril_median_and_spread(image, 1.0)

    parameters = ImageScaler.autostretch_parameters(image)

    assert parameters is not None
    assert parameters.black_point == pytest.approx(0.0)
    assert parameters.white_point == pytest.approx(min(median + 2.8 * spread, 1.0))
    assert parameters.midtones == pytest.approx(1.0 - _siril_midtones(parameters.white_point - median))


def test_colour_image_uses_the_linked_parameters() -> None:
    """One set of numbers: the channels' statistics are averaged."""
    red = _noisy_sky(0.02, 0.002, seed=1)
    green = _noisy_sky(0.04, 0.004, seed=2)
    blue = _noisy_sky(0.06, 0.003, seed=3)
    image = np.stack([red, green, blue], axis=-1)
    statistics = [_siril_median_and_spread(channel, 1.0) for channel in (red, green, blue)]
    expected_black = np.mean([median - 2.8 * spread for median, spread in statistics])
    expected_median = np.mean([median for median, _ in statistics])

    parameters = ImageScaler.autostretch_parameters(image)

    assert parameters is not None
    assert parameters.black_point == pytest.approx(expected_black, rel=1e-9)
    assert parameters.midtones == pytest.approx(_siril_midtones(expected_median - expected_black))


def test_zero_mad_uses_siril_guard_value() -> None:
    """A MAD of exactly 0 is replaced by 0.001, so black is median - 0.0028."""
    image = np.full((100, 100), 0.1)
    image[:10, :10] = 0.5

    parameters = ImageScaler.autostretch_parameters(image)

    assert parameters is not None
    assert parameters.black_point == pytest.approx(0.1 - 2.8 * 0.001)


def test_autostretch_command_uses_measured_sky() -> None:
    """A measured sky gives an mtf command with the black point below it."""
    from astrometricslib.pipelines.stacking.post_processing.stretch_sky import choose_sky_stretch

    command = choose_sky_stretch(0.2, False, (0.01, 1e-4)).command().split()
    assert command[0] == "mtf" and command[3] == "1"
    assert float(command[1]) == pytest.approx(0.01 - 2.8e-4)
    assert choose_sky_stretch(0.2).command().startswith("autostretch")
