"""A fixed stacking run for the metric conformance test.

The conformance test (`pipelines/test/test_metric_conformance.py`) needs every
pipeline that records measurements to show that its record step emits each
metric the pipeline declares. This file builds one finished stack summary whose
every number is filled, with two exposure groups, all sixteen gates, a noise
measurement and two member frames, and runs the stacking record step on it.
It adds the two rows of one leave-one-night-out stack and the nine rows of the
half-difference noise, which the subset jobs store (`subset_noise_run.py`).
The two quarantine metrics are frame metrics that the frame measurement run
(`frame_measurement_run.py`) stores, so the fixture adds the rows it would add.
"""

import math
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
from astrometricslib.pipelines.stacking.post_processing.expected_noise import (
    ExpectedNoise,
    ExpectedNoiseOutcome,
)
from astrometricslib.pipelines.stacking.post_processing.flat_noise_contribution import (
    FlatNoiseContribution,
    FlatNoiseOutcome,
)
from astrometricslib.pipelines.stacking.post_processing.record_quality import (
    frame_geometry_measurements,
    stacking_measurements,
)
from astrometricslib.pipelines.stacking.post_processing.record_science import StackScience
from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
    StackMember,
    StackMembership,
)
from astrometricslib.pipelines.stacking.post_processing.stack_noise import (
    StackNoiseOutcome,
    StackNoiseResult,
)
from astrometricslib.pipelines.stacking.subset_noise_run import (
    expected_ratio_measurement,
    half_difference_measurements,
    night_measurements,
)

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


def fixture_science() -> StackScience:
    """Give the expected noise, flat noise and sky level, all filled.

    Returns
    -------
    science : `StackScience`
        An expected noise with its parts, a flat contribution and a sky level.
        The star peaks are not here: they sit on the exposure groups of
        `fixture_summary` (the second has a clipped star whose peak is
        estimated).
    """
    return StackScience(
        expected_noise=ExpectedNoiseOutcome(
            ExpectedNoise(
                sky_noise=0.0001,
                read_noise=0.00005,
                total_noise=0.000112,
                aperture_area_px=59.4,
                stack_units_per_adu=1.0 / 65535.0,
                frame_count=2,
                predicted_sky_level=0.006,
            )
        ),
        flat_noise=FlatNoiseOutcome(FlatNoiseContribution(0.004, 0.00003, 0.006)),
        stack_sky_level=0.006,
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
            brightestStarPeakFraction=0.4,
            brightestStarPeakAboveSkyAdu=24000.0,
            peakSkyAdu=500.0,
            peakClipCeilingAdu=65532.0,
            peakLinearityLimitAdu=60000.0,
            peakFramesWithPeak=3,
            peakFramesClipped=0,
            peakFramesSampled=3,
            peakFramesOnProfileCeiling=3,
            peakCheckedRangeRoomMultiples=80.0,
            peakClipCeilingProvenance="measured",
            peakLinearityLimitProvenance="measured",
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
            brightestStarPeakFraction=1.3,
            brightestStarPeakAboveSkyAdu=84000.0,
            peakSkyAdu=900.0,
            peakClipCeilingAdu=65532.0,
            peakLinearityLimitAdu=60000.0,
            peakFramesWithPeak=3,
            peakFramesEstimated=3,
            peakFramesClipped=3,
            peakFramesSampled=3,
            peakLargestEstimateToRoomRatio=1.4,
            peakCheckedRangeRoomMultiples=80.0,
            peakClipCeilingProvenance="measured",
            peakLinearityLimitProvenance="measured",
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


def fixture_frame_geometry_rows(context: PipelineRunContext) -> list[QualityMeasurement]:
    """Run the rotation step on two small frame records.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The rotation row of each of two frames. Siril's transform for the
        first is the identity and the second is turned by 0.004 degrees.
    """
    frames = [
        SimpleNamespace(
            path=f"/frames/lights/M 99/Scope/Cam/{name}",
            camera="Cam",
            exposure="60",
            timestamp=1_792_000_000.0,
        )
        for name in ("a.fits", "b.fits")
    ]
    turn = math.radians(0.004)
    identity = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    turned = [math.cos(turn), -math.sin(turn), 1.0, math.sin(turn), math.cos(turn), 0.0, 0.0, 0.0, 1.0]
    diagnostics = {
        "symlinked_light_paths": [frame.path for frame in frames],
        "registration_included": [True, True],
        "registration_frames": [
            {"dx": 0.0, "dy": 0.0, "star_count": 120, "homography": identity},
            {"dx": 1.0, "dy": 0.0, "star_count": 118, "homography": turned},
        ],
    }
    return frame_geometry_measurements(fixture_summary(), context, frames, diagnostics, "")


def stacking_emitted_measurements() -> list[QualityMeasurement]:
    """Run the stacking record step on a fixed, fully measured stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        What the record step emits, plus the two quarantine rows of one frame.
    """
    context = PipelineRunContext("M 99", "stacking", "1.3.0", "job-1", "job-1", FIXTURE_TIME)
    measurements = stacking_measurements(
        fixture_summary(),
        context,
        "M99_Stacked.fits",
        fixture_noise_outcome(),
        fixture_membership(),
        science=fixture_science(),
    )
    frame = FrameRecord(path="/frames/lights/M 99/Scope/Cam/a.fits", camera="Cam", exposure="60")
    measurements.extend(fixture_frame_geometry_rows(context))
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
    measurements.append(
        expected_ratio_measurement(
            context,
            context.stack_subject(
                "_subsets/M99_L_Stacked.without-2026-10-01/M99_L_Stacked.without-2026-10-01.fits"
            ),
            None,
            SubsetStackResult(
                targetId="M 99", label="M99_L_Stacked.without-2026-10-01", effectiveFrameCount=9.0
            ),
            "",
        )
    )
    measurements.extend(
        half_difference_measurements(
            context,
            context.stack_subject("M99_L_Stacked.fits"),
            fixture_noise_outcome(),
            "",
            "",
            (9.0, 9.5),
            "",
            0.2,
            (0.1, 0.12),
            1.5,
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
