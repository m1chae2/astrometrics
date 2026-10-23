"""A fixed photometry run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) needs every
pipeline that records measurements to show that its record step emits each
metric the pipeline declares. This file runs the photometry record step on one
healthy run whose every value is filled, so no metric is left out for being
missing.
"""

from datetime import UTC, datetime

from astrometricslib.models.quality.gate_result import GateResult
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import PhotometryPipelineQualityMetrics
from astrometricslib.pipelines.photometry.post_processing.record_quality import (
    PhotometryRunInputs,
    photometry_run_gates,
    photometry_run_measurements,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import CentroidShiftSummary
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import ComparisonSetResult
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext

FIXTURE_COMPARISON_SETS = (
    ComparisonSetResult(
        star_ids=tuple(f"Star_{index}" for index in range(12)),
        scatter_mag=0.0012,
        expected_error_mag=0.0011,
        candidate_count=13,
        uses_errors=True,
        frame_sizes=(12,) * 40,
    ),
)

FIXTURE_CENTROID_SUMMARIES = (
    CentroidShiftSummary(
        frame_count=40,
        measurement_count=400,
        fallback_count=2,
        median_offset_px=0.05,
        p95_offset_px=0.2,
        worst_frame=None,
        worst_frame_p95_offset_px=None,
        corner_displacement_p95_px=0.1,
        fitted_frame_count=39,
    ),
)


def photometry_fixture_metrics() -> PhotometryPipelineQualityMetrics:
    """Build the summary metrics of a healthy two-session run.

    Returns
    -------
    metrics : `PhotometryPipelineQualityMetrics`
        Metrics with every number filled.
    """
    return PhotometryPipelineQualityMetrics(
        stars_processed=120,
        stars_found=120,
        frames_processed=78,
        variable_candidate_count=3,
        cross_session_match_count=90,
        long_term_variable_candidate_count=1,
        astrometry_identified_star_count=100,
        catalog_matched_star_count=100,
        position_only_star_count=15,
        unresolved_star_count=5,
        light_curve_scatter_rms_mag=0.012,
    )


def photometry_fixture_inputs() -> PhotometryRunInputs:
    """Build the run facts of the healthy run.

    Returns
    -------
    inputs : `PhotometryRunInputs`
        Inputs with every value present and a known gain.
    """
    return PhotometryRunInputs(
        comparison_sets=FIXTURE_COMPARISON_SETS,
        registration_drifts_px=[3.0, 5.0, None],
        centroid_shift_summaries=FIXTURE_CENTROID_SUMMARIES,
        stars_with_scatter=80,
        noise_model_star_count=60,
        median_flux_error_mag=0.02,
        errors_assume_unit_gain=False,
    )


def photometry_fixture_gates() -> list[GateResult]:
    """Build the eleven gates of the healthy run.

    Returns
    -------
    gates : `list` [`GateResult`]
        The gates from `photometry_run_gates` on inputs that pass them all.
    """
    return photometry_run_gates(
        frames_contributed=80,
        rejected_frame_count=2,
        frames_without_timestamp=0,
        session_count=2,
        session_empty_reasons=[],
        sessions_missing_wcs=[],
        no_work_reason=None,
        comparison_sets=FIXTURE_COMPARISON_SETS,
        registration_drifts_px=[3.0, 5.0, None],
        centroid_shift_summaries=FIXTURE_CENTROID_SUMMARIES,
        stars_with_scatter=80,
        known_variable_scores=[3.0 + 0.01 * index for index in range(20)],
        unlisted_scores=[0.05 + 0.001 * index for index in range(60)],
        cutoff_cv=0.04,
        noise_model_stars=60,
        noise_floor_mag=0.01,
        median_flux_error_mag=0.02,
    )


def photometry_emitted_measurements() -> list[QualityMeasurement]:
    """Run the photometry record step on a fixed, fully measured run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a healthy two-session run.
    """
    context = PipelineRunContext(
        "M 99", "photometry", "1.3.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
    )
    return photometry_run_measurements(
        photometry_fixture_metrics(), photometry_fixture_gates(), photometry_fixture_inputs(), context
    )
