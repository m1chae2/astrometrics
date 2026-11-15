"""Purpose: The Linux way to give Siril its command and output pipes.

Description: Imported by `pipes` only when the program does not run on
Windows. On Linux this program makes the two pipes as FIFO files (``mkfifo``)
in the work folder and hands their paths to Siril. Opening a FIFO waits until
the other end is opened too, so no retries are needed. Each function has the
same name and inputs as its Windows version in `_pipes_windows`.
"""

import contextlib
import glob
import logging
import os
import signal
import subprocess
from typing import Any

logger = logging.getLogger(__name__)


def create_pipes(base_path: str) -> tuple[str, str]:
    """Create the FIFOs used to send commands to and read output from Siril.

    Parameters
    ----------
    base_path : `str`
        Directory in which to create the named pipes.

    Returns
    -------
    command_pipe : `str`
        Path to the pipe Siril reads commands from.
    output_pipe : `str`
        Path to the pipe Siril writes status output to.
    """
    command_pipe = os.path.join(base_path, "siril_command.in")
    output_pipe = os.path.join(base_path, "siril_command.out")
    for pipe in [command_pipe, output_pipe]:
        if os.path.exists(pipe):
            os.remove(pipe)
        os.mkfifo(pipe)
        # Owner-only: both ends (this process and the spawned Siril
        # subprocess) run as the current user, so wider access isn't
        # needed.
        os.chmod(pipe, 0o600)
    return command_pipe, output_pipe


def open_pipe(path: str, mode: str, keep_trying: Any = None) -> Any:
    """Open one end of a Siril pipe.

    The pipe is a file this program made, and `open` waits until Siril opens
    the other end.

    Parameters
    ----------
    path : `str`
        The pipe.
    mode : `str`
        ``"r"`` or ``"w"``.
    keep_trying : `~collections.abc.Callable`, optional
        Not used on Linux; kept so both versions take the same inputs.

    Returns
    -------
    pipe : file object
        The open pipe.
    """
    del keep_trying
    return open(path, mode)


def _kill_other_processes(find_command: list[str]) -> None:
    """Force-kill the processes a command lists, but never this program.

    This program can have a Siril pipe open itself: the thread that writes
    commands holds the pipe while it waits for Siril to answer. If Siril dies
    during that wait, the program is one of the processes using the pipe, so
    killing everything on the list would kill the program too.

    Parameters
    ----------
    find_command : `list` [`str`]
        A command that prints process ids on standard output, such as
        ``fuser`` or ``pgrep``. Anything else it prints is ignored.
    """
    found = subprocess.run(find_command, capture_output=True, text=True).stdout
    for process_id in {int(word) for word in found.split() if word.isdigit()} - {os.getpid()}:
        with contextlib.suppress(OSError):
            os.kill(process_id, signal.SIGKILL)


def stop_pipe_holders(work_directory: str) -> None:
    """Kill any process still tied to the work folder's pipes, ignoring errors.

    Siril runs in a sandbox (flatpak or bwrap) whose helper processes can
    outlive the process group that was stopped. They show up as users of the
    pipe files, or as processes with the work folder in their command line.

    Parameters
    ----------
    work_directory : `str`
        The folder `create_pipes` made the pipes in.
    """
    if not os.path.exists(work_directory):
        return
    try:
        for pipe in glob.glob(os.path.join(work_directory, "siril_command.*")):
            try:
                _kill_other_processes(["fuser", pipe])
            except (OSError, subprocess.SubprocessError) as exc:
                logger.debug("fuser cleanup failed for pipe '%s': %s", pipe, exc)
        # Safety net for sandboxed processes linked to workdir pipes
        _kill_other_processes(["pgrep", "-f", work_directory])
    except (OSError, subprocess.SubprocessError) as exc:
        logger.debug("Failed to force-kill lingering Siril processes for '%s': %s", work_directory, exc)
