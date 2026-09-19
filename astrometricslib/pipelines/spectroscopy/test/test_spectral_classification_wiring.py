"""Purpose: Verify spectral classification is wired into StellarObject.

Description: `classify_spectral_type` has its own unit tests; this
confirms `_apply_result_to_stellar_object` actually calls it and stores
the result, since that wiring is exactly the kind of thing that's easy
to write, forget to call, or call with the wrong array.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.instrument_response import load_instrument_response
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_correction import (
    interpolate_quantum_efficiency,
)
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_curves import get_quantum_efficiency_curve
from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

# A stand-in for AstrometricsImage carrying only the .timestamp property
# _apply_result_to_stellar_object reads, at a fixed value so history
# entries are deterministic to assert on.
_FAKE_IMAGE = SimpleNamespace(timestamp=1700000000.0)


def _build_pipeline(camera_name: str = "ZWO ASI 533MM Pro") -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` for a named test camera.

    Parameters
    ----------
    camera_name : `str`, optional
        The camera's name. The default has a quantum efficiency curve and
        an instrument response on file; any other name has neither.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline.
    """
    camera = CameraConfig(
        name=camera_name,
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=300.0,
        sensor_max_wavelength=1000.0,
    )
    config = SpectroscopyConfig(camera=camera, grating_distance_mm=16.5)
    return SpectroscopyPipeline(config=config)


def _as_the_instrument_would_record(spectral_type: str) -> tuple[np.ndarray, np.ndarray]:
    """Build the raw counts a reference star would give this instrument.

    The pipeline divides raw counts by the sensor's quantum efficiency and
    then by the instrument response, so multiplying a reference spectrum
    by both is the exact inverse: a correct pipeline recovers the type.

    Returns
    -------
    wavelength_angstrom, counts : `tuple` [`np.ndarray`, `np.ndarray`]
        The reference wavelengths and the simulated raw counts.
    """
    wavelength_angstrom, flux = _get_reference_templates()[spectral_type]
    curve = get_quantum_efficiency_curve("ZWO ASI 533MM Pro")
    response = load_instrument_response("ZWO ASI 533MM Pro")
    quantum_efficiency = interpolate_quantum_efficiency(wavelength_angstrom / 10.0, curve)
    return wavelength_angstrom, flux * quantum_efficiency * response.value_at(wavelength_angstrom)


def test_apply_result_to_stellar_object_sets_a_self_determined_spectral_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a G0V-like extracted spectrum gets classified onto the star."""
    pipeline = _build_pipeline()
    wavelength_angstrom, counts = _as_the_instrument_would_record("G0V")
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": (wavelength_angstrom / 10.0).tolist(),
        "intensities": counts.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, _FAKE_IMAGE)

    assert star.spectroscopy.self_determined_spectral_type == "G0V"
    assert star.spectroscopy.self_determined_spectral_type_confidence > 0.9
    assert star.spectroscopy.self_determined_spectral_type_rms < 0.05
    assert star.spectroscopy.self_determined_spectral_type_candidates
    assert star.spectroscopy.self_determined_spectral_type_candidates[0]["spectral_type"] == "G0V"
    assert isinstance(star.spectroscopy.probable_spectral_features, list)
    assert len(star.spectra_history) == 1
    assert star.spectra_history[0].wavelengths == star.spectroscopy.wavelengths_angstrom
    assert star.spectra_history[0].intensities == star.spectroscopy.intensities


def test_a_camera_with_no_instrument_response_gets_no_spectral_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a spectrum is not classified when its tilt cannot be removed."""
    pipeline = _build_pipeline(camera_name="TestCam")
    wavelength_angstrom, flux = _get_reference_templates()["G0V"]
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": (wavelength_angstrom / 10.0).tolist(),
        "intensities": flux.tolist(),
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, _FAKE_IMAGE)

    assert star.spectroscopy.self_determined_spectral_type == "Unknown"
    assert star.spectroscopy.self_determined_spectral_type_confidence is None
    assert "instrument response" in star.spectroscopy.self_determined_spectral_type_note


def test_apply_result_to_stellar_object_handles_unclassifiable_data():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a too-short extracted spectrum doesn't crash the pipeline."""
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": [500.0, 501.0, 502.0],
        "intensities": [1.0, 1.1, 0.9],
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, _FAKE_IMAGE)

    assert star.spectroscopy.self_determined_spectral_type == "Unknown"
    assert star.spectroscopy.self_determined_spectral_type_confidence is None
    assert star.spectroscopy.self_determined_spectral_type_candidates == []
    assert star.spectroscopy.probable_spectral_features == []


def test_apply_result_to_stellar_object_timestamps_the_history_entry_from_the_image():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the new spectra_history entry uses the image's own timestamp."""
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": [500.0, 501.0, 502.0],
        "intensities": [1.0, 1.1, 0.9],
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, _FAKE_IMAGE)

    assert star.spectra_history[0].timestamp == datetime.fromtimestamp(_FAKE_IMAGE.timestamp, tz=UTC)


def test_apply_result_to_stellar_object_falls_back_to_now_without_a_frame_date():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a frame with no parseable date still records a history entry.

    Mirrors VariabilityAnalyzer's own DATE-OBS fallback: a missing or
    unparseable observation date shouldn't stop the star's spectrum
    from being recorded, just leave its timestamp less precise.
    """
    pipeline = _build_pipeline()
    star = StellarObject(id="TestStar")
    result = {
        "detected_angle": 0.0,
        "wavelengths": [500.0, 501.0, 502.0],
        "intensities": [1.0, 1.1, 0.9],
        "target_pos": (100.0, 200.0),
        "trail_centerline_px": None,
        "trail_width_px": None,
    }

    pipeline._apply_result_to_stellar_object(star, result, SimpleNamespace(timestamp=None))

    assert len(star.spectra_history) == 1
    assert star.spectra_history[0].timestamp is not None
