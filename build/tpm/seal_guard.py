"""Purpose: Prove the TPM run is sealed and the backend stays local.

Description: The harness runs the throwaway backend in its own network
namespace (``bwrap --unshare-net``, set up by `run_tpm.sh`). This tool is a
second check on top of that namespace:

- `check_sealed` proves the run really is in a loopback-only network: the only
  network interface is ``lo``, and a connection to a public address fails.
- `non_loopback_connections` lists the TCP connections of the backend (and the
  processes it started) that go anywhere but this computer. The run aborts if
  there is one.

The app does not need these checks, so they live with the harness. The
checks of the simulator's settings are in
`wayfindinglib.simulation.observatory_guard`.

Usage::

    python build/tpm/seal_guard.py sealed
    python build/tpm/seal_guard.py connections --pid N

Each command exits 1 and prints the reason when a check fails.
"""

import argparse
import re
import socket
import subprocess
import sys
from pathlib import Path

from wayfindinglib.exceptions import GuardError
from wayfindinglib.simulation.observatory_guard import is_loopback_address


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


#: Public addresses a sealed run must not be able to reach (a public DNS
#: server and a documentation address). Only a connection attempt is made.
PROBE_ADDRESSES = (("1.1.1.1", 443), ("8.8.8.8", 53))


def check_sealed() -> None:
    """Prove this process is in a network namespace with only loopback.

    Raises
    ------
    GuardError
        If a network interface other than ``lo`` exists, or a connection to a
        public address succeeds.
    """
    # /proc/net/dev lists the interfaces of this process's own network
    # namespace (a mounted /sys/class/net can still show the host's).
    lines = Path("/proc/net/dev").read_text().splitlines()[2:]
    names = sorted(line.split(":", 1)[0].strip() for line in lines if ":" in line)
    if names != ["lo"]:
        raise GuardError(f"The run is not sealed: network interfaces {names} exist; only 'lo' may.")
    for address in PROBE_ADDRESSES:
        try:
            probe = socket.create_connection(address, timeout=2)
        except OSError:
            continue
        probe.close()
        raise GuardError(f"The run is not sealed: a connection to {address[0]}:{address[1]} succeeded.")


def main() -> int:
    """Run one check from the command line.

    Returns
    -------
    status : `int`
        0 when the check passes, 1 when it fails.
    """
    parser = argparse.ArgumentParser(description="Seal and connection checks for the TPM run.")
    commands = parser.add_subparsers(dest="command", required=True)
    connections = commands.add_parser("connections")
    connections.add_argument("--pid", type=int, required=True)
    commands.add_parser("sealed")
    arguments = parser.parse_args()
    try:
        if arguments.command == "sealed":
            check_sealed()
            print("Seal guard: the run is sealed (only loopback, no route out).")
        else:
            offending = non_loopback_connections(arguments.pid)
            if offending:
                print(
                    "SEAL GUARD FAILED: the backend has connections beyond this computer:\n  "
                    + "\n  ".join(offending),
                    file=sys.stderr,
                )
                return 1
            print("Seal guard: the backend has no connection beyond this computer.")
    except GuardError as error:
        print(f"SEAL GUARD FAILED: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
