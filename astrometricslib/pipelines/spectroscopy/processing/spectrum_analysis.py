"""Runs the standard analyses on one extracted spectrum.

Both the spectroscopy pipeline (when a spectrum is first extracted) and
the recompute script (when stored spectra are re-analyzed) need the same
steps: compare an already response-corrected spectrum with the reference
spectra, and test for the named absorption features and emission lines.
Keeping them here means the two can never drift apart. Removing the
instrument's own tilt happens before this, alongside the
quantum-efficiency correction (see `pre_processing.instrument_response`).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from astrometricslib.models.spectroscopy_quality import CatalogComparison
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import compare_to_catalog
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_extinction import ExtinctionCorrection
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    ResolutionProfile,
    resolve_resolution_element_angstrom,
)
from astrometricslib.pipelines.spectroscopy.processing.emission_line_detector import (
    detect_emission_lines,
    is_emission_line_source,
    line_half_width_angstrom,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    GIANT_REFERENCE_SPECTRAL_TYPES,
    classify_spectral_type,
    is_catalog_giant,
    nearest_reference_type,
    unclassified_result,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import (
    KIND_EMISSION,
    VERDICT_DETECTED,
    VERDICT_POSSIBLE,
    detect_named_features,
)
from astrometricslib.pipelines.spectroscopy.processing.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
    estimate_spectrum_signal_to_noise,
)
from astrometricslib.pipelines.spectroscopy.processing.synthetic_colour import synthetic_b_minus_v


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
    response_corrected_intensity : `list` [`float`] or `None`
        The spectrum with the instrument's own tilt removed (see
        `apply_instrument_response`) -- the same brightness values the
        classifier compared against reference spectra. This is the
        physically meaningful "normalized flux" for display and for
        measuring line depths; `None` when no instrument response was
        available or applied (`response_applied` is `False`), in which
        case a caller should fall back to QE-corrected or raw counts and
        say so, rather than label them "normalized".
    catalog_comparison : `CatalogComparison` or `None`
        How this classification compares with the star's catalog entry
        (see `post_processing.compare_to_catalog`). `None` when nothing
        was classified, so there was nothing to compare.
    extinction_correction : `dict` or `None`
        Whether the airmass extinction correction was applied to
        `response_corrected_intensity`, and the target and reference
        airmasses (see `ExtinctionCorrection.as_dict`). `None` when the
        caller did not try the correction.
    synthetic_b_minus_v : `float` or `None`
        The B-V colour measured from the spectrum itself (see
        `synthetic_colour`), in magnitudes. `None` when the spectrum was not
        classified, so the colour was not tried, or when it could not be
        measured.
    """

    classification: dict[str, object]
    features: list[dict[str, object]]
    response_applied: bool
    resolution_element_angstrom: float
    is_resolution_measured: bool
    signal_to_noise: float | None = None
    emission_lines: list[dict[str, object]] = field(default_factory=list)
    is_emission_line_source: bool = False
    response_corrected_intensity: list[float] | None = None
    catalog_comparison: CatalogComparison | None = None
    extinction_correction: dict[str, object] | None = None
    synthetic_b_minus_v: float | None = None


# The `stellar_spectral_type` label given to extended objects (clusters
# and nebulae; their `spectral_type` keeps the object kind, for example
# "PN"). Callers use it to set `is_extended_target`.
EXTENDED_TARGET_SPECTRAL_TYPE = "Cluster"


# An emission line is left out of the classification over this many resolution
# elements either side of where it was measured. A line the instrument has
# blurred is down to 0.2% of its height at 1.5 of its own widths, but a real
# emission line is wider than the instrument's blur: gamma Cas's H-alpha
# measured about 80 A across against a 50 A resolution element, so the window
# is two resolution elements (100 A, 2.9 sigma of that line) either side to
# leave under 2% of its height in the comparison. Checked on that one star.
EMISSION_EXCLUSION_HALF_WIDTH_RESOLUTION_ELEMENTS = 2.0


def _emission_windows(
    features: list[dict[str, object]], resolution_element_angstrom: float
) -> tuple[list[tuple[float, float]], list[str]]:
    """Find the windows of the emission lines the feature test found.

    Emission fills the lines a star of its type shows in absorption, so
    comparing the star with the reference spectra there would make it look
    like a different type. Only emission that is detected or possible counts.

    Parameters
    ----------
    features : `list` [`dict`]
        The results of `detect_named_features`.
    resolution_element_angstrom : `float`
        The instrument's resolution element, in Angstroms.

    Returns
    -------
    windows : `list` [`tuple` [`float`, `float`]]
        The (low, high) window around each emission line, in Angstroms.
    names : `list` [`str`]
        The short names of those lines, for example ``"H-alpha"``.
    """
    half_width = EMISSION_EXCLUSION_HALF_WIDTH_RESOLUTION_ELEMENTS * resolution_element_angstrom
    windows: list[tuple[float, float]] = []
    names: list[str] = []
    for feature in features:
        if feature.get("kind") != KIND_EMISSION or feature["verdict"] not in (
            VERDICT_DETECTED,
            VERDICT_POSSIBLE,
        ):
            continue
        centre = float(feature["measured_wavelength_angstrom"])  # type: ignore[arg-type]
        windows.append((centre - half_width, centre + half_width))
        names.append(str(feature["feature"]).split("(")[-1].strip(")"))
    return windows, names


# A flagged neighbour is left out of the comparison only when its flux is at
# least this fraction of the target's own -- a neighbour a hundred times
# fainter geometrically overlaps but cannot realistically move the target's
# shape. A neighbour with no known flux (`neighbor_flux_ratio` is `None`) is
# left out anyway: that is the more cautious reading of missing evidence,
# not a measured value. Provisional, no real pair has been checked with this
# threshold yet.
NEIGHBOR_CONTAMINATION_MINIMUM_FLUX_RATIO = 0.05


def _neighbor_contamination_windows(
    possible_neighbor_contamination: list[dict[str, float | None]] | None,
) -> list[tuple[float, float]]:
    """Turn flagged neighbour windows into (low, high) pairs worth excluding.

    Parameters
    ----------
    possible_neighbor_contamination : `list` [`dict`], optional
        `StellarObject.spectroscopy.possible_neighbor_contamination` (see
        `find_neighbor_contamination_windows`): every window where another
        known star's position falls inside this star's own reading box,
        each with that neighbour's flux relative to this star's own.

    Returns
    -------
    windows : `list` [`tuple` [`float`, `float`]]
        The windows severe enough to leave out of the comparison (see
        `NEIGHBOR_CONTAMINATION_MINIMUM_FLUX_RATIO`).
    """
    if not possible_neighbor_contamination:
        return []
    windows: list[tuple[float, float]] = []
    for window in possible_neighbor_contamination:
        ratio = window.get("neighbor_flux_ratio")
        if ratio is not None and ratio < NEIGHBOR_CONTAMINATION_MINIMUM_FLUX_RATIO:
            continue
        windows.append((float(window["wavelength_low_angstrom"]), float(window["wavelength_high_angstrom"])))
    return windows


def analyze_spectrum(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    response_corrected_intensity: np.ndarray | None,
    catalog_spectral_type: str | None = None,
    trail_width_px: Sequence[float] | None = None,
    extraction_box_width_px: float | None = None,
    is_extended_target: bool = False,
    catalog_b_minus_v: float | None = None,
    resolution_profile: ResolutionProfile | None = None,
    possible_neighbor_contamination: list[dict[str, float | None]] | None = None,
    extinction_correction: ExtinctionCorrection | None = None,
    intensity_errors: np.ndarray | None = None,
    response_corrected_intensity_errors: np.ndarray | None = None,
) -> SpectrumAnalysis:
    """Classify a spectrum and test it for the named absorption features.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The spectrum's wavelengths, in Angstroms.
    intensity : `np.ndarray`
        The spectrum's brightness. Corrected for the sensor's quantum
        efficiency when one is known.
    response_corrected_intensity : `np.ndarray` or `None`
        `intensity` with the instrument's full response (grating, optics,
        atmosphere) also removed (see
        `pre_processing.instrument_response.apply_instrument_response`),
        or `None` when no instrument response was available or `intensity`
        was not quantum-efficiency corrected to begin with -- the response
        was derived from corrected spectra, so it is only ever applied to
        corrected ones. The caller computes this alongside the
        quantum-efficiency correction, before calling this function; it is
        the same brightness values the classifier compares against
        reference spectra.
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type, when known. It says what depth
        each feature should have; without it, a good spectrum match is
        used instead.
    trail_width_px : `Sequence` [`float`], optional
        The width (sigma, in pixels) of the spectrum's trail at each
        sample, lined up one to one with `wavelength_angstrom`. It gives
        this spectrum's own resolution (see `spectral_resolution`); when
        it is missing or unusable, a fixed fallback resolution is used.
    resolution_profile : `ResolutionProfile`, optional
        How the instrument's blur changes along the spectrum (see
        `load_line_spread_profile`). When given, the references are blurred
        by it at each wavelength; the single width from `trail_width_px`
        still sets the feature windows and the counting of independent
        measurements.
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
    possible_neighbor_contamination : `list` [`dict`], optional
        `StellarObject.spectroscopy.possible_neighbor_contamination` (see
        `find_neighbor_contamination_windows` in `pipeline.py`): windows
        where a different, independently detected star's own known
        position falls inside this star's reading box. Severe ones (see
        `NEIGHBOR_CONTAMINATION_MINIMUM_FLUX_RATIO`) are left out of the
        comparison alongside the emission windows, not instead of them --
        the two causes are unrelated and either can apply to the same
        spectrum.
    extinction_correction : `ExtinctionCorrection`, optional
        The record of the airmass extinction correction the caller applied
        to `response_corrected_intensity` (see
        `pre_processing.atmospheric_extinction`). The analysis does not
        apply it; it stores the record in the result so the airmasses and
        the applied or skipped status travel with the spectrum.
    intensity_errors : `np.ndarray`, optional
        The 1-sigma error of each `intensity` value (see
        `pre_processing.intensity_variance`). The feature test uses it to give
        each feature an equivalent width and its error. It changes no
        verdict.
    response_corrected_intensity_errors : `np.ndarray`, optional
        The 1-sigma error of each `response_corrected_intensity` value. The
        classifier uses it to give each reference a reduced chi-square next to
        its RMS. It changes no ranking, decision or threshold.

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

    # Emission is looked for first, because the classification has to leave
    # it out. The catalog type gives the feature test its reference; when the
    # catalog has none, the test is run again below with the spectrum's own
    # best match.
    expected_reference_type = nearest_reference_type(catalog_spectral_type)
    features = detect_named_features(
        wavelength_angstrom,
        intensity,
        reference_spectral_type=expected_reference_type,
        resolution_element_angstrom=resolution_element_angstrom,
        resolution_profile=resolution_profile,
        errors=intensity_errors,
    )
    emission_windows, emission_names = _emission_windows(features, resolution_element_angstrom)
    # Two unrelated causes can each make part of the spectrum untrustworthy
    # for classification -- emission filling an absorption line, and a
    # different star's light landing in the box -- so both sets of windows
    # are excluded together, not one replacing the other.
    excluded_windows = emission_windows + _neighbor_contamination_windows(possible_neighbor_contamination)

    corrected_intensity = (
        None
        if response_corrected_intensity is None
        else np.asarray(response_corrected_intensity, dtype=float)
    )
    if corrected_intensity is None:
        classification = unclassified_result(
            "no instrument response is available for this camera, so the spectrum cannot be compared"
        )
    else:
        classification = classify_spectral_type(
            wavelength_angstrom,
            corrected_intensity,
            resolution_element_angstrom=resolution_element_angstrom,
            excluded_windows_angstrom=excluded_windows,
            resolution_profile=resolution_profile,
            intensity_errors=response_corrected_intensity_errors,
        )

    comparison: CatalogComparison | None = None
    synthetic_colour: float | None = None
    if classification["spectral_type"] != "Unknown" and not classification["reason"]:
        # For a star the catalog calls a giant, also find the closest giant
        # reference, so the note can say what the spectrum looks like once
        # the (unreliable) luminosity class is set aside.
        closest_giant = None
        if corrected_intensity is not None and is_catalog_giant(catalog_spectral_type):
            giant_result = classify_spectral_type(
                wavelength_angstrom,
                corrected_intensity,
                resolution_element_angstrom=resolution_element_angstrom,
                reference_types=GIANT_REFERENCE_SPECTRAL_TYPES,
                excluded_windows_angstrom=excluded_windows,
                resolution_profile=resolution_profile,
                intensity_errors=response_corrected_intensity_errors,
            )
            giant_rms = giant_result["classification_rms"]
            if giant_result["spectral_type"] != "Unknown" and isinstance(giant_rms, int | float):
                closest_giant = (str(giant_result["spectral_type"]), float(giant_rms))
        synthetic_colour = (
            synthetic_b_minus_v(wavelength_angstrom, corrected_intensity)
            if corrected_intensity is not None
            else None
        )
        comparison = compare_to_catalog(
            catalog_spectral_type,
            str(classification["spectral_type"]),
            closest_giant,
            catalog_b_minus_v,
            synthetic_colour,
        )
        emission_note = (
            f"the {' and '.join(emission_names)} emission was left out of the comparison"
            if emission_names
            else ""
        )
        classification["reason"] = (
            "; ".join(note for note in (emission_note, comparison.joined_note()) if note) or None
        )

    # The catalog type says what kind of star this is without using the
    # spectrum being tested. A spectrum match is only a fallback, and only
    # when it is a good one, since a poor match would put a wrong
    # expectation behind the feature probabilities.
    if (
        expected_reference_type is None
        and classification["spectral_type"] != "Unknown"
        and classification["match_quality"] == "good"
    ):
        features = detect_named_features(
            wavelength_angstrom,
            intensity,
            reference_spectral_type=str(classification["spectral_type"]),
            resolution_element_angstrom=resolution_element_angstrom,
            resolution_profile=resolution_profile,
            errors=intensity_errors,
        )
    return SpectrumAnalysis(
        classification,
        features,
        corrected_intensity is not None,
        resolution_element_angstrom,
        is_resolution_measured,
        signal_to_noise,
        emission_lines,
        is_emission_source,
        response_corrected_intensity=(
            corrected_intensity.tolist() if corrected_intensity is not None else None
        ),
        catalog_comparison=comparison,
        extinction_correction=extinction_correction.as_dict() if extinction_correction is not None else None,
        synthetic_b_minus_v=synthetic_colour,
    )
