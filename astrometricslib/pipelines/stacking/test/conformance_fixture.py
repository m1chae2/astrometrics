"""A fixed stacking run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) needs every
pipeline that records measurements to show that its record step emits each
metric the pipeline declares. This file builds one finished stack summary whose
every number is filled, with two exposure groups, all sixteen gates, a noise
measurement and two member frames, and runs the stacking record step on it.
It adds the two rows of one leave-one-night-out stack, which the subset jobs
store (`subset_noise_run.py`).
The two quarantine metrics are frame metrics that the frame measurement run
(`frame_measurement_run.py`) stores, so the fixture adds the rows it would add.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

from astrometricslib.models.quality.gate_result import passed_gate
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.quality_summary import (
    ExposureGroupSummary,
    StackingPipelineQualityMetrics,
    StackQualitySummary,
)
from astrometricslib.models.quality.stacking_quality import StackingInputQuality
from astrometricslib.models.quality.subset_stacks import SubsetStackResult
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.frame_measurement_run import quarantine_measurements_for_frame
from astrometricslib.pipelines.stacking.metrics import GATE_METRIC_BY_NAME
from astrometricslib.pipelines.stacking.post_processing.record_quality import stacking_measurements
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMember,
    StackMembership,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    StackNoiseOutcome,
    StackNoiseResult,
)
from astrometricslib.pipelines.stacking.subset_noise_run import night_measurements

FIXTURE_TIME = datetime(2026, 10, 22, tzinfo=UTC)


def fixture_noise_outcome() -> StackNoiseOutcome:
    """Give a noise measurement that has every number filled.

    Returns
    -------
    outcome : `StackNoiseOutcome`
        A result with 100 apertures and 10 reference stars.
    """
    return StackNoiseOutcome(
        StackNoiseResult(
            noise_adu=0.002,
            noise_uncertainty_adu=0.00014,
            aperture_count=100,
            reference_flux_adu=0.4,
            reference_uncertainty_adu=0.01,
            reference_star_count=10,
            normalized_noise=0.005,
            normalized_uncertainty=0.0004,
            wcs_source="stack header",
            apertures_created=False,
        )
    )


def fixture_summary() -> StackQualitySummary:
    """Build a finished stack summary with every number and gate filled.

    Returns
    -------
    summary : `StackQualitySummary`
        A stack of two exposure groups.
    """
    groups = [
        ExposureGroupSummary(
            exposureSeconds=60.0,
            framesSubmitted=10,
            framesStacked=10,
            darkApplied=True,
            stackPath="/stacks/lights/M 99/M99_Stacked.fits",
        ),
        ExposureGroupSummary(
            exposureSeconds=120.0,
            framesSubmitted=8,
            framesStacked=8,
            darkApplied=True,
            stackPath="/stacks/lights/M 99/groups/M99_Stacked_exp120s.fits",
            alignmentShiftPixels=[3.0, 4.0],
            gainMidRange=2.0,
            gainBrightEndRatio=2.02,
            gainDisagreement=0.01,
        ),
    ]
    metrics = StackingPipelineQualityMetrics(
        isSpectral=False,
        framesSubmitted=20,
        framesStacked=18,
        rejectedPixelFraction=0.02,
        flatFrameCount=30,
        flatNoiseFraction=0.004,
        flatSmoothingSigmaPx=0.0,
        saturatedPixelFraction=0.0001,
        exposureGroups=groups,
        zeroPixelFraction=0.0,
        negativePixelMaxPercent=12,
        stackedFwhmPx=3.1,
        medianInputFwhmPx=3.0,
        expectedStackFwhmPx=3.05,
        stackingDurationSeconds=95.0,
        registrationReferenceStarCount=150,
    )
    summary = StackQualitySummary(
        targetId="M 99",
        stackingMetrics=metrics,
        inputQuality=StackingInputQuality(
            framesSubmitted=20,
            framesAccepted=18,
            framesExcludedForGain=1,
            framesExcludedForBackground=0,
            framesQuarantined=1,
        ),
    )
    for gate_name in GATE_METRIC_BY_NAME:
        summary.record_gate(passed_gate(gate_name, 0.01, 0.1, "fixture limit"))
    return summary


def fixture_membership() -> StackMembership:
    """Give a stack of two member frames with known weights.

    Returns
    -------
    membership : `StackMembership`
        Two frames of 60 s and 120 s, the second weighted twice as much.
    """
    frames = [
        SimpleNamespace(
            path="/frames/lights/M 99/Scope/Cam/a.fits", exposure="60", timestamp=1_792_000_000.0
        ),
        SimpleNamespace(
            path="/frames/lights/M 99/Scope/Cam/b.fits", exposure="120", timestamp=1_792_000_100.0
        ),
    ]
    return StackMembership((StackMember(frames[0], 1.0), StackMember(frames[1], 2.0)))


def stacking_emitted_measurements() -> list[QualityMeasurement]:
    """Run the stacking record step on a fixed, fully measured stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits, plus the two quarantine rows of one frame.
    """
    context = PipelineRunContext("M 99", "stacking", "1.3.0", "job-1", "job-1", FIXTURE_TIME)
    measurements = stacking_measurements(
        fixture_summary(), context, "M99_Stacked.fits", fixture_noise_outcome(), fixture_membership()
    )
    frame = FrameRecord(path="/frames/lights/M 99/Scope/Cam/a.fits", camera="Cam", exposure="60")
    measurements.extend(
        night_measurements(
            context,
            "M99_L_Stacked.without-2026-10-01",
            "2026-10-01",
            0.4,
            "",
            SubsetStackResult(targetId="M 99", label="M99_L_Stacked.without-2026-10-01"),
            fixture_noise_outcome(),
        )
    )
    measurements.extend(
        quarantine_measurements_for_frame(
            {"roundness": 0.9, "sky_median_adu": 500.0},
            frame,
            "M 99",
            run_id="job-1",
            job_id="job-1",
            measured_at=FIXTURE_TIME,
            capture_setting_id=None,
        )
    )
    return measurements
