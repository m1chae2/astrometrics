"""Runs the standard analyses on one extracted spectrum.

Both the spectroscopy pipeline (when a spectrum is first extracted) and
the recompute script (when stored spectra are re-analyzed) need the same
steps: remove the instrument's tilt, compare with the reference spectra,
and test for the named absorption features and emission lines. Keeping
them here means the two can never drift apart.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from astrometricslib.pipelines.spectroscopy.emission_line_detector import (
    detect_emission_lines,
    is_emission_line_source,
    line_half_width_angstrom,
)
from astrometricslib.pipelines.spectroscopy.instrument_response import (
    apply_instrument_response,
    load_instrument_response,
)
from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    GIANT_REFERENCE_SPECTRAL_TYPES,
    catalog_disagreement_note,
    classify_spectral_type,
    is_catalog_giant,
    luminosity_class_note,
    nearest_reference_type,
    unclassified_result,
)
from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import detect_named_features
from astrometricslib.pipelines.spectroscopy.spectral_resolution import resolve_resolution_element_angstrom
from astrometricslib.pipelines.spectroscopy.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
    estimate_spectrum_signal_to_noise,
)
from astrometricslib.pipelines.spectroscopy.synthetic_colour import (
    colour_disagreement_note,
    synthetic_b_minus_v,
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
    emission_lines: list[dict[str, object]] = field(default_factory=list)
    is_emission_line_source: bool = False


# The `stellar_spectral_type` label given to extended objects (clusters
# and nebulae; their `spectral_type` keeps the object kind, for example
# "PN"). Callers use it to set `is_extended_target`.
EXTENDED_TARGET_SPECTRAL_TYPE = "Cluster"


def analyze_spectrum(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    camera_name: str,
    is_quantum_efficiency_corrected: bool,
    catalog_spectral_type: str | None = None,
    trail_width_px: Sequence[float] | None = None,
    extraction_box_width_px: float | None = None,
    is_extended_target: bool = False,
    catalog_b_minus_v: float | None = None,
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
    extraction_box_width_px : `float`, optional
        The width, in pixels, of the box the spectrum was extracted from.
        It sets how wide the emission lines are expected to be (see
        `line_half_width_angstrom`); without it the lines are taken to be
        as narrow as the instrument blur, which suits a point source.
    catalog_b_minus_v : `float`, optional
        The star's catalog B-V colour. When given, and the colour the
        spectrum implies (see `synthetic_colour`) is more than half a
        magnitude away, the classification's note says so.
    is_extended_target : `bool`, optional
        Whether the catalog says this is an extended object (its
        `stellar_spectral_type` is `EXTENDED_TARGET_SPECTRAL_TYPE`). Only
        such targets never get a stellar classification: it is replaced by
        an "Unknown" result saying why (see below). An ordinary star's is
        never replaced.

    Returns
    -------
    analysis : `SpectrumAnalysis`
        The classification, the feature results and the emission lines.
        When `is_extended_target` is set, the stellar classification is
        replaced by an "Unknown" result saying so and the absorption
        features are left empty, since neither describes a nebula or a
        cluster's light. The emission lines are still tested and reported.
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

    half_width_angstrom = (
        line_half_width_angstrom(wavelength_angstrom, extraction_box_width_px)
        if extraction_box_width_px is not None
        else resolution_element_angstrom
    )
    emission_lines = detect_emission_lines(
        wavelength_angstrom,
        intensity,
        max(half_width_angstrom, resolution_element_angstrom),
        resolution_element_angstrom=resolution_element_angstrom,
    )
    is_emission_source = is_emission_line_source(emission_lines)
    if is_emission_source and is_extended_target:
        return SpectrumAnalysis(
            unclassified_result(
                "glowing gas: the spectrum is made of emission lines, so a stellar type does not apply"
            ),
            [],
            False,
            resolution_element_angstrom,
            is_resolution_measured,
            signal_to_noise,
            emission_lines,
            True,
        )

    if is_extended_target:
        # No emission lines were confirmed, but the light is still a whole
        # nebula's or cluster's, not one star's. Matching it to a single-star
        # reference gave M 27 (a planetary nebula) G8V and M 13 (a globular
        # cluster) K3V at 0.89, both meaningless. The absorption-line tests
        # are left empty for the same reason.
        return SpectrumAnalysis(
            unclassified_result(
                "extended object: the light is a whole nebula's or cluster's, not one star's, so a "
                "single stellar type does not apply"
            ),
            [],
            False,
            resolution_element_angstrom,
            is_resolution_measured,
            signal_to_noise,
            emission_lines,
            False,
        )

    response = load_instrument_response(camera_name) if is_quantum_efficiency_corrected else None
    corrected_intensity = None
    if response is None:
        classification = unclassified_result(
            "no instrument response is available for this camera, so the spectrum cannot be compared"
        )
    else:
        corrected_intensity = apply_instrument_response(wavelength_angstrom, intensity, response)
        classification = classify_spectral_type(
            wavelength_angstrom,
            corrected_intensity,
            resolution_element_angstrom=resolution_element_angstrom,
        )

    if classification["spectral_type"] != "Unknown" and not classification["reason"]:
        # For a star the catalog calls a giant, also find the closest giant
        # reference, so the note can say what the spectrum looks like once
        # the (unreliable) luminosity class is set aside.
        closest_giant = None
        if response is not None and is_catalog_giant(catalog_spectral_type):
            giant_result = classify_spectral_type(
                wavelength_angstrom,
                corrected_intensity,
                resolution_element_angstrom=resolution_element_angstrom,
                reference_types=GIANT_REFERENCE_SPECTRAL_TYPES,
            )
            if giant_result["spectral_type"] != "Unknown":
                closest_giant = (str(giant_result["spectral_type"]), float(giant_result["rms"]))  # type: ignore[arg-type]
        colour_note = ""
        if corrected_intensity is not None:
            colour_note = colour_disagreement_note(
                catalog_b_minus_v, synthetic_b_minus_v(wavelength_angstrom, corrected_intensity)
            )
        notes = [
            note
            for note in (
                colour_note,
                catalog_disagreement_note(catalog_spectral_type, str(classification["spectral_type"])),
                luminosity_class_note(
                    catalog_spectral_type, str(classification["spectral_type"]), closest_giant
                ),
            )
            if note
        ]
        classification["reason"] = "; ".join(notes) or None

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
        emission_lines,
        is_emission_source,
    )
