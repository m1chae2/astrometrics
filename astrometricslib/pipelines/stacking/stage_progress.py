"""Purpose: Show which step a stacking job is on, and how far along it is.

Description: A stack takes minutes, and most of that time is spent in a few
long steps. Without a sign of life a person cannot tell a slow stack from a
stuck one. This module gives the stacking stage three things:

- A fixed list of stages (`STACK_STAGES`). `begin_stage` writes one plain
  line, such as ``Stage 2 of 7: checking 40 frames before stacking...``, and
  moves the job's progress to where that stage starts. A stage that does not
  run is announced with `skip_stage` or `skip_remaining_stages`, so the
  numbering stays the same.
- A weight for each stage. The weights add up to 100 and are rough guesses of
  each stage's share of the run time. They only decide how the job's progress
  bar moves.
- `LoopProgress`, for loops over frames. It writes ``Checked 10 of 40
  frames...`` every 10 frames or every 30 seconds, whichever comes first, and
  moves the job's progress inside the stage that is running.

Progress goes through the job handle's `mark` method, the same way as every
other job (see `foundation/jobs/runner.py`). When the stack does not run as a
tracked job, the log lines are still written and the progress is not
reported. When no stage tracking is active at all (a test, or a caller that
runs one step alone), every function here does nothing.
"""

import logging
import time
from collections.abc import Callable, Generator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from astrometricslib.foundation.jobs.runner import JobHandle, get_current_job

logger = logging.getLogger(__name__)

__all__ = [
    "CHOOSE_FRAMES_STAGE",
    "LOOP_LOG_EVERY_FRAMES",
    "LOOP_LOG_EVERY_SECONDS",
    "PREPARE_STAGE",
    "PREVIEW_STAGE",
    "QUALITY_STAGE",
    "SCREEN_FRAMES_STAGE",
    "STACKING_STAGE",
    "STACK_STAGES",
    "SUMMARIZE_STAGE",
    "LoopProgress",
    "StackStage",
    "StageTracker",
    "begin_stage",
    "finish_stages",
    "skip_remaining_stages",
    "skip_stage",
    "stage_window",
    "track_stages",
]

#: A loop over frames writes a progress line after this many frames. Ten
#: frames is a few seconds of work for the quick checks and about a minute
#: for the slow ones, so a person sees a line often enough to know the job
#: is alive without the log filling up.
LOOP_LOG_EVERY_FRAMES = 10

#: A loop also writes a progress line when this many seconds have passed
#: since its last line, so a loop whose frames are slow (a network drive)
#: still shows a sign of life.
LOOP_LOG_EVERY_SECONDS = 30.0

#: Keys of the stages, in the order they run.
CHOOSE_FRAMES_STAGE = "choose_frames"
SCREEN_FRAMES_STAGE = "screen_frames"
PREPARE_STAGE = "prepare"
STACKING_STAGE = "stacking"
SUMMARIZE_STAGE = "summarize"
QUALITY_STAGE = "quality"
PREVIEW_STAGE = "preview"


@dataclass(frozen=True)
class StackStage:
    """One stage of a stacking job and its share of the progress bar.

    Parameters
    ----------
    key : `str`
        The stage's name, used to begin or skip it.
    weight : `int`
        The stage's share of the 0 to 100 progress bar. A rough guess at the
        share of the run time, not a measurement.
    """

    key: str
    weight: int


#: The stages of a stacking job, in order. The weights are rough guesses
#: from the run times seen on a 40-frame target (the frame checks read every
#: frame; Siril is the longest single step; the preview runs three programs).
#: They were not fitted, and only shape how the progress bar moves.
STACK_STAGES: tuple[StackStage, ...] = (
    StackStage(CHOOSE_FRAMES_STAGE, 2),
    StackStage(SCREEN_FRAMES_STAGE, 25),
    StackStage(PREPARE_STAGE, 3),
    StackStage(STACKING_STAGE, 45),
    StackStage(SUMMARIZE_STAGE, 5),
    StackStage(QUALITY_STAGE, 10),
    StackStage(PREVIEW_STAGE, 10),
)

#: The tracker of the stack running in this context, if any.
_active_tracker: ContextVar[StageTracker | None] = ContextVar("stack_stage_tracker", default=None)


class StageTracker:
    """Keep the stage the stack is on and report progress to the job.

    Parameters
    ----------
    job : `JobHandle` or `None`
        The job to report progress to. `None` writes log lines only.
    clock : `Callable`, optional
        Gives the time in seconds. Tests pass a fake clock.
    stages : `tuple` [`StackStage`], optional
        The stages, in order.
    """

    def __init__(
        self,
        job: JobHandle | None,
        clock: Callable[[], float] = time.monotonic,
        stages: tuple[StackStage, ...] = STACK_STAGES,
    ) -> None:
        """Start before the first stage, with no progress reported."""
        self.job = job
        self.clock = clock
        self.stages = stages
        self.current_index = -1
        self.window_start = 0.0
        self.window_span = 1.0
        self.reported_progress = 0

    def _index_of(self, key: str) -> int:
        """Give the position of a stage in the list.

        Returns
        -------
        index : `int`
            Zero for the first stage.

        Raises
        ------
        KeyError
            If no stage has that key.
        """
        for index, stage in enumerate(self.stages):
            if stage.key == key:
                return index
        raise KeyError(f"Unknown stack stage '{key}'")

    def _percent_before(self, index: int) -> float:
        """Give the progress at which a stage starts.

        Returns
        -------
        percent : `float`
            The summed weights of the stages before it.
        """
        return float(sum(stage.weight for stage in self.stages[:index]))

    def _report(self, percent: float, message: str) -> None:
        """Move the job's progress forward, never backward.

        Parameters
        ----------
        percent : `float`
            The wanted progress, out of 100.
        message : `str`
            The job's status line.
        """
        self.reported_progress = max(self.reported_progress, min(100, int(percent)))
        job = self.job
        if job is not None and not job.reached_terminal_status:
            job.mark("running", self.reported_progress, message=message)

    def begin(self, key: str, text: str) -> None:
        """Announce that a stage starts and move the progress to its start.

        Parameters
        ----------
        key : `str`
            The stage.
        text : `str`
            What the stage does, in lower case without a stage number, such
            as ``checking 40 frames before stacking``.
        """
        index = self._index_of(key)
        self.current_index = index
        self.window_start, self.window_span = 0.0, 1.0
        message = f"Stage {index + 1} of {len(self.stages)}: {text}…"
        logger.info("%s", message)
        self._report(self._percent_before(index), message)

    def skip(self, key: str, reason: str) -> None:
        """Announce that a stage does not run, and why.

        Parameters
        ----------
        key : `str`
            The stage.
        reason : `str`
            Why it is skipped, such as ``the stack is already up to date``.
        """
        index = self._index_of(key)
        message = f"Stage {index + 1} of {len(self.stages)}: skipped, {reason}."
        logger.info("%s", message)
        self.current_index = max(self.current_index, index)
        self._report(self._percent_before(index + 1), message)

    def skip_remaining(self, reason: str) -> None:
        """Announce that every stage after the current one is skipped.

        Parameters
        ----------
        reason : `str`
            Why they are skipped.
        """
        for stage in self.stages[self.current_index + 1 :]:
            self.skip(stage.key, reason)

    def finish(self) -> None:
        """Report that every stage is done (progress 100)."""
        self._report(100, "Stack finished")

    def report_within_stage(self, fraction: float, message: str) -> None:
        """Move the progress inside the current stage.

        Parameters
        ----------
        fraction : `float`
            How far through the current window of the stage, 0 to 1.
        message : `str`
            The job's status line.
        """
        if self.current_index < 0:
            return
        stage = self.stages[self.current_index]
        inside = self.window_start + self.window_span * max(0.0, min(1.0, fraction))
        self._report(self._percent_before(self.current_index) + stage.weight * inside, message)


@contextmanager
def track_stages(
    job: JobHandle | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> Generator[StageTracker]:
    """Track the stages of one stacking job while the block runs.

    Parameters
    ----------
    job : `JobHandle` or `None`, optional
        The job to report to. Defaults to the job the calling work runs as.
    clock : `Callable`, optional
        Gives the time in seconds. Tests pass a fake clock.

    Yields
    ------
    tracker : `StageTracker`
        The tracker, active for the block.
    """
    tracker = StageTracker(job if job is not None else get_current_job(), clock)
    token = _active_tracker.set(tracker)
    try:
        yield tracker
    finally:
        _active_tracker.reset(token)


def begin_stage(key: str, text: str) -> None:
    """Announce a stage of the active stack. Does nothing without one.

    Parameters
    ----------
    key : `str`
        The stage (one of the ``*_STAGE`` constants).
    text : `str`
        What the stage does, in lower case.
    """
    tracker = _active_tracker.get()
    if tracker is not None:
        tracker.begin(key, text)


def skip_stage(key: str, reason: str) -> None:
    """Announce that a stage of the active stack is skipped.

    Parameters
    ----------
    key : `str`
        The stage.
    reason : `str`
        Why it is skipped.
    """
    tracker = _active_tracker.get()
    if tracker is not None:
        tracker.skip(key, reason)


def skip_remaining_stages(reason: str) -> None:
    """Announce that all later stages of the active stack are skipped.

    Parameters
    ----------
    reason : `str`
        Why they are skipped.
    """
    tracker = _active_tracker.get()
    if tracker is not None:
        tracker.skip_remaining(reason)


def finish_stages() -> None:
    """Report that the active stack has done all its stages."""
    tracker = _active_tracker.get()
    if tracker is not None:
        tracker.finish()


@contextmanager
def stage_window(start: float, span: float) -> Generator[None]:
    """Give the loops inside the block a part of the current stage.

    A stage with several long loops (the frame checks) gives each loop its
    own part, so the bar moves forward through all of them.

    Parameters
    ----------
    start : `float`
        Where the part starts in the stage, 0 to 1.
    span : `float`
        How much of the stage the part covers, 0 to 1.

    Yields
    ------
    None
    """
    tracker = _active_tracker.get()
    if tracker is None:
        yield
        return
    previous = (tracker.window_start, tracker.window_span)
    tracker.window_start, tracker.window_span = start, span
    try:
        yield
    finally:
        tracker.window_start, tracker.window_span = previous


class LoopProgress:
    """Write progress lines for a loop over frames.

    A line is written after `LOOP_LOG_EVERY_FRAMES` frames or
    `LOOP_LOG_EVERY_SECONDS` seconds since the last one, whichever comes
    first, and one more when the loop ends. Each line also moves the job's
    progress inside the current stage.

    Parameters
    ----------
    verb : `str`
        The start of each line, such as ``Checked``.
    total : `int`
        How many frames the loop goes through.
    noun : `str`, optional
        What is counted. Defaults to ``frames``.
    """

    def __init__(self, verb: str, total: int, noun: str = "frames") -> None:
        """Start counting from zero at the tracker's clock time."""
        self.verb = verb
        self.total = total
        self.noun = noun
        self.done = 0
        self.last_logged_done = 0
        tracker = _active_tracker.get()
        self.tracker = tracker
        self.clock: Callable[[], float] = tracker.clock if tracker is not None else time.monotonic
        self.last_logged_time = self.clock()

    def tick(self) -> None:
        """Count one more frame, and write a line when one is due."""
        self.done += 1
        now = self.clock()
        due = (
            self.done - self.last_logged_done >= LOOP_LOG_EVERY_FRAMES
            or now - self.last_logged_time >= LOOP_LOG_EVERY_SECONDS
            or self.done >= self.total
        )
        if not due:
            return
        message = f"{self.verb} {self.done} of {self.total} {self.noun}…"
        logger.info("%s", message)
        if self.tracker is not None and self.total > 0:
            self.tracker.report_within_stage(self.done / self.total, message)
        self.last_logged_done = self.done
        self.last_logged_time = now
