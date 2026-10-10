"""Purpose: Tests for equivalent widths and their errors.

Description: A Gaussian dip of known depth and width has a known equivalent
width, `depth * sigma * sqrt(2 * pi)`. These tests inject one into a smooth
continuum with known per-sample errors, measure its equivalent width over 200
noise realizations, and check that the mean is the truth and that the
reported error matches the scatter of the 200 results. They also check each
part of the error on its own (the sample noise in the window and the
covariance of the continuum fit), the cases that give no measurement, the
excess-scatter inflation, and that the feature detector reports the width
without changing any verdict or depth.
"""

import math

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.processing.equivalent_width import (
    EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS,
    EquivalentWidthMeasurement,
    measure_equivalent_width,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import (
    SHOULDER_BAND_HALF_WINDOWS,
    detect_named_features,
)

LINE_CENTER = 4861.0
LINE_SPREAD = 45.0  # full width at half maximum of the blur, in Angstroms
LINE_SIGMA = LINE_SPREAD / 2.355
CORE_HALF_WINDOW = 25.0
INNER_BAND_EDGE = max(CORE_HALF_WINDOW, 1.5 * LINE_SPREAD)
BAND_WIDTH = SHOULDER_BAND_HALF_WINDOWS * CORE_HALF_WINDOW
DEPTH = 0.2
REALIZATIONS = 200

WAVELENGTH = np.arange(3800.0, 8000.0, 11.0)


def _continuum() -> np.ndarray:
    """Build a smooth quadratic continuum.

    Returns
    -------
    continuum : `numpy.ndarray`
        A brightness of 1000 near 5000 A that rises a little to the red and
        bends down, so a quadratic fit describes it exactly.
    """
    x = (WAVELENGTH - 5000.0) / 1000.0
    return 1000.0 * (1.0 + 0.1 * x - 0.04 * x**2)


def _model(depth: float = DEPTH, center: float = LINE_CENTER) -> np.ndarray:
    """Build the noise-free spectrum with one Gaussian dip.

    Parameters
    ----------
    depth : `float`, optional
        The dip's depth as a fraction of the continuum. A negative depth is
        a bump (emission).
    center : `float`, optional
        The dip's center, in Angstroms.

    Returns
    -------
    model : `numpy.ndarray`
        The continuum times `1 - depth * exp(-x^2 / (2 sigma^2))`.
    """
    line = depth * np.exp(-0.5 * ((WAVELENGTH - center) / LINE_SIGMA) ** 2)
    return _continuum() * (1.0 - line)


def _errors() -> np.ndarray:
    """Give a per-sample error that grows with the brightness.

    Returns
    -------
    errors : `numpy.ndarray`
        6 ADU plus 1% of the continuum, so the samples are not all alike.
    """
    return 6.0 + 0.01 * _continuum()


def _true_width(depth: float = DEPTH) -> float:
    """Give the equivalent width of the injected dip inside the window.

    Parameters
    ----------
    depth : `float`, optional
        The dip's depth.

    Returns
    -------
    width : `float`
        ``depth * sigma * sqrt(2 pi) * erf(w / (sqrt(2) sigma))`` for the
        window half-width `w` (the integral cut at the window).
    """
    window = EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS * LINE_SPREAD
    return depth * LINE_SIGMA * math.sqrt(2.0 * math.pi) * math.erf(window / (math.sqrt(2.0) * LINE_SIGMA))


def _measure(values: np.ndarray, errors: np.ndarray, **overrides: float) -> EquivalentWidthMeasurement | None:
    """Measure the injected line with the standard bands.

    Parameters
    ----------
    values : `numpy.ndarray`
        The spectrum.
    errors : `numpy.ndarray`
        The per-sample errors.
    **overrides : `float`
        Replacements for `center_angstrom` or `line_spread_angstrom`.

    Returns
    -------
    measurement : `EquivalentWidthMeasurement` or `None`
        The result of `measure_equivalent_width`.
    """
    arguments = {"center_angstrom": LINE_CENTER, "line_spread_angstrom": LINE_SPREAD}
    arguments.update(overrides)
    return measure_equivalent_width(
        WAVELENGTH,
        values,
        errors,
        arguments["center_angstrom"],
        arguments["line_spread_angstrom"],
        INNER_BAND_EDGE,
        BAND_WIDTH,
    )


def _realizations(errors: np.ndarray, model: np.ndarray, seed: int = 5) -> list:
    """Measure the line in many noise realizations.

    Parameters
    ----------
    errors : `numpy.ndarray`
        The per-sample errors used both to draw the noise and to report.
    model : `numpy.ndarray`
        The noise-free spectrum.
    seed : `int`, optional
        Seed of the random generator.

    Returns
    -------
    measurements : `list`
        One `EquivalentWidthMeasurement` per realization.
    """
    rng = np.random.default_rng(seed)
    return [_measure(model + rng.normal(size=WAVELENGTH.size) * errors, errors) for _ in range(REALIZATIONS)]


def test_the_equivalent_width_of_a_gaussian_dip_is_recovered() -> None:
    """The mean over 200 realizations is the truth.

    The injected dip has depth 0.2 and a blur of 45 A, so its equivalent width
    is `0.2 * 19.1 * sqrt(2 pi)` = 9.58 A. The mean must lie within 3 standard
    errors of the mean of the truth, the z-scores must have a standard
    deviation near 1, and the first realization must be within 2 sigma.
    """
    truth = _true_width()
    results = _realizations(_errors(), _model())
    widths = np.array([result.equivalent_width_angstrom for result in results])
    errors = np.array([result.equivalent_width_error_angstrom for result in results])

    z_scores = (widths - truth) / errors
    assert abs(z_scores[0]) < 2.0
    assert abs(np.mean(z_scores)) < 3.0 / math.sqrt(REALIZATIONS)
    assert np.std(z_scores, ddof=1) == pytest.approx(1.0, abs=0.15)
    assert truth == pytest.approx(9.58, abs=0.01)


def test_the_reported_error_matches_the_scatter_over_200_realizations() -> None:
    """The mean reported error is within 20% of the scatter of the results."""
    results = _realizations(_errors(), _model())
    widths = np.array([result.equivalent_width_angstrom for result in results])
    errors = np.array([result.equivalent_width_error_angstrom for result in results])

    assert np.mean(errors) / np.std(widths, ddof=1) == pytest.approx(1.0, abs=0.2)


def test_the_window_noise_alone_is_the_error_when_the_bands_are_exact() -> None:
    """With noise only in the window, the error is the window part."""
    errors = _errors()
    window = np.abs(WAVELENGTH - LINE_CENTER) <= EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS * LINE_SPREAD
    window_errors = np.where(window, errors, 0.0)
    results = _realizations(window_errors, _model())
    widths = np.array([result.equivalent_width_angstrom for result in results])
    reported = np.array([result.equivalent_width_error_angstrom for result in results])

    assert np.mean(reported) / np.std(widths, ddof=1) == pytest.approx(1.0, abs=0.2)
    # The continuum is exact, so the mean is the truth to a fraction
    # of the error.
    assert np.mean(widths) == pytest.approx(_true_width(), abs=0.5)


def test_the_continuum_covariance_alone_is_the_error_when_only_the_bands_are_noisy() -> None:
    """With noise only in the bands, the error comes from the continuum fit."""
    errors = _errors()
    distance = np.abs(WAVELENGTH - LINE_CENTER)
    band = (distance > INNER_BAND_EDGE) & (distance <= INNER_BAND_EDGE + BAND_WIDTH)
    band_errors = np.where(band, errors, 0.0)
    results = _realizations(band_errors, _model())
    widths = np.array([result.equivalent_width_angstrom for result in results])
    reported = np.array([result.equivalent_width_error_angstrom for result in results])

    assert np.std(widths, ddof=1) > 0.5
    assert np.mean(reported) / np.std(widths, ddof=1) == pytest.approx(1.0, abs=0.2)


def test_an_emission_line_has_a_negative_equivalent_width() -> None:
    """A bump above the continuum gives a negative width of the same size."""
    results = _realizations(_errors(), _model(depth=-DEPTH))
    widths = np.array([result.equivalent_width_angstrom for result in results])

    assert np.mean(widths) == pytest.approx(-_true_width(), abs=0.5)


def test_a_wider_line_spread_widens_the_window() -> None:
    """The window is 1.5 line spreads either side of the center."""
    noise_free = _model()
    narrow = _measure(noise_free, _errors(), line_spread_angstrom=40.0)
    wide = _measure(noise_free, _errors(), line_spread_angstrom=60.0)

    assert narrow is not None and wide is not None
    assert narrow.window_half_width_angstrom == pytest.approx(60.0)
    assert wide.window_half_width_angstrom == pytest.approx(90.0)
    # Both windows hold the whole dip, so the width is the same to 0.2 A.
    assert wide.equivalent_width_angstrom == pytest.approx(narrow.equivalent_width_angstrom, abs=0.2)


def test_the_bands_move_out_with_a_wide_window() -> None:
    """A window wider than the detector's inner edge pushes the bands out.

    A line spread of 120 A has a window of 180 A, past the detector's inner
    edge of 67.5 A. The bands start at 180 A, so the dip's own wings (still
    present at 90 A) cannot bias the continuum, and the full width is found.
    """
    measurement = _measure(_model(), np.full(WAVELENGTH.size, 1e-6), line_spread_angstrom=120.0)

    assert measurement is not None
    assert measurement.window_half_width_angstrom == pytest.approx(180.0)
    assert measurement.equivalent_width_angstrom == pytest.approx(
        DEPTH * LINE_SIGMA * math.sqrt(2.0 * math.pi), abs=0.01
    )


def test_a_line_at_the_edge_of_the_spectrum_gives_no_measurement() -> None:
    """A window that runs off the spectrum is not measured."""
    assert _measure(_model(center=3850.0), _errors(), center_angstrom=3850.0) is None
    assert _measure(_model(), _errors(), center_angstrom=7990.0) is None


def test_a_gap_in_the_samples_gives_no_measurement() -> None:
    """Bands with too few samples are not measured."""
    values = _model()
    errors = _errors().copy()
    errors[np.abs(WAVELENGTH - LINE_CENTER) > 100.0] = np.nan

    assert _measure(values, errors) is None


def test_a_continuum_below_zero_gives_no_measurement() -> None:
    """A spectrum whose fitted continuum is not positive is not measured."""
    assert _measure(-_model(), _errors()) is None


def test_clean_noise_does_not_inflate_the_continuum_error() -> None:
    """Ordinary noise passes the excess-scatter test almost every time."""
    results = _realizations(_errors(), _model(), seed=9)
    inflated = [result.continuum_scatter_inflation > 1.0 for result in results]

    assert np.mean(inflated) < 0.03


def test_structure_the_errors_do_not_know_inflates_the_continuum_error() -> None:
    """A wiggle in the bands raises the reduced chi-square and the error."""
    errors = _errors()
    quiet = _measure(_model(), errors)
    wiggle = 0.06 * _continuum() * np.sin(2 * math.pi * (WAVELENGTH - LINE_CENTER) / 31.0)
    wiggly = _measure(_model() + wiggle, errors)

    assert quiet is not None and wiggly is not None
    assert quiet.continuum_scatter_inflation == pytest.approx(1.0)
    assert wiggly.continuum_scatter_inflation > 2.0
    assert wiggly.equivalent_width_error_angstrom > quiet.equivalent_width_error_angstrom


# ---------------------------------------------------- the feature detector


def _hbeta(features: list[dict[str, object]]) -> dict[str, object]:
    """Pick the H-beta entry out of the detector's result.

    Parameters
    ----------
    features : `list` [`dict`]
        The result of `detect_named_features`.

    Returns
    -------
    entry : `dict`
        The H-beta feature.
    """
    return next(entry for entry in features if "H-beta" in str(entry["feature"]))


def test_the_detector_reports_the_equivalent_width_without_changing_a_verdict() -> None:
    """Passing errors adds the width and error and changes nothing else."""
    rng = np.random.default_rng(21)
    errors = _errors()
    values = _model() + rng.normal(size=WAVELENGTH.size) * errors

    without = detect_named_features(WAVELENGTH, values, resolution_element_angstrom=LINE_SPREAD)
    with_errors = detect_named_features(
        WAVELENGTH, values, resolution_element_angstrom=LINE_SPREAD, errors=errors
    )

    extra = {
        "equivalent_width_angstrom",
        "equivalent_width_error_angstrom",
        "equivalent_width_window_half_width_angstrom",
    }
    assert [entry["feature"] for entry in without] == [entry["feature"] for entry in with_errors]
    for plain, rich in zip(without, with_errors, strict=True):
        assert {key: value for key, value in rich.items() if key not in extra} == {
            key: value for key, value in plain.items() if key not in extra
        }
    assert _hbeta(without)["equivalent_width_angstrom"] is None
    assert _hbeta(without)["equivalent_width_error_angstrom"] is None

    entry = _hbeta(with_errors)
    width = entry["equivalent_width_angstrom"]
    error = entry["equivalent_width_error_angstrom"]
    assert isinstance(width, float) and isinstance(error, float)
    assert entry["kind"] == "absorption"
    assert entry["depth"] > 0.1  # the depth fields are kept
    assert entry["equivalent_width_window_half_width_angstrom"] == pytest.approx(
        EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS * LINE_SPREAD
    )
    # The center is chosen as the best dip, which pulls a faint line's width
    # up a little, so allow 3 sigma.
    assert abs(width - _true_width()) < 3.0 * error


def test_the_detector_uses_the_line_spread_profile_at_the_feature() -> None:
    """With a profile, the window follows the line spread at the feature."""
    from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import ResolutionProfile

    errors = _errors()
    profile = ResolutionProfile(np.array([4000.0, 8000.0]), np.array([40.0, 100.0]))

    features = detect_named_features(
        WAVELENGTH,
        _model(),
        resolution_element_angstrom=LINE_SPREAD,
        resolution_profile=profile,
        errors=errors,
    )

    entry = _hbeta(features)
    local_spread = float(profile.at(np.array([LINE_CENTER]))[0])
    assert entry["equivalent_width_window_half_width_angstrom"] == pytest.approx(
        EQUIVALENT_WIDTH_WINDOW_LINE_SPREADS * local_spread, rel=0.01
    )
