"""Purpose: Let a run that was given up on stop its own helper programs.

Description: A stack runs in a background thread, and the code that waits for
it may give up (a timeout). A thread cannot be stopped from outside, so
without help the abandoned stack keeps running: its Siril program keeps
working and the thread keeps its place in the job slot queue, which holds up
every other job.

A `RunAbandonment` is made by the code that starts the run, bound for the
run's thread with `bind_abandonment`, and handed to `abandon` when the run is
given up on. It knows only the processes this run started, because the code
that starts a process registers it here (`launch_registered`). So giving up
stops this run's processes and no one else's, even when other stacks run in
the same process at the same time.

Two rules follow from this:

- A process is registered and the abandoned flag is checked under the same
  lock, so a run abandoned before its program started never starts it, and a
  run abandoned just after the start has the program stopped at once.
- The run's thread then ends by itself, through the usual error paths, and
  gives its slot back. `raise_if_abandoned` is the check that code on that
  thread uses to stop doing further work (writing results, for example).

A thread the run starts must bind the same object itself, because a new
thread does not inherit a context variable.
"""

import contextlib
import contextvars
import logging
import os
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping the run's own processes is the point
import threading
import time
from collections.abc import Callable, Generator, Sequence

from astrometricslib.foundation.errors import ProcessingError

logger = logging.getLogger(__name__)

__all__ = [
    "RunAbandonedError",
    "RunAbandonment",
    "bind_abandonment",
    "launch_registered",
    "raise_if_abandoned",
    "run_registered",
]

# How long a stopped process group gets to exit after SIGTERM before SIGKILL.
# Siril closes its pipes and exits at once on SIGTERM when it is responsive,
# and a hung one will not respond, so a long grace period buys nothing. This
# matches the 0.2 s pause `SirilSession._kill_process_tree` already uses.
STOP_GRACE_SECONDS = 0.2

# How long to wait for a killed process to be reaped. A SIGKILLed process is
# gone within milliseconds unless it is stuck in the kernel; two seconds
# matches `SirilSession._kill_process_tree`.
REAP_TIMEOUT_SECONDS = 2.0


class RunAbandonedError(ProcessingError):
    """Raised on a run's own thread when the run was given up on."""


class RunAbandonment:
    """The processes one run started, and whether the run was given up on."""

    def __init__(self) -> None:
        """Start with no processes and the run not abandoned."""
        self._lock = threading.Lock()
        self._abandoned = False
        self._processes: list[subprocess.Popen] = []

    @property
    def abandoned(self) -> bool:
        """Whether `abandon` has been called.

        Returns
        -------
        abandoned : `bool`
            `True` once the run was given up on.
        """
        with self._lock:
            return self._abandoned

    @property
    def registered_process_count(self) -> int:
        """How many processes this run has started so far.

        Returns
        -------
        count : `int`
            Number of registered processes, finished or not.
        """
        with self._lock:
            return len(self._processes)

    def abandon(self) -> None:
        """Give up on the run: stop every process it started and start none.

        Safe to call more than once and from any thread.
        """
        with self._lock:
            self._abandoned = True
            processes = list(self._processes)
        for process in processes:
            stop_process_group(process)

    def launch(self, start_process: Callable[[], subprocess.Popen]) -> subprocess.Popen:
        """Start a process for this run, unless the run was given up on.

        Parameters
        ----------
        start_process : `collections.abc.Callable`
            Starts the process and returns a `subprocess.Popen` whose
            process group it leads (``start_new_session=True``).

        Returns
        -------
        process : `subprocess.Popen`
            The running process, now registered with this run.

        Raises
        ------
        RunAbandonedError
            If the run was given up on before the process could start.
        """
        with self._lock:
            if self._abandoned:
                raise RunAbandonedError("The run was given up on before this program started.")
            process = start_process()
            self._processes.append(process)
            return process


def stop_process_group(process: subprocess.Popen) -> None:
    """Stop one process and everything it started, ignoring errors.

    Siril runs under flatpak or bwrap, so the real program is a descendant of
    the process we started. The process was started as the leader of its own
    group (``start_new_session=True``), so signalling the group reaches all of
    them. Only that group is signalled: nothing is matched by name.

    Parameters
    ----------
    process : `subprocess.Popen`
        The process to stop.
    """
    if process.poll() is not None:
        return
    try:
        process_group_id = os.getpgid(process.pid)
    except OSError:
        return
    for signal_number in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process_group_id, signal_number)
        except OSError as signal_error:
            logger.debug("Signal %s to group %s failed: %s", signal_number, process_group_id, signal_error)
            break
        if signal_number == signal.SIGTERM:
            time.sleep(STOP_GRACE_SECONDS)
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=REAP_TIMEOUT_SECONDS)


_bound_abandonment: contextvars.ContextVar[RunAbandonment | None] = contextvars.ContextVar(
    "run_abandonment", default=None
)


@contextlib.contextmanager
def bind_abandonment(abandonment: RunAbandonment) -> Generator[RunAbandonment]:
    """Make ``abandonment`` the one for the run on this thread.

    Parameters
    ----------
    abandonment : `RunAbandonment`
        The run's object.

    Yields
    ------
    abandonment : `RunAbandonment`
        The same object, for ``with ... as`` use.
    """
    token = _bound_abandonment.set(abandonment)
    try:
        yield abandonment
    finally:
        _bound_abandonment.reset(token)


def raise_if_abandoned() -> None:
    """Stop the calling code if its run was given up on.

    Does nothing when no run is bound (a script, a test) or the run is still
    wanted.

    Raises
    ------
    RunAbandonedError
        If the bound run was given up on.
    """
    abandonment = _bound_abandonment.get()
    if abandonment is not None and abandonment.abandoned:
        raise RunAbandonedError("The run was given up on.")


def launch_registered(start_process: Callable[[], subprocess.Popen]) -> subprocess.Popen:
    """Start a process, registering it with the bound run if there is one.

    Parameters
    ----------
    start_process : `collections.abc.Callable`
        Starts the process and returns the `subprocess.Popen`.

    Returns
    -------
    process : `subprocess.Popen`
        The running process.

    Notes
    -----
    `RunAbandonedError` is raised, and nothing is started, if the bound run
    was already given up on.
    """
    abandonment = _bound_abandonment.get()
    if abandonment is None:
        return start_process()
    return abandonment.launch(start_process)


def run_registered(arguments: Sequence[str], timeout_seconds: float) -> subprocess.CompletedProcess[str]:
    """Run a program to the end, like `subprocess.run`, but stoppable.

    Output is captured as text and a non-zero exit code is not an error, as
    for ``subprocess.run(..., capture_output=True, text=True, check=False)``.
    When a run is bound, the program runs in its own process group and is
    registered with that run, so giving up on the run stops it. With no run
    bound this is the plain call.

    Parameters
    ----------
    arguments : `collections.abc.Sequence` [`str`]
        The program and its arguments.
    timeout_seconds : `float`
        How long the program may run before it is stopped.

    Returns
    -------
    completed : `subprocess.CompletedProcess`
        The exit code and captured output.

    Raises
    ------
    subprocess.TimeoutExpired
        If the program ran longer than ``timeout_seconds``.

    Notes
    -----
    `RunAbandonedError` is raised if the run was given up on before or
    while the program ran.
    """
    if _bound_abandonment.get() is None:
        # No run to give up on: behave exactly like the plain call.
        return subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- caller's fixed argv
            list(arguments), capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    process = launch_registered(
        lambda: subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- caller's fixed argv
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
    )
    try:
        standard_output, standard_error = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        process.communicate()
        raise
    raise_if_abandoned()
    return subprocess.CompletedProcess(arguments, process.returncode, standard_output, standard_error)
