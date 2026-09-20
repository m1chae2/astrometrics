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

from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import (
    INCONCLUSIVE_MINIMUM_DEPTH,
    INCONCLUSIVE_P_VALUE,
    NAMED_FEATURES,
    POSSIBLE_P_VALUE,
    VERDICT_DETECTED,
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_COVERED,
    VERDICT_NOT_DETECTED,
    VERDICT_POSSIBLE,
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

    A 10% dip in 6% noise (seed 5) gives a p-value between the "possible"
    and "inconclusive" cutoffs: too weak to call, too big to ignore.
    """
    h_alpha = next(f for f in NAMED_FEATURES if "H-alpha" in f["name"])
    wavelength, intensity = _spectrum_with_dip(
        center=h_alpha["wavelength_angstrom"],
        depth=0.10,
        half_width=h_alpha["window_angstrom"],
        noise_fraction=0.06,
        seed=5,
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
        seed=5,
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
