"""Purpose: Check that nothing else is busy before a TPM baseline run.

Description: A baseline is only worth keeping if the computer was quiet. A
running upload, a database backup or the real Astrometrics backend takes disk,
network and processor time away from the run and changes its numbers. This
script looks for those three things and refuses (exit code 1) if it finds
one. It prints what to stop. It never stops, kills or changes anything.

What it looks for:

* a ``rclone copy`` or ``rclone sync`` process (``pgrep -f``);
* a ``backup_library_databases`` process (``pgrep -f``);
* a listener on TCP port 5000, the real backend's port (``ss -ltn``).

Usage::

    .venv/bin/python build/tpm/preflight.py [--port 5000]
"""

import argparse
import os
import shutil
import subprocess
import sys
from collections.abc import Callable

#: The real backend's port. The harness never uses it.
REAL_BACKEND_PORT = 5000
#: ``pgrep -f`` patterns (extended regular expressions) and what to tell the
#: user about each.
PROCESS_CHECKS = (
    ("rclone (copy|sync)", "an rclone upload or sync is running: let it finish, or stop it yourself"),
    ("backup_library_databases", "a library database backup is running: wait for it to finish"),
)


def run_command(command: list[str]) -> str | None:
    """Run a program and give its output.

    Parameters
    ----------
    command : `list` [`str`]
        The program and its arguments (no shell).

    Returns
    -------
    output : `str` or `None`
        Standard output, an empty text when the program found nothing (exit
        code 1), or `None` when the program is missing or fails otherwise.
    """
    if shutil.which(command[0]) is None:
        return None
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=10)
    if result.returncode in (0, 1):
        return result.stdout
    return None


def find_problems(
    port: int = REAL_BACKEND_PORT, run: Callable[[list[str]], str | None] = run_command
) -> list[str]:
    """Look for the things that spoil a baseline run.

    Parameters
    ----------
    port : `int`, optional
        The real backend's port.
    run : callable, optional
        The function that runs a program (a test passes a stand-in for
        ``pgrep`` and ``ss``).

    Returns
    -------
    problems : `list` [`str`]
        One sentence per problem, saying what to stop. Empty if the computer
        is quiet. A missing ``pgrep`` or ``ss`` is itself a problem, because
        the check cannot be made.
    """
    problems = []
    own = str(os.getpid())
    for pattern, advice in PROCESS_CHECKS:
        output = run(["pgrep", "-f", pattern])
        if output is None:
            problems.append(f"cannot run pgrep, so the check for '{pattern}' was not made")
            continue
        found = [line for line in output.split() if line != own]
        if found:
            problems.append(f"{advice} (process ids: {', '.join(found)})")
    output = run(["ss", "-H", "-ltn", f"sport = :{port}"])
    if output is None:
        problems.append(f"cannot run ss, so the check for the real backend on port {port} was not made")
    elif output.strip():
        problems.append(
            f"the real Astrometrics backend is listening on port {port}: close the app "
            "(build/linux/run_astrometrics.sh stop, run by you; this script never stops anything)"
        )
    return problems


def main() -> int:
    """Run the check and print the result.

    Returns
    -------
    status : `int`
        0 if the computer is quiet, 1 if anything must be stopped first.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=REAL_BACKEND_PORT)
    arguments = parser.parse_args()
    problems = find_problems(arguments.port)
    if not problems:
        print("Preflight: no upload, no database backup and no real backend is running.")
        return 0
    print("PREFLIGHT FAILED. Stop these first (this script stops nothing):", file=sys.stderr)
    for problem in problems:
        print(f"  - {problem}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
