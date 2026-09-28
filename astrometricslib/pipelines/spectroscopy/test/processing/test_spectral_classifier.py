"""Purpose: Unit tests for template-matching spectral classification.

Description: Verifies classify_spectral_type against the real bundled
Pickles (1998) reference spectra -- a template matched against itself
(plus noise) must win its own comparison, and a hot blue-white star's
spectrum must not be confused for a cool red one. Also covers the two
"not enough to go on" paths (too few points, a flat/degenerate signal)
and sanity-checks the bundled reference data itself, since a corrupted
CSV would otherwise fail silently as a bad classification rather than
a loud error.

Tests of the post-processing trust/comparison functions that used to
live in this module (`is_classification_low_confidence`,
`is_classification_ambiguous`, `build_spectral_classification_concerns`,
`catalog_disagreement_note`, `luminosity_class_note`) now live in
`test/post_processing/test_assess_output_quality.py` and
`test/post_processing/test_compare_to_catalog.py`.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    _RANKING_SOFTMAX_TEMPERATURE,
    GIANT_REFERENCE_SPECTRAL_TYPES,
    REFERENCE_SPECTRAL_TYPES,
    UNRELIABLE_MATCH_RMS_THRESHOLD,
    _get_reference_templates,
    _rank_by_probability,
    classify_spectral_type,
    nearest_reference_type,
)


def test_bundled_reference_templates_are_well_formed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every shipped template loads, and its data makes sense."""
    templates = _get_reference_templates()

    assert set(templates) == set(REFERENCE_SPECTRAL_TYPES) | set(GIANT_REFERENCE_SPECTRAL_TYPES)
    assert len(GIANT_REFERENCE_SPECTRAL_TYPES) == len(set(GIANT_REFERENCE_SPECTRAL_TYPES)) == 56
    for spectral_type, (wavelength, flux) in templates.items():
        assert wavelength.size == flux.size > 0, spectral_type
        assert np.all(np.diff(wavelength) > 0), f"{spectral_type} wavelengths must be increasing"
        assert np.all(flux >= 0), f"{spectral_type} flux must be non-negative"


def test_a_template_matched_against_itself_wins_with_high_confidence():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify self-matching (plus noise) picks the correct type."""
    templates = _get_reference_templates()
    wavelength, flux = templates["G0V"]
    rng = np.random.default_rng(seed=0)
    noisy_flux = flux * (1.0 + rng.normal(0.0, 0.02, size=flux.size))

    result = classify_spectral_type(wavelength, noisy_flux)

    assert result["spectral_type"] == "G0V"
    assert result["confidence"] > 0.95


def test_ranked_types_puts_the_winner_first_and_sums_to_one():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the probability-ranked list agrees with the single best match."""
    templates = _get_reference_templates()
    wavelength, flux = templates["K0V"]

    result = classify_spectral_type(wavelength, flux)

    assert result["ranked_types"], "expected at least one ranked candidate"
    assert result["ranked_types"][0]["spectral_type"] == result["spectral_type"]
    assert result["ranked_types"][0]["probability"] == max(
        entry["probability"] for entry in result["ranked_types"]
    )
    probabilities = [entry["probability"] for entry in result["ranked_types"]]
    assert probabilities == sorted(probabilities, reverse=True)
    assert abs(sum(probabilities) - 1.0) < 1e-9


def test_rank_by_probability_matches_a_hand_computed_softmax():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Pins the softmax weights, now computed via `scipy.special.softmax`."""
    rms_by_type = {"A0V": 0.02, "A2V": 0.05, "F0V": 0.20}
    correlation_by_type = {"A0V": 0.99, "A2V": 0.97, "F0V": 0.80}

    ranked = _rank_by_probability(rms_by_type, correlation_by_type)

    differences = np.array([0.02, 0.05, 0.20])
    scaled = -(differences - differences.min()) / _RANKING_SOFTMAX_TEMPERATURE
    weights = np.exp(scaled)
    expected = dict(zip(["A0V", "A2V", "F0V"], weights / weights.sum(), strict=True))

    assert [entry["spectral_type"] for entry in ranked] == ["A0V", "A2V", "F0V"]
    for entry in ranked:
        assert entry["probability"] == pytest.approx(expected[entry["spectral_type"]])


def test_a_hot_blue_star_is_not_confused_for_a_cool_red_one():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a clear hot/cool pair lands on the right side of the sequence."""
    templates = _get_reference_templates()

    hot_wavelength, hot_flux = templates["O5V"]
    hot_result = classify_spectral_type(hot_wavelength, hot_flux)
    assert hot_result["spectral_type"] in ("O5V", "B0V", "B8V")

    cool_wavelength, cool_flux = templates["M5V"]
    cool_result = classify_spectral_type(cool_wavelength, cool_flux)
    assert cool_result["spectral_type"] in ("M5V", "M0V", "K5V")


def test_too_few_points_returns_unknown_without_crashing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a handful of points isn't enough to attempt a match."""
    result = classify_spectral_type(
        wavelength_angstrom=np.array([5000.0, 5010.0, 5020.0]),
        intensity=np.array([1.0, 1.1, 0.9]),
    )

    assert result["spectral_type"] == "Unknown"
    assert result["confidence"] is None
    assert result["correlation_by_type"] == {}
    assert result["ranked_types"] == []


def test_a_flat_spectrum_returns_unknown_without_crashing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a constant signal (zero variance) doesn't blow up the math."""
    wavelength = np.linspace(3600.0, 7500.0, 200)
    flat_intensity = np.full_like(wavelength, 500.0)

    result = classify_spectral_type(wavelength, flat_intensity)

    assert result["spectral_type"] == "Unknown"
    assert result["confidence"] is None
    assert result["ranked_types"] == []


def test_a_spectrum_covering_too_little_of_the_range_is_not_classified():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum that stops early gets no type."""
    wavelength, flux = _get_reference_templates()["G0V"]
    keep = wavelength <= 4500.0  # only 1500 A of the spectrum, less than the 2500 A needed

    result = classify_spectral_type(wavelength[keep], flux[keep])

    assert result["spectral_type"] == "Unknown"
    assert result["confidence"] is None
    assert "covers only" in result["reason"]


def test_a_spectrum_unlike_every_reference_is_flagged_as_a_poor_match():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an odd tilt gets a poor-match flag."""
    wavelength, flux = _get_reference_templates()["G0V"]
    # A bump no star has, strong enough to be a poor match (0.176 here) but
    # not so strong that no reference matches at all (see the next test).
    tilted = flux * np.exp(-(((wavelength - 6000.0) / 900.0) ** 2) * 0.05)

    result = classify_spectral_type(wavelength, tilted)

    assert result["match_quality"] == "poor"
    assert result["rms"] > 0.15


def test_the_score_separates_types_that_correlation_cannot():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an A0V spectrum is far from F6V by score though they correlate.

    This is the failure the score replaced: with correlation, an A0V
    spectrum correlates almost as well with an F6V reference as with A0V.
    """
    wavelength, flux = _get_reference_templates()["A0V"]
    # This synthetic instrument blurs by 30 A, and the classifier is told so,
    # the way the real pipeline tells it each spectrum's measured resolution.
    synthetic_resolution_angstrom = 30.0
    blurred = gaussian_filter1d(flux, synthetic_resolution_angstrom / 2.355 / 5.0)  # what it would record
    # The instrument response leaves only 4200-8000 A usable, so only that
    # part is compared in practice.
    blurred[(wavelength < 4200.0) | (wavelength > 8000.0)] = np.nan

    result = classify_spectral_type(
        wavelength, blurred, resolution_element_angstrom=synthetic_resolution_angstrom
    )

    by_type = {entry["spectral_type"]: entry for entry in result["ranked_types"]}
    assert result["spectral_type"] == "A0V"
    assert by_type["A0V"]["rms"] < 0.01
    assert by_type["F6V"]["correlation"] > 0.9  # correlation alone cannot tell them apart
    assert by_type["F6V"]["rms"] > 0.15  # the score can


def test_nearest_reference_type_reads_catalog_spectral_types():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify catalog spectral types map to a reference."""
    assert nearest_reference_type("A0Va") == "A0V"
    assert nearest_reference_type("K0") == "K0V"
    assert nearest_reference_type("A5V+M3-4V") == "A5V"
    assert nearest_reference_type("B7") == "B8V"
    assert nearest_reference_type("K2III") == "K2III"
    assert nearest_reference_type("Unknown") is None
    assert nearest_reference_type("") is None
    assert nearest_reference_type(None) is None


def test_the_score_does_not_depend_on_the_overall_brightness():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum five times brighter gets the same score."""
    wavelength, flux = _get_reference_templates()["G0V"]
    tilted = flux * (1.0 + 0.1 * np.sin(wavelength / 700.0))  # a shape that is not exactly G0V

    faint = classify_spectral_type(wavelength, tilted)
    bright = classify_spectral_type(wavelength, 5.0 * tilted)

    assert bright["spectral_type"] == faint["spectral_type"]
    assert bright["rms"] == pytest.approx(faint["rms"], rel=1e-6)


def test_the_score_is_a_fraction_of_the_average_brightness():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a wiggle 5% of the average in size scores about 0.035.

    A sine wave of amplitude 0.05 has a root-mean-square size of
    0.05 / sqrt(2) = 0.035, so a spectrum with such a wiggle added to a
    reference should score close to that against the reference.
    """
    wavelength, flux = _get_reference_templates()["G0V"]
    keep = (wavelength >= 4200.0) & (wavelength <= 8000.0)
    wavelength, flux = wavelength[keep], flux[keep]
    blurred = gaussian_filter1d(flux, 30.0 / 2.355 / float(np.median(np.diff(wavelength))))
    wiggle = 0.05 * blurred.mean() * np.sin(wavelength / 150.0)

    result = classify_spectral_type(
        wavelength, blurred + wiggle, resolution_element_angstrom=30.0, exclude_atmospheric_bands=False
    )

    assert result["spectral_type"] == "G0V"
    assert result["rms"] == pytest.approx(0.05 / np.sqrt(2.0), rel=0.15)


def test_a_spectrum_no_reference_matches_is_left_unclassified() -> None:
    """Pure noise is off from every template by far more than the cut."""
    wavelength = np.arange(3800.0, 10000.0, 11.0)
    rng = np.random.default_rng(21)
    noise_spectrum = np.abs(rng.normal(1.0, 1.5, wavelength.size)) + 0.01

    result = classify_spectral_type(wavelength, noise_spectrum)

    assert result["spectral_type"] == "Unknown"
    assert result["confidence"] is None
    assert "no reference matches" in str(result["reason"])


def test_a_close_match_is_still_classified_below_the_unreliable_cut() -> None:
    """A template plus mild noise stays under the cut and keeps its type."""
    wavelength, flux = _get_reference_templates()["G2V"]
    grid = np.arange(3800.0, 10000.0, 11.0)
    observed = np.interp(grid, wavelength, flux)
    observed = observed + np.random.default_rng(22).normal(0.0, 0.02 * observed.mean(), grid.size)

    result = classify_spectral_type(grid, observed)

    assert result["spectral_type"] != "Unknown"
    assert float(result["rms"]) < UNRELIABLE_MATCH_RMS_THRESHOLD  # type: ignore[arg-type]


def test_giant_references_are_only_used_when_asked_for() -> None:
    """A giant template is not a candidate for an ordinary classification."""
    wavelength, flux = _get_reference_templates()["K3III"]
    grid = np.arange(3800.0, 10000.0, 11.0)
    observed = np.interp(grid, wavelength, flux)

    ordinary = classify_spectral_type(grid, observed)
    giants_only = classify_spectral_type(grid, observed, reference_types=GIANT_REFERENCE_SPECTRAL_TYPES)

    assert ordinary["spectral_type"] in REFERENCE_SPECTRAL_TYPES
    assert giants_only["spectral_type"] == "K3III"


@pytest.mark.parametrize(
    ("catalog_type", "expected"),
    [
        ("K3II", "K34II"),
        ("K2III", "K2III"),
        ("B9III", "B9III"),
        ("G5Ib", "G5I"),
        ("K3V", "K3V"),
        ("A0V", "A0V"),
        ("K3", "K3V"),
    ],
)
def test_nearest_reference_uses_the_catalogs_luminosity_class(catalog_type: str, expected: str) -> None:
    """A catalog giant uses giant references, a dwarf uses dwarfs."""
    assert nearest_reference_type(catalog_type) == expected
