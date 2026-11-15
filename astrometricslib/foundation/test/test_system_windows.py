r"""Purpose: Tests for the Windows versions of the rules in `system`.

Description: These run only on Windows, where the Windows CI job runs them.
They use real processes: a child that sets its own priority class, and a
child with a grandchild that must stop together. The Linux versions have
their own tests in `test_system_posix.py`.
"""

import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real child processes are the point
import sys
import time
from pathlib import Path

import psutil
import pytest

from astrometricslib.foundation import system

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="these are the Windows versions")


# A child that starts a sleeping grandchild, prints the grandchild's process
# number and then sleeps itself. A minute is far longer than any test waits.
CHILD_WITH_GRANDCHILD = (
    "import subprocess, sys, time;"
    "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "print(g.pid, flush=True);"
    "time.sleep(60)"
)

# Lowers the priority as asked and prints the resulting priority class.
PRIORITY_PROBE = (
    "import psutil, sys;"
    "from astrometricslib.foundation.system import lower_process_priority;"
    "lower_process_priority(int(sys.argv[1]));"
    "print(psutil.Process().nice())"
)


def priority_class_after_lowering(niceness: int) -> int:
    """Lower the priority of a fresh process and read its priority class.

    A fresh process is used so the test process keeps its own priority.

    Parameters
    ----------
    niceness : `int`
        The value given to `lower_process_priority`.

    Returns
    -------
    result : `int`
        The child's priority class afterwards.
    """
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", PRIORITY_PROBE, str(niceness)],
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    return int(completed.stdout.strip())


def test_a_program_path_with_spaces_stays_whole_quoted_or_not(tmp_path: Path) -> None:
    """A path to an existing file names one program, with or without quotes."""
    program = tmp_path / "Program Files" / "Siril" / "bin" / "siril-cli.exe"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    assert system.split_program_command(str(program)) == [str(program)]
    assert system.split_program_command(f'"{program}"') == [str(program)]


def test_backslashes_are_kept_and_quotes_are_removed() -> None:
    """A backslash is part of the path, and quotes only group words."""
    parts = system.split_program_command(r'"C:\Program Files\Tool\tool.exe" --fast')
    assert parts == [r"C:\Program Files\Tool\tool.exe", "--fast"]


def test_lowering_the_priority_sets_the_below_normal_class() -> None:
    """A positive niceness gives the below-normal class; zero leaves it.

    A Windows child starts with its parent's class, so "left as it was"
    means this test process's own class (an earlier test may have lowered
    it), not always the normal class.
    """
    assert priority_class_after_lowering(10) == psutil.BELOW_NORMAL_PRIORITY_CLASS
    assert priority_class_after_lowering(0) == psutil.Process().nice()


def test_stopping_a_process_tree_stops_the_child_and_its_grandchild() -> None:
    """The child and the program it started both end."""
    child = subprocess.Popen(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", CHILD_WITH_GRANDCHILD],
        stdout=subprocess.PIPE,
        text=True,
    )
    grandchild_id = int(child.stdout.readline())
    assert psutil.pid_exists(grandchild_id)

    system.stop_process_tree(child, 0.2, 5.0)

    assert child.poll() is not None
    deadline = time.monotonic() + 5.0
    while psutil.pid_exists(grandchild_id) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not psutil.pid_exists(grandchild_id)


def test_stopping_a_process_that_already_ended_does_nothing() -> None:
    """A finished process is left alone and raises nothing."""
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    system.stop_process_tree(finished, 0.2, 5.0)
    assert finished.returncode == 0
