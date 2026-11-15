"""Purpose: Tests for the parts of the Siril session that run on every system.

Description: The session picks nothing by system. It stops Siril with
`stop_process_group` and gets its pipes from `pipes`, which holds the one
version for the system the program runs on. These tests check the session's
use of them with stand-ins, so Siril is never started.
"""

from pathlib import Path

import pytest

from astrometricslib._internal.drivers.siril_interface import pipes, session


def test_stopping_siril_uses_the_shared_process_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Siril and everything it started are stopped by `stop_process_group`."""
    stopped: list[object] = []
    holders: list[str] = []
    monkeypatch.setattr(session, "stop_process_group", stopped.append)
    monkeypatch.setattr(pipes, "stop_pipe_holders", holders.append)
    process = object()
    session.SirilSession._kill_process_tree(object(), process, workdir="/work")  # type: ignore[arg-type]
    assert stopped == [process]
    assert holders == ["/work"]


def test_stopping_siril_without_a_work_folder_skips_the_pipe_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no work folder given, only the process is stopped."""
    holders: list[str] = []
    monkeypatch.setattr(session, "stop_process_group", lambda process: None)
    monkeypatch.setattr(pipes, "stop_pipe_holders", holders.append)
    session.SirilSession._kill_process_tree(object(), object())  # type: ignore[arg-type]
    assert holders == []


def test_the_session_gets_its_pipes_from_the_pipes_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`create_named_pipes` returns what `pipes.create_pipes` gives."""
    monkeypatch.setattr(pipes, "create_pipes", lambda base_path: (f"{base_path}/in", f"{base_path}/out"))
    assert session.SirilSession.create_named_pipes(object(), str(tmp_path)) == (  # type: ignore[arg-type]
        f"{tmp_path}/in",
        f"{tmp_path}/out",
    )
