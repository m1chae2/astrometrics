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

from astrometricslib import FrameRecord, Target
from astrometricslib._internal.results import BatchRunSummary
from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.jobs import JobStore
from backend.services.analysis.analysis_orchestrator import AnalysisOrchestrator
from backend.services.processing.job_service import JobService


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
                acquire_job_slot=lambda: contextlib.nullcontext(),
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
        assert call_args.args[0] is target
        assert call_args.args[1] == [frame_a, frame_b]  # unmatched path excluded
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
                acquire_job_slot=lambda: contextlib.nullcontext(),
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
        frame_records = run_by_session.call_args.args[1]
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

    def test_unmatched_path_falls_back_to_explicit_filter_name(self) -> None:
        """Verify an unmatched path falls back to the explicit filter_name."""
        target = Target(id="ClassifyUnmatchedTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyUnmatchedTarget", ["/tmp/unmatched.fits"], "SPEC")

        orchestrator._run_spectroscopy_analysis.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_unmatched_frame_with_spectral_filter_header_is_spectroscopy(self) -> None:
        """Verify a spectral FILTER card sends an ad hoc frame to spectra."""
        target = Target(id="ClassifyHeaderTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)
        orchestrator.astrometrics.targets.read_frame_header.return_value = {"FILTER": "Star Analyzer 200"}

        orchestrator._start_analysis_task("job1", "ClassifyHeaderTarget", ["/tmp/adhoc.fits"], "ALL")

        orchestrator.astrometrics.targets.read_frame_header.assert_called_once_with("/tmp/adhoc.fits")
        orchestrator._run_spectroscopy_analysis.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_unreadable_header_is_logged_and_the_frame_stays_light(self) -> None:
        """Verify a header read error is caught; the frame stays a light."""
        target = Target(id="ClassifyBadHeaderTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)
        orchestrator.astrometrics.targets.read_frame_header.side_effect = OSError("cut off")

        orchestrator._start_analysis_task("job1", "ClassifyBadHeaderTarget", ["/tmp/adhoc.fits"], "ALL")

        orchestrator._run_spectroscopy_analysis.assert_not_called()
        orchestrator._run_photometry_analysis.assert_called_once()


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


class TestResultSurvivesRestart:
    """Verify the finished summary is read back from the job store."""

    def _make_job_service(self, tmp_path: Path) -> JobService:
        """Build a real job service on a throwaway database.

        Returns
        -------
        job_service : `JobService`
            A job service backed by a database under `tmp_path`.
        """
        return JobService(JobStore(str(tmp_path / "jobs.db")))

    def _make_orchestrator_with(self, job_service: JobService) -> AnalysisOrchestrator:
        """Build an orchestrator that shares the given job service.

        Returns
        -------
        orchestrator : `AnalysisOrchestrator`
            An orchestrator with no in-memory futures.
        """
        orchestrator = _make_orchestrator()
        orchestrator._job_service = job_service
        return orchestrator

    def test_summary_is_returned_by_a_new_orchestrator(self, tmp_path: Path) -> None:
        """A second orchestrator on the same job store returns the summary."""
        job_service = self._make_job_service(tmp_path)
        summary = {
            "status": "finished",
            "totalImages": 12,
            "rejectedCount": 7,
            "rejectedFiles": ["/lib/a.fits"],
            "variableCandidates": [{"starId": 3, "score": 1.5}],
        }
        first = self._make_orchestrator_with(job_service)
        job = job_service.create_job(target_id="M 31", job_type="analysis", log_file=None)
        first._store_job_result(job.id, summary)
        job_service.update_job(job.id, status="completed", progress=100.0)

        restarted = self._make_orchestrator_with(job_service)

        assert restarted.get_analysis_results("M 31") == summary

    def test_job_without_stored_summary_reports_finished(self, tmp_path: Path) -> None:
        """A completed job with no saved summary still answers."""
        job_service = self._make_job_service(tmp_path)
        job = job_service.create_job(target_id="M 31", job_type="analysis", log_file=None)
        job_service.update_job(job.id, status="completed", progress=100.0)

        result = self._make_orchestrator_with(job_service).get_analysis_results("M 31")

        assert result == {"status": "finished", "jobId": job.id}


class TestChosenStages:
    """Verify the `stages` argument of `analyze_image` and its worker."""

    def _make_setup(self, target: Target, outcome: SimpleNamespace) -> tuple[AnalysisOrchestrator, MagicMock]:
        """Build an orchestrator whose library call is recorded.

        Returns
        -------
        orchestrator : `AnalysisOrchestrator`
            The orchestrator, with a job service that records updates.
        process_target : `MagicMock`
            The recorded library call, which answers with ``outcome``.
        """
        process_target = MagicMock(return_value=outcome)
        astrometrics = SimpleNamespace(
            processing=SimpleNamespace(
                process_target=process_target, acquire_job_slot=lambda: contextlib.nullcontext()
            )
        )
        orchestrator = _make_orchestrator(astrometrics=astrometrics)
        orchestrator._job_service = MagicMock()
        # The worker writes to the job's log file, so give it no path to write.
        orchestrator._job_service.get_job.return_value.log_file_path = None
        orchestrator._target_service.get_targets.return_value = target
        return orchestrator, process_target

    @staticmethod
    def _outcome(**fields: object) -> SimpleNamespace:
        """Build a stand-in for the library's answer.

        Returns
        -------
        outcome : `SimpleNamespace`
            An answer with nothing run unless the fields say otherwise.
        """
        values = {"stages_run": [], "prerequisites_run": [], "stages_skipped": {}, "results": {}}
        values.update(fields)
        return SimpleNamespace(**values)

    def test_unknown_or_empty_stages_are_refused_before_a_job_starts(self) -> None:
        """Verify a bad stage name raises and no job is submitted."""
        orchestrator = _make_orchestrator()
        orchestrator._submit_job = MagicMock()

        with pytest.raises(InvalidArgumentError, match="asteroids"):
            orchestrator.analyze_image("M 57", [], stages=["photometry", "asteroids"])
        with pytest.raises(InvalidArgumentError):
            orchestrator.analyze_image("M 57", [], stages=[])

        orchestrator._submit_job.assert_not_called()

    def test_stages_are_kept_once_in_running_order(self) -> None:
        """Verify the checked list is deduplicated and ordered."""
        checked = AnalysisOrchestrator._checked_stages(["spectroscopy", "astrometry", "spectroscopy"])

        assert checked == ["astrometry", "spectroscopy"]
        assert AnalysisOrchestrator._checked_stages(None) is None

    def test_chosen_stages_reach_the_worker(self) -> None:
        """Verify `stages` is handed to the job and `None` is left alone."""
        orchestrator = _make_orchestrator()
        orchestrator._job_service = MagicMock()
        orchestrator._job_service.get_jobs_for_target.return_value = []
        orchestrator._submit_job = MagicMock(return_value="job1")
        orchestrator._config_service.get_logs_path.return_value = Path("/tmp")

        orchestrator.analyze_image("M 57", ["/lib/a.fits"], stages=["spectroscopy", "photometry"])
        orchestrator.analyze_image("M 57", ["/lib/a.fits"])

        assert orchestrator._submit_job.call_args_list[0].args[-1] == ["photometry", "spectroscopy"]
        assert orchestrator._submit_job.call_args_list[1].args[-1] is None

    def test_a_target_with_stacks_uses_them_and_ignores_the_sent_files(self, tmp_path: Path) -> None:
        """Verify stacks win over sent files; spectroscopy keeps its limit."""
        imaging_stack = tmp_path / "stack.fits"
        spectral_stack = tmp_path / "spec_stack.fits"
        imaging_stack.write_bytes(b"")
        spectral_stack.write_bytes(b"")
        target = Target(
            id="HasStacksTarget",
            stacked_image=str(imaging_stack),
            stacked_spectral_target=str(spectral_stack),
            frames=[FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC")],
        )
        orchestrator, process_target = self._make_setup(
            target, self._outcome(stages_run=["astrometry", "spectroscopy"], prerequisites_run=["astrometry"])
        )

        result = orchestrator._run_chosen_stages(
            "job1", "HasStacksTarget", ["/lib/light.fits", "/lib/spec.fits"], "L", ["spectroscopy"]
        )

        keywords = process_target.call_args.kwargs
        assert keywords["stages"] == ["spectroscopy"]
        assert keywords["astrometry"] is None
        assert keywords["spectroscopy"] == {"path": str(spectral_stack), "limit": 10}
        assert keywords["run_prerequisites"] is True
        assert keywords["register_job"] is False
        assert result["prerequisitesRun"] == ["astrometry"]
        assert result["message"] == (
            "Ran astrometry, spectroscopy (astrometry added because its result was missing"
            " or older than the stack)."
        )
        orchestrator._job_service.update_job.assert_any_call("job1", status_message=result["message"])

    def test_a_target_without_stacks_uses_the_sent_files(self) -> None:
        """Verify the sent files stand in for the stacks the target lacks."""
        target = Target(
            id="NoStacksTarget",
            frames=[
                FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance"),
                FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC"),
            ],
        )
        orchestrator, process_target = self._make_setup(target, self._outcome())

        orchestrator._run_chosen_stages(
            "job1",
            "NoStacksTarget",
            ["/lib/spec.fits", "/lib/light.fits"],
            "L",
            ["astrometry", "spectroscopy"],
        )

        keywords = process_target.call_args.kwargs
        assert keywords["astrometry"] == {"path": "/lib/light.fits"}
        assert keywords["spectroscopy"] == {"path": "/lib/spec.fits"}

    def test_skipped_stages_are_reported_with_their_reasons(self) -> None:
        """Verify the job message and result name each skipped stage."""
        reason = "needs astrometry: the target has no imaging stack to solve and no image was given"
        target = Target(id="SkippedTarget")
        orchestrator, _process_target = self._make_setup(
            target,
            self._outcome(
                stages_skipped={"photometry": reason},
                results={"photometry": {"status": "skipped", "reason": reason}},
            ),
        )

        result = orchestrator._run_chosen_stages("job1", "SkippedTarget", [], "L", ["photometry"])

        assert result["stagesSkipped"] == {"photometry": reason}
        assert result["message"] == f"Ran no stage. Skipped photometry ({reason})."
        assert result["results"] == {"photometry": "skipped"}

    def test_the_worker_routes_chosen_stages_to_the_library_call(self) -> None:
        """Verify `_start_analysis_task` takes the `stages` path when given."""
        target = Target(id="RoutedTarget")
        orchestrator, process_target = self._make_setup(target, self._outcome(stages_run=["astrometry"]))
        orchestrator._run_photometry_analysis = MagicMock()

        orchestrator._start_analysis_task("job1", "RoutedTarget", [], "L", "photometry", ["astrometry"])

        process_target.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()
