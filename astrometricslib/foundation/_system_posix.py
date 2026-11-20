"""Purpose: The Linux versions of the rules in `system`.

Description: Imported by `system` only when the program does not run on
Windows. Each function has the same name, inputs and result as its Windows
version in `_system_windows`.
"""

import contextlib
import ctypes
import logging
import os
import shlex
import signal
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping our own processes
import sys
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# The Linux ``prctl`` option that asks the kernel to send a signal to this
# process when its parent dies (see ``man prctl``, PR_SET_PDEATHSIG).
PR_SET_PDEATHSIG = 1


def _load_prctl() -> Callable[..., int] | None:
    """Find the C library's ``prctl`` function, on Linux only.

    It is loaded here, when the module is imported, and not in the child
    process after a fork: loading a library in a forked child of a threaded
    program can deadlock.

    Returns
    -------
    prctl : `collections.abc.Callable` or `None`
        The function, or `None` on other systems or when it is missing.
    """
    if not sys.platform.startswith("linux"):
        return None
    try:
        return ctypes.CDLL(None, use_errno=True).prctl
    except OSError, AttributeError:
        return None


_prctl = _load_prctl()


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


def child_process_options() -> dict[str, Any]:
    """Give the keyword arguments for starting a helper program.

    The helper leads a process group of its own (``start_new_session=True``),
    so `stop_process_tree` can stop its whole tree. On Linux it also gets a
    ``preexec_fn`` that asks the kernel to send it SIGTERM when its parent
    dies, so a backend that crashes or is killed (SIGKILL, out of memory)
    does not leave helpers running.

    The kernel ties the signal to the *thread* that started the child, not to
    the process. It fires if that thread ends while the child runs.
    `run_registered` waits for the child in the starting thread, so it is
    safe. A Siril session lives as long as the stack run that opened it, on
    that run's worker thread. Do not start a helper from a thread that
    then returns while the helper keeps running.

    Returns
    -------
    options : `dict` [`str`, `object`]
        Keyword arguments for `subprocess.Popen`. On macOS and other systems
        without ``prctl`` only the new session is set, so helpers there still
        rely on the caller to stop them.
    """
    if _prctl is None:
        return {"start_new_session": True}
    parent_process_id = os.getpid()
    prctl = _prctl

    def exit_with_parent() -> None:
        """Ask for SIGTERM when the parent dies; exit now if it already did."""
        prctl(PR_SET_PDEATHSIG, signal.SIGTERM, 0, 0, 0)
        # The parent may have died between the fork and the call above, and
        # then the kernel would never send the signal.
        if os.getppid() != parent_process_id:
            os._exit(1)

    return {"start_new_session": True, "preexec_fn": exit_with_parent}
