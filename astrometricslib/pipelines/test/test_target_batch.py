"""Purpose: Unit tests for the many-target run in `pipelines/target_batch.py`.

Description: This module has real branching logic of its own -- classifying
a target as success, skipped, or failed, and reconciling worker counts -- so
it gets behavior tests rather than delegation-contract tests. The last tests
check that `ProcessingPipelines.process_target` hands a list of targets, or
every target, to it.
"""

from unittest.mock import MagicMock

import astrometricslib
from astrometricslib.api.processing import ProcessingPipelines
from astrometricslib.models.target import Target
from astrometricslib.pipelines import target_batch, tasks
from astrometricslib.pipelines.shared import frame_grouping
from astrometricslib.utilities import concurrency, parallel_batch


def _patch_astrometrics(monkeypatch, target) -> MagicMock:  # ruff: ignore[missing-type-function-argument]
    """Point astrometricslib.Astrometrics() at a mock with a fixed target.

    Returns
    -------
    astrometrics_instance : `MagicMock`
        The instance the worker will receive from `Astrometrics()`.
    """
    astrometrics_instance = MagicMock()
    astrometrics_instance.targets.get.return_value = target
    astrometrics_class = MagicMock(return_value=astrometrics_instance)
    monkeypatch.setattr(astrometricslib, "Astrometrics", astrometrics_class)
    return astrometrics_instance


class TestProcessSingleTargetWorker:
    """Behavior tests for the per-target worker's outcome classification."""

    def test_returns_failed_when_the_target_is_not_in_the_catalog(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a missing target is reported as a failure, not a crash."""
        _patch_astrometrics(monkeypatch, target=None)

        result = target_batch._process_single_target_worker("Missing", 2, "ASI294")

        assert result["status"] == "failed"
        assert result["error"] == "Target not found in catalog"

    def test_returns_skipped_when_no_frames_match_the_camera(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a target with no matching frames is skipped, not failed.

        Skipped is kept apart from failed so a batch run's success count
        only reflects targets that were actually processed.
        """
        target = Target(id="M13")
        _patch_astrometrics(monkeypatch, target=target)
        monkeypatch.setattr(frame_grouping, "select_frames_for_camera", lambda t, c: [])

        result = target_batch._process_single_target_worker("M13", 2, "ASI294")

        assert result["status"] == "skipped"
        assert "ASI294" in result["error"]

    def test_returns_success_and_the_pipeline_s_output_on_a_normal_run(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a normal run reports success and carries pipeline output."""
        target = Target(id="M13")
        astrometrics_instance = _patch_astrometrics(monkeypatch, target=target)
        monkeypatch.setattr(frame_grouping, "select_frames_for_camera", lambda t, c: ["a frame"])
        pipeline_mock = MagicMock(return_value={"astrometry": "ok"})
        monkeypatch.setattr(tasks, "run_full_pipeline", pipeline_mock)

        result = target_batch._process_single_target_worker("M13", 2, "ASI294", focal_length_mm=600.0)

        assert result["status"] == "success"
        assert result["stack_outputs"] == {"astrometry": "ok"}
        pipeline_mock.assert_called_once_with(
            target, astrometrics_instance, max_workers=2, camera_name="ASI294", focal_length_mm=600.0
        )

    def test_returns_failed_with_the_exception_message_when_the_pipeline_raises(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """Verify a pipeline exception is caught and reported, not raised.

        This runs inside a worker process, so an uncaught exception here
        would surface as an opaque worker crash instead of a per-item
        failure the batch summary can attribute to this target.
        """
        target = Target(id="M13")
        _patch_astrometrics(monkeypatch, target=target)
        monkeypatch.setattr(frame_grouping, "select_frames_for_camera", lambda t, c: ["a frame"])

        def _explode(*args: object, **kwargs: object) -> object:
            raise RuntimeError("pipeline blew up")

        monkeypatch.setattr(tasks, "run_full_pipeline", _explode)

        result = target_batch._process_single_target_worker("M13", 2, "ASI294")

        assert result["status"] == "failed"
        assert "RuntimeError" in result["error"]
        assert "pipeline blew up" in result["error"]

    def test_reports_the_exception_type_even_when_its_message_is_empty(self, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
        """A messageless exception must not collapse to a blank error.

        `str(exc)` is empty for an exception raised with no arguments
        (e.g. a bare Rust panic pyo3 converts into a Python exception),
        and `run_parallel_batch` treats a falsy "error" as "Unknown
        failure" -- discarding the only clue to what actually broke. This
        reproduces a real production incident where every target in a
        batch run came back as an undiagnosable "Unknown failure".
        """
        target = Target(id="M13")
        _patch_astrometrics(monkeypatch, target=target)
        monkeypatch.setattr(frame_grouping, "select_frames_for_camera", lambda t, c: ["a frame"])

        def _explode_with_no_message(*args: object, **kwargs: object) -> object:
            raise RuntimeError

        monkeypatch.setattr(tasks, "run_full_pipeline", _explode_with_no_message)

        result = target_batch._process_single_target_worker("M13", 2, "ASI294")

        assert result["status"] == "failed"
        assert result["error"]
        assert "RuntimeError" in result["error"]


class TestProcessTargetsInParallel:
    """Behavior tests for target selection and worker-count reconciliation."""

    def _make_pipelines(self, target_ids: list) -> ProcessingPipelines:
        """Build the processing API over a fake catalog and worker config.

        Returns
        -------
        pipelines : `ProcessingPipelines`
            The API, whose target catalog lists ``target_ids``.
        """
        config = MagicMock()
        config.get_target_workers.return_value = "auto"
        config.get_photometry_workers.return_value = "auto"
        config.get_worker_niceness.return_value = 5
        targets = MagicMock()
        targets.list.return_value = [Target(id=target_id) for target_id in target_ids]
        targets.get.side_effect = lambda target_id, refresh=False: Target(id=target_id)
        return ProcessingPipelines(config, MagicMock(), targets=targets)

    def test_defaults_to_every_catalog_target_when_target_is_none(self, monkeypatch: object) -> None:
        """Verify `target=None` processes the whole catalog."""
        pipelines = self._make_pipelines(["M13", "M31"])
        run_mock = MagicMock()
        monkeypatch.setattr(parallel_batch, "run_parallel_batch", run_mock)

        pipelines.process_target(None, camera_id="ASI294", register_job=False)

        called_item_ids = run_mock.call_args[0][0]
        assert called_item_ids == ["M13", "M31"]

    def test_uses_the_listed_targets_instead_of_the_full_catalog(self, monkeypatch: object) -> None:
        """Verify a list of targets is used as it is, unfiltered."""
        pipelines = self._make_pipelines(["M13", "M31"])
        run_mock = MagicMock()
        monkeypatch.setattr(parallel_batch, "run_parallel_batch", run_mock)

        pipelines.process_target(["M31"], camera_id="ASI294", register_job=False)

        called_item_ids = run_mock.call_args[0][0]
        assert called_item_ids == ["M31"]
        pipelines._targets.list.assert_not_called()

    def test_forwards_resolved_worker_counts_and_niceness(self, monkeypatch: object) -> None:
        """Verify config settings reach run_parallel_batch resolved.

        resolve_worker_counts is monkeypatched to a deterministic stub so
        this test only verifies the forwarding contract -- that the values
        returned by resolve_worker_counts are passed through correctly --
        without being affected by real system memory constraints on CI
        runners. Memory-based capping logic is tested separately in the
        concurrency module's own unit tests.
        """
        config = MagicMock()
        config.get_target_workers.return_value = "2"
        config.get_photometry_workers.return_value = "3"
        config.get_worker_niceness.return_value = 5
        monkeypatch.setattr(
            target_batch,
            "resolve_worker_counts",
            lambda outer, inner: concurrency.WorkerCounts(
                outer_worker_count=int(outer), inner_worker_count=int(inner)
            ),
        )
        run_mock = MagicMock(return_value="a summary")
        monkeypatch.setattr(parallel_batch, "run_parallel_batch", run_mock)

        result = target_batch.process_targets_in_parallel(
            config, ["M13"], camera_id="ASI294", focal_length_mm=600.0
        )

        assert result == "a summary"
        _, kwargs = run_mock.call_args
        assert kwargs["max_workers"] == 2
        assert kwargs["worker_arguments"] == (3, "ASI294", 600.0)
        assert kwargs["niceness"] == 5
