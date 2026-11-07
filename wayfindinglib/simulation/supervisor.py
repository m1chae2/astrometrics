"""Purpose: Run one simulator process so it can never outlive its owner.

Description: The app (or the TPM harness) starts every stand-in through this
supervisor. The supervisor starts the real command in its own process group
and then watches its parent. If the parent process ends for any reason,
including a crash or ``kill -9``, or the supervisor gets SIGTERM, it ends the
whole group. That leaves no orphan INDI server or simulator behind.

Usage::

    python -m wayfindinglib.simulation.supervisor -- command [arguments]
"""

import os
import signal
import subprocess
import sys
import time

#: Seconds between looks at the parent.
POLL_SECONDS = 0.5
#: Seconds a group gets to end after SIGTERM before it is killed.
GRACE_SECONDS = 3.0


def _end_group(child: subprocess.Popen) -> None:
    """End the child's process group, politely first.

    Parameters
    ----------
    child : `subprocess.Popen`
        The supervised process, started with its own session.
    """
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError, PermissionError:
        return
    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline and child.poll() is None:
        time.sleep(0.1)
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError, PermissionError:
        pass


def supervise(command: list[str]) -> int:
    """Run `command` until it ends or the parent goes away.

    The parent is whichever process started this supervisor.

    Parameters
    ----------
    command : `list` [`str`]
        The command and its arguments.

    Returns
    -------
    status : `int`
        The command's exit status, or 143 when this supervisor was stopped.
    """
    parent_pid = os.getppid()
    child = subprocess.Popen(command, start_new_session=True)
    stopping = []

    def stop(_signal_number: int, _frame: object) -> None:
        """Remember that a stop was asked for."""
        stopping.append(True)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while child.poll() is None:
        if stopping or os.getppid() != parent_pid:
            _end_group(child)
            return 143
        time.sleep(POLL_SECONDS)
    return child.returncode


def main() -> int:
    """Read the command line and supervise.

    Returns
    -------
    status : `int`
        See `supervise`.
    """
    arguments = sys.argv[1:]
    if len(arguments) < 2 or arguments[0] != "--":
        sys.stderr.write("usage: supervisor -- command [arguments]\n")
        return 2
    return supervise(arguments[1:])


if __name__ == "__main__":
    sys.exit(main())
