"""Tests for the neighbour-wing stage (`neighbor_trail_deblending`).

Each test builds synthetic streaks from a "true" blur that the module does not
know: lopsided (the right wing is heavier than the left) and wider farther
along the streak. The blur is measured on an isolated reference star at one
focus, then used on a crowded pair taken at a slightly different focus, as
happens on a real night. The true answer is known, so the tests can check the
light the bright star puts into the faint star's box.
"""

import numpy as np

from astrometricslib.pipelines.spectroscopy.pre_processing.neighbor_trail_deblending import (
    MAXIMUM_TRUSTED_RELATIVE_RESIDUAL,
    EmpiricalBlur,
    NeighborFit,
    build_band_profiles,
    enclosed_fraction,
    fit_neighbor_amplitudes,
    measure_empirical_blur,
    neighbor_wing_flux,
    pixel_profile,
    subtract_neighbor_wings,
)

ROWS = 400
CROSS_PIXELS = 220
BRIGHT_CENTRE_PX = 130.0
GAP_PX = -17.0  # the faint star is 17 px to the left of the bright one
FAINT_CENTRE_PX = BRIGHT_CENTRE_PX + GAP_PX
REFERENCE_CENTRE_PX = 110.0
BOX_HALF_WIDTH_PX = 8.5
SKY_LEVEL = 5.0
BAND_EDGES = np.arange(0, ROWS + 1, 50)
FINE_STEP_PX = 0.05


def true_pixel_light(row: int, scale: float, centre_px: float) -> np.ndarray:
    """Give the fraction of a star's light in each pixel of one row.

    The blur is a Gaussian core that widens along the streak plus a wing that
    is heavier on the right than on the left, all stretched by `scale` (the
    focus change).

    Returns
    -------
    light : `numpy.ndarray`
        The fraction of the star's light in each cross-streak pixel.
    """
    fine = np.arange(-90.0, 90.0 + FINE_STEP_PX, FINE_STEP_PX)
    x = fine / scale
    core_sigma = 1.3 + 0.3 * row / ROWS
    core = np.exp(-0.5 * (x / core_sigma) ** 2)
    core /= core.sum()
    wing_width = np.where(x < 0, 9.0, 12.0)
    wing = (1.0 + (x / wing_width) ** 2) ** -1.6
    wing *= np.where(x < 0, 0.8, 1.0)
    wing /= wing.sum()
    density = 0.95 * core + 0.05 * wing
    cumulative = np.cumsum(density) / density.sum()
    cross = np.arange(CROSS_PIXELS, dtype=float)
    high = np.interp(cross + 0.5 - centre_px, fine, cumulative, left=0.0, right=1.0)
    low = np.interp(cross - 0.5 - centre_px, fine, cumulative, left=0.0, right=1.0)
    return high - low


def make_image(
    stars: list[tuple[float, np.ndarray]], scale: float, noise_sigma: float, seed: int
) -> np.ndarray:
    """Build an image of streaks, one per star, with a sky level and noise.

    Returns
    -------
    image : `numpy.ndarray`
        The image, rows by cross pixels.
    """
    image = np.full((ROWS, CROSS_PIXELS), SKY_LEVEL)
    for row in range(ROWS):
        for centre, spectrum in stars:
            image[row] += spectrum[row] * true_pixel_light(row, scale, centre)
    if noise_sigma > 0:
        image += np.random.default_rng(seed).normal(0.0, noise_sigma, image.shape)
    return image


def measure_reference_blur() -> EmpiricalBlur:
    """Measure the blur on an isolated star at focus scale 1.0.

    Returns
    -------
    blur : `EmpiricalBlur`
        The measured blur.
    """
    image = make_image([(REFERENCE_CENTRE_PX, np.full(ROWS, 6000.0))], 1.0, 1.0, seed=1)
    return measure_empirical_blur(
        image, True, np.full(ROWS, REFERENCE_CENTRE_PX), BAND_EDGES, 0.0, half_width_px=75
    )


def make_pair(scale: float = 1.1) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Build a bright and a faint streak, at a different focus.

    Returns
    -------
    image, bright_spectrum, faint_spectrum, bright_only : `tuple`
        The pair image, each star's true light per row, and an image of the
        bright star alone (the exact source of the contamination).
    """
    bright_spectrum = np.full(ROWS, 8000.0)
    faint_spectrum = np.linspace(3000.0, 250.0, ROWS)
    image = make_image(
        [(BRIGHT_CENTRE_PX, bright_spectrum), (FAINT_CENTRE_PX, faint_spectrum)],
        scale,
        noise_sigma=1.0,
        seed=2,
    )
    bright_only = make_image([(BRIGHT_CENTRE_PX, bright_spectrum)], scale, noise_sigma=0.0, seed=0)
    return image, bright_spectrum, faint_spectrum, bright_only


def fit_the_pair(image: np.ndarray, blur: EmpiricalBlur, star_offsets: list[float]) -> NeighborFit:
    """Fit the reference blur to the crowded pair.

    Returns
    -------
    fit : `NeighborFit`
        The fit.
    """
    offsets, profiles, positions = build_band_profiles(
        image, True, np.full(ROWS, BRIGHT_CENTRE_PX), BAND_EDGES, 0.0, half_width_px=75
    )
    return fit_neighbor_amplitudes(
        blur, offsets, profiles, positions, np.array(star_offsets), noise_floor=0.5
    )


def box_sum(source: np.ndarray, centre: float) -> np.ndarray:
    """Add up the light in a box, less the sky, for every row.

    Returns
    -------
    box_flux : `numpy.ndarray`
        The sky-subtracted light in the box, one value per row.
    """
    low, high = round(centre - BOX_HALF_WIDTH_PX), round(centre + BOX_HALF_WIDTH_PX)
    return source[:, low : high + 1].sum(axis=1) - SKY_LEVEL * (high - low + 1)


def predicted_wing(blur: EmpiricalBlur, fit: NeighborFit, bright_box: np.ndarray) -> np.ndarray:
    """Predict the bright star's light inside the faint star's box, row by row.

    Returns
    -------
    wing : `numpy.ndarray`
        The predicted light, one value per row.
    """
    half = np.full(ROWS, BOX_HALF_WIDTH_PX)
    return neighbor_wing_flux(
        blur,
        fit.dilation,
        np.arange(ROWS, dtype=float),
        bright_box,
        half,
        np.zeros(ROWS),
        half,
        np.full(ROWS, GAP_PX),
    )


def test_measured_blur_adds_up_to_one_and_sees_the_heavier_right_wing() -> None:
    """Each band adds up to 1, and the right side holds more wing light."""
    blur = measure_reference_blur()
    assert np.allclose(blur.profiles.sum(axis=1), 1.0)
    for profile in blur.profiles:
        left = profile[(blur.offsets_px >= -15) & (blur.offsets_px <= -8)].sum()
        right = profile[(blur.offsets_px >= 8) & (blur.offsets_px <= 15)].sum()
        assert right > left


def test_enclosed_fraction_covers_all_the_light_and_none_of_a_far_interval() -> None:
    """The window holds all a star's light; a far stretch holds almost none."""
    blur = measure_reference_blur()
    assert abs(enclosed_fraction(blur, 100.0, 1.0, 0.0, -75.0, 75.0) - 1.0) < 1e-6
    assert enclosed_fraction(blur, 100.0, 1.0, 0.0, 50.0, 60.0) < 0.01


def test_stretching_the_blur_moves_light_out_of_the_core() -> None:
    """A stretched blur puts less light in the core."""
    blur = measure_reference_blur()
    sharp = enclosed_fraction(blur, 100.0, 1.0, 0.0, -3.0, 3.0)
    stretched = enclosed_fraction(blur, 100.0, 1.4, 0.0, -3.0, 3.0)
    assert stretched < sharp


def test_pixel_profile_sums_to_one() -> None:
    """A star's light spread over the pixels adds up to all of it."""
    blur = measure_reference_blur()
    assert abs(pixel_profile(blur, 100.0, 1.0, 0.0, np.arange(-75.0, 76.0)).sum() - 1.0) < 1e-3


def test_band_profiles_do_not_depend_on_dispersion_direction() -> None:
    """The same streaks laid left to right give the same band profiles."""
    image, _, _, _ = make_pair()
    centres = np.full(ROWS, BRIGHT_CENTRE_PX)
    _, vertical, _ = build_band_profiles(image, True, centres, BAND_EDGES, 0.0)
    _, horizontal, _ = build_band_profiles(image.T, False, centres, BAND_EDGES, 0.0)
    assert np.allclose(vertical, horizontal)


def test_fit_finds_the_focus_change_and_is_called_reliable() -> None:
    """The fit finds a stretch near the true 1.1 and passes the gate."""
    blur = measure_reference_blur()
    image, _, _, _ = make_pair(scale=1.1)
    fit = fit_the_pair(image, blur, [0.0, GAP_PX])
    assert abs(fit.dilation - 1.1) < 0.1
    assert fit.relative_residual < MAXIMUM_TRUSTED_RELATIVE_RESIDUAL
    assert fit.is_reliable


def test_a_fit_that_misses_a_star_is_called_unreliable() -> None:
    """Telling the fit there is one star when there are two is not trusted."""
    blur = measure_reference_blur()
    image, _, _, _ = make_pair()
    fit = fit_the_pair(image, blur, [0.0])
    assert not fit.is_reliable


def test_the_predicted_light_matches_the_true_light_in_the_faint_box() -> None:
    """The predicted light in the faint box is close to the exact amount."""
    blur = measure_reference_blur()
    image, _, _, bright_only = make_pair(scale=1.1)
    fit = fit_the_pair(image, blur, [0.0, GAP_PX])
    predicted = predicted_wing(blur, fit, box_sum(image, BRIGHT_CENTRE_PX))
    true_wing = box_sum(bright_only, FAINT_CENTRE_PX)
    last = slice(ROWS - 100, ROWS)
    ratio = predicted[last].mean() / true_wing[last].mean()
    assert 0.75 < ratio < 1.25


def test_subtraction_moves_the_faint_star_toward_its_true_spectrum() -> None:
    """The corrected faint box is closer to the truth where faintest."""
    blur = measure_reference_blur()
    image, _, faint_spectrum, _ = make_pair(scale=1.1)
    fit = fit_the_pair(image, blur, [0.0, GAP_PX])
    faint_box = box_sum(image, FAINT_CENTRE_PX)
    wing = predicted_wing(blur, fit, box_sum(image, BRIGHT_CENTRE_PX))
    corrected, fraction = subtract_neighbor_wings(faint_box, [wing])

    faint_only = make_image([(FAINT_CENTRE_PX, faint_spectrum)], 1.1, noise_sigma=0.0, seed=0)
    true_inside = box_sum(faint_only, FAINT_CENTRE_PX)
    last = slice(ROWS - 60, ROWS)
    error_before = np.mean(np.abs(faint_box[last] - true_inside[last]) / true_inside[last])
    error_after = np.mean(np.abs(corrected[last] - true_inside[last]) / true_inside[last])
    assert error_before > 0.05
    assert error_after < error_before / 3
    assert fraction[-1] > fraction[0]


def test_a_star_with_no_neighbour_is_unchanged() -> None:
    """With no neighbours the flux is unchanged and the audit fraction is 0."""
    flux = np.linspace(100.0, 200.0, 50)
    corrected, fraction = subtract_neighbor_wings(flux, [])
    assert np.array_equal(corrected, flux)
    assert not fraction.any()


def test_subtraction_never_goes_below_zero() -> None:
    """An overshooting correction is held at zero, not made negative."""
    corrected, _ = subtract_neighbor_wings(np.array([10.0, 10.0]), [np.array([3.0, 30.0])])
    assert corrected.tolist() == [7.0, 0.0]
