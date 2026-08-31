"""Tests for the one-command-at-a-time handshake over Siril's pipes.

Siril's pipe protocol executes a single command at a time. Writing the
next command while the current one is still running aborts it, so
`send_commands` must wait for each command's own "status:" line --
published to a queue by `read_output` reading the other pipe -- before
writing the next. These tests pin that handshake without needing Siril:
they drive `send_commands` against a real FIFO and feed the queue by
hand.

They also cover `_open_pipe_or_die`, the helper that bounds the wait for
Siril to open its end of a FIFO in the first place: a plain `open()` on a
FIFO blocks until a peer connects, and if Siril dies before ever reaching
that point (a crash inside a sandbox, a version too old to understand
`-p/-r/-w`), nothing will ever connect and that open() hangs forever with
no way for the caller to notice.
"""

import os
import pathlib
import queue
import subprocess
import threading
import time

import pytest

from astrometricslib.drivers import siril_interface


@pytest.fixture
def processor(monkeypatch: pytest.MonkeyPatch) -> siril_interface.ImageProcessing:
    """Build an `ImageProcessing` without touching config or the library.

    Returns
    -------
    processor : `ImageProcessing`
        An instance with `__init__` stubbed out, usable for the pipe
        methods alone.
    """
    monkeypatch.setattr(siril_interface.ImageProcessing, "__init__", lambda self: None, raising=False)
    return siril_interface.ImageProcessing()


def _read_lines(path: str, count: int, out: list[str]) -> None:
    """Read `count` newline-terminated commands from the FIFO into `out`."""
    with open(path) as pipe:
        for line in pipe:
            out.append(line.strip())
            if len(out) >= count:
                return


def test_send_commands_waits_for_each_status_line(
    tmp_path: pathlib.Path, processor: siril_interface.ImageProcessing
) -> None:
    """The second command is not written until the first reports status."""
    command_pipe = str(tmp_path / "siril_command.in")
    os.mkfifo(command_pipe)
    status_queue: queue.Queue[str] = queue.Queue()
    received: list[str] = []

    reader = threading.Thread(target=_read_lines, args=(command_pipe, 3, received), daemon=True)
    reader.start()

    writer = threading.Thread(
        target=processor.send_commands,
        args=(command_pipe, ["convert light", "stack r_light"]),
        kwargs={"status_queue": status_queue},
        daemon=True,
    )
    writer.start()

    # Nothing has been published to the queue, so the writer must be
    # parked after the first command. If it raced ahead, "stack" would
    # already be in `received`.
    reader.join(timeout=2)
    assert received == ["convert light"], received

    status_queue.put("status: success convert\n")
    status_queue.put("status: success stack\n")

    reader.join(timeout=5)
    writer.join(timeout=5)
    assert received == ["convert light", "stack r_light", "exit"]


def test_send_commands_stops_after_an_error_status(
    tmp_path: pathlib.Path, processor: siril_interface.ImageProcessing
) -> None:
    """A failed command aborts the script instead of sending the rest."""
    command_pipe = str(tmp_path / "siril_command.in")
    os.mkfifo(command_pipe)
    status_queue: queue.Queue[str] = queue.Queue()
    status_queue.put("status: error command interrupted\n")
    received: list[str] = []

    reader = threading.Thread(target=_read_lines, args=(command_pipe, 2, received), daemon=True)
    reader.start()

    processor.send_commands(command_pipe, ["convert light", "stack r_light"], status_queue=status_queue)
    reader.join(timeout=5)

    # "stack" is never written; "exit" still is, so Siril shuts down.
    assert received == ["convert light", "exit"]


def test_send_commands_gives_up_when_no_status_arrives(
    tmp_path: pathlib.Path,
    processor: siril_interface.ImageProcessing,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A silent Siril bounds the wait rather than hanging forever."""
    monkeypatch.setattr(siril_interface, "SIRIL_COMMAND_TIMEOUT_SECONDS", 0.1)
    command_pipe = str(tmp_path / "siril_command.in")
    os.mkfifo(command_pipe)
    received: list[str] = []

    reader = threading.Thread(target=_read_lines, args=(command_pipe, 2, received), daemon=True)
    reader.start()

    processor.send_commands(command_pipe, ["convert light", "stack r_light"], status_queue=queue.Queue())
    reader.join(timeout=5)

    assert received == ["convert light", "exit"]


def test_read_output_publishes_status_lines_to_the_queue(
    tmp_path: pathlib.Path, processor: siril_interface.ImageProcessing
) -> None:
    """Every status line reaches the queue, so the writer can advance."""
    output_pipe = str(tmp_path / "siril_command.out")
    os.mkfifo(output_pipe)
    target_folder = tmp_path / "work"
    (target_folder / "process").mkdir(parents=True)
    status_queue: queue.Queue[str] = queue.Queue()

    def write_output() -> None:
        with open(output_pipe, "w") as pipe:
            pipe.write("log: converting\n")
            pipe.write("status: success convert\n")
            pipe.write("status: error stack\n")
            pipe.flush()

    writer = threading.Thread(target=write_output, daemon=True)
    writer.start()

    result = processor.read_output(output_pipe, str(target_folder), "M 13", status_queue=status_queue)
    writer.join(timeout=5)

    # An error status ends the read with no stacked file.
    assert result is None
    # The non-status "log:" line is not published; the two status lines are.
    assert status_queue.get_nowait().strip() == "status: success convert"
    assert status_queue.get_nowait().strip() == "status: error stack"
    assert status_queue.empty()


def test_open_pipe_or_die_returns_once_a_peer_connects(tmp_path: pathlib.Path) -> None:
    """The common case: a peer opens the pipe before the deadline."""
    pipe_path = str(tmp_path / "command.in")
    os.mkfifo(pipe_path)
    process = subprocess.Popen(["sleep", "5"])
    try:
        reader = threading.Thread(target=lambda: open(pipe_path).close(), daemon=True)
        reader.start()

        pipe = siril_interface._open_pipe_or_die(pipe_path, "w", process, timeout=5)
        pipe.close()
        reader.join(timeout=5)
    finally:
        process.kill()
        process.wait()


def test_open_pipe_or_die_raises_once_the_process_exits(tmp_path: pathlib.Path) -> None:
    """A dead Siril is reported, instead of waited on forever."""
    pipe_path = str(tmp_path / "command.in")
    os.mkfifo(pipe_path)
    # Nothing ever opens the other end of this FIFO.
    process = subprocess.Popen(["true"])
    process.wait()

    started_at = time.monotonic()
    with pytest.raises(RuntimeError, match="exited"):
        siril_interface._open_pipe_or_die(pipe_path, "w", process, timeout=30)
    elapsed = time.monotonic() - started_at

    # The failure must be reported promptly off the dead process, not
    # only once the full 30s timeout budget elapses.
    assert elapsed < 5


def test_open_pipe_or_die_times_out_on_a_process_that_never_connects(tmp_path: pathlib.Path) -> None:
    """A stuck-but-alive Siril is bounded by the timeout, not waited on."""
    pipe_path = str(tmp_path / "command.in")
    os.mkfifo(pipe_path)
    process = subprocess.Popen(["sleep", "30"])
    try:
        with pytest.raises(TimeoutError):
            siril_interface._open_pipe_or_die(pipe_path, "w", process, timeout=0.3)
    finally:
        process.kill()
        process.wait()


def test_send_commands_uses_the_bounded_open_when_given_a_process(
    tmp_path: pathlib.Path, processor: siril_interface.ImageProcessing
) -> None:
    """A dead Siril fails send_commands promptly, not by hanging it."""
    command_pipe = str(tmp_path / "siril_command.in")
    os.mkfifo(command_pipe)
    process = subprocess.Popen(["true"])
    process.wait()

    started_at = time.monotonic()
    # send_commands swallows the failure and logs it rather than raising,
    # matching its existing "Pipe write error" behavior -- the point here
    # is that it returns promptly instead of hanging in open().
    processor.send_commands(command_pipe, ["convert light"], process=process)
    elapsed = time.monotonic() - started_at

    assert elapsed < 5
