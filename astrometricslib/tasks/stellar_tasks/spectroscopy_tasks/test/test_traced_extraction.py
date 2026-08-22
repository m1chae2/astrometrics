"""Purpose: Unit tests for rung-3 traced spectral extraction.

Description: Verifies the per-position Gaussian cross-section fit and
trail-centerline polynomial smoothing on a synthetic tilted, width-varying
trace; the per-position fallback to fixed-box extraction on an unfittable
cross-section; and that calibration is unaffected by the extraction method,
confirming the shape-agnostic calibration claim empirically rather than
just by reading the code.
"""

import numpy as np
import pytest

from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectroscopy_instrument import (
    SpectroscopyInstrument,
)
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectrum_calibrator import SpectrumCalibrator
from astrometricslib.tasks.stellar_tasks.spectroscopy_tasks.spectrum_extractor import SpectrumExtractor
from astrometricslib.utilities import AstrometricsImage, CameraConfig, SpectroscopyConfig


class MockAstrometricsImage(AstrometricsImage):
    """Mock AstrometricsImage that accepts a direct array input."""

    def __init__(self, data: np.ndarray, header=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        """Initialize MockAstrometricsImage with given data and header."""
        self._data = data
        self._header = header or {}
        self._wcs = None

    @property
    def data(self) -> np.ndarray:
        """Image array data."""
        return self._data

    @property
    def header(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Image headers dict."""
        return self._header


def _build_tilted_width_varying_trace(  # ruff: ignore[missing-return-type-private-function]
    width: int = 200, height: int = 100, tilt_slope: float = 0.05, background: float = 500.0
):
    """Build a synthetic horizontal trace with a known tilt and width.

    Returns
    -------
    tuple[np.ndarray, dict]
        The trace image data and a mapping of x-position to the true
        Gaussian sigma injected at that column.
    """
    data = np.full((height, width), background)
    true_sigmas = {}
    for x in range(20, width - 20):
        y_center = 50 + tilt_slope * (x - 20)
        sigma = 1.5 + 0.01 * (x - 20)  # widens gradually along the trace
        true_sigmas[x] = sigma
        for dy in range(-15, 16):
            y = round(y_center) + dy
            if 0 <= y < height:
                data[y, x] += 4000 * np.exp(-0.5 * (dy / sigma) ** 2)
    return data, true_sigmas


def test_traced_extraction_centerline_tracks_known_tilt():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the fitted centerline follows a known linear tilt slope."""
    tilt_slope = 0.05
    data, _ = _build_tilted_width_varying_trace(tilt_slope=tilt_slope)
    image = MockAstrometricsImage(data=data)
    extractor = SpectrumExtractor(radius=15)

    _, centerline, _ = extractor.extract_line_traced(image, (20.0, 50.0), np.array([1.0, 0.0]), 160)

    measured_slope = (centerline[150] - centerline[10]) / 140
    assert abs(measured_slope - tilt_slope) < 0.01


def test_traced_extraction_width_matches_known_sigma():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify trail_width_px matches the injected Gaussian sigma."""
    data, true_sigmas = _build_tilted_width_varying_trace()
    image = MockAstrometricsImage(data=data)
    extractor = SpectrumExtractor(radius=15)

    _, _, widths = extractor.extract_line_traced(image, (20.0, 50.0), np.array([1.0, 0.0]), 160)

    for step in (20, 60, 100, 140):
        expected_sigma = true_sigmas[20 + step]
        assert abs(widths[step] - expected_sigma) < 0.3


def test_traced_extraction_falls_back_on_unfittable_cross_section():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a flat, no-signal region falls back to fixed-box extraction."""
    data = np.full((100, 200), 500.0)  # entirely flat -- no trace at all
    image = MockAstrometricsImage(data=data)
    extractor = SpectrumExtractor(radius=10)

    profile, _centerline, widths = extractor.extract_line_traced(
        image, (20.0, 50.0), np.array([1.0, 0.0]), 50
    )

    assert len(profile) == 50
    # every step fell back
    assert all(width == pytest.approx(0.0) for width in widths)
    assert all(np.isfinite(profile))


def test_calibration_shape_unaffected_by_extraction_method():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify wavelengths/intensities shape matches for fixed vs traced."""
    data, _ = _build_tilted_width_varying_trace(tilt_slope=0.0)
    image = MockAstrometricsImage(data=data)
    extractor = SpectrumExtractor(radius=15)

    config = SpectroscopyConfig(
        camera=CameraConfig(name="TestCam", pixel_size_um=5.0, sensor_width_px=2000, sensor_height_px=2000),
        grating_distance_mm=10.0,
    )
    instrument = SpectroscopyInstrument(config)
    calibrator = SpectrumCalibrator(instrument)

    fixed_profile = extractor.extract_line(image, (20.0, 50.0), np.array([1.0, 0.0]), 160)
    traced_profile, _, _ = extractor.extract_line_traced(image, (20.0, 50.0), np.array([1.0, 0.0]), 160)

    fixed_wavelengths, fixed_intensities = calibrator.calibrate(fixed_profile, 0.0)
    traced_wavelengths, traced_intensities = calibrator.calibrate(traced_profile, 0.0)

    assert fixed_wavelengths.shape == traced_wavelengths.shape
    assert fixed_intensities.shape == traced_intensities.shape
    assert len(traced_wavelengths) == 160
