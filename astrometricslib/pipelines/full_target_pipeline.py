"""Runs the complete pipeline for one target, start to finish.

Stacks a target's raw frames, plate-solves the result, tracks star
brightness (photometry), pulls out light spectra (spectroscopy) when
there are any, and saves the target's record -- in that order, for one
target at a time.

The only caller is `api/batch.py`, which runs many targets through
this same sequence in parallel worker processes -- this module doesn't
know or care about that; it just runs one target's full sequence.
"""

import os
from typing import Any

from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.dispatch import analyze_target, stack_frames_with_timeout
from astrometricslib.pipelines.shared.frame_grouping import (
    frame_configuration_key,
    frames_missing_focal_length,
    select_frames_for_camera,
)
from astrometricslib.utilities.enums import FilterType
from datastore.process_locks import acquire_resource_slot


def run_full_pipeline(
    target: Target,
    astrometrics: Any,
    max_workers: int | None = None,
    *,
    camera_name: str,
    focal_length_mm: float | None = None,
) -> dict[str, str]:
    """Run the complete start-to-finish processing pipeline for a target.

    This runs stacking, position solving (astrometry), brightness
    tracking (photometry), and light spectrum (spectroscopy) in order,
    then saves the target's data to the database.

    Parameters
    ----------
    target : Target
        The target we want to process.
    astrometrics : Any
        The system interface that gives us config settings and database access.
    max_workers : int, optional
        How many parallel processes to use during the brightness tracking step.
    camera_name : str
        The name of the camera to process images for. Any images taken
        by a different camera will be ignored.

    Returns
    -------
    stack_outputs : dict
        A dictionary mapping the stack type ("standard" or "spectral")
        to the final saved image file path.
    """
    print("\n==========================================")
    print(f"STARTING BATCH PROCESSING FOR TARGET: {target.id}")
    print("==========================================")

    camera_frames = _select_frames_for_processing(target, camera_name, focal_length_mm)
    if camera_frames is None:
        return {}

    standard_frames, spectral_frames = _split_standard_and_spectral_frames(target, camera_frames)
    stack_outputs = _stack_camera_frames(target, camera_name, standard_frames, spectral_frames)

    # 2. Astrometry Analysis
    _run_astrometry_stage(target, astrometrics)

    # 3. Photometry Analysis
    max_concurrent_jobs = astrometrics.config.get_max_concurrent_jobs()
    _run_photometry_stage(target, astrometrics, camera_frames, max_workers, max_concurrent_jobs)

    # 4. Spectroscopy Analysis (only when this target actually has a
    # SPEC stack)
    _run_spectroscopy_stage(target, astrometrics, spectral_frames, max_concurrent_jobs)

    # Save this target's own record (safe under concurrent callers,
    # unlike a full-catalog resync)
    astrometrics.catalog_access.put(target, "target_record", {})
    print(f"[{target.id}] Processing completed and metadata saved successfully.")

    return stack_outputs


def _select_frames_for_processing(
    target: Target, camera_name: str, focal_length_mm: float | None
) -> list[FrameRecord] | None:
    """Narrow a target's frames down to one camera and (optionally) one optic.

    Frames of different focal length image at different scales -- this
    library's 300mm and 405mm optics differ by 1.35x -- so a stack
    blending them has no single pixel scale, cannot be plate solved
    accurately, and produces fluxes that are not comparable between
    frames. Seven targets were being stacked that way, NGC 7023 worst
    at 424 frames of one optic mixed with 111 of the other.

    Returns
    -------
    camera_frames : `list` [`FrameRecord`] or `None`
        The matching frames, or `None` if there are none to process
        (already logged, so the caller should stop with no work done).
    """
    # Restrict all processing to frames captured with the requested
    # camera; every other camera's frames on this target are excluded.
    camera_frames = select_frames_for_camera(target, camera_name)

    if focal_length_mm is not None:
        requested_key_suffix = f"@{round(float(focal_length_mm))}mm"
        selected_frames = [
            frame
            for frame in camera_frames
            if (frame_configuration_key(frame) or "").endswith(requested_key_suffix)
        ]
        if not selected_frames:
            print(
                f"[{target.id}] No frames at {focal_length_mm:g}mm for camera '{camera_name}'. "
                "Skipping all processing steps."
            )
            return None
        unassignable = frames_missing_focal_length(target, camera_name)
        if unassignable:
            # Never dropped silently: a frame with no FOCALLEN cannot be
            # grouped, and on this library that is 602 frames. See
            # scripts/backfill_focal_length.
            print(
                f"[{target.id}] {len(unassignable)} frame(s) excluded: no FOCALLEN recorded, "
                "so their optic is unknown."
            )
        camera_frames = selected_frames

    if not camera_frames:
        print(
            f"[{target.id}] No frames matching camera '{camera_name}' found for this target. "
            "Skipping all processing steps."
        )
        return None

    return camera_frames


def _split_standard_and_spectral_frames(
    target: Target, camera_frames: list[FrameRecord]
) -> tuple[list[FrameRecord], list[FrameRecord]]:
    """Split a target's camera frames into standard and spectral groups.

    Excludes derived frames (already-stacked images, starless/starmask
    products) before classifying what's left by filter.

    Returns
    -------
    standard_frames, spectral_frames : `list` [`FrameRecord`]
        The non-SPEC and SPEC frames, respectively.
    """
    print(f"[{target.id}] Stacking frames...")
    target_frames = [
        frame
        for frame in camera_frames
        if not any(k in frame.path.lower() for k in ("_stacked", "starless", "starmask"))
    ]

    # Check if there is a mixed set of spectral and standard frames.
    # If so, run standard stacking on standard frames, and spectral
    # stacking on spectral frames.
    standard_frames = []
    spectral_frames = []
    for frame in target_frames:
        is_spectral = (
            frame.filter == FilterType.SPEC
            or getattr(frame.filter, "name", None) == "SPEC"
            or str(frame.filter).upper() in ("SPEC", "STAR ANALYZER 200")
        )
        if is_spectral:
            spectral_frames.append(frame)
        else:
            standard_frames.append(frame)

    return standard_frames, spectral_frames


def _stack_camera_frames(
    target: Target,
    camera_name: str,
    standard_frames: list[FrameRecord],
    spectral_frames: list[FrameRecord],
) -> dict[str, str]:
    """Stack a target's standard and/or spectral frames.

    Returns
    -------
    stack_outputs : `dict`
        Maps the stack type ("standard" or "spectral") to the final
        saved image file path, for whichever kinds of frames existed.

    Raises
    ------
    ValueError
        If a kind of frame that needed stacking failed to produce a
        valid output file.
    """
    stack_outputs: dict[str, str] = {}

    # We do not lock the Siril process here because the `siril_interface`
    # already does it during the actual Siril launch. If we lock it here too,
    # two workers could grab the outer locks and then wait forever for each
    # other to release the inner locks, causing a deadlock.
    #
    # The driver is the right place for the lock because it protects
    # every Siril launch, not just the ones started by this batch script.
    if standard_frames and spectral_frames:
        print(
            f"[{target.id}] Target contains mixed frames. Stacking standard and spectral frames separately."
        )
        stacked_output = stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking failed on mixed target.")
        stacked_spectral = stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking failed on mixed target.")
        print(f"[{target.id}] Stacking succeeded: Standard={stacked_output}, Spectral={stacked_spectral}")
        stack_outputs["standard"] = stacked_output
        stack_outputs["spectral"] = stacked_spectral
    elif standard_frames:
        stacked_output = stack_frames_with_timeout(target, standard_frames)
        if not stacked_output or not os.path.exists(stacked_output):
            raise ValueError("Standard stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Standard stacking succeeded: {stacked_output}")
        stack_outputs["standard"] = stacked_output
    elif spectral_frames:
        stacked_spectral = stack_frames_with_timeout(target, spectral_frames)
        if not stacked_spectral or not os.path.exists(stacked_spectral):
            raise ValueError("Spectral stacking pipeline returned no valid output path.")
        print(f"[{target.id}] Spectral stacking succeeded: {stacked_spectral}")
        stack_outputs["spectral"] = stacked_spectral
    else:
        print(
            f"[{target.id}] No valid frames matching camera '{camera_name}' found for stacking. "
            "Skipping stacking step."
        )

    return stack_outputs


def _run_astrometry_stage(target: Target, astrometrics: Any) -> dict[str, Any]:
    """Run astrometry analysis on the target's stacked image.

    Returns
    -------
    astrometry_results : `dict`
        The astrometry pipeline's result dict.

    Raises
    ------
    ValueError
        If astrometry analysis failed to produce a result.
    """
    print(f"[{target.id}] Running Astrometry Analysis...")
    astrometry_results = analyze_target(
        target, pipeline_type="astrometry", catalog_access=astrometrics.catalog_access
    )
    if astrometry_results is None:
        raise ValueError("Astrometry analysis failed.")
    print(
        f"[{target.id}] Astrometry Analysis complete. "
        f"Resolved WCS: {astrometry_results.get('wcs') is not None}"
    )
    return astrometry_results


def _run_photometry_stage(
    target: Target,
    astrometrics: Any,
    camera_frames: list[FrameRecord],
    max_workers: int | None,
    max_concurrent_jobs: int,
) -> dict[str, Any]:
    """Run photometry/variability analysis on the target's camera frames.

    Returns
    -------
    photometry_results : `dict`
        The photometry pipeline's result dict.
    """
    print(f"[{target.id}] Running Photometry/Variability Analysis...")
    with acquire_resource_slot(astrometrics.config, "job", max_concurrent_jobs):
        photometry_results = analyze_target(
            target,
            pipeline_type="photometry",
            frames=camera_frames,
            catalog_access=astrometrics.catalog_access,
            max_workers=max_workers,
        )
    print(
        f"[{target.id}] Photometry Analysis complete. Stars found: {photometry_results.get('starsFound', 0)}"
    )
    return photometry_results


def _run_spectroscopy_stage(
    target: Target,
    astrometrics: Any,
    spectral_frames: list[FrameRecord],
    max_concurrent_jobs: int,
) -> None:
    """Run spectroscopy analysis when the target has SPEC frames.

    Raises
    ------
    ValueError
        If spectroscopy analysis failed to produce a result.
    """
    if not spectral_frames:
        print(f"[{target.id}] No SPEC frames found for this target. Skipping spectroscopy analysis.")
        return

    print(f"[{target.id}] Running Spectroscopy Analysis...")
    with acquire_resource_slot(astrometrics.config, "job", max_concurrent_jobs):
        spectroscopy_results = analyze_target(
            target, pipeline_type="spectroscopy", limit=10, catalog_access=astrometrics.catalog_access
        )
    if spectroscopy_results is None:
        raise ValueError("Spectroscopy analysis failed.")
    print(f"[{target.id}] Spectroscopy Analysis complete.")
