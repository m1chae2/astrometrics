"""Purpose: Unit tests for AnalysisOrchestrator's spectroscopy analysis path.

Description: Verifies _run_spectroscopy_analysis's wiring to the
session-grouped astrometricslib astrometrics -- the part most likely to
silently misbehave given this orchestrator had no regression coverage
at all until now. Coverage for the quality-summary aggregation itself
now lives in
astrometricslib/tasks/target_tasks/test/test_spectroscopy_batch_tasks.py,
alongside _attach_spectroscopy_quality_summary, since that's where the
aggregation now runs.
"""

import contextlib
from types import SimpleNamespace
from unittest.mock import MagicMock

from astrometricslib import BatchRunSummary, FrameRecord, Target
from backend.services.analysis.analysis_orchestrator import AnalysisOrchestrator


def _make_orchestrator(astrometrics=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    return AnalysisOrchestrator(
        config_service=MagicMock(),
        stellar_service=MagicMock(),
        target_service=MagicMock(),
        notification_service=MagicMock(),
        job_service=None,
        astrometrics=astrometrics or MagicMock(),
    )


def _make_session(session_id, frame_paths):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    return SimpleNamespace(id=session_id, frame_paths=frame_paths)


class TestRunSpectroscopyAnalysis:
    """Unit tests for AnalysisOrchestrator._run_spectroscopy_analysis."""

    def test_resolves_paths_to_frame_records_and_calls_session_grouped_astrometrics(self, tmp_path):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
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
        orchestrator._config_service.get_analysis_concurrency.return_value = 1
        orchestrator._config_service.get_library_path.return_value = str(tmp_path)

        results = orchestrator._run_spectroscopy_analysis(
            "job1",
            "OrchestratorWiringTestTarget",
            ["/lib/a.fits", "/lib/b.fits", "/unmatched.fits"],
            pipeline=MagicMock(),
        )

        call_args = run_spectroscopy_by_session.call_args
        assert call_args.args[0] is astrometrics
        assert call_args.args[1] is target
        assert call_args.args[2] == [frame_a, frame_b]  # unmatched path excluded
        assert call_args.kwargs["max_workers"] is None  # resolved internally by the library

        assert results["starsProcessed"] == 5
        assert results["spectraExtracted"] == 5
        # target.spectroscopy_quality_summary is now attached by the
        # library's run_spectroscopy_by_session itself (mocked here, so
        # not exercised) -- see test_spectroscopy_batch_tasks.py's
        # TestAttachSpectroscopyQualitySummary for that coverage.


class TestStartAnalysisTaskClassification:
    """Verify _start_analysis_task routes paths by real FrameRecord.filter."""

    def _make_orchestrator_with_target(self, target):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        orchestrator = _make_orchestrator()
        orchestrator._target_service.get_targets.return_value = target
        orchestrator._run_photometry_analysis = MagicMock(return_value={"status": "finished"})
        orchestrator._run_spectroscopy_analysis = MagicMock(return_value={"status": "finished"})
        return orchestrator

    def test_routes_light_frame_to_photometry_via_frame_record_filter(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a Luminance-filter frame routes to photometry only."""
        light_frame = FrameRecord(path="/lib/light.fits", role="LIGHT", filter="Luminance")
        target = Target(id="ClassifyLightTarget", frames=[light_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyLightTarget", ["/lib/light.fits"], None)

        orchestrator._run_photometry_analysis.assert_called_once()
        assert orchestrator._run_photometry_analysis.call_args.args[2] == ["/lib/light.fits"]
        orchestrator._run_spectroscopy_analysis.assert_not_called()

    def test_routes_spec_frame_to_spectroscopy_via_frame_record_filter(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify a SPEC-filter frame routes to spectroscopy only."""
        spec_frame = FrameRecord(path="/lib/spec.fits", role="LIGHT", filter="SPEC")
        target = Target(id="ClassifySpecTarget", frames=[spec_frame])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifySpecTarget", ["/lib/spec.fits"], None)

        orchestrator._run_spectroscopy_analysis.assert_called_once()
        assert orchestrator._run_spectroscopy_analysis.call_args.args[2] == ["/lib/spec.fits"]
        orchestrator._run_photometry_analysis.assert_not_called()

    def test_mixed_batch_runs_both_and_returns_combined_result(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
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

    def test_unmatched_path_falls_back_to_explicit_filter_type(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Verify an unmatched path falls back to the explicit filter_type."""
        target = Target(id="ClassifyUnmatchedTarget", frames=[])
        orchestrator = self._make_orchestrator_with_target(target)

        orchestrator._start_analysis_task("job1", "ClassifyUnmatchedTarget", ["/tmp/unmatched.fits"], "SPEC")

        orchestrator._run_spectroscopy_analysis.assert_called_once()
        orchestrator._run_photometry_analysis.assert_not_called()
