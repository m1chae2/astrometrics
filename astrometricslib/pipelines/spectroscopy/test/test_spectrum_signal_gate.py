"""Purpose: Unit tests for the "is there a spectrum at all" checks.

Description: A star too faint for the frames leaves only sky noise, and the
classifier and feature detector used to answer for it anyway (an M0V match
and a "possible" Mg b line on a magnitude-13.6 star, seen in the app). These
tests pin the two checks that stop that: a signal-to-noise gate in front of
the analysis, and a cap on the verdict of a feature measured too noisily.
"""

import numpy as np
import pytest

import astrometricslib.pipelines.spectroscopy.spectral_feature_detector as feature_detector
from astrometricslib.pipelines.spectroscopy.spectral_feature_detector import (
    MAXIMUM_UNCERTAINTY_FOR_A_VERDICT,
    VERDICT_INCONCLUSIVE,
    detect_named_features,
)
from astrometricslib.pipelines.spectroscopy.spectrum_analysis import analyze_spectrum
from astrometricslib.pipelines.spectroscopy.spectrum_signal import (
    MINIMUM_SPECTRUM_SIGNAL_TO_NOISE,
    estimate_spectrum_signal_to_noise,
)

WAVELENGTHS = np.arange(3800.0, 8000.0, 11.0)
CAMERA = "ZWO ASI 533MM Pro"


def _noise_only_spectrum(seed: int = 1) -> np.ndarray:
    """Sky noise after subtraction, floored at zero like a stored spectrum.

    Returns
    -------
    intensity : `numpy.ndarray`
        Mostly zeros with small positive spikes.
    """
    return np.clip(np.random.default_rng(seed).normal(0.0, 3e-5, WAVELENGTHS.size), 0.0, None)


def _star_spectrum(signal_to_noise: float, seed: int = 2) -> np.ndarray:
    """Build a smooth continuum with noise at a chosen signal-to-noise.

    Returns
    -------
    intensity : `numpy.ndarray`
        The continuum plus noise.
    """
    continuum = 1e-3 * (1.0 + 0.4 * np.sin((WAVELENGTHS - 3800.0) / 4200.0 * np.pi))
    noise = 1e-3 / signal_to_noise
    return continuum + np.random.default_rng(seed).normal(0.0, noise, WAVELENGTHS.size)


def test_a_noise_only_spectrum_has_no_signal() -> None:
    """Zero-floored sky noise scores far below the gate."""
    score = estimate_spectrum_signal_to_noise(WAVELENGTHS, _noise_only_spectrum(), 45.0)

    assert score is not None
    assert score < MINIMUM_SPECTRUM_SIGNAL_TO_NOISE


def test_a_real_continuum_scores_above_the_gate() -> None:
    """Even a modest signal-to-noise of 6 clears the gate comfortably."""
    score = estimate_spectrum_signal_to_noise(WAVELENGTHS, _star_spectrum(6.0), 45.0)

    assert score is not None
    assert score > 2 * MINIMUM_SPECTRUM_SIGNAL_TO_NOISE


def test_too_few_resolution_elements_are_not_judged() -> None:
    """A tiny spectrum gets no score rather than a wrong one."""
    assert estimate_spectrum_signal_to_noise(WAVELENGTHS[:20], _star_spectrum(6.0)[:20], 45.0) is None


def test_a_noiseless_positive_spectrum_has_unlimited_signal() -> None:
    """No scatter at all means nothing to hide the signal."""
    flat = np.full(WAVELENGTHS.size, 1e-3)

    assert estimate_spectrum_signal_to_noise(WAVELENGTHS, flat, 45.0) == float("inf")


def test_the_analysis_declines_a_spectrum_that_is_only_noise() -> None:
    """No classification and no feature tests, and the reason says why."""
    analysis = analyze_spectrum(
        WAVELENGTHS, _noise_only_spectrum(), CAMERA, is_quantum_efficiency_corrected=True
    )

    assert analysis.classification["spectral_type"] == "Unknown"
    assert "no measurable spectrum" in str(analysis.classification["reason"])
    assert analysis.features == []
    assert analysis.signal_to_noise is not None
    assert analysis.signal_to_noise < MINIMUM_SPECTRUM_SIGNAL_TO_NOISE


def test_the_analysis_still_runs_on_a_real_spectrum() -> None:
    """A spectrum with a continuum is analysed as before."""
    analysis = analyze_spectrum(
        WAVELENGTHS, _star_spectrum(20.0), CAMERA, is_quantum_efficiency_corrected=True
    )

    assert "no measurable spectrum" not in str(analysis.classification.get("reason"))
    assert analysis.features
    assert analysis.signal_to_noise is not None
    assert analysis.signal_to_noise >= MINIMUM_SPECTRUM_SIGNAL_TO_NOISE


def _spectrum_with_a_dip_in_noise() -> np.ndarray:
    """Build a flat continuum with a deep Mg b dip and 15% noise.

    Returns
    -------
    intensity : `numpy.ndarray`
        The spectrum. The dip is real but the depth is only known to about
        20%, so the detector's raw p-value is small.
    """
    rng = np.random.default_rng(4)
    spectrum = 1.0 + rng.normal(0.0, 0.15, WAVELENGTHS.size)
    return spectrum * (1.0 - 0.7 * np.exp(-0.5 * ((WAVELENGTHS - 5175.0) / 25.0) ** 2))


def _mg_b(intensity: np.ndarray) -> dict[str, object]:
    """Run the detector and pick out the Mg b entry.

    Returns
    -------
    entry : `dict`
        The Mg b result.
    """
    entries = detect_named_features(
        WAVELENGTHS, intensity, reference_spectral_type=None, resolution_element_angstrom=45.0
    )
    return next(entry for entry in entries if entry["feature"] == "Magnesium b triplet")


def test_a_feature_measured_too_noisily_is_capped_at_inconclusive() -> None:
    """A dip whose depth is uncertain to 20% cannot be called possible."""
    entry = _mg_b(_spectrum_with_a_dip_in_noise())

    assert float(entry["depth_uncertainty"]) > MAXIMUM_UNCERTAINTY_FOR_A_VERDICT
    assert entry["limited_by_noise"] is True
    assert entry["verdict"] == VERDICT_INCONCLUSIVE


def test_the_cap_is_what_holds_the_verdict_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the cap lifted, the same dip is called possible or better."""
    monkeypatch.setattr(feature_detector, "MAXIMUM_UNCERTAINTY_FOR_A_VERDICT", 10.0)

    entry = _mg_b(_spectrum_with_a_dip_in_noise())

    assert entry["verdict"] in ("possible", "detected")


def test_a_precisely_measured_feature_is_not_capped() -> None:
    """Low noise leaves the detector's own verdict untouched."""
    rng = np.random.default_rng(4)
    spectrum = 1.0 + rng.normal(0.0, 0.01, WAVELENGTHS.size)
    spectrum *= 1.0 - 0.5 * np.exp(-0.5 * ((WAVELENGTHS - 5175.0) / 25.0) ** 2)

    entry = _mg_b(spectrum)

    assert float(entry["depth_uncertainty"]) < MAXIMUM_UNCERTAINTY_FOR_A_VERDICT
    assert entry["limited_by_noise"] is False
    assert entry["verdict"] == "detected"
