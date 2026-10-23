"""A fixed spectroscopy run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) asks each
pipeline that has reached phase 3 to emit every metric it declares. This
module builds one fixed, healthy run (its summary numbers, its roll-ups and
its nine gates) and runs the pipeline's record step on it.

`healthy_summary_metrics` and `healthy_gates` are also used by this folder's
own tests, so both read the same run.
"""

from datetime import UTC, datetime

from astrometricslib.models.quality.gaia_xp_comparison import GaiaXpBandSummary, GaiaXpRunSummary
from astrometricslib.models.quality.gate_result import GateResult, passed_gate
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import SpectroscopyPipelineQualityMetrics
from astrometricslib.models.quality.spectroscopy_quality import StageQualityRollup
from astrometricslib.models.wavelength_scale import WavelengthScaleSummary
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.spectroscopy.metrics import (
    GAIA_XP_BAND_EDGES_ANGSTROM,
    STAGE_METRIC_KEYS,
    STAGE_NAMES,
)
from astrometricslib.pipelines.spectroscopy.post_processing import record_quality
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import GAIA_XP_GATE_NAME

# When the fixed run measured.
MEASURED_AT = datetime(2026, 10, 22, 21, 0, tzinfo=UTC)


def healthy_summary_metrics() -> SpectroscopyPipelineQualityMetrics:
    """Build the summary numbers of a healthy run.

    Returns
    -------
    metrics : `SpectroscopyPipelineQualityMetrics`
        Every field filled: totals, identification counts, a median for
        every checkpoint metric, the Gaia XP bands and the wavelength scale.
    """
    rollups = [
        StageQualityRollup(
            stage=stage,
            spectrum_count=4,
            failed_spectrum_count=0,
            metric_medians={key: 1.5 for rollup_stage, key in STAGE_METRIC_KEYS if rollup_stage == stage},
        )
        for stage in STAGE_NAMES
    ]
    bands = [
        GaiaXpBandSummary(
            start_angstrom=float(start),
            end_angstrom=float(end),
            median_ratio=1.02,
            scatter=0.03,
            star_count=4,
        )
        for start, end in GAIA_XP_BAND_EDGES_ANGSTROM
    ]
    return SpectroscopyPipelineQualityMetrics(
        zero_order_saturated_pixel_fraction=0.0,
        dispersion_angle_deg=-1.5,
        median_trail_width_px=3.1,
        catalog_matched_star_count=4,
        position_only_star_count=1,
        unresolved_star_count=0,
        poor_match_classification_count=0,
        ambiguous_classification_count=1,
        stage_quality_summary=rollups,
        gaia_xp_summary=GaiaXpRunSummary(
            compared_star_count=4,
            bands=bands,
            median_slope_percent_per_1000_angstrom=0.4,
            median_absolute_slope_percent_per_1000_angstrom=0.9,
        ),
        wavelength_scale_summary=WavelengthScaleSummary(
            spectrum_count=4,
            applied_count=3,
            rms_before_correction_angstrom=6.0,
            rms_after_correction_angstrom=1.5,
            offset_saturation_correlation=0.1,
            correlation_spectrum_count=4,
        ),
    )


def healthy_gates() -> list[GateResult]:
    """Build the nine gates of a healthy run.

    Returns
    -------
    gates : `list` [`GateResult`]
        The eight gates of `spectroscopy_run_gates` and the Gaia XP gate, all
        passed.
    """
    scale = healthy_summary_metrics().wavelength_scale_summary
    facts = {
        "spectra": 4,
        "classified": 4,
        "subtype_ambiguous": 0,
        "catalog_compared": 4,
        "catalog_disagree": 0,
        "feature_stars_tested": 4,
        "feature_stars_uncalibrated": 0,
        "resolution_measured": 4,
        "processing_checked": 4,
        "processing_failed": 0,
    }
    return [
        *record_quality.spectroscopy_run_gates(facts, [0.0, 0.0], [], scale),
        passed_gate(GAIA_XP_GATE_NAME, 0.9, 3.0, "median absolute tilt of observed / Gaia XP", "4 stars"),
    ]


def run_context(job_id: str | None = "job-1") -> PipelineRunContext:
    """Build the shared facts of the fixed run.

    Parameters
    ----------
    job_id : `str` or `None`, optional
        The job the run belongs to.

    Returns
    -------
    context : `PipelineRunContext`
        A context for target ``M 99``.
    """
    return PipelineRunContext("M 99", "spectroscopy", "1.6.0", job_id or "run-fixed", job_id, MEASURED_AT)


def spectroscopy_emitted_measurements() -> list[QualityMeasurement]:
    """Run the spectroscopy pipeline's record step on the fixed run.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits for the fixed healthy run.
    """
    return record_quality.spectroscopy_run_measurements(
        healthy_summary_metrics(), healthy_gates(), run_context()
    )
