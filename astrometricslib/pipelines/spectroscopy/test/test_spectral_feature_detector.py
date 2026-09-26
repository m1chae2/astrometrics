"""Purpose: Unit tests for named absorption-feature detection.

Description: Verifies detect_named_features against synthetic spectra
with a known, deliberately placed dip. A real Balmer-line-strength dip is
found and given a small p-value, a spectrum with no dip is not reported
as having features, noise alone is not called a detection, and features
the spectrum does not reach say so instead of being silently dropped.
The statistical calibration (false-positive rate and recovery of
injected dips over many random spectra) is checked in
validate_spectral_and_period_analysis.py; these tests only pin the behavior.
"""

import numpy as np
import pytest

from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import (
    CENTER_SEARCH_TOLERANCE_ANGSTROM,
    INCONCLUSIVE_MINIMUM_DEPTH,
    INCONCLUSIVE_P_VALUE,
    NAMED_FEATURES,
    POSSIBLE_P_VALUE,
    VERDICT_DETECTED,
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_COVERED,
    VERDICT_NOT_DETECTED,
    VERDICT_POSSIBLE,
    _control_centers,
    _mark_features_sharing_a_dip,
    _shoulder_edges,
    detect_named_features,
    expected_feature_depth,
)


def _spectrum_with_dip(
    center: float, depth: float, half_width: float, noise_fraction: float = 0.005, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Build a flat continuum with one box-shaped dip at `center`.

    A box (rather than a narrow spike) fills the whole `half_width`
    window the detector reads its core from, the way a real,
    resolution-broadened absorption line would.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`np.ndarray`, `np.ndarray`]
        A synthetic spectrum spanning 3500-8000 A with a single feature.
    """
    wavelength = np.arange(3500.0, 8000.0, 5.0)
    intensity = np.full_like(wavelength, 1000.0)
    in_dip = np.abs(wavelength - center) <= half_width
    intensity = np.where(in_dip, intensity * (1.0 - depth), intensity)
    rng = np.random.default_rng(seed=seed)
    return wavelength, intensity * (1.0 + rng.normal(0.0, noise_fraction, size=intensity.size))


def _entry(features: list[dict[str, object]], name_part: str) -> dict[str, object]:
    """Pick the entry whose feature name contains `name_part`.

    Returns
    -------
    entry : `dict`
        The matching feature entry.
    """
    return next(entry for entry in features if name_part in str(entry["feature"]))


def test_a_clear_dip_at_a_named_wavelength_is_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a strong, clean dip at H-alpha is detected and measured."""
    h_alpha = next(f for f in NAMED_FEATURES if "H-alpha" in f["name"])
    wavelength, intensity = _spectrum_with_dip(
        center=h_alpha["wavelength_angstrom"], depth=0.3, half_width=h_alpha["window_angstrom"]
    )

    features = detect_named_features(wavelength, intensity)

    detected = _entry(features, "H-alpha")
    assert detected["verdict"] == VERDICT_DETECTED
    assert detected["depth"] > 0.2
    assert detected["p_value"] < 0.01
    assert abs(detected["measured_wavelength_angstrom"] - 6563.0) <= 30.0
    assert features[0] is detected  # most convincing first


def test_a_flat_noisy_spectrum_has_no_detections():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify noise alone is not called a feature."""
    rng = np.random.default_rng(seed=2)
    wavelength = np.arange(3500.0, 8000.0, 5.0)
    intensity = 1000.0 * (1.0 + rng.normal(0.0, 0.005, size=wavelength.size))

    features = detect_named_features(wavelength, intensity)

    assert all(entry["verdict"] != VERDICT_DETECTED for entry in features)


def test_every_named_feature_gets_an_entry_and_uncovered_ones_say_so():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify lines the spectrum does not reach are reported as such."""
    wavelength, intensity = _spectrum_with_dip(center=6563.0, depth=0.3, half_width=25.0)
    keep = wavelength >= 5300.0

    features = detect_named_features(wavelength[keep], intensity[keep])

    assert len(features) == len(NAMED_FEATURES)
    assert _entry(features, "H-beta")["verdict"] == VERDICT_NOT_COVERED
    assert _entry(features, "H-alpha")["verdict"] == VERDICT_DETECTED
    assert "p_value" not in _entry(features, "H-beta")


def test_too_narrow_a_wavelength_range_returns_nothing():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum too short to measure anything is safe."""
    wavelength = np.array([5000.0, 5001.0, 5002.0])
    intensity = np.array([1.0, 0.9, 1.0])

    assert detect_named_features(wavelength, intensity) == []


def test_a_shallow_dip_is_not_reported_however_clean_the_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a dip under 1% of the continuum is never called a detection."""
    wavelength, intensity = _spectrum_with_dip(
        center=6563.0, depth=0.005, half_width=25.0, noise_fraction=0.0002
    )

    assert _entry(detect_named_features(wavelength, intensity), "H-alpha")["verdict"] != VERDICT_DETECTED


def test_an_a_type_reference_expects_deeper_balmer_lines_than_a_k_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the expected depth comes from the reference spectrum."""
    a_type = expected_feature_depth("A0V", "Hydrogen Balmer series (H-beta)")
    k_type = expected_feature_depth("K5V", "Hydrogen Balmer series (H-beta)")

    assert a_type is not None
    assert k_type is not None
    assert a_type > 0.03
    assert a_type > k_type


def test_a_reference_type_adds_expected_depth_and_a_probability():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a reference type adds an expected depth and probability."""
    wavelength, intensity = _spectrum_with_dip(center=4861.0, depth=0.25, half_width=25.0)

    with_type = _entry(detect_named_features(wavelength, intensity, reference_spectral_type="A0V"), "H-beta")
    without_type = _entry(detect_named_features(wavelength, intensity), "H-beta")

    assert with_type["expected_depth"] is not None
    assert 0.0 <= with_type["probability_present"] <= 1.0
    assert with_type["probability_present"] > 0.5
    assert without_type["expected_depth"] is None
    assert without_type["probability_present"] is None


def test_a_dip_the_noise_could_hide_is_inconclusive():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a dip too big to ignore but too weak to call is inconclusive.

    A 10% dip in 6% noise (seed 2) gives a p-value between the "possible"
    and "inconclusive" cutoffs: too weak to call, too big to ignore. (The
    seed only fixes one draw: seeds 5 and 12 were in that range with earlier
    versions of the detector, and many other seeds are in it now.)
    """
    h_alpha = next(f for f in NAMED_FEATURES if "H-alpha" in f["name"])
    wavelength, intensity = _spectrum_with_dip(
        center=h_alpha["wavelength_angstrom"],
        depth=0.10,
        half_width=h_alpha["window_angstrom"],
        noise_fraction=0.06,
        seed=2,
    )

    entry = _entry(detect_named_features(wavelength, intensity), "H-alpha")

    assert entry["verdict"] == VERDICT_INCONCLUSIVE
    assert POSSIBLE_P_VALUE < entry["p_value"] <= INCONCLUSIVE_P_VALUE
    assert entry["depth"] >= INCONCLUSIVE_MINIMUM_DEPTH


def test_a_quiet_spectrum_with_no_dip_has_no_inconclusive_features():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a clean, featureless spectrum has no inconclusive features."""
    wavelength, intensity = _spectrum_with_dip(
        center=6563.0, depth=0.0, half_width=25.0, noise_fraction=0.002
    )

    features = detect_named_features(wavelength, intensity)

    assert all(entry["verdict"] != VERDICT_INCONCLUSIVE for entry in features)


def test_inconclusive_features_sort_between_possible_and_not_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the list runs most convincing first."""
    h_alpha = next(f for f in NAMED_FEATURES if "H-alpha" in f["name"])
    wavelength, intensity = _spectrum_with_dip(
        center=h_alpha["wavelength_angstrom"],
        depth=0.10,
        half_width=h_alpha["window_angstrom"],
        noise_fraction=0.06,
        seed=2,
    )
    order = [
        VERDICT_DETECTED,
        VERDICT_POSSIBLE,
        VERDICT_INCONCLUSIVE,
        VERDICT_NOT_DETECTED,
        VERDICT_NOT_COVERED,
    ]

    verdict_positions = [
        order.index(entry["verdict"]) for entry in detect_named_features(wavelength, intensity)
    ]

    assert verdict_positions == sorted(verdict_positions)


def test_no_feature_core_is_narrower_than_twenty_angstroms():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify every core is wide enough to keep the continuum off the dip.

    Na D was 15 A and missed a 28% dip at 5900 A on a real spectrum; see
    the comment on NAMED_FEATURES.
    """
    assert all(float(feature["window_angstrom"]) >= 20.0 for feature in NAMED_FEATURES)


def test_one_dip_is_not_credited_to_two_lines_too_close_to_tell_apart():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A lone H-gamma dip must not also be reported as the G band.

    The G band (4300 A) is 40 A from H-gamma (4340 A), less than the
    instrument's resolution element, and each feature searches 30 A either
    side of its rest wavelength, so both find the same dip. Seen on real
    Vega, Albireo B and Deneb spectra, where the G band came out with the
    same depth and p-value as H-gamma.
    """
    wavelength, intensity = _spectrum_with_dip(center=4335.0, depth=0.3, half_width=20.0)

    features = detect_named_features(wavelength, intensity)

    h_gamma = _entry(features, "H-gamma")
    g_band = _entry(features, "G band")
    assert h_gamma["verdict"] == VERDICT_DETECTED
    assert h_gamma["blended_with"] is None
    assert g_band["verdict"] == VERDICT_INCONCLUSIVE
    assert g_band["blended_with"] == h_gamma["feature"]


def test_a_real_g_band_away_from_h_gamma_is_still_detected():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A dip at the G band that H-gamma cannot also claim keeps its verdict."""
    wavelength, intensity = _spectrum_with_dip(center=4285.0, depth=0.3, half_width=20.0)

    features = detect_named_features(wavelength, intensity, resolution_element_angstrom=20.0)

    g_band = _entry(features, "G band")
    assert g_band["verdict"] == VERDICT_DETECTED
    assert g_band["blended_with"] is None


def test_a_feature_that_was_not_detected_is_never_marked_as_blended():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Only a feature that would have been reported can be blended away."""
    wavelength, intensity = _spectrum_with_dip(center=6563.0, depth=0.3, half_width=25.0)

    features = detect_named_features(wavelength, intensity)

    assert all(entry.get("blended_with") is None for entry in features)


def test_an_a_type_star_gives_a_shared_dip_to_h_gamma_despite_distance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The reference type's expected depths break the tie, not the distance.

    A dip at 4314 A is 14 A from the G band and 26 A from H-gamma, but an
    A0V star shows H-gamma (expected depth 0.13) and not the G band (0.0).
    """
    wavelength, intensity = _spectrum_with_dip(center=4314.0, depth=0.3, half_width=20.0)

    features = detect_named_features(wavelength, intensity, reference_spectral_type="A0V")

    h_gamma = _entry(features, "H-gamma")
    g_band = _entry(features, "G band")
    assert h_gamma["blended_with"] is None
    assert h_gamma["verdict"] in (VERDICT_DETECTED, VERDICT_POSSIBLE)
    assert g_band["verdict"] == VERDICT_INCONCLUSIVE
    assert g_band["blended_with"] == h_gamma["feature"]


def test_a_k_type_star_gives_a_shared_dip_to_the_g_band_despite_distance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A K giant expects the G band, not H-gamma, so the G band keeps it.

    A dip at 4322 A is 18 A from H-gamma and 22 A from the G band.
    """
    wavelength, intensity = _spectrum_with_dip(center=4322.0, depth=0.3, half_width=20.0)

    features = detect_named_features(wavelength, intensity, reference_spectral_type="K0III")

    h_gamma = _entry(features, "H-gamma")
    g_band = _entry(features, "G band")
    assert g_band["blended_with"] is None
    assert g_band["verdict"] in (VERDICT_DETECTED, VERDICT_POSSIBLE)
    assert h_gamma["verdict"] == VERDICT_INCONCLUSIVE
    assert h_gamma["blended_with"] == g_band["feature"]


def _blurred_line_spectrum(
    depth: float, fwhm_angstrom: float, noise_fraction: float = 0.002, seed: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Build a flat continuum with a blurred Gaussian H-beta dip.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`np.ndarray`, `np.ndarray`]
        A spectrum 3800-8000 A at 10.9 A spacing (the real sampling).
    """
    wavelength = np.arange(3800.0, 8000.0, 10.9)
    sigma = fwhm_angstrom / 2.355
    intensity = 1000.0 * (1.0 - depth * np.exp(-0.5 * ((wavelength - 4861.0) / sigma) ** 2))
    rng = np.random.default_rng(seed)
    return wavelength, intensity * (1.0 + rng.normal(0.0, noise_fraction, size=intensity.size))


def test_the_continuum_bands_start_beyond_the_wings_of_a_blurred_line():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Bands start 1.5 resolution elements out, or a half-window if more."""
    assert _shoulder_edges(25.0, 49.0) == (73.5, 148.5)
    assert _shoulder_edges(35.0, 45.0) == (67.5, 172.5)
    assert _shoulder_edges(25.0, 10.0) == (25.0, 100.0)


def test_a_line_blurred_to_the_instrument_resolution_reads_its_true_depth():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A 49 A wide dip of depth 0.20 reads as its core average.

    With continuum bands starting at the core's edge (25 A) the wings were
    fitted as continuum and this read about 0.09 instead.
    """
    wavelength, intensity = _blurred_line_spectrum(depth=0.20, fwhm_angstrom=49.0)
    sigma = 49.0 / 2.355
    core = np.abs(wavelength - 4861.0) <= 25.0
    true_core_depth = float(np.mean(0.20 * np.exp(-0.5 * ((wavelength[core] - 4861.0) / sigma) ** 2)))

    entry = _entry(detect_named_features(wavelength, intensity, resolution_element_angstrom=49.0), "H-beta")

    assert entry["depth"] == pytest.approx(true_core_depth, rel=0.08)


def test_the_wings_of_a_line_do_not_inflate_its_uncertainty():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A line's depth uncertainty stays that of the noise.

    The uncertainty comes from how the continuum band samples scatter about
    the fitted curve. With the bands on the wings that scatter included the
    dip itself, and the same line read 0.031 against 0.017 in two spectra of
    Vega.
    """
    wavelength, without_line = _blurred_line_spectrum(depth=0.0, fwhm_angstrom=49.0)
    _, with_line = _blurred_line_spectrum(depth=0.20, fwhm_angstrom=49.0)

    quiet = _entry(
        detect_named_features(wavelength, without_line, resolution_element_angstrom=49.0), "H-beta"
    )
    lined = _entry(detect_named_features(wavelength, with_line, resolution_element_angstrom=49.0), "H-beta")

    assert lined["depth_uncertainty"] < 1.5 * quiet["depth_uncertainty"]
    assert lined["verdict"] == VERDICT_DETECTED


def test_controls_cover_the_blue_but_never_reach_a_feature_core():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Controls lie in the blue too, and clear of every named feature's core.

    With continuum bands kept fully clear of features no control lay below
    4558 A, so the crowded blue features (Ca H & K, H-delta) were tested
    against controls from a different part of the spectrum and came out at
    5-6% "detected" in pure noise.
    """
    wavelength = np.arange(3800.0, 8000.0, 10.9)

    controls = _control_centers(wavelength, 25.0, CENTER_SEARCH_TOLERANCE_ANGSTROM, 45.0)

    assert controls.min() < 4400.0
    for feature in NAMED_FEATURES:
        rest = float(feature["wavelength_angstrom"])
        window = float(feature["window_angstrom"])
        assert np.all(np.abs(controls - rest) > CENTER_SEARCH_TOLERANCE_ANGSTROM + 25.0 + window)


def _spectrum_with_hump(
    center: float, height: float, half_width: float, noise_fraction: float = 0.005, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Build a flat continuum with one box-shaped bump (emission) at `center`.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`np.ndarray`, `np.ndarray`]
        A synthetic spectrum spanning 3500-8000 A with a single bump.
    """
    return _spectrum_with_dip(center, -height, half_width, noise_fraction, seed)


def test_a_bump_at_a_named_wavelength_is_detected_as_emission():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A Be star's H-alpha emission is reported, with its height."""
    wavelength, intensity = _spectrum_with_hump(6563.0, 0.25, 25.0)

    features = detect_named_features(wavelength, intensity, reference_spectral_type="B0V")

    h_alpha = _entry(features, "H-alpha")
    assert h_alpha["kind"] == "emission"
    assert h_alpha["verdict"] == VERDICT_DETECTED
    assert h_alpha["depth"] == pytest.approx(0.25, abs=0.05)
    assert h_alpha["p_value"] < 0.01
    assert h_alpha["probability_present"] is None
    assert features[0] is h_alpha


def test_a_dip_is_still_reported_as_absorption():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Every measured feature says which way it points."""
    wavelength, intensity = _spectrum_with_dip(6563.0, 0.3, 25.0)

    features = detect_named_features(wavelength, intensity)

    assert _entry(features, "H-alpha")["kind"] == "absorption"
    assert all(entry["kind"] == "absorption" for entry in features if entry["verdict"] != VERDICT_NOT_COVERED)


def test_a_flat_spectrum_has_no_emission_either():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Testing both directions does not make noise look like emission."""
    wavelength, intensity = _spectrum_with_hump(6563.0, 0.0, 25.0, noise_fraction=0.005)

    features = detect_named_features(wavelength, intensity)

    assert all(entry["verdict"] != VERDICT_DETECTED for entry in features)


def test_testing_both_directions_costs_a_factor_of_two_in_the_p_value():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """The p-value pays for both directions: twice the one-sided value."""
    wavelength, intensity = _spectrum_with_dip(6563.0, 0.06, 25.0, noise_fraction=0.02, seed=3)

    entry = _entry(detect_named_features(wavelength, intensity), "H-alpha")

    assert 0.0 < entry["p_value"] <= 1.0
    assert entry["p_value"] == pytest.approx(min(1.0, 2.0 * entry["p_value_one_sided"]))


def test_features_of_opposite_kinds_are_not_blended_into_one_dip():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A bump and a dip close together are two features."""
    entries = [
        {
            "feature": "Hydrogen Balmer series (H-gamma)",
            "wavelength_angstrom": 4340.0,
            "verdict": VERDICT_DETECTED,
            "kind": "emission",
            "measured_wavelength_angstrom": 4335.0,
            "blended_with": None,
        },
        {
            "feature": "Iron/titanium blend (G band)",
            "wavelength_angstrom": 4300.0,
            "verdict": VERDICT_DETECTED,
            "kind": "absorption",
            "measured_wavelength_angstrom": 4320.0,
            "blended_with": None,
        },
    ]

    _mark_features_sharing_a_dip(entries, 50.0)

    assert entries[1]["blended_with"] is None
    assert entries[1]["verdict"] == VERDICT_DETECTED


def test_only_h_alpha_and_h_beta_are_tested_for_emission():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A bump at H-gamma is not called emission (blanketed spectra)."""
    wavelength, intensity = _spectrum_with_hump(4340.0, 0.25, 20.0)

    features = detect_named_features(wavelength, intensity)

    h_gamma = _entry(features, "H-gamma")
    assert h_gamma["kind"] == "absorption"
    assert h_gamma["verdict"] == VERDICT_NOT_DETECTED
    h_beta_wavelength, h_beta_intensity = _spectrum_with_hump(4861.0, 0.25, 25.0)
    assert _entry(detect_named_features(h_beta_wavelength, h_beta_intensity), "H-beta")["kind"] == "emission"
