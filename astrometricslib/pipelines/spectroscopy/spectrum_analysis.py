"""Runs the standard analyses on one extracted spectrum.

Both the spectroscopy pipeline (when a spectrum is first extracted) and
the recompute script (when stored spectra are re-analyzed) need the same
steps: remove the instrument's tilt, compare with the reference spectra,
and test for the named absorption features. Keeping them here means the
two can never drift apart.
"""

from collections.abc import Sequence
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
from astrometricslib.pipelines.spectroscopy.spectral_resolution import resolve_resolution_element_angstrom
from astrometricslib.pipelines.spectroscopy.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
    estimate_spectrum_signal_to_noise,
)


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
    resolution_element_angstrom : `float`
        How much the instrument blurred this spectrum, in Angstroms. Both
        the classification and the feature tests were run at this width.
    is_resolution_measured : `bool`
        `True` when the resolution came from this spectrum's own trail
        width, `False` when the fixed fallback was used.
    signal_to_noise : `float` or `None`
        How strongly the spectrum stands out from its own scatter (see
        `estimate_spectrum_signal_to_noise`), `None` when it could not be
        judged.
    """

    classification: dict[str, object]
    features: list[dict[str, object]]
    response_applied: bool
    resolution_element_angstrom: float
    is_resolution_measured: bool
    signal_to_noise: float | None = None


def analyze_spectrum(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    camera_name: str,
    is_quantum_efficiency_corrected: bool,
    catalog_spectral_type: str | None = None,
    trail_width_px: Sequence[float] | None = None,
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
    trail_width_px : `Sequence` [`float`], optional
        The width (sigma, in pixels) of the spectrum's trail at each
        sample, lined up one to one with `wavelength_angstrom`. It gives
        this spectrum's own resolution (see `spectral_resolution`); when
        it is missing or unusable, a fixed fallback resolution is used.

    Returns
    -------
    analysis : `SpectrumAnalysis`
        The classification and the feature results.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)

    # How much the instrument blurred THIS spectrum. The classifier and the
    # feature detector both compare the spectrum with sharper reference
    # spectra, so they need to blur the references by the same amount.
    resolution_element_angstrom, is_resolution_measured = resolve_resolution_element_angstrom(
        wavelength_angstrom, trail_width_px
    )

    # Before anything is compared with anything: is there a spectrum at all?
    # A star too faint for the frames leaves only sky noise, and the
    # classifier and the feature detector would still give it an answer.
    signal_to_noise = estimate_spectrum_signal_to_noise(
        wavelength_angstrom, intensity, resolution_element_angstrom
    )
    if signal_to_noise is not None and signal_to_noise < MINIMUM_SPECTRUM_SIGNAL_TO_NOISE:
        return SpectrumAnalysis(
            unclassified_result(
                "no measurable spectrum: the star is too faint here, its signal-to-noise is "
                f"{signal_to_noise:.1f} per resolution element and at least "
                f"{MINIMUM_SPECTRUM_SIGNAL_TO_NOISE:g} is needed"
            ),
            [],
            False,
            resolution_element_angstrom,
            is_resolution_measured,
            signal_to_noise,
        )

    response = load_instrument_response(camera_name) if is_quantum_efficiency_corrected else None
    if response is None:
        classification = unclassified_result(
            "no instrument response is available for this camera, so the spectrum cannot be compared"
        )
    else:
        classification = classify_spectral_type(
            wavelength_angstrom,
            apply_instrument_response(wavelength_angstrom, intensity, response),
            resolution_element_angstrom=resolution_element_angstrom,
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
        wavelength_angstrom,
        intensity,
        reference_spectral_type=expected_reference_type,
        resolution_element_angstrom=resolution_element_angstrom,
    )
    return SpectrumAnalysis(
        classification,
        features,
        response is not None,
        resolution_element_angstrom,
        is_resolution_measured,
        signal_to_noise,
    )
