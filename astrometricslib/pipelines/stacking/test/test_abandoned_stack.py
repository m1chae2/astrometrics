"""Tests that giving up on a stack stops its Siril and frees its job slot.

`tasks.stack_frames_with_timeout` runs a stack in a background thread. When
it gives up, the thread used to go on: its Siril kept running and the thread
kept its slot, which held up every other job. These tests use a real
short-lived child process as a stand-in for Siril (a Python process that
sleeps) and check, with the real slot files, that the slot is free after the
stack is given up on, and that nothing the thread does afterward is recorded.
"""

import os
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers import siril_interface
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib._internal.drivers.siril_interface import session as siril_session
from astrometricslib._internal.drivers.siril_interface.imaging_stacker import SirilImagingStacker
from astrometricslib.foundation.paths import locks_directory
from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    raise_if_abandoned,
)
from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.pipelines import tasks
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.frame_measurement_run import FrameQualityRecording

# A Siril stand-in: Python ignores the extra Siril options that follow its
# `-c` code, and sleeps far longer than any test waits.
FAKE_SIRIL_COMMAND = f'{sys.executable} -c "import time; time.sleep(60)"'


class _Target:
    """A target with only the fields the timeout code reads."""

    id = "NGC 1499"
    stacking = SimpleNamespace(quality_summary=None)


def make_fake_siril_session() -> siril_session.SirilSession:
    """Make a session that launches the sleeping stand-in.

    Returns
    -------
    session : `SirilSession`
        A session with only the settings `run_siril_headless` reads.
    """
    session = siril_session.SirilSession.__new__(siril_session.SirilSession)
    session.siril_executable = FAKE_SIRIL_COMMAND
    session.subprocesses = []
    return session


def slot_can_be_taken(seconds: float = 10.0) -> bool:
    """Say whether another job can take the only job slot soon.

    Returns
    -------
    taken : `bool`
        `True` if a slot request finished within the time.
    """
    finished = threading.Event()

    def take_slot() -> None:
        """Take and give back the slot."""
        with acquire_resource_slot(None, "job", 1, poll_interval_seconds=0.05):
            pass
        finished.set()

    threading.Thread(target=take_slot, daemon=True).start()
    return finished.wait(timeout=seconds)


def test_giving_up_on_a_stack_stops_its_siril_and_frees_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Kill the stand-in Siril, free the slot and keep no late result."""
    session = make_fake_siril_session()
    finished_normally: list[str] = []

    def stack_with_fake_siril(target: object, *_: object, **__: object) -> str:
        """Hold a slot and wait on a Siril that never finishes by itself.

        Returns
        -------
        stacked_path : `str`
            A path that must never be recorded.
        """
        with siril_interface.siril_process_lock(max_concurrent_runs=1, work_name="NGC 1499"):
            process = session.run_siril_headless("unused_command_pipe", "unused_output_pipe")
            process.wait()
        raise_if_abandoned()
        finished_normally.append("late result")
        return "/late/result.fits"

    monkeypatch.setattr(tasks, "_stack_with_job_tracking", stack_with_fake_siril)
    monkeypatch.setattr(tasks, "_STACKING_TIMEOUT_POLL_SECONDS", 0.05)

    result = tasks.stack_frames_with_timeout(_Target(), [], timeout_seconds=0.5)

    assert result is None
    assert len(session.subprocesses) == 1
    assert session.subprocesses[0].poll() is not None, "the stand-in Siril is still running"
    assert slot_can_be_taken(), "the abandoned stack still holds its slot"
    assert finished_normally == []
    holder_path = os.path.join(locks_directory(), "job_slot_0.holder.json")
    assert not os.path.exists(holder_path)


def test_a_stack_given_up_on_before_siril_started_never_starts_siril(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A thread that reaches the Siril launch after the give-up is refused."""
    session = make_fake_siril_session()
    abandonment = RunAbandonment()
    abandonment.abandon()

    with bind_abandonment(abandonment), pytest.raises(RunAbandonedError):
        session.run_siril_headless("unused_command_pipe", "unused_output_pipe")

    assert session.subprocesses == []
    assert abandonment.registered_process_count == 0


def test_a_run_given_up_on_does_not_queue_for_a_slot() -> None:
    """Refuse an abandoned run's slot request, even with the slot busy."""
    abandonment = RunAbandonment()
    abandonment.abandon()
    with acquire_resource_slot(None, "job", 1):
        # The only slot is held here (by this thread, so a plain request
        # would be reused); ask from a thread with its own context.
        outcome: list[str] = []

        def ask() -> None:
            """Ask for the slot as the abandoned run."""
            with bind_abandonment(abandonment):
                try:
                    with siril_interface.siril_process_lock(max_concurrent_runs=1):
                        outcome.append("got the slot")
                except RunAbandonedError:
                    outcome.append("refused")

        thread = threading.Thread(target=ask, daemon=True)
        thread.start()
        thread.join(timeout=5.0)

    assert outcome == ["refused"]


def test_process_target_lets_the_give_up_error_reach_the_caller() -> None:
    """The stacker does not turn the give-up into an ordinary failure."""
    stacker = SirilImagingStacker.__new__(SirilImagingStacker)
    cleaned_up: list[bool] = []
    fake_run = SimpleNamespace(job_logger=SimpleNamespace(exception=lambda *_: None))

    def refuse_to_launch(*_: object) -> None:
        """Stand in for a launch that finds the run given up on.

        Raises
        ------
        RunAbandonedError
            Always.
        """
        raise RunAbandonedError("given up on")

    stacker._begin_run = lambda request: fake_run  # type: ignore[method-assign]
    stacker._stage_inputs = lambda run: object()  # type: ignore[method-assign]
    stacker._launch_siril = refuse_to_launch  # type: ignore[method-assign]
    stacker._clean_up_run = lambda run: cleaned_up.append(True)  # type: ignore[method-assign]

    with pytest.raises(RunAbandonedError):
        stacker.process_target(SimpleNamespace())

    assert cleaned_up == [True]


def test_a_stack_that_finishes_after_the_give_up_is_not_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage raises and puts the old stack back instead of going on."""
    abandonment = RunAbandonment()
    archive_results: list[bool] = []
    stage_run = SimpleNamespace(
        engine=object(),
        target=SimpleNamespace(id="NGC 1499"),
        stack_file="stack.fits",
        log_file=None,
        target_frames=[],
        has_spectral=False,
        job_id=None,
        rejection_sigma=None,
        filter_wfwhm=None,
        filter_round=None,
        stack_weight=None,
        generate_rejmap=None,
    )
    monkeypatch.setattr(stage, "_archive_stack_before_restack", lambda *_: "staging")
    monkeypatch.setattr(
        stage,
        "_finish_stack_archive",
        lambda staging, stack_file, target_id, kept: archive_results.append(kept),
    )
    monkeypatch.setattr(stage, "_report_stage", lambda *_: None)

    def late_stack(*_: object, **__: object) -> tuple[str, dict]:
        """Stand in for a stack finished just as it was given up on.

        Returns
        -------
        stacked_path, diagnostics : `tuple`
            A path and empty diagnostics.
        """
        abandonment.abandon()
        return "/late/result.fits", {}

    monkeypatch.setattr("astrometricslib.pipelines.stacking.stack_runner.run_stack", late_stack)

    with bind_abandonment(abandonment), pytest.raises(RunAbandonedError):
        stage._run_stacking_engine(stage_run)

    assert archive_results == [False]


def test_an_abandoned_stack_is_recorded_as_a_processing_decision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The timeout stores a decision row and still sets `flagged` for readers.

    `flagged` is dropped in a later phase, so the summary keeps its flag and
    its reason while the quality store gains the row.
    """
    release = threading.Event()

    def hung_stack(*_: object, **__: object) -> str:
        """Stand in for a stack that records a job, then hangs until released.

        Returns
        -------
        stacked_path : `str`
            A path that is never used.
        """
        from astrometricslib.foundation.jobs import runner

        runner._job_started_listener.get()(SimpleNamespace(job_id="job-stack-1"))
        release.wait(30)
        return "/never/reached.fits"

    monkeypatch.setattr(tasks, "_stack_with_job_tracking", hung_stack)
    monkeypatch.setattr(tasks, "_STACKING_TIMEOUT_POLL_SECONDS", 0.05)
    summary = SimpleNamespace(
        flagged=False, flag_reasons=[], stacking_metrics=SimpleNamespace(timed_out=False)
    )
    target = SimpleNamespace(id="NGC 1499", stacking=SimpleNamespace(quality_summary=summary))
    configuration = SimpleNamespace(get_library_path=lambda: tmp_path)
    recording = FrameQualityRecording(configuration=configuration, calibration_library=object())

    try:
        result = tasks.stack_frames_with_timeout(
            target,
            [],
            timeout_seconds=1,
            measured_at=datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
            quality_recording=recording,
        )
    finally:
        release.set()

    assert result is None
    assert summary.flagged is True
    assert summary.flag_reasons == ["stacking timed out after 1s"]
    rows = QualityStore(str(tmp_path / "astrometrics.db")).query(metric="stacking.abandoned_after_s")
    assert len(rows) == 1
    assert rows[0].subject.level is SubjectLevel.RUN
    assert rows[0].subject.target_id == "NGC 1499"
    assert rows[0].value is not None
    assert rows[0].value >= 0.9
    assert rows[0].action_taken == "stack abandoned after its 1 s time budget"
    # The row is a warning: a failed check against the time budget.
    assert rows[0].check_outcome.value == "failed"
    assert rows[0].check_limit.value == pytest.approx(1.0)
    assert rows[0].reason == "stacking timed out after 1s"
    # The timeout is a warning of the stacking job that was abandoned.
    assert rows[0].job_id == "job-stack-1"
