"""Unit tests for the variability indices and the noise model.

The indices are checked against values worked out by hand. The noise model
is checked against a curve that the test injects into a synthetic field.
"""

import math

import numpy as np
import pytest

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.processing import variability_indices as vi


def make_star(
    star_id: str, values: list[float], errors: list[float] | None, flux: float = 1000.0
) -> StellarObject:
    """Build a star whose normalized light curve is given.

    Parameters
    ----------
    star_id : `str`
        The star's id.
    values : `list` [`float`]
        The normalized brightness values.
    errors : `list` [`float`] or `None`
        Their errors, or `None` for none.
    flux : `float`, optional
        The star's constant raw flux, in ADU per second.

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    star = StellarObject(id=star_id)
    star.photometry = PhotometryResult(
        fluxes=[flux] * len(values),
        fluxes_normalized=list(values),
        fluxes_normalized_errors=list(errors) if errors is not None else [],
    )
    return star


def test_stetson_j_matches_a_hand_computed_value_for_a_four_point_series() -> None:
    """Values 1.0 to 1.3 with errors 0.1 give J = (2 - 1 / sqrt(3)) / 3.

    The weighted mean is 1.15. The residuals over the error are -1.5, -0.5,
    0.5 and 1.5. Each is multiplied by sqrt(4 / 3) = 1.1547, which gives
    -1.7321, -0.5774, 0.5774 and 1.7321. The three neighbouring products are
    1.0, -1/3 and 1.0. The index averages sign(P) sqrt(|P|):
    (1 - 0.57735 + 1) / 3 = 0.47422.
    """
    j_index = vi.stetson_j([1.0, 1.1, 1.2, 1.3], [0.1] * 4)

    assert j_index == pytest.approx((2.0 - 1.0 / math.sqrt(3.0)) / 3.0)
    assert j_index == pytest.approx(0.47422, abs=1e-5)


def test_stetson_j_is_negative_for_a_series_that_alternates() -> None:
    """Values 1.0, 1.2, 1.0, 1.2 with errors 0.1 give J = -sqrt(4 / 3).

    Every neighbouring product is -4 / 3, so the index is -sqrt(4 / 3).
    """
    assert vi.stetson_j([1.0, 1.2, 1.0, 1.2], [0.1] * 4) == pytest.approx(-math.sqrt(4.0 / 3.0))


def test_one_bad_point_adds_little_to_stetson_j_but_much_to_chi_square() -> None:
    """A single outlier gives two products of opposite sign, which cancel."""
    rng = np.random.default_rng(1)
    values = 1.0 + 0.01 * rng.normal(size=40)
    errors = np.full(40, 0.01)
    spiked = values.copy()
    spiked[20] += 0.2

    assert vi.reduced_chi_square(spiked, errors) > 5.0 * vi.reduced_chi_square(values, errors)
    assert abs(vi.stetson_j(spiked, errors) - vi.stetson_j(values, errors)) < 0.5


def test_a_smooth_change_gives_a_large_stetson_j_and_noise_gives_about_zero() -> None:
    """A slow sinusoid scores far above white noise of the same size."""
    rng = np.random.default_rng(2)
    errors = np.full(60, 0.01)
    noise = 1.0 + 0.01 * rng.normal(size=60)
    sinusoid = noise + 0.03 * np.sin(2.0 * np.pi * np.arange(60) / 20.0)

    assert abs(vi.stetson_j(noise, errors)) < 0.5
    assert vi.stetson_j(sinusoid, errors) > 1.0


def test_reduced_chi_square_matches_a_hand_computed_value() -> None:
    """Values 1.0, 1.2, 0.8, 1.0, 1.0 with errors 0.1 give (4 + 4) / 4 = 2."""
    assert vi.reduced_chi_square([1.0, 1.2, 0.8, 1.0, 1.0], [0.1] * 5) == pytest.approx(2.0)


def test_reduced_chi_square_uses_the_error_weighted_mean() -> None:
    """Values 1 and 2 with errors 1 and 2 have the mean 1.2 and chi-square 0.2.

    The weights are 1 and 0.25, so the mean is (1 + 0.5) / 1.25 = 1.2. The
    sum is (0.2 / 1)^2 + (0.8 / 2)^2 = 0.2, divided by n - 1 = 1.
    """
    assert vi.reduced_chi_square([1.0, 2.0], [1.0, 2.0]) == pytest.approx(0.2)


def test_reduced_chi_square_is_near_one_for_noise_that_matches_the_errors() -> None:
    """Gaussian noise of the stated size gives a chi-square near 1."""
    rng = np.random.default_rng(3)
    values = [1.0 + 0.02 * rng.normal(size=50) for _ in range(300)]
    chi = [vi.reduced_chi_square(row, np.full(50, 0.02)) for row in values]

    assert np.mean(chi) == pytest.approx(1.0, abs=0.03)


def injected_rms(magnitude: np.ndarray) -> np.ndarray:
    """Give the scatter of the synthetic field's constant stars.

    A floor of 0.003 mag, plus a term that doubles every 1.25 magnitudes of
    faintness, added in quadrature.

    Returns
    -------
    rms_mag : `numpy.ndarray`
        The scatter at each magnitude, in magnitudes.
    """
    return np.sqrt(0.003**2 + (0.002 * 10 ** (0.4 * (magnitude - 14.0)) ** 1) ** 2)


def test_the_noise_model_follows_the_injected_scatter_and_ignores_variables() -> None:
    """The fitted curve stays within 15 percent of the truth.

    One star in ten is a variable with five times its expected scatter. The
    bin medians do not move for them.
    """
    rng = np.random.default_rng(4)
    magnitude = rng.uniform(10.0, 16.0, size=600)
    truth = injected_rms(magnitude)
    measured = truth * np.exp(rng.normal(0.0, 0.1, size=magnitude.size))
    variable = rng.uniform(size=magnitude.size) < 0.1
    measured[variable] *= 5.0

    model = vi.fit_noise_model(magnitude, measured)

    assert model is not None
    assert len(model.magnitudes) == vi.MAXIMUM_NOISE_BINS
    assert sum(model.star_counts) == 600
    for centre, fitted in zip(model.magnitudes, model.rms_mag, strict=True):
        assert fitted == pytest.approx(float(injected_rms(np.array([centre]))[0]), rel=0.15)
    assert all(later >= earlier for earlier, later in zip(model.rms_mag, model.rms_mag[1:], strict=False))


def test_the_noise_model_interpolates_between_bins_and_extends_past_the_faint_end() -> None:
    """Between centres the curve is log-linear, and it keeps rising."""
    model = vi.NoiseModel((10.0, 12.0, 14.0), (0.01, 0.02, 0.04), (10, 10, 10))

    assert model.expected_rms_mag(9.0) == pytest.approx(0.01)
    assert model.expected_rms_mag(11.0) == pytest.approx(math.sqrt(0.01 * 0.02))
    assert model.expected_rms_mag(14.0) == pytest.approx(0.04)
    assert model.expected_rms_mag(15.0) == pytest.approx(0.04 * math.sqrt(2.0))


def test_the_noise_model_needs_enough_stars() -> None:
    """Fewer than twenty usable stars give no model; bad values are skipped."""
    magnitudes = np.linspace(10.0, 15.0, 19)
    assert vi.fit_noise_model(magnitudes, np.full(19, 0.02)) is None
    twenty = np.linspace(10.0, 15.0, 20)
    assert vi.fit_noise_model(twenty, np.full(20, 0.02)) is not None
    with_bad = np.full(20, 0.02)
    with_bad[:3] = [np.nan, -1.0, 0.0]
    assert vi.fit_noise_model(twenty, with_bad) is None


def test_the_scaled_errors_rise_to_the_noise_model_and_never_fall_below_the_propagated_errors() -> None:
    """A model above the propagated error scales it; one below does not."""
    model = vi.NoiseModel((-8.0, -6.0), (0.02, 0.02), (10, 10))
    values = np.full(10, 1.0)
    small_errors = vi.LightCurveSeries(
        values, np.full(10, 0.005), -7.0, 0.02, 0.005 * vi.MAGNITUDES_PER_FRACTION
    )
    large_errors = vi.LightCurveSeries(
        values, np.full(10, 0.05), -7.0, 0.02, 0.05 * vi.MAGNITUDES_PER_FRACTION
    )
    no_errors = vi.LightCurveSeries(values, None, -7.0, 0.02, None)

    assert vi.scaled_errors(model, small_errors) == pytest.approx(
        np.full(10, 0.02 / vi.MAGNITUDES_PER_FRACTION)
    )
    assert vi.scaled_errors(model, large_errors) == pytest.approx(np.full(10, 0.05))
    assert vi.scaled_errors(model, no_errors) == pytest.approx(np.full(10, 0.02 / vi.MAGNITUDES_PER_FRACTION))


def test_the_expected_scatter_is_never_below_the_stars_own_propagated_error() -> None:
    """A star with a large propagated error is judged against that error."""
    model = vi.NoiseModel((-8.0, -6.0), (0.01, 0.01), (10, 10))
    noisy = vi.LightCurveSeries(np.ones(8), np.full(8, 0.05), -7.0, 0.06, 0.054)

    assert vi.expected_rms_mag(model, noisy) == pytest.approx(0.054)


def test_extract_series_drops_bad_points_and_mismatched_errors() -> None:
    """Bad points leave with their errors; short curves give None."""
    star = make_star("A", [1.0, 1.1, float("nan"), 0.9, 1.0, -1.0, 1.05], [0.1] * 7, flux=100.0)
    series = vi.extract_series(star)

    assert series is not None
    assert series.values.tolist() == [1.0, 1.1, 0.9, 1.0, 1.05]
    assert series.errors is not None
    assert series.errors.size == 5
    assert series.instrumental_magnitude == pytest.approx(-5.0)
    assert vi.extract_series(make_star("B", [1.0, 1.1, 0.9], [0.1] * 3)) is None
    mismatched = make_star("C", [1.0] * 6, [0.1] * 5)
    assert vi.extract_series(mismatched).errors is None


def test_the_score_is_above_one_exactly_when_all_three_thresholds_are_passed() -> None:
    """Stetson J can only cap the score at 1; it never raises it."""
    thresholds = vi.VariabilityThresholds(reduced_chi_square=2.0, stetson_j=0.4, excess_scatter=1.5)

    assert vi.variability_score(4.0, 0.9, 3.0, thresholds) == pytest.approx(2.0)
    assert vi.variability_score(4.0, 0.9, 1.8, thresholds) == pytest.approx(1.2)
    assert vi.variability_score(4.0, 0.3, 3.0, thresholds) == pytest.approx(1.0)
    assert vi.variability_score(1.0, 0.9, 3.0, thresholds) == pytest.approx(0.5)
    assert vi.variability_score(4.0, 0.9, 1.2, thresholds) == pytest.approx(0.8)


def test_the_thresholds_follow_the_field_and_ignore_a_few_variables() -> None:
    """A field with high chi-squares gets a high chi-square threshold."""
    rng = np.random.default_rng(5)
    chi = 10.0 ** rng.normal(0.0, 0.1, size=300)
    jay = rng.normal(0.0, 0.15, size=300)
    chi[:6] = 50.0
    jay[:6] = 2.0

    base = vi.calibrate_thresholds(chi, jay)
    inflated = vi.calibrate_thresholds(chi * 5.0, jay)

    assert 1.2 < base.reduced_chi_square < 2.2
    assert 0.2 < base.stetson_j < 0.6
    assert inflated.reduced_chi_square == pytest.approx(5.0 * base.reduced_chi_square)
    assert base.excess_scatter == vi.EXCESS_SCATTER_THRESHOLD


def build_field(variable_amplitude: float) -> list[StellarObject]:
    """Build forty stars of constant noise, one of which has a sinusoid.

    Parameters
    ----------
    variable_amplitude : `float`
        The fractional semi-amplitude of the sinusoid on the first star.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The first star has a 20-point-period sinusoid; the rest are constant.
    """
    rng = np.random.default_rng(6)
    stars = []
    for index in range(40):
        flux = 10 ** (-0.4 * (-8.0 + 0.05 * index))
        values = 1.0 + 0.01 * rng.normal(size=60)
        if index == 0:
            values = values + variable_amplitude * np.sin(2.0 * np.pi * np.arange(60) / 20.0)
        star = make_star(f"S{index}", values.tolist(), [0.01] * 60, flux=flux)
        stars.append(star)
    return stars


def test_assess_variability_flags_the_injected_star_and_writes_the_indices() -> None:
    """The sinusoid is the only candidate; every star gets indices."""
    stars = build_field(0.05)

    assessment = vi.assess_variability(stars)

    assert assessment.noise_model is not None
    assert assessment.thresholds is not None
    assert [star.id for star in assessment.candidates] == ["S0"]
    for star in stars:
        photometry = star.photometry
        for name in ("instrumental_mag", "rms_mag", "excess_scatter", "reduced_chi_square", "stetson_j"):
            assert getattr(photometry, name) is not None
    assert stars[0].photometry.variability_score > 1.0
    assert all(star.photometry.variability_score <= 1.0 for star in stars[1:])
    assert stars[0].photometry.excess_scatter > vi.EXCESS_SCATTER_THRESHOLD


def test_assess_variability_flags_nothing_in_a_field_of_constants() -> None:
    """No injected signal means no candidate."""
    assert vi.assess_variability(build_field(0.0)).candidates == []


def test_assess_variability_does_nothing_in_a_field_too_small_for_a_noise_model() -> None:
    """Five stars give no model, no thresholds and no written indices."""
    stars = build_field(0.05)[:5]

    assessment = vi.assess_variability(stars)

    assert assessment.noise_model is None
    assert assessment.thresholds is None
    assert assessment.candidates == []
    assert all(star.photometry.variability_score is None for star in stars)


def test_a_star_without_errors_is_judged_against_the_noise_model() -> None:
    """Without errors the model's scatter stands in; the sinusoid is found."""
    stars = build_field(0.05)
    for star in stars:
        star.photometry.fluxes_normalized_errors = []

    assessment = vi.assess_variability(stars)

    assert [star.id for star in assessment.candidates] == ["S0"]
