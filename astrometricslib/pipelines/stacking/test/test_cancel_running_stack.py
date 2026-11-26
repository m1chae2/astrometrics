"""Tests that Cancel stops a stack that is already running, and frees its slot.

A cancel used to work only while a job waited for a slot. These tests check the
running stages with fake work: the frame check stops within one frame and
starts no new frames, a program that is running (a sleeping Python process
standing in for Siril, GraXpert or Cosmic Clarity) is ended within about a
second, the earlier stack and the earlier preview pictures stay as they were,
and the job slot is free afterward.
"""

import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.run_abandonment import (
    RunAbandonedError,
    RunAbandonment,
    bind_abandonment,
    run_registered,
)
from astrometricslib.foundation.storage.process_locks import acquire_resource_slot
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing import preview_remake
from astrometricslib.pipelines.stacking.post_processing import stack_preview as preview_module
from astrometricslib.pipelines.stacking.post_processing.preview_siril import run_preview_script
from astrometricslib.pipelines.stacking.post_processing.previous_stack import archive_current_stack
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine
from astrometricslib.pipelines.stacking.stage_progress import LoopProgress, begin_stage, track_stages
from astrometricslib.utilities.stack_preview_path import preview_path_for, processed_fits_path_for

# A stand-in for any external program: a Python process that sleeps far longer
# than a test waits.
SLEEPING_PROGRAM_COMMAND = f'{sys.executable} -c "import time; time.sleep(60)"'

# How long one fake frame takes, and how many frames there are. Forty frames
# of 0.3 s on four workers would take 3 s if nothing stopped them.
FAKE_FRAME_SECONDS = 0.3
FAKE_FRAME_COUNT = 40


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


def test_cancel_during_the_frame_check_stops_within_one_frame_and_starts_no_new_frames() -> None:
    """Stop the frame check after the frames in progress."""
    frames = [SimpleNamespace(path=f"/frames/{index}.fits") for index in range(FAKE_FRAME_COUNT)]
    start_times: list[float] = []
    start_lock = threading.Lock()

    def slow_measure(_path: str) -> dict:
        """Take a while to measure one frame.

        Returns
        -------
        measurement : `dict`
            An empty measurement.
        """
        with start_lock:
            start_times.append(time.monotonic())
        time.sleep(FAKE_FRAME_SECONDS)
        return {}

    abandonment = RunAbandonment()
    cancel_time: list[float] = []

    def cancel_after_a_moment() -> None:
        """Cancel the run while the first frames are being measured."""
        time.sleep(0.4)
        cancel_time.append(time.monotonic())
        abandonment.abandon()

    threading.Thread(target=cancel_after_a_moment, daemon=True).start()
    started = time.monotonic()
    with bind_abandonment(abandonment), pytest.raises(RunAbandonedError):
        frame_quarantine._measure_frames(frames, slow_measure)
    stopped_after = time.monotonic() - started

    # The cancel came at 0.4 s. The check noticed it within the poll step and
    # did not wait for the frames in progress to be started again.
    assert stopped_after < 0.4 + 0.5 + 0.2
    assert len(start_times) < FAKE_FRAME_COUNT
    time.sleep(FAKE_FRAME_SECONDS + 0.2)
    started_after_cancel = [moment for moment in start_times if moment > cancel_time[0] + 0.05]
    assert started_after_cancel == [], "a frame was started after the cancel"


def test_loop_progress_and_stage_start_stop_a_cancelled_job() -> None:
    """Stop at the next frame and before the next stage."""
    abandonment = RunAbandonment()
    with bind_abandonment(abandonment), track_stages(None):
        progress = LoopProgress("Checked", 5)
        progress.tick()
        abandonment.abandon()
        with pytest.raises(RunAbandonedError):
            progress.tick()
        with pytest.raises(RunAbandonedError):
            begin_stage("prepare", "preparing")


def test_cancel_while_an_external_program_runs_ends_it_and_frees_the_slot(
    tmp_path: Path,
) -> None:
    """End the preview's program and free the slot."""
    abandonment = RunAbandonment()
    outcome: list[str] = []

    def run_preview_program() -> None:
        """Run the stand-in program like the preview's Siril run does."""
        with bind_abandonment(abandonment):
            try:
                with acquire_resource_slot(None, "job", 1, poll_interval_seconds=0.05):
                    # The real function, not the suite stand-in.
                    run_preview_script(str(tmp_path), ["noop"], SLEEPING_PROGRAM_COMMAND)
                    outcome.append("returned")
            except RunAbandonedError:
                outcome.append("abandoned")
            except (OSError, ValueError) as error:  # reported by the assertion
                outcome.append(repr(error))

    worker = threading.Thread(target=run_preview_program, daemon=True)
    worker.start()
    time.sleep(0.5)
    assert worker.is_alive(), outcome
    cancel_started = time.monotonic()
    abandonment.abandon()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert time.monotonic() - cancel_started < 1.5
    assert outcome == ["abandoned"]
    assert slot_can_be_taken(), "the cancelled job still holds its slot"


def test_run_registered_raises_within_a_second_of_the_cancel() -> None:
    """End a program run to the end and raise at once."""
    abandonment = RunAbandonment()
    threading.Timer(0.3, abandonment.abandon).start()
    started = time.monotonic()
    with bind_abandonment(abandonment), pytest.raises(RunAbandonedError):
        run_registered([sys.executable, "-c", "import time; time.sleep(60)"], 120.0)
    assert time.monotonic() - started < 1.5


def test_cancel_while_stacking_puts_the_earlier_stack_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Keep the earlier stack as it was when the stacker is cancelled."""
    stack_file = tmp_path / "stack.fits"
    stack_file.write_bytes(b"earlier stack")
    monkeypatch.setattr(
        stage,
        "_archive_stack_before_restack",
        lambda _target_id, output_file: archive_current_stack(output_file),
    )

    def cancelled_stacker(*_: object, **__: object) -> None:
        """Leave half a stack behind and report the cancel.

        Raises
        ------
        RunAbandonedError
            Always.
        """
        stack_file.write_bytes(b"half a stack")
        raise RunAbandonedError("cancelled")

    monkeypatch.setattr("astrometricslib.pipelines.stacking.stack_runner.run_stack", cancelled_stacker)
    run = SimpleNamespace(
        engine=object(),
        target=SimpleNamespace(id="T 1"),
        target_frames=[],
        stack_file=str(stack_file),
        log_file=None,
        has_spectral=False,
        job_id=None,
        drivers=None,
        rejection_sigma=None,
        filter_wfwhm=None,
        filter_round=None,
        stack_weight=None,
        generate_rejmap=None,
    )

    with pytest.raises(RunAbandonedError):
        stage._run_stacking_engine(run)

    assert stack_file.read_bytes() == b"earlier stack"


def test_cancel_during_a_preview_remake_puts_the_old_pictures_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Restore the old pictures when a remake is cancelled partway."""
    stack_path = str(tmp_path / "stack.fits")
    Path(stack_path).write_bytes(b"stack")
    old_picture = Path(preview_path_for(stack_path))
    old_picture.write_bytes(b"old picture")
    old_fits = Path(processed_fits_path_for(stack_path))
    old_fits.write_bytes(b"old fits")

    def cancelled_preview(path: str, *_: object, **__: object) -> None:
        """Remove the pictures, then be cancelled.

        Raises
        ------
        RunAbandonedError
            Always.
        """
        old_picture.unlink()
        old_fits.unlink()
        raise RunAbandonedError("cancelled")

    monkeypatch.setattr(preview_remake, "write_stack_preview", cancelled_preview)
    monkeypatch.setattr(preview_remake, "anchor_for_target", lambda _target: None)
    target = SimpleNamespace(
        id="T 1",
        object_category=None,
        stacking=SimpleNamespace(stacked_image=stack_path),
        spectral_stacking=SimpleNamespace(stacked_image=None),
    )

    with pytest.raises(RunAbandonedError):
        preview_remake.remake_stack_preview(target, False, preview_module.PreviewSettings())

    assert old_picture.read_bytes() == b"old picture"
    assert old_fits.read_bytes() == b"old fits"
