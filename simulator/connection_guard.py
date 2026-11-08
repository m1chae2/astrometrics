"""Purpose: List the connections of a process tree that leave this computer.

Description: While the app runs against the simulator, nothing it opens may
reach beyond this computer (decision D17 of the simulator plan). Two
programs check this with the same code: the launcher (`simulator.launch`)
looks every few seconds and stops the session if it finds one, and the TPM
harness (``build/tpm/seal_guard.py``) checks once at the end of a run.

The check reads ``/proc`` for the process and everything it started, then
asks ``ss`` for the TCP and UDP sockets and keeps those whose peer address is
not a loopback address (`astrometricslib.drivers.is_loopback_host`).
A UDP socket with no peer (a listener) is not a connection and is skipped.
It needs no root rights and opens no connection itself.

This guard sees a connection after it exists. It ends the session; it does
not stop the first packet. The offline settings and the settings checks are
what prevent traffic. If ``ss`` itself fails, `non_loopback_connections`
raises `OSError`, so the caller can count the failure and fail closed.
"""

import re
import subprocess
from pathlib import Path

from astrometricslib.drivers import is_loopback_host

__all__ = ["descendant_pids", "non_loopback_connections", "parse_ss_connections"]


def descendant_pids(root_pid: int) -> set[int]:
    """Return a process and all the processes it started.

    Parameters
    ----------
    root_pid : `int`
        The first process.

    Returns
    -------
    pids : `set` [`int`]
        `root_pid` and every descendant found in ``/proc``.
    """
    parent_of: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
        except OSError:
            continue
        # The command name sits in parentheses and may contain spaces.
        after_name = stat.rsplit(")", 1)[1].split()
        parent_of[int(entry.name)] = int(after_name[1])
    found = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parent_of.items():
            if parent in found and pid not in found:
                found.add(pid)
                changed = True
    return found


def parse_ss_connections(output: str, pids: set[int]) -> list[str]:
    """Pick the connections of the given processes that leave this computer.

    Parameters
    ----------
    output : `str`
        The output of ``ss -H -tunp``. Each line starts with the socket type
        (``tcp`` or ``udp``), then the state, the two queue sizes, the local
        address and the peer address.
    pids : `set` [`int`]
        The processes to look at.

    Returns
    -------
    offending : `list` [`str`]
        One text per connection whose peer address is not a loopback address.
    """
    offending = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 6:
            continue
        # With -t and -u, the first column is the type: tcp or udp.
        peer = parts[5]
        if peer in ("*", "*:*", "0.0.0.0:*", "[::]:*"):
            continue  # an unconnected UDP socket has no peer
        users = " ".join(parts[6:])
        owner_pids = {int(number) for number in re.findall(r"pid=(\d+)", users)}
        if not owner_pids & pids:
            continue
        peer_address = peer.rsplit(":", 1)[0]
        if not is_loopback_host(peer_address):
            offending.append(line.strip())
    return offending


def non_loopback_connections(root_pid: int) -> list[str]:
    """List the TCP and UDP sockets of a process tree that leave this computer.

    Parameters
    ----------
    root_pid : `int`
        The backend's process id.

    Returns
    -------
    offending : `list` [`str`]
        The ``ss`` lines of the offending connections, empty when all are
        local.

    Raises
    ------
    OSError
        If ``ss`` cannot run or exits with an error. An empty answer from a
        failed run is not the same as "no connections".
    """
    result = subprocess.run(["ss", "-H", "-tunp"], capture_output=True, text=True, check=False, timeout=20)
    if result.returncode != 0:
        raise OSError(f"ss failed with exit code {result.returncode}: {result.stderr.strip()}")
    output = result.stdout
    return parse_ss_connections(output, descendant_pids(root_pid))
