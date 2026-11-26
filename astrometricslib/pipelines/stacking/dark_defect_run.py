"""Measures the dark frames of the library for particle tracks and bad frames.

`measure_calibration_frame` stores the numbers of one frame by itself. The
defects of a dark (a long track, a line across the frame, many outlier pixels)
only show against the frames of its own set, so they are measured here, set by
set. What one run does:

1. For each set of darks (one camera, gain, offset, binning, temperature slot
   and exposure) with enough frames, choose the frames of the set's reference
   (`choose_reference_paths`; the frames nearest the set's median level, so a
   few frames of another setting do not shift it).
2. Skip a frame whose file and reference are the same as when it was last
   measured. The stamp is kept in the calibration index, by the frame's
   subject id with a ``dark_defects/`` prefix, so the run costs almost nothing
   when nothing changed.
3. Build the set's reference (the per-pixel median) once, and measure each
   frame that needs it against the reference in two worker threads. The rows
   are stored with `record_measurements`.
4. Judge every measured frame of the set from its stored numbers
   (`judge_dark_defects`) and write the result to the calibration library
   (`CalibrationLibrary.replace_dark_defect_verdicts`). The judging uses only
   stored numbers, so a changed limit takes effect on the next run without
   reading any pixels.

The run never moves, changes or deletes a frame file. Leaving a dark out only
means the library no longer chooses it for a master.
"""

import contextvars
import hashlib
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary, CalibrationSet
from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.measurement import QualityMeasurement, SubjectLevel
from astrometricslib.pipelines.shared.frame_scanning import file_state_matches
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import calibration_subject_id
from astrometricslib.pipelines.shared.quality.dark_defect_measurement import (
    DARK_DEFECT_MINIMUM_SET_FRAMES,
    build_set_reference,
    choose_reference_paths,
    dark_defect_metric_set_fingerprint,
    measure_dark_defect_frame,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import UNREADABLE_FRAME_REASON_PREFIX
from astrometricslib.pipelines.shared.quality.metrics import (
    DARK_DEFECT_METRIC_NAMES,
    FRAME_CLIPPED_MEAN_LEVEL,
)
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking.pre_processing.dark_defect_judgement import (
    DefectVerdictKind,
    judge_dark_defects,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DARK_DEFECT_WORKERS",
    "DarkDefectSummary",
    "measure_dark_defects_in_library",
]

# Frames measured at the same time. Each worker holds one frame as floating
# point (36 MB for a 3008 x 3008 frame, 96 MB for 6000 x 4000) next to the
# set's reference, and the heavy steps run inside numpy and scipy, which
# release the interpreter lock. Two keeps the job under 1 GB, the same as the
# measuring job.
DARK_DEFECT_WORKERS = 2

# How many notes about frames a summary keeps.
_NOTE_LIMIT = 5

_STAMP_PREFIX = "dark_defects/"


@dataclass
class DarkDefectSummary:
    """What one run of `measure_dark_defects_in_library` did.

    Attributes
    ----------
    set_count : `int`
        The dark sets the run looked at.
    small_set_count : `int`
        Sets with too few frames to check. Their frames are kept.
    measured_count : `int`
        Frames read, measured and stored.
    unchanged_count : `int`
        Frames skipped because nothing changed since they were measured.
    unreadable_count : `int`
        Frames that could not be read. They are tried again on the next run.
    warned_count : `int`
        Frames kept with a warning (a long track inside the frame).
    omitted_count : `int`
        Frames the library no longer chooses for a master.
    rows_stored : `int`
        The new rows written to the quality store.
    notes : `list` [`str`]
        One plain sentence for each of the first warned or omitted frames,
        and for each failure.
    """

    set_count: int = 0
    small_set_count: int = 0
    measured_count: int = 0
    unchanged_count: int = 0
    unreadable_count: int = 0
    warned_count: int = 0
    omitted_count: int = 0
    rows_stored: int = 0
    notes: list[str] = field(default_factory=list)


def _read_noise_adu(calibration_set: CalibrationSet, configuration: AppConfiguration | None) -> float | None:
    """Find the read noise of a set's setting in ADU, from the camera profile.

    Parameters
    ----------
    calibration_set : `CalibrationSet`
        The set.
    configuration : `AppConfiguration` or `None`
        The configuration that holds the camera profiles.

    Returns
    -------
    read_noise_adu : `float` or `None`
        The read noise divided by the gain, or `None` when the profile has no
        entry. The offset does not change the read noise, so the entry of
        another offset is used when none matches (the set's own frames may be
        cut off at zero, which hides their noise).
    """
    profile = resolve_camera_profile(calibration_set.camera, configuration)
    if profile is None:
        return None
    entry = profile.sensor_noise_for(
        calibration_set.gain, calibration_set.offset, calibration_set.binning, clipped_at_zero=False
    )
    if entry is None or entry.read_noise_e is None or entry.gain_e_per_adu is None:
        return None
    return entry.read_noise_e.value / entry.gain_e_per_adu.value


def _stat_or_none(path: str) -> os.stat_result | None:
    """Read a file's size and times.

    Returns
    -------
    file_state : `os.stat_result` or `None`
        The `os.stat` result, or `None` if the file cannot be read.
    """
    try:
        return os.stat(path)
    except OSError:
        return None


def _frame_pixel_count(path: str) -> int | None:
    """Read the number of pixels of one frame from its header.

    Returns
    -------
    pixel_count : `int` or `None`
        Width times height, or `None` when the header cannot be read.
    """
    try:
        header = read_header(path)
        return int(header["NAXIS1"]) * int(header["NAXIS2"])
    except (*FITS_READ_ERRORS, KeyError, ValueError, TypeError):
        return None


def _measure_one(
    path: str,
    reference: np.ndarray,
    read_noise_adu: float | None,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
) -> tuple[list[QualityMeasurement], os.stat_result | None]:
    """Measure one dark against its set's reference.

    The file state is taken before the pixels are read.

    Parameters
    ----------
    path : `str`
        The dark frame.
    reference : `numpy.ndarray`
        The set's per-pixel median.
    read_noise_adu : `float` or `None`
        The profile's read noise in ADU.
    run_id, job_id, measured_at
        The run's facts, copied onto the rows.

    Returns
    -------
    result : `tuple`
        The frame's rows and the file state taken before the read.
    """
    state = _stat_or_none(path)
    rows = measure_dark_defect_frame(
        path, "dark", reference, read_noise_adu, run_id=run_id, job_id=job_id, measured_at=measured_at
    )
    return rows, state


def _note(summary: DarkDefectSummary, text: str) -> None:
    """Add a note to a summary while there is room."""
    if len(summary.notes) < _NOTE_LIMIT:
        summary.notes.append(text)


def measure_dark_defects_in_library(
    library: CalibrationLibrary,
    store: QualityStore,
    configuration: AppConfiguration | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    camera: str | None = None,
    force: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> DarkDefectSummary:
    """Measure the darks that are new or changed, then judge every dark set.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library. Its stamps and dark verdicts are updated and saved.
    store : `QualityStore`
        Where the rows are stored.
    configuration : `AppConfiguration` or `None`
        The configuration that holds the camera profiles.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the run starts, with a time zone.
    camera : `str`, optional
        Only this camera's darks.
    force : `bool`, optional
        Measure every dark again, whatever its stamp says.
    progress : `Callable`, optional
        Called as ``progress(done, total)`` after each frame measured.

    Returns
    -------
    summary : `DarkDefectSummary`
        The counts and the first notes.
    """
    summary = DarkDefectSummary()
    minimum_frames = resolve(DARK_DEFECT_MINIMUM_SET_FRAMES).value
    fingerprint = dark_defect_metric_set_fingerprint()
    levels_by_subject = {
        row.subject.subject_id: row.value
        for row in store.query(
            metric=FRAME_CLIPPED_MEAN_LEVEL, level=SubjectLevel.FRAME, subject_id_prefix="calibration/dark/"
        )
        if row.value is not None
    }
    sets = []
    seen: set[str] = set()
    for calibration_set in library.list_calibration_sets("dark", camera=camera):
        # A file can be listed under two settings; it is measured once,
        # with the first set.
        paths = [path for path in dict.fromkeys(calibration_set.paths) if path not in seen]
        seen.update(paths)
        summary.set_count += 1
        if len(paths) < minimum_frames:
            summary.small_set_count += 1
            library.replace_dark_defect_verdicts(paths, {})
            continue
        reference_paths = choose_reference_paths(
            paths, [levels_by_subject.get(calibration_subject_id(path, "dark")) for path in paths]
        )
        signature = f"{fingerprint}|{hashlib.sha256(chr(10).join(reference_paths).encode()).hexdigest()[:12]}"
        pending = []
        for path in paths:
            stamp = library.measurement_stamp(_STAMP_PREFIX + calibration_subject_id(path, "dark"))
            state = _stat_or_none(path)
            current = (
                stamp is not None
                and stamp.get("metric_set") == signature
                and file_state_matches(state, stamp.get("size"), stamp.get("modified_ns"))
            )
            if force or not current:
                pending.append(path)
            else:
                summary.unchanged_count += 1
        sets.append((calibration_set, paths, reference_paths, signature, pending))

    total_pending = sum(len(item[4]) for item in sets)
    done = 0
    for calibration_set, _, reference_paths, signature, pending in sets:
        if not pending:
            continue
        try:
            reference = build_set_reference(reference_paths)
        except (*FITS_READ_ERRORS, ValueError, OSError) as read_error:
            _note(
                summary,
                f"the reference of a {calibration_set.exposure_seconds} s dark set failed: {read_error}",
            )
            summary.unreadable_count += len(pending)
            continue
        read_noise_adu = _read_noise_adu(calibration_set, configuration)

        with ThreadPoolExecutor(max_workers=DARK_DEFECT_WORKERS) as pool:
            futures = {
                pool.submit(
                    contextvars.copy_context().run,
                    _measure_one,
                    path,
                    reference,
                    read_noise_adu,
                    run_id,
                    job_id,
                    measured_at,
                ): path
                for path in pending
            }
            for future, path in futures.items():
                done += 1
                try:
                    rows, state = future.result()
                    if all(row.reason.startswith(UNREADABLE_FRAME_REASON_PREFIX) for row in rows):
                        summary.unreadable_count += 1
                        _note(summary, f"{path}: {rows[0].reason}")
                    else:
                        summary.rows_stored += record_measurements(store, rows)
                        if state is not None:
                            library.set_measurement_stamp(
                                _STAMP_PREFIX + calibration_subject_id(path, "dark"),
                                state.st_size,
                                state.st_mtime_ns,
                                signature,
                            )
                        summary.measured_count += 1
                except (AstrometricsError, OSError, ValueError) as frame_error:
                    summary.unreadable_count += 1
                    _note(summary, f"{path}: {frame_error}")
                    logger.warning("Could not measure dark frame %s for defects: %s", path, frame_error)
                if progress is not None:
                    progress(done, total_pending)

    _judge_sets(library, store, [(item[0], item[1]) for item in sets], summary)
    try:
        library.save_library()
    except OSError as save_error:
        _note(summary, f"the calibration index could not be saved: {save_error}")
    return summary


def _judge_sets(
    library: CalibrationLibrary,
    store: QualityStore,
    sets: list[tuple[CalibrationSet, list[str]]],
    summary: DarkDefectSummary,
) -> None:
    """Judge each set from its stored numbers and write the verdicts.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The library that receives the verdicts.
    store : `QualityStore`
        Where the numbers were stored.
    sets : `list` [`tuple`]
        Each set with its frame paths.
    summary : `DarkDefectSummary`
        Receives the warned and omitted counts and the first notes.
    """
    wanted = set(DARK_DEFECT_METRIC_NAMES)
    values_by_subject: dict[str, dict[str, float]] = {}
    for row in store.query(level=SubjectLevel.FRAME, subject_id_prefix="calibration/dark/"):
        if row.metric in wanted and row.value is not None:
            values_by_subject.setdefault(row.subject.subject_id, {})[row.metric] = row.value
    for _, paths in sets:
        pixel_count = next((count for count in map(_frame_pixel_count, paths[:3]) if count), None)
        values_by_path = {
            path: values_by_subject[calibration_subject_id(path, "dark")]
            for path in paths
            if calibration_subject_id(path, "dark") in values_by_subject
        }
        if pixel_count is None or not values_by_path:
            continue
        marks: dict[str, dict[str, str]] = {}
        for path, verdict in judge_dark_defects(values_by_path, pixel_count).items():
            if verdict.kind is DefectVerdictKind.KEEP:
                continue
            marks[path] = {"verdict": verdict.kind.value, "reason": verdict.reason}
            if verdict.kind is DefectVerdictKind.OMIT:
                summary.omitted_count += 1
            else:
                summary.warned_count += 1
            _note(summary, f"{os.path.basename(path)} ({verdict.kind.value}): {verdict.reason}")
        library.replace_dark_defect_verdicts(paths, marks)
