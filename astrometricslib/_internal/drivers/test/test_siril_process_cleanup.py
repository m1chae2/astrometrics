"""Tests that cleaning up after Siril never kills this program."""

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real processes are the point
import sys
from pathlib import Path

from astrometricslib._internal.drivers.siril_interface import session


def test_the_cleanup_kills_a_listed_process_but_not_this_program() -> None:
    """A listed process dies; this program, listed with it, lives."""
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        session._kill_other_processes(["echo", f"{os.getpid()} {other.pid}"])
        assert other.wait(timeout=10) == -9
    finally:
        other.kill()


def test_the_cleanup_really_spares_a_program_holding_the_pipe(tmp_path: Path) -> None:
    """With real ``fuser``, a program holding the file survives."""
    pipe = tmp_path / "siril_command.in"
    pipe.touch()
    script = (
        "import sys; from astrometricslib._internal.drivers.siril_interface import session;"
        "held = open(sys.argv[1]);"
        "session._kill_other_processes(['fuser', sys.argv[1]]);"
        "print('alive')"
    )
    done = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
        [sys.executable, "-c", script, str(pipe)], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    assert "alive" in done.stdout
