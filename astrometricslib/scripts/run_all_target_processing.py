"""Batch processing script that runs the full pipeline on every target.

Runs stacking, astrometry, photometry, and spectroscopy (where
applicable) in two sequential passes: first every target's ZWO
ASI533MM Pro (monochrome) frames, then every *remaining* target's
Nikon DSLR DSC D5300 (color) frames -- see `run_full_processing`'s
docstring for why the second pass excludes targets the first pass
already covered.

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
from astrometricslib.tasks.target_tasks.pipeline_tasks import select_frames_for_camera
from astrometricslib.utilities.parallel_batch import BatchRunSummary

ASI_CAMERA_NAME = "ZWO ASI 533MM Pro"
NIKON_CAMERA_NAME = "Nikon DSLR DSC D5300"


def _print_pass_summary(camera_name: str, summary: BatchRunSummary) -> None:
    """Print one camera pass's outcome in the same format as the others.

    Parameters
    ----------
    camera_name : `str`
        The camera this pass processed, for the header line.
    summary : `BatchRunSummary`
        The pass's aggregated success/skip/failure state.
    """
    stacked_targets = [
        (target_id, result["stack_outputs"])
        for target_id, result in summary.results.items()
        if result.get("stack_outputs")
    ]

    print("\n==========================================")
    print(f"{camera_name} PASS COMPLETE")
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


def run_full_processing() -> None:
    """Run the full processing pipeline for every target in the catalog.

    Runs the ASI533MM (monochrome) pass first, across every target
    with frames for that camera. The Nikon DSLR (color) pass then runs
    second, but only across targets the ASI533MM pass did *not* touch
    -- `Target.stacked_image`, `processed_image`, and every quality-
    summary field are single-valued, not per-camera, so running a
    second camera's pass on a target the first pass already processed
    would silently overwrite that target's ASI533MM stack reference
    and quality summaries with the DSLR-camera results (the ASI533MM
    FITS files themselves stay on disk; the target record would simply
    stop pointing at them). 9 of this catalog's targets have frames
    from both cameras as of 2026-08-23; excluding them from the second
    pass keeps their existing ASI533MM results intact.
    """
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

    # Reindex before processing so the run works from a current view of
    # what is actually on disk, and so every frame carries the
    # header-derived acquisition conditions the analyses expect.
    #
    # refresh_headers is the load-bearing part: scanning alone only
    # builds records for files it has not seen before, so a field added
    # to FrameRecord after a frame was first indexed stays None on that
    # frame forever. Reading headers costs ~10ms per frame -- under a
    # minute for this whole catalog -- because nothing here touches
    # pixel data.
    #
    # prune_missing is deliberately NOT set: it deletes frame records
    # whose files are not currently readable, and this library lives on
    # an external drive. A drive that is slow to mount would silently
    # erase real frame history rather than fail loudly.
    print("Reindexing frames and refreshing acquisition metadata...")
    reindexed_target_count = 0
    for target in targets:
        try:
            astrometrics.targets.reindex_frames(target, refresh_headers=True)
            reindexed_target_count += 1
        except Exception as reindex_error:
            print(f"  [{target.id}] Reindex failed, continuing with stored frames: {reindex_error}")
    astrometrics.targets.save()
    print(f"Reindexed {reindexed_target_count} of {len(targets)} target(s).")

    print("Running the full pipeline (stacking, astrometry, photometry, spectroscopy) for each target...")

    # `camera_name` is a required, keyword-only argument on
    # `process_all_targets` (no default). The full camera name is used
    # rather than a partial match like "533mm", since `run_full_pipeline`
    # matches it as a case-insensitive substring against each frame's
    # camera -- a partial string risks matching more than one camera if
    # the catalog ever grows a similarly-named one.
    print(f"\n--- Pass 1: {ASI_CAMERA_NAME} ---")
    asi_summary = astrometrics.process_all_targets(camera_name=ASI_CAMERA_NAME)
    _print_pass_summary(ASI_CAMERA_NAME, asi_summary)

    nikon_only_target_ids = [
        target.id
        for target in targets
        if select_frames_for_camera(target, NIKON_CAMERA_NAME)
        and not select_frames_for_camera(target, ASI_CAMERA_NAME)
    ]
    print(f"\n--- Pass 2: {NIKON_CAMERA_NAME} (targets without ASI533MM data only) ---")
    if not nikon_only_target_ids:
        print("No Nikon-only targets found; skipping this pass.")
        return
    nikon_summary = astrometrics.process_all_targets(
        target_ids=nikon_only_target_ids, camera_name=NIKON_CAMERA_NAME
    )
    _print_pass_summary(NIKON_CAMERA_NAME, nikon_summary)


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
