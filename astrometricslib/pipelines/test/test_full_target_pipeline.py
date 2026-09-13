"""Regression tests for run_full_pipeline's stage sequencing.

`stack_and_solve` (now folded into `orchestration.py`) used to run its
own astrometry pass internally right after a successful standard
stack, in addition to `run_full_pipeline`'s own, separate
`_run_astrometry_stage` call immediately afterward -- silently
plate-solving and recording the same target's stars twice per run.
This test exercises the real stacking and analysis-dispatch code (only
the external Siril call is stubbed out) to pin that each stage now
runs exactly once.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines import orchestration
from astrometricslib.pipelines.stacking import stage as stacking_stage


class _StubCatalogAccess:
    """Fake catalog_access that just remembers its last saved record."""

    def __init__(self) -> None:
        self.saved: list[tuple[Any, str, dict]] = []

    def put(self, record: Any, record_type: str, options: dict) -> None:
        self.saved.append((record, record_type, options))


class _StubConfig:
    """Fake config exposing only what the pipeline's stages read from it."""

    def __init__(self, library_path: Path) -> None:
        self._library_path = library_path

    def get_max_concurrent_jobs(self) -> int:
        return 1

    def get_library_path(self) -> Path:
        return self._library_path


def test_run_full_pipeline_runs_astrometry_and_photometry_exactly_once(monkeypatch, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A full pipeline run must not solve/record a target's stars twice.

    Regression test for the duplicate-astrometry bug: stacking used to
    trigger its own hidden astrometry call, on top of this pipeline's
    explicit one, for every standard-frame target. Only the actual
    Siril invocation is stubbed out -- stack_frames_with_timeout,
    analyze_target, and the pipeline dispatch table all run for real.
    """
    stacked_path = str(tmp_path / "stacked.fits")
    Path(stacked_path).touch()

    target = Target(
        id="RegressionTarget",
        frames=[
            FrameRecord(path="/fake/frame1.fits", camera="TestCam", filter="Luminance"),
            FrameRecord(path="/fake/frame2.fits", camera="TestCam", filter="Luminance"),
        ],
    )

    def _fake_stack_frames(
        target,  # ruff: ignore[missing-type-function-argument]
        log_file=None,  # ruff: ignore[missing-type-function-argument]
        frames_to_stack=None,  # ruff: ignore[missing-type-function-argument]
        filter_type=None,  # ruff: ignore[missing-type-function-argument]
        **kwargs: Any,
    ) -> str:
        target.stacked_image = stacked_path
        return stacked_path

    monkeypatch.setattr(stacking_stage, "stack_frames", _fake_stack_frames)

    pipeline_type_calls: list[str] = []

    def _fake_run_analysis_pipeline_match(
        target,  # ruff: ignore[missing-type-function-argument]
        frames,  # ruff: ignore[missing-type-function-argument]
        pipeline_type,  # ruff: ignore[missing-type-function-argument]
        filter_type,  # ruff: ignore[missing-type-function-argument]
        catalog_access,  # ruff: ignore[missing-type-function-argument]
        path,  # ruff: ignore[missing-type-function-argument]
        **kwargs: Any,
    ) -> dict[str, Any]:
        pipeline_type_calls.append(pipeline_type)
        if pipeline_type == "astrometry":
            return {"wcs": object()}
        if pipeline_type == "photometry":
            return {"starsFound": 0}
        raise AssertionError(f"Unexpected pipeline_type for this target: {pipeline_type}")

    monkeypatch.setattr(orchestration, "_run_analysis_pipeline_match", _fake_run_analysis_pipeline_match)

    catalog_access = _StubCatalogAccess()
    astrometrics = SimpleNamespace(catalog_access=catalog_access, config=_StubConfig(tmp_path))

    stack_outputs = orchestration.run_full_pipeline(target, astrometrics, camera_name="TestCam")

    assert stack_outputs == {"standard": stacked_path}
    assert pipeline_type_calls.count("astrometry") == 1
    assert pipeline_type_calls.count("photometry") == 1
    assert len(catalog_access.saved) == 1
