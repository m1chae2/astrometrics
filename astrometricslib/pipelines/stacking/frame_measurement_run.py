"""Measures the light frames of a target once and stores the numbers.

`measure_frame` (in `pipelines/shared/quality/frame_measurement.py`) measures
one raw frame and writes nothing. This module is the glue around it, beside
`sensor_noise_run.py` and `calibration_ingest.py`, the other code that reads
the calibration library. It sits in the stacking folder and not under
`pipelines/shared/quality/` because it passes in the Gaussian star width from
the astrometry pipeline, and code under `shared/` may not import a pipeline.

What one run does, in order:

1. List the target's light frames, newest first.
2. Skip a frame whose file has the size and last-change time it had when it
   was last measured (`FrameRecord.quality_measured_file_size` and
   `quality_measured_file_modified_ns`). Asking the file system for that
   stamp does not read the pixels, so a second run over an unchanged library
   costs almost nothing.
3. Find the camera profile, the capture setting row and the bias level for
   each remaining frame. The bias level is the median over a few bias frames
   taken at exactly the frame's gain setting, offset and binning. A nearby
   setting's bias is never used: a frame with no bias at its own setting gets
   its sky metrics ``not_measured`` with that reason.
4. Measure the frames with a few worker threads (the pixel work is NumPy and
   SciPy, which release Python's global lock for much of their time, and the
   files may be on a network mount, where reads overlap).
5. Store each frame's rows in one transaction through `record_measurements`,
   then stamp the frame. A frame whose rows could not be stored is not
   stamped, so the next run tries it again.

The run never moves, changes or deletes a frame file. A failure on one frame
is logged and counted and does not stop the others.

The same module records the quarantine step's own numbers for every frame it
checked (`quarantine_measurements_for_frame`), so the stacking stage can keep
what it measured about the frames it did not move.
"""

import contextvars
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError, StorageError
from astrometricslib.foundation.jobs.runner import get_current_job
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.frame_measurement_summary import FrameMeasurementSummary
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_scanning import file_state_matches
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    UNREADABLE_FRAME_REASON_PREFIX,
    frame_subject_id,
    measure_frame,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_BACKGROUND_LEVEL
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking.metrics import FRAME_QUARANTINE_ROUNDNESS, FRAME_QUARANTINE_SKY_MEDIAN
from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

__all__ = [
    "BIAS_FRAMES_FOR_LEVEL",
    "MEASUREMENT_WORKER_COUNT",
    "STACKING_STAGE_MEASUREMENT_LIMIT",
    "BiasLevelFinder",
    "FrameMeasurementSession",
    "build_capture_setting",
    "frame_needs_measuring",
    "quarantine_measurements_for_frame",
    "record_frame_measurements_for_target",
    "record_stage_frame_quality",
]

# How many frames are measured at the same time. Measuring one 3008 x 3008 px
# frame takes 11 to 14 s on one core of this 12-core machine (timed on the two
# golden M 13 frames; see `FRAME_MEASUREMENT_TIME_BUDGET_SECONDS`). Four
# workers is the same count the quarantine step uses for its reads
# (`MEASUREMENT_THREADS`): it overlaps the file reads on a network mount and
# the NumPy work, and it leaves most of the 12 cores to the app and the
# backend, so the interface stays responsive while a sync is measured. It is a
# courtesy limit, not a tuned optimum.
MEASUREMENT_WORKER_COUNT = 4

# How many bias frames are read to find the bias level of one setting. The
# level of one frame is the mean of about nine million pixels, so its random
# error is the read noise divided by the square root of that count, far below
# 0.01 ADU. A median over up to five frames guards against one odd frame (a
# cosmic-ray hit or a half-written file) rather than against noise. Five reads
# cost about a second and are made once per setting per run.
BIAS_FRAMES_FOR_LEVEL = 5

# The most frames the stacking stage measures in full during one stack. The
# job after a sync normally measures every frame long before it is stacked, so
# the stage only picks up frames that were never measured (an older library, or
# a sync that failed). Measuring is about 3 s per frame with four workers (12 s
# per frame divided by `MEASUREMENT_WORKER_COUNT`), so 40 frames add about two
# minutes to a stack, which is small next to the stack itself. The frames that
# are left wait for the next stack or the next call of
# `ProcessingPipelines.record_frame_measurements`. Not tuned beyond that.
STACKING_STAGE_MEASUREMENT_LIMIT = 40

# How many failed frames a run describes by name in its notes. The count is
# always complete; the names are only a pointer for the reader of the log.
FAILURE_NOTES_LIMIT = 5


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


def frame_needs_measuring(frame: FrameRecord, file_state: os.stat_result | None) -> bool:
    """Tell whether a frame's file changed since its quality was recorded.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame record, carrying the file stamp from its last measurement.
    file_state : `os.stat_result` or `None`
        The file's `os.stat` result now, or `None` if it cannot be read.

    Returns
    -------
    needs_measuring : `bool`
        `True` unless the frame has a stamp and the file still matches it,
        by the same rule the header scan uses (`file_state_matches`). A frame
        never measured has no stamp, so it needs measuring.
    """
    return not file_state_matches(
        file_state, frame.quality_measured_file_size, frame.quality_measured_file_modified_ns
    )


def _stamp_frame_as_measured(frame: FrameRecord, file_state: os.stat_result) -> None:
    """Record the file state a frame was measured at.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame to stamp.
    file_state : `os.stat_result`
        The file's state taken before it was read, so a file that changed
        during the measurement still reads as changed next time.
    """
    frame.quality_measured_file_size = file_state.st_size
    frame.quality_measured_file_modified_ns = file_state.st_mtime_ns


def _whole_number(text: object) -> int | None:
    """Read a camera setting as a whole number.

    Parameters
    ----------
    text : `object`
        A setting such as ``"100"``, ``"100.0"`` or ``10``.

    Returns
    -------
    number : `int` or `None`
        The value, or `None` when it is missing or not a whole number.
    """
    try:
        number = float(str(text).strip())
    except ValueError:
        return None
    return int(number) if math.isfinite(number) and number == int(number) else None


def build_capture_setting(frame: FrameRecord) -> CaptureSetting:
    """Describe the equipment and exposure settings of a frame.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame record.

    Returns
    -------
    setting : `CaptureSetting`
        The camera, telescope, focal length, pixel scale, filter, exposure,
        gain setting, offset and binning. A value the frame does not state is
        `None`. A telescope recorded as ``"Unknown"`` is `None`.
    """
    try:
        exposure_seconds: float | None = float(frame.exposure)
    except TypeError, ValueError:
        exposure_seconds = None
    if exposure_seconds is not None and not math.isfinite(exposure_seconds):
        exposure_seconds = None
    filter_name = str(frame.filter)
    return CaptureSetting(
        camera=frame.camera,
        telescope=None if frame.telescope in ("", "Unknown") else frame.telescope,
        focal_length_mm=frame.focal_length_mm,
        pixel_scale_arcsec=frame.pixel_scale_arcsec,
        filter_name=None if filter_name in ("", "None") else filter_name,
        exposure_seconds=exposure_seconds,
        gain_setting=_whole_number(canonical_gain_setting(frame.iso)),
        offset=_whole_number(canonical_offset_setting(frame.offset)),
        binning=_whole_number(canonical_binning_setting(frame.binning).split("x")[0]),
    )


def frame_was_unreadable(measurements: Sequence[QualityMeasurement]) -> bool:
    """Tell whether a measurement failed because the file could not be read.

    Parameters
    ----------
    measurements : `Sequence` [`QualityMeasurement`]
        The result of `measure_frame`.

    Returns
    -------
    unreadable : `bool`
        `True` when the background level is ``not_measured`` for the reason
        that the frame could not be read.
    """
    return any(
        row.metric == FRAME_BACKGROUND_LEVEL
        and row.status == MeasurementStatus.NOT_MEASURED
        and row.reason.startswith(UNREADABLE_FRAME_REASON_PREFIX)
        for row in measurements
    )


class BiasLevelFinder:
    """Finds the bias level of a camera setting, once per setting.

    The bias level is the constant the camera adds to every pixel of a raw
    frame, in ADU (analog-to-digital units, one step of the stored pixel
    value). A raw frame holds it inside its pixels and does not state it, so
    it is read from the bias frames taken at the same gain setting, offset and
    binning. Answers are kept, so a run reads each setting's bias frames once.

    Parameters
    ----------
    library : `CalibrationLibrary`
        The calibration library that lists the bias frames.
    """

    def __init__(self, library: Any) -> None:
        self._library = library
        self._answers: dict[tuple[str, str, str, str], tuple[float | None, str]] = {}
        self._lock = threading.Lock()

    def find(self, frame: FrameRecord) -> tuple[float | None, str]:
        """Give the bias level for the setting a frame was taken at.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame, which names the camera, gain setting, offset and
            binning.

        Returns
        -------
        bias_level_adu : `float` or `None`
            The median, over up to `BIAS_FRAMES_FOR_LEVEL` bias frames at
            exactly this setting, of each frame's mean level. `None` when the
            library holds no bias frames at exactly this setting or none can
            be read.
        reason : `str`
            Why the level is missing, or an empty string.
        """
        gain = canonical_gain_setting(frame.iso)
        offset = canonical_offset_setting(frame.offset)
        binning = canonical_binning_setting(frame.binning)
        key = (frame.camera, gain, offset, binning)
        with self._lock:
            if key not in self._answers:
                self._answers[key] = self._read_level(frame.camera, gain, offset, binning)
            return self._answers[key]

    def _read_level(self, camera: str, gain: str, offset: str, binning: str) -> tuple[float | None, str]:
        """Read the bias level of one setting from the library's bias frames.

        Returns
        -------
        bias_level_adu : `float` or `None`
            The level, or `None`.
        reason : `str`
            Why it is missing, or an empty string.
        """
        setting_text = f"gain {gain}, offset {offset}, binning {binning}"
        try:
            selection = self._library.select_bias_frames(camera, iso=gain, offset=offset, binning=binning)
        except (AstrometricsError, *DATA_ERRORS) as error:
            return None, f"the calibration library could not list bias frames for {camera}: {error}"
        # Any flag means the library fell back to another setting. A bias
        # level from a nearby setting would be wrong, so none is used.
        if selection.flags or selection.blocking_flags or not selection.frames:
            return (
                None,
                f"the calibration library holds no bias frames for {camera} at exactly {setting_text}",
            )
        levels = []
        for path in sorted(selection.frames)[:BIAS_FRAMES_FOR_LEVEL]:
            try:
                with open_primary_hdu(path) as hdu:
                    levels.append(float(np.mean(collapse_to_2d(np.asarray(hdu.data)), dtype=np.float64)))
            except (*FITS_READ_ERRORS, *DATA_ERRORS) as error:
                logger.warning("Could not read the bias frame %s: %s", path, error)
        if not levels:
            return None, f"none of the bias frames for {camera} at {setting_text} could be read"
        return float(np.median(levels)), ""


class FrameMeasurementSession:
    """Measures frames and stores the rows, with the lookups shared by a run.

    A session holds the quality store, the calibration library's bias finder
    and the camera profiles it has resolved, so a run looks each one up once.
    One session serves one run: every row it writes carries the same run id
    and time.

    Parameters
    ----------
    store : `QualityStore`
        Where the rows are written.
    library : `CalibrationLibrary`
        The calibration library, for the bias levels.
    run_id : `str` or `None`, optional
        The id that groups this run's rows. The current job's id when there is
        one, otherwise a new unique id.
    job_id : `str` or `None`, optional
        The job's id, kept on each row as a soft link to the job record.
    measured_at : `datetime`
        When the run measures, with a time zone. The caller supplies it:
        code in a pipeline never reads the clock.
    """

    def __init__(
        self,
        store: QualityStore,
        library: Any,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        measured_at: datetime,
    ) -> None:
        job = get_current_job()
        self.store = store
        self.bias_levels = BiasLevelFinder(library)
        self.job_id = job_id if job_id is not None else (job.job_id if job is not None else None)
        self.run_id = run_id or self.job_id or f"frame-measurement-{uuid.uuid4().hex}"
        self.measured_at = measured_at
        self._profiles: dict[str, CameraProfile] = {}
        self._capture_setting_ids: dict[CaptureSetting, int] = {}

    def capture_setting_id(self, frame: FrameRecord) -> int:
        """Find or add the capture setting row for a frame.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame.

        Returns
        -------
        capture_setting_id : `int`
            The row id, the same for every frame with the same settings.

        Notes
        -----
        A database failure raises `StorageError`. Call this from one thread:
        it writes.
        """
        setting = build_capture_setting(frame)
        if setting not in self._capture_setting_ids:
            self._capture_setting_ids[setting] = self.store.get_or_create_capture_setting(setting)
        return self._capture_setting_ids[setting]

    def camera_profile(self, frame: FrameRecord) -> CameraProfile:
        """Find the camera profile for a frame's camera.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame.

        Returns
        -------
        profile : `CameraProfile`
            The matching profile, or the generic fallback profile.
        """
        if frame.camera not in self._profiles:
            self._profiles[frame.camera] = resolve_camera_profile(frame.camera)
        return self._profiles[frame.camera]

    def measure(
        self, frame: FrameRecord, target_id: str, capture_setting_id: int
    ) -> list[QualityMeasurement]:
        """Read one frame's pixels and measure it. Nothing is written.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame.
        target_id : `str`
            The target the frame belongs to.
        capture_setting_id : `int`
            The frame's capture setting row, from `capture_setting_id`.

        Returns
        -------
        measurements : `list` [`QualityMeasurement`]
            One per frame metric (see `measure_frame`).
        """
        # Imported here: the astrometry pipeline is a peer of this one, and
        # only the width function is needed.
        from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

        bias_level_adu, bias_reason = self.bias_levels.find(frame)
        return measure_frame(
            frame.path,
            frame,
            target_id,
            self.camera_profile(frame),
            run_id=self.run_id,
            job_id=self.job_id,
            measured_at=self.measured_at,
            capture_setting_id=capture_setting_id,
            bias_level_adu=bias_level_adu,
            bias_level_reason=bias_reason,
            measure_gaussian_width=measure_fwhm_from_data,
        )

    def record(self, measurements: Sequence[QualityMeasurement]) -> int:
        """Store one frame's rows in one transaction.

        Parameters
        ----------
        measurements : `Sequence` [`QualityMeasurement`]
            The rows of one frame.

        Returns
        -------
        new_count : `int`
            How many rows were new.
        """
        return record_measurements(self.store, measurements)

    def measure_and_record(self, frame: FrameRecord, target_id: str) -> int:
        """Measure one frame, store its rows and stamp the frame.

        Parameters
        ----------
        frame : `FrameRecord`
            The frame. It is stamped in memory; the caller saves the target.
        target_id : `str`
            The target the frame belongs to.

        Returns
        -------
        new_count : `int`
            How many rows were new.

        Raises
        ------
        StorageError
            If the file cannot be read.
        """
        file_state = os.stat(frame.path)
        capture_setting_id = self.capture_setting_id(frame)
        measurements = self.measure(frame, target_id, capture_setting_id)
        if frame_was_unreadable(measurements):
            raise StorageError(f"{os.path.basename(frame.path)} could not be read")
        new_count = self.record(measurements)
        _stamp_frame_as_measured(frame, file_state)
        return new_count


def quarantine_measurements_for_frame(
    measurement: dict[str, Any],
    frame: FrameRecord,
    target_id: str,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    capture_setting_id: int | None,
) -> list[QualityMeasurement]:
    """Turn one frame's quarantine measurement into quality rows.

    Parameters
    ----------
    measurement : `dict`
        One entry from `raw_frame_check.measure_raw_frame`: ``roundness``
        (`None` when no star had a measurable shape) and ``sky_median_adu``.
    frame : `FrameRecord`
        The frame the entry measures.
    target_id : `str`
        The target the frame belongs to.
    run_id : `str`
        The run the rows belong to.
    job_id : `str` or `None`
        The job, if any.
    measured_at : `datetime`
        When the quarantine step measured (with a time zone).
    capture_setting_id : `int` or `None`
        The frame's capture setting row.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One roundness row and one sky median row. The roundness row is
        ``not_measured`` with a reason when the frame had no measurable star.
    """
    subject = QualitySubject(
        level=SubjectLevel.FRAME,
        subject_id=frame_subject_id(frame.path, target_id),
        target_id=target_id,
        observing_night_id=observing_night_id(frame.timestamp) if frame.timestamp is not None else None,
        capture_setting_id=capture_setting_id,
    )
    common = {
        "definition_version": 1,
        "subject": subject,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": run_id,
        "job_id": job_id,
        "measured_at": measured_at,
    }
    roundness = measurement.get("roundness")
    sky = measurement.get("sky_median_adu")
    rows = []
    for metric, value, missing_reason in (
        (FRAME_QUARANTINE_ROUNDNESS, roundness, "no star with a measurable shape was found"),
        (FRAME_QUARANTINE_SKY_MEDIAN, sky, "the median could not be computed"),
    ):
        if value is None or not math.isfinite(float(value)):
            rows.append(
                QualityMeasurement(
                    metric=metric, status=MeasurementStatus.NOT_MEASURED, reason=missing_reason, **common
                )
            )
        else:
            rows.append(
                QualityMeasurement(
                    metric=metric, status=MeasurementStatus.MEASURED, value=float(value), **common
                )
            )
    return rows


def record_frame_measurements_for_target(
    target: Target,
    session: FrameMeasurementSession,
    *,
    frames: Sequence[FrameRecord] | None = None,
    limit: int | None = None,
    save_target: Callable[[], None] | None = None,
) -> FrameMeasurementSummary:
    """Measure the light frames that need it and store the rows.

    Parameters
    ----------
    target : `Target`
        The target. Its frames are stamped in memory as they are measured.
    session : `FrameMeasurementSession`
        The store, bias finder and run identity for this run.
    frames : `Sequence` [`FrameRecord`], optional
        Only these frames (light frames among them are used). Every light
        frame of the target when omitted.
    limit : `int`, optional
        Measure at most this many frames, newest first. The rest wait for the
        next run.
    save_target : `Callable`, optional
        Called once at the end, when any frame was stamped, to save the
        target's stamps. It is called even if the run was interrupted by an
        error.

    Returns
    -------
    summary : `FrameMeasurementSummary`
        The counts and notes of the run.
    """
    started = time.monotonic()
    chosen = list(target.frames if frames is None else frames)
    lights = [frame for frame in chosen if str(frame.role).upper() == "LIGHT"]
    lights.sort(key=lambda frame: frame.timestamp if frame.timestamp is not None else -math.inf, reverse=True)
    summary = FrameMeasurementSummary(target_id=target.id, frames_considered=len(lights))

    pending: list[tuple[FrameRecord, os.stat_result, int]] = []
    failure_notes: list[str] = []
    deferred_count = 0
    for frame in lights:
        file_state = _stat_or_none(frame.path)
        if file_state is None:
            summary.frames_failed += 1
            failure_notes.append(f"{os.path.basename(frame.path)}: the file cannot be read")
            continue
        if not frame_needs_measuring(frame, file_state):
            summary.frames_skipped_unchanged += 1
            continue
        if limit is not None and len(pending) >= limit:
            deferred_count += 1
            continue
        try:
            pending.append((frame, file_state, session.capture_setting_id(frame)))
        except AstrometricsError as error:
            summary.frames_failed += 1
            failure_notes.append(f"{os.path.basename(frame.path)}: {error}")

    job = get_current_job()
    caller_context = contextvars.copy_context()
    stamped_any = False
    night_ids: set[str] = set()

    def measure_in_job_context(item: tuple[FrameRecord, os.stat_result, int]) -> list[QualityMeasurement]:
        """Measure one frame inside a copy of the caller's job context.

        Returns
        -------
        measurements : `list` [`QualityMeasurement`]
            The frame's rows.
        """
        frame, _file_state, capture_setting_id = item
        return caller_context.copy().run(session.measure, frame, target.id, capture_setting_id)

    try:
        with ThreadPoolExecutor(max_workers=MEASUREMENT_WORKER_COUNT) as pool:
            futures = {pool.submit(measure_in_job_context, item): item for item in pending}
            for done_count, future in enumerate(as_completed(futures), start=1):
                frame, file_state, _capture_setting_id = futures[future]
                failure: str | None = None
                try:
                    measurements = future.result()
                    if frame_was_unreadable(measurements):
                        failure = "the file could not be read"
                    else:
                        summary.rows_written += session.record(measurements)
                except (AstrometricsError, *FITS_READ_ERRORS, *DATA_ERRORS) as error:
                    failure = str(error)
                if failure is None:
                    _stamp_frame_as_measured(frame, file_state)
                    if frame.timestamp is not None:
                        night_ids.add(observing_night_id(frame.timestamp))
                    stamped_any = True
                    summary.frames_measured += 1
                else:
                    summary.frames_failed += 1
                    failure_notes.append(f"{os.path.basename(frame.path)}: {failure}")
                    logger.warning("Could not measure %s: %s", frame.path, failure)
                if job is not None:
                    job.stage(
                        int(100 * done_count / len(pending)),
                        f"Measured {done_count} of {len(pending)} frames",
                    )
    finally:
        if stamped_any and save_target is not None:
            save_target()

    summary.observing_night_ids = sorted(night_ids)
    summary.notes = failure_notes[:FAILURE_NOTES_LIMIT]
    if deferred_count:
        summary.notes.append(f"{deferred_count} frame(s) over the limit wait for the next run.")
    if len(failure_notes) > FAILURE_NOTES_LIMIT:
        summary.notes.append(f"{len(failure_notes) - FAILURE_NOTES_LIMIT} more frame(s) failed.")
    summary.seconds = time.monotonic() - started
    return summary


def record_stage_frame_quality(
    target: Target,
    kept_frames: Sequence[FrameRecord],
    quarantine_measurements: Sequence[dict[str, Any]],
    all_checked_frames: Sequence[FrameRecord],
    config: Any,
    measured_at: datetime,
) -> FrameMeasurementSummary:
    """Store what the stacking stage measured about a target's frames.

    The stacking stage calls this after the quarantine step. It records two
    things:

    1. The quarantine step's own numbers (star roundness and median pixel
       value) for every frame it measured, kept or moved. Today only the
       moved frames' numbers reach a file.
    2. The full frame measurement for kept frames whose file has not been
       measured yet, up to `STACKING_STAGE_MEASUREMENT_LIMIT` frames.

    The quarantine step reads each file into its own arrays and returns only
    numbers, so there are no loaded pixels to reuse. The full measurement
    therefore reads the frame again through the same per-frame path the job
    uses (`FrameMeasurementSession.measure_and_record`). Sharing the pixels
    would mean changing both readers, for a read that the file system cache
    usually still holds.

    Parameters
    ----------
    target : `Target`
        The target being stacked. Its kept frames are stamped in memory; the
        stage saves the target.
    kept_frames : `Sequence` [`FrameRecord`]
        The frames that stay in the stack.
    quarantine_measurements : `Sequence` [`dict`]
        `QuarantineReport.measurements`.
    all_checked_frames : `Sequence` [`FrameRecord`]
        Every frame the quarantine step looked at, moved ones included, to
        match the numbers to a frame by path.
    config : `AppConfiguration`
        The configuration, for the database and the calibration library.
    measured_at : `datetime`
        When the stack started, with a time zone, supplied by the caller.

    Returns
    -------
    summary : `FrameMeasurementSummary`
        The counts of the full frame measurements. ``rows_written`` also
        counts the quarantine rows.
    """
    from astrometricslib.drivers.calibration_library import CalibrationLibrary

    frames_by_path = {frame.path: frame for frame in all_checked_frames}
    needing = []
    for frame in kept_frames:
        file_state = _stat_or_none(frame.path)
        # A frame whose file is missing is left to the stage's own errors.
        if (
            file_state is not None
            and str(frame.role).upper() == "LIGHT"
            and frame_needs_measuring(frame, file_state)
        ):
            needing.append(frame)
    summary = FrameMeasurementSummary(target_id=target.id)
    if not quarantine_measurements and not needing:
        return summary
    session = FrameMeasurementSession(
        QualityStore.from_configuration(config),
        CalibrationLibrary(app_config=config),
        measured_at=measured_at,
    )
    for measurement in quarantine_measurements:
        frame = frames_by_path.get(measurement.get("path"))
        if frame is None:
            continue
        try:
            rows = quarantine_measurements_for_frame(
                measurement,
                frame,
                target.id,
                run_id=session.run_id,
                job_id=session.job_id,
                measured_at=session.measured_at,
                capture_setting_id=session.capture_setting_id(frame),
            )
            summary.rows_written += session.record(rows)
        except AstrometricsError as error:
            logger.warning("Could not store the quarantine measurements of %s: %s", frame.path, error)
    if needing:
        full_summary = record_frame_measurements_for_target(
            target, session, frames=needing, limit=STACKING_STAGE_MEASUREMENT_LIMIT
        )
        full_summary.rows_written += summary.rows_written
        return full_summary
    return summary
