"""Measures the dark and bias frames of the calibration library once.

`measure_calibration_frame` (in `pipelines/shared/quality/`) measures one dark
or bias frame and writes nothing. This module is the glue around it, in the
same way `frame_measurement_run.py` is for light frames. What one run does:

1. List every dark and bias frame of the library.
2. Skip a frame whose file has the size and last-change time it had when it
   was last measured, and was measured with the current set of calibration
   metrics. Asking the file system does not read the pixels, so a second run
   over an unchanged library costs almost nothing. Light frames keep this
   stamp on their frame record inside a target. A calibration frame has no
   target, so its stamp is kept in the calibration library's own index file
   (`CalibrationLibrary.measurement_stamps`), by the frame's subject id.
3. Measure the remaining frames in a few worker threads.
4. Store each frame's rows through `record_measurements`, then stamp the
   frame. A frame that could not be read is not stored and not stamped, so the
   next run tries it again.

The stamps are saved to the index every `STAMP_SAVE_INTERVAL_FRAMES` frames
and at the end, so an interrupted run keeps most of its progress.

The run never moves, changes or deletes a frame file. A failure on one frame
is logged and counted and does not stop the others.

How long it takes. A 3008 x 3008 pixel frame takes about 0.5 s of one core,
and a 6000 x 4000 pixel frame about 1 s (measured during the validation
run, register entry STK-47). The first run over a library of 2,367 frames
therefore takes tens of minutes, so callers run it as a job.
"""

import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime

from astrometricslib.drivers.calibration_library import CalibrationLibrary
from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.pipelines.shared.frame_scanning import file_state_matches
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import (
    calibration_metric_set_fingerprint,
    calibration_subject_id,
    measure_calibration_frame,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import UNREADABLE_FRAME_REASON_PREFIX
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements

logger = logging.getLogger(__name__)

__all__ = [
    "STAMP_SAVE_INTERVAL_FRAMES",
    "CalibrationMeasurementSummary",
    "calibration_frame_needs_measuring",
    "measure_calibration_frames",
]

# The number of frames measured at the same time. Each worker holds one frame
# as 16-bit numbers and several copies as floating point, about 300 MB for a
# 6000 x 4000 pixel Nikon frame, so two workers keep the job under 1 GB. The
# heavy steps (sigma clipping, medians) run inside numpy, which releases
# Python's global lock, so two threads do overlap.
CALIBRATION_MEASUREMENT_WORKERS = 2

# How many frames pass between saves of the stamps to the calibration index.
# The index file is a few hundred kilobytes, so a save every 20 frames costs
# little and loses at most 20 frames of progress. The same figure as the light
# frame job.
STAMP_SAVE_INTERVAL_FRAMES = 20

# How many failure notes a summary keeps, so one bad mount does not fill it.
FAILURE_NOTES_LIMIT = 5


@dataclass
class CalibrationMeasurementSummary:
    """What one run of `measure_calibration_frames` did.

    Attributes
    ----------
    frame_count : `int`
        The dark and bias frames the run looked at.
    measured_count : `int`
        Frames read, measured and stored.
    unchanged_count : `int`
        Frames skipped because their file still matches its stamp.
    unreadable_count : `int`
        Frames that could not be read. They are tried again on the next run.
    failed_count : `int`
        Frames whose rows could not be stored, or that failed unexpectedly.
    rows_stored : `int`
        The new rows written to the quality store.
    notes : `list` [`str`]
        One plain sentence for each of the first failures.
    """

    frame_count: int = 0
    measured_count: int = 0
    unchanged_count: int = 0
    unreadable_count: int = 0
    failed_count: int = 0
    rows_stored: int = 0
    notes: list[str] = field(default_factory=list)


def calibration_frame_needs_measuring(
    library: CalibrationLibrary, subject_id: str, file_state: os.stat_result | None
) -> bool:
    """Tell whether a calibration frame's file changed since it was measured.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library that holds the stamps.
    subject_id : `str`
        The frame's quality subject id.
    file_state : `os.stat_result` or `None`
        The file's `os.stat` result now, or `None` if it cannot be read.

    Returns
    -------
    needs_measuring : `bool`
        `True` unless the frame has a stamp, the file still matches it (by the
        rule the header scan and the light frame job use) and the frame was
        measured with the current calibration metric set. A frame that cannot
        be read always needs measuring, to find out why.
    """
    stamp = library.measurement_stamp(subject_id)
    if stamp is None or stamp.get("metric_set") != calibration_metric_set_fingerprint():
        return True
    return not file_state_matches(file_state, stamp.get("size"), stamp.get("modified_ns"))


def _frame_was_unreadable(rows: list[QualityMeasurement]) -> bool:
    """Tell whether the rows of a frame say the file could not be read.

    Parameters
    ----------
    rows : `list` [`QualityMeasurement`]
        The frame's measurements.

    Returns
    -------
    unreadable : `bool`
        `True` when every row is ``not_measured`` with the unreadable reason.
    """
    return bool(rows) and all(row.reason.startswith(UNREADABLE_FRAME_REASON_PREFIX) for row in rows)


def measure_calibration_frames(
    library: CalibrationLibrary,
    store: QualityStore,
    configuration: AppConfiguration | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    kinds: tuple[str, ...] = ("dark", "bias"),
    camera: str | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> CalibrationMeasurementSummary:
    """Measure the dark and bias frames that are new or changed.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library to measure. Its stamps are updated and saved.
    store : `QualityStore`
        Where the rows are stored.
    configuration : `AppConfiguration` or `None`
        The configuration that holds the camera profiles. `None` uses the
        process-wide one.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the run starts, with a time zone.
    kinds : `tuple` [`str`, ...], optional
        ``"dark"``, ``"bias"`` or both (the default).
    camera : `str`, optional
        Only this camera's frames.
    force : `bool`, optional
        Measure every frame again, whatever its stamp says.
    progress : `Callable`, optional
        Called as ``progress(done, total)`` after each frame, counting the
        frames that needed measuring.

    Returns
    -------
    summary : `CalibrationMeasurementSummary`
        The counts, and a note for each of the first failures.
    """
    summary = CalibrationMeasurementSummary()
    pending: list[tuple[str, str, str, str]] = []
    seen_subjects: set[str] = set()
    for kind in kinds:
        for calibration_set in library.list_calibration_sets(kind, camera=camera):
            for path in calibration_set.paths:
                subject_id = calibration_subject_id(path, kind)
                if subject_id in seen_subjects:
                    continue
                seen_subjects.add(subject_id)
                summary.frame_count += 1
                try:
                    file_state = os.stat(path)
                except OSError:
                    file_state = None
                if force or calibration_frame_needs_measuring(library, subject_id, file_state):
                    pending.append((kind, calibration_set.camera, path, subject_id))
                else:
                    summary.unchanged_count += 1

    profile_by_camera = {
        camera_name: resolve_camera_profile(camera_name, configuration)
        for camera_name in {item[1] for item in pending}
    }
    save_lock = threading.Lock()
    stamped_since_save = 0

    def save_stamps() -> None:
        """Write the stamps to the calibration index, noting a failure."""
        try:
            library.save_library()
        except OSError as save_error:
            _note(summary, f"the calibration index could not be saved: {save_error}")

    def measure_one(
        item: tuple[str, str, str, str],
    ) -> tuple[list[QualityMeasurement], os.stat_result | None]:
        """Read and measure one frame, taking its file state first.

        Returns
        -------
        result : `tuple`
            The frame's rows and the file state taken before the read.
        """
        kind, camera_name, path, _ = item
        file_state = _stat_or_none(path)
        rows = measure_calibration_frame(
            path, kind, profile_by_camera[camera_name], run_id=run_id, job_id=job_id, measured_at=measured_at
        )
        return rows, file_state

    done = 0
    with ThreadPoolExecutor(max_workers=CALIBRATION_MEASUREMENT_WORKERS) as pool:
        futures = {pool.submit(measure_one, item): item for item in pending}
        for future in as_completed(futures):
            _, _, path, subject_id = futures[future]
            done += 1
            try:
                rows, file_state = future.result()
                if _frame_was_unreadable(rows):
                    summary.unreadable_count += 1
                    _note(summary, f"{path}: {rows[0].reason}")
                else:
                    summary.rows_stored += record_measurements(store, rows)
                    if file_state is not None:
                        library.set_measurement_stamp(
                            subject_id,
                            file_state.st_size,
                            file_state.st_mtime_ns,
                            calibration_metric_set_fingerprint(),
                        )
                    summary.measured_count += 1
            except (AstrometricsError, OSError, ValueError) as frame_error:
                summary.failed_count += 1
                _note(summary, f"{path}: {frame_error}")
                logger.warning("Could not measure calibration frame %s: %s", path, frame_error)
            with save_lock:
                stamped_since_save += 1
                if stamped_since_save >= STAMP_SAVE_INTERVAL_FRAMES:
                    stamped_since_save = 0
                    save_stamps()
            if progress is not None:
                progress(done, len(pending))
    if pending:
        save_stamps()
    return summary


def _stat_or_none(path: str) -> os.stat_result | None:
    """Read a file's size and times.

    Parameters
    ----------
    path : `str`
        The file's path.

    Returns
    -------
    file_state : `os.stat_result` or `None`
        The `os.stat` result, or `None` if the file cannot be read.
    """
    try:
        return os.stat(path)
    except OSError:
        return None


def _note(summary: CalibrationMeasurementSummary, text: str) -> None:
    """Add a failure note to a summary while there is room.

    Parameters
    ----------
    summary : `CalibrationMeasurementSummary`
        The summary to add to.
    text : `str`
        The sentence.
    """
    if len(summary.notes) < FAILURE_NOTES_LIMIT:
        summary.notes.append(text)
