"""Purpose: Measure the throwaway backend's RPC timings, CPU and memory.

Description: The backend does not log how long each RPC call takes (its log
has only failures and warnings, and the HTTP access log is off). So the
per-method timings come from the other end: `flow.mjs` records the round-trip
time of every `/api/rpc` call the page makes. This script turns those records
into count, median (p50) and 95th percentile (p95) per method. It also
samples the backend process from `/proc` while the run goes on, to get its
CPU seconds and peak resident memory, and counts failed RPC calls in the
backend log.

Two commands:

* ``sample``: run in the background by `run_tpm.sh`. Every second it appends
  one JSON line with the backend's resident memory and CPU time. It stops
  when the process ends or when it receives SIGTERM.
* ``summarize``: read the flow records, the samples and the log, and write
  ``backend-metrics.json``.

Safety: it only reads ``/proc/<pid>`` of the process it is told about and
the files it is given. It refuses a process whose ``ASTROMETRICS_PORT`` is
not the throwaway port it is told about, and refuses port 5000, so it cannot
sample the user's real backend.

Usage::

    .venv/bin/python build/tpm/rpc_timings.py sample --pid 123 \
        --port 47123 --out samples.jsonl
    .venv/bin/python build/tpm/rpc_timings.py summarize \
        --log backend.log \
        --out backend-metrics.json
"""

import argparse
import json
import math
import os
import re
import signal
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

#: The real backend's port. The sampler refuses to look at a process using it.
REAL_BACKEND_PORT = 5000

_running = True


def read_process_port(pid: int) -> int | None:
    """Read the ``ASTROMETRICS_PORT`` a process was started with.

    Parameters
    ----------
    pid : `int`
        The process id.

    Returns
    -------
    port : `int` or `None`
        The port, or `None` if it cannot be read.
    """
    try:
        environment = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return None
    for entry in environment:
        if entry.startswith(b"ASTROMETRICS_PORT="):
            return int(entry.split(b"=", 1)[1])
    return None


def read_sample(pid: int) -> dict[str, float] | None:
    """Read the resident memory and CPU time of one process.

    Parameters
    ----------
    pid : `int`
        The process id.

    Returns
    -------
    sample : `dict` [`str`, `float`] or `None`
        ``rss_kb``, ``peak_rss_kb`` and ``cpu_seconds`` (user plus system
        time), or `None` if the process is gone.
    """
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return None
    rss = re.search(r"VmRSS:\s+(\d+) kB", status)
    peak = re.search(r"VmHWM:\s+(\d+) kB", status)
    # Fields after the command name (in parentheses; it may hold spaces).
    fields = stat.rsplit(")", 1)[1].split()
    ticks_per_second = os.sysconf("SC_CLK_TCK")
    cpu_seconds = (int(fields[11]) + int(fields[12])) / ticks_per_second
    return {
        "rss_kb": float(rss.group(1)) if rss else math.nan,
        "peak_rss_kb": float(peak.group(1)) if peak else math.nan,
        "cpu_seconds": cpu_seconds,
    }


def _stop(_signal_number: int, _frame: object) -> None:
    """Ask the sampling loop to finish."""
    global _running
    _running = False


def sample_command(arguments: argparse.Namespace) -> int:
    """Sample the backend process once a second until it ends.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        ``pid``, ``port`` and ``out``.

    Returns
    -------
    status : `int`
        0 on success, 2 if the process is not the throwaway backend.
    """
    if arguments.port == REAL_BACKEND_PORT or read_process_port(arguments.pid) != arguments.port:
        print("Refusing to sample: that process is not the throwaway backend of this run.", file=sys.stderr)
        return 2
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    with open(arguments.out, "a", encoding="utf-8") as output:
        while _running:
            sample = read_sample(arguments.pid)
            if sample is None:
                break
            output.write(json.dumps({"time": time.time(), **sample}) + "\n")
            output.flush()
            time.sleep(1.0)
    return 0


def percentile(values: list[float], fraction: float) -> float:
    """Give a percentile by linear interpolation.

    Parameters
    ----------
    values : `list` [`float`]
        The values, in any order. Must not be empty.
    fraction : `float`
        0.95 for the 95th percentile.

    Returns
    -------
    value : `float`
        The percentile.
    """
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarize_command(arguments: argparse.Namespace) -> int:
    """Write per-method timings, CPU seconds and peak memory.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        ``flow``, ``samples``, ``log`` and ``out``.

    Returns
    -------
    status : `int`
        0 on success.
    """
    flow = json.loads(Path(arguments.flow).read_text())
    by_method: dict[str, list[float]] = defaultdict(list)
    for call in flow["rpc"]:
        by_method[call["method"]].append(call["ms"])
    methods = {
        method: {
            "count": len(times),
            "p50_ms": statistics.median(times),
            "p95_ms": percentile(times, 0.95),
        }
        for method, times in sorted(by_method.items())
    }
    samples = [json.loads(line) for line in Path(arguments.samples).read_text().splitlines() if line.strip()]
    peak_rss_kb = max((sample["peak_rss_kb"] for sample in samples), default=None)
    cpu_seconds = (samples[-1]["cpu_seconds"] - samples[0]["cpu_seconds"]) if len(samples) > 1 else None
    failures = 0
    log_path = Path(arguments.log)
    if log_path.exists():
        failures = sum(
            1
            for line in log_path.read_text(errors="replace").splitlines()
            if " RPC " in line and " failed " in line
        )
    Path(arguments.out).write_text(
        json.dumps(
            {
                "note": "Per-method times are the page's round trips (the backend logs no timing).",
                "methods": methods,
                "backend_peak_rss_mb": None if peak_rss_kb is None else peak_rss_kb / 1024.0,
                "backend_cpu_seconds": cpu_seconds,
                "sample_count": len(samples),
                "backend_rpc_failures_logged": failures,
            },
            indent=2,
        )
        + "\n"
    )
    return 0


def main() -> int:
    """Run the command named on the command line.

    Returns
    -------
    status : `int`
        The command's exit status.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    sample = commands.add_parser("sample")
    sample.add_argument("--pid", type=int, required=True)
    sample.add_argument("--port", type=int, required=True)
    sample.add_argument("--out", required=True)
    summarize = commands.add_parser("summarize")
    summarize.add_argument("--flow", required=True)
    summarize.add_argument("--samples", required=True)
    summarize.add_argument("--log", required=True)
    summarize.add_argument("--out", required=True)
    arguments = parser.parse_args()
    return sample_command(arguments) if arguments.command == "sample" else summarize_command(arguments)


if __name__ == "__main__":
    sys.exit(main())
