"""Purpose: Unit tests for `ProcessingPipelines`' pipeline-running methods.

Description: `run_astrometry`/`run_photometry`/`run_spectroscopy` used to
take a bare `**kwargs`, so a caller (or the MCP schema built from these
signatures) had no way to see what options actually existed. These tests
verify each method's now-explicit named parameters still reach the
underlying pipeline dispatch correctly, and that `process_target` runs the
three stages in the right fixed order, respects a `stages` subset, skips
spectroscopy gracefully when a target has no spectral data, and threads
photometry's result into the spectroscopy call.
"""

import pytest

from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.foundation.config import AppConfiguration
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
def recording_pipelines(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
    return ProcessingPipelines(AppConfiguration()), recorder


def test_run_astrometry_forwards_its_named_parameters(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify `run_astrometry`'s named parameters reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="AstrometryParamsTarget", stacked_image="/fake/stack.fits")

    pipelines.run_astrometry(target, path="/override.fits", register_job=False)

    assert len(recorder.calls) == 1
    assert recorder.calls[0]["pipeline_type"] == "astrometry"
    assert recorder.calls[0]["path"] == "/override.fits"


def test_run_photometry_forwards_its_named_parameters(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify `run_photometry`'s named parameters reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="PhotometryParamsTarget")
    frame = FrameRecord(path="/frame1.fits", filter="Luminance")

    pipelines.run_photometry(
        target,
        frames=[frame],
        filter_type="L",
        use_astrometry_seed=False,
        max_workers=3,
        register_job=False,
    )

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "photometry"
    assert call["frames"] == [frame]
    assert call["filter_type"] == "L"
    assert call["use_astrometry_seed"] is False
    assert call["max_workers"] == 3


def test_run_spectroscopy_forwards_its_named_parameters(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify `run_spectroscopy`'s named parameters reach the dispatch call."""
    pipelines, recorder = recording_pipelines
    target = Target(id="SpectroscopyParamsTarget", stacked_spectral_target="/fake/spec.fits")

    pipelines.run_spectroscopy(target, limit=5, register_job=False, photometry_result={"status": "finished"})

    assert len(recorder.calls) == 1
    call = recorder.calls[0]
    assert call["pipeline_type"] == "spectroscopy"
    assert call["limit"] == 5
    assert call["photometry_result"] == {"status": "finished"}


def test_process_target_runs_all_three_stages_in_order(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
    assert set(results) == {"astrometry", "photometry", "spectroscopy"}


def test_process_target_threads_photometry_result_into_spectroscopy(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify spectroscopy receives photometry's result forwarded to it."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetThreadingTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(target, register_job=False)

    spectroscopy_call = next(call for call in recorder.calls if call["pipeline_type"] == "spectroscopy")
    assert spectroscopy_call["photometry_result"] == results["photometry"]


def test_process_target_stages_subset_runs_only_the_requested_stages(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a `stages` subset skips whatever stage isn't listed."""
    pipelines, recorder = recording_pipelines
    target = Target(
        id="ProcessTargetSubsetTarget",
        stacked_image="/fake/stack.fits",
        stacked_spectral_target="/fake/spec.fits",
    )

    results = pipelines.process_target(
        target, stages=frozenset({"spectroscopy", "photometry"}), register_job=False
    )

    assert [call["pipeline_type"] for call in recorder.calls] == ["photometry", "spectroscopy"]
    assert set(results) == {"photometry", "spectroscopy"}


def test_process_target_skips_spectroscopy_without_spectral_data(recording_pipelines):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify spectroscopy is skipped on a target with no spectral input."""
    pipelines, recorder = recording_pipelines
    target = Target(id="ProcessTargetNoSpectraTarget", stacked_image="/fake/stack.fits")

    results = pipelines.process_target(target, register_job=False)

    assert "spectroscopy" not in [call["pipeline_type"] for call in recorder.calls]
    assert results["spectroscopy"] == {"status": "skipped", "reason": "target has no spectral data"}


def test_stack_summary_reports_a_target_with_no_stack() -> None:
    """A target without a saved stack summary gets an error, not a crash."""
    target = Target(id="Nothing_Yet", name="Nothing Yet")
    answer = ProcessingPipelines(AppConfiguration()).stack_summary(target)
    assert "has no saved summary" in answer["error"]
