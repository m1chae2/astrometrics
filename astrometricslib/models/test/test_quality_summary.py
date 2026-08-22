"""Purpose: Unit tests for the per-pipeline quality-summary schemas.

Description: Verifies each summary model constructs with its required
fields and that the shared-base defaults (upstream reference, pipeline
identity, session provenance) are correctly set for each pipeline.
Merged from the former targetlib/test_{asteroid_recovery_quality,
astrometry_quality, photometry_quality, spectroscopy_quality}.py, since
their source modules were merged into models/quality_summary.py.
"""

import pytest

from astrometricslib.models.quality_summary import (
    AsteroidRecoveryPipelineQualityMetrics,
    AsteroidRecoveryQualitySummary,
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


def _make_asteroid_recovery_metrics(**overrides) -> AsteroidRecoveryPipelineQualityMetrics:  # ruff: ignore[missing-type-kwargs]
    defaults = {
        "frames_with_wcs_estimate": 40,
        "frames_excluded_missing_pointing_metadata": 0,
        "candidates_detected": 5,
        "candidates_persistence_confirmed": 3,
        "candidates_rate_linearity_confirmed": 1,
        "candidates_ephemeris_matched": 1,
    }
    defaults.update(overrides)
    return AsteroidRecoveryPipelineQualityMetrics(**defaults)


def test_asteroid_recovery_quality_summary_constructs_with_minimal_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just the required fields."""
    summary = AsteroidRecoveryQualitySummary(
        target_id="M 81", asteroid_recovery_metrics=_make_asteroid_recovery_metrics()
    )
    assert summary.asteroid_recovery_metrics.candidates_detected == 5
    assert summary.pipeline_name == "asteroid_recovery"
    assert summary.upstream_quality_summary_reference == "astrometry"
    assert summary.flagged is False


def test_asteroid_recovery_quality_summary_defaults_to_empty_session_provenance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies session fields default empty until a caller populates them."""
    summary = AsteroidRecoveryQualitySummary(
        target_id="M 81", asteroid_recovery_metrics=_make_asteroid_recovery_metrics()
    )
    assert summary.target_session_ids == []
    assert summary.target_session_breakdown == []


def test_astrometry_quality_summary_constructs_with_minimal_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just the required fields."""
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
    assert summary.flagged is False


def test_astrometry_quality_summary_defaults_to_empty_session_provenance():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies session fields stay empty; astrometry solves one stack."""
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


def test_photometry_quality_summary_constructs_with_minimal_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just the required fields."""
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
    assert summary.flagged is False


def test_photometry_quality_summary_has_no_upstream_reference():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies upstream_quality_summary_reference defaults to None."""
    summary = PhotometryQualitySummary(
        target_id="M 13",
        photometry_metrics=PhotometryPipelineQualityMetrics(
            stars_processed=0, stars_found=0, frames_processed=0, variable_candidate_count=0
        ),
    )
    assert summary.upstream_quality_summary_reference is None


def test_spectroscopy_quality_summary_constructs_with_minimal_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just the required fields."""
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


def test_spectroscopy_quality_summary_trail_width_profile_defaults_unavailable():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify trail_width_profile_available defaults False pre-tracing."""
    summary = SpectroscopyQualitySummary(
        target_id="M 13",
        spectroscopy_metrics=SpectroscopyPipelineQualityMetrics(),
    )
    assert summary.spectroscopy_metrics.trail_width_profile_available is False


def test_stack_quality_summary_constructs_with_minimal_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies the model can be constructed with just the required fields."""
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
    assert summary.flagged is False
    assert summary.pipeline_name == "stacking"
