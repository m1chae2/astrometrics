"""The time budget that keeps one slow frame from stalling a batch.

The budget counts the CPU time of the measuring thread, not the wall clock, so
time spent waiting for Python's global lock, for the disk or for other programs
does not use it up. It is checked between the steps; when it has run out, the
remaining slow steps (the star detections and fits) come back
``not_measured`` and the quick header and background metrics are still
returned.
"""

import time

# How much CPU time one frame may use before the slow steps are skipped, in
# seconds. It counts the CPU time of the measuring thread
# (`time.thread_time`), not the wall clock: on real data (M 57, 61 frames, four
# threads, three test suites running) a wall-clock budget of 60 s cut 46 of 61
# frames short, because the threads waited for Python's global lock and for
# the other programs, which is time the frame did not use. Timed on this
# machine (12 cores) on the two 3008 x 3008 ASI533MM golden M 13 frames
# (M_13_Light_Luminance_019 and _020), 8 frames per row, 2026-10-22:
#
#   one at a time (one thread)        13.1 to 15.1 s of CPU time per frame
#   four in worker processes          20.3 to 23.0 s
#   four in worker threads (worst)    23.3 to 27.5 s
#
# The CPU time rises with the number of frames running at once because they
# share memory bandwidth and, for threads, fight over the global lock. The
# budget is 60 s: 4 times the lone-frame time and 2.2 times the worst measured
# case, so a normal frame never reaches it on a busy machine, and a frame that
# needs several times the normal work (a larger sensor, a crowded field) is cut
# off instead of stalling a batch. The budget is checked between the steps, not
# inside one, so one step can run past it. A frame that is cut off is not
# marked as measured and is tried again on the next run. Register row STK-D22
# holds the numbers.
FRAME_MEASUREMENT_TIME_BUDGET_SECONDS = 60.0

# What the reason of a skipped step starts with when the frame's CPU time
# budget ran out. The measurement job tests for it (see
# `frame_needs_another_try`) so that a frame cut short is measured again on the
# next run instead of being recorded as measured.
TIME_BUDGET_REASON_PREFIX = "CPU time budget"


class Stopwatch:
    """Count the CPU seconds spent on one frame against a budget.

    Parameters
    ----------
    budget_seconds : `float`
        The seconds allowed for the whole frame.
    """

    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        self._start = time.thread_time()

    def elapsed(self) -> float:
        """Give the CPU seconds spent so far by the measuring thread.

        Returns
        -------
        seconds : `float`
            CPU seconds of the thread that made the stopwatch, since it was
            made. Waiting (a sleep, a lock, a disk read) adds nothing. Call it
            from that same thread.
        """
        return time.thread_time() - self._start

    def expired(self) -> bool:
        """Say whether the budget is used up.

        Returns
        -------
        expired : `bool`
            `True` once the elapsed time exceeds the budget.
        """
        return self.elapsed() > self.budget_seconds

    def reason(self) -> str:
        """Give the sentence stored when a step is skipped for lack of time.

        Returns
        -------
        reason : `str`
            "CPU time budget of N s exceeded after M s".
        """
        return (
            f"{TIME_BUDGET_REASON_PREFIX} of {self.budget_seconds:g} s exceeded after {self.elapsed():.1f} s"
        )
