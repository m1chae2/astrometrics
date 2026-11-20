"""Purpose: Unit tests for the absorption-feature detector tuner.

Description: Checks the pure functions of `feature_detection_tuning` on
synthetic spectra with a known dip, and that the tuner declines a filter
without spectra and stays silent with no stored data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.tuning import TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy.processing.feature_detection_tuning import (
    FEATURE_CONTINUUM_BANDS_TUNER,
    band_depth,
    control_centers,
    control_scatter,
    feature_geometry,
    moving_band_changes,
    sample_counts,
    spectrum_point_limit,
    tune_feature_continuum_bands,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import StoredSpectrum


def test_band_depth_recovers_a_known_dip() -> None:
    """A flat continuum of 100 with a core at 80 has a depth of 0.2."""
    wavelength = np.arange(4000.0, 5000.0, 5.0)
    intensity = np.full(wavelength.size, 100.0)
    intensity[np.abs(wavelength - 4861.0) <= 25.0] = 80.0
    depth = band_depth(wavelength, intensity, 4861.0, 25.0, 75.0, 150.0, 1, 2, 8)
    assert depth == pytest.approx(0.2)
    assert band_depth(wavelength, intensity, 4861.0, 25.0, 75.0, 150.0, 1, 100, 8) is None
    assert band_depth(wavelength, intensity, 4990.0, 25.0, 75.0, 150.0, 1, 2, 8) is None


def test_control_centers_stay_clear_of_features_and_edges() -> None:
    """Control centers stay clear of features and edges.

    No control lies within the keep-clear distance of a feature or inside the
    reach of an edge.
    """
    wavelength = np.arange(3800.0, 8000.0, 11.0)
    centers = control_centers(wavelength, 200.0, 15.0, np.array([4861.0]), 100.0)
    assert centers.min() >= 4000.0
    assert centers.max() <= 7800.0
    assert np.all(np.abs(centers - 4861.0) > 100.0)
    assert control_centers(wavelength[:10], 200.0, 15.0, np.array([4861.0]), 100.0).size == 0


def test_control_scatter() -> None:
    """Control scatter.

    The scatter is the scaled median absolute deviation, and two depths are too
    few.
    """
    assert control_scatter([0.0, 0.0, 0.0, 0.1]) == pytest.approx(0.0)
    assert control_scatter([1.0, 2.0, 3.0]) == pytest.approx(1.4826)
    assert control_scatter([1.0, 2.0]) is None


def test_feature_geometry_follows_the_detector_rule() -> None:
    """The inner edge is the larger of a half-window and the resolution."""
    assert feature_geometry(25.0, 50.0, 1.5, 3.0) == pytest.approx((75.0, 150.0))
    assert feature_geometry(25.0, 10.0, 1.5, 3.0) == pytest.approx((25.0, 100.0))


def test_moving_band_changes() -> None:
    """Moving band changes.

    The change is the median absolute depth difference between neighboring
    edges.
    """
    changes = moving_band_changes([[0.1, 0.2], [0.1, 0.25], [0.1, None]])
    assert changes == [pytest.approx(0.025), pytest.approx(0.0)]


def test_sample_counts_and_spectrum_limit() -> None:
    """Sample counts and spectrum limit.

    Every named feature fits in a wide spectrum, and the limit is the coverage
    quantile rounded up.
    """
    wavelength = np.arange(3000.0, 9000.0, 11.0)
    spectrum = StoredSpectrum("A", "T", wavelength, np.ones(wavelength.size), 50.0, None, False)
    counts = sample_counts(spectrum, 50.0, 1.5, 3.0)
    assert len(counts) == 8
    assert all(core in (3, 4, 5) for core, _ in counts[:1] + counts[2:3])
    assert all(shoulder > 8 for _, shoulder in counts)
    assert spectrum_point_limit([30.0, 40.0, 50.0], 1.0) == 50
    assert spectrum_point_limit([30.5, 40.0], 0.0) == 31


def test_tuner_sets_the_six_feature_limits() -> None:
    """The tuner names the six continuum-band limits."""
    assert len(FEATURE_CONTINUUM_BANDS_TUNER.parameters) == 6


def test_tuner_skips_without_data() -> None:
    """Tuner skips without data.

    A filter without spectra, or a library with none, gives a skip, not a
    guess.
    """
    astrometrics = SimpleNamespace(targets=SimpleNamespace(list=list))
    setup = SetupConfig(name="s", camera_name="cam", optic_name="optic")
    for filter_name in ("L", "SPEC"):
        inputs = TuningInputs(setup, filter_name, None, {"astrometrics": astrometrics})
        with pytest.raises(TuningSkippedError):
            tune_feature_continuum_bands(inputs)
