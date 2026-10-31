"""Purpose: Measure how sharp a stack is compared with the frames that made it.

Description: A good stack is at least as sharp as the width its input frames
predict. This module measures the star width (FWHM, full width at half
maximum) of a sample of the input frames, in parallel when it is safe to do
so, and compares it with the star width of the stacked image. It sets the
FWHM fields and the degradation flag on the stack's quality summary.
"""

import logging
from typing import Any

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["measure_fwhm_degradation"]


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


def _fwhm_worker_count(sample_size: int) -> int:
    """Choose how many processes measure the sample frames at once.

    Uses the configured inner worker count (``photometry_workers``, the
    same setting the other per-target parallel steps use), kept within the
    available memory by `resolve_worker_counts`. One measurement of a 24
    megapixel frame was measured to use about 1 GB; the helper's default
    budget of 1.5 GB per worker covers that with margin. The count never
    exceeds the sample size. A process that is itself a daemon (such as a
    `multiprocessing.Pool` worker) may not have child processes, so it
    measures serially.

    Parameters
    ----------
    sample_size : `int`
        Number of frames to measure.

    Returns
    -------
    worker_count : `int`
        Number of worker processes; 1 means measure in this process.
    """
    import multiprocessing

    if sample_size < 2 or multiprocessing.current_process().daemon:
        return 1
    try:
        from astrometricslib.foundation.config import get_configuration
        from astrometricslib.utilities.concurrency import resolve_worker_counts

        counts = resolve_worker_counts("1", get_configuration().get_photometry_workers())
    except (AstrometricsError, OSError, ValueError) as configuration_error:
        logger.debug("Measuring FWHM serially; worker count unavailable: %s", configuration_error)
        return 1
    return max(1, min(counts.inner_worker_count, sample_size))


def _measure_sample_fwhms(paths: list[str], worker_count: int) -> list[float | None]:
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
        import multiprocessing
        from concurrent.futures import ProcessPoolExecutor
        from concurrent.futures.process import BrokenProcessPool

        try:
            # 'fork' like the photometry pool: 'spawn' re-imports the entry
            # script, which fails for scripts run as `python script.py`.
            with ProcessPoolExecutor(
                max_workers=worker_count, mp_context=multiprocessing.get_context("fork")
            ) as executor:
                return list(executor.map(_measure_one_frame_fwhm, paths))
        except (BrokenProcessPool, OSError) as pool_error:
            logger.warning("Parallel FWHM measurement failed (%s); measuring serially.", pool_error)
    return [_measure_one_frame_fwhm(path) for path in paths]


def measure_fwhm_degradation(
    summary: Any, stacked_path: str, target_frames: list[Any], stacked_fwhm: float | None = None
) -> None:
    """Compare the stacked image's sharpness against its input frames.

    Measured with the same `measure_image_fwhm` function on both sides. The
    input sample is spread evenly over all the frames, so a stack of several
    nights is judged against all of them and not only the first night. The
    stack is compared with the width its inputs predict (see
    `expected_stack_fwhm`), not with their median.

    A stack combined from exposure groups arrives with its width already
    measured, away from the cores that the combine patched (see
    `stack_runner._measure_combined_fwhm`); that value is used instead of
    measuring the file, which would pick those patched cores as its
    brightest stars.

    Sets `summary.stacking_metrics`' FWHM fields and the degradation
    flag in place.
    """
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import (
        FWHM_MEASUREMENT_STAR_COUNT,
        measure_image_fwhm,
    )
    from astrometricslib.pipelines.stacking.post_processing.stack_quality import (
        expected_stack_fwhm,
        is_stacked_fwhm_degraded,
    )

    # A sample of at most 15 frames (matching FWHM_MEASUREMENT_STAR_COUNT),
    # spaced evenly over the whole set. A median or an RMS only needs a
    # representative sample, unlike the background-split check, which needs
    # every frame to avoid missing a split.
    step = max(1, len(target_frames) // FWHM_MEASUREMENT_STAR_COUNT)
    sample_paths = [frame.path for frame in target_frames[::step][:FWHM_MEASUREMENT_STAR_COUNT]]
    input_fwhms = [
        fwhm
        for fwhm in _measure_sample_fwhms(sample_paths, _fwhm_worker_count(len(sample_paths)))
        if fwhm is not None
    ]
    expected = expected_stack_fwhm(input_fwhms)
    if input_fwhms:
        import statistics as _statistics

        summary.stacking_metrics.median_input_fwhm_px = _statistics.median(input_fwhms)
        summary.stacking_metrics.expected_stack_fwhm_px = expected

    if stacked_fwhm is None:
        stacked_fwhm = measure_image_fwhm(stacked_path)
    if stacked_fwhm is not None:
        summary.stacking_metrics.stacked_fwhm_px = stacked_fwhm
        if expected is not None:
            summary.stacking_metrics.fwhm_degraded = is_stacked_fwhm_degraded(stacked_fwhm, expected)
