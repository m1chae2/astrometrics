"""Purpose: List the connections of a process tree that leave this computer.

Description: While the app runs against the simulator, nothing it opens may
reach beyond this computer (decision D17 of the simulator plan). Two
programs check this with the same code: the launcher (`simulator.launch`)
looks every few seconds and stops the session if it finds one, and the TPM
harness (``build/tpm/seal_guard.py``) checks once at the end of a run.

The check reads ``/proc`` for the process and everything it started, then
asks ``ss`` for the TCP connections and keeps those whose peer address is
not a loopback address (`simulator.observatory_guard.is_loopback_address`).
It needs no root rights and opens no connection itself.
"""

import re
import subprocess
from pathlib import Path

from simulator.observatory_guard import is_loopback_address

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
        The output of ``ss -H -tnp``.
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
        if len(parts) < 5:
            continue
        peer = parts[4]
        users = " ".join(parts[5:])
        owner_pids = {int(number) for number in re.findall(r"pid=(\d+)", users)}
        if not owner_pids & pids:
            continue
        peer_address = peer.rsplit(":", 1)[0]
        if not is_loopback_address(peer_address):
            offending.append(line.strip())
    return offending


def non_loopback_connections(root_pid: int) -> list[str]:
    """List the TCP connections of a process tree that go beyond this computer.

    Parameters
    ----------
    root_pid : `int`
        The backend's process id.

    Returns
    -------
    offending : `list` [`str`]
        The ``ss`` lines of the offending connections, empty when all are
        local.
    """
    output = subprocess.run(
        ["ss", "-H", "-tnp"], capture_output=True, text=True, check=False, timeout=20
    ).stdout
    return parse_ss_connections(output, descendant_pids(root_pid))
