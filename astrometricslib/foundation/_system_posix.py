"""Purpose: The Linux versions of the rules in `system`.

Description: Imported by `system` only when the program does not run on
Windows. Each function has the same name, inputs and result as its Windows
version in `_system_windows`.
"""

import contextlib
import logging
import os
import shlex
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping our own processes
import time

logger = logging.getLogger(__name__)


def split_program_command(text: str) -> list[str]:
    """Split a program setting into the program and its arguments.

    Parameters
    ----------
    text : `str`
        The setting, such as ``"siril-cli"`` or
        ``"flatpak run org.siril.Siril"``.

    Returns
    -------
    parts : `list` [`str`]
        The words, split the way a shell would (`shlex.split`).
    """
    return shlex.split(text)


def lower_process_priority(niceness: int) -> None:
    """Lower this process's scheduling priority by `niceness` (``os.nice``).

    Parameters
    ----------
    niceness : `int`
        How much to lower the priority. 0 leaves it unchanged.
    """
    if niceness:
        os.nice(niceness)


def stop_process_tree(process: subprocess.Popen, grace_seconds: float, reap_timeout_seconds: float) -> None:
    """Stop one process and everything it started, ignoring errors.

    Siril runs under flatpak or bwrap, so the real program is a descendant of
    the process we started. The process was started as the leader of its own
    group (``start_new_session=True``), so signalling the group reaches all of
    them, even a helper whose parent has already exited. Only that group is
    signalled: nothing is matched by name.

    Parameters
    ----------
    process : `subprocess.Popen`
        The process to stop.
    grace_seconds : `float`
        How long the group has to exit after SIGTERM before SIGKILL.
    reap_timeout_seconds : `float`
        Longest wait for the process to be collected afterwards.
    """
    # No `process.poll()` first: it would collect an exited leader, and then
    # the group could no longer be looked up, while a sandboxed Siril (under
    # flatpak or bwrap) may still be running in it.
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
            time.sleep(grace_seconds)
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=reap_timeout_seconds)
