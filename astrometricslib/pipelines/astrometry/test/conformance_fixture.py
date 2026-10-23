"""A fixed astrometry run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) needs every
pipeline that records measurements to show that its record step emits each
metric the pipeline declares. This file runs the astrometry record step on one
solved run whose every value is filled, so no metric is left out for being
missing.
"""

from datetime import UTC, datetime

from astrometricslib.models.quality.gate_result import passed_gate
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import AstrometryPipelineQualityMetrics
from astrometricslib.pipelines.astrometry.post_processing.record_quality import (
    PLATE_SOLVE_GATE_NAME,
    astrometry_run_gates,
    astrometry_run_measurements,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext


def astrometry_emitted_measurements() -> list[QualityMeasurement]:
    """Run the astrometry record step on a fixed, fully measured run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for a solved run with a Gaia refit and
        a match table.
    """
    metrics = AstrometryPipelineQualityMetrics(
        sources_detected=80,
        solve_attempted=True,
        plate_solve_succeeded=True,
        simbad_matched_count=40,
        catalog_matched_star_count=60,
        position_only_star_count=15,
        unresolved_star_count=5,
        catalog_match_separation_rms_arcsec=1.2,
        plate_solve_fit_residual_rms_arcsec=0.8,
        plate_solve_matched_star_count=70,
        gaia_fit_rms_arcsec=0.4,
        gaia_fit_rms_before_arcsec=0.9,
        gaia_fit_star_count=55,
        wcs_refinement_shift_arcsec=1.5,
        plate_scale_arcsec_per_pixel=1.9,
        star_fwhm_px=3.0,
        remote_catalog_queries_attempted=10,
        remote_catalog_queries_failed=1,
        plate_solve_attempts=1,
    )
    gates = [passed_gate(PLATE_SOLVE_GATE_NAME, detail="the image was solved to sky coordinates")]
    gates.extend(astrometry_run_gates(metrics))
    context = PipelineRunContext(
        "M 99", "astrometry", "1.4.0", "job-1", "job-1", datetime(2026, 10, 22, tzinfo=UTC)
    )
    return astrometry_run_measurements(metrics, gates, context, "M99_Stacked.fits")
