"""Equivalence tests for the calibration tuner's absorption-dip detection.

The tuner's hand-rolled local-minimum scan was replaced with
scipy.signal.find_peaks on the negated spectrum (prominence, wlen=31,
distance=6). These tests confirm the replacement finds the same absorption
features as the historical algorithm on a synthetic Balmer-line spectrum, so
the downstream best-RMS combinatorial fit receives an equivalent candidate set.

Also covers the continuum-normalized fallback search: on a spectrum with a
strong background slope, a dip sitting in a faint (low-continuum) region can
have too little *absolute* depth for the raw scan's prominence floor, even
though its *fractional* depth relative to the local continuum is normal.
Dividing by a fitted continuum before re-running the same scan recovers it.
"""

import numpy as np
from scipy.signal import find_peaks


def _historical_dip_scan(smoothed_intensities: np.ndarray, minimum_depth: float) -> list:
    """Reimplement the pre-replacement dip-detection scan for reference.

    Returns
    -------
    list
        Indices of detected absorption dips.
    """
    dips = []
    for index in range(5, len(smoothed_intensities) - 5):
        window = smoothed_intensities[index - 5 : index + 6]
        if smoothed_intensities[index] == np.min(window):
            left_maximum = np.max(smoothed_intensities[max(0, index - 15) : index])
            right_maximum = np.max(smoothed_intensities[index : min(len(smoothed_intensities), index + 16)])
            depth = min(left_maximum, right_maximum) - smoothed_intensities[index]
            if depth > minimum_depth:
                dips.append(index)
    return dips


def _find_peaks_dip_scan(smoothed_intensities: np.ndarray, minimum_depth: float) -> list:
    """Run the replacement scan using calibration_tuner.py's parameters.

    Returns
    -------
    list
        Indices of detected absorption dips.
    """
    peak_indices, _ = find_peaks(-smoothed_intensities, prominence=minimum_depth, wlen=31, distance=6)
    return [int(index) for index in peak_indices if 5 <= index < len(smoothed_intensities) - 5]


def _synthetic_balmer_spectrum() -> np.ndarray:
    """Build a smooth continuum with three Gaussian absorption dips.

    Dip positions and depths loosely model H-delta, H-gamma, and
    H-beta as they appear in a Star Analyser 200 extraction: well
    separated (> 40 px), depths of 5-15% of the continuum. Mild
    Gaussian noise is added before boxcar smoothing.

    Returns
    -------
    np.ndarray
        Smoothed synthetic spectrum with three absorption dips.
    """
    rng = np.random.default_rng(42)
    pixel_axis = np.arange(400, dtype=float)
    continuum = 1.0 - 0.0005 * (pixel_axis - 200.0) ** 2 / 200.0
    spectrum = continuum.copy()
    for center, depth, sigma in ((80.0, 0.10, 4.0), (160.0, 0.15, 5.0), (280.0, 0.08, 4.0)):
        spectrum -= depth * np.exp(-0.5 * ((pixel_axis - center) / sigma) ** 2)
    spectrum += rng.normal(0.0, 0.001, size=len(pixel_axis))
    # Boxcar smoothing mirrors SpectrumCalibrator.apply_smoothing(window=5)
    return np.convolve(spectrum, np.ones(5) / 5.0, mode="same")


def test_find_peaks_matches_historical_scan_on_synthetic_balmer_spectrum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify find_peaks matches the historical scan on synthetic data."""
    spectrum = _synthetic_balmer_spectrum()
    historical_dips = _historical_dip_scan(spectrum, minimum_depth=0.01)
    replacement_dips = _find_peaks_dip_scan(spectrum, minimum_depth=0.01)

    assert len(historical_dips) == 3
    assert len(replacement_dips) == 3
    # Same features to within +/-1 px (ties inside flat minima may
    # resolve to an adjacent sample; the downstream L-BFGS-B fit is
    # insensitive to this).
    for historical_index, replacement_index in zip(
        sorted(historical_dips), sorted(replacement_dips), strict=False
    ):
        assert abs(historical_index - replacement_index) <= 1


def test_find_peaks_rejects_shallow_noise_dips():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the replacement scan reports no dips on pure noise."""
    rng = np.random.default_rng(7)
    noise_only = 1.0 + rng.normal(0.0, 0.001, size=400)
    smoothed = np.convolve(noise_only, np.ones(5) / 5.0, mode="same")
    assert _find_peaks_dip_scan(smoothed, minimum_depth=0.01) == []


def test_find_peaks_respects_edge_exclusion():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify dips forced onto the excluded edge are not reported."""
    spectrum = _synthetic_balmer_spectrum()
    # A dip forced onto the excluded leading edge must not be reported
    spectrum[2] -= 0.5
    replacement_dips = _find_peaks_dip_scan(spectrum, minimum_depth=0.01)
    assert all(index >= 5 for index in replacement_dips)


def _raw_dip_scan(smoothed_intensities: np.ndarray) -> list:
    """Reimplement calibration_tuner.py's primary (non-normalized) dip sweep.

    Sweeps `minimum_depth` from 0.01 down to 0.001 in steps of 0.002,
    stopping as soon as 3 dips are found.

    Returns
    -------
    list
        Indices of detected absorption dips.
    """
    minimum_depth = 0.01
    dips: list = []
    while len(dips) < 3 and minimum_depth >= 0.001:
        peak_indices, _ = find_peaks(-smoothed_intensities, prominence=minimum_depth, wlen=31, distance=6)
        dips = [int(index) for index in peak_indices if 5 <= index < len(smoothed_intensities) - 5]
        minimum_depth -= 0.002
    return dips


def _continuum_normalized_fallback_scan(smoothed_intensities: np.ndarray) -> list:
    """Reimplement calibration_tuner.py's continuum-normalized fallback sweep.

    Fits a cubic polynomial baseline, divides it out, then re-runs the
    same depth sweep as `_raw_dip_scan` against the normalized spectrum.

    Returns
    -------
    list
        Indices of detected absorption dips.
    """
    pixel_index = np.arange(len(smoothed_intensities))
    polynomial_coefficients = np.polyfit(pixel_index, smoothed_intensities, 3)
    continuum = np.polyval(polynomial_coefficients, pixel_index)
    normalized_spectrum = smoothed_intensities / np.maximum(continuum, 1e-6)

    minimum_depth = 0.01
    dips: list = []
    while len(dips) < 3 and minimum_depth >= 0.001:
        peak_indices, _ = find_peaks(-normalized_spectrum, prominence=minimum_depth, wlen=31, distance=6)
        dips = [int(index) for index in peak_indices if 5 <= index < len(smoothed_intensities) - 5]
        minimum_depth -= 0.002
    return dips


def _synthetic_sloped_continuum_spectrum_with_faint_dip() -> tuple[np.ndarray, list[float]]:
    """Build a strongly-sloped continuum with three fractionally-equal dips.

    The continuum ramps from 2% to 100% of full scale across the
    array, so a dip near the faint end has the same ~12% *fractional*
    depth as the others but a much smaller *absolute* depth -- the
    scenario the continuum-normalized fallback exists for.

    Returns
    -------
    spectrum : `numpy.ndarray`
        Smoothed synthetic spectrum with three absorption dips.
    dip_centers : `list` [`float`]
        The pixel centers the three dips were placed at.
    """
    rng = np.random.default_rng(3)
    pixel_axis = np.arange(400, dtype=float)
    continuum = 0.02 + 0.98 * (pixel_axis / 399.0)
    dip_centers = [60.0, 200.0, 340.0]
    spectrum = continuum.copy()
    for center in dip_centers:
        local_continuum = 0.02 + 0.98 * (center / 399.0)
        spectrum -= 0.12 * local_continuum * np.exp(-0.5 * ((pixel_axis - center) / 4.0) ** 2)
    spectrum += rng.normal(0.0, 0.0005, size=len(pixel_axis))
    smoothed = np.convolve(spectrum, np.ones(5) / 5.0, mode="same")
    return smoothed, dip_centers


def test_raw_dip_scan_misses_the_faint_end_dip_on_a_sloped_continuum():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the raw (non-normalized) scan under-detects on a slope.

    Establishes that the fallback is actually needed for this
    spectrum, not just that it happens to also succeed.
    """
    spectrum, _dip_centers = _synthetic_sloped_continuum_spectrum_with_faint_dip()
    raw_dips = _raw_dip_scan(spectrum)
    assert len(raw_dips) < 3


def test_continuum_normalized_fallback_recovers_all_three_dips():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the continuum-normalized fallback finds all three dips.

    Where the raw scan under-detects (previous test), dividing out the
    fitted continuum restores each dip to a comparable fractional
    depth, letting the same prominence sweep find all three.
    """
    spectrum, dip_centers = _synthetic_sloped_continuum_spectrum_with_faint_dip()
    normalized_dips = _continuum_normalized_fallback_scan(spectrum)

    assert len(normalized_dips) == 3
    for expected_center, detected_index in zip(sorted(dip_centers), sorted(normalized_dips), strict=True):
        assert abs(expected_center - detected_index) <= 1
