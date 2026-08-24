r"""Measure how many Siril runs this machine should allow at once.

Stacking dominates a batch run -- 87 of 199 minutes on 2026-08-24, 44%
of the wall clock -- and it is serialised by a slot limit, so that limit
sets the floor on how long a run can take. Siril is internally
multithreaded and took roughly 688% CPU of 12 cores while holding the
only slot, about 57% utilisation: enough headroom that a second
concurrent run may finish two targets in less than twice the time, and
enough contention that it may not.

Which it is depends on the machine, so it is measured rather than
assumed. The same target is stacked at each candidate slot count and the
wall clock compared.

Two costs are deliberately surfaced rather than hidden. Peak scratch
scales with concurrency -- one 167-frame colour target held 69GB -- so
the disk headroom needed multiplies. And a run that oversubscribes the
CPU does not merely go slower: on the 2026-08-23 run it pushed stacks
past their timeout, turning a slow stack into a failed one.

Usage::

    python -m astrometricslib.scripts.benchmark_siril_concurrency \\
        --target "NGC 6888" --slots 1 --slots 2 --slots 3
"""

import argparse
import logging
import shutil
import sys
import time
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
    astrometrics: Any,
    target_ids: list[str],
    camera_name: str,
    focal_length_mm: float | None,
    slot_count: int,
) -> dict[str, Any]:
    """Stack the given targets once with a fixed number of Siril slots.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The high-level interface.
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
        ``free_disk_gb_low`` (the lowest free space seen).
    """
    # Overriding the accessor rather than the file keeps the benchmark
    # from editing the user's configuration to measure it.
    original_accessor = type(astrometrics.config).get_siril_concurrency
    type(astrometrics.config).get_siril_concurrency = lambda _self: slot_count
    try:
        started_at = time.monotonic()
        summary = astrometrics.process_all_targets(
            target_ids=target_ids,
            camera_name=camera_name,
            focal_length_mm=focal_length_mm,
        )
        wall_seconds = time.monotonic() - started_at
    finally:
        type(astrometrics.config).get_siril_concurrency = original_accessor

    return {
        "slot_count": slot_count,
        "wall_seconds": round(wall_seconds, 1),
        "succeeded": len(summary.succeeded),
        "failed": len(summary.failed),
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
        print("None of the requested targets exist in the catalog.")
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
            print("The requested targets have no frames with a camera recorded.")
            return 2
        camera_name = cameras.most_common(1)[0][0]

    target_ids = [target.id for target in targets]
    print(f"Benchmarking {len(target_ids)} target(s): {', '.join(target_ids)}")
    print(f"Camera: {camera_name}   optic: {focal_length_mm or 'any'}")
    if len(target_ids) < max(slot_counts):
        print(
            f"WARNING: {len(target_ids)} target(s) cannot fill {max(slot_counts)} slots; "
            "the higher counts will measure the same serial time."
        )
    print(f"Free disk before: {free_disk_gigabytes('/'):.0f}GB\n")

    measurements = []
    for slot_count in slot_counts:
        print(f"--- {slot_count} slot(s) ---")
        measurement = time_one_slot_count(astrometrics, target_ids, camera_name, focal_length_mm, slot_count)
        measurement["free_disk_gb_after"] = round(free_disk_gigabytes("/"))
        measurements.append(measurement)
        print(
            f"  {measurement['wall_seconds']:.1f}s  "
            f"succeeded={measurement['succeeded']} failed={measurement['failed']}  "
            f"free disk after: {measurement['free_disk_gb_after']}GB"
        )

    print("\n==========================================")
    print("SIRIL CONCURRENCY BENCHMARK")
    print(f"{'slots':>6s} {'wall_s':>9s} {'speedup':>8s} {'ok':>4s} {'failed':>7s}")
    baseline = measurements[0]["wall_seconds"] if measurements else 0.0
    for measurement in measurements:
        speedup = baseline / measurement["wall_seconds"] if measurement["wall_seconds"] else 0.0
        print(
            f"{measurement['slot_count']:>6d} {measurement['wall_seconds']:>9.1f} "
            f"{speedup:>7.2f}x {measurement['succeeded']:>4d} {measurement['failed']:>7d}"
        )
    print("\nSet the winner as [Processing.Parallelism] siril_concurrency.")
    print("A count that raises `failed` is oversubscribing: a stack pushed past its timeout")
    print("is worse than a slow one. Check free disk too -- scratch scales with concurrency.")
    print("==========================================")
    return 0


if __name__ == "__main__":
    sys.exit(run_benchmark())
