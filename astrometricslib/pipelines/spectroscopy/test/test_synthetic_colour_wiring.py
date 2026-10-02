"""Purpose: Unit tests for the colour-disagreement flag reaching a result.

Description: A spectrum's synthetic colour, when it disagrees with the
star's catalog B-V, should show up in the classification's note. This
exercises the full wiring across pre-processing (instrument response),
processing (`analyze_spectrum`) and post-processing (`compare_to_catalog`)
together, which is why it stays at the top level of `test/` rather than
in any one stage's folder. The colour measurement itself
(`synthetic_b_minus_v`) and the note-only checks
(`colour_disagreement_note`) are tested in isolation in
`test/processing/test_synthetic_colour.py` and
`test/post_processing/test_compare_to_catalog.py`.
"""

import numpy as np
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    apply_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import _get_reference_templates
from astrometricslib.pipelines.spectroscopy.processing.spectrum_analysis import analyze_spectrum


def _recorded_k2v() -> tuple[np.ndarray, np.ndarray]:
    """Give a K2V reference as the instrument would record it.

    Returns
    -------
    wavelength_angstrom, recorded : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The wavelengths and the brightness with the instrument's tilt on it.
    """
    wavelength, flux = _get_reference_templates()["K2V"]
    blurred = gaussian_filter1d(flux, 45.0 / 2.355 / 5.0)
    return wavelength, blurred * load_instrument_response("ZWO ASI 533MM Pro").value_at(wavelength)


def test_analysis_flags_a_colour_far_from_the_catalog() -> None:
    """A K2V spectrum on a star the catalog calls blue gets the note."""
    wavelength, recorded = _recorded_k2v()
    response = load_instrument_response("ZWO ASI 533MM Pro")

    analysis = analyze_spectrum(
        wavelength,
        recorded,
        apply_instrument_response(wavelength, recorded, response),
        catalog_spectral_type="K2",
        catalog_b_minus_v=-0.13,
    )

    assert analysis.classification["spectral_type"] == "K2V"
    assert "mag redder than the catalog colour" in str(analysis.classification["reason"])


def test_analysis_stays_quiet_when_the_colour_agrees() -> None:
    """The same spectrum with its real colour gets no colour note."""
    wavelength, recorded = _recorded_k2v()
    response = load_instrument_response("ZWO ASI 533MM Pro")

    analysis = analyze_spectrum(
        wavelength,
        recorded,
        apply_instrument_response(wavelength, recorded, response),
        catalog_spectral_type="K2",
        catalog_b_minus_v=0.91,
    )

    assert analysis.classification["reason"] is None


def test_analysis_without_a_catalog_colour_is_unchanged() -> None:
    """A star with no catalog colour behaves exactly as before."""
    wavelength, recorded = _recorded_k2v()
    response = load_instrument_response("ZWO ASI 533MM Pro")

    analysis = analyze_spectrum(
        wavelength,
        recorded,
        apply_instrument_response(wavelength, recorded, response),
        catalog_spectral_type="K2",
    )

    assert analysis.classification["reason"] is None
