"""Purpose: Measure how sharp a stack is compared with the frames that made it.

Description: A good stack is at least as sharp as the width its input frames
predict. This module measures the star width (FWHM, full width at half
maximum) of every frame in the stack, reusing the widths the frame quality
step already stored and measuring the rest in parallel when it is safe to do
so. It predicts the stack's width from them, with each frame weighted as the
stacking program weighted it, and measures the star width of the stacked
image. It sets the FWHM fields on the stack's quality summary.
"""

import logging
import multiprocessing
from collections.abc import Mapping, Sequence
from typing import Any

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["measure_fwhm_degradation", "stored_frame_widths"]


def _measure_one_frame_fwhm(path: str) -> float | None:
    """Measure one frame's star width, or give `None`.

    A module-level function so a worker process can run it. A frame that
    cannot be read or measured is logged and skipped, not raised.

    Parameters
    ----------
    path : `str`
        Path of the frame to measure.

    Returns
    -------
    fwhm : `float` or `None`
        The median star width in pixels, or `None` if there is no width.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm

    try:
        return measure_image_fwhm(path)
    except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as exc:
        logger.debug("Skipping FWHM measurement for '%s': %s", path, exc)
        return None


def _fwhm_worker_count(frame_count: int) -> int:
    """Choose how many processes measure the frames at once.

    Uses the configured inner worker count (``photometry_workers``, the
    same setting the other per-target parallel steps use), kept within the
    available memory by `resolve_worker_counts`. One measurement of a 24
    megapixel frame was measured to use about 1 GB; the helper's default
    budget of 1.5 GB per worker covers that with margin. The count never
    exceeds the number of frames. A process that is itself a daemon (such as a
    `multiprocessing.Pool` worker) may not have child processes, so it
    measures serially.

    Parameters
    ----------
    frame_count : `int`
        Number of frames to measure.

    Returns
    -------
    worker_count : `int`
        Number of worker processes; 1 means measure in this process.
    """
    import multiprocessing

    if frame_count < 2 or multiprocessing.current_process().daemon:
        return 1
    try:
        from astrometricslib.foundation.config import get_configuration
        from astrometricslib.utilities.concurrency import resolve_worker_counts

        counts = resolve_worker_counts("1", get_configuration().get_photometry_workers())
    except (AstrometricsError, OSError, ValueError) as configuration_error:
        logger.debug("Measuring FWHM serially; worker count unavailable: %s", configuration_error)
        return 1
    return max(1, min(counts.inner_worker_count, frame_count))


def _measure_frame_fwhms(paths: list[str], worker_count: int) -> list[float | None]:
    """Measure the star width of each frame, keeping the order of `paths`.

    Parameters
    ----------
    paths : `list` [`str`]
        Frame paths to measure.
    worker_count : `int`
        Number of worker processes; 1 measures in this process.

    Returns
    -------
    fwhms : `list` [`float` or `None`]
        One width per path, in the same order; `None` where a frame was
        skipped.
    """
    if worker_count > 1 and len(paths) > 1:
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        try:
            # "spawn", never "fork": the backend runs many threads, and
            # Python's documentation warns that forking a program with
            # threads is unsafe (the child can inherit a lock that another
            # thread held). The worker function is at module level and its
            # argument is a path string, so it can be sent to a fresh
            # process. A script that reaches this pool needs an
            # ``if __name__ == "__main__":`` guard, because "spawn" imports
            # the script again in each worker.
            with ProcessPoolExecutor(
                max_workers=worker_count, mp_context=multiprocessing.get_context("spawn")
            ) as executor:
                return list(executor.map(_measure_one_frame_fwhm, paths))
        except (BrokenProcessPool, OSError) as pool_error:
            logger.warning("Parallel FWHM measurement failed (%s); measuring serially.", pool_error)
    return [_measure_one_frame_fwhm(path) for path in paths]


def stored_frame_widths(target_id: str, frames: Sequence[Any], configuration: Any = None) -> dict[str, float]:
    """Read the star widths the frame quality step already stored.

    The frame quality step stores each raw frame's
    ``frame.star_width_gaussian_brightest_px``, the value of
    `measure_fwhm_from_data` on the frame. That is the same estimator, on the
    same pixels, as `measure_image_fwhm` on the frame's file, so the stack
    check can use it instead of measuring the frame again. Only measured rows
    of the metric's current definition, for the whole frame (not one color
    plane), are used.

    Parameters
    ----------
    target_id : `str`
        The target the frames belong to.
    frames : `Sequence`
        The frames (they have ``path``).
    configuration : `AppConfiguration`, optional
        Says where the quality store is. The process-wide one if omitted.

    Returns
    -------
    widths : `dict` [`str`, `float`]
        The stored width of each frame that has one, by path. Empty when the
        store cannot be read; the caller then measures the frames.
    """
    from astrometricslib._internal.drivers.quality_store import MAXIMUM_SUBJECT_IDS_PER_QUERY, QualityStore
    from astrometricslib.models.quality.measurement import MeasurementStatus
    from astrometricslib.pipelines.shared.quality.metrics import (
        FRAME_METRICS,
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    )
    from astrometricslib.utilities.frame_identity import frame_subject_id

    version = next(
        spec.definition_version
        for spec in FRAME_METRICS
        if spec.name == FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX
    )
    path_by_subject = {frame_subject_id(frame.path, target_id): frame.path for frame in frames}
    subjects = list(path_by_subject)
    widths: dict[str, float] = {}
    try:
        store = QualityStore.from_configuration(configuration)
        for start in range(0, len(subjects), MAXIMUM_SUBJECT_IDS_PER_QUERY):
            for row in store.query(
                metric=FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
                target_id=target_id,
                library_only=True,
                subject_ids=subjects[start : start + MAXIMUM_SUBJECT_IDS_PER_QUERY],
            ):
                if (
                    row.definition_version == version
                    and row.status == MeasurementStatus.MEASURED
                    and not row.subject.channel
                    and row.value is not None
                ):
                    widths[path_by_subject[row.subject.subject_id]] = float(row.value)
    except (AstrometricsError, OSError, ValueError) as store_error:
        logger.debug("Stored frame widths of '%s' could not be read; measuring: %s", target_id, store_error)
        return {}
    return widths


def _stack_frames_and_weights(
    target_frames: list[Any], diagnostics: dict | None, stack_weight: str | None
) -> tuple[list[Any], list[float] | None]:
    """List the frames in the stack and the weight each had.

    Parameters
    ----------
    target_frames : `list`
        The frames given to the stage.
    diagnostics : `dict` or `None`
        What the stacking run reported. `None` means every frame counts, with
        equal weight.
    stack_weight : `str` or `None`
        The Siril ``-weight`` mode the stack used, `None` for equal weights.

    Returns
    -------
    frames : `list`
        The stack's member frames (see `determine_stack_members`).
    weights : `list` [`float`] or `None`
        Each member's weight, with mean 1, or `None` when the weights are not
        known (the prediction then uses equal weights).
    """
    if diagnostics is None:
        return list(target_frames), None
    from astrometricslib.pipelines.stacking.post_processing.stack_membership import (
        determine_stack_members,
        normalized_weights,
    )

    membership = determine_stack_members(target_frames, diagnostics, stack_weight)
    frames = [member.frame for member in membership.members]
    weights = normalized_weights(membership)
    if not frames or any(weight is None for weight in weights):
        if frames and membership.weights_reason:
            logger.info("Expected stack width uses equal weights: %s", membership.weights_reason)
        return frames or list(target_frames), None
    return frames, [float(weight) for weight in weights if weight is not None]


def measure_fwhm_degradation(
    summary: Any,
    stacked_path: str,
    target_frames: list[Any],
    stacked_fwhm: float | None = None,
    *,
    diagnostics: dict | None = None,
    stack_weight: str | None = None,
    stored_widths: Mapping[str, float] | None = None,
) -> None:
    """Compare the stacked image's sharpness against its input frames.

    Measured with the same `measure_image_fwhm` function on both sides. Every
    frame in the stack counts, so a stack of several nights is judged against
    all of them, and adding or removing one frame changes the numbers. A
    frame whose width the frame quality step already stored (`stored_widths`)
    is not measured again. The stack is compared with the width its inputs
    predict (see `expected_stack_fwhm`), with each frame weighted as the
    stacking program weighted it, not with their median.

    A stack combined from exposure groups arrives with its width already
    measured, away from the cores that the combine patched (see
    `stack_runner._measure_combined_fwhm`); that value is used instead of
    measuring the file, which would pick those patched cores as its
    brightest stars.

    Sets `summary.stacking_metrics`' FWHM fields in place.

    Parameters
    ----------
    summary : `StackQualitySummary`
        The stack's quality summary. Its FWHM fields are set.
    stacked_path : `str`
        The stacked image.
    target_frames : `list`
        The frames given to the stage.
    stacked_fwhm : `float`, optional
        The stack's width, when it was already measured.
    diagnostics : `dict`, optional
        What the stacking run reported. It says which frames are in the stack
        and their weights (see `determine_stack_members`). Without it every
        frame counts with equal weight.
    stack_weight : `str`, optional
        The Siril ``-weight`` mode the stack used, `None` for equal weights.
    stored_widths : `Mapping` [`str`, `float`], optional
        Widths already measured with the same estimator, by frame path (see
        `stored_frame_widths`).
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_image_fwhm
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        expected_stack_fwhm,
    )

    frames, weights = _stack_frames_and_weights(target_frames, diagnostics, stack_weight)
    known = dict(stored_widths or {})
    missing_paths = list(dict.fromkeys(frame.path for frame in frames if frame.path not in known))
    if missing_paths:
        measured = _measure_frame_fwhms(missing_paths, _fwhm_worker_count(len(missing_paths)))
        known.update({
            path: width for path, width in zip(missing_paths, measured, strict=True) if width is not None
        })

    input_fwhms: list[float] = []
    input_weights: list[float] = []
    for index, frame in enumerate(frames):
        width = known.get(frame.path)
        if width is None:
            continue
        input_fwhms.append(width)
        input_weights.append(1.0 if weights is None else weights[index])
    expected = expected_stack_fwhm(input_fwhms, input_weights)
    if input_fwhms:
        import statistics as _statistics

        summary.stacking_metrics.median_input_fwhm_px = _statistics.median(input_fwhms)
        summary.stacking_metrics.expected_stack_fwhm_px = expected

    if stacked_fwhm is None:
        stacked_fwhm = measure_image_fwhm(stacked_path)
    if stacked_fwhm is not None:
        summary.stacking_metrics.stacked_fwhm_px = stacked_fwhm
