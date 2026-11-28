"""Purpose: Measure the throwaway backend's RPC timings, CPU and memory.

Description: Per-method timings come from both ends. `flow.mjs` records the
round-trip time of every `/api/rpc` call the page makes. The throwaway
backend runs with ``ASTROMETRICS_LOG_LEVEL=VERBOSE``, so it also logs one
``RPC_TIMING`` line per call to its log, with the time the call waited for a
worker (``wait_ms``: a heavy-lane place or a free thread) and the time its
work ran (``run_ms``); see ``backend/services/infrastructure/rpc_timing.py``.
This script turns both into count, median (p50) and 95th percentile (p95)
per method. It also samples the backend process from `/proc` while the run
goes on, to get its CPU seconds and peak resident memory, and counts failed
RPC calls in the backend log.

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

#: The word that starts each backend timing line (``RPC_TIMING_MARKER`` in
#: ``backend/services/infrastructure/rpc_timing.py``).
RPC_TIMING_MARKER = "RPC_TIMING"

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


def read_backend_timings(log_text: str) -> list[dict[str, object]]:
    """Read the backend's per-call timing lines from its log.

    Parameters
    ----------
    log_text : `str`
        The whole backend log. Lines without the timing marker, and timing
        lines that do not hold a JSON object, are skipped.

    Returns
    -------
    timings : `list` [`dict`]
        One entry per call: ``method``, ``lane``, ``wait_ms``, ``run_ms`` and
        ``ok``.
    """
    timings: list[dict[str, object]] = []
    for line in log_text.splitlines():
        marker_at = line.find(RPC_TIMING_MARKER + " ")
        if marker_at < 0:
            continue
        try:
            # The backend's log format may add context after the message, so
            # read only the JSON object that follows the marker.
            entry, _ = json.JSONDecoder().raw_decode(line[marker_at + len(RPC_TIMING_MARKER) + 1 :])
        except ValueError:
            continue
        if isinstance(entry, dict) and "method" in entry and "run_ms" in entry and "wait_ms" in entry:
            timings.append(entry)
    return timings


def summarize_backend_timings(timings: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    """Give each method's backend run time and lane wait, p50 and p95.

    Parameters
    ----------
    timings : `list` [`dict`]
        The entries from `read_backend_timings`.

    Returns
    -------
    methods : `dict` [`str`, `dict`]
        For each method, in name order: ``count``, ``lane`` (the lanes its
        calls used, joined by ``+`` when more than one), ``failed`` (calls
        that raised), ``run_p50_ms``, ``run_p95_ms``, ``wait_p50_ms`` and
        ``wait_p95_ms``.
    """
    by_method: dict[str, list[dict[str, object]]] = defaultdict(list)
    for entry in timings:
        by_method[str(entry["method"])].append(entry)
    summary: dict[str, dict[str, object]] = {}
    for method, entries in sorted(by_method.items()):
        run_times = [float(entry["run_ms"]) for entry in entries]
        wait_times = [float(entry["wait_ms"]) for entry in entries]
        summary[method] = {
            "count": len(entries),
            "lane": "+".join(sorted({str(entry.get("lane", "")) for entry in entries})),
            "failed": sum(1 for entry in entries if entry.get("ok") is False),
            "run_p50_ms": statistics.median(run_times),
            "run_p95_ms": percentile(run_times, 0.95),
            "wait_p50_ms": statistics.median(wait_times),
            "wait_p95_ms": percentile(wait_times, 0.95),
        }
    return summary


def summarize_command(arguments: argparse.Namespace) -> int:
    """Write per-method timings, CPU seconds and peak memory.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        ``flow`` (optional: a processing run has none), ``samples``, ``log``
        and ``out``.

    Returns
    -------
    status : `int`
        0 on success.
    """
    # A processing run has no browser flow, so no page round trips.
    flow = json.loads(Path(arguments.flow).read_text()) if arguments.flow else {"rpc": []}
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
    backend_methods: dict[str, dict[str, object]] = {}
    log_path = Path(arguments.log)
    if log_path.exists():
        log_text = log_path.read_text(errors="replace")
        failures = sum(1 for line in log_text.splitlines() if " RPC " in line and " failed " in line)
        backend_methods = summarize_backend_timings(read_backend_timings(log_text))
    Path(arguments.out).write_text(
        json.dumps(
            {
                "note": (
                    "methods: the page's round trips. backend_methods: the backend's own time per call, "
                    "split into the wait for a worker (wait_*) and the run (run_*) (RPC_TIMING lines)."
                ),
                "methods": methods,
                "backend_methods": backend_methods,
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
    summarize.add_argument("--flow")
    summarize.add_argument("--samples", required=True)
    summarize.add_argument("--log", required=True)
    summarize.add_argument("--out", required=True)
    arguments = parser.parse_args()
    return sample_command(arguments) if arguments.command == "sample" else summarize_command(arguments)


if __name__ == "__main__":
    sys.exit(main())
