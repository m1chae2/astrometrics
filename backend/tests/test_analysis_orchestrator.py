"""Purpose: Unit tests for AnalysisOrchestrator's analysis routing.

Description: Verifies how analyze_image routes frames by type and how the
chosen stages reach the library's process_target. Raw spectral frames now go
through the spectroscopy stage; the per-frame work (the spectral variation
stage) lives in the library, and its tests are in
astrometricslib/pipelines/spectroscopy/test/test_spectral_variation.py.
"""

import contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import FrameRecord, Target
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


class TestStartAnalysisTaskClassification:
    """Verify _start_analysis_task routes paths by real FrameRecord.filter."""

    def _make_orchestrator_with_target(self, target: Target) -> AnalysisOrchestrator:
        orchestrator = _make_orchestrator()
        orchestrator._target_service.get_targets.return_value = target
        orchestrator._run_photometry_analysis = MagicMock(return_value={"status": "finished"})
        orchestrator._run_chosen_stages = MagicMock(return_value={"status": "finished"})
        return orchestrator

    def test_routes_light_frame_to_photometry_via_frame_record_filter(self) -> None:
        """Verify a Luminance-filter frame routes to photometry only."""
        light_frame = FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance")
        target = Target(id="ClassifyLightTarget", frames=[light_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyLightTarget", ["/lib/light.fits"], None)

        orchestrator._run_photometry_analysis.assert_called_once()
        assert orchestrator._run_photometry_analysis.call_args.args[2] == ["/lib/light.fits"]
        orchestrator._run_chosen_stages.assert_not_called()

    def test_routes_spec_frame_to_spectroscopy_via_frame_record_filter(self) -> None:
        """Verify a SPEC-filter frame routes to spectroscopy only."""
        spec_frame = FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC")
        target = Target(id="ClassifySpecTarget", frames=[spec_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifySpecTarget", ["/lib/spec.fits"], None)

        orchestrator._run_chosen_stages.assert_called_once()
        assert orchestrator._run_chosen_stages.call_args.args[2] == ["/lib/spec.fits"]
        assert orchestrator._run_chosen_stages.call_args.args[4] == ["spectroscopy"]
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
        orchestrator._run_chosen_stages.assert_called_once()
        assert orchestrator._run_chosen_stages.call_args.args[2] == ["/lib/spec.fits"]

        assert "photometry" in result
        assert "spectroscopy" in result

    def test_unmatched_path_falls_back_to_explicit_filter_name(self) -> None:
        """Verify an unmatched path falls back to the explicit filter_name."""
        target = Target(id="ClassifyUnmatchedTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyUnmatchedTarget", ["/tmp/unmatched.fits"], "SPEC")

        orchestrator._run_chosen_stages.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_unmatched_frame_with_spectral_filter_header_is_spectroscopy(self) -> None:
        """Verify a spectral FILTER card sends an ad hoc frame to spectra."""
        target = Target(id="ClassifyHeaderTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)
        orchestrator.astrometrics.targets.read_frame_header.return_value = {"FILTER": "Star Analyzer 200"}

        orchestrator._start_analysis_task("job1", "ClassifyHeaderTarget", ["/tmp/adhoc.fits"], "ALL")

        orchestrator.astrometrics.targets.read_frame_header.assert_called_once_with("/tmp/adhoc.fits")
        orchestrator._run_chosen_stages.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_unreadable_header_is_logged_and_the_frame_stays_light(self) -> None:
        """Verify a header read error is caught; the frame stays a light."""
        target = Target(id="ClassifyBadHeaderTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)
        orchestrator.astrometrics.targets.read_frame_header.side_effect = OSError("cut off")

        orchestrator._start_analysis_task("job1", "ClassifyBadHeaderTarget", ["/tmp/adhoc.fits"], "ALL")

        orchestrator._run_chosen_stages.assert_not_called()
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
        checked = AnalysisOrchestrator._checked_stages([
            "spectral_variation",
            "spectroscopy",
            "astrometry",
            "spectroscopy",
        ])

        assert checked == ["astrometry", "spectroscopy", "spectral_variation"]
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
        assert result["message"] == ("Astrometry \u2192 Spectroscopy")
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
        assert result["message"] == "Photometry (skipped: needs astrometry)"
        assert result["results"] == {"photometry": "skipped"}

    def test_spectral_variation_follows_the_sent_raw_spectral_frames(self) -> None:
        """Verify the sent raw spectral frames become the stage's frames."""
        spec_a = FrameRecord(path="/lib/a.fits", role="LIGHT", filter="SPEC")
        spec_b = FrameRecord(path="/lib/b.fits", role="LIGHT", filter="SPEC")
        target = Target(id="VariationTarget", frames=[spec_a, spec_b])
        orchestrator, process_target = self._make_setup(
            target, self._outcome(stages_run=["spectral_variation"])
        )

        orchestrator._run_chosen_stages(
            "job1", "VariationTarget", ["/lib/b.fits"], "SPEC", ["spectral_variation"]
        )

        keywords = process_target.call_args.kwargs
        assert keywords["stages"] == ["spectral_variation"]
        assert keywords["spectral_variation"] == {"frames": [spec_b]}

    def test_a_skipped_spectral_variation_reason_is_in_the_message(self) -> None:
        """Verify a stage skipped for too few frames says why."""
        target = Target(id="FewFramesTarget")
        orchestrator, _process_target = self._make_setup(
            target,
            self._outcome(
                stages_run=["spectral_variation"],
                results={
                    "spectral_variation": {
                        "status": "skipped",
                        "reason": "no session has 2 or more raw spectral frames",
                    }
                },
            ),
        )

        result = orchestrator._run_chosen_stages(
            "job1", "FewFramesTarget", [], "SPEC", ["spectral_variation"]
        )

        assert result["message"] == "Spectral Variation (skipped)"

    def test_the_worker_routes_chosen_stages_to_the_library_call(self) -> None:
        """Verify `_start_analysis_task` takes the `stages` path when given."""
        target = Target(id="RoutedTarget")
        orchestrator, process_target = self._make_setup(target, self._outcome(stages_run=["astrometry"]))
        orchestrator._run_photometry_analysis = MagicMock()

        orchestrator._start_analysis_task("job1", "RoutedTarget", [], "L", "photometry", ["astrometry"])

        process_target.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()


class TestRecordedSteps:
    """Verify a chosen-stages run records its steps on the job."""

    def test_an_added_prerequisite_is_recorded_while_running_and_after(self, tmp_path: Path) -> None:
        """Save the plan before the stages run, then the outcome."""
        job_service = JobService(JobStore(str(tmp_path / "jobs.db")))
        job = job_service.create_job(target_id="M 31", job_type="analysis", log_file=None)
        seen_while_running: list[dict] = []

        def process_target(target: object, **keywords: object) -> SimpleNamespace:
            """Stand in for the library, noting the record after the plan.

            Returns
            -------
            outcome : `SimpleNamespace`
                An answer where astrometry was added and both stages ran.
            """
            keywords["on_stages_planned"](("photometry",), ("astrometry",))
            seen_while_running.append(job_service.get_job(job.id).input_metrics["steps"])
            return SimpleNamespace(
                stages_run=["astrometry", "photometry"],
                prerequisites_run=["astrometry"],
                stages_skipped={},
                results={},
            )

        astrometrics = SimpleNamespace(
            processing=SimpleNamespace(
                process_target=process_target, acquire_job_slot=lambda: contextlib.nullcontext()
            )
        )
        orchestrator = _make_orchestrator(astrometrics=astrometrics)
        orchestrator._job_service = job_service
        orchestrator._target_service.get_targets.return_value = Target(id="M 31")

        orchestrator._run_chosen_stages(job.id, "M 31", [], "L", ["photometry"])

        assert seen_while_running[0]["requested"] == ["photometry"]
        assert seen_while_running[0]["prerequisites"] == ["astrometry"]
        assert "finished" not in seen_while_running[0]
        final = job_service.get_job(job.id).input_metrics["steps"]
        assert final["run"] == ["astrometry", "photometry"]
        assert final["prerequisites"] == ["astrometry"]
        assert final["skipped"] == {}
        assert final["finished"] is True

    def test_a_skipped_stage_is_recorded_with_its_reason(self, tmp_path: Path) -> None:
        """List a skipped stage as skipped, not as run."""
        job_service = JobService(JobStore(str(tmp_path / "jobs.db")))
        job = job_service.create_job(target_id="M 31", job_type="analysis", log_file=None)
        outcome = SimpleNamespace(
            stages_run=[],
            prerequisites_run=[],
            stages_skipped={"photometry": "needs astrometry: no stack"},
            results={"photometry": {"status": "skipped", "reason": "needs astrometry: no stack"}},
        )
        astrometrics = SimpleNamespace(
            processing=SimpleNamespace(
                process_target=MagicMock(return_value=outcome),
                acquire_job_slot=lambda: contextlib.nullcontext(),
            )
        )
        orchestrator = _make_orchestrator(astrometrics=astrometrics)
        orchestrator._job_service = job_service
        orchestrator._target_service.get_targets.return_value = Target(id="M 31")

        orchestrator._run_chosen_stages(job.id, "M 31", [], "L", ["photometry"])

        final = job_service.get_job(job.id).input_metrics["steps"]
        assert final["run"] == []
        assert final["skipped"] == {"photometry": "needs astrometry: no stack"}


class TestCancelAnalysis:
    """Verify cancelling an analysis job stops it and marks it cancelled."""

    def test_cancel_while_waiting_for_a_slot_ends_the_job_as_cancelled(self, tmp_path: Path) -> None:
        """A job queued for a slot gives up and is cancelled, not failed."""
        import threading
        import time

        from astrometricslib.foundation.storage.process_locks import acquire_resource_slot

        slot_held = threading.Event()
        release_slot = threading.Event()

        def hold_the_only_slot() -> None:
            """Hold the only slot, in a thread of its own, until released."""
            with acquire_resource_slot(None, "cancel_analysis_test", 1):
                slot_held.set()
                release_slot.wait(30)

        holder = threading.Thread(target=hold_the_only_slot)
        holder.start()
        assert slot_held.wait(10)

        orchestrator = _make_orchestrator()
        orchestrator._job_service = JobService(JobStore(str(tmp_path / "jobs.db")))

        def wait_for_slot(*_arguments: object) -> dict:
            """Queue for the slot the way the analysis does.

            Returns
            -------
            result : `dict`
                Never reached while the slot is held.
            """
            with acquire_resource_slot(None, "cancel_analysis_test", 1, poll_interval_seconds=0.05):
                return {"status": "finished"}

        orchestrator._run_analysis_task_body = wait_for_slot  # type: ignore[method-assign]
        job_id = orchestrator._submit_job(
            "M 52", "analysis", orchestrator._start_analysis_task, ["f.fits"], None
        )
        end = time.monotonic() + 10
        while job_id not in orchestrator._analysis_abandonments:
            assert time.monotonic() < end, "the job never started"
            time.sleep(0.02)

        answer = orchestrator.cancel_analysis("M 52")

        orchestrator._jobs[job_id]["future"].result(timeout=10)
        release_slot.set()
        holder.join(10)
        record = orchestrator._job_service.get_job(job_id)
        assert answer["cancelled"] is True
        assert record.status == "cancelled"
        assert record.message == "Cancelled by user"
        assert job_id not in orchestrator._analysis_abandonments
