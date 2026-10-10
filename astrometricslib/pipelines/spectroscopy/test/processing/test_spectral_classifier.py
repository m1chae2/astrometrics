"""Purpose: Unit tests for template-matching spectral classification.

Description: Verifies classify_spectral_type against the real bundled
Pickles (1998) reference spectra -- a template matched against itself
(plus noise) must win its own comparison, and a hot blue-white star's
spectrum must not be confused for a cool red one. Also covers the two
"not enough to go on" paths (too few points, a flat/degenerate signal)
and sanity-checks the bundled reference data itself, since a corrupted
CSV would otherwise fail silently as a bad classification rather than
a loud error.

Also verifies the two numbers that describe how clear a match is, both in
relative RMS units and neither a probability: the best reference's
`classification_rms` and `rms_gap_to_second_best`, the RMS of the second-best
reference minus the best one's. The gap must be zero only for identical
templates, small for an adjacent pair of subtypes and large for a hot-versus-
cool pair, and `is_ambiguous` must follow `AMBIGUOUS_RMS_GAP`.

Tests of the post-processing trust/comparison functions
(`is_classification_poor_match`, `is_classification_ambiguous`,
`build_spectral_classification_concerns`, `catalog_disagreement_note`,
`luminosity_class_note`) live in
`test/post_processing/test_assess_output_quality.py` and
`test/post_processing/test_compare_to_catalog.py`.
"""

import numpy as np
import pytest
from scipy.ndimage import gaussian_filter1d

from astrometricslib.models.stellar_source import AMBIGUOUS_RMS_GAP, NO_GOOD_MATCH_RMS, UNRELIABLE_MATCH_RMS
from astrometricslib.pipelines.spectroscopy.processing import spectral_classifier
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    GIANT_REFERENCE_SPECTRAL_TYPES,
    REFERENCE_SPECTRAL_TYPES,
    _get_blurred_templates,
    _get_reference_templates,
    _rank_by_rms,
    classify_spectral_type,
    nearest_reference_type,
)

# The resolution used to build synthetic observations from the references, in
# Angstroms. The classifier is told the same value, as the real pipeline is.
_TEST_RESOLUTION_ANGSTROM = 30.0


def _observation_from_blurred_templates(*spectral_types: str) -> tuple[np.ndarray, np.ndarray]:
    """Build a synthetic observation from the average of blurred references.

    The observation covers 4200-8000 Angstroms, the part of the spectrum the
    instrument response leaves usable, so it matches a reference exactly when
    only one type is given.

    Parameters
    ----------
    *spectral_types : `str`
        The reference types to average.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`np.ndarray`, `np.ndarray`]
        The observation's wavelength grid and brightness.
    """
    blurred = _get_blurred_templates(_TEST_RESOLUTION_ANGSTROM)
    wavelength = blurred[spectral_types[0]][0]
    intensity = np.mean([blurred[name][1] for name in spectral_types], axis=0)
    usable = (wavelength >= 4200.0) & (wavelength <= 8000.0)
    return wavelength[usable], intensity[usable]


def test_bundled_reference_templates_are_well_formed() -> None:
    """Verify every shipped template loads, and its data makes sense."""
    templates = _get_reference_templates()

    assert set(templates) == set(REFERENCE_SPECTRAL_TYPES) | set(GIANT_REFERENCE_SPECTRAL_TYPES)
    assert len(GIANT_REFERENCE_SPECTRAL_TYPES) == len(set(GIANT_REFERENCE_SPECTRAL_TYPES)) == 56
    for spectral_type, (wavelength, flux) in templates.items():
        assert wavelength.size == flux.size > 0, spectral_type
        assert np.all(np.diff(wavelength) > 0), f"{spectral_type} wavelengths must be increasing"
        assert np.all(flux >= 0), f"{spectral_type} flux must be non-negative"


def test_a_template_matched_against_itself_wins_with_a_small_rms() -> None:
    """Verify self-matching (plus noise) picks the type with a low RMS."""
    templates = _get_reference_templates()
    wavelength, flux = templates["G0V"]
    rng = np.random.default_rng(seed=0)
    noisy_flux = flux * (1.0 + rng.normal(0.0, 0.02, size=flux.size))

    result = classify_spectral_type(wavelength, noisy_flux)

    assert result["spectral_type"] == "G0V"
    assert result["classification_rms"] < 0.05


def test_ranked_types_puts_the_closest_first_and_scores_by_rms() -> None:
    """Verify the ranked list is sorted by RMS and carries no probability."""
    templates = _get_reference_templates()
    wavelength, flux = templates["K0V"]

    result = classify_spectral_type(wavelength, flux)

    ranked = result["ranked_types"]
    assert ranked, "expected at least one ranked candidate"
    assert ranked[0]["spectral_type"] == result["spectral_type"]
    assert ranked[0]["rms"] == result["classification_rms"]
    rms_values = [entry["rms"] for entry in ranked]
    assert rms_values == sorted(rms_values)
    assert all("probability" not in entry for entry in ranked)
    assert "confidence" not in result


def test_rank_by_rms_sorts_closest_first_and_keeps_the_scores() -> None:
    """Pins the ranking: ascending RMS, and each entry keeps its own RMS."""
    rms_by_type = {"F0V": 0.20, "A0V": 0.02, "A2V": 0.05}
    correlation_by_type = {"A0V": 0.99, "A2V": 0.97, "F0V": 0.80}

    ranked = _rank_by_rms(rms_by_type, correlation_by_type)

    assert [entry["spectral_type"] for entry in ranked] == ["A0V", "A2V", "F0V"]
    assert [entry["rms"] for entry in ranked] == [0.02, 0.05, 0.20]
    assert ranked[2]["correlation"] == pytest.approx(0.80)


def test_a_hot_blue_star_is_not_confused_for_a_cool_red_one() -> None:
    """Verify a clear hot/cool pair lands on the right side of the sequence."""
    templates = _get_reference_templates()

    hot_wavelength, hot_flux = templates["O5V"]
    hot_result = classify_spectral_type(hot_wavelength, hot_flux)
    assert hot_result["spectral_type"] in ("O5V", "B0V", "B8V")

    cool_wavelength, cool_flux = templates["M5V"]
    cool_result = classify_spectral_type(cool_wavelength, cool_flux)
    assert cool_result["spectral_type"] in ("M5V", "M0V", "K5V")


def test_too_few_points_returns_unknown_without_crashing() -> None:
    """Verify a handful of points isn't enough to attempt a match."""
    result = classify_spectral_type(
        wavelength_angstrom=np.array([5000.0, 5010.0, 5020.0]),
        intensity=np.array([1.0, 1.1, 0.9]),
    )

    assert result["spectral_type"] == "Unknown"
    assert result["classification_rms"] is None
    assert result["rms_gap_to_second_best"] is None
    assert result["is_ambiguous"] is None
    assert result["rms_gap_to_next_class"] is None
    assert result["is_class_ambiguous"] is None
    assert result["correlation_by_type"] == {}
    assert result["ranked_types"] == []


def test_a_flat_spectrum_returns_unknown_without_crashing() -> None:
    """Verify a constant signal (zero variance) doesn't blow up the math."""
    wavelength = np.linspace(3600.0, 7500.0, 200)
    flat_intensity = np.full_like(wavelength, 500.0)

    result = classify_spectral_type(wavelength, flat_intensity)

    assert result["spectral_type"] == "Unknown"
    assert result["classification_rms"] is None
    assert result["rms_gap_to_second_best"] is None
    assert result["is_ambiguous"] is None
    assert result["rms_gap_to_next_class"] is None
    assert result["is_class_ambiguous"] is None
    assert result["ranked_types"] == []


def test_a_spectrum_covering_too_little_of_the_range_is_not_classified() -> None:
    """Verify a spectrum that stops early gets no type."""
    wavelength, flux = _get_reference_templates()["G0V"]
    keep = wavelength <= 4500.0  # only 1500 A of the spectrum, less than the 2500 A needed

    result = classify_spectral_type(wavelength[keep], flux[keep])

    assert result["spectral_type"] == "Unknown"
    assert result["classification_rms"] is None
    assert result["rms_gap_to_second_best"] is None
    assert result["is_ambiguous"] is None
    assert result["rms_gap_to_next_class"] is None
    assert result["is_class_ambiguous"] is None
    assert "covers only" in result["reason"]


def test_a_spectrum_unlike_every_reference_is_flagged_as_a_poor_match() -> None:
    """Verify an odd tilt gets a poor-match flag."""
    wavelength, flux = _get_reference_templates()["G0V"]
    # A bump no star has, strong enough to be a poor match (0.176 here) but
    # not so strong that no reference matches at all (see the next test).
    tilted = flux * np.exp(-(((wavelength - 6000.0) / 900.0) ** 2) * 0.05)

    result = classify_spectral_type(wavelength, tilted)

    assert result["match_quality"] == "poor"
    assert result["classification_rms"] > NO_GOOD_MATCH_RMS


def test_the_score_separates_types_that_correlation_cannot() -> None:
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


def test_nearest_reference_type_reads_catalog_spectral_types() -> None:
    """Verify catalog spectral types map to a reference."""
    assert nearest_reference_type("A0Va") == "A0V"
    assert nearest_reference_type("K0") == "K0V"
    assert nearest_reference_type("A5V+M3-4V") == "A5V"
    assert nearest_reference_type("B7") == "B8V"
    assert nearest_reference_type("K2III") == "K2III"
    assert nearest_reference_type("Unknown") is None
    assert nearest_reference_type("") is None
    assert nearest_reference_type(None) is None


def test_the_score_does_not_depend_on_the_overall_brightness() -> None:
    """Verify a spectrum five times brighter gets the same score."""
    wavelength, flux = _get_reference_templates()["G0V"]
    tilted = flux * (1.0 + 0.1 * np.sin(wavelength / 700.0))  # a shape that is not exactly G0V

    faint = classify_spectral_type(wavelength, tilted)
    bright = classify_spectral_type(wavelength, 5.0 * tilted)

    assert bright["spectral_type"] == faint["spectral_type"]
    assert bright["classification_rms"] == pytest.approx(faint["classification_rms"], rel=1e-6)


def test_the_score_is_a_fraction_of_the_average_brightness() -> None:
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
    assert result["classification_rms"] == pytest.approx(0.05 / np.sqrt(2.0), rel=0.15)


def test_a_spectrum_no_reference_matches_is_left_unclassified() -> None:
    """Pure noise is off from every template by far more than the cut."""
    wavelength = np.arange(3800.0, 10000.0, 11.0)
    rng = np.random.default_rng(21)
    noise_spectrum = np.abs(rng.normal(1.0, 1.5, wavelength.size)) + 0.01

    result = classify_spectral_type(wavelength, noise_spectrum)

    assert result["spectral_type"] == "Unknown"
    assert result["classification_rms"] is None
    assert result["rms_gap_to_second_best"] is None
    assert result["is_ambiguous"] is None
    assert result["rms_gap_to_next_class"] is None
    assert result["is_class_ambiguous"] is None
    assert "no reference matches" in str(result["reason"])


def test_a_close_match_is_still_classified_below_the_unreliable_cut() -> None:
    """A template plus mild noise stays under the cut and keeps its type."""
    wavelength, flux = _get_reference_templates()["G2V"]
    grid = np.arange(3800.0, 10000.0, 11.0)
    observed = np.interp(grid, wavelength, flux)
    observed = observed + np.random.default_rng(22).normal(0.0, 0.02 * observed.mean(), grid.size)

    result = classify_spectral_type(grid, observed)

    assert result["spectral_type"] != "Unknown"
    rms = result["classification_rms"]
    assert isinstance(rms, float)
    assert rms < UNRELIABLE_MATCH_RMS


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


def test_the_gap_is_zero_for_identical_templates_and_positive_for_every_distinct_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify the gap is zero only when two templates are the same.

    Every rung of the ladder, shown to the classifier as its own blurred
    spectrum, wins with a near-zero RMS and a positive gap to its neighbor.
    Then A2V is replaced by a copy of A0V and a spectrum identical to both
    ties them exactly, so the gap is 0.0 and the match is ambiguous.
    """
    for spectral_type in REFERENCE_SPECTRAL_TYPES:
        wavelength, intensity = _observation_from_blurred_templates(spectral_type)
        result = classify_spectral_type(
            wavelength, intensity, resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM
        )
        assert result["spectral_type"] == spectral_type
        assert result["classification_rms"] < 1e-6
        assert result["rms_gap_to_second_best"] > 0.0, spectral_type

    templates = dict(_get_reference_templates())
    templates["A2V"] = templates["A0V"]
    monkeypatch.setattr(spectral_classifier, "_reference_cache", templates)
    monkeypatch.setattr(spectral_classifier, "_blurred_cache", {})
    wavelength, intensity = _observation_from_blurred_templates("A0V")

    tied = classify_spectral_type(
        wavelength,
        intensity,
        resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM,
        reference_types={"A0V", "A2V", "F6V"},
    )

    assert tied["rms_gap_to_second_best"] == pytest.approx(0.0, abs=1e-12)
    assert tied["is_ambiguous"] is True


def test_an_adjacent_subtype_pair_has_a_gap_below_the_ambiguity_limit() -> None:
    """Verify a spectrum halfway between A0V and A2V is ambiguous.

    The two neighbouring rungs fit it almost equally well, so the RMS gap
    between the best and the second best is far under `AMBIGUOUS_RMS_GAP`.
    """
    wavelength, intensity = _observation_from_blurred_templates("A0V", "A2V")

    result = classify_spectral_type(
        wavelength, intensity, resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM
    )

    assert {entry["spectral_type"] for entry in result["ranked_types"][:2]} == {"A0V", "A2V"}
    assert result["rms_gap_to_second_best"] < AMBIGUOUS_RMS_GAP
    assert result["is_ambiguous"] is True
    # Both candidates are A stars, so the nearest other class is further off.
    assert result["rms_gap_to_next_class"] > AMBIGUOUS_RMS_GAP
    assert result["is_class_ambiguous"] is False


def test_a_cross_class_pair_is_ambiguous_at_both_levels() -> None:
    """Verify a spectrum halfway between F8V and G0V is ambiguous both ways.

    The two neighbouring rungs belong to different spectral classes, so the
    best reference of another class is almost as close as the best one.
    """
    wavelength, intensity = _observation_from_blurred_templates("F8V", "G0V")

    result = classify_spectral_type(
        wavelength, intensity, resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM
    )

    assert {entry["spectral_type"] for entry in result["ranked_types"][:2]} == {"F8V", "G0V"}
    assert result["rms_gap_to_second_best"] < AMBIGUOUS_RMS_GAP
    assert result["rms_gap_to_next_class"] < AMBIGUOUS_RMS_GAP
    assert result["is_ambiguous"] is True
    assert result["is_class_ambiguous"] is True


def test_a_hot_versus_cool_pair_has_a_large_gap_and_is_not_ambiguous() -> None:
    """Verify an O5V spectrum compared with O5V and M2V is clearly O5V.

    The RMS gap is about 1.0, fifty times `AMBIGUOUS_RMS_GAP`, so the match is
    not ambiguous.
    """
    wavelength, intensity = _observation_from_blurred_templates("O5V")

    result = classify_spectral_type(
        wavelength,
        intensity,
        resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM,
        reference_types={"O5V", "M2V"},
    )

    assert result["spectral_type"] == "O5V"
    assert result["rms_gap_to_second_best"] > 50 * AMBIGUOUS_RMS_GAP
    assert result["is_ambiguous"] is False
    assert result["rms_gap_to_next_class"] > 50 * AMBIGUOUS_RMS_GAP
    assert result["is_class_ambiguous"] is False


def test_a_single_reference_gives_no_gap_and_no_ambiguity_verdict() -> None:
    """Verify one compared reference leaves the gap and verdict as `None`."""
    wavelength, intensity = _observation_from_blurred_templates("G2V")

    result = classify_spectral_type(
        wavelength, intensity, resolution_element_angstrom=_TEST_RESOLUTION_ANGSTROM, reference_types={"G2V"}
    )

    assert result["spectral_type"] == "G2V"
    assert result["rms_gap_to_second_best"] is None
    assert result["is_ambiguous"] is None
    assert result["rms_gap_to_next_class"] is None
    assert result["is_class_ambiguous"] is None
