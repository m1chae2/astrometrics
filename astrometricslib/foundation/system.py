r"""Purpose: The few operating-system rules that cannot be written once.

Description: The library runs the same code on Linux and Windows wherever it
can. It uses libraries that already handle each system (``platformdirs`` for
folders, ``filelock`` for locks, ``psutil`` for processes), and it tries an
action and falls back when the action fails, rather than asking which system
it is on. What is left is here: a handful of functions whose Linux and Windows
versions really differ.

Each function has one version in `_system_posix` (Linux) and one in
`_system_windows`. This module imports the right one once, when it is first
loaded, the way Python's own `os` module loads ``posixpath`` or ``ntpath``.
Callers import the names from here and never check the system themselves.
The tests of each version run on the system it is for.

* `split_program_command` reads a program setting such as
  ``siril_executable``. Linux splits it the way a shell would; on Windows a
  backslash is part of a path (``C:\Program Files\...``), not an escape.
* `lower_process_priority` lets a worker process give way to the rest of the
  computer: ``os.nice`` on Linux, the "below normal" priority class on
  Windows.
* `stop_process_tree` stops a program and everything it started: a signal to
  its process group on Linux, ``taskkill /T /F`` on Windows.
* `child_process_options` gives the `subprocess.Popen` options for a helper
  program: its own process group, and on Linux a request that the kernel
  stop it when the backend dies.
* `on_windows` says whether the program runs on Windows. Use it only to turn
  on a feature that exists on one system alone (the Windows program setup),
  never to choose between two ways of doing the same thing.
"""

import sys

if sys.platform == "win32":
    from astrometricslib.foundation._system_windows import (
        child_process_options,
        lower_process_priority,
        split_program_command,
        stop_process_tree,
    )
else:
    from astrometricslib.foundation._system_posix import (
        child_process_options,
        lower_process_priority,
        split_program_command,
        stop_process_tree,
    )

__all__ = [
    "child_process_options",
    "lower_process_priority",
    "on_windows",
    "split_program_command",
    "stop_process_tree",
]


def on_windows() -> bool:
    """Say whether the program runs on Windows.

    A function, not a constant, so a test can turn on a Windows-only feature.

    Returns
    -------
    windows : `bool`
        `True` on Windows.
    """
    return sys.platform == "win32"
