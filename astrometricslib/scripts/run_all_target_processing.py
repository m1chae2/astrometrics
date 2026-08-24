"""Batch processing script that runs the full pipeline on every target.

Runs stacking, astrometry, photometry, and spectroscopy (where
applicable) for every target's ZWO ASI533MM Pro frames, using only the
public `Astrometrics.process_all_targets` facade.

A failure in any single target's pipeline -- including a real
worker-process crash -- is caught and recorded rather than
propagated, so it never halts the rest of the batch; see
`astrometricslib.utilities.parallel_batch.run_parallel_batch` for the
full fault-tolerance behavior (per-target exception handling, plus
automatic worker-pool restart on a process crash).
"""

import logging
import os
import sys

# Disable opening Siril GUI when stacking completes -- must be set
# before astrometricslib is imported, since a batch run across every
# target should never pop open a GUI window mid-run.
os.environ["HEADLESS"] = "1"

from astrometricslib import Astrometrics


def run_full_processing() -> None:
    """Run the full processing pipeline for every target in the catalog."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    targets = astrometrics.targets.list()
    if not targets:
        print("No targets found in the database catalog.")
        return

    print(f"Found {len(targets)} target(s) in the catalog.")
    print("Running the full pipeline (stacking, astrometry, photometry, spectroscopy) for each target...")

    # `camera_name` is a required, keyword-only argument on
    # `process_all_targets` (no default), so it's passed explicitly here.
    # The full camera name is used rather than a partial match like
    # "533mm", since `run_full_pipeline` matches it as a case-insensitive
    # substring against each frame's camera -- a partial string risks
    # matching more than one camera if the catalog ever grows a
    # similarly-named one.
    summary = astrometrics.process_all_targets(camera_name="ZWO ASI 533MM Pro")

    stacked_targets = [
        (target_id, result["stack_outputs"])
        for target_id, result in summary.results.items()
        if result.get("stack_outputs")
    ]

    print("\n==========================================")
    print("BATCH PROCESSING RUN COMPLETE")
    print(f"Processed targets: {len(summary.succeeded)}")
    print(f"Skipped targets (no frames for this camera): {len(summary.skipped)}")
    print(f"Failed targets: {len(summary.failed)}")
    if summary.failed:
        print("\nFailed Targets Summary:")
        for target_id, reason in summary.failed:
            print(f"  - {target_id}: {reason}")
    if summary.skipped:
        print("\nSkipped Targets Summary:")
        for target_id, reason in summary.skipped:
            print(f"  - {target_id}: {reason}")
    print(f"Stacked targets: {len(stacked_targets)}")
    if stacked_targets:
        print("\nStacked Targets Summary:")
        for target_id, outputs in stacked_targets:
            stack_descriptions = [f"{stack_type.capitalize()}={path}" for stack_type, path in outputs.items()]
            print(f"  - {target_id}: {', '.join(stack_descriptions)}")
    print("==========================================")


# This guard is REQUIRED, not stylistic boilerplate: `process_all_targets`
# runs its targets across a `ProcessPoolExecutor`, and Python 3.14 on Linux
# defaults to the "forkserver" start method, which re-imports this module
# inside every worker process. Without the guard, each worker re-executes
# the whole script body, tries to start its own worker pool, and the run
# dies immediately with "An attempt has been made to start a new process
# before the current process has finished its bootstrapping phase."
# The script really is imported -- by multiprocessing itself.
if __name__ == "__main__":
    run_full_processing()
