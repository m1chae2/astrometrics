r"""Measure how many Siril runs this machine should allow at once.

This tool runs stacking tasks with different concurrency limits to
help you find the fastest setting for your computer without running
out of memory or causing tasks to time out.

Usage::

    python -m astrometricslib.scripts.benchmark_siril_concurrency \\
        --target "NGC 6888" --slots 1 --slots 2 --slots 3
"""

import argparse
import logging
import os
import shutil
import sys
from typing import Any

from astrometricslib import Astrometrics

logger = logging.getLogger(__name__)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering the target, the slot counts, and the optic.
    """
    parser = argparse.ArgumentParser(
        prog="benchmark_siril_concurrency",
        description="Time the same stack at several Siril slot counts to pick siril_concurrency.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        required=True,
        metavar="TARGET_ID",
        help=(
            "Target to stack, repeatable. Use as many as the largest slot count being tested, "
            "since one target can only ever occupy one slot -- testing concurrency with a single "
            "target measures nothing."
        ),
    )
    parser.add_argument(
        "--slots",
        action="append",
        dest="slot_counts",
        type=int,
        metavar="N",
        help="Slot count to test, repeatable. Defaults to 1 and 2.",
    )
    parser.add_argument(
        "--optic",
        type=float,
        dest="focal_length_mm",
        metavar="MM",
        help="Restrict to this focal length. Defaults to the configured primary optic.",
    )
    parser.add_argument(
        "--camera",
        default=None,
        help="Camera to stack. Defaults to whichever camera the first target has most frames for.",
    )
    return parser


def free_disk_gigabytes(path: str) -> float:
    """Report free space on the filesystem holding `path`.

    Parameters
    ----------
    path : `str`
        Any path on the filesystem of interest.

    Returns
    -------
    free_gigabytes : `float`
        Free space in gigabytes.
    """
    return shutil.disk_usage(path).free / 1_000_000_000


def time_one_slot_count(
    target_ids: list[str],
    camera_name: str,
    focal_length_mm: float | None,
    slot_count: int,
) -> dict[str, Any]:
    """Stack the given targets once, in a fresh process, at a fixed slot count.

    Each measurement runs as its own subprocess. Python's forkserver
    start method creates its server process once and forks every later
    worker from it, so the workers inherit the environment as it stood
    when that server started -- an override changed between measurements
    in one process never reaches them. Measured directly: with the
    environment set to 2, workers still logged "Waiting for a free Siril
    slot (1 allowed concurrently)", the value from the previous
    measurement.

    Parameters
    ----------
    target_ids : `list` [`str`]
        Targets to stack concurrently.
    camera_name : `str`
        Camera whose frames are stacked.
    focal_length_mm : `float` or `None`
        Optic to restrict to.
    slot_count : `int`
        Slots to allow for this measurement.

    Returns
    -------
    measurement : `dict`
        ``slot_count``, ``wall_seconds``, ``succeeded``, ``failed`` and
        ``peak_siril`` (the most concurrent Siril processes observed).
    """
    import json
    import subprocess  # ruff: ignore[suspicious-subprocess-import] -- a fresh process is the point
    import threading

    worker_source = (
        "import json, sys, time;"
        f"sys.path.insert(0, {os.getcwd()!r});"
        "from astrometricslib import Astrometrics;"
        "a = Astrometrics();"
        "t0 = time.monotonic();"
        f"s = a.process_all_targets(target_ids={target_ids!r}, camera_name={camera_name!r},"
        f" focal_length_mm={focal_length_mm!r});"
        "print('BENCHMARK_RESULT ' + json.dumps({"
        "'wall_seconds': round(time.monotonic() - t0, 1),"
        "'succeeded': len(s.succeeded), 'failed': len(s.failed)}))"
    )

    environment = dict(os.environ)
    environment["ASTROMETRICS_SIRIL_CONCURRENCY"] = str(slot_count)
    environment["HEADLESS"] = "1"

    peak_siril = 0
    stop_sampling = threading.Event()

    def _sample_siril_processes() -> None:
        """Track the most concurrent Siril processes seen."""
        nonlocal peak_siril
        while not stop_sampling.wait(2.0):
            try:
                # Absolute path: a partial one resolves through PATH,
                # which is not fixed for a sampler running this long.
                running = subprocess.run(
                    ["/usr/bin/pgrep", "-x", "-c", "siril"], capture_output=True, text=True
                )
                peak_siril = max(peak_siril, int(running.stdout.strip() or 0))
            except Exception as sampling_error:
                logger.debug("Siril process sample failed: %s", sampling_error)
                continue

    sampler = threading.Thread(target=_sample_siril_processes, daemon=True)
    sampler.start()
    try:
        completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true] -- fixed argv
            [sys.executable, "-c", worker_source], capture_output=True, text=True, env=environment
        )
    finally:
        stop_sampling.set()
        sampler.join(timeout=5)

    payload = {"wall_seconds": 0.0, "succeeded": 0, "failed": 0}
    for line in completed.stdout.splitlines():
        if line.startswith("BENCHMARK_RESULT "):
            payload = json.loads(line[len("BENCHMARK_RESULT ") :])
    if not completed.stdout.count("BENCHMARK_RESULT"):
        print(f"  (measurement produced no result; stderr tail: {completed.stderr[-300:]})", flush=True)

    return {
        "slot_count": slot_count,
        "wall_seconds": payload["wall_seconds"],
        "succeeded": payload["succeeded"],
        "failed": payload["failed"],
        "peak_siril": peak_siril,
    }


def run_benchmark(argv: list[str] | None = None) -> int:
    """Time a stack at each requested slot count and report the results.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``2`` when the targets could not be resolved.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    astrometrics = Astrometrics()
    slot_counts = arguments.slot_counts or [1, 2]
    focal_length_mm = arguments.focal_length_mm or astrometrics.config.get_primary_focal_length_mm()

    wanted = {target_id.strip().casefold() for target_id in arguments.target_ids}
    targets = [target for target in astrometrics.targets.list() if target.id.casefold() in wanted]
    if not targets:
        print("None of the requested targets exist in the catalog.", flush=True)
        return 2

    camera_name = arguments.camera
    if not camera_name:
        from collections import Counter

        cameras: Counter = Counter()
        for target in targets:
            for frame in target.frames or []:
                if frame.camera:
                    cameras[frame.camera] += 1
        if not cameras:
            print("The requested targets have no frames with a camera recorded.", flush=True)
            return 2
        camera_name = cameras.most_common(1)[0][0]

    target_ids = [target.id for target in targets]
    print(f"Benchmarking {len(target_ids)} target(s): {', '.join(target_ids)}", flush=True)
    print(f"Camera: {camera_name}   optic: {focal_length_mm or 'any'}", flush=True)
    if len(target_ids) < max(slot_counts):
        print(
            f"WARNING: {len(target_ids)} target(s) cannot fill {max(slot_counts)} slots; "
            "the higher counts will measure the same serial time.",
            flush=True,
        )
    print(f"Free disk before: {free_disk_gigabytes('/'):.0f}GB\n", flush=True)

    measurements = []
    for slot_count in slot_counts:
        print(f"--- {slot_count} slot(s) ---", flush=True)
        measurement = time_one_slot_count(target_ids, camera_name, focal_length_mm, slot_count)
        measurement["free_disk_gb_after"] = round(free_disk_gigabytes("/"))
        measurements.append(measurement)
        print(
            f"  {measurement['wall_seconds']:.1f}s  "
            f"succeeded={measurement['succeeded']} failed={measurement['failed']}  "
            f"peak Siril={measurement['peak_siril']}  "
            f"free disk after: {measurement['free_disk_gb_after']}GB",
            flush=True,
        )

    print("\n==========================================", flush=True)
    print("SIRIL CONCURRENCY BENCHMARK", flush=True)
    print(
        f"{'slots':>6s} {'wall_s':>9s} {'speedup':>8s} {'ok':>4s} {'failed':>7s} {'peakSiril':>10s}",
        flush=True,
    )
    baseline = measurements[0]["wall_seconds"] if measurements else 0.0
    for measurement in measurements:
        speedup = baseline / measurement["wall_seconds"] if measurement["wall_seconds"] else 0.0
        print(
            f"{measurement['slot_count']:>6d} {measurement['wall_seconds']:>9.1f} "
            f"{speedup:>7.2f}x {measurement['succeeded']:>4d} {measurement['failed']:>7d} "
            f"{measurement['peak_siril']:>10d}",
            flush=True,
        )
    print("\nSet the winner as [Processing.Parallelism] siril_concurrency.", flush=True)
    print("peakSiril below the slot count means the limit was never the constraint -- the", flush=True)
    print("measurement says nothing about that setting, so treat it as untested, not as equal.", flush=True)
    print("A count that raises `failed` is oversubscribing: a stack pushed past its timeout", flush=True)
    print("is worse than a slow one. Check free disk too -- scratch scales with concurrency.", flush=True)
    print("==========================================", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run_benchmark())
