"""Tests for the once-per-spectrum record of detection label limits.

The record must repeat the detectors' own constants and the register ids, and
a spectrum stored before the record existed must still load.
"""

from pathlib import Path

from astrometricslib.models.stellar_source import SpectroscopyResult
from astrometricslib.pipelines.spectroscopy.processing import (
    emission_line_detector,
    spectral_feature_detector,
)
from astrometricslib.pipelines.spectroscopy.processing.detection_label_limits import (
    build_detection_label_limits,
)

REGISTER_FILE = "documentation/library_design/validation/spectroscopy.md"


def test_the_record_repeats_the_detector_constants() -> None:
    """Every stored limit equals the constant the detector uses."""
    limits = build_detection_label_limits()
    features = limits.absorption_features
    assert features.detected_p_value == spectral_feature_detector.DETECTED_P_VALUE
    assert features.possible_p_value == spectral_feature_detector.POSSIBLE_P_VALUE
    assert features.inconclusive_p_value == spectral_feature_detector.INCONCLUSIVE_P_VALUE
    assert features.inconclusive_minimum_depth == spectral_feature_detector.INCONCLUSIVE_MINIMUM_DEPTH
    assert features.minimum_reported_depth == spectral_feature_detector.MINIMUM_REPORTED_DEPTH
    assert (
        features.maximum_depth_uncertainty_for_label
        == spectral_feature_detector.MAXIMUM_UNCERTAINTY_FOR_A_VERDICT
    )
    lines = limits.emission_lines
    assert lines.detected_significance == emission_line_detector.DETECTED_SIGNIFICANCE
    assert lines.unclear_significance == emission_line_detector.UNCLEAR_SIGNIFICANCE
    assert lines.minimum_covered_fraction == emission_line_detector.MINIMUM_COVERED_FRACTION
    assert (
        lines.minimum_detected_for_emission_source
        == emission_line_detector.MINIMUM_DETECTED_FOR_EMISSION_SOURCE
    )


def test_the_register_ids_are_headings_in_the_validation_register() -> None:
    """Each stored register id names an entry in the register."""
    register = Path(__file__).resolve().parents[5] / REGISTER_FILE
    text = register.read_text(encoding="utf-8")
    limits = build_detection_label_limits()
    assert f"### {limits.absorption_features.register_entry}\n" in text
    assert f"### {limits.emission_lines.register_entry}\n" in text
    assert limits.absorption_features.register_entry == "SPE-24"
    assert limits.emission_lines.register_entry == "SPE-25"


def test_the_record_round_trips_through_the_stored_form() -> None:
    """Saving a spectrum and loading it back keeps the record."""
    saved = SpectroscopyResult(detection_label_limits=build_detection_label_limits())
    loaded = SpectroscopyResult.model_validate(saved.model_dump(by_alias=True))
    assert loaded.detection_label_limits == build_detection_label_limits()


def test_an_old_stored_spectrum_without_the_record_still_loads() -> None:
    """A row saved before the record existed loads with it empty."""
    old_row = {
        "probableSpectralFeatures": [{"feature": "H-alpha", "verdict": "detected"}],
        "emissionLines": [],
    }
    loaded = SpectroscopyResult.model_validate(old_row)
    assert loaded.detection_label_limits is None
    assert loaded.probable_spectral_features[0]["verdict"] == "detected"
