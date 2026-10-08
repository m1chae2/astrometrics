"""Purpose: Unit tests for AnalysisOrchestrator's spectroscopy analysis path.

Description: Verifies _run_spectroscopy_analysis's wiring to the
session-grouped astrometricslib astrometrics -- the part most likely to
silently misbehave given this orchestrator had no regression coverage
at all until now. Coverage for the quality-summary aggregation itself
now lives in
astrometricslib/pipelines/spectroscopy/test/test_spectroscopy_batch_tasks.py,
alongside _attach_spectroscopy_quality_summary, since that's where the
aggregation now runs.
"""

import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import BatchRunSummary, FrameRecord, InvalidArgumentError, Target
from backend.services.analysis.analysis_orchestrator import AnalysisOrchestrator


def _make_orchestrator(astrometrics: MagicMock | None = None) -> AnalysisOrchestrator:
    return AnalysisOrchestrator(
        config_service=MagicMock(),
        stellar_service=MagicMock(),
        target_service=MagicMock(),
        notification_service=MagicMock(),
        job_service=None,
        astrometrics=astrometrics or MagicMock(),
    )


def _make_session(session_id: str, frame_paths: list[str]) -> SimpleNamespace:
    return SimpleNamespace(id=session_id, frame_paths=frame_paths)


class TestRunSpectroscopyAnalysis:
    """Unit tests for AnalysisOrchestrator._run_spectroscopy_analysis."""

    def test_resolves_paths_to_frame_records_and_calls_session_grouped_astrometrics(
        self, tmp_path: Path
    ) -> None:
        """Verify paths resolve to FrameRecords for grouped astrometrics."""
        # A plain object (not a Mock/MagicMock instance) standing in
        # for the real astrometricslib astrometrics: _run_spectroscopy_analysis
        # has a dedicated branch for `isinstance(self.astrometrics, Mock)`
        # that takes an entirely different (older, mock-pipeline) code
        # path for existing tests -- using a real MagicMock here would
        # accidentally exercise that branch instead of the real one
        # this test means to cover.
        frame_a = FrameRecord(path="/lib/a.fits", role="LIGHT", timestamp=1000.0)
        frame_b = FrameRecord(path="/lib/b.fits", role="LIGHT", timestamp=1005.0)
        target = Target(id="OrchestratorWiringTestTarget", frames=[frame_a, frame_b])

        summary = BatchRunSummary(
            succeeded=["/lib/a.fits", "/lib/b.fits"],
            failed=[],
            results={
                "/lib/a.fits": {"status": "success", "stars_processed": 2},
                "/lib/b.fits": {"status": "success", "stars_processed": 3},
            },
        )
        session = _make_session(
            "OrchestratorWiringTestTarget:2026-01-01:800:0", ["/lib/a.fits", "/lib/b.fits"]
        )
        run_spectroscopy_by_session = MagicMock(
            return_value=(summary, [(session, SimpleNamespace(wcs=None))])
        )
        astrometrics = SimpleNamespace(
            processing=SimpleNamespace(
                run_spectroscopy_by_session=run_spectroscopy_by_session,
                acquire_analysis_slot=lambda: contextlib.nullcontext(),
            )
        )

        orchestrator = _make_orchestrator(astrometrics=astrometrics)
        orchestrator._target_service.get_targets.return_value = target
        orchestrator._config_service.get_photometry_workers.return_value = 1
        orchestrator._config_service.get_max_concurrent_jobs.return_value = 1
        orchestrator._config_service.get_library_path.return_value = str(tmp_path)

        results = orchestrator._run_spectroscopy_analysis(
            "job1", "OrchestratorWiringTestTarget", ["/lib/a.fits", "/lib/b.fits", "/unmatched.fits"]
        )

        call_args = run_spectroscopy_by_session.call_args
        assert call_args.args[0] is astrometrics
        assert call_args.args[1] is target
        assert call_args.args[2] == [frame_a, frame_b]  # unmatched path excluded
        assert call_args.kwargs["max_workers"] is None  # resolved internally by the library

        assert results["starsProcessed"] == 5
        assert results["spectraExtracted"] == 5
        # target.quality.spectroscopy is now attached by the
        # library's run_spectroscopy_by_session itself (mocked here, so
        # not exercised) -- see test_spectroscopy_batch_tasks.py's
        # TestAttachSpectroscopyQualitySummary for that coverage.


class TestMasterSpectralStackAnalysis:
    """Tests for stage one, the master stacked spectral image."""

    def _make_setup(self, stacked_path: str) -> tuple[AnalysisOrchestrator, MagicMock, MagicMock]:
        frame = FrameRecord(path="/lib/a.fits", role="LIGHT", timestamp=1000.0)
        target = Target(id="MasterStackTestTarget", frames=[frame], stacked_spectral_target=stacked_path)
        session = _make_session("MasterStackTestTarget:2026-01-01:800:0", ["/lib/a.fits"])
        summary = BatchRunSummary(
            succeeded=["/lib/a.fits"], failed=[], results={"/lib/a.fits": {"stars_processed": 2}}
        )
        run_spectroscopy_by_session = MagicMock(
            return_value=(summary, [(session, SimpleNamespace(wcs=None))])
        )
        process_target = MagicMock(
            return_value=SimpleNamespace(results={"spectroscopy": {"stellar_objects": [object()] * 3}})
        )
        astrometrics = SimpleNamespace(
            catalog_access=object(),
            processing=SimpleNamespace(
                process_target=process_target,
                run_spectroscopy_by_session=run_spectroscopy_by_session,
                acquire_analysis_slot=lambda: contextlib.nullcontext(),
            ),
        )
        orchestrator = _make_orchestrator(astrometrics=astrometrics)
        orchestrator._target_service.get_targets.return_value = target
        return orchestrator, process_target, run_spectroscopy_by_session

    def test_analyzes_the_master_stack_on_its_own_without_session_grouping(self) -> None:
        """Verify the master stack is analyzed as one image."""
        stacked_path = "/lib/Target_SPEC_Stacked.fits"
        orchestrator, process_target, run_by_session = self._make_setup(stacked_path)

        results = orchestrator._run_spectroscopy_analysis("job1", "MasterStackTestTarget", [stacked_path])

        process_target.assert_called_once()
        assert process_target.call_args.kwargs["stages"] == ["spectroscopy"]
        assert process_target.call_args.kwargs["spectroscopy"]["path"] == stacked_path
        run_by_session.assert_not_called()
        assert results["starsProcessed"] == 3

    def test_raw_frames_still_go_through_session_grouping(self) -> None:
        """Verify raw frames are grouped and the master stack is not."""
        stacked_path = "/lib/Target_SPEC_Stacked.fits"
        orchestrator, process_target, run_by_session = self._make_setup(stacked_path)

        results = orchestrator._run_spectroscopy_analysis(
            "job1", "MasterStackTestTarget", [stacked_path, "/lib/a.fits"]
        )

        process_target.assert_called_once()
        frame_records = run_by_session.call_args.args[2]
        assert [frame.path for frame in frame_records] == ["/lib/a.fits"]
        assert results["starsProcessed"] == 3 + 2


class TestStartAnalysisTaskClassification:
    """Verify _start_analysis_task routes paths by real FrameRecord.filter."""

    def _make_orchestrator_with_target(self, target: Target) -> AnalysisOrchestrator:
        orchestrator = _make_orchestrator()
        orchestrator._target_service.get_targets.return_value = target
        orchestrator._run_photometry_analysis = MagicMock(return_value={"status": "finished"})
        orchestrator._run_spectroscopy_analysis = MagicMock(return_value={"status": "finished"})
        return orchestrator

    def test_routes_light_frame_to_photometry_via_frame_record_filter(self) -> None:
        """Verify a Luminance-filter frame routes to photometry only."""
        light_frame = FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance")
        target = Target(id="ClassifyLightTarget", frames=[light_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyLightTarget", ["/lib/light.fits"], None)

        orchestrator._run_photometry_analysis.assert_called_once()
        assert orchestrator._run_photometry_analysis.call_args.args[2] == ["/lib/light.fits"]
        orchestrator._run_spectroscopy_analysis.assert_not_called()

    def test_routes_spec_frame_to_spectroscopy_via_frame_record_filter(self) -> None:
        """Verify a SPEC-filter frame routes to spectroscopy only."""
        spec_frame = FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC")
        target = Target(id="ClassifySpecTarget", frames=[spec_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifySpecTarget", ["/lib/spec.fits"], None)

        orchestrator._run_spectroscopy_analysis.assert_called_once()
        assert orchestrator._run_spectroscopy_analysis.call_args.args[2] == ["/lib/spec.fits"]
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_mixed_batch_runs_both_and_returns_combined_result(self) -> None:
        """Verify a mixed-type batch runs both handlers and merges results."""
        light_frame = FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance")
        spec_frame = FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC")
        target = Target(id="ClassifyMixedTarget", frames=[light_frame, spec_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        result = orchestrator._start_analysis_task(
            "job1", "ClassifyMixedTarget", ["/lib/light.fits", "/lib/spec.fits"], None
        )

        orchestrator._run_photometry_analysis.assert_called_once()
        assert orchestrator._run_photometry_analysis.call_args.args[2] == ["/lib/light.fits"]
        orchestrator._run_spectroscopy_analysis.assert_called_once()
        assert orchestrator._run_spectroscopy_analysis.call_args.args[2] == ["/lib/spec.fits"]

        assert "photometry" in result
        assert "spectroscopy" in result

    def test_unmatched_path_falls_back_to_explicit_filter_type(self) -> None:
        """Verify an unmatched path falls back to the explicit filter_type."""
        target = Target(id="ClassifyUnmatchedTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyUnmatchedTarget", ["/tmp/unmatched.fits"], "SPEC")

        orchestrator._run_spectroscopy_analysis.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()


class TestAnalysisFailures:
    """Verify analysis failures raise, and failed jobs report as data."""

    def test_no_paths_for_spectroscopy_raises_invalid_argument(self) -> None:
        """An empty spectroscopy batch raises InvalidArgumentError."""
        orchestrator = _make_orchestrator()
        orchestrator._target_service.get_targets.return_value = None

        with pytest.raises(InvalidArgumentError, match="No image files"):
            orchestrator._start_analysis_task("job1", "EmptyTarget", [], None, type="spectroscopy")

    def test_failed_job_reports_failed_status_with_message(self) -> None:
        """A failed job in the job table reads back as status "failed"."""
        orchestrator = _make_orchestrator()
        orchestrator._job_service = MagicMock()
        orchestrator._job_service.get_jobs_for_target.return_value = [
            SimpleNamespace(id="job9", status="failed", message="Plate solve failed")
        ]

        result = orchestrator.get_analysis_results("FailedTarget")

        assert result == {"status": "failed", "jobId": "job9", "error": "Plate solve failed"}
