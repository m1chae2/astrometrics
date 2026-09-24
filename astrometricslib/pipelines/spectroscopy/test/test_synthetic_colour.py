"""Purpose: Unit tests for the spectrum-versus-catalog colour check.

Description: A spectrum's colour, read from two wavelength windows and
calibrated on the dwarf references, is compared with the star's catalog B-V.
These tests check that the calibration recovers the references' own colours,
that late or uncovered spectra are left alone, that the flag fires only for a
large disagreement (the size of the Elnath and TYC 3105-899-1 cases), and that
the flag reaches the classification note through `analyze_spectrum`.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum
from astrometricslib.pipelines.spectroscopy.synthetic_colour import (
    COLOUR_DISAGREEMENT_MAGNITUDES,
    DWARF_B_MINUS_V,
    MAXIMUM_CALIBRATED_B_MINUS_V,
    colour_disagreement_note,
    synthetic_b_minus_v,
    window_colour_index,
)


def _blurred(spectral_type: str) -> tuple[np.ndarray, np.ndarray]:
    """Give a reference spectrum blurred to the instrument's resolution.

    Returns
    -------
    wavelength_angstrom, flux : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The reference wavelengths and blurred flux.
    """
    wavelength, flux = _get_reference_templates()[spectral_type]
    return wavelength, gaussian_filter1d(flux, 45.0 / 2.355 / 5.0)


def test_the_calibration_recovers_the_reference_colours() -> None:
    """Each dwarf reference reads close to the B-V it was calibrated on."""
    errors = []
    for reference_type, b_minus_v in DWARF_B_MINUS_V.items():
        recovered = synthetic_b_minus_v(*_get_reference_templates()[reference_type])
        assert recovered is not None, reference_type
        errors.append(recovered - b_minus_v)

    assert np.sqrt(np.mean(np.square(errors))) < 0.06
    assert max(abs(error) for error in errors) < 0.15


@pytest.mark.parametrize(("reference_type", "expected"), [("A0V", 0.0), ("G2V", 0.63), ("K5V", 1.15)])
def test_blurred_references_keep_their_colour(reference_type: str, expected: float) -> None:
    """Blurring to the instrument's resolution barely moves the colour."""
    assert synthetic_b_minus_v(*_blurred(reference_type)) == pytest.approx(expected, abs=0.1)


def test_a_spectrum_that_misses_a_window_has_no_colour() -> None:
    """Without the blue window there is nothing to compare."""
    wavelength, flux = _blurred("G2V")
    red_only = wavelength >= 5000.0

    assert window_colour_index(wavelength[red_only], flux[red_only]) is None
    assert synthetic_b_minus_v(wavelength[red_only], flux[red_only]) is None


def test_a_spectrum_redder_than_any_reference_reads_as_the_reddest() -> None:
    """A late M star reads as a lower limit and still shows a difference."""
    colour = synthetic_b_minus_v(*_get_reference_templates()["M6V"])

    assert colour is not None
    assert 1.5 < colour <= MAXIMUM_CALIBRATED_B_MINUS_V
    assert colour_disagreement_note(0.0, colour) != ""


def test_a_non_positive_window_has_no_colour() -> None:
    """A window that averages to zero gives no magnitude difference."""
    wavelength, flux = _blurred("G2V")

    assert window_colour_index(wavelength, np.zeros_like(flux)) is None


@pytest.mark.parametrize(
    ("catalog", "synthetic", "word"),
    [(-0.13, 0.91, "redder"), (0.47, -0.11, "bluer"), (0.0, 1.00, "redder"), (1.23, 1.70, "redder")],
)
def test_the_colours_of_the_problem_spectra_are_flagged(catalog: float, synthetic: float, word: str) -> None:
    """Elnath, TYC 3105-899-1, HD 172449 and Arcturus are all flagged."""
    note = colour_disagreement_note(catalog, synthetic)

    assert word in note
    assert "may not be this star's" in note


@pytest.mark.parametrize(
    ("catalog", "synthetic"),
    [(0.76, 1.06), (0.87, 0.99), (0.0, -0.08), (1.5, 1.5 + COLOUR_DISAGREEMENT_MAGNITUDES)],
)
def test_ordinary_scatter_is_not_flagged(catalog: float, synthetic: float) -> None:
    """The worst ordinary case (0.30 mag) and the threshold stay quiet."""
    assert colour_disagreement_note(catalog, synthetic) == ""


@pytest.mark.parametrize(
    ("catalog", "synthetic"),
    [
        (None, 0.5),
        (0.5, None),
        (float("nan"), 0.5),
        (0.5, float("nan")),
        (MAXIMUM_CALIBRATED_B_MINUS_V + 0.1, 0.0),
    ],
)
def test_missing_or_uncalibratable_colours_are_not_flagged(
    catalog: float | None, synthetic: float | None
) -> None:
    """Nothing is said for a missing colour or one beyond the range."""
    assert colour_disagreement_note(catalog, synthetic) == ""


def _recorded_k2v() -> tuple[np.ndarray, np.ndarray]:
    """Give a K2V reference as the instrument would record it.

    Returns
    -------
    wavelength_angstrom, recorded : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The wavelengths and the brightness with the instrument's tilt on it.
    """
    wavelength, flux = _blurred("K2V")
    return wavelength, flux * load_instrument_response("ZWO ASI 533MM Pro").value_at(wavelength)


def test_analysis_flags_a_colour_far_from_the_catalog() -> None:
    """A K2V spectrum on a star the catalog calls blue gets the note."""
    wavelength, recorded = _recorded_k2v()

    analysis = analyze_spectrum(
        wavelength, recorded, "ZWO ASI 533MM Pro", True, catalog_spectral_type="K2", catalog_b_minus_v=-0.13
    )

    assert analysis.classification["spectral_type"] == "K2V"
    assert "mag redder than the catalog colour" in str(analysis.classification["reason"])


def test_analysis_stays_quiet_when_the_colour_agrees() -> None:
    """The same spectrum with its real colour gets no colour note."""
    wavelength, recorded = _recorded_k2v()

    analysis = analyze_spectrum(
        wavelength, recorded, "ZWO ASI 533MM Pro", True, catalog_spectral_type="K2", catalog_b_minus_v=0.91
    )

    assert analysis.classification["reason"] is None


def test_analysis_without_a_catalog_colour_is_unchanged() -> None:
    """A star with no catalog colour behaves exactly as before."""
    wavelength, recorded = _recorded_k2v()

    analysis = analyze_spectrum(wavelength, recorded, "ZWO ASI 533MM Pro", True, catalog_spectral_type="K2")

    assert analysis.classification["reason"] is None
