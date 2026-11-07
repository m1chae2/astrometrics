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
import collections
import logging
import shutil
import socket
import subprocess
import threading
from collections.abc import Callable
from typing import Any

from astrometricslib.drivers import OfflineModeError
from astrometricslib.jobs import background_log_context

logger = logging.getLogger(__name__)

# Matches PHD2Client and ConnectionManager.connection_cooldown, so every
# hardware link in the app backs off from a dead endpoint the same way.
_RESTART_COOLDOWN_SECONDS = 5.0

# How long one probe waits to connect to the forwarded local port. The port is
# on this computer, so a listening port answers at once.
_PROBE_TIMEOUT_SECONDS = 0.2

# How long a stop waits for ssh to exit after a terminate before killing it.
_TERMINATE_WAIT_SECONDS = 2.0

# How many of ssh's last error lines to keep for the failure message.
_STDERR_LINES_KEPT = 20

# Linux tool that sets the parent-death signal on a program it starts.
_PARENT_DEATH_TOOL = "setpriv"

# How often the supervisor checks whether ssh is still running.
_POLL_SECONDS = 0.2


def local_port_accepts_connections(local_port: int) -> bool:
    """Report whether something is listening on a loopback port.

    ssh opens the local end of the forward only after the login worked, so a
    connection here is the sign that the tunnel is really up. A process that
    is merely alive (for example, still looking up a host name) does not
    pass this test.

    Parameters
    ----------
    local_port : `int`
        Port on 127.0.0.1 to try.

    Returns
    -------
    accepts : `bool`
        True when a TCP connection was made.
    """
    try:
        with socket.create_connection(("127.0.0.1", local_port), timeout=_PROBE_TIMEOUT_SECONDS):
            return True
    except OSError:
        return False


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
    port_probe : `Callable` [[`int`], `bool`], optional
        Tells whether the forwarded local port accepts a connection. The
        tunnel counts as up only when it does. Defaults to
        `local_port_accepts_connections`. Tests pass a fake.
    executable_finder : `Callable` [[`str`], `str` or `None`], optional
        Finds a program on the search path, like `shutil.which`. Used to
        look for ``setpriv``. Tests pass a fake so they do not depend on
        the machine.

    Notes
    -----
    When ``setpriv`` is found, ssh runs as ``setpriv --pdeathsig KILL ssh
    ...``, so the kernel kills ssh when its parent thread ends. That covers
    a crash or ``kill -9`` of the app, where `atexit` does not run.

    The command is built again on every start, so a changed host address
    (for example the remembered one used while the name will not resolve)
    takes effect on the next restart. The supervisor thread logs under a
    background request id, so its lines stay out of job logs.
    """

    def __init__(
        self,
        command_for_port: Callable[[int], list[str]],
        local_port: int | None = None,
        process_launcher: Callable[..., Any] | None = None,
        restart_cooldown_seconds: float = _RESTART_COOLDOWN_SECONDS,
        port_probe: Callable[[int], bool] = local_port_accepts_connections,
        executable_finder: Callable[[str], str | None] = shutil.which,
    ) -> None:
        """Pick the local port and store the settings. Nothing starts yet."""
        self.local_port = local_port if local_port is not None else pick_free_local_port()
        self._command_for_port = command_for_port
        self._executable_finder = executable_finder
        try:
            self._command = self._build_command()
        except OfflineModeError:
            # The launch sends nothing off this computer. The supervisor asks
            # again and keeps waiting, quietly.
            self._command = []
        self._process_launcher = process_launcher or subprocess.Popen
        self._restart_cooldown_seconds = restart_cooldown_seconds
        self._port_probe = port_probe
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._process: Any | None = None
        self._lock = threading.Lock()
        # True after a failure was logged as a warning, so repeats stay quiet.
        self._failure_reported = False

    def _build_command(self) -> list[str]:
        """Build the full command from the current host settings.

        Returns
        -------
        command : `list` [`str`]
            The ssh command, wrapped with the parent-death signal.
        """
        return self._with_parent_death_signal(
            self._command_for_port(self.local_port), self._executable_finder
        )

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
        """The ssh command the forward ran last, or will run first.

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
        # Background marker, so the job log router never copies these lines
        # into a job's log. Context variables are per thread, so set it here.
        with background_log_context(f"ssh-port-forward:{self.local_port}"):
            self._supervise_loop()

    def _supervise_loop(self) -> None:
        """Launch ssh in a loop until a stop is requested."""
        while not self._stop_event.is_set():
            try:
                self._command = self._build_command()
                process = self._process_launcher(
                    self._command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True
                )
            except OfflineModeError as offline_error:
                logger.debug("The port forward waits: %s", offline_error)
                self._stop_event.wait(self._restart_cooldown_seconds)
                continue
            except OSError as launch_error:
                self._report_failure(f"could not start ssh: {launch_error}")
                self._stop_event.wait(self._restart_cooldown_seconds)
                continue
            with self._lock:
                self._process = process
            error_lines, drain_thread = self._drain_stderr(process)
            self._watch(process)
            if drain_thread is not None:
                drain_thread.join(timeout=1.0)
            if self._stop_event.is_set():
                break
            self._report_failure(self._exit_reason(process, error_lines))
            self._stop_event.wait(self._restart_cooldown_seconds)

    def _watch(self, process: Any) -> None:
        """Wait until `process` exits or a stop is requested.

        Each cycle it checks the forwarded port. The first time the port
        accepts a connection after a reported failure, it logs "restored"
        once. A process that is alive but not yet listening is not up.

        Parameters
        ----------
        process : `Any`
            The running ssh process.
        """
        while not self._stop_event.is_set() and process.poll() is None:
            if self._failure_reported and self._port_probe(self.local_port) and process.poll() is None:
                logger.info("SSH port forward on 127.0.0.1:%s restored", self.local_port)
                self._failure_reported = False
            self._stop_event.wait(_POLL_SECONDS)

    @staticmethod
    def _drain_stderr(process: Any) -> tuple[collections.deque[str], threading.Thread | None]:
        """Read ssh's error output on a thread while ssh runs.

        ssh writes a line to its error output for every refused channel
        (for example when PHD2 is stopped). If nobody reads the pipe, it
        fills up and ssh freezes while its port still answers. The thread
        keeps the pipe empty and remembers only the last few lines.

        Parameters
        ----------
        process : `Any`
            The running ssh process.

        Returns
        -------
        lines : `collections.deque` [`str`]
            The last `_STDERR_LINES_KEPT` error lines, filled as they arrive.
        thread : `threading.Thread` or `None`
            The reader thread, or `None` when the process has no error pipe.
        """
        lines: collections.deque[str] = collections.deque(maxlen=_STDERR_LINES_KEPT)
        stream = getattr(process, "stderr", None)
        if stream is None:
            return lines, None

        def read_lines() -> None:
            """Copy lines from the pipe until it closes."""
            try:
                for line in stream:
                    stripped = str(line).strip()
                    if stripped:
                        lines.append(stripped)
            except OSError, ValueError:
                pass  # The pipe closed under us; there is nothing more to read.

        thread = threading.Thread(target=read_lines, daemon=True, name="SshPortForwardStderr")
        thread.start()
        return lines, thread

    @staticmethod
    def _exit_reason(process: Any, error_lines: collections.deque[str] | None = None) -> str:
        """Describe why ssh exited, using its error output if there is any.

        Parameters
        ----------
        process : `Any`
            The exited ssh process.
        error_lines : `collections.deque` [`str`], optional
            The last error lines the drain thread collected.

        Returns
        -------
        reason : `str`
            Exit code and the last error line.
        """
        last_line = error_lines[-1] if error_lines else ""
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
