"""Purpose: Keep an SSH local port forward open to the observatory computer.

Description: Some services on the observatory computer, such as PHD2's event
server, accept only connections that start on that computer. An SSH local port
forward carries such a connection over the existing SSH login. `SshPortForward`
runs ``ssh -N -L ...`` in a background thread, restarts it if it dies, and
stops it for good on `stop`. The ssh command itself comes from
`StellarMateInterface.port_forward_command`, so the host name and the SSH
options live in one place. The local port is picked once and kept across
restarts, so a client can use one fixed address.
"""

import atexit
import logging
import shutil
import socket
import subprocess
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

# Matches PHD2Client and ConnectionManager.connection_cooldown, so every
# hardware link in the app backs off from a dead endpoint the same way.
_RESTART_COOLDOWN_SECONDS = 5.0

# How long a new ssh must stay alive before the tunnel counts as up. With
# ExitOnForwardFailure, ssh exits at once if the login or the forward fails.
# Design estimate: a login that is going to fail normally does so within the
# SSH connect timeout (2 s), but a name lookup can take longer, so this only
# decides when the "restored" message is logged, not whether the tunnel works.
_SETTLE_SECONDS = 1.0

# How long a stop waits for ssh to exit after a terminate before killing it.
_TERMINATE_WAIT_SECONDS = 2.0

# Linux tool that sets the parent-death signal on a program it starts.
_PARENT_DEATH_TOOL = "setpriv"

# How often the supervisor checks whether ssh is still running.
_POLL_SECONDS = 0.2


def pick_free_local_port() -> int:
    """Ask the operating system for a free port on the loopback address.

    Returns
    -------
    port : `int`
        A port that was free a moment ago. It is released before returning,
        so another program could in principle take it before ssh does.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class SshPortForward:
    """A supervised ``ssh -N -L`` process that is restarted when it dies.

    Parameters
    ----------
    command_for_port : `Callable` [[`int`], `list` [`str`]]
        Builds the full ssh command for a given local port.
    local_port : `int`, optional
        Local port to listen on. Chosen once, by asking the operating
        system, when omitted.
    process_launcher : `Callable`, optional
        Starts a process from a command list and returns an object with
        ``poll``, ``terminate``, ``kill`` and ``wait``. Defaults to
        `subprocess.Popen`. Tests pass a fake, so they never run ssh.
    restart_cooldown_seconds : `float`, optional
        Wait before a restart (default 5 s).
    settle_seconds : `float`, optional
        How long a new ssh must survive to count as up (default 1 s).
    executable_finder : `Callable` [[`str`], `str` or `None`], optional
        Finds a program on the search path, like `shutil.which`. Used to
        look for ``setpriv``. Tests pass a fake so they do not depend on
        the machine.

    Notes
    -----
    When ``setpriv`` is found, ssh runs as ``setpriv --pdeathsig KILL ssh
    ...``, so the kernel kills ssh when its parent thread ends. That covers
    a crash or ``kill -9`` of the app, where `atexit` does not run.
    """

    def __init__(
        self,
        command_for_port: Callable[[int], list[str]],
        local_port: int | None = None,
        process_launcher: Callable[..., Any] | None = None,
        restart_cooldown_seconds: float = _RESTART_COOLDOWN_SECONDS,
        settle_seconds: float = _SETTLE_SECONDS,
        executable_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Pick the local port and store the settings. Nothing starts yet."""
        self.local_port = local_port if local_port is not None else pick_free_local_port()
        self._command = self._with_parent_death_signal(command_for_port(self.local_port), executable_finder)
        self._process_launcher = process_launcher or subprocess.Popen
        self._restart_cooldown_seconds = restart_cooldown_seconds
        self._settle_seconds = settle_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._lock = threading.Lock()
        # True after a failure was logged as a warning, so repeats stay quiet.
        self._failure_reported = False

    @staticmethod
    def _with_parent_death_signal(
        command: list[str], executable_finder: Callable[[str], str | None]
    ) -> list[str]:
        """Prefix `command` so the kernel kills it when its parent ends.

        The parent-death signal is tied to the thread that started the
        child. Here that is the supervisor thread, which lives exactly as
        long as the tunnel, so the signal fires when the app crashes or is
        killed with SIGKILL, and it fires harmlessly after `stop`.
        ``preexec_fn`` is not used because it is not thread-safe.

        Parameters
        ----------
        command : `list` [`str`]
            The plain ssh command.
        executable_finder : `Callable` [[`str`], `str` or `None`]
            Finds ``setpriv`` on the search path.

        Returns
        -------
        command : `list` [`str`]
            The command, prefixed with ``setpriv --pdeathsig KILL`` when
            ``setpriv`` exists, otherwise unchanged.
        """
        tool_path = executable_finder(_PARENT_DEATH_TOOL)
        if tool_path is None:
            logger.debug("setpriv not found; ssh may outlive the app if it is killed")
            return list(command)
        return [tool_path, "--pdeathsig", "KILL", *command]

    @property
    def command(self) -> list[str]:
        """The ssh command this forward runs.

        Returns
        -------
        command : `list` [`str`]
            A copy of the command tokens.
        """
        return list(self._command)

    def start(self) -> None:
        """Start the supervisor thread. Does nothing if it already runs."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._supervise, daemon=True, name="SshPortForward")
            self._thread.start()
        # Stops ssh if the Python process exits without an orderly shutdown.
        atexit.register(self.stop)

    def stop(self) -> None:
        """Stop ssh and the supervisor thread. Safe to call more than once."""
        self._stop_event.set()
        with self._lock:
            thread = self._thread
            self._thread = None
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_TERMINATE_WAIT_SECONDS * 3)
        self._terminate_process()
        atexit.unregister(self.stop)

    def _terminate_process(self) -> None:
        """Terminate the ssh process, killing it if it will not leave."""
        with self._lock:
            process = self._process
            self._process = None
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=_TERMINATE_WAIT_SECONDS)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()

    def _supervise(self) -> None:
        """Run ssh, and start it again after a cooldown whenever it exits."""
        while not self._stop_event.is_set():
            try:
                process = self._process_launcher(
                    self._command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                )
            except OSError as launch_error:
                self._report_failure(f"could not start ssh: {launch_error}")
                self._stop_event.wait(self._restart_cooldown_seconds)
                continue
            with self._lock:
                self._process = process
            self._watch(process)
            if self._stop_event.is_set():
                break
            self._report_failure(self._exit_reason(process))
            self._stop_event.wait(self._restart_cooldown_seconds)

    def _watch(self, process: Any) -> None:
        """Wait until `process` exits or a stop is requested.

        Logs once when a process that failed before has stayed up.

        Parameters
        ----------
        process : `Any`
            The running ssh process.
        """
        waited = 0.0
        while not self._stop_event.is_set() and process.poll() is None:
            self._stop_event.wait(_POLL_SECONDS)
            waited += _POLL_SECONDS
            if self._failure_reported and waited >= self._settle_seconds and process.poll() is None:
                logger.info("SSH port forward on 127.0.0.1:%s restored", self.local_port)
                self._failure_reported = False

    @staticmethod
    def _exit_reason(process: Any) -> str:
        """Describe why ssh exited, using its error output if there is any.

        Parameters
        ----------
        process : `Any`
            The exited ssh process.

        Returns
        -------
        reason : `str`
            Exit code and the last error line.
        """
        last_line = ""
        stream = getattr(process, "stderr", None)
        if stream is not None:
            try:
                lines = (stream.read() or "").strip().splitlines()
            except OSError, ValueError:
                lines = []
            last_line = lines[-1] if lines else ""
        reason = f"ssh exited with code {process.poll()}"
        return f"{reason} ({last_line})" if last_line else reason

    def _report_failure(self, reason: str) -> None:
        """Log a failure: a warning the first time, debug after that.

        Parameters
        ----------
        reason : `str`
            What went wrong.
        """
        if self._failure_reported:
            logger.debug("SSH port forward on 127.0.0.1:%s still failing: %s", self.local_port, reason)
            return
        self._failure_reported = True
        logger.warning(
            "SSH port forward on 127.0.0.1:%s failed: %s. Retrying every %.0f s without more warnings.",
            self.local_port,
            reason,
            self._restart_cooldown_seconds,
        )
