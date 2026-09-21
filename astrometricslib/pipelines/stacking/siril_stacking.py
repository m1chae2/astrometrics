"""Running Siril for a stack, with the two safeguards spectroscopy needs.

`run_siril_stack` is what the stacking stage calls instead of talking to the
Siril driver directly. It adds two things the driver alone does not do:

1. Registration fallback. Spectral frames are lined up by matching stars
   against a reference frame. If the standard star detection cannot match
   enough frames, the stack is tried again with a more relaxed detection,
   and the better of the two is kept. Losing frames used to happen silently;
   now it is retried and, if it still happens, logged as a warning.
2. Exposure groups. A spectral session shot at several exposure lengths is
   stacked one exposure length at a time and the results are combined (see
   `exposure_groups` for why the usual all-at-once recipe wastes the light).
"""

import logging
import os
import shutil
from typing import Any

logger = logging.getLogger(__name__)

# The share of frames that must register for a spectral stack to be accepted
# as it is. Below this, the stack is retried with relaxed star detection and
# the run that registered more frames is kept; if both fall below it, a
# warning is logged. Unvalidated: chosen so that losing one frame in ten is
# tolerated (a stray cloudy frame) while losing a third is not. On the Vega
# session the standard detection registered 140 of 140 frames (100%) and the
# relaxed one 94 of 140 (67%). It has not been tuned on other sessions.
MINIMUM_REGISTERED_FRACTION = 0.9

# The star-detection settings to try for spectral frames, in order (see
# `siril_interface.SPECTRAL_STAR_DETECTION_COMMANDS`).
SPECTRAL_STAR_DETECTION_ORDER = ("standard", "relaxed")


def run_siril_stack(
    siril_driver: Any,
    frames: list[Any],
    target_id: str,
    output_file: str | None,
    log_file: str | None,
    is_spectral: bool,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Stack frames with Siril, one exposure length at a time if they differ.

    Parameters
    ----------
    siril_driver : `ImageProcessing`
        The Siril driver.
    frames : `list`
        The frame records to stack (or dictionaries of them).
    target_id : `str`
        The target's name, used for Siril's working folder.
    output_file : `str` or `None`
        The file name of the stacked image, which is written to the target's
        library folder. `None` uses the driver's own name for the target.
    log_file : `str` or `None`
        Where Siril's output is logged.
    is_spectral : `bool`
        Whether these are spectroscopy frames. Registration fallback and
        exposure groups only apply to them.
    **stack_options
        The other options `ImageProcessing.process_target` takes
        (``rejection_sigma``, ``filter_wfwhm``, ``filter_round``,
        ``stack_weight``, ``generate_rejmap``).

    Returns
    -------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if stacking failed.
    diagnostics : `dict`
        What the driver reported about the run. For a stack made from several
        exposure groups, the per-frame lists are joined in group order and an
        ``"exposure_groups"`` entry describes each group.
    """
    if output_file is None:
        output_file = f"{target_id.replace(' ', '_')}_Stacked.fits"
    if is_spectral:
        from astrometricslib.pipelines.stacking.exposure_groups import split_frames_by_exposure

        groups = split_frames_by_exposure(frames)
        if len(groups) > 1:
            return _stack_exposure_groups(
                siril_driver, groups, target_id, output_file, log_file, **stack_options
            )
    return _stack_one_batch(
        siril_driver, frames, target_id, output_file, log_file, is_spectral, **stack_options
    )


def _stack_one_batch(
    siril_driver: Any,
    frames: list[Any],
    target_id: str,
    output_file: str,
    log_file: str | None,
    is_spectral: bool,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Run Siril once for frames that can be stacked together.

    For spectral frames the standard star detection is tried first. If too
    few frames register, the relaxed detection is tried as well and the run
    that registered more frames is kept.

    Returns
    -------
    stacked_path : `str` or `None`
        Where the stacked image was written, or `None` if every attempt failed.
    diagnostics : `dict`
        The driver's diagnostics for the kept run, with
        ``"spectral_star_detection"`` and ``"registered_fraction"`` added for
        spectral frames.
    """
    image_files = [frame.model_dump() if hasattr(frame, "model_dump") else frame for frame in frames]
    modes = SPECTRAL_STAR_DETECTION_ORDER if is_spectral else (None,)
    attempts = []
    for attempt_index, mode in enumerate(modes):
        attempt_output = output_file if attempt_index == 0 else _with_suffix(output_file, f"_{mode}")
        attempt_log = log_file if attempt_index == 0 else _with_suffix(log_file, f"_{mode}")
        options = dict(stack_options)
        if mode is not None:
            options["spectral_star_detection"] = mode
        path = siril_driver.process_target(
            id=target_id,
            image_files=image_files,
            output_file=attempt_output,
            log_file=attempt_log,
            is_spectral=is_spectral,
            **options,
        )
        diagnostics = dict(siril_driver.last_run_diagnostics)
        fraction = _registered_fraction(diagnostics)
        if mode is not None:
            diagnostics["spectral_star_detection"] = mode
            diagnostics["registered_fraction"] = fraction
        attempts.append((path, diagnostics, fraction))
        if path and fraction >= MINIMUM_REGISTERED_FRACTION:
            break
        if mode is not None and attempt_index + 1 < len(modes):
            if path:
                logger.warning(
                    "Only %.0f%% of %d frames registered for '%s' with %s star detection; trying %s.",
                    100 * fraction,
                    len(frames),
                    target_id,
                    mode,
                    modes[attempt_index + 1],
                )
            else:
                # Siril gave up before it reported any registration totals.
                logger.warning(
                    "Stacking %d frames for '%s' failed with %s star detection; trying %s.",
                    len(frames),
                    target_id,
                    mode,
                    modes[attempt_index + 1],
                )

    usable = [attempt for attempt in attempts if attempt[0]]
    if not usable:
        return None, attempts[-1][1]
    kept = max(usable, key=lambda attempt: (attempt[2], _registered_count(attempt[1])))
    if len(attempts) > 1:
        _keep_only(kept[0], [attempt[0] for attempt in attempts if attempt[0]], output_file)
        kept_path = os.path.join(os.path.dirname(kept[0]), output_file)
    else:
        kept_path = kept[0]
    if is_spectral and kept[2] < MINIMUM_REGISTERED_FRACTION:
        logger.warning(
            "Only %.0f%% of the %d frames for '%s' could be registered (%s star detection); "
            "the stack is built from the rest.",
            100 * kept[2],
            len(frames),
            target_id,
            kept[1].get("spectral_star_detection"),
        )
    return kept_path, kept[1]


def _registered_fraction(diagnostics: dict[str, Any]) -> float:
    """Work out what share of frames Siril registered.

    Returns
    -------
    fraction : `float`
        The share from 0 to 1, or 1.0 when Siril did not report it (so an
        unreported run is not retried).
    """
    failed = diagnostics.get("registration_failed_frames")
    registered = diagnostics.get("registered_frames")
    if failed is None or registered is None or failed + registered == 0:
        return 1.0
    return registered / (failed + registered)


def _registered_count(diagnostics: dict[str, Any]) -> int:
    """Read how many frames Siril registered.

    Returns
    -------
    count : `int`
        The number registered, or 0 when Siril did not say.
    """
    return int(diagnostics.get("registered_frames") or 0)


def _with_suffix(name: str | None, suffix: str) -> str | None:
    """Add a suffix before a file name's extension.

    Returns
    -------
    new_name : `str` or `None`
        The changed name, or `None` when `name` is `None`.
    """
    if name is None:
        return None
    stem, extension = os.path.splitext(name)
    return f"{stem}{suffix}{extension}"


def _sibling_paths(stack_path: str) -> list[str]:
    """List the files the driver writes next to a stacked image.

    Returns
    -------
    paths : `list` [`str`]
        The stack itself, its rejection map and its registration sequence.
    """
    stem = os.path.splitext(stack_path)[0]
    return [stack_path, f"{stem}_RejMap.fits", f"{stem}_Registration.seq"]


def _keep_only(kept_path: str, all_paths: list[str], output_file: str) -> None:
    """Keep one attempt's files under the requested name and delete the others.

    Every attempt wrote its own stack, rejection map and registration
    sequence. The kept attempt's files are moved to the names asked for, and
    the rest are removed.

    Parameters
    ----------
    kept_path : `str`
        The stack of the attempt to keep.
    all_paths : `list` [`str`]
        The stacks of every attempt that produced one.
    output_file : `str`
        The file name the caller asked for.
    """
    directory = os.path.dirname(kept_path)
    final_path = os.path.join(directory, output_file)
    # The losers go first. One of them may hold the very names the kept
    # attempt is about to be moved to, and deleting afterwards would delete
    # the kept attempt's files.
    for path in all_paths:
        if path == kept_path:
            continue
        for leftover in _sibling_paths(path):
            if os.path.exists(leftover):
                os.remove(leftover)
    for source, destination in zip(_sibling_paths(kept_path), _sibling_paths(final_path), strict=True):
        if source != destination and os.path.exists(source):
            shutil.move(source, destination)


def _stack_exposure_groups(
    siril_driver: Any,
    groups: list[Any],
    target_id: str,
    output_file: str,
    log_file: str | None,
    **stack_options: Any,
) -> tuple[str | None, dict[str, Any]]:
    """Stack each exposure group on its own and combine the results.

    Returns
    -------
    stacked_path : `str` or `None`
        Where the combined image was written, or `None` if no group stacked.
    diagnostics : `dict`
        The groups' diagnostics joined in group order, with an
        ``"exposure_groups"`` entry per group.
    """
    import numpy as np

    from astrometricslib.drivers.fits_access import read_data, read_header, write_image
    from astrometricslib.pipelines.stacking.exposure_groups import (
        combine_exposure_group_images,
        group_frame_noises,
        merge_registration_sequences,
        merge_rejection_maps,
    )

    logger.info(
        "Stacking '%s' as %d exposure groups (%s s) and combining them.",
        target_id,
        len(groups),
        ", ".join(f"{group.exposure_seconds:g}" for group in groups),
    )
    results = []
    for group in groups:
        tag = f"exp{group.exposure_seconds:g}s".replace(".", "p")
        path, diagnostics = _stack_one_batch(
            siril_driver,
            group.frames,
            target_id,
            _with_suffix(output_file, f"_{tag}"),
            _with_suffix(log_file, f"_{tag}"),
            True,
            **stack_options,
        )
        if path is None:
            logger.warning(
                "The %g s exposure group (%d frames) of '%s' could not be stacked; leaving it out.",
                group.exposure_seconds,
                len(group.frames),
                target_id,
            )
            continue
        results.append((group, path, diagnostics))
    if not results:
        return None, {}

    images, exposures, counts = [], [], []
    for group, path, diagnostics in results:
        images.append(np.asarray(read_data(path), dtype=np.float32))
        exposures.append(group.exposure_seconds)
        counts.append(int(diagnostics.get("images_stacked") or len(group.frames)))
    kept_groups = [group for group, _, _ in results]
    try:
        frame_noises = group_frame_noises(kept_groups)
    except OSError as read_error:
        # The weights should never cost the stack: without frame noises every
        # group is treated as equally noisy per frame, which weights by
        # frames x exposure^2.
        logger.warning(
            "Could not read frames to measure their noise (%s); weighting by exposure only.", read_error
        )
        frame_noises = [1.0] * len(kept_groups)
    combined = combine_exposure_group_images(images, exposures, counts, frame_noises=frame_noises)

    directory = os.path.dirname(results[0][1])
    final_path = os.path.join(directory, output_file)
    largest = max(results, key=lambda result: len(result[0].frames))
    header = read_header(largest[1])
    header["EXPTIME"] = float(
        sum(count * exposure for count, exposure in zip(counts, exposures, strict=True))
    )
    header["STACKCNT"] = int(sum(counts))
    header["HISTORY"] = "Combined from exposure groups: " + ", ".join(
        f"{count} x {exposure:g} s" for count, exposure in zip(counts, exposures, strict=True)
    )
    write_image(final_path, combined, header)

    stem = os.path.splitext(final_path)[0]
    merge_rejection_maps(
        [_sibling_paths(path)[1] for _, path, _ in results],
        counts,
        f"{stem}_RejMap.fits",
    )
    merge_registration_sequences(
        [_sibling_paths(path)[2] for _, path, _ in results], f"{stem}_Registration.seq"
    )
    for _, path, _ in results:
        for leftover in _sibling_paths(path):
            if os.path.exists(leftover) and leftover != final_path:
                os.remove(leftover)

    return final_path, _merge_diagnostics(results)


def _merge_diagnostics(results: list[tuple[Any, str, dict[str, Any]]]) -> dict[str, Any]:
    """Join the diagnostics of several exposure-group runs.

    The per-frame lists are joined in group order, which is also the order of
    the merged registration sequence, so the two stay lined up.

    Returns
    -------
    merged : `dict`
        The first group's diagnostics with the per-frame lists joined, the
        durations added, and an ``"exposure_groups"`` entry per group.
    """
    merged = dict(results[0][2])
    for key in (
        "corrupt_frames_skipped",
        "calibration_mismatch_flags",
        "symlinked_light_paths",
        "zero_order_stars",
    ):
        merged[key] = [item for _, _, diagnostics in results for item in diagnostics.get(key, [])]
    durations = [diagnostics.get("stacking_duration_seconds") for _, _, diagnostics in results]
    if any(duration is not None for duration in durations):
        merged["stacking_duration_seconds"] = float(sum(duration or 0.0 for duration in durations))
    # Every count below describes a whole session, so add up all the groups
    # (the copy of the first group would report only its own frames).
    for key in ("images_stacked", "num_lights", "registered_frames", "registration_failed_frames"):
        if any(diagnostics.get(key) is not None for _, _, diagnostics in results):
            merged[key] = sum(int(diagnostics.get(key) or 0) for _, _, diagnostics in results)
    merged["registered_fraction"] = _registered_fraction(merged)
    merged["exposure_groups"] = [
        {
            "exposure_seconds": group.exposure_seconds,
            "frames": len(group.frames),
            "images_stacked": diagnostics.get("images_stacked"),
            "registered_fraction": diagnostics.get("registered_fraction"),
            "spectral_star_detection": diagnostics.get("spectral_star_detection"),
        }
        for group, _, diagnostics in results
    ]
    return merged
