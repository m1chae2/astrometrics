"""Tests for notice rows: the warnings that are not gates.

A notice row has the check outcome ``failed`` and a limit of zero, carries
the job id of its run, and holds the sentence that says what was noticed.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.models.quality.gate_result import GateStatus
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.quality_summary import PipelineQualitySummaryBase
from astrometricslib.pipelines.shared.applied_camera_profile import record_camera_profile
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.notices import (
    NOTICE_REGISTER_ENTRY,
    camera_without_profile_measurement,
    camera_without_profile_metric_name,
    camera_without_profile_reason,
    notice_limit,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.notice_reasons import (
    exposure_group_left_out_reason,
    frames_moved_to_excluded_reason,
    single_frame_stack_reason,
)

MEASURED_AT = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
PIPELINES = ("stacking", "astrometry", "photometry", "spectroscopy", "asteroid_detection")


def _context(pipeline: str = "photometry", job_id: str | None = "job-9") -> PipelineRunContext:
    """Build the shared facts of a run.

    Returns
    -------
    context : `PipelineRunContext`
        A context for target M 31.
    """
    return PipelineRunContext.from_options(
        "M 31", pipeline, "1", {"job_id": job_id, "measured_at": MEASURED_AT}
    )


def test_the_notice_limit_is_zero_and_cites_the_register() -> None:
    """Any occurrence is reported, so the limit is zero."""
    limit = notice_limit("count")
    assert limit.value == pytest.approx(0.0)
    assert limit.register_entry == NOTICE_REGISTER_ENTRY


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_every_pipeline_declares_its_camera_notice(pipeline: str) -> None:
    """The metric of each pipeline is registered as a decision metric."""
    spec = get_metric_registry().get(camera_without_profile_metric_name(pipeline))
    assert spec.purpose.value == "decision"
    assert spec.checkpoint.value == pipeline


def test_the_camera_notice_is_a_failed_check_with_the_job_and_sentence() -> None:
    """The row is a warning of its job, with the camera sentence."""
    context = _context()
    row = camera_without_profile_measurement("Acme", context, context.run_subject())
    assert row.metric == "photometry.notice_camera_without_profile"
    assert row.check_outcome == GateStatus.FAILED
    assert row.check_limit.value == pytest.approx(0.0)
    assert row.job_id == "job-9"
    assert row.reason == camera_without_profile_reason("Acme")
    assert row.subject.level is SubjectLevel.RUN


def test_recording_an_unlisted_camera_stores_the_notice_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stored row keeps the sentence the summary used to flag."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.QualityStore.from_configuration",
        lambda config=None: QualityStore(str(tmp_path / "astrometrics.db")),
    )
    summary = PipelineQualitySummaryBase(pipeline_name="photometry", pipeline_version="1", target_id="M 31")
    record_camera_profile(summary, "Acme Imager 9000", _context())
    rows = QualityStore(str(tmp_path / "astrometrics.db")).query(job_id="job-9", latest_only=False)
    assert [row.reason for row in rows] == [
        "camera 'Acme Imager 9000' has no camera profile; generic assumptions about the sensor were used"
    ]
    assert summary.camera_profile is not None
    assert summary.camera_profile.is_generic_fallback


def test_a_listed_camera_stores_no_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A recognised camera raises nothing."""
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.QualityStore.from_configuration",
        lambda config=None: QualityStore(str(tmp_path / "astrometrics.db")),
    )
    summary = PipelineQualitySummaryBase(pipeline_name="photometry", pipeline_version="1", target_id="M 31")
    record_camera_profile(summary, "ZWO ASI 533MM Pro", _context())
    assert QualityStore(str(tmp_path / "astrometrics.db")).query(latest_only=False) == []


def test_stacking_notice_sentences_are_the_ones_stored_before_the_flag_was_removed() -> None:
    """The stored reason text of each stacking notice is unchanged."""
    assert frames_moved_to_excluded_reason(3) == (
        "3 frame(s) with clouds or trailed stars moved to the _excluded folder"
    )
    assert (
        single_frame_stack_reason()
        == "single-frame stack: no rejection/registration quality processing applied"
    )
    assert exposure_group_left_out_reason(60.0, "was left out") == "the 60 s exposure group was left out"


def test_stacking_notice_rows_follow_the_summary() -> None:
    """Give a row for each notice that happened."""
    from astrometricslib.models.quality.quality_summary import (
        ExposureGroupSummary,
        StackingPipelineQualityMetrics,
        StackQualitySummary,
    )
    from astrometricslib.pipelines.stacking.post_processing.record_quality import (
        stacking_notice_measurements,
    )

    summary = StackQualitySummary(
        targetId="M 31",
        stackingMetrics=StackingPipelineQualityMetrics(isSpectral=False, framesSubmitted=1, framesStacked=1),
    )
    summary.stacking_metrics.frames_accepted = 6
    summary.stacking_metrics.frames_quarantined = 2
    summary.stacking_metrics.flat_calibration_issues = [
        "flats are faint: 5% of full scale",
        "flats are bright: 95% of full scale",
        "not smoothed: blurring a colour sensor's flat would mix its colours",
    ]
    summary.quality_processing_applied = False
    summary.stacking_metrics.exposure_groups = [
        ExposureGroupSummary(
            exposureSeconds=30.0,
            framesSubmitted=6,
            framesStacked=0,
            darkApplied=True,
            leftOutReason="was left out because it has no dark frames",
        ),
        ExposureGroupSummary(
            exposureSeconds=60.0,
            framesSubmitted=6,
            framesStacked=0,
            darkApplied=True,
            gainNonlinear=True,
            leftOutReason="gain ratios disagree",
        ),
    ]
    record_camera_profile(summary, "Acme Imager 9000")
    rows = stacking_notice_measurements(summary, _context("stacking"), "M31_Stacked.fits")
    assert sorted(row.metric for row in rows) == [
        "stacking.notice_camera_without_profile",
        "stacking.notice_exposure_group_left_out",
        "stacking.notice_flat_calibration_note",
        "stacking.notice_frames_moved_to_excluded",
        "stacking.notice_single_frame_stack",
    ]
    assert all(row.check_outcome == GateStatus.FAILED and row.job_id == "job-9" for row in rows)
    assert {row.reason for row in rows} == {
        frames_moved_to_excluded_reason(2),
        single_frame_stack_reason(),
        exposure_group_left_out_reason(30.0, "was left out because it has no dark frames"),
        "flat calibration: flats are bright: 95% of full scale; "
        "flat calibration: not smoothed: blurring a colour sensor's flat would mix its colours",
        "camera 'Acme Imager 9000' has no camera profile; generic assumptions about the sensor were used",
    }


def test_no_notice_when_nothing_happened() -> None:
    """A normal stack stores no notice rows."""
    from astrometricslib.models.quality.quality_summary import (
        StackingPipelineQualityMetrics,
        StackQualitySummary,
    )
    from astrometricslib.pipelines.stacking.post_processing.record_quality import (
        stacking_notice_measurements,
    )

    summary = StackQualitySummary(
        targetId="M 31",
        stackingMetrics=StackingPipelineQualityMetrics(isSpectral=False, framesSubmitted=1, framesStacked=1),
    )
    assert stacking_notice_measurements(summary, _context("stacking"), "M31_Stacked.fits") == []


def test_the_timeout_row_is_a_failed_check_of_the_stacking_job() -> None:
    """The abandoned-stack row has the budget as limit and the job's id."""
    from astrometricslib.pipelines.stacking.post_processing.record_quality import (
        abandoned_stack_measurements,
    )

    context = _context("stacking", job_id="job-stack")
    (row,) = abandoned_stack_measurements(context, 612.4, 600)
    assert row.metric == "stacking.abandoned_after_s"
    assert row.check_outcome == GateStatus.FAILED
    assert row.check_limit.value == pytest.approx(600.0)
    assert row.reason == "stacking timed out after 600s"
    assert row.job_id == "job-stack"
