r"""Purpose: Tests for the Linux and Windows rules in `platform_tools`.

Description: Linux is the main system, so each helper must give exactly the
old Linux behavior there; Windows gets its own rule. The tests choose the
system by patching `platform_tools.on_windows`, so both halves run on any
computer. Symbolic links are tested only where the system allows them.
"""

import os
import sys
from pathlib import Path

import pytest

from astrometricslib.foundation import platform_tools, run_abandonment


@pytest.fixture
def linux(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the helpers as on Linux."""
    monkeypatch.setattr(platform_tools, "on_windows", lambda: False)


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run the helpers as on Windows."""
    monkeypatch.setattr(platform_tools, "on_windows", lambda: True)


def test_linux_splits_a_program_setting_like_a_shell(linux: None) -> None:
    """Linux keeps shell splitting, quotes and all."""
    assert platform_tools.split_program_command("flatpak run org.siril.Siril") == [
        "flatpak",
        "run",
        "org.siril.Siril",
    ]
    assert platform_tools.split_program_command('"/opt/my apps/siril-cli" -d x') == [
        "/opt/my apps/siril-cli",
        "-d",
        "x",
    ]


def test_windows_keeps_backslashes_and_spaces_of_a_program_path(windows: None, tmp_path: Path) -> None:
    """A Windows path with spaces names one program, quoted or not."""
    program = tmp_path / "Program Files" / "Siril" / "bin" / "siril-cli.exe"
    program.parent.mkdir(parents=True)
    program.write_bytes(b"")
    assert platform_tools.split_program_command(str(program)) == [str(program)]
    assert platform_tools.split_program_command(f'"{program}"') == [str(program)]
    quoted_with_argument = platform_tools.split_program_command(r'"C:\Program Files\Tool\tool.exe" --fast')
    assert quoted_with_argument == [r"C:\Program Files\Tool\tool.exe", "--fast"]


def test_siril_paths_are_quoted_only_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paths are quoted for Siril on Windows only; Linux stays as it was."""
    monkeypatch.setattr(platform_tools, "on_windows", lambda: False)
    assert platform_tools.quote_siril_path("/home/jane/work/M 31") == "/home/jane/work/M 31"
    monkeypatch.setattr(platform_tools, "on_windows", lambda: True)
    assert platform_tools.quote_siril_path(r"C:\Users\Jane Doe\work") == r'"C:\Users\Jane Doe\work"'


@pytest.mark.skipif(sys.platform == "win32", reason="Windows allows symbolic links only with special rights")
def test_linux_stages_a_frame_with_a_symbolic_link(linux: None, tmp_path: Path) -> None:
    """Linux staging is unchanged: a symbolic link to the library frame."""
    source = tmp_path / "frame.fits"
    source.write_bytes(b"data")
    destination = tmp_path / "light_00001.fits"
    assert platform_tools.stage_file(str(source), str(destination)) == platform_tools.STAGED_BY_LINK
    assert destination.is_symlink()
    assert os.readlink(destination) == str(source)


def test_windows_stages_a_frame_with_a_hard_link_on_the_same_drive(windows: None, tmp_path: Path) -> None:
    """On one drive the frame is hard-linked: no copy, no special rights."""
    source = tmp_path / "frame.fits"
    source.write_bytes(b"data")
    destination = tmp_path / "light_00001.fits"
    assert platform_tools.stage_file(str(source), str(destination)) == platform_tools.STAGED_BY_LINK
    assert not destination.is_symlink()
    assert os.stat(destination).st_ino == os.stat(source).st_ino


def test_windows_copies_a_frame_it_cannot_link(
    windows: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A frame that cannot be hard-linked is copied."""

    def refuse(*_arguments: object) -> None:
        """Fail like a hard link across drives.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("The system cannot move the file to a different disk drive")

    monkeypatch.setattr(platform_tools.os, "link", refuse)
    source = tmp_path / "frame.fits"
    source.write_bytes(b"data")
    destination = tmp_path / "light_00001.fits"
    assert platform_tools.stage_file(str(source), str(destination)) == platform_tools.STAGED_BY_COPY
    assert destination.read_bytes() == b"data"
    assert os.stat(destination).st_ino != os.stat(source).st_ino


def test_windows_stops_a_process_tree_with_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Windows the run's stop uses taskkill /T /F, not signals."""
    calls: list[list[str]] = []

    class _Running:
        """A process that is still running until taskkill is called."""

        pid = 4242
        stopped = False

        def poll(self) -> int | None:
            """Report whether it has ended.

            Returns
            -------
            code : `int` or `None`
                `None` while running.
            """
            return 0 if self.stopped else None

        def wait(self, timeout: float) -> int:
            """Wait for it to end.

            Returns
            -------
            code : `int`
                Always 0.
            """
            return 0

    process = _Running()

    def fake_run(command: list[str], **_keywords: object) -> object:
        """Record the command and end the process.

        Returns
        -------
        completed : `subprocess.CompletedProcess`
            A finished command.
        """
        calls.append(command)
        process.stopped = True
        return platform_tools.subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_abandonment, "on_windows", lambda: True)
    monkeypatch.setattr(platform_tools.subprocess, "run", fake_run)
    run_abandonment.stop_process_group(process)  # type: ignore[arg-type]
    assert calls == [["taskkill", "/PID", "4242", "/T", "/F"]]


def test_worker_processes_fork_on_linux_and_spawn_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux keeps "fork"; Windows, which has no "fork", uses "spawn"."""
    asked: list[str] = []
    monkeypatch.setattr(platform_tools.multiprocessing, "get_context", lambda method: asked.append(method))
    monkeypatch.setattr(platform_tools, "on_windows", lambda: False)
    platform_tools.worker_process_context()
    monkeypatch.setattr(platform_tools, "on_windows", lambda: True)
    platform_tools.worker_process_context()
    assert asked == ["fork", "spawn"]
