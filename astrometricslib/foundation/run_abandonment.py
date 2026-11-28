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

Work spread over a pool of worker threads or processes needs one more piece:
the thread that waits for the workers must notice a cancel while every worker
is busy. `completed_unless_abandoned` waits for results in short steps and
checks the flag each time, and `stopping_on_abandonment` shuts the pool down
without waiting for the frames still in progress. So a cancel is seen within
`CANCEL_POLL_SECONDS`, no new item starts, and only the items that were
already running finish, with their results thrown away.
"""

import contextlib
import contextvars
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping the run's own processes is the point
import threading
from collections.abc import Callable, Generator, Iterable, Sequence
from concurrent.futures import FIRST_COMPLETED, Executor, Future, wait

from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.foundation.system import child_process_options, stop_process_tree

__all__ = [
    "CANCEL_POLL_SECONDS",
    "RunAbandonedError",
    "RunAbandonment",
    "bind_abandonment",
    "completed_unless_abandoned",
    "launch_registered",
    "raise_if_abandoned",
    "run_registered",
    "stop_process_group",
    "stopping_on_abandonment",
]

# How often a thread that waits for pool workers looks at the cancel flag.
# Half a second is the same step the slot wait uses (`acquire_resource_slot`).
# It is short enough that Cancel feels immediate and long enough that the
# check costs nothing next to frames that take seconds each. A chosen value,
# not a measured one.
CANCEL_POLL_SECONDS = 0.5

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
    the process we started. `stop_process_tree` stops the whole tree: on Linux
    through the process group (the process leads its own group, started with
    ``start_new_session=True``), on Windows through ``taskkill``. Nothing is
    matched by name.

    Parameters
    ----------
    process : `subprocess.Popen`
        The process to stop.
    """
    stop_process_tree(process, STOP_GRACE_SECONDS, REAP_TIMEOUT_SECONDS)


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
    """Run a program to the end, like `subprocess.run`, but in its own group.

    Output is captured as text and a non-zero exit code is not an error, as
    for ``subprocess.run(..., capture_output=True, text=True, check=False)``.
    The program always leads a process group of its own, so a timeout ends
    the program and every helper it started (`stop_process_group`), not just
    the program. This is the one way the library runs GraXpert, Cosmic
    Clarity and the headless Siril preview. When a run is
    bound, the program is also registered with that run, so giving up on the
    run stops it too.

    Parameters
    ----------
    arguments : `collections.abc.Sequence` [`str`]
        The program and its arguments.
    timeout_seconds : `float`
        How long the program may run before its whole group is stopped.

    Returns
    -------
    completed : `subprocess.CompletedProcess`
        The exit code and captured output.

    Raises
    ------
    subprocess.TimeoutExpired
        If the program ran longer than ``timeout_seconds``. By then the
        program and its helpers are stopped.

    Notes
    -----
    `RunAbandonedError` is raised if the run was given up on before or
    while the program ran.
    """
    process = launch_registered(
        lambda: subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- caller's fixed argv
            list(arguments),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **child_process_options(),
        )
    )
    try:
        standard_output, standard_error = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        stop_process_group(process)
        # A helper that left the group could still hold the output pipes, so
        # this last read is bounded too.
        with contextlib.suppress(subprocess.TimeoutExpired):
            process.communicate(timeout=REAP_TIMEOUT_SECONDS)
        raise
    raise_if_abandoned()
    return subprocess.CompletedProcess(arguments, process.returncode, standard_output, standard_error)


def completed_unless_abandoned(
    futures: Iterable[Future], poll_seconds: float = CANCEL_POLL_SECONDS
) -> Generator[Future]:
    """Give each future as it finishes, stopping when the run is given up on.

    Like `concurrent.futures.as_completed`, except that the wait is cut into
    steps of ``poll_seconds`` and the cancel flag is checked at each one and
    after each result, so a cancel is seen even while every worker is busy
    with a slow item.

    Parameters
    ----------
    futures : `collections.abc.Iterable` [`concurrent.futures.Future`]
        The submitted work.
    poll_seconds : `float`, optional
        How long to wait between looks at the flag.

    Yields
    ------
    future : `concurrent.futures.Future`
        A finished future, in the order they finish.

    Raises
    ------
    RunAbandonedError
        If the bound run was given up on. Futures that have not started are
        cancelled first, so no new item starts. Items already running
        cannot be interrupted and finish on their own.
    """
    pending = set(futures)
    try:
        while pending:
            raise_if_abandoned()
            finished, pending = wait(pending, timeout=poll_seconds, return_when=FIRST_COMPLETED)
            for future in finished:
                yield future
                raise_if_abandoned()
    except RunAbandonedError:
        for future in pending:
            future.cancel()
        raise


@contextlib.contextmanager
def stopping_on_abandonment(executor: Executor) -> Generator[Executor]:
    """Run a block with a pool, and give the pool up quickly on a cancel.

    Works like ``with executor:`` (the pool is shut down and joined when the
    block ends) except when the block raises `RunAbandonedError`: then the
    work that has not started is cancelled and the pool is shut down without
    waiting for the items still running, so the cancel is not held up by the
    slowest one. Those items finish in the background and their results are
    dropped.

    Parameters
    ----------
    executor : `concurrent.futures.Executor`
        The pool to manage.

    Yields
    ------
    executor : `concurrent.futures.Executor`
        The same pool.

    Raises
    ------
    RunAbandonedError
        Passed on from the block, after the pool is given up.
    """
    try:
        yield executor
    except RunAbandonedError:
        executor.shutdown(wait=False, cancel_futures=True)
        raise
    except BaseException:
        executor.shutdown(wait=True)
        raise
    else:
        executor.shutdown(wait=True)
