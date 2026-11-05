"""Tests that a stacking job tells a person which stage it is on.

A fake stack runs with every stage's work stubbed out. The tests check the
"Stage N of M" lines, the line for a skipped stage, the cadence of the
"Checked 10 of 40 frames" lines (with a fake clock) and the job's progress,
which must never go back and must end at 100.
"""

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.pipelines.stacking import stage, stage_progress
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import find_frames_to_quarantine
from astrometricslib.pipelines.stacking.stage_progress import (
    LOOP_LOG_EVERY_FRAMES,
    LOOP_LOG_EVERY_SECONDS,
    STACK_STAGES,
    LoopProgress,
    begin_stage,
    stage_window,
    track_stages,
)

PROGRESS_LOGGER = "astrometricslib.pipelines.stacking.stage_progress"


class FakeJob:
    """Stand-in for a job handle that remembers every progress report."""

    def __init__(self) -> None:
        """Start with no reports and a job that has not finished."""
        self.reached_terminal_status = False
        self.reports: list[tuple[str, int, str | None]] = []

    def mark(self, status: str, progress_current: int = 100, *, message: str | None = None) -> None:
        """Remember one progress report."""
        self.reports.append((status, progress_current, message))

    @property
    def percents(self) -> list[int]:
        """Give the reported progress values, in order."""
        return [percent for _, percent, _ in self.reports]


class FakeClock:
    """A clock that only moves when the test says so."""

    def __init__(self) -> None:
        """Start at time zero."""
        self.now = 0.0

    def __call__(self) -> float:
        """Give the current fake time.

        Returns
        -------
        now : `float`
            The fake time in seconds.
        """
        return self.now


def stage_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Give the messages of the stage lines in the log.

    Returns
    -------
    lines : `list` [`str`]
        The messages that start with "Stage ".
    """
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Stage ")]


def stub_steps(monkeypatch: pytest.MonkeyPatch, *, unchanged: bool, makes_stack: bool) -> None:
    """Replace the work of every stage with a stand-in that does nothing."""

    def choose(run: Any) -> None:
        """Offer 40 frames."""
        run.target_frames = list(range(40))

    def run_engine(run: Any) -> None:
        """Pretend the stacking program ran."""
        run.stacked_path = "/stack.fits" if makes_stack else None

    def nothing(*_args: object, **_kwargs: object) -> None:
        """Ignore the call: the stage's work is not under test."""

    monkeypatch.setattr(stage, "_choose_frames", choose)
    for name in (
        "_check_frame_types",
        "_name_output_file",
        "_screen_frames",
        "_prepare_stack",
        "_trim_stack_edges",
        "_point_target_at_stack",
        "_record_provenance",
        "_save_registration_record",
        "_store_stack_quality",
        "_make_preview",
        "_save_stack_inputs",
        "_log_finished_stack",
    ):
        monkeypatch.setattr(stage, name, nothing)
    monkeypatch.setattr(stage, "_stack_is_unchanged", lambda run: unchanged)
    monkeypatch.setattr(stage, "_run_stacking_engine", run_engine)
    monkeypatch.setattr(stage, "_summarize_stack", lambda run: SimpleNamespace())


def run_fake_stack(monkeypatch: pytest.MonkeyPatch, job: FakeJob) -> None:
    """Run the stack's list of steps as if it were the fake job."""
    monkeypatch.setattr(stage_progress, "get_current_job", lambda: job)
    stage._stack_frames_guarded(SimpleNamespace(id="M 31"))


def test_stage_weights_add_up_to_one_hundred() -> None:
    """The stage weights share the whole progress bar."""
    assert sum(stage_.weight for stage_ in STACK_STAGES) == 100


def test_every_stage_is_announced_in_order(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A stack that is made logs stage lines 1 to 7 in order."""
    stub_steps(monkeypatch, unchanged=False, makes_stack=True)
    job = FakeJob()
    with caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER):
        run_fake_stack(monkeypatch, job)

    lines = stage_lines(caplog)
    assert [line.split(":")[0] for line in lines] == [f"Stage {n} of 7" for n in range(1, 8)]
    assert "checking 40 frames before stacking" in lines[1]
    assert "Siril" in lines[3]


def test_progress_rises_and_ends_at_one_hundred(monkeypatch: pytest.MonkeyPatch) -> None:
    """Progress starts at 0, never goes back and ends at 100 on success."""
    stub_steps(monkeypatch, unchanged=False, makes_stack=True)
    job = FakeJob()
    run_fake_stack(monkeypatch, job)

    assert job.percents[0] == 0
    assert job.percents == sorted(job.percents)
    assert job.percents[-1] == 100
    assert len(set(job.percents)) > 5


def test_an_unchanged_stack_skips_the_later_stages(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """An up-to-date stack skips stages 4 to 7 and says why."""
    stub_steps(monkeypatch, unchanged=True, makes_stack=True)
    job = FakeJob()
    with caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER):
        run_fake_stack(monkeypatch, job)

    lines = stage_lines(caplog)
    assert len(lines) == 7
    assert lines[3] == "Stage 4 of 7: skipped, the stack is already up to date."
    assert lines[6] == "Stage 7 of 7: skipped, the stack is already up to date."
    assert job.percents == sorted(job.percents)
    assert job.percents[-1] == 100


def test_a_missing_stack_skips_the_quality_and_preview_stages(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """With no stack made, only the stages after the summary are skipped."""
    stub_steps(monkeypatch, unchanged=False, makes_stack=False)
    with caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER):
        run_fake_stack(monkeypatch, FakeJob())

    lines = stage_lines(caplog)
    assert lines[4].startswith("Stage 5 of 7: trimming")
    assert lines[5] == "Stage 6 of 7: skipped, Siril made no stack."
    assert lines[6] == "Stage 7 of 7: skipped, Siril made no stack."


def test_loop_lines_come_every_ten_frames(caplog: pytest.LogCaptureFixture) -> None:
    """A fast 40-frame loop writes a line at 10, 20, 30 and 40."""
    clock = FakeClock()
    with track_stages(FakeJob(), clock), caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER):
        progress = LoopProgress("Checked", 40)
        for _ in range(40):
            clock.now += 0.1
            progress.tick()

    messages = [r.getMessage() for r in caplog.records]
    assert messages == [f"Checked {n} of 40 frames…" for n in (10, 20, 30, 40)]
    assert LOOP_LOG_EVERY_FRAMES == 10


def test_loop_lines_come_every_thirty_seconds_when_frames_are_slow(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A slow loop writes a line after 30 seconds, before ten frames."""
    clock = FakeClock()
    with track_stages(FakeJob(), clock), caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER):
        progress = LoopProgress("Checked", 40)
        for _ in range(7):
            clock.now += LOOP_LOG_EVERY_SECONDS / 3
            progress.tick()

    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["Checked 3 of 40 frames…", "Checked 6 of 40 frames…"]


def test_loop_progress_moves_the_job_inside_its_stage() -> None:
    """A loop moves the job inside its stage, within its window."""
    job = FakeJob()
    clock = FakeClock()
    with track_stages(job, clock):
        begin_stage("screen_frames", "checking")
        start = job.percents[-1]
        with stage_window(0.0, 0.5):
            progress = LoopProgress("Checked", 20)
            for _ in range(20):
                progress.tick()

    assert start == 2
    assert job.percents == sorted(job.percents)
    # Half of the 25-point stage is covered by the window.
    assert job.percents[-1] == start + 12


def test_the_quarantine_measuring_loop_reports_progress(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The cloud and trailing check writes progress lines as it measures."""
    from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine

    frames = [SimpleNamespace(path=f"/f{i}.fits") for i in range(40)]
    monkeypatch.setattr(frame_quarantine, "split_into_batches", lambda frames_: [list(frames_)])
    monkeypatch.setattr(frame_quarantine, "judge_batch", lambda measurements: [])

    with caplog.at_level(logging.INFO, logger=PROGRESS_LOGGER), track_stages(FakeJob()):
        find_frames_to_quarantine(frames, lambda path: {"roundness": 0.9})

    checked = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Checked")]
    assert checked[-1] == "Checked 40 of 40 frames…"
    assert len(checked) >= 2
