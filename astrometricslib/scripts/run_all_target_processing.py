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

import argparse
import logging
import os
import sys
from typing import Any

# Disable opening Siril GUI when stacking completes -- must be set
# before astrometricslib is imported, since a batch run across every
# target should never pop open a GUI window mid-run.
os.environ["HEADLESS"] = "1"

from astrometricslib import Astrometrics
from astrometricslib.pipelines.shared.frame_grouping import select_frames_for_camera
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


def _resolve_focal_lengths(astrometrics: Astrometrics, targets: list, arguments: Any) -> list:
    """Decide which optic(s) this run processes.

    An explicit --optic wins. --all-optics runs every focal length
    present in the selection, primary first, so a target imaged through
    two optics ends up with a stack for each. Otherwise the observatory's
    primary optic is used, which is what makes a plain run deterministic
    rather than dependent on whichever frames happen to be present.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        Interface providing the configuration.
    targets : `list`
        The targets this run will process.
    arguments : `Any`
        Parsed command-line arguments.

    Returns
    -------
    focal_lengths : `list`
        Focal lengths in millimetres, in the order passes should run. A
        single `None` means "every focal length", used only when no
        primary is configured and none could be discovered.
    """
    if arguments.focal_length_mm:
        return [arguments.focal_length_mm]

    primary = astrometrics.config.get_primary_focal_length_mm()
    if not arguments.all_optics:
        if primary:
            print(f"Using the primary optic: {primary:g}mm (from [Observatory.Telescope]).")
            return [primary]
        print(
            "WARNING: no primary optic configured ([Observatory.Telescope] focal_length_mm); "
            "frames of differing focal length may be stacked together."
        )
        return [None]

    present = sorted({
        round(float(frame.focal_length_mm))
        for target in targets
        for frame in (target.frames or [])
        if getattr(frame, "focal_length_mm", None)
    })
    if not present:
        print("No frame records a focal length yet; run a reindex, or backfill_focal_length.")
        return [None]
    # Primary first so its stack is written before any other optic's,
    # making it the one a reader sees if a later pass is interrupted.
    ordered = [float(value) for value in present]
    if primary and round(primary) in present:
        ordered.remove(float(round(primary)))
        ordered.insert(0, float(round(primary)))
    print(f"Running one pass per optic: {', '.join(f'{value:g}mm' for value in ordered)}")
    return ordered


def _reindex_targets(astrometrics: Astrometrics, targets: list) -> None:
    """Reindex the given targets and refresh their header metadata.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The high-level interface owning the catalog.
    targets : `list`
        Targets to reindex.
    """
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


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering target selection and dry-run preview.
    """
    parser = argparse.ArgumentParser(
        prog="run_all_target_processing",
        description="Run the full pipeline over the target catalog, or a chosen subset.",
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help=(
            "Process only this target; repeatable. Matched case-insensitively against "
            "the catalog id. Defaults to every target."
        ),
    )
    parser.add_argument(
        "--targets-from",
        metavar="PATH",
        help="Read target ids from a file, one per line; blank lines and # comments ignored.",
    )
    parser.add_argument(
        "--skip-target",
        action="append",
        dest="skip_target_ids",
        metavar="TARGET_ID",
        help=(
            "Never process this target; repeatable, matched like --target. Intended for targets "
            "star-based registration cannot handle at all -- a solar or planetary disk carries no "
            "star field, so Siril reports 'not enough stars in reference image' after staging every "
            "frame. Sun, Venus and Jupiter fail this way on every run."
        ),
    )
    parser.add_argument(
        "--skip-targets-from",
        metavar="PATH",
        help="Read target ids to skip from a file, one per line; blank lines and # comments ignored.",
    )
    parser.add_argument(
        "--optic",
        type=float,
        dest="focal_length_mm",
        metavar="MM",
        help=(
            "Process only frames taken at this focal length, in millimetres. Defaults to the "
            "primary optic from [Observatory.Telescope] focal_length_mm, so a target imaged "
            "through two optics stacks the observatory's own rather than blending scales."
        ),
    )
    parser.add_argument(
        "--all-optics",
        action="store_true",
        help=(
            "Run a separate pass per focal length instead of only the primary one. Each optic "
            "gets its own stack; the primary remains the target's stacked_image."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print which targets each camera pass would process, then exit without processing.",
    )
    parser.add_argument(
        "--skip-reindex",
        action="store_true",
        help=(
            "Skip the pre-run reindex. Only safe when nothing has changed on disk since "
            "the last run; the reindex costs about 10 seconds for a 4,000-frame catalog."
        ),
    )
    return parser


def _resolve_requested_targets(targets: list, requested_ids: list[str]) -> tuple[list, list[str]]:
    """Narrow the catalog to the requested ids.

    Matching is case-insensitive and ignores surrounding whitespace,
    since ids are typed by hand and carry spaces ("NGC 7023").

    Parameters
    ----------
    targets : `list`
        Every target in the catalog.
    requested_ids : `list` [`str`]
        Ids the caller asked for.

    Returns
    -------
    selected : `list`
        The matching targets, in catalog order.
    unmatched : `list` [`str`]
        Requested ids that matched nothing, so a typo is reported rather
        than silently processing fewer targets than intended.
    """
    wanted = {target_id.strip().casefold() for target_id in requested_ids if target_id.strip()}
    selected = [target for target in targets if target.id.strip().casefold() in wanted]
    matched = {target.id.strip().casefold() for target in selected}
    unmatched = sorted(
        target_id
        for target_id in requested_ids
        if target_id.strip() and target_id.strip().casefold() not in matched
    )
    return selected, unmatched


def _read_target_ids_from_file(path: str) -> list[str]:
    """Read one target id per line, ignoring blanks and comments.

    Parameters
    ----------
    path : `str`
        File to read.

    Returns
    -------
    target_ids : `list` [`str`]
        The ids found, in file order.
    """
    with open(path) as handle:
        return [line.strip() for line in handle if line.strip() and not line.lstrip().startswith("#")]


def run_full_processing(argv: list[str] | None = None) -> None:
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
    stop pointing at them). Some targets have frames from both cameras;
    excluding them from the second pass keeps their first-pass results intact.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    print("Initializing Astrometrics...")
    astrometrics = Astrometrics()

    targets = astrometrics.targets.list()
    if not targets:
        print("No targets found in the database catalog.")
        return

    requested_ids = list(arguments.target_ids or [])
    if arguments.targets_from:
        requested_ids.extend(_read_target_ids_from_file(arguments.targets_from))

    skip_ids = list(arguments.skip_target_ids or [])
    if arguments.skip_targets_from:
        skip_ids.extend(_read_target_ids_from_file(arguments.skip_targets_from))
    if skip_ids:
        skipped, unmatched_skips = _resolve_requested_targets(targets, skip_ids)
        skipped_names = {target.id for target in skipped}
        if skipped_names:
            targets = [target for target in targets if target.id not in skipped_names]
            print(
                f"Skipping {len(skipped_names)} target(s) at the caller's request: "
                f"{', '.join(sorted(skipped_names))}"
            )
        if unmatched_skips:
            print(f"WARNING: nothing to skip for {len(unmatched_skips)} id(s): {', '.join(unmatched_skips)}")

    if requested_ids:
        targets, unmatched = _resolve_requested_targets(targets, requested_ids)
        if unmatched:
            # Reported rather than ignored: a mistyped id would otherwise
            # quietly shrink the run and look like a clean pass.
            print(f"WARNING: no target matches {len(unmatched)} requested id(s): {', '.join(unmatched)}")
        if not targets:
            print("None of the requested targets exist in the catalog; nothing to do.")
            return
        print(f"Processing {len(targets)} requested target(s) of the catalog.")
    else:
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
    # Reclaim scratch space left by earlier failed or interrupted runs before
    # this one starts staging its own files. A successful stack removes its own
    # work directory, so anything old enough to be swept here belongs to
    # a run that crashed or was cancelled.
    from astrometricslib.drivers.siril_interface import purge_stale_work_directories

    siril_work_directory = os.path.join(os.path.expanduser("~"), "Siril", "Work")
    purged_count, reclaimed_bytes = purge_stale_work_directories(siril_work_directory)
    if purged_count:
        print(
            f"Reclaimed {reclaimed_bytes / 1_000_000_000:.1f}GB from {purged_count} stale "
            f"work director{'y' if purged_count == 1 else 'ies'}."
        )

    if arguments.dry_run:
        # Resolved here too, so a preview shows which optic a real run
        # would use -- the whole point of previewing before a long job.
        for focal_length_mm in _resolve_focal_lengths(astrometrics, targets, arguments):
            if focal_length_mm:
                matching = sum(
                    1
                    for t in targets
                    for f in (t.frames or [])
                    if getattr(f, "focal_length_mm", None)
                    and round(float(f.focal_length_mm)) == round(focal_length_mm)
                )
                print(f"  optic {focal_length_mm:g}mm -> {matching} frame(s) in this selection")
        asi_ids = [t.id for t in targets if select_frames_for_camera(t, ASI_CAMERA_NAME)]
        nikon_ids = [
            t.id
            for t in targets
            if select_frames_for_camera(t, NIKON_CAMERA_NAME)
            and not select_frames_for_camera(t, ASI_CAMERA_NAME)
        ]
        neither = [t.id for t in targets if t.id not in asi_ids and t.id not in nikon_ids]
        print(f"\nPass 1 ({ASI_CAMERA_NAME}) would process {len(asi_ids)}: {', '.join(asi_ids) or '-'}")
        print(f"Pass 2 ({NIKON_CAMERA_NAME}) would process {len(nikon_ids)}: {', '.join(nikon_ids) or '-'}")
        if neither:
            print(f"No frames for either camera ({len(neither)}): {', '.join(neither)}")
        print("\nDry run: nothing was processed.")
        return

    if arguments.skip_reindex:
        print("Skipping reindex at the caller's request.")
    else:
        _reindex_targets(astrometrics, targets)

    print("Running the full pipeline (stacking, astrometry, photometry, spectroscopy) for each target...")

    # `camera_name` is a required, keyword-only argument on
    # `process_all_targets` (no default). The full camera name is used
    # rather than a partial match like "533mm", since `run_full_pipeline`
    # matches it as a case-insensitive substring against each frame's
    # camera -- a partial string risks matching more than one camera if
    # the catalog ever grows a similarly-named one.
    # Ids are passed explicitly for both passes. Omitting them makes
    # `process_all_targets` walk the entire catalog, which would silently
    # ignore a --target selection and reprocess everything.
    asi_target_ids = [target.id for target in targets if select_frames_for_camera(target, ASI_CAMERA_NAME)]
    nikon_only_target_ids = [
        target.id
        for target in targets
        if select_frames_for_camera(target, NIKON_CAMERA_NAME)
        and not select_frames_for_camera(target, ASI_CAMERA_NAME)
    ]

    # Which optic(s) to run. Defaults to the observatory's primary --
    # frames of different focal length image at different scales and must
    # never share a stack, so processing every optic at once is not an
    # option; the choice is which one a plain run produces.
    focal_lengths = _resolve_focal_lengths(astrometrics, targets, arguments)

    for focal_length_mm in focal_lengths:
        optic_label = f" @ {focal_length_mm:g}mm" if focal_length_mm else ""
        print(f"\n--- Pass 1: {ASI_CAMERA_NAME}{optic_label} ---")
        if asi_target_ids:
            asi_summary = astrometrics.process_all_targets(
                target_ids=asi_target_ids,
                camera_name=ASI_CAMERA_NAME,
                focal_length_mm=focal_length_mm,
            )
            _print_pass_summary(f"{ASI_CAMERA_NAME}{optic_label}", asi_summary)
        else:
            print("No targets with ASI533MM frames in this selection; skipping this pass.")

        print(f"\n--- Pass 2: {NIKON_CAMERA_NAME}{optic_label} (targets without ASI533MM only) ---")
        if not nikon_only_target_ids:
            print("No Nikon-only targets found; skipping this pass.")
            continue
        nikon_summary = astrometrics.process_all_targets(
            target_ids=nikon_only_target_ids,
            camera_name=NIKON_CAMERA_NAME,
            focal_length_mm=focal_length_mm,
        )
        _print_pass_summary(f"{NIKON_CAMERA_NAME}{optic_label}", nikon_summary)


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
