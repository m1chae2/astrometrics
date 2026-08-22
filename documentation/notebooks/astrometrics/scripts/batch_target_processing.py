"""Batch processing script that cycles through all targets in the catalog.

Performs image stacking, astrometry analysis, photometry analysis, and
spectroscopy analysis for each target, catching any failures to proceed
to the next target. Targets are processed concurrently via
Astrometrics.process_all_targets(); the actual pipeline sequencing and
process-pool orchestration live in astrometricslib itself
(tasks.target_tasks.pipeline_tasks.run_full_pipeline and
astrometricslib.run_parallel_batch), so this script is just a thin
entry point.

**Architecture Note:** This script demonstrates the Orchestration Layer
of the high-level interface Architecture. The `Astrometrics` client coordinates
the full Scientific Analysis pipeline concurrently across multiple targets.
"""

import logging
import os
import sys

# Disable opening Siril GUI when stacking completes
os.environ["HEADLESS"] = "1"

from astrometricslib import Astrometrics


def run_batch_processing() -> None:
    """Initialize Astrometrics and run the pipeline for every target.

    Delegates the actual per-target pipeline sequencing and process-pool
    orchestration to Astrometrics.process_all_targets(); this script only
    handles startup logging and printing the final run summary.
    """
    # Configure logging to output Siril stacking details to stdout
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    targets = astrometrics.targets.list()
    if not targets:
        print("No targets found in the database catalog.")
        return

    print(f"Found {len(targets)} targets in the catalog.")

    summary = astrometrics.process_all_targets([target.id for target in targets])

    stacked_targets = [
        (target_id, result["stack_outputs"])
        for target_id, result in summary.results.items()
        if result.get("stack_outputs")
    ]

    print("\n==========================================")
    print("BATCH PROCESSING RUN COMPLETE")
    print(f"Successful targets: {len(summary.succeeded)}")
    print(f"Failed targets: {len(summary.failed)}")
    if summary.failed:
        print("\nFailed Targets Summary:")
        for target_id, reason in summary.failed:
            print(f"  - {target_id}: {reason}")
    print(f"Stacked targets: {len(stacked_targets)}")
    if stacked_targets:
        print("\nStacked Targets Summary:")
        for target_id, outputs in stacked_targets:
            stack_descriptions = [f"{stack_type.capitalize()}={path}" for stack_type, path in outputs.items()]
            print(f"  - {target_id}: {', '.join(stack_descriptions)}")
    print("==========================================")


if __name__ == "__main__":
    run_batch_processing()
