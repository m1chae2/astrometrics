"""Purpose: Unit tests for named absorption-feature detection.

Description: Verifies detect_named_features against synthetic spectra
with a known, deliberately placed dip -- a real Balmer-line-strength
absorption should be found with high confidence, while a smooth
continuum with no dip should report nothing, and noise alone shouldn't
be mistaken for a feature.
"""

import numpy as np

from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import (
    NAMED_FEATURES,
    detect_named_features,
)


def _flat_spectrum_with_dip(
    center: float, depth: float, half_width: float, noise_amplitude: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """Build a flat continuum with one box-shaped dip at `center`.

    A box (rather than a narrow spike) fills the whole `half_width`
    window the detector reads its "core" from, the way a real,
    resolution-broadened absorption line would.

    Returns
    -------
    wavelength_angstrom, intensity : `tuple` [`np.ndarray`, `np.ndarray`]
        A synthetic spectrum spanning 3500-8000 A with a single feature.
    """
    wavelength = np.arange(3500.0, 8000.0, 2.0)
    intensity = np.full_like(wavelength, 1000.0)
    in_dip = np.abs(wavelength - center) <= half_width
    intensity = np.where(in_dip, intensity * (1.0 - depth), intensity)
    if noise_amplitude:
        rng = np.random.default_rng(seed=1)
        intensity = intensity + rng.normal(0.0, noise_amplitude, size=intensity.size)
    return wavelength, intensity


def test_a_clear_dip_at_a_named_wavelength_is_detected_with_high_confidence():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a strong, clean dip at H-alpha is found and named correctly."""
    h_alpha = next(f for f in NAMED_FEATURES if "H-alpha" in f["name"])
    wavelength, intensity = _flat_spectrum_with_dip(
        center=h_alpha["wavelength_angstrom"], depth=0.3, half_width=h_alpha["window_angstrom"]
    )

    detections = detect_named_features(wavelength, intensity)

    assert len(detections) >= 1
    top = detections[0]
    assert top["feature"] == h_alpha["name"]
    assert top["depth"] > 0.1
    assert top["confidence"] > 0.9


def test_a_smooth_continuum_with_no_dip_reports_no_features():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a flat spectrum (no real absorption) doesn't false-positive."""
    wavelength = np.arange(3500.0, 8000.0, 2.0)
    intensity = np.full_like(wavelength, 1000.0)

    assert detect_named_features(wavelength, intensity) == []


def test_small_noise_alone_does_not_register_as_a_confident_detection():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify noise without a real dip doesn't produce a confident hit."""
    rng = np.random.default_rng(seed=2)
    wavelength = np.arange(3500.0, 8000.0, 2.0)
    intensity = 1000.0 + rng.normal(0.0, 5.0, size=wavelength.size)

    detections = detect_named_features(wavelength, intensity)

    assert all(entry["confidence"] < 0.9 for entry in detections)


def test_too_narrow_a_wavelength_range_skips_every_feature():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum too short to cover any feature's shoulders is safe."""
    wavelength = np.array([5000.0, 5001.0, 5002.0])
    intensity = np.array([1.0, 0.9, 1.0])

    assert detect_named_features(wavelength, intensity) == []
