r"""Purpose: Tests for the Windows pipes of the Siril driver.

Description: On Windows, Siril's named pipes are not files: Siril creates
them by name (``\\.\pipe\...``), so this program only chooses names and tries
to open a pipe again until Siril has made it. These tests run only on
Windows. The Linux pipes are tested in `test_pipes_posix`, and the parts that
are the same on both systems in `test_siril_session` and the staging tests.
Siril itself is never started here.
"""

import sys
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril.core import _pipes_windows

# The module imports on any system; its tests need real Windows named pipes.
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the pipes are Windows named pipes")


def test_windows_pipes_are_unique_names_and_no_files(tmp_path: Path) -> None:
    """Two runs get different pipe names; no file is made for them."""
    first = _pipes_windows.create_pipes(str(tmp_path))
    second = _pipes_windows.create_pipes(str(tmp_path))
    assert first != second
    for pipe in (*first, *second):
        assert pipe.startswith(_pipes_windows.PIPE_PREFIX)
    assert first[0].endswith(".in") and first[1].endswith(".out")
    assert list(tmp_path.iterdir()) == []


def test_windows_open_retries_until_siril_has_made_the_pipe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Opening fails until Siril creates the pipe, so it is tried again."""
    target = tmp_path / "pipe.out"
    target.write_text("ready\n", encoding="utf-8")
    real_open = open
    attempts = {"count": 0}

    def open_after_three_tries(path: str, mode: str = "r") -> object:
        """Fail like a pipe that does not exist yet, three times.

        Returns
        -------
        handle : file object
            The opened file, from the fourth try on.

        Raises
        ------
        FileNotFoundError
            On the first three tries.
        """
        attempts["count"] += 1
        if attempts["count"] <= 3:
            raise FileNotFoundError(path)
        return real_open(path, mode)

    monkeypatch.setattr("builtins.open", open_after_three_tries)
    monkeypatch.setattr(_pipes_windows, "PIPE_RETRY_SECONDS", 0.0)
    with _pipes_windows.open_pipe(str(target), "r") as pipe:
        assert pipe.read() == "ready\n"
    assert attempts["count"] == 4


def test_windows_open_gives_up_when_told_to(tmp_path: Path) -> None:
    """A pipe Siril never makes ends the tries once the caller says stop."""
    with pytest.raises(OSError):
        _pipes_windows.open_pipe(str(tmp_path / "never.in"), "r", keep_trying=lambda: False)


def test_windows_has_no_pipe_holders_to_stop(tmp_path: Path) -> None:
    """The Windows process stop reaches everything, so this does nothing."""
    _pipes_windows.stop_pipe_holders(str(tmp_path))
