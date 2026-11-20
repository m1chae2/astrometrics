r"""Purpose: The Windows versions of the rules in `system`.

Description: Imported by `system` only on Windows. Each function has the
same name, inputs and result as its Linux version in `_system_posix`.
"""

import contextlib
import os
import shlex
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping our own processes
from typing import Any

import psutil


def split_program_command(text: str) -> list[str]:
    r"""Split a program setting into the program and its arguments.

    Parameters
    ----------
    text : `str`
        The setting, such as ``siril-cli`` or
        ``C:\Program Files\Siril\bin\siril-cli.exe``.

    Returns
    -------
    parts : `list` [`str`]
        The whole text when it names an existing file (a path with spaces
        needs no quotes). Otherwise the words, split at spaces outside double
        quotes, with the quotes removed and backslashes kept.
    """
    whole = text.strip().strip('"')
    if os.path.isfile(whole):
        return [whole]
    return [word.strip('"') for word in shlex.split(text, posix=False)]


def lower_process_priority(niceness: int) -> None:
    """Give this process the "below normal" priority class.

    Windows priorities come in classes, not steps, so any positive
    `niceness` sets "below normal", the nearest match to the niceness of 10
    the callers use on Linux.

    Parameters
    ----------
    niceness : `int`
        How much to lower the priority, as on Linux. 0 leaves it unchanged.
    """
    if niceness:
        psutil.Process().nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)


def stop_process_tree(process: subprocess.Popen, grace_seconds: float, reap_timeout_seconds: float) -> None:
    """Stop a process and everything it started, ignoring errors.

    Windows has no process groups of the Linux kind, so ``taskkill /T /F``
    stops the process and its children at once; there is no gentle first
    signal, so `grace_seconds` is not used.

    Parameters
    ----------
    process : `subprocess.Popen`
        The process to stop.
    grace_seconds : `float`
        Not used on Windows; kept so both versions take the same inputs.
    reap_timeout_seconds : `float`
        Longest wait for ``taskkill`` and then for the stopped process.
    """
    del grace_seconds
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=reap_timeout_seconds,
            check=False,
        )
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=reap_timeout_seconds)


def child_process_options() -> dict[str, Any]:
    """Give the keyword arguments for starting a helper program.

    Windows has no process groups of the Linux kind, and ``taskkill /T``
    stops the tree (`stop_process_tree`). A helper that the backend starts
    is stopped with the backend by the job object the backend joins at
    start-up, so no option is needed here.

    Returns
    -------
    options : `dict` [`str`, `object`]
        Keyword arguments for `subprocess.Popen`; ``start_new_session`` is
        accepted and ignored on Windows, as before.
    """
    return {"start_new_session": True}
