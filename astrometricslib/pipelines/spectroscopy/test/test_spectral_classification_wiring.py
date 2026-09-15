"""Purpose: Verify spectral classification is wired into StellarObject.

Description: `classify_spectral_type` has its own unit tests; this
confirms `_apply_result_to_stellar_object` actually calls it and stores
the result, since that wiring is exactly the kind of thing that's easy
to write, forget to call, or call with the wrong array.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.spectroscopy.pipeline import SpectroscopyPipeline
from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates
from astrometricslib.utilities import CameraConfig, SpectroscopyConfig

# A stand-in for AstrometricsImage carrying only the .timestamp property
# _apply_result_to_stellar_object reads, at a fixed value so history
# entries are deterministic to assert on.
_FAKE_IMAGE = SimpleNamespace(timestamp=1700000000.0)


def _build_pipeline() -> SpectroscopyPipeline:
    """Build a `SpectroscopyPipeline` with an unregistered test camera.

    Returns
    -------
    pipeline : `SpectroscopyPipeline`
        The constructed pipeline. The camera has no QE curve on file, so
        classification falls back to the raw extracted intensities.
    """
    camera = CameraConfig(
        name="TestCam",
        pixel_size_um=3.76,
        sensor_width_px=900,
        sensor_height_px=900,
        sensor_min_wavelength=350.0,
        sensor_max_wavelength=900.0,
    )
    config = SpectroscopyConfig(camera=camera, grating_distance_mm=16.5)
    return SpectroscopyPipeline(config=config)


def test_apply_result_to_stellar_object_sets_a_self_determined_spectral_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a G0V-like extracted spectrum gets classified onto the star."""
    pipeline = _build_pipeline()
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

    assert star.spectroscopy.self_determined_spectral_type == "G0V"
    assert star.spectroscopy.self_determined_spectral_type_confidence > 0.99
    assert star.spectroscopy.self_determined_spectral_type_candidates
    assert star.spectroscopy.self_determined_spectral_type_candidates[0]["spectral_type"] == "G0V"
    assert isinstance(star.spectroscopy.probable_spectral_features, list)
    assert len(star.spectra_history) == 1
    assert star.spectra_history[0].wavelengths == star.spectroscopy.wavelengths_angstrom
    assert star.spectra_history[0].intensities == star.spectroscopy.intensities


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
