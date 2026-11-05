"""Purpose: Tests for the supervised SSH port forward.

Description: Checks the ssh command the StellarMate driver builds, and that
`SshPortForward` restarts a dead process after the cooldown, rebuilds the
command on each start, logs the first failure as a warning and repeats at
debug, calls the tunnel restored only when the local port accepts a
connection, keeps its log lines out of job logs, and stops cleanly. A fake
process and a fake port probe stand in for ssh and the network.
"""

import logging
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import time
from collections.abc import Callable
from typing import Any

import pytest

from astrometricslib.foundation.logging import get_log_context
from wayfindinglib.drivers.ssh_port_forward import SshPortForward, pick_free_local_port
from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface


class FakeProcess:
    """A stand-in for a running ssh process."""

    def __init__(self, exit_code: int | None = None, alive_polls: int = 0) -> None:
        """Start alive, or already exited when `exit_code` is given.

        With `alive_polls`, the process reports running for that many polls
        and then exits with `exit_code`, like an ssh that hangs on a lookup.
        """
        self.exit_code = exit_code
        self.alive_polls = alive_polls
        self.terminated = False
        self.killed = False
        self.stderr = None
        self.ignore_terminate = False

    def poll(self) -> int | None:
        """Report whether the process has exited.

        Returns
        -------
        exit_code : `int` or `None`
            The exit code, or `None` while running.
        """
        if self.alive_polls > 0 and self.exit_code is not None:
            self.alive_polls -= 1
            return None
        return self.exit_code

    def terminate(self) -> None:
        """Record a terminate request and exit unless told to ignore it."""
        self.terminated = True
        if not self.ignore_terminate:
            self.exit_code = -15

    def kill(self) -> None:
        """Record a kill and exit."""
        self.killed = True
        self.exit_code = -9

    def wait(self, timeout: float | None = None) -> int | None:
        """Return at once, or time out when terminate is being ignored.

        Returns
        -------
        exit_code : `int`
            The exit code.

        Raises
        ------
        subprocess.TimeoutExpired
            If the process is still running.
        """
        if self.exit_code is None:
            raise subprocess.TimeoutExpired("ssh", timeout or 0)
        return self.exit_code


class FakeLauncher:
    """Hands out fake processes in order and records the commands."""

    def __init__(self, processes: list[FakeProcess]) -> None:
        """Store the processes to hand out; the last one repeats."""
        self.processes = processes
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **_kwargs: Any) -> FakeProcess:
        """Record `command` and hand out the next fake process.

        Returns
        -------
        process : `FakeProcess`
            The next process in the list.
        """
        self.commands.append(command)
        index = min(len(self.commands) - 1, len(self.processes) - 1)
        return self.processes[index]


def _wait_until(condition: Callable[[], bool], timeout: float = 3.0) -> bool:
    """Poll `condition` until it is true or `timeout` passes.

    Returns
    -------
    satisfied : `bool`
        Whether the condition became true in time.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return condition()


def _forward(
    launcher: FakeLauncher,
    cooldown: float = 0.05,
    port_probe: Callable[[int], bool] = lambda _port: True,
    command_for_port: Callable[[int], list[str]] = lambda port: ["ssh", str(port)],
) -> SshPortForward:
    """Build a forward over a fake launcher with a short cooldown.

    Returns
    -------
    forward : `SshPortForward`
        A forward that has not started. The fake probe says the port is
        open unless a test passes another.
    """
    return SshPortForward(
        command_for_port,
        process_launcher=launcher,
        restart_cooldown_seconds=cooldown,
        port_probe=port_probe,
        executable_finder=lambda _name: None,
    )


def test_port_forward_command_uses_alias_forward_spec_and_options() -> None:
    """The command carries the host alias, the -L spec and every option."""
    command = StellarMateInterface(host_alias="stellarmate.local").port_forward_command(5555, 4400)

    assert command[:2] == ["ssh", "-N"]
    assert command[-3:] == ["-L", "127.0.0.1:5555:localhost:4400", "stellarmate.local"]
    options = {command[i + 1] for i, flag in enumerate(command) if flag == "-o"}
    assert options == {
        "ConnectTimeout=2",
        "ExitOnForwardFailure=yes",
        "BatchMode=yes",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=3",
    }


def test_local_port_is_chosen_once_and_kept() -> None:
    """The port picked at construction is the one in every command."""
    launcher = FakeLauncher([FakeProcess(exit_code=1), FakeProcess()])
    forward = _forward(launcher)
    port = forward.local_port
    assert port == pick_free_local_port() or port > 0
    forward.start()
    try:
        assert _wait_until(lambda: len(launcher.commands) >= 2)
    finally:
        forward.stop()
    assert {tuple(command) for command in launcher.commands} == {("ssh", str(port))}


def test_restarts_after_the_process_exits_honoring_the_cooldown() -> None:
    """A dead ssh is started again, but not before the cooldown passed."""
    launcher = FakeLauncher([FakeProcess(exit_code=255), FakeProcess()])
    forward = _forward(launcher, cooldown=0.3)
    started = time.monotonic()
    forward.start()
    try:
        assert _wait_until(lambda: len(launcher.commands) == 2)
        assert time.monotonic() - started >= 0.3
    finally:
        forward.stop()


def test_first_failure_warns_and_repeats_are_debug(caplog: pytest.LogCaptureFixture) -> None:
    """One warning for the first failure, debug for the ones after it."""
    launcher = FakeLauncher([FakeProcess(exit_code=255)])
    forward = _forward(launcher, cooldown=0.01)
    with caplog.at_level(logging.DEBUG, logger="wayfindinglib.drivers.ssh_port_forward"):
        forward.start()
        try:
            assert _wait_until(lambda: len(launcher.commands) >= 4)
        finally:
            forward.stop()

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    debugs = [record for record in caplog.records if record.levelno == logging.DEBUG]
    assert len(warnings) == 1
    assert len(debugs) >= 2


def test_recovery_is_logged_once(caplog: pytest.LogCaptureFixture) -> None:
    """After a failure, a process that stays up logs a single restored line."""
    launcher = FakeLauncher([FakeProcess(exit_code=255), FakeProcess()])
    forward = _forward(launcher, cooldown=0.01)
    with caplog.at_level(logging.INFO, logger="wayfindinglib.drivers.ssh_port_forward"):
        forward.start()
        try:
            assert _wait_until(lambda: any("restored" in record.getMessage() for record in caplog.records))
        finally:
            forward.stop()
    assert sum("restored" in record.getMessage() for record in caplog.records) == 1


def test_stop_terminates_the_process_and_is_safe_twice() -> None:
    """stop() ends ssh, and a second call changes nothing."""
    process = FakeProcess()
    launcher = FakeLauncher([process])
    forward = _forward(launcher)
    forward.start()
    assert _wait_until(lambda: len(launcher.commands) == 1)
    time.sleep(0.05)

    forward.stop()
    forward.stop()

    assert process.terminated
    assert not process.killed
    assert len(launcher.commands) == 1


def test_stop_kills_a_process_that_ignores_terminate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A process that outlives terminate is killed."""
    monkeypatch.setattr("wayfindinglib.drivers.ssh_port_forward._TERMINATE_WAIT_SECONDS", 0.05)
    process = FakeProcess()
    process.ignore_terminate = True
    forward = _forward(FakeLauncher([process]))
    forward.start()
    assert _wait_until(lambda: forward._process is process)

    forward.stop()

    assert process.killed


def test_command_is_prefixed_with_setpriv_when_it_is_installed() -> None:
    """With setpriv present, ssh runs under a parent-death kill signal."""
    forward = SshPortForward(
        lambda port: ["ssh", str(port)], local_port=4567, executable_finder=lambda _name: "/usr/bin/setpriv"
    )

    assert forward.command == ["/usr/bin/setpriv", "--pdeathsig", "KILL", "ssh", "4567"]


def test_command_is_plain_ssh_when_setpriv_is_missing() -> None:
    """Without setpriv, the command is the plain ssh command."""
    forward = SshPortForward(
        lambda port: ["ssh", str(port)], local_port=4567, executable_finder=lambda _name: None
    )

    assert forward.command == ["ssh", "4567"]


def test_command_is_rebuilt_on_every_start() -> None:
    """A changed address option shows up in the next start's command."""
    launcher = FakeLauncher([FakeProcess(exit_code=255), FakeProcess()])
    # The first call happens in the constructor; the next two are the starts.
    address_options = [["-o", "HostName=unused"], ["-o", "HostName=none"], ["-o", "HostName=10.0.0.5"]]
    calls: list[int] = []

    def command_for_port(port: int) -> list[str]:
        """Build a command whose address option changes per call.

        Returns
        -------
        command : `list` [`str`]
            An ssh command with the next address option.
        """
        calls.append(port)
        return ["ssh", *address_options[min(len(calls) - 1, 2)], str(port)]

    forward = _forward(launcher, cooldown=0.01, command_for_port=command_for_port)
    forward.start()
    try:
        assert _wait_until(lambda: len(launcher.commands) >= 2)
    finally:
        forward.stop()

    assert launcher.commands[0][1:3] == ["-o", "HostName=none"]
    assert launcher.commands[1][1:3] == ["-o", "HostName=10.0.0.5"]


def test_alive_but_not_listening_is_not_restored(caplog: pytest.LogCaptureFixture) -> None:
    """An ssh that lives on without opening the port never logs restored."""
    launcher = FakeLauncher([FakeProcess(exit_code=255, alive_polls=3)])
    forward = _forward(launcher, cooldown=0.01, port_probe=lambda _port: False)
    with caplog.at_level(logging.DEBUG, logger="wayfindinglib.drivers.ssh_port_forward"):
        forward.start()
        try:
            assert _wait_until(lambda: len(launcher.commands) >= 4)
        finally:
            forward.stop()

    assert not any("restored" in record.getMessage() for record in caplog.records)
    assert sum(record.levelno == logging.WARNING for record in caplog.records) == 1


def test_real_recovery_logs_restored_once(caplog: pytest.LogCaptureFixture) -> None:
    """A failure, then an answering port, gives one warning, one restored."""
    launcher = FakeLauncher([FakeProcess(exit_code=255, alive_polls=2), FakeProcess()])
    forward = _forward(launcher, cooldown=0.01, port_probe=lambda _port: len(launcher.commands) >= 2)
    with caplog.at_level(logging.INFO, logger="wayfindinglib.drivers.ssh_port_forward"):
        forward.start()
        try:
            assert _wait_until(lambda: any("restored" in record.getMessage() for record in caplog.records))
            time.sleep(0.1)
        finally:
            forward.stop()

    assert sum("restored" in record.getMessage() for record in caplog.records) == 1
    assert sum(record.levelno == logging.WARNING for record in caplog.records) == 1


def test_supervisor_records_carry_a_background_request_id() -> None:
    """Supervisor records carry a request id and no job id."""
    seen: list[dict[str, object]] = []

    class ContextCapture(logging.Filter):
        """Notes the log context in force when a record is made."""

        def filter(self, record: logging.LogRecord) -> bool:
            """Store the current context and keep the record.

            Returns
            -------
            keep : `bool`
                Always True.
            """
            seen.append(get_log_context())
            return True

    capture = ContextCapture()
    logger = logging.getLogger("wayfindinglib.drivers.ssh_port_forward")
    logger.addFilter(capture)
    launcher = FakeLauncher([FakeProcess(exit_code=255)])
    forward = _forward(launcher, cooldown=0.01)
    try:
        forward.start()
        try:
            assert _wait_until(lambda: len(launcher.commands) >= 2)
        finally:
            forward.stop()
    finally:
        logger.removeFilter(capture)

    assert seen
    for context in seen:
        assert str(context.get("request_id", "")).startswith(
            f"background:ssh-port-forward:{forward.local_port}"
        )
        assert "job_id" not in context
