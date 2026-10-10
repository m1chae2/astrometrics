"""Purpose: Unit tests for `ProcessingPipelines.process_target`'s stages.

Description: Each stage takes its options in its own dictionary, so an
option meant for one stage never reaches another. These tests verify each
stage's options reach the underlying pipeline dispatch correctly, that an
unknown option is refused, and that `process_target` runs the stages in the
right fixed order, respects a `stages` subset, skips spectroscopy gracefully
when a target has no spectral data, and threads photometry's result into
the spectroscopy call.
"""

from unittest.mock import MagicMock

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines import tasks


class _RecordingPipelineMatch:
    """A `tasks._run_analysis_pipeline_match` stub that records every call.

    Returns a distinct, recognizable dict per `pipeline_type` so a test
    can confirm which stage's result reached a later stage.
    """

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[dict] = []

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
        """Record the call and return a stage-recognizable stub result.

        Returns
        -------
        result : `dict`
            A minimal stub result carrying `pipeline_type` back.
        """
        self.calls.append({
            "pipeline_type": pipeline_type,
            "frames": frames,
            "filter_type": filter_type,
            "catalog_access": catalog_access,
            "path": path,
            **kwargs,
        })
        return {"status": "finished", "pipeline_type": pipeline_type}


@pytest.fixture
def recording_pipelines(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ProcessingPipelines, _RecordingPipelineMatch]:
    """Build a `ProcessingPipelines` whose dispatch is recorded, not run.

    Returns
    -------
    pipelines : `ProcessingPipelines`
        A pipelines instance backed by a fresh, in-memory-only config.
    recorder : `_RecordingPipelineMatch`
        The stub recording every dispatched call.
    """
    recorder = _RecordingPipelineMatch()
    monkeypatch.setattr(tasks, "_run_analysis_pipeline_match", recorder)
    return ProcessingPipelines(AppConfiguration(), MagicMock()), recorder


def test_the_astrometry_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the astrometry options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="AstrometryParamsTarget", stacked_image="/fake/stack.fits")

    pipelines.process_target(
        target, stages=["astrometry"], astrometry={"path": "/override.fits"}, register_job=False
    )

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["pipeline_type"] == "astrometry"
    assert recorder.calls[0]["path"] == "/override.fits"


def test_the_photometry_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the photometry options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="PhotometryParamsTarget")
    frame = FrameRecord(path="/frame1.fits", filter="Luminance")

    pipelines.process_target(
        target,
        stages=["photometry"],
        photometry={"frames": [frame], "filter_type": "L", "use_astrometry_seed": False, "max_workers": 3},
        register_job=False,
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "photometry"
    assert call["frames"] == [frame]
    assert call["filter_type"] == "L"
    assert call["use_astrometry_seed"] is False
    assert call["max_workers"] == 3


def test_the_spectroscopy_stage_forwards_its_options(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the spectroscopy options reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="SpectroscopyParamsTarget", stacked_spectral_target="/fake/spec.fits")

    pipelines.process_target(target, stages=["spectroscopy"], spectroscopy={"limit": 5}, register_job=False)

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "spectroscopy"
    assert call["limit"] == 5
    assert call["photometry_result"] is None


def test_an_option_a_stage_does_not_know_is_refused(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a misspelt or misplaced option raises, not being dropped."""
    pipelines, recorder = recording_pipelines
    target = Target(id="UnknownOptionTarget", stacked_image="/fake/stack.fits")

    with pytest.raises(InvalidArgumentError, match="Unknown astrometry option"):
        pipelines.process_target(target, stages=["astrometry"], astrometry={"limit": 5}, register_job=False)
    with pytest.raises(InvalidArgumentError, match="does not use: camera_id"):
        pipelines.process_target(target, camera_id="ASI294", register_job=False)
    assert recorder.calls == []


def test_process_target_runs_all_three_stages_in_order(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify the default run order is astrometry, photometry, spectroscopy."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetOrderTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, register_job=False)

    assert [call["pipeline_type"] for call in recorder.calls] == [
        "astrometry",
        "photometry",
        "spectroscopy",
    ]
    assert results.stages_run == ["astrometry", "photometry", "spectroscopy"]


def test_process_target_threads_photometry_result_into_spectroscopy(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy receives photometry's result forwarded to it."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetThreadingTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, register_job=False)

    spectroscopy_call = next(call for call in recorder.calls if call["pipeline_type"] == "spectroscopy")
    assert spectroscopy_call["photometry_result"] == results.results["photometry"]


def test_process_target_stages_subset_runs_only_the_requested_stages(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify a `stages` subset skips whatever stage isn't listed."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetSubsetTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, stages=["spectroscopy", "photometry"], register_job=False)

    assert [call["pipeline_type"] for call in recorder.calls] == ["photometry", "spectroscopy"]
    assert set(results.results) == {"photometry", "spectroscopy"}


def test_process_target_skips_spectroscopy_without_spectral_data(
    recording_pipelines: tuple[ProcessingPipelines, _RecordingPipelineMatch],
) -> None:
    """Verify spectroscopy is skipped on a target with no spectral input."""
    pipelines, recorder = recording_pipelines
    target = Target(id="ProcessTargetNoSpectraTarget", stacked_image="/fake/stack.fits")

    results = pipelines.process_target(target, register_job=False)

    assert "spectroscopy" not in [call["pipeline_type"] for call in recorder.calls]
    assert results.results["spectroscopy"] == {"status": "skipped", "reason": "target has no spectral data"}


def test_stack_summary_reports_a_target_with_no_stack() -> None:
    """A target without a saved stack summary raises NotFoundError."""
    target = Target(id="Nothing_Yet", name="Nothing Yet")
    with pytest.raises(NotFoundError, match="has no saved summary"):
        ProcessingPipelines(AppConfiguration(), MagicMock()).stack_summary(target)
