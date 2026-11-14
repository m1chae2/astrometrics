r"""Purpose: Tests for the Windows branches of the Siril driver.

Description: On Windows, Siril's named pipes are not files (Siril creates
them by name, ``\.\pipe\...``), the app stops Siril with taskkill, and
frames are staged by hard link or copy. These tests patch the system choice,
so they run on Linux continuous integration as well; the Linux branches are
covered by the existing Siril tests. Siril itself is never started here.
"""

import os
from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril_interface import session, staging


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the Siril driver's code as on Windows."""
    monkeypatch.setattr(session, "on_windows", lambda: True)
    monkeypatch.setattr(staging, "on_windows", lambda: True)


def test_windows_pipes_are_unique_names_and_no_files(windows: None, tmp_path: Path) -> None:
    """Two runs get different pipe names; no file is made for them."""
    first = session.SirilSession.create_named_pipes(object(), str(tmp_path))  # type: ignore[arg-type]
    second = session.SirilSession.create_named_pipes(object(), str(tmp_path))  # type: ignore[arg-type]
    assert first != second
    for pipe in (*first, *second):
        assert pipe.startswith(session.WINDOWS_PIPE_PREFIX)
    assert first[0].endswith(".in") and first[1].endswith(".out")
    assert list(tmp_path.iterdir()) == []


def test_windows_open_retries_until_siril_has_made_the_pipe(
    windows: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    monkeypatch.setattr(session, "WINDOWS_PIPE_RETRY_SECONDS", 0.0)
    with session._open_named_pipe(str(target), "r") as pipe:
        assert pipe.read() == "ready\n"
    assert attempts["count"] == 4


def test_windows_open_gives_up_when_told_to(windows: None, tmp_path: Path) -> None:
    """A pipe Siril never makes ends the tries once the caller says stop."""
    with pytest.raises(OSError):
        session._open_named_pipe(str(tmp_path / "never.in"), "r", keep_trying=lambda: False)


def test_windows_stops_siril_through_the_shared_process_stop(
    windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows the session stops Siril with the shared stop (taskkill)."""
    stopped: list[object] = []
    monkeypatch.setattr(session, "stop_process_group", stopped.append)
    process = object()
    session.SirilSession._kill_process_tree(object(), process)  # type: ignore[arg-type]
    assert stopped == [process]


def test_windows_cache_key_follows_the_library_frame_through_a_hard_link(
    windows: None, tmp_path: Path
) -> None:
    """A hard link keeps the library frame's identity; a copy does not."""
    library = tmp_path / "library"
    library.mkdir()
    dark = library / "dark.fits"
    dark.write_bytes(b"dark data")
    first_run = tmp_path / "run1"
    second_run = tmp_path / "run2"
    copy_run = tmp_path / "run3"
    for folder in (first_run, second_run, copy_run):
        folder.mkdir()
    os.link(dark, first_run / "dark_00001.fits")
    os.link(dark, second_run / "dark_00001.fits")
    (copy_run / "dark_00001.fits").write_bytes(dark.read_bytes())
    os.utime(copy_run / "dark_00001.fits", (dark.stat().st_atime, dark.stat().st_mtime))
    key = staging._calibration_source_fingerprint
    assert key(str(first_run)) == key(str(second_run))
    assert key(str(copy_run)) != key(str(first_run))
