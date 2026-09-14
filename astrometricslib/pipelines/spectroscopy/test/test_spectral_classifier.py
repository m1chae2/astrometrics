"""Purpose: Unit tests for template-matching spectral classification.

Description: Verifies classify_spectral_type against the real bundled
Pickles (1998) reference spectra -- a template matched against itself
(plus noise) must win its own comparison, and a hot blue-white star's
spectrum must not be confused for a cool red one. Also covers the two
"not enough to go on" paths (too few points, a flat/degenerate signal)
and sanity-checks the bundled reference data itself, since a corrupted
CSV would otherwise fail silently as a bad classification rather than
a loud error.
"""

import numpy as np

from astrometricslib.pipelines.spectroscopy.spectral_classifier import (
    REFERENCE_SPECTRAL_TYPES,
    _get_reference_templates,
    classify_spectral_type,
)


def test_bundled_reference_templates_are_well_formed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every shipped template loads, and its data makes sense."""
    templates = _get_reference_templates()

    assert set(templates) == set(REFERENCE_SPECTRAL_TYPES)
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
    assert result["confidence"] > 0.99


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
