"""Purpose: Unit tests for the instrument response and spectrum analysis.

Description: Verifies that a response derived from a reference star
recovers a known tilt, that applying it removes the tilt, and that a
stored response exists for the camera this library ships settings for.
Also checks the combined analysis used by the pipeline and the recompute
script.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.instrument_response import (
    apply_instrument_response,
    derive_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum


def _blurred(spectral_type: str) -> tuple[np.ndarray, np.ndarray]:
    """Give a reference spectrum blurred to the instrument's resolution.

    Returns
    -------
    wavelength_angstrom, flux : `tuple` [`np.ndarray`, `np.ndarray`]
        The reference wavelengths and blurred flux.
    """
    wavelength, flux = _get_reference_templates()[spectral_type]
    return wavelength, gaussian_filter1d(flux, 30.0 / 2.355 / 5.0)


def _known_tilt(wavelength_angstrom: np.ndarray) -> np.ndarray:
    """Build a smooth tilt like a grating and sensor would add.

    Returns
    -------
    tilt : `np.ndarray`
        A curve rising to a peak near 5300 A and falling toward the red.
    """
    return np.exp(-(((wavelength_angstrom - 5300.0) / 2500.0) ** 2))


def test_a_derived_response_recovers_a_known_tilt():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify dividing out the derived response returns the reference shape."""
    wavelength, flux = _blurred("A0V")
    observed = flux * _known_tilt(wavelength)

    response = derive_instrument_response(wavelength, observed, "A0V", "TestCam", "synthetic")
    corrected = apply_instrument_response(wavelength, observed, response)

    inside = np.isfinite(corrected)
    ratio = corrected[inside] / flux[inside]
    ratio /= np.median(ratio)
    assert np.percentile(np.abs(ratio - 1.0), 90) < 0.05


def test_the_response_is_not_applied_outside_its_valid_range():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify samples outside the fitted range become NaN."""
    wavelength, flux = _blurred("A0V")
    response = derive_instrument_response(wavelength, flux * _known_tilt(wavelength), "A0V", "TestCam", "x")

    corrected = apply_instrument_response(wavelength, flux, response)

    assert np.isnan(corrected[wavelength < 4200.0]).all()
    assert np.isnan(corrected[wavelength > 8000.0]).all()
    assert np.isfinite(corrected[(wavelength >= 4200.0) & (wavelength <= 8000.0)]).all()


def test_an_unknown_reference_type_is_rejected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify asking for a reference that is not bundled is an error."""
    wavelength, flux = _blurred("A0V")
    with pytest.raises(ValueError):
        derive_instrument_response(wavelength, flux, "Z9Z", "TestCam", "x")


def test_a_response_is_stored_for_the_asi533_and_matches_camera_names_loosely():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the shipped response loads, whatever the spacing in the name."""
    spaced = load_instrument_response("ZWO ASI 533MM Pro")
    unspaced = load_instrument_response("ZWO ASI533MM Pro")

    assert spaced is not None
    assert unspaced == spaced
    assert spaced.reference_type == "A0V"
    assert load_instrument_response("Some Other Camera") is None


def test_analysis_recovers_the_type_of_a_spectrum_the_instrument_would_record():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a K2V reference seen through the instrument is classified K2V."""
    wavelength, flux = _blurred("K2V")
    response = load_instrument_response("ZWO ASI 533MM Pro")
    recorded = flux * response.value_at(wavelength)

    analysis = analyze_spectrum(
        wavelength, recorded, load_instrument_response("ZWO ASI 533MM Pro"), True, catalog_spectral_type="K2"
    )

    assert analysis.response_applied is True
    assert analysis.classification["spectral_type"] == "K2V"
    assert analysis.classification["match_quality"] == "good"
    assert len(analysis.features) == 8


def test_analysis_without_a_response_does_not_classify_but_still_tests_features():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an unknown camera gets features but no spectral type."""
    wavelength, flux = _blurred("A0V")

    analysis = analyze_spectrum(wavelength, flux, load_instrument_response("Some Other Camera"), True)

    assert analysis.response_applied is False
    assert analysis.classification["spectral_type"] == "Unknown"
    assert len(analysis.features) == 8


def test_the_fit_range_can_be_widened_to_the_full_reference_range():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a response can be fitted over the camera's full range."""
    wavelength, flux = _blurred("A0V")
    observed = flux * _known_tilt(wavelength)

    response = derive_instrument_response(
        wavelength, observed, "A0V", "TestCam", "x", wavelength_range_angstrom=(4200.0, 10000.0)
    )
    corrected = apply_instrument_response(wavelength, observed, response)

    assert response.maximum_wavelength_angstrom == pytest.approx(10000.0)
    assert np.isfinite(corrected[(wavelength >= 4200.0) & (wavelength <= 10000.0)]).all()


def test_the_references_cover_the_cameras_full_range():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every bundled reference runs from 3000 to 10000 A."""
    for spectral_type, (wavelength, flux) in _get_reference_templates().items():
        assert wavelength[0] == pytest.approx(3000.0), spectral_type
        assert wavelength[-1] == pytest.approx(10000.0), spectral_type
        assert (flux[wavelength >= 4000.0] > 0).all(), spectral_type
