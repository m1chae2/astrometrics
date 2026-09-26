"""Purpose: Unit tests for classifying a star that shows emission lines.

Description: Emission fills the lines a star of its type should show in
absorption, so matching such a star against reference spectra that hold the
absorption gives a wrong type: the Be star gamma Cas (catalog B0IVe) was
classified A5V, because its H-alpha and H-beta emission made it look like a
star with weak Balmer lines. These tests build a spectrum from a real bundled
reference plus an H-alpha emission line and check that the comparison leaves
the line out, keeps the right type, and says what it did.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    _get_reference_templates,
    classify_spectral_type,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum

RESOLUTION_ELEMENT_ANGSTROM = 50.0
WAVELENGTHS = np.arange(3800.0, 9000.0, 11.0)
RESPONSE = load_instrument_response("ZWO ASI 533MM Pro")


def _reference_with_emission(
    spectral_type: str = "B0V", emission_height: float = 0.5, seed: int = 4
) -> np.ndarray:
    """Build a blurred reference spectrum plus H-alpha emission.

    Parameters
    ----------
    spectral_type : `str`, optional
        The bundled reference to use.
    emission_height : `float`, optional
        The bump's height, as a fraction of the reference's level there.
    seed : `int`, optional
        The seed of the small noise added.

    Returns
    -------
    intensity : `numpy.ndarray`
        The spectrum on `WAVELENGTHS`, with the instrument's response removed
        (a corrected spectrum), scaled to about 1.
    """
    template_wavelength, template_flux = _get_reference_templates()[spectral_type]
    sample_spacing = float(np.median(np.diff(template_wavelength)))
    blurred = gaussian_filter1d(template_flux, RESOLUTION_ELEMENT_ANGSTROM / sample_spacing / 2.355)
    spectrum = np.interp(WAVELENGTHS, template_wavelength, blurred)
    spectrum = spectrum / np.median(spectrum)
    level = float(np.interp(6563.0, WAVELENGTHS, spectrum))
    spectrum = spectrum + emission_height * level * np.exp(-0.5 * ((WAVELENGTHS - 6563.0) / 34.0) ** 2)
    noise = np.random.default_rng(seed).normal(0.0, 0.004, WAVELENGTHS.size)
    return spectrum * (1.0 + noise)


def test_a_window_can_be_left_out_of_the_comparison() -> None:
    """Leaving the emission line out gives a better match and says so."""
    spectrum = _reference_with_emission()

    with_line = classify_spectral_type(WAVELENGTHS, spectrum, RESOLUTION_ELEMENT_ANGSTROM)
    without_line = classify_spectral_type(
        WAVELENGTHS,
        spectrum,
        RESOLUTION_ELEMENT_ANGSTROM,
        excluded_windows_angstrom=[(6480.0, 6650.0)],
    )

    assert without_line["rms"] < with_line["rms"]
    assert without_line["spectral_type"] == "B0V"
    assert without_line["excluded_windows_angstrom"] == [(6480.0, 6650.0)]
    assert with_line["excluded_windows_angstrom"] == []


def test_the_analysis_leaves_a_detected_emission_line_out_of_the_classification() -> None:
    """The classification leaves the emission out and says which line."""
    intensity = _reference_with_emission(emission_height=0.5)
    observed = intensity * RESPONSE.value_at(WAVELENGTHS)

    analysis = analyze_spectrum(
        WAVELENGTHS,
        observed,
        RESPONSE,
        is_quantum_efficiency_corrected=True,
        catalog_spectral_type="B0V",
    )

    emission = [f for f in analysis.features if f.get("kind") == "emission" and f["verdict"] == "detected"]
    assert [f["feature"] for f in emission] == ["Hydrogen Balmer series (H-alpha)"]
    windows = analysis.classification["excluded_windows_angstrom"]
    assert len(windows) == 1
    assert windows[0][0] < 6563.0 < windows[0][1]
    assert "H-alpha" in str(analysis.classification["reason"])
    assert "emission" in str(analysis.classification["reason"])
    assert analysis.classification["spectral_type"].startswith("B")


def test_a_spectrum_without_emission_excludes_nothing() -> None:
    """A plain reference spectrum is compared over its whole range."""
    intensity = _reference_with_emission(emission_height=0.0)
    observed = intensity * RESPONSE.value_at(WAVELENGTHS)

    analysis = analyze_spectrum(
        WAVELENGTHS,
        observed,
        RESPONSE,
        is_quantum_efficiency_corrected=True,
        catalog_spectral_type="B0V",
    )

    assert analysis.classification["excluded_windows_angstrom"] == []
    assert "emission" not in str(analysis.classification["reason"])


def test_a_weak_possible_emission_bump_is_also_left_out_but_an_inconclusive_one_is_not() -> None:
    """Only detected and possible emission is excluded."""
    intensity = _reference_with_emission(emission_height=0.0)
    observed = intensity * RESPONSE.value_at(WAVELENGTHS)

    analysis = analyze_spectrum(
        WAVELENGTHS,
        observed,
        RESPONSE,
        is_quantum_efficiency_corrected=True,
        catalog_spectral_type="B0V",
    )

    for feature in analysis.features:
        if feature.get("kind") == "emission":
            assert feature["verdict"] not in ("detected", "possible")
    assert analysis.classification["excluded_windows_angstrom"] == []


def test_the_classifier_still_treats_no_windows_as_before() -> None:
    """Without windows the result is what it was, apart from the new key."""
    spectrum = _reference_with_emission(emission_height=0.0)

    result = classify_spectral_type(WAVELENGTHS, spectrum, RESOLUTION_ELEMENT_ANGSTROM)

    assert result["spectral_type"] == "B0V"
    assert result["excluded_windows_angstrom"] == []
    assert result["rms"] == pytest.approx(0.0, abs=0.02)
