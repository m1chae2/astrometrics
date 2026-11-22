"""Purpose: Unit tests for the analysis stage prerequisites.

Description: Astrometry is a hard prerequisite of photometry and
spectroscopy. These tests check the `STAGE_PREREQUISITES` table, the rule
`stage_result_is_current` uses to tell whether a saved astrometry result still
matches the imaging stack, and how `process_target` adds a missing or old
astrometry run, skips the stages that need it when it cannot run, and leaves
the run alone with `run_prerequisites=False`. The stage runners are replaced
by a recorder, so no image is read or solved.
"""

import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, ProcessingError
from astrometricslib.models.quality.quality_summary import AstrometryQualitySummary, StackQualitySummary
from astrometricslib.models.target import Target
from astrometricslib.pipelines import tasks
from astrometricslib.pipelines.shared.provenance_recording import stacked_image_entity_id

_NOW = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)


class _RecordingPipelineMatch:
    """A stand-in for the stage dispatch that records every call."""

    def __init__(self) -> None:
        """Start with no calls and no failure."""
        self.calls: list[dict] = []
        self.failures: dict[str, Exception] = {}
        self.results: dict[str, dict] = {}

    def __call__(
        self,
        target: object,
        frames: object,
        pipeline_type: str,
        filter_type: object,
        catalog_access: object,
        path: object,
        **kwargs: object,
    ) -> dict:
        """Record the call, raise a chosen failure, or return a stub result.

        Returns
        -------
        result : `dict`
            The result chosen for the stage, or a plain finished marker.
        """
        self.calls.append({"pipeline_type": pipeline_type, "path": path, **kwargs})
        if pipeline_type in self.failures:
            raise self.failures[pipeline_type]
        return self.results.get(pipeline_type, {"status": "finished", "pipeline_type": pipeline_type})

    @property
    def stages(self) -> list[str]:
        """The stages called, in order.

        Returns
        -------
        stages : `list` [`str`]
            The pipeline types.
        """
        return [call["pipeline_type"] for call in self.calls]


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _RecordingPipelineMatch:
    """Replace the stage dispatch with a recorder.

    Returns
    -------
    recorder : `_RecordingPipelineMatch`
        The recorder.
    """
    recording = _RecordingPipelineMatch()
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recording)
    return recording


@pytest.fixture
def pipelines() -> ProcessingPipelines:
    """Build the pipelines on an in-memory configuration.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        The pipelines.
    """
    return ProcessingPipelines(AppConfiguration(), MagicMock())


def _stack_file(tmp_path: Path) -> str:
    """Make an empty file that stands in for an imaging stack.

    Returns
    -------
    path : `str`
        The file's path.
    """
    path = tmp_path / "stack.fits"
    path.write_bytes(b"")
    return str(path)


def _astrometry_summary(
    created_at: datetime, upstream_entity_id: str | None = None
) -> AstrometryQualitySummary:
    """Build an astrometry summary with only the fields the rule reads.

    Returns
    -------
    summary : `AstrometryQualitySummary`
        The summary.
    """
    return AstrometryQualitySummary.model_construct(
        created_at=created_at, upstream_entity_id=upstream_entity_id
    )


def _target_with_astrometry(
    stack_path: str,
    astrometry_at: datetime | None,
    stack_at: datetime | None = _NOW,
    upstream_entity_id: str | None = None,
    spectral: bool = False,
) -> Target:
    """Build a target with a stack and, optionally, a saved astrometry result.

    Returns
    -------
    target : `Target`
        The target.
    """
    target = Target(
        id="PrerequisiteTarget",
        stacked_image=stack_path,
        stacked_spectral_target="/fake/spec.fits" if spectral else "",
    )
    if stack_at is not None:
        target.stacking.quality_summary = StackQualitySummary.model_construct(created_at=stack_at)
    if astrometry_at is not None:
        target.quality.astrometry = _astrometry_summary(astrometry_at, upstream_entity_id)
    return target


def test_the_table_names_astrometry_as_the_prerequisite_of_photometry_and_spectroscopy() -> None:
    """Verify the table has every stage and the agreed prerequisites."""
    assert set(tasks.STAGE_PREREQUISITES) == set(tasks.ANALYSIS_STAGES)
    assert tasks.STAGE_PREREQUISITES["photometry"] == ("astrometry",)
    assert tasks.STAGE_PREREQUISITES["spectroscopy"] == ("astrometry",)
    assert tasks.STAGE_PREREQUISITES["astrometry"] == ()
    assert tasks.STAGE_PREREQUISITES["asteroids"] == ()


def test_a_missing_result_is_not_current(tmp_path: Path) -> None:
    """Verify a target with no astrometry result needs astrometry."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=None)

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_result_older_than_the_stack_is_not_current(tmp_path: Path) -> None:
    """Verify a result made before the stack finished is stale."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=_NOW - timedelta(minutes=1))

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_result_made_after_the_stack_is_current(tmp_path: Path) -> None:
    """Verify a newer result, with no recorded stack, is current."""
    target = _target_with_astrometry(_stack_file(tmp_path), astrometry_at=_NOW + timedelta(minutes=1))

    assert tasks.stage_result_is_current(target, "astrometry")


def test_a_result_that_names_this_stack_is_current(tmp_path: Path) -> None:
    """Verify a recorded stack that matches the current path is current."""
    stack_path = _stack_file(tmp_path)
    entity_id = stacked_image_entity_id("PrerequisiteTarget", stack_path)
    target = _target_with_astrometry(stack_path, _NOW + timedelta(minutes=1), upstream_entity_id=entity_id)

    assert tasks.stage_result_is_current(target, "astrometry")


def test_a_result_that_names_another_stack_is_not_current(tmp_path: Path) -> None:
    """Verify a recorded stack with a different path is stale."""
    other_id = stacked_image_entity_id("PrerequisiteTarget", "/somewhere/else.fits")
    target = _target_with_astrometry(
        _stack_file(tmp_path), _NOW + timedelta(minutes=1), upstream_entity_id=other_id
    )

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_without_a_stack_summary_the_file_time_decides(tmp_path: Path) -> None:
    """Verify the stack file's modification time stands in for its summary."""
    stack_path = _stack_file(tmp_path)
    stack_time = datetime.fromtimestamp(os.path.getmtime(stack_path), UTC)
    older = _target_with_astrometry(stack_path, stack_time - timedelta(hours=1), stack_at=None)
    newer = _target_with_astrometry(stack_path, stack_time + timedelta(hours=1), stack_at=None)

    assert not tasks.stage_result_is_current(older, "astrometry")
    assert tasks.stage_result_is_current(newer, "astrometry")


def test_without_a_stack_on_disk_nothing_is_current() -> None:
    """Verify a result cannot be current when the stack file is gone."""
    target = _target_with_astrometry("/fake/missing.fits", _NOW + timedelta(hours=1))

    assert not tasks.stage_result_is_current(target, "astrometry")


def test_a_caller_given_image_is_never_current(tmp_path: Path) -> None:
    """Verify a result is never taken as made on an image the caller names."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    assert not tasks.stage_result_is_current(target, "astrometry", image_path="/chosen/image.fits")


def test_only_astrometry_has_a_staleness_rule(tmp_path: Path) -> None:
    """Verify asking about another stage is an error."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    with pytest.raises(InvalidArgumentError, match="No staleness rule"):
        tasks.stage_result_is_current(target, "photometry")


def test_a_stale_result_adds_astrometry_before_photometry(
    pipelines: ProcessingPipelines,
    recorder: _RecordingPipelineMatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify a stale result reruns astrometry first and logs why."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW - timedelta(hours=1))

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = pipelines.process_target(target, stages=["photometry"], register_job=False)

    assert any(
        "Running astrometry first: photometry needs it and its result is older than the stack" in message
        for message in caplog.messages
    )

    assert recorder.stages == ["astrometry", "photometry"]
    assert result.prerequisites_run == ["astrometry"]
    assert result.stages_run == ["astrometry", "photometry"]
    assert result.stages_skipped == {}


def test_a_current_result_is_not_rerun(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a current astrometry result leaves photometry to run alone."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    result = pipelines.process_target(target, stages=["photometry"], register_job=False)

    assert recorder.stages == ["photometry"]
    assert result.prerequisites_run == []


def test_spectroscopy_also_adds_astrometry(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify spectroscopy needs astrometry as photometry does."""
    target = _target_with_astrometry(_stack_file(tmp_path), None, spectral=True)

    result = pipelines.process_target(target, stages=["spectroscopy"], register_job=False)

    assert recorder.stages == ["astrometry", "spectroscopy"]
    assert result.prerequisites_run == ["astrometry"]


def test_a_requested_astrometry_is_not_counted_as_a_prerequisite(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify astrometry the caller asked for runs once, as a stage."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = pipelines.process_target(target, stages=["astrometry", "photometry"], register_job=False)

    assert recorder.stages == ["astrometry", "photometry"]
    assert result.prerequisites_run == []


def test_spectroscopy_with_no_spectral_data_adds_no_astrometry(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a stage that has nothing to run does not trigger astrometry."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = pipelines.process_target(target, stages=["spectroscopy"], register_job=False)

    assert recorder.calls == []
    assert result.results["spectroscopy"]["status"] == "skipped"
    assert result.prerequisites_run == []


def test_no_stack_and_no_image_skips_the_dependent_stage(
    pipelines: ProcessingPipelines,
    recorder: _RecordingPipelineMatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Verify photometry is skipped with a logged reason."""
    target = Target(id="NoStackTarget")

    with caplog.at_level(logging.INFO, logger=tasks.logger.name):
        result = pipelines.process_target(target, stages=["photometry"], register_job=False)

    assert any(
        "Running astrometry first: photometry needs it and its result is missing" in m
        for m in caplog.messages
    )
    assert any("Skipping photometry: needs astrometry" in m for m in caplog.messages)

    assert recorder.calls == []
    assert result.stages_run == []
    assert result.stages_skipped["photometry"].startswith("needs astrometry: ")
    assert result.results["photometry"]["status"] == "skipped"


def test_a_caller_given_image_stands_in_for_the_missing_stack(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify astrometry runs on the named image when there is no stack."""
    image_path = _stack_file(tmp_path)
    target = Target(id="NoStackTarget")

    result = pipelines.process_target(
        target, stages=["photometry"], astrometry={"path": image_path}, register_job=False
    )

    assert recorder.stages == ["astrometry", "photometry"]
    assert recorder.calls[0]["path"] == image_path
    assert result.prerequisites_run == ["astrometry"]


def test_a_caller_given_image_that_is_missing_skips_the_dependent_stage(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch
) -> None:
    """Verify a named image that is not on disk blocks the dependent stage."""
    target = Target(id="NoStackTarget")

    result = pipelines.process_target(
        target, stages=["photometry"], astrometry={"path": "/gone/image.fits"}, register_job=False
    )

    assert recorder.calls == []
    assert "does not exist" in result.stages_skipped["photometry"]


def test_a_caller_given_image_is_used_even_when_a_stack_exists_and_is_current(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify an explicit image always reruns astrometry."""
    target = _target_with_astrometry(_stack_file(tmp_path), _NOW + timedelta(hours=1))

    pipelines.process_target(
        target,
        stages=["photometry"],
        astrometry={"path": _stack_file(tmp_path)},
        register_job=False,
    )

    assert recorder.stages == ["astrometry", "photometry"]


def test_a_failed_prerequisite_skips_the_dependent_stage(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify an astrometry failure is reported and photometry does not run."""
    recorder.failures["astrometry"] = ProcessingError("no stars found")
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = pipelines.process_target(target, stages=["photometry"], register_job=False)

    assert recorder.stages == ["astrometry"]
    assert result.stages_run == []
    assert "no stars found" in result.stages_skipped["photometry"]


def test_a_prerequisite_with_no_solution_skips_the_dependent_stage(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify a stack that could not be solved blocks photometry."""
    recorder.results["astrometry"] = {"wcs": None}
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = pipelines.process_target(target, stages=["photometry"], register_job=False)

    assert recorder.stages == ["astrometry"]
    assert "could not be plate solved" in result.stages_skipped["photometry"]


def test_run_prerequisites_false_runs_only_the_requested_stages(
    pipelines: ProcessingPipelines, recorder: _RecordingPipelineMatch, tmp_path: Path
) -> None:
    """Verify the old behavior is available by asking for it."""
    target = _target_with_astrometry(_stack_file(tmp_path), None)

    result = pipelines.process_target(
        target, stages=["photometry"], run_prerequisites=False, register_job=False
    )

    assert recorder.stages == ["photometry"]
    assert result.prerequisites_run == []


def test_several_targets_refuse_run_prerequisites_false(pipelines: ProcessingPipelines) -> None:
    """Verify the full pipeline, which always solves first, refuses it."""
    with pytest.raises(InvalidArgumentError, match="run_prerequisites"):
        pipelines.process_target(["M 1", "M 2"], camera_id="ZWO", run_prerequisites=False)
