"""Tests that the measurement time reaches the stacking stage on every path.

Pipeline code never reads the clock, so each caller supplies `measured_at`.
These tests stub the stack itself and check that the full-pipeline path, the
batch worker, the batch entry and the rebuild script all hand a time down, and
that with the stack stubbed the stage's hook stores frame quality rows.
"""

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines import target_batch, tasks
from astrometricslib.pipelines.stacking import stage as stacking_stage
from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import QuarantineReport
from astrometricslib.scripts import rebuild_stacks

MEASURED_AT = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)


def _target(tmp_path: Path) -> Target:
    """Build a target with one light frame that exists on disk.

    Returns
    -------
    target : `Target`
        The target.
    """
    path = tmp_path / "lights" / "M 99" / "frame.fits"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    return Target(
        id="M 99",
        frames=[FrameRecord(path=str(path), camera="Cam", focalLengthMm=400.0, timestamp=1_792_000_000.0)],
    )


def _recording(tmp_path: Path) -> FrameQualityRecording:
    """Build the recording context a caller of the stage supplies.

    Returns
    -------
    recording : `FrameQualityRecording`
        The test configuration and a library with no bias frames.
    """
    from astrometricslib.foundation.config import get_configuration

    library = SimpleNamespace(
        select_bias_frames=lambda *a, **k: SimpleNamespace(frames=[], flags=[], blocking_flags=[])
    )
    return FrameQualityRecording(configuration=get_configuration(), calibration_library=library)


def _stack_that_records_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[list[datetime | None], QualityStore]:
    """Replace the stage's stack with one that only runs the quality hook.

    Returns
    -------
    received : `list` [`datetime` or `None`]
        The `measured_at` each stack call received.
    store : `QualityStore`
        The store the hook writes to.
    """
    received: list[datetime | None] = []
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)

    def fake_stack(
        target: Target,
        measured_at: datetime | None = None,
        quality_recording: FrameQualityRecording | None = None,
        **_options: object,
    ) -> str:
        """Run only the stage's frame quality hook.

        Returns
        -------
        path : `str`
            A made-up stack path.
        """
        received.append(measured_at)
        report = QuarantineReport(
            measurements=[{"path": f.path, "roundness": 0.9, "sky_median_adu": 500.0} for f in target.frames]
        )
        stacking_stage._record_frame_quality(
            target, [], report, list(target.frames), measured_at, quality_recording
        )
        return "/fake/stack.fits"

    monkeypatch.setattr(stacking_stage, "stack_frames", fake_stack)
    return received, QualityStore(str(tmp_path / "astrometrics.db"))


def test_the_full_pipeline_path_stores_frame_quality(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`stack_frames_with_timeout` hands the time to the stage."""
    received, store = _stack_that_records_quality(tmp_path, monkeypatch)

    tasks.stack_frames_with_timeout(
        _target(tmp_path), [], measured_at=MEASURED_AT, quality_recording=_recording(tmp_path)
    )

    assert received == [MEASURED_AT]
    assert len(store.query(metric="frame.quarantine_roundness")) == 1


def test_the_rebuild_script_path_stores_frame_quality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`rebuild_target` supplies a time-zone-aware time of its own."""
    received, store = _stack_that_records_quality(tmp_path, monkeypatch)
    target = _target(tmp_path)
    astrometrics = SimpleNamespace(
        targets=SimpleNamespace(get=lambda name: target, save=MagicMock()),
        config=SimpleNamespace(get_frames_path=lambda: tmp_path, get_library_path=lambda: tmp_path),
        processing=SimpleNamespace(
            calibration=SimpleNamespace(library=_recording(tmp_path).calibration_library)
        ),
    )

    rebuild_stacks.rebuild_target(astrometrics, "M 99", str(tmp_path / "backup"))

    assert len(received) == 1 and received[0] is not None and received[0].tzinfo is not None
    assert len(store.query(metric="frame.quarantine_roundness")) == 1


def test_the_batch_worker_passes_the_time_to_the_full_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    """The worker hands `measured_at` to `run_full_pipeline`."""
    pipeline = MagicMock(return_value={})
    monkeypatch.setattr(tasks, "run_full_pipeline", pipeline)
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.target_records.find_stored_target",
        lambda access, target_id: object(),
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.frame_grouping.select_frames_for_camera", lambda target, camera: [1]
    )
    monkeypatch.setattr("astrometricslib.drivers.catalog_access.CatalogAccess", lambda config: object())
    monkeypatch.setattr(
        "astrometricslib.drivers.calibration_library.CalibrationLibrary", lambda app_config: "library"
    )

    target_batch._process_single_target_worker("M 99", 2, "Cam", measured_at=MEASURED_AT)

    assert pipeline.call_args.kwargs["measured_at"] == MEASURED_AT
    assert pipeline.call_args.kwargs["quality_recording"].calibration_library == "library"


def test_the_batch_entry_sends_the_time_to_every_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The batch entry puts `measured_at` in the worker arguments."""
    run = MagicMock()
    monkeypatch.setattr(target_batch.parallel_batch, "run_parallel_batch", run)
    config = SimpleNamespace(
        get_target_workers=lambda: 1, get_photometry_workers=lambda: 1, get_worker_niceness=lambda: 0
    )

    target_batch.process_targets_in_parallel(config, ["M 99"], camera_id="Cam", measured_at=MEASURED_AT)

    assert run.call_args.kwargs["worker_arguments"][-1] == MEASURED_AT


def test_the_api_supplies_the_time_for_a_batch_of_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """`process_target` for several targets passes a time-zone-aware time."""
    from astrometricslib.api.processing import ProcessingPipelines

    batch = MagicMock()
    monkeypatch.setattr(target_batch, "process_targets_in_parallel", batch)
    pipelines = ProcessingPipelines(
        MagicMock(), MagicMock(), targets=SimpleNamespace(get=lambda name, refresh=False: Target(id=name))
    )

    pipelines.process_target(["M 99"], camera_id="Cam", register_job=False)

    measured_at = batch.call_args.kwargs["measured_at"]
    assert measured_at.tzinfo is not None
