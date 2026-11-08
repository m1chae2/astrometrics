"""Purpose: Prove the TPM run is sealed and the backend stays local.

Description: The harness runs the throwaway backend in its own network
namespace (``bwrap --unshare-net``, set up by `run_tpm.sh`). This tool is a
second check on top of that namespace:

- `check_sealed` proves the run really is in a loopback-only network: the only
  network interface is ``lo``, and a connection to a public address fails.
- `non_loopback_connections` lists the TCP connections of the backend (and the
  processes it started) that go anywhere but this computer. The run aborts if
  there is one.

The connection check itself lives in `simulator.connection_guard`, which
the launcher also uses. The checks of the simulator's settings are in
`simulator.observatory_guard`.

Usage::

    python build/tpm/seal_guard.py sealed
    python build/tpm/seal_guard.py connections --pid N

Each command exits 1 and prints the reason when a check fails.
"""

import argparse
import socket
import sys
from pathlib import Path

from simulator.connection_guard import descendant_pids, non_loopback_connections, parse_ss_connections
from simulator.errors import GuardError

__all__ = [
    "check_sealed",
    "descendant_pids",
    "non_loopback_connections",
    "parse_ss_connections",
]


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
