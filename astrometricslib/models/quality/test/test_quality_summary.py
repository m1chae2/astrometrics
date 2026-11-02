"""Tests for the processing quality reports.

These tests make sure that when we create a quality report (like for stacking
or astrometry), it starts with the correct default values and requires
all the necessary information.
"""

import pytest

from astrometricslib.models.quality.quality_summary import (
    AsteroidDetectionPipelineQualityMetrics,
    AsteroidDetectionQualitySummary,
    AstrometryPipelineQualityMetrics,
    AstrometryQualitySummary,
    ExcludedFrame,
    FrameEnsembleComposition,
    PhotometryPipelineQualityMetrics,
    PhotometryQualitySummary,
    SpectroscopyPipelineQualityMetrics,
    SpectroscopyQualitySummary,
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)


def _make_asteroid_detection_metrics(**overrides) -> AsteroidDetectionPipelineQualityMetrics:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "frames_with_wcs_estimate": 40,
        "frames_excluded_missing_pointing_metadata": 0,
        "candidates_detected": 5,
        "candidates_persistence_confirmed": 3,
        "candidates_rate_linearity_confirmed": 1,
        "candidates_ephemeris_matched": 1,
    }
    defaults.update(overrides)
    return AsteroidDetectionPipelineQualityMetrics(**defaults)


def test_asteroid_detection_quality_summary_constructs_with_minimal_fields() -> None:
    """Check that we can create an asteroid report with only required data."""
    summary = AsteroidDetectionQualitySummary(
        target_id="M 81", asteroid_detection_metrics=_make_asteroid_detection_metrics()
    )
    assert summary.asteroid_detection_metrics.candidates_detected == 5
    assert summary.pipeline_name == "asteroid_detection"
    assert summary.upstream_quality_summary_reference == "astrometry"


def test_asteroid_detection_quality_summary_defaults_to_empty_session_provenance() -> None:
    """Check that the list of observing sessions starts out empty."""
    summary = AsteroidDetectionQualitySummary(
        target_id="M 81", asteroid_detection_metrics=_make_asteroid_detection_metrics()
    )
    assert summary.target_session_ids == []
    assert summary.target_session_breakdown == []


def test_astrometry_quality_summary_constructs_with_minimal_fields() -> None:
    """Check that we can create an astrometry report with minimal data."""
    summary = AstrometryQualitySummary(
        target_id="M 13",
        astrometry_metrics=AstrometryPipelineQualityMetrics(
            sources_detected=42,
            solve_attempted=True,
            plate_solve_succeeded=True,
            simbad_matched_count=10,
        ),
    )
    assert summary.astrometry_metrics.sources_detected == 42
    assert summary.pipeline_name == "astrometry"
    assert summary.upstream_quality_summary_reference == "stacking"


def test_astrometry_quality_summary_defaults_to_empty_session_provenance() -> None:
    """Check that the list of observing sessions stays empty for astrometry."""
    summary = AstrometryQualitySummary(
        target_id="M 13",
        astrometry_metrics=AstrometryPipelineQualityMetrics(
            sources_detected=0,
            solve_attempted=False,
            plate_solve_succeeded=False,
            simbad_matched_count=0,
        ),
    )
    assert summary.target_session_ids == []
    assert summary.target_session_breakdown == []


def test_photometry_quality_summary_constructs_with_minimal_fields() -> None:
    """Check that we can create a photometry report with only required data."""
    summary = PhotometryQualitySummary(
        target_id="M 13",
        photometry_metrics=PhotometryPipelineQualityMetrics(
            stars_processed=200,
            stars_found=200,
            frames_processed=38,
            variable_candidate_count=3,
            frame_ensemble_composition=[
                FrameEnsembleComposition(frame_path="frame_01.fits", ensemble_size=199)
            ],
        ),
    )
    assert summary.photometry_metrics.frames_processed == 38
    assert summary.pipeline_name == "photometry"


def test_photometry_quality_summary_has_no_upstream_reference() -> None:
    """Check that photometry reports don't link back to a previous step."""
    summary = PhotometryQualitySummary(
        target_id="M 13",
        photometry_metrics=PhotometryPipelineQualityMetrics(
            stars_processed=0, stars_found=0, frames_processed=0, variable_candidate_count=0
        ),
    )
    assert summary.upstream_quality_summary_reference is None


def test_spectroscopy_quality_summary_constructs_with_minimal_fields() -> None:
    """Check that we can create a spectroscopy report with minimal data."""
    summary = SpectroscopyQualitySummary(
        target_id="M 13",
        spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(
            zero_order_saturated_pixel_fraction=0.0,
            zero_order_saturation_flagged=False,
            dispersion_angle_deg=1.5,
        ),
    )
    assert summary.spectroscopy_metrics.dispersion_angle_deg == pytest.approx(1.5)
    assert summary.pipeline_name == "spectroscopy"
    assert summary.upstream_quality_summary_reference == "stacking"


def test_spectroscopy_quality_summary_trail_width_profile_defaults_unavailable() -> None:
    """Check that we correctly assume trail width data is missing at first."""
    summary = SpectroscopyQualitySummary(
        target_id="M 13",
        spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(),
    )
    assert summary.spectroscopy_metrics.trail_width_profile_available is False


def test_stack_quality_summary_constructs_with_minimal_fields() -> None:
    """Check that we can create a stacking report with only required data."""
    summary = StackQualitySummary(
        target_id="M 13",
        resolved_parameters={
            "rejection_sigma_low": 2.5,
            "rejection_sigma_high": 2.5,
            "rejection_sigma_mode": "adaptive",
        },
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False,
            frames_submitted=40,
            frames_stacked=38,
            excluded_frames=[ExcludedFrame(path="bad.fits", reason="minority gain")],
        ),
    )
    assert summary.stacking_metrics.frames_stacked == 38
    assert summary.stacking_metrics.excluded_frames[0].reason == "minority gain"
    assert summary.pipeline_name == "stacking"


def test_a_stored_stacking_summary_with_the_removed_yes_no_fields_still_loads() -> None:
    """Old rows with the dropped yes/no fields load and keep the numbers."""
    stored = {
        "isSpectral": False,
        "framesSubmitted": 10,
        "framesStacked": 9,
        "rejectedPixelFraction": 0.2,
        "rejectedFractionFlagged": True,
        "saturatedPixelFraction": 0.01,
        "saturationFlagged": True,
        "zeroPixelFraction": 0.6,
        "zeroFractionFlagged": True,
        "negativePixelMaxPercent": 70,
        "negativePixelsFlagged": True,
        "fwhmDegraded": True,
        "timedOut": True,
    }

    metrics = StackingPipelineQualityMetrics.model_validate(stored)

    assert metrics.rejected_pixel_fraction == pytest.approx(0.2)
    assert metrics.zero_pixel_fraction == pytest.approx(0.6)
    assert metrics.negative_pixel_max_percent == 70
    for removed in ("rejected_fraction_flagged", "saturation_flagged", "fwhm_degraded", "timed_out"):
        assert not hasattr(metrics, removed)


def test_a_stored_summary_with_the_removed_flag_fields_loads() -> None:
    """Old rows hold `flagged`, `flagReasons`, `inputQuality` and more.

    The models ignore the flag fields. The frame counts of `inputQuality`
    move to the stack metrics, so an old stack keeps its numbers.
    """
    stored = {
        "targetId": "M 81",
        "flagged": True,
        "flagReasons": ["2 frame(s) with clouds or trailed stars moved to the _excluded folder"],
        "stackingMetrics": {"isSpectral": False, "framesSubmitted": 40, "framesStacked": 38},
        "inputQuality": {
            "framesSubmitted": 40,
            "framesAccepted": 38,
            "framesExcludedForGain": 1,
            "framesQuarantined": 2,
            "darkUniqueFrameCount": 12,
            "isFlagged": True,
            "flagReasons": ["2 frame(s) with clouds or trailed stars moved to the _excluded folder"],
        },
        "outputQuality": {"isFlagged": False, "flagReasons": []},
    }
    summary = StackQualitySummary.model_validate(stored)
    assert not hasattr(summary, "flagged")
    assert not hasattr(summary, "flag_reasons")
    assert summary.stacking_metrics.frames_accepted == 38
    assert summary.stacking_metrics.frames_excluded_for_gain == 1
    assert summary.stacking_metrics.frames_quarantined == 2
    assert summary.stacking_metrics.dark_unique_frame_count == 12
    assert "flagged" not in summary.model_dump(by_alias=True)
    assert "flagReasons" not in summary.model_dump(by_alias=True)


def test_stored_summaries_of_the_other_pipelines_with_flag_fields_still_load() -> None:
    """The flag fields and the removed spectral list are ignored on load."""
    spectroscopy = SpectroscopyQualitySummary.model_validate({
        "targetId": "M 13",
        "flagged": True,
        "flagReasons": ["spectral classification uncertain for 2 star(s)"],
        "spectroscopyMetrics": {
            "poorMatchClassificationCount": 1,
            "flaggedSpectralClassifications": [
                {"starId": "HD 1", "reason": "poor_match", "spectralType": "A0V"}
            ],
        },
    })
    assert spectroscopy.spectroscopy_metrics.poor_match_classification_count == 1
    assert not hasattr(spectroscopy.spectroscopy_metrics, "flagged_spectral_classifications")
    astrometry = AstrometryQualitySummary.model_validate({
        "targetId": "TEST",
        "flagged": True,
        "flagReasons": ["plate solve failed"],
        "astrometryMetrics": {
            "sourcesDetected": 0,
            "solveAttempted": False,
            "plateSolveSucceeded": False,
            "simbadMatchedCount": 0,
        },
    })
    assert astrometry.target_id == "TEST"


def test_older_stack_results_and_target_reports_with_the_removed_keys_load() -> None:
    """`flagged`, `flag_reasons` and `flaggedPipelines` are ignored on load."""
    from astrometricslib.models.processing_results import StackResult

    result = StackResult.model_validate({
        "target_id": "M 81",
        "kind": "imaging",
        "frames_selected": 3,
        "first_file": "a",
        "last_file": "c",
        "flagged": True,
        "flag_reasons": ["x"],
    })
    assert result.frames_selected == 3
    assert not hasattr(result, "flagged")
