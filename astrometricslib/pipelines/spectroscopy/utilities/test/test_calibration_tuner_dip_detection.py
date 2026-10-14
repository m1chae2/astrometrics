"""Tests for the calibration tuner's absorption-dip detection.

The tuner's hand-rolled local-minimum scan was replaced with
scipy.signal.find_peaks on the negated spectrum (prominence, wlen=31,
distance=6). The first tests confirm the replacement finds the same
absorption features as the historical algorithm on a synthetic Balmer-line
spectrum, so the downstream best-RMS combinatorial fit receives an equivalent
candidate set.

The tuner now divides the spectrum by a running continuum (a wide median)
before the search. The depth a dip needs is then a fraction of the continuum,
not a number of camera counts, and a dip must also be several times deeper
than the local noise. The remaining tests cover this:

- a strongly sloped continuum no longer hides the dip at the faint end;
- a spectrum in camera counts (3000 ADU continuum, Poisson noise) gives a
  handful of dips, where the old absolute prominence of 0.001 gave dozens;
- a spectrum of pure noise gives no dips;
- no more than `_MAX_DIP_CANDIDATES` dips are returned, and they are the
  deepest;
- the parabolic refinement puts a dip at a fractional sample to 0.1 sample.
"""

import numpy as np
import pytest
from scipy.signal import find_peaks

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.pipelines.spectroscopy.utilities.calibration_tuner import (
    _MAX_DIP_CANDIDATES,
    SpectroscopyCalibrationTuner,
)


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


def test_find_peaks_matches_historical_scan_on_synthetic_balmer_spectrum() -> None:
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


def test_find_peaks_rejects_shallow_noise_dips() -> None:
    """Verify the replacement scan reports no dips on pure noise."""
    rng = np.random.default_rng(7)
    noise_only = 1.0 + rng.normal(0.0, 0.001, size=400)
    smoothed = np.convolve(noise_only, np.ones(5) / 5.0, mode="same")
    assert _find_peaks_dip_scan(smoothed, minimum_depth=0.01) == []


def test_find_peaks_respects_edge_exclusion() -> None:
    """Verify dips forced onto the excluded edge are not reported."""
    spectrum = _synthetic_balmer_spectrum()
    # A dip forced onto the excluded leading edge must not be reported
    spectrum[2] -= 0.5
    replacement_dips = _find_peaks_dip_scan(spectrum, minimum_depth=0.01)
    assert all(index >= 5 for index in replacement_dips)


def _raw_dip_scan(smoothed_intensities: np.ndarray) -> list:
    """Reimplement the tuner's old absolute-prominence sweep for comparison.

    This is the search the tuner used before it normalized by the continuum.
    Sweeps `minimum_depth` from 0.01 down to 0.001 in steps of 0.002 in the
    spectrum's own units, stopping as soon as 3 dips are found.

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


def _synthetic_sloped_continuum_spectrum_with_faint_dip() -> tuple[np.ndarray, list[float]]:
    """Build a strongly-sloped continuum with three fractionally-equal dips.

    The continuum ramps from 2% to 100% of full scale across the
    array, so a dip near the faint end has the same ~12% *fractional*
    depth as the others but a much smaller *absolute* depth -- the
    scenario the continuum normalization exists for.

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


def test_raw_dip_scan_misses_the_faint_end_dip_on_a_sloped_continuum() -> None:
    """Verify the old absolute-prominence scan under-detects on a slope.

    Establishes that the continuum normalization is actually needed for
    this spectrum, not just that the new search also happens to succeed.
    """
    spectrum, _dip_centers = _synthetic_sloped_continuum_spectrum_with_faint_dip()
    raw_dips = _raw_dip_scan(spectrum)
    assert len(raw_dips) < 3


def test_tuner_recovers_all_three_dips_on_a_sloped_continuum() -> None:
    """Verify the tuner finds all three dips where the old scan missed one.

    Dividing out the running continuum restores each dip to a comparable
    fractional depth, so the same sweep finds all three.
    """
    spectrum, dip_centers = _synthetic_sloped_continuum_spectrum_with_faint_dip()
    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(spectrum)

    assert len(dips) == 3
    for expected_center, detected_index in zip(sorted(dip_centers), dips, strict=True):
        assert abs(expected_center - detected_index) <= 1


# Where the three synthetic Balmer-like dips sit in the camera-count tests,
# in samples. The second and third are between samples on purpose.
_ADU_DIP_CENTERS = (120.0, 250.4, 430.7)


def _poisson_adu_spectrum(continuum_adu: float, depth: float, seed: int) -> np.ndarray:
    """Build a spectrum in camera counts with Poisson noise and three dips.

    The continuum rises and falls by 30 percent across 600 samples, like
    the response of a real camera. Each dip is a Gaussian 3 samples wide
    (standard deviation) and `depth` of the continuum deep. The counts get
    Poisson noise, then the same 5-sample boxcar smoothing the tuner applies.

    Parameters
    ----------
    continuum_adu : `float`
        The mean continuum, in camera counts (ADU).
    depth : `float`
        How deep each dip is, as a fraction of the continuum.
    seed : `int`
        Seed for the noise.

    Returns
    -------
    smoothed : `numpy.ndarray`
        The smoothed spectrum, in camera counts.
    """
    rng = np.random.default_rng(seed)
    samples = np.arange(600, dtype=float)
    continuum = continuum_adu * (1.0 + 0.3 * np.sin(samples / 200.0))
    profile = continuum.copy()
    for center in _ADU_DIP_CENTERS:
        profile -= depth * continuum * np.exp(-0.5 * ((samples - center) / 3.0) ** 2)
    counts = rng.poisson(profile).astype(float)
    return np.convolve(counts, np.ones(5) / 5.0, mode="same")


@pytest.mark.parametrize("seed", [0, 1, 2, 3])
def test_adu_scale_noisy_spectrum_gives_few_candidates_including_the_true_dips(seed: int) -> None:
    """A noisy 3000 ADU spectrum gives few dips, the true ones among them.

    The old search used a prominence of 0.001 counts, which this noise
    exceeds everywhere, and reported dozens of dips. The new search
    scales to the continuum and the noise, so it returns at most
    `_MAX_DIP_CANDIDATES` and each true line has a dip within 1 sample.
    """
    smoothed = _poisson_adu_spectrum(3000.0, 0.4, seed)

    assert len(_raw_dip_scan(smoothed)) > 30
    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
    centers = SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, dips)

    assert 3 <= len(dips) <= _MAX_DIP_CANDIDATES
    assert dips == sorted(dips)
    for true_center in _ADU_DIP_CENTERS:
        assert min(abs(np.array(centers) - true_center)) < 1.0


def test_pure_noise_spectrum_gives_no_dips() -> None:
    """A spectrum of pure Poisson noise raises instead of reporting noise dips.

    The dips of a 3000 ADU noise-only spectrum are all within a few times
    the noise, which the noise requirement rejects.
    """
    rng = np.random.default_rng(11)
    counts = rng.poisson(3000.0, size=600).astype(float)
    smoothed = np.convolve(counts, np.ones(5) / 5.0, mode="same")

    with pytest.raises(ProcessingError, match="at least 3 absorption features"):
        SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)


def test_candidates_are_capped_to_the_deepest_dips() -> None:
    """With 20 real dips, the 12 deepest are returned.

    The dips get deeper from left to right, so the deepest 12 are the last
    12 placed.
    """
    samples = np.arange(1100, dtype=float)
    centers = 50.0 + 50.0 * np.arange(20)
    depths = np.linspace(0.05, 0.4, 20)
    spectrum = np.full(samples.size, 3000.0)
    for center, depth in zip(centers, depths, strict=True):
        spectrum -= 3000.0 * depth * np.exp(-0.5 * ((samples - center) / 3.0) ** 2)
    smoothed = np.convolve(spectrum, np.ones(5) / 5.0, mode="same")

    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)

    assert len(dips) == _MAX_DIP_CANDIDATES
    np.testing.assert_allclose(dips, centers[-_MAX_DIP_CANDIDATES:], atol=1.0)


@pytest.mark.parametrize("fraction", [0.0, 0.1, 0.25, 0.4, 0.5, 0.6, 0.75, 0.9])
def test_refinement_recovers_a_dip_at_a_fractional_column(fraction: float) -> None:
    """A dip placed between samples is recovered to 0.1 sample.

    Three dips are placed at `100 + fraction`, `200 + fraction` and
    `300 + fraction`, with a sloping continuum. The whole-sample position
    can be up to 0.5 sample off; the refined one must be within 0.1.

    Parameters
    ----------
    fraction : `float`
        The fractional part of each dip's centre, in samples.
    """
    samples = np.arange(400, dtype=float)
    true_centers = np.array([100.0, 200.0, 300.0]) + fraction
    spectrum = 1000.0 + 0.4 * samples
    for center in true_centers:
        spectrum -= 300.0 * np.exp(-0.5 * ((samples - center) / 1.3) ** 2)
    smoothed = np.convolve(spectrum, np.ones(5) / 5.0, mode="same")

    dips = SpectroscopyCalibrationTuner._detect_absorption_dips(smoothed)
    centers = SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, dips)

    assert len(centers) == 3
    np.testing.assert_allclose(centers, true_centers, atol=0.1)
    assert all(abs(center - dip) <= 0.5 for center, dip in zip(centers, dips, strict=True))


def test_refinement_keeps_the_index_of_a_dip_with_a_flat_bottom() -> None:
    """A flat-bottomed dip has no parabola, so it keeps its index."""
    smoothed = np.array([5.0, 5.0, 4.0, 3.0, 3.0, 3.0, 4.0, 5.0, 5.0])

    assert SpectroscopyCalibrationTuner._refine_dip_centers(smoothed, [4]) == [4.0]
