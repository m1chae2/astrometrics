"""Tests that the stacking stage runs the quarantine step and uses its result.

The quarantine step itself is tested in `test_frame_quarantine.py`. These
tests check the wiring: the stage hands the step the frames, stacks only the
frames it keeps, and records the moved ones as excluded.
"""

from unittest.mock import MagicMock, patch

import pytest

from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.gate_result import GateStatus, failed_gate
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stage as stacking_tasks
from astrometricslib.pipelines.stacking.pre_processing.assess_input_quality import (
    QUARANTINE_EXCLUSION_REASON_PREFIX,
)
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    QuarantineDecision,
    QuarantineReport,
)

SIRIL_DRIVER = "astrometricslib.drivers.siril_interface.ImageProcessing"
SUMMARY_BUILDER = "astrometricslib.pipelines.stacking.stage._build_stack_quality_summary"
QUARANTINE_STEP = "astrometricslib.pipelines.stacking.pre_processing.frame_quarantine.quarantine_bad_frames"


def make_target(frame_count: int = 10) -> Target:
    """Build a target with luminance frames that exist only as records.

    Returns
    -------
    target : `Target`
        A target holding `frame_count` frames.
    """
    return Target(
        id="M 52",
        frames=[
            FrameRecord(
                path=f"/fake/l_{index}.fits",
                filter=FilterType.L,
                camera="ZWO ASI 533MM Pro",
                exposure="120.0",
                timestamp=float(index) * 120.0,
            )
            for index in range(frame_count)
        ],
    )


def run_stage(target: Target) -> tuple[MagicMock, MagicMock]:
    """Run `stack_frames` with the stacker and the summary builder stubbed.

    Returns
    -------
    driver : `MagicMock`
        The stand-in stacking driver.
    summary_builder : `MagicMock`
        The stand-in summary builder, whose call shows the excluded frames.
    """
    dummy_summary = StackQualitySummary(
        pipeline_name="stacking",
        pipeline_version="1.0.0",
        target_id=target.id,
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=10, frames_stacked=9
        ),
    )
    with patch(SIRIL_DRIVER) as driver_class, patch(SUMMARY_BUILDER) as summary_builder:
        driver = MagicMock()
        driver.last_run_diagnostics = {"corrupt_frames_skipped": []}
        driver.process_target.return_value = "/library/lights/M_52/M_52_L_Stacked.fits"
        driver_class.return_value = driver
        summary_builder.return_value = dummy_summary
        stacking_tasks.stack_frames(target, filter_type=FilterType.L)
    return driver, summary_builder


def test_stage_stacks_only_the_frames_the_quarantine_step_keeps() -> None:
    """A moved frame is left out of the stack and listed as excluded."""
    target = make_target()
    moved_path = target.frames[9].path
    report = QuarantineReport(
        moved=[
            QuarantineDecision(
                moved_path, "clouded", "203 stars against a typical 3348", 203, 3348.0, 0.88, 5000.0
            )
        ]
    )

    def fake_quarantine(
        target_argument: Target, frames: list[FrameRecord]
    ) -> tuple[list[FrameRecord], QuarantineReport]:
        return [frame for frame in frames if frame.path != moved_path], report

    with patch(QUARANTINE_STEP, side_effect=fake_quarantine) as quarantine_step:
        driver, summary_builder = run_stage(target)

    quarantine_step.assert_called_once()
    stacked_frames = driver.process_target.call_args.kwargs["image_files"]
    stacked_paths = [frame["path"] if isinstance(frame, dict) else frame.path for frame in stacked_frames]
    assert moved_path not in stacked_paths
    assert len(stacked_frames) == 9
    excluded = summary_builder.call_args.kwargs["excluded_frames"]
    assert [frame.path for frame in excluded] == [moved_path]
    assert excluded[0].reason.startswith(QUARANTINE_EXCLUSION_REASON_PREFIX)


def test_stage_skips_the_quarantine_step_when_the_setting_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the setting off, the stage never calls the step."""
    from astrometricslib.foundation.config import get_configuration

    configuration = get_configuration()
    monkeypatch.setattr(configuration, "get_quarantine_bad_frames_enabled", lambda: False)

    with patch(QUARANTINE_STEP) as quarantine_step:
        run_stage(make_target())

    quarantine_step.assert_not_called()


def test_stage_hands_the_quarantine_gate_to_the_summary_builder() -> None:
    """A clean, judged session reaches the summary as a passed gate."""
    report = QuarantineReport(batches_judged=1)
    with patch(QUARANTINE_STEP, side_effect=lambda target, frames: (list(frames), report)):
        _driver, summary_builder = run_stage(make_target())

    gates = {gate.name: gate for gate in summary_builder.call_args.kwargs["gate_results"]}
    assert gates["frame_quarantine"].status is GateStatus.PASSED


def test_stage_records_a_switched_off_quarantine_as_not_checked(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the setting off, the summary says the step did not run."""
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_quarantine_bad_frames_enabled", lambda: False)

    _driver, summary_builder = run_stage(make_target())

    gates = {gate.name: gate for gate in summary_builder.call_args.kwargs["gate_results"]}
    assert gates["frame_quarantine"].status is GateStatus.NOT_CHECKED


def test_a_failed_quarantine_gate_survives_the_flag_rebuild() -> None:
    """The real builder keeps a failed gate's reason after the flag rebuild."""
    summary = stacking_tasks._build_stack_quality_summary(
        target=make_target(),
        is_spectral=False,
        frames_submitted=10,
        target_frames=make_target().frames,
        excluded_frames=[],
        diagnostics={},
        background_split=None,
        stacked_path=None,
        gate_results=[failed_gate("frame_quarantine", "too many bad frames; still in the stack")],
    )

    assert summary.flagged is True
    assert "too many bad frames; still in the stack" in summary.flag_reasons
    assert summary.gate("frame_quarantine").status is GateStatus.FAILED
