"""Batch processing script that cycles through all targets in the catalog.

Performs reindexing on all targets to ensure file pointers are up to
date.
"""

import logging
import sys

from astrometricslib import Astrometrics


def run_batch_processing() -> None:
    """Reindex every target in the catalog, tolerating per-target errors.

    Initializes `Astrometrics`, retrieves all targets, and calls
    `Target.reindex_frames` on each in turn, saving progress after
    every successful reindex.

    Notes
    -----
    Exceptions raised while processing an individual target are caught
    and recorded rather than propagated, so that a single failing
    target does not halt the batch run.
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

    success_count = 0
    failed_targets = []

    for target in targets:
        try:
            print("\n==========================================")
            print(f"REINDEXING TARGET: {target.id}")
            print("==========================================")
            astrometrics.targets.reindex_frames(target, prune_missing=True)
            success_count += 1
            astrometrics.targets.save()
        except Exception as err:
            print(f"\n[ERROR] Failed to process target '{target.id}': {err}")
            failed_targets.append((target.id, str(err)))
            # Continue to next target

    print("\n==========================================")
    print("BATCH PROCESSING RUN COMPLETE")
    print(f"Successful targets: {success_count}")
    print(f"Failed targets: {len(failed_targets)}")
    if failed_targets:
        print("\nFailed Targets Summary:")
        for name, reason in failed_targets:
            print(f"  - {name}: {reason}")
    print("==========================================")


if __name__ == "__main__":
    run_batch_processing()
