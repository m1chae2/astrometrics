"""Purpose: A running total that belongs to one run, not to the process.

Description: Some numbers describe what one run went through: how long a
stack waited for a free Siril slot, how many tries a plate solve needed.
They used to be module-level counters that the caller reset before a run and
read after it. Two runs in one process then shared (and overwrote) one
number. A `RunTally` is made by the code that starts the run and read by the
same code, so it holds that run's numbers and no one else's.

The code that adds to the number is deep inside the run, several calls away
from the code that started it, so the tally is handed over through a context
variable (`contextvars.ContextVar`, a value that belongs to the current thread
of work). `bind_tally` puts a tally into a variable for a block of code; the
deep code asks `add_to_bound_tally` to add to whichever tally is bound there
(and does nothing when none is). A thread that the run starts must bind the
tally itself, because a new thread does not inherit the binding.
"""

import contextlib
import threading
import time
from collections.abc import Callable, Generator
from contextvars import ContextVar

__all__ = ["RunTally", "add_to_bound_tally", "bind_tally"]


class RunTally:
    """A thread-safe running total for one run.

    Parameters
    ----------
    total : `float`, optional
        The starting total. Defaults to zero.
    """

    def __init__(self, total: float = 0.0) -> None:
        """Start the tally at ``total``."""
        self._lock = threading.Lock()
        self._total = total
        self._open_wait_starts: list[float] = []

    def add(self, amount: float) -> None:
        """Add to the total.

        Parameters
        ----------
        amount : `float`
            What to add. A try counts as 1; a wait counts as its seconds.
        """
        with self._lock:
            self._total += amount

    def start_open_wait(self) -> Callable[[], None]:
        """Count a wait in seconds toward the total while it is still going.

        A wait is usually added only when it ends, so code that watches the
        total during the wait, such as a timeout, would see nothing. Until the
        returned function is called, `total` also holds the seconds since this
        call. Those seconds are then dropped, so the code that waited must add
        the finished wait itself, before calling it.

        Returns
        -------
        stop : `collections.abc.Callable`
            Ends the open wait. Calling it again does nothing.
        """
        started_at = time.monotonic()
        with self._lock:
            self._open_wait_starts.append(started_at)
        stopped = False

        def stop() -> None:
            """Stop counting this wait, once."""
            nonlocal stopped
            with self._lock:
                if not stopped:
                    self._open_wait_starts.remove(started_at)
                    stopped = True

        return stop

    @property
    def total(self) -> float:
        """The total so far, with the seconds of any wait still going.

        Returns
        -------
        total : `float`
            The sum of everything added, plus the time so far of each open
            wait.
        """
        now = time.monotonic()
        with self._lock:
            return self._total + sum(now - started_at for started_at in self._open_wait_starts)


@contextlib.contextmanager
def bind_tally(variable: ContextVar[RunTally | None], tally: RunTally) -> Generator[RunTally]:
    """Make ``tally`` the one that ``variable`` points to inside a block.

    Parameters
    ----------
    variable : `contextvars.ContextVar`
        The variable the adding code reads (each kind of number has its own).
    tally : `RunTally`
        The run's tally.

    Yields
    ------
    tally : `RunTally`
        The same tally, for ``with ... as`` use.
    """
    token = variable.set(tally)
    try:
        yield tally
    finally:
        variable.reset(token)


def add_to_bound_tally(variable: ContextVar[RunTally | None], amount: float) -> None:
    """Add to the tally bound to ``variable``, if there is one.

    Parameters
    ----------
    variable : `contextvars.ContextVar`
        The variable to read.
    amount : `float`
        What to add. Nothing happens when no tally is bound, so code that is
        run outside any tallied run (a script, a test) is not affected.
    """
    tally = variable.get()
    if tally is not None:
        tally.add(amount)
