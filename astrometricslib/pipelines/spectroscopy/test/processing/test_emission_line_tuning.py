"""Purpose: Unit tests for the emission-line detector tuners.

Description: Checks the pure functions of `emission_line_tuning` on synthetic
spectra with a known answer, that the tuners decline a filter without
spectra, and that they stay silent with no stored data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.processing.detector_tuning_statistics import (
    smallest_degree_within_tolerance,
)
from astrometricslib.pipelines.spectroscopy.processing.emission_line_tuning import (
    EMISSION_LINE_BOXES_TUNER,
    EMISSION_SPECTRUM_CHECKS_TUNER,
    blend_limit,
    box_half_width_angstrom,
    continuum_errors_by_degree,
    inflation_cap_fraction,
    injection_recovered,
    pairwise_box_correlations,
    point_count_shares,
    source_line_count_limit,
    tune_emission_line_boxes,
    tune_emission_spectrum_checks,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import StoredSpectrum

_WAVELENGTH = np.arange(3800.0, 8000.0, 11.0)


def _smooth_spectrum(seed: int = 1) -> np.ndarray:
    """Make a gently sloping spectrum with a little noise.

    Returns
    -------
    intensity : `numpy.ndarray`
        The brightness on `_WAVELENGTH`.
    """
    return (
        100.0 + 0.01 * (_WAVELENGTH - 5000.0) + np.random.default_rng(seed).normal(0.0, 0.5, _WAVELENGTH.size)
    )


def _inputs(filter_name: str | None, targets: list) -> TuningInputs:
    """Build tuning inputs around a fake library.

    Returns
    -------
    inputs : `TuningInputs`
        Inputs whose library lists the given targets.
    """
    astrometrics = SimpleNamespace(targets=SimpleNamespace(list=lambda: targets))
    setup = SetupConfig(name="s", camera_name="cam", optic_name="optic")
    return TuningInputs(setup, filter_name, None, {"astrometrics": astrometrics})


def test_continuum_degree_is_the_true_degree() -> None:
    """Continuum degree is the true degree.

    A quadratic continuum is best predicted by degree 2 and a line does worse.
    """
    positions = np.linspace(-1.0, 1.0, _WAVELENGTH.size)
    intensity = 100.0 * (1.0 + 0.3 * positions + 0.3 * positions**2)
    intensity = intensity + np.random.default_rng(5).normal(0.0, 0.1, intensity.size)
    errors = continuum_errors_by_degree([(_WAVELENGTH, intensity)], range(1, 4), 5)
    assert errors[1] > 3.0 * errors[2]
    assert smallest_degree_within_tolerance(errors, 0.05) == 2


def test_inflation_cap_fraction_is_small_for_white_noise() -> None:
    """Inflation cap fraction is small for white noise.

    Independent noise needs almost no inflation, so the cap fraction is far
    below the default.
    """
    fraction = inflation_cap_fraction([(_WAVELENGTH, _smooth_spectrum())], 1, 0.95)
    assert fraction is not None
    assert fraction < 0.05


def test_point_count_shares_skip_counts_that_do_not_fit() -> None:
    """A count whose windows number fewer than three is untested."""
    shares = point_count_shares([(_WAVELENGTH, _smooth_spectrum())], (20, 300), 1, 0.5)
    assert shares[0] is not None
    assert shares[1] is None


def test_source_line_count_limit() -> None:
    """Source line count limit.

    The limit is the smallest count that no more than the allowed share of
    stars reach.
    """
    assert source_line_count_limit([0] * 99 + [2], 0.01) == 1
    assert source_line_count_limit([0] * 90 + [3] * 10, 0.01) == 4


def test_pairwise_box_correlations_find_the_look_alike_lines() -> None:
    """Pairwise box correlations find the look alike lines.

    H-beta and [O III] look alike, and the blend limit falls below their
    correlation.
    """
    correlations = pairwise_box_correlations(_WAVELENGTH, 204.0, 20.0, 2, 0.5)
    assert 0.5 < correlations["H-beta", "[O III] 4959+5007"] < 0.8
    limit = blend_limit([correlations], 3)
    assert limit is not None
    assert 0.2 < limit < correlations["H-beta", "[O III] 4959+5007"]


def test_blend_limit_needs_enough_pairs() -> None:
    """Two positively correlated pairs are too few to place a limit."""
    assert blend_limit([{("a", "b"): 0.7, ("a", "c"): 0.1, ("b", "c"): -0.4}], 3) is None


def test_injection_is_recovered_in_a_smooth_spectrum() -> None:
    """Injection is recovered in a smooth spectrum.

    A strong line half inside the range is recovered, and a spectrum that
    cannot hold it gives none.
    """
    intensity = _smooth_spectrum()
    assert injection_recovered(_WAVELENGTH, intensity, 5, 0.5, 200.0, 20.0, 20.0, 0.25)
    short = _WAVELENGTH < 7200.0
    assert injection_recovered(_WAVELENGTH[short], intensity[short], 7, 0.9, 200.0, 20.0, 20.0, 0.25) is None
    late = _WAVELENGTH > 4600.0
    assert injection_recovered(_WAVELENGTH[late], intensity[late], 0, 0.5, 200.0, 20.0, 20.0, 0.25) is None


def test_box_half_width_follows_the_extraction_box() -> None:
    """Box half width follows the extraction box.

    A box 18.5 px either side of the trail at 11 A per pixel is 203.5 A across
    half.
    """
    spectrum = StoredSpectrum("A", "T", _WAVELENGTH, _smooth_spectrum(), None, 18.5, True)
    assert box_half_width_angstrom(spectrum) == pytest.approx(203.5)
    unrecorded = StoredSpectrum("A", "T", _WAVELENGTH, _smooth_spectrum(), None, None, True)
    assert box_half_width_angstrom(unrecorded) is None


def test_tuners_set_the_declared_parameters() -> None:
    """Tuners set the declared parameters.

    The two tuners share no parameter and name the six emission limits between
    them.
    """
    first = {parameter.name for parameter in EMISSION_SPECTRUM_CHECKS_TUNER.parameters}
    second = {parameter.name for parameter in EMISSION_LINE_BOXES_TUNER.parameters}
    assert not first & second
    assert len(first | second) == 6


def test_tuners_skip_without_data() -> None:
    """Tuners skip without data.

    A filter without spectra, or a library with none, gives a skip, not a
    guess.
    """
    for tune in (tune_emission_spectrum_checks, tune_emission_line_boxes):
        with pytest.raises(TuningSkippedError):
            tune(_inputs("L", []))
        with pytest.raises(TuningSkippedError):
            tune(_inputs("SPEC", []))
