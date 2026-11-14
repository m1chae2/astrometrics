r"""Purpose: Small rules that differ between Linux and Windows.

Description: The library was written for Linux, which stays the main system.
These helpers keep the few Windows differences in one place, and each one
gives exactly the Linux behavior when the program does not run on Windows:

* `split_program_command` reads a program setting such as
  ``siril_executable``. Linux splits it like a shell would; on Windows a
  backslash is part of a path (``C:\Program Files\...``), not an escape.
* `quote_siril_path` writes a path into a Siril command. Siril for Windows
  stops reading an unquoted path at its first space, so the path is quoted
  there; Linux commands are left as they are.
* `stage_file` puts a frame into a Siril work folder. Linux makes a
  symbolic link. Windows allows those only with administrator rights or
  Developer Mode, which the app does not ask for, so it makes a hard link
  (the same file under a second name, free and instant, but only on one
  NTFS drive) and copies the file when that is not possible.
* `stop_windows_process_tree` stops a program and everything it started on
  Windows with ``taskkill /T /F``; Linux signals the process group instead
  (`astrometricslib.foundation.run_abandonment.stop_process_group`).
"""

import contextlib
import logging
import os
import shlex
import shutil
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- stopping our own processes
import sys

logger = logging.getLogger(__name__)

TASKKILL_TIMEOUT_SECONDS = 10.0
"""Longest wait for ``taskkill`` and the stopped process (Windows)."""

STAGED_BY_LINK = "link"
"""`stage_file` made a link (symbolic on Linux, hard on Windows)."""

STAGED_BY_COPY = "copy"
"""`stage_file` copied the file (Windows, when no hard link is possible)."""


def on_windows() -> bool:
    """Say whether the program runs on Windows.

    A function, not a constant, so tests can choose the system.

    Returns
    -------
    windows : `bool`
        `True` on Windows.
    """
    return sys.platform == "win32"


def split_program_command(text: str) -> list[str]:
    r"""Split a program setting into the program and its arguments.

    Parameters
    ----------
    text : `str`
        The setting, such as ``"siril-cli"``,
        ``"flatpak run org.siril.Siril"`` or, on Windows,
        ``C:\Program Files\Siril\bin\siril-cli.exe``.

    Returns
    -------
    parts : `list` [`str`]
        On Linux, `shlex.split` of the text. On Windows, the whole text when
        it names an existing file (a path with spaces needs no quotes);
        otherwise the words, split at spaces outside double quotes, with the
        quotes removed and backslashes kept.
    """
    if not on_windows():
        return shlex.split(text)
    whole = text.strip().strip('"')
    if os.path.isfile(whole):
        return [whole]
    return [word.strip('"') for word in shlex.split(text, posix=False)]


def quote_siril_path(path: str) -> str:
    r"""Write a path the way a Siril command must receive it.

    Parameters
    ----------
    path : `str`
        A file or folder path.

    Returns
    -------
    text : `str`
        On Windows the path in double quotes, because Siril for Windows
        stops at the first space of an unquoted path
        (``C:\Users\Jane Doe\...``). On Linux the path unchanged.
    """
    return f'"{path}"' if on_windows() else path


def stage_file(source: str, destination: str) -> str:
    """Put a file into a work folder without changing the original.

    Parameters
    ----------
    source : `str`
        The original file.
    destination : `str`
        The new name in the work folder. It must not exist yet.

    Returns
    -------
    method : `str`
        `STAGED_BY_LINK` or `STAGED_BY_COPY`. An `OSError` comes through
        when the file cannot be linked (Linux) or neither linked nor copied
        (Windows).
    """
    if not on_windows():
        os.symlink(source, destination)
        return STAGED_BY_LINK
    try:
        # A hard link needs no special rights, but both names must be on the
        # same NTFS drive. A Google Drive or network folder cannot hold one.
        os.link(source, destination)
        return STAGED_BY_LINK
    except OSError as link_error:
        logger.debug("No hard link for %s (%s); copying it", source, link_error)
    shutil.copy2(source, destination)
    return STAGED_BY_COPY


def stop_windows_process_tree(process: subprocess.Popen) -> None:
    """Stop a process and everything it started, on Windows, ignoring errors.

    Windows has no process groups of the Linux kind, so ``taskkill /T /F``
    stops the process and its children. The Linux way is
    `astrometricslib.foundation.run_abandonment.stop_process_group`, which
    calls this on Windows.

    Parameters
    ----------
    process : `subprocess.Popen`
        The process to stop.
    """
    if process.poll() is not None:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            capture_output=True,
            timeout=TASKKILL_TIMEOUT_SECONDS,
            check=False,
        )
    with contextlib.suppress(OSError, subprocess.TimeoutExpired):
        process.wait(timeout=TASKKILL_TIMEOUT_SECONDS)
