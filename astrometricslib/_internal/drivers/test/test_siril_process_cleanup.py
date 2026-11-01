"""Tests that cleaning up after Siril never kills this program."""

import os
import subprocess  # ruff: ignore[suspicious-subprocess-import] -- real processes are the point
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from astrometricslib._internal.drivers.siril_interface import session
from astrometricslib._internal.drivers.siril_stacking_driver import SirilStackingDriver

# A stand-in for Siril: it starts a sleeping helper (as flatpak and bwrap start
# the real program), writes the helper's id to the file named by its first
# argument, and then sleeps itself.
STAND_IN_SIRIL = (
    "import subprocess, sys, time;"
    "helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']);"
    "open(sys.argv[1], 'w').write(str(helper.pid));"
    "time.sleep(60)"
)


def _process_exists(process_id: int) -> bool:
    """Say whether a process is still running (a zombie counts as gone).

    Parameters
    ----------
    process_id : `int`
        The process to look for.

    Returns
    -------
    exists : `bool`
        `True` if it can be signalled and is not a zombie.
    """
    try:
        os.kill(process_id, 0)
        with open(f"/proc/{process_id}/stat", encoding="utf-8") as stat_file:
            return stat_file.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def test_stopping_the_stacking_programs_leaves_no_siril_and_no_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stop a stand-in Siril and its helper through the driver."""
    helper_file = tmp_path / "helper_pid.txt"
    # `run_siril_headless` waits two seconds for Siril to start; a test needs
    # only the helper's id file.
    monkeypatch.setattr(
        session, "time", SimpleNamespace(sleep=lambda _seconds: None, monotonic=time.monotonic)
    )
    stacking_session = session.SirilSession.__new__(session.SirilSession)
    stacking_session.siril_executable = f'{sys.executable} -c "{STAND_IN_SIRIL}" {helper_file}'
    stacking_session.subprocesses = []
    session._active_image_processing_instances.add(stacking_session)
    process = stacking_session.run_siril_headless("unused_command_pipe", "unused_output_pipe")
    try:
        deadline = time.monotonic() + 10.0
        while not (helper_file.exists() and helper_file.read_text()) and time.monotonic() < deadline:
            time.sleep(0.05)
        helper_process_id = int(helper_file.read_text())
        assert _process_exists(helper_process_id)

        SirilStackingDriver(session=stacking_session).stop_running_programs()

        assert process.poll() is not None
        deadline = time.monotonic() + 5.0
        while _process_exists(helper_process_id) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not _process_exists(helper_process_id)
    finally:
        session._active_image_processing_instances.discard(stacking_session)
        if process.poll() is None:
            process.kill()


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
