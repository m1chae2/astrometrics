"""Purpose: End the backend so a launcher can start it on another source.

Description: The "Observatory source" setting has two values: the real
telescope, or a simulated sky. The backend does not build or tear down any
world for it. A launcher (a program that starts the backend, for example
``python -m simulator.launch``) reads the saved choice, starts the backend as
its child with the world's settings and folders, and sets
``ASTROMETRICS_LAUNCHER`` (`LAUNCHER_VARIABLE`). The backend reports the
launch (`ObservatorySourceService.get_source`) and, when the user changes the
source, saves the choice and ends with `RESTART_EXIT_CODE` (75,
``EX_TEMPFAIL``: "try again") after it has stopped its services and child
processes. The launcher then reads the choice and starts the next session.
Any other exit code is a stop.

The choice file and the names shared with a launcher live in
`astrometricslib.foundation.source_choice`. Without a launcher the backend
cannot restart itself on another source, so the change is refused (see
`ObservatorySourceService.set_source`).
"""

import logging
import os
import sys
import threading
import time
from collections.abc import Callable, Mapping

import psutil

from astrometricslib.drivers import LAUNCHER_VARIABLE, RESTART_EXIT_CODE

__all__ = [
    "RESTART_DELAY_SECONDS",
    "end_child_processes",
    "launched_by_launcher",
    "restart_backend",
]

logger = logging.getLogger(__name__)

#: Seconds the backend waits after answering before it ends for the restart.
RESTART_DELAY_SECONDS = 0.7


def launched_by_launcher(environ: Mapping[str, str] | None = None) -> bool:
    """Say whether a launcher runs this backend as its child.

    Parameters
    ----------
    environ : `~collections.abc.Mapping` [`str`, `str`], optional
        The environment. Default: the process environment.

    Returns
    -------
    launched : `bool`
        `True` when ``ASTROMETRICS_LAUNCHER`` is set.
    """
    return bool((os.environ if environ is None else environ).get(LAUNCHER_VARIABLE))


def end_child_processes(timeout: float = 5.0) -> int:
    """End every process this process started, and wait for them.

    The backend starts a Siril in a session of its own, the INDI worker and
    process pools. None of them may outlive the backend. They get SIGTERM,
    then SIGKILL after `timeout` seconds.

    Parameters
    ----------
    timeout : `float`, optional
        Seconds to wait after SIGTERM.

    Returns
    -------
    count : `int`
        How many child processes there were.
    """
    children = psutil.Process().children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except psutil.NoSuchProcess:
            pass
    _, alive = psutil.wait_procs(children, timeout=timeout)
    for child in alive:
        try:
            child.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(alive, timeout=2.0)
    return len(children)


def restart_backend(
    shutdown: Callable[[], None] | None = None,
    delay_seconds: float = RESTART_DELAY_SECONDS,
    exit_process: Callable[[int], None] = os._exit,
) -> threading.Thread:
    """End this backend with `RESTART_EXIT_CODE`, soon after the reply is sent.

    The launcher starts the backend again with the saved choice. Before the
    process ends, this

    1. calls `shutdown` (the container's `shutdown_resources`: the
       maintenance and download threads, Siril, the INDI worker);
    2. ends and waits for every child process that is left; and
    3. flushes the output.

    Parameters
    ----------
    shutdown : `~collections.abc.Callable`, optional
        Stops the backend's services.
    delay_seconds : `float`, optional
        Seconds to wait first, so the reply goes out.
    exit_process : `~collections.abc.Callable`, optional
        Ends the process with an exit code. Tests pass a recorder.

    Returns
    -------
    thread : `threading.Thread`
        The thread that does the restart.
    """

    def restart() -> None:
        """Wait for the reply to go out, stop everything, end the process."""
        time.sleep(delay_seconds)
        if shutdown is not None:
            try:
                shutdown()
            except Exception:  # the restart must go on to stop the rest
                logger.exception("Shutdown before the restart failed")
        ended = end_child_processes()
        logger.warning(
            "Stopping the backend for the launcher to start it again (%d child processes ended).", ended
        )
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
            except OSError:
                pass
        exit_process(RESTART_EXIT_CODE)

    thread = threading.Thread(target=restart, name="observatory-source-restart", daemon=True)
    thread.start()
    return thread
