"""Runs the standard analyses on one extracted spectrum.

Both the spectroscopy pipeline (when a spectrum is first extracted) and
the recompute script (when stored spectra are re-analyzed) need the same
steps: remove the instrument's tilt, compare with the reference spectra,
and test for the named absorption features. Keeping them here means the
two can never drift apart.
"""

from dataclasses import dataclass

import numpy as np

from astrometricslib.pipelines.spectroscopy.instrument_response import (
    apply_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    classify_spectral_type,
    nearest_reference_type,
    unclassified_result,
)
from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import detect_named_features


@dataclass(frozen=True)
class SpectrumAnalysis:
    """What the standard analyses found in one spectrum.

    Attributes
    ----------
    classification : `dict`
        The result of `classify_spectral_type` (or an "Unknown" result
        saying why no classification was made).
    features : `list` [`dict`]
        The result of `detect_named_features`.
    response_applied : `bool`
        Whether an instrument response was removed before classifying.
    """

    classification: dict[str, object]
    features: list[dict[str, object]]
    response_applied: bool


def analyze_spectrum(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    camera_name: str,
    is_quantum_efficiency_corrected: bool,
    catalog_spectral_type: str | None = None,
) -> SpectrumAnalysis:
    """Classify a spectrum and test it for the named absorption features.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `np.ndarray`
        The spectrum's brightness. Corrected for the sensor's quantum
        efficiency when one is known (see `is_quantum_efficiency_corrected`).
    camera_name : `str`
        The camera that took the spectrum, used to find its instrument
        response.
    is_quantum_efficiency_corrected : `bool`
        Whether `intensity` has had the sensor's quantum efficiency
        removed. The instrument response was derived from corrected
        spectra, so it is only applied to corrected ones.
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type, when known. It says what depth
        each feature should have; without it, a good spectrum match is
        used instead.

    Returns
    -------
    analysis : `SpectrumAnalysis`
        The classification and the feature results.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)

    response = load_instrument_response(camera_name) if is_quantum_efficiency_corrected else None
    if response is None:
        classification = unclassified_result(
            "no instrument response is available for this camera, so the spectrum cannot be compared"
        )
    else:
        classification = classify_spectral_type(
            wavelength_angstrom, apply_instrument_response(wavelength_angstrom, intensity, response)
        )

    # The catalog type says what kind of star this is without using the
    # spectrum being tested. A spectrum match is only a fallback, and only
    # when it is a good one, since a poor match would put a wrong
    # expectation behind the feature probabilities.
    expected_reference_type = nearest_reference_type(catalog_spectral_type)
    if (
        expected_reference_type is None
        and classification["spectral_type"] != "Unknown"
        and classification["match_quality"] == "good"
    ):
        expected_reference_type = str(classification["spectral_type"])

    features = detect_named_features(
        wavelength_angstrom, intensity, reference_spectral_type=expected_reference_type
    )
    return SpectrumAnalysis(classification, features, response is not None)
