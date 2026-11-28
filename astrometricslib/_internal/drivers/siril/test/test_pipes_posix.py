"""Purpose: Tests for the Linux pipes of the Siril driver.

Description: On Linux the command and output pipes are FIFO files in the work
folder. These tests run only on Linux. Siril itself is never started.
"""

import os
import stat
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril.core import _pipes_posix

pytestmark = pytest.mark.linux_only("the pipes are FIFO files made with os.mkfifo")


def test_create_pipes_makes_owner_only_fifos(tmp_path: Path) -> None:
    """Both pipes are FIFO files in the folder, readable by the owner alone."""
    command_pipe, output_pipe = _pipes_posix.create_pipes(str(tmp_path))
    assert (command_pipe, output_pipe) == (
        str(tmp_path / "siril_command.in"),
        str(tmp_path / "siril_command.out"),
    )
    for pipe in (command_pipe, output_pipe):
        mode = os.stat(pipe).st_mode
        assert stat.S_ISFIFO(mode)
        assert stat.S_IMODE(mode) == 0o600


def test_create_pipes_replaces_pipes_left_by_an_earlier_run(tmp_path: Path) -> None:
    """A file left at a pipe's path is replaced by a fresh FIFO."""
    (tmp_path / "siril_command.in").write_text("old")
    command_pipe, _output_pipe = _pipes_posix.create_pipes(str(tmp_path))
    assert stat.S_ISFIFO(os.stat(command_pipe).st_mode)


def test_open_pipe_opens_the_path_and_ignores_keep_trying(tmp_path: Path) -> None:
    """Linux opens the path at once; `keep_trying` has no use."""
    target = tmp_path / "pipe.out"
    target.write_text("ready\n", encoding="utf-8")
    with _pipes_posix.open_pipe(str(target), "r", keep_trying=lambda: False) as pipe:
        assert pipe.read() == "ready\n"


def test_stop_pipe_holders_ignores_a_missing_folder(tmp_path: Path) -> None:
    """A work folder that is already gone needs no cleanup."""
    _pipes_posix.stop_pipe_holders(str(tmp_path / "gone"))
