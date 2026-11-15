"""Purpose: Tests for the Linux versions of the rules in `system`.

Description: These run only on Linux (and other non-Windows systems). They
use real processes where they can: a child that sets its own priority, and a
child with a grandchild that must stop together. The Windows versions have
their own tests in `test_system_windows.py`, which run on Windows.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real child processes are the point
import sys
import time

import psutil
import pytest

from astrometricslib.foundation import system

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="these are the Linux versions")


# A child that starts a sleeping grandchild, prints the grandchild's process
# number and then sleeps itself. A minute is far longer than any test waits.
CHILD_WITH_GRANDCHILD = (
    "import subprocess, sys, time;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "print(g.pid, flush=True);"
    "time.sleep(60)"
)

# Lowers the priority as asked and prints the resulting niceness.
PRIORITY_PROBE = (
    "import os, sys;"
    "from astrometricslib.foundation.system import lower_process_priority;"
    "lower_process_priority(int(sys.argv[1]));"
    "print(os.nice(0))"
)


def niceness_after_lowering(niceness: int) -> int:
    """Lower the priority of a fresh process and read its niceness.

    A fresh process is used so the test process keeps its own priority.

    Parameters
    ----------
    niceness : `int`
        The value given to `lower_process_priority`.

    Returns
    -------
    result : `int`
        The child's niceness afterwards.
    """
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", PRIORITY_PROBE, str(niceness)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return int(completed.stdout.strip())


def test_a_program_setting_is_split_like_a_shell() -> None:
    """Words split at spaces, and quotes keep a path with spaces whole."""
    assert system.split_program_command("flatpak run org.siril.Siril") == [
        "flatpak",
        "run",
        "org.siril.Siril",
    ]
    assert system.split_program_command('"/opt/my apps/siril-cli" -d x') == [
        "/opt/my apps/siril-cli",
        "-d",
        "x",
    ]


def test_lowering_the_priority_adds_the_niceness() -> None:
    """A positive niceness is added to the current one; zero adds nothing."""
    unchanged = niceness_after_lowering(0)
    assert niceness_after_lowering(5) == unchanged + 5


def test_stopping_a_process_tree_stops_the_child_and_its_grandchild() -> None:
    """The whole process group of the child ends, not only the child."""
    child = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", CHILD_WITH_GRANDCHILD],
        stdout=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    grandchild_id = int(child.stdout.readline())
    assert psutil.pid_exists(grandchild_id)

    system.stop_process_tree(child, 0.2, 2.0)

    assert child.poll() is not None
    deadline = time.monotonic() + 5.0
    while psutil.pid_exists(grandchild_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(grandchild_id)


def test_stopping_a_process_that_already_ended_does_nothing() -> None:
    """A finished process is left alone and raises nothing."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"], start_new_session=True)
    finished.wait()
    system.stop_process_tree(finished, 0.2, 2.0)
    assert finished.returncode == 0
