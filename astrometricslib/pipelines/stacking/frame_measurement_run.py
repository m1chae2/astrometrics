"""Measures the light frames of a target once and stores the numbers.

`measure_frame` (in `pipelines/shared/quality/frame_measurement.py`) measures
one raw frame and writes nothing. This module is the glue around it, beside
`sensor_noise_run.py` and `calibration_ingest.py`, the other code that reads
the calibration library. It sits in the stacking folder and not under
`pipelines/shared/quality/` because it passes in the Gaussian star width from
the astrometry pipeline, and code under `shared/` may not import a pipeline.

What one run does, in order:

1. Take the run's turn (see "One run at a time" below).
2. List the target's light frames, newest first.
3. Skip a frame whose file has the size and last-change time it had when it
   was last measured (`FrameRecord.quality_measured_file_size` and
   `quality_measured_file_modified_ns`). Asking the file system for that
   stamp does not read the pixels, so a second run over an unchanged library
   costs almost nothing. Two kinds of
   frame are not skipped: one measured with an older set of frame metrics, and
   one whose stored sky rate says the camera profile had no gain and read
   noise entry while the profile now has one
   (`frames_whose_camera_profile_gap_closed`: one store query, no file read).
4. Find the camera profile, the capture setting row and the bias level for
   each remaining frame. The bias level is the sigma-clipped mean over a few
   bias frames taken at exactly the frame's gain setting, offset and binning.
   A nearby setting's bias is never used: a frame with no bias at its own
   setting gets its sky metrics ``not_measured`` with that reason.
5. Measure the frames in a few worker processes (the star fitting is mostly
   Python-level work, so threads of one process wait for each other's lock;
   see `MEASUREMENT_WORKER_COUNT` for the timings). Each worker reads and
   measures one frame at a time.
6. Store each frame's rows in one transaction through `record_measurements`,
   then stamp the frame. A frame whose rows could not be stored is not
   stamped, so the next run tries it again. So is a frame the CPU time budget
   cut short (`frame_was_cut_short`): the measured rows are stored, the
   skipped ones are not, and the next run measures the frame again. A frame
   with a lasting reason for a missing metric (no gain, no bias) is stamped.
   The stamps are saved to the stored target every
   `STAMP_SAVE_INTERVAL_FRAMES` frames and at the end, so an interrupted run
   keeps most of its progress.

The run never moves, changes or deletes a frame file. Any failure on one
frame (a read error, a storage error, or an unexpected error from the image
libraries) is logged and counted and does not stop the other frames.

One run at a time. Every caller (the RPC, the frame sync, the backend's
ingestion and the stacking stage) goes through
`record_frame_measurements_for_target`, which holds two guards:

- one run per target: a second caller for a target that is already being
  measured returns at once with a summary that says so
  (`FrameMeasurementSummary.already_running`);
- one run reading frames at a time in the process: a run for another target
  waits for its turn. The stacking stage never waits; it skips its
  measurement step when the turn is taken.

How long it takes. Measuring one 3008 x 3008 px frame takes about 13 to 15 s
of one core. With four worker processes a batch of those frames takes about
6 s per frame (four worker threads took 11 s per frame; 61 real frames took
about 650 s with threads). A sync that
adds frames waits that long per added frame before it returns.

The same module records the quarantine step's own numbers for every frame it
checked (`quarantine_measurements_for_frame`), so the stacking stage can keep
what it measured about the frames it did not move. That stacking hook runs
only for imaging stacks with the quarantine step turned on (spectral stacks
and stacks with `quarantine_bad_frames_enabled` off store none of these
rows); the explicit run and the sync do not depend on it.
"""

import contextvars
import logging
import math
import multiprocessing
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Generator, Mapping, Sequence
from concurrent.futures import Executor, Future, ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import numpy as np

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.foundation.jobs.runner import JobHandle, get_current_job
from astrometricslib.models.camera_profile import UNMIGRATED_NOISE_REASON, CameraProfile
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.frame_measurement_summary import FrameMeasurementSummary
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.frame_scanning import file_state_matches
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    TIME_BUDGET_REASON_PREFIX,
    UNREADABLE_FRAME_REASON_PREFIX,
    frame_metric_set_fingerprint,
    frame_subject,
    frame_subject_id,
    measure_clipped_mean_adu,
    measure_frame,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_SKY_RATE_E_PER_S_PER_PX,
)
from astrometricslib.pipelines.shared.quality.record_measurements import record_measurements
from astrometricslib.pipelines.stacking.exact_bias_frames import exact_setting_bias_frames
from astrometricslib.pipelines.stacking.metrics import FRAME_QUARANTINE_ROUNDNESS, FRAME_QUARANTINE_SKY_MEDIAN
from astrometricslib.pipelines.stacking.processing.exposure_groups import CLIPPED_FRAME_ZERO_FRACTION
from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.observing_night import observing_night_id

if TYPE_CHECKING:
    from astrometricslib.drivers.calibration_library import CalibrationLibrary
    from astrometricslib.foundation.config import AppConfiguration

logger = logging.getLogger(__name__)

__all__ = [
    "BIAS_FRAMES_FOR_LEVEL",
    "MEASUREMENT_WORKER_COUNT",
    "MEASUREMENT_WORKER_NICENESS",
    "STACKING_STAGE_MEASUREMENT_LIMIT",
    "STAMP_SAVE_INTERVAL_FRAMES",
    "BiasLevelFinder",
    "FrameMeasurementSession",
    "FrameMeasurementTask",
    "FrameQualityRecording",
    "build_capture_setting",
    "frame_needs_measuring",
    "frame_was_cut_short",
    "frames_whose_camera_profile_gap_closed",
    "measurements_to_store",
    "quarantine_measurements_for_frame",
    "record_frame_measurements_for_target",
    "record_stage_frame_quality",
    "run_measurement_task",
]

# How many frames are measured at the same time, each in a worker process of
# its own. The fitting is mostly Python-level work (SciPy calls many small
# functions), so Python's global lock lets only one thread of a process run it
# at a time. Timed on this 12-core machine on the two golden 3008 x 3008 px
# M 13 frames (8 frames per row, 2026-10-22):
#
#   workers   threads: s per frame   processes: s per frame
#      1            14.1                  (not run)
#      2            10.6                    8.8
#      4            11.2                    5.9
#
# Threads gain 1.3 times at two workers and nothing more at four, while the
# CPU time of each frame rises from 13 to 25 s as the threads fight for the
# lock. Four processes gain 2.4 times over one thread (each frame's CPU time
# rises to 20 to 23 s, the cores share memory bandwidth). Four is also the
# count the quarantine step uses for its reads (`MEASUREMENT_THREADS`) and
# leaves most of the cores to the app and the backend, so the interface stays
# responsive during a sync. It is a courtesy limit, not a tuned optimum. Each
# worker holds one frame at a time (an 18 MB float32 frame and a few copies),
# so memory stays below about 1 GB for the pool. Reading is unchanged
# (`open_primary_hdu`, no memory map). Register row STK-D22 holds the table.
MEASUREMENT_WORKER_COUNT = 4

# The niceness added to a measurement worker process so that the app and the
# backend keep the cores they need. A small positive number: the workers still
# get most of the idle time. It is a courtesy, not a tuned value.
MEASUREMENT_WORKER_NICENESS = 5

# How many bias frames are read to find the bias level of one setting. The
# level of one frame is the clipped mean of about nine million pixels, so its
# random error is the read noise divided by the square root of that count, far
# below 0.01 ADU. A median over up to five frames guards against one odd frame
# (a cosmic-ray hit or a half-written file) rather than against noise. Five
# reads cost a few seconds and are made once per setting per run.
BIAS_FRAMES_FOR_LEVEL = 5

# The most frames the stacking stage measures in full during one stack. The
# job after a sync normally measures every frame long before it is stacked, so
# the stage only picks up frames that were never measured (an older library, or
# a sync that failed). Measured on real data, a batch costs about 11 s per
# frame (see `MEASUREMENT_WORKER_COUNT`), so 40 frames add about seven minutes
# to a stack. That is long next to a stack of a few minutes; the limit is a
# cap on how much one stack can be delayed, not a tuned value. The frames that
# are left wait for the next stack or the next call of
# `ProcessingPipelines.record_frame_measurements`.
STACKING_STAGE_MEASUREMENT_LIMIT = 40

# How many newly measured frames are collected before the frames' stamps are
# saved to the stored target. A run of 60 frames takes about 11 minutes, so
# saving every 20 frames (about every 3 to 4 minutes) means an interrupted run
# loses at most 20 stamps, which cost only a repeat of those frames' work. Each
# save writes one target record, a small cost next to 20 frames of pixel work.
# It is a design choice, not a tuned value.
STAMP_SAVE_INTERVAL_FRAMES = 20

# How many failed frames a run describes by name in its notes. The count is
# always complete; the names are only a pointer for the reader of the log.
FAILURE_NOTES_LIMIT = 5

# The library-wide turn: at most one measurement run reads frames at a time in
# this process. Each run already measures `MEASUREMENT_WORKER_COUNT` frames at
# once, so a second run at the same time would only make both slower and slow
# the app. The sync, the backend's ingestion, the RPC and the stacking stage
# all reach the run through `record_frame_measurements_for_target`, so this
# lock serves them all.
_RUN_TURN = threading.Lock()

# The targets with a run under way or waiting for its turn, and the lock that
# guards the set.
_ACTIVE_TARGET_IDS: set[str] = set()
_ACTIVE_TARGETS_LOCK = threading.Lock()


@contextmanager
def _measurement_turn(target_id: str, *, wait_for_turn: bool) -> Generator[str | None]:
    """Take the per-target guard and the library-wide turn for one run.

    Parameters
    ----------
    target_id : `str`
        The target the run measures.
    wait_for_turn : `bool`
        `True` waits for the library-wide turn when another target is being
        measured. `False` skips the run at once instead.

    Yields
    ------
    refusal : `str` or `None`
        `None` when the run may go ahead (the guards are held until the block
        ends). Otherwise a sentence saying why the run must be skipped.
    """
    with _ACTIVE_TARGETS_LOCK:
        if target_id in _ACTIVE_TARGET_IDS:
            refusal = f"a measurement run for {target_id} is already going"
        else:
            refusal = None
            _ACTIVE_TARGET_IDS.add(target_id)
    if refusal is not None:
        yield refusal
        return
    try:
        if not _RUN_TURN.acquire(blocking=wait_for_turn):
            yield "another measurement run is reading frames, so this one was skipped"
            return
        try:
            yield None
        finally:
            _RUN_TURN.release()
    finally:
        with _ACTIVE_TARGETS_LOCK:
            _ACTIVE_TARGET_IDS.discard(target_id)


@dataclass(frozen=True)
class FrameQualityRecording:
    """What the stacking stage needs to store frame quality.

    The stage does not read the global configuration or build a calibration
    library for this: the caller that already holds them passes them in, so
    every stack shares the one library and tests can pass their own.

    Attributes
    ----------
    configuration : `AppConfiguration`
        The configuration that locates the quality database.
    calibration_library : `CalibrationLibrary`
        The shared calibration library, for the bias levels.
    """

    configuration: AppConfiguration
    calibration_library: CalibrationLibrary


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
        `True` unless the frame has a stamp, the file still matches it (by the
        same rule the header scan uses, `file_state_matches`) and the frame
        was measured with the current set of frame metrics
        (`frame_metric_set_fingerprint`). A frame never measured has no stamp,
        so it needs measuring. A frame measured before a metric was added or
        redefined, or before the set was recorded, is measured again.
    """
    if frame.quality_measured_metric_set != frame_metric_set_fingerprint():
        return True
    return not file_state_matches(
        file_state, frame.quality_measured_file_size, frame.quality_measured_file_modified_ns
    )


# The start of the reason text that `CameraProfile.sensor_noise_gap_reason`
# gives when the profile holds no gain and read noise entry for a frame's
# setting. A stored ``not_measured`` sky rate with this start is a profile gap.
_PROFILE_GAP_REASON_PREFIX = "camera profile has no gain and read noise entry"

# Text in a profile gap reason that marks a gap which a profile edit cannot
# close: the frame itself is cut off at zero, so no entry of another offset may
# stand in for it.
_FRAME_CLIPPED_REASON_TEXT = "is clipped at zero"


def frames_whose_camera_profile_gap_closed(
    store: QualityStore,
    target_id: str,
    frames: Sequence[FrameRecord],
    find_camera_profile: Callable[[FrameRecord], CameraProfile],
) -> set[str]:
    """Find frames whose sky lacked only a camera profile entry.

    A frame's file and metric set can be unchanged while its camera profile
    changes (a gain and read noise entry is added, or the offset fallback is
    allowed). The frame's stored sky rate then still says "no entry", and the
    stamp would keep the frame from being measured again. This finds those
    frames with one query of the quality store, and reads no frame file.

    Parameters
    ----------
    store : `QualityStore`
        The quality store holding the frames' rows.
    target_id : `str`
        The target the frames belong to.
    frames : `Sequence` [`FrameRecord`]
        The frames to check.
    find_camera_profile : `Callable`
        Gives the camera profile now in force for a frame.

    Returns
    -------
    paths : `set` [`str`]
        The paths of the frames whose latest sky rate row is ``not_measured``
        for a missing profile entry, and whose gain setting, offset and
        binning the current profile now resolves. The offset fallback is
        assumed allowed, because the pixels are not read here. A frame that
        was found clipped at zero is left out, so it is not measured again
        on every run. Empty when no frame qualifies.
    """
    if not frames:
        return set()
    rows = store.query(
        metric=FRAME_SKY_RATE_E_PER_S_PER_PX,
        level=SubjectLevel.FRAME,
        target_id=target_id,
        latest_only=True,
    )
    gap_subjects = {
        row.subject.subject_id
        for row in rows
        if row.status == MeasurementStatus.NOT_MEASURED
        and (row.reason.startswith(_PROFILE_GAP_REASON_PREFIX) or row.reason == UNMIGRATED_NOISE_REASON)
        and _FRAME_CLIPPED_REASON_TEXT not in row.reason
    }
    closed: set[str] = set()
    for frame in frames:
        if frame_subject_id(frame.path, target_id) not in gap_subjects:
            continue
        profile = find_camera_profile(frame)
        if (
            profile.sensor_noise_for(frame.iso, frame.offset, frame.binning, clipped_at_zero=False)
            is not None
        ):
            closed.add(frame.path)
    return closed


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
    frame.quality_measured_metric_set = frame_metric_set_fingerprint()


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


def frame_was_cut_short(measurements: Sequence[QualityMeasurement]) -> bool:
    """Tell whether the CPU time budget cut a frame's measurement short.

    Parameters
    ----------
    measurements : `Sequence` [`QualityMeasurement`]
        The result of `measure_frame`.

    Returns
    -------
    cut_short : `bool`
        `True` when any metric is ``not_measured`` because the frame's CPU
        time budget ran out. Another try may succeed (on a quieter machine, or
        with a bigger budget), so the frame is not stamped as measured. Other
        ``not_measured`` reasons (no gain entry, no bias frames, no pixel
        scale, no star found) are permanent: a retry gives the same answer, so
        the frame is stamped.
    """
    return any(
        row.status == MeasurementStatus.NOT_MEASURED and row.reason.startswith(TIME_BUDGET_REASON_PREFIX)
        for row in measurements
    )


def measurements_to_store(measurements: Sequence[QualityMeasurement]) -> list[QualityMeasurement]:
    """Choose the rows of one frame to write.

    Parameters
    ----------
    measurements : `Sequence` [`QualityMeasurement`]
        The result of `measure_frame`.

    Returns
    -------
    rows : `list` [`QualityMeasurement`]
        All the rows, except that a frame cut short by the time budget leaves
        out the rows that the budget skipped. Such a row only says "run out of
        time", which is not a finding about the frame, and the next run
        measures the frame again and writes a real row. The rows that were
        measured are kept, so a second run adds a second row for each of them
        (the unique key includes the run id). That is deliberate: each run is
        a complete record of one measurement, the latest row wins in every
        read, and finding the earlier rows would mean guessing whether the
        file changed in between. The duplicates are bounded by the number of
        runs and cost a few small rows.
    """
    if not frame_was_cut_short(measurements):
        return list(measurements)
    return [
        row
        for row in measurements
        if not (
            row.status == MeasurementStatus.NOT_MEASURED and row.reason.startswith(TIME_BUDGET_REASON_PREFIX)
        )
    ]


@dataclass(frozen=True)
class FrameMeasurementTask:
    """Everything a worker needs to measure one frame, in plain data.

    The task holds no open file, lock or store, so it can be sent to a worker
    process. The worker reads the pixels itself.

    Attributes
    ----------
    frame : `FrameRecord`
        The frame (its path names the file).
    target_id : `str`
        The target the frame belongs to.
    camera_profile : `CameraProfile`
        The camera's profile, resolved by the caller.
    run_id : `str`
        The run the rows belong to.
    job_id : `str` or `None`
        The job, if any.
    measured_at : `datetime`
        When the run measures, with a time zone.
    capture_setting_id : `int`
        The frame's capture setting row.
    bias_level_adu : `float` or `None`
        The bias level at the frame's setting, or `None` when unknown.
    bias_level_reason : `str`
        Why the bias level is `None`, or an empty string.
    """

    frame: FrameRecord
    target_id: str
    camera_profile: CameraProfile
    run_id: str
    job_id: str | None
    measured_at: datetime
    capture_setting_id: int
    bias_level_adu: float | None
    bias_level_reason: str


def run_measurement_task(task: FrameMeasurementTask) -> list[QualityMeasurement]:
    """Read one frame's pixels and measure it. Nothing is written.

    This is a plain function at the top of the module so that a worker
    process can import and run it.

    Parameters
    ----------
    task : `FrameMeasurementTask`
        The frame and everything its measurement needs.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One per frame metric (see `measure_frame`).
    """
    # Imported here: the astrometry pipeline is a peer of this one, and
    # only the width function is needed.
    from astrometricslib.pipelines.astrometry.pre_processing.fwhm import measure_fwhm_from_data

    return measure_frame(
        task.frame.path,
        task.frame,
        task.target_id,
        task.camera_profile,
        run_id=task.run_id,
        job_id=task.job_id,
        measured_at=task.measured_at,
        capture_setting_id=task.capture_setting_id,
        bias_level_adu=task.bias_level_adu,
        bias_level_reason=task.bias_level_reason,
        measure_gaussian_width=measure_fwhm_from_data,
        clipped_zero_fraction_limit=CLIPPED_FRAME_ZERO_FRACTION,
    )


def _initialize_measurement_worker() -> None:
    """Lower a worker process's priority so the app keeps its cores."""
    try:
        os.nice(MEASUREMENT_WORKER_NICENESS)
    except AttributeError, OSError:
        # No os.nice on Windows, or not allowed: the priority stays as it is.
        pass


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

    def __init__(self, library: CalibrationLibrary) -> None:
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
            exactly this setting, of each frame's sigma-clipped mean (the same
            estimator as the sky background of the electron metrics, see
            `measure_clipped_mean_adu`). `None` when the library holds no bias
            frames at exactly this setting or none can be read.
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
            # A bias frame of a nearby setting would give a wrong level, so
            # only an exact match counts (see `exact_setting_bias_frames`).
            bias_frames = exact_setting_bias_frames(self._library, camera, gain, offset, binning)
        except (AstrometricsError, *DATA_ERRORS) as error:
            return None, f"the calibration library could not list bias frames for {camera}: {error}"
        if not bias_frames.paths:
            return None, bias_frames.reason
        levels = []
        for path in sorted(bias_frames.paths)[:BIAS_FRAMES_FOR_LEVEL]:
            try:
                with open_primary_hdu(path) as hdu:
                    # The clipped mean, not the plain mean: hot pixels pull a
                    # plain mean high (the bias level is the same estimator
                    # as the sky it is subtracted from).
                    levels.append(
                        measure_clipped_mean_adu(collapse_to_2d(np.asarray(hdu.data, dtype=np.float64)))
                    )
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
        The id that groups this run's rows. When omitted it is the id of
        `progress_job` if the run has its own job, otherwise a new unique id.
        It is never the id of a parent job that merely contains the run.
    job_id : `str` or `None`, optional
        The job's id, kept on each row as a soft link to the job record. The
        id of `progress_job` when omitted, else the id of the job the run is
        nested in (if any).
    progress_job : `JobHandle` or `None`, optional
        The job that this run registered for itself. Only this job gets the
        run's progress. A run nested in a parent job (stacking, ingestion)
        passes `None`, so it never takes over the parent's progress.
    measured_at : `datetime`
        When the run measures, with a time zone. The caller supplies it:
        code in a pipeline never reads the clock.
    use_worker_processes : `bool`, optional
        `True` (default) measures frames in worker processes (see
        `MEASUREMENT_WORKER_COUNT` for why). `False` measures in worker
        threads of this process, which tests use to replace `measure`.
    """

    def __init__(
        self,
        store: QualityStore,
        library: CalibrationLibrary,
        *,
        run_id: str | None = None,
        job_id: str | None = None,
        progress_job: JobHandle | None = None,
        measured_at: datetime,
        use_worker_processes: bool = True,
    ) -> None:
        own_job_id = progress_job.job_id if progress_job is not None else None
        enclosing_job = get_current_job()
        self.store = store
        self.bias_levels = BiasLevelFinder(library)
        self.progress_job = progress_job if own_job_id is not None else None
        self.job_id = job_id or own_job_id or (enclosing_job.job_id if enclosing_job is not None else None)
        self.run_id = run_id or own_job_id or f"frame-measurement-{uuid.uuid4().hex}"
        self.measured_at = measured_at
        self.use_worker_processes = use_worker_processes
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
        return run_measurement_task(self.prepare_task(frame, target_id, capture_setting_id))

    def prepare_task(
        self, frame: FrameRecord, target_id: str, capture_setting_id: int
    ) -> FrameMeasurementTask:
        """Gather what a worker needs to measure one frame.

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
        task : `FrameMeasurementTask`
            The frame with its camera profile and bias level. Looking up a
            setting's bias level reads a few bias frames the first time, so
            call this from the run's own thread, not from a worker.
        """
        bias_level_adu, bias_reason = self.bias_levels.find(frame)
        return FrameMeasurementTask(
            frame=frame,
            target_id=target_id,
            camera_profile=self.camera_profile(frame),
            run_id=self.run_id,
            job_id=self.job_id,
            measured_at=self.measured_at,
            capture_setting_id=capture_setting_id,
            bias_level_adu=bias_level_adu,
            bias_level_reason=bias_reason,
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


def quarantine_measurements_for_frame(
    measurement: dict[str, Any],
    frame: FrameRecord,
    target_id: str,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    capture_setting_id: int | None,
    action_taken: str | None = None,
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
    action_taken : `str` or `None`, optional
        What the step did with the frame, such as the text of
        `set_aside_action_text` when it moved the frame. A processing
        decision is stored as its measurement plus the action taken, so both
        rows carry it. `None` for a frame that was kept.

    Returns
    -------
    measurements : `list` [`QualityMeasurement`]
        One roundness row and one sky median row. The roundness row is
        ``not_measured`` with a reason when the frame had no measurable star.
    """
    subject = frame_subject(frame.path, target_id, frame.timestamp, capture_setting_id=capture_setting_id)
    common = {
        "definition_version": 1,
        "subject": subject,
        "origin": MeasurementOrigin.MEASURED,
        "run_id": run_id,
        "job_id": job_id,
        "measured_at": measured_at,
        "action_taken": action_taken,
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


def _save_stamps(save_target: Callable[[], None] | None, notes: list[str]) -> None:
    """Save the frames' stamps, never letting a failure end the run.

    Parameters
    ----------
    save_target : `Callable` or `None`
        The caller's save function, or `None` when it keeps no stamps.
    notes : `list` [`str`]
        The run's notes. A failure adds one sentence the first time.
    """
    if save_target is None:
        return
    try:
        save_target()
    except (AstrometricsError, sqlite3.Error, OSError) as error:
        logger.warning("Could not save the measurement stamps of the frames: %s", error)
        note = f"the measurement stamps could not be saved ({error}); the frames may be measured again"
        if note not in notes:
            notes.append(note)


def record_frame_measurements_for_target(
    target: Target,
    session: FrameMeasurementSession,
    *,
    frames: Sequence[FrameRecord] | None = None,
    limit: int | None = None,
    save_target: Callable[[], None] | None = None,
    wait_for_turn: bool = True,
) -> FrameMeasurementSummary:
    """Measure the light frames that need it and store the rows.

    Every caller reaches the measurement through this function, so the guards
    are here: only one run per target, and only one run in the process reading
    frames at a time (see the module docstring).

    Parameters
    ----------
    target : `Target`
        The target. Its frames are stamped in memory as they are measured.
    session : `FrameMeasurementSession`
        The store, bias finder and run identity for this run.
    frames : `Sequence` [`FrameRecord`], optional
        Only these frames (light frames among them are used). Every light
        frame of the target when omitted. The sync and the ingestion pass the
        frames they added, so an old, never-measured frame is not swept in.
    limit : `int`, optional
        Measure at most this many frames, newest first. The rest wait for the
        next run.
    save_target : `Callable`, optional
        Called every `STAMP_SAVE_INTERVAL_FRAMES` newly stamped frames and
        once at the end, when any frame was stamped, to save the target's
        stamps. It is called even if the run was interrupted by an error. It
        should save only the stamps (see
        `target_records.save_quality_stamps`), so that it never overwrites
        other changes to the stored target. A failure of the save is logged
        and noted, not raised.
    wait_for_turn : `bool`, optional
        `True` (default) waits when another target's run is reading frames.
        `False` returns at once instead; the stacking stage uses it.

    Returns
    -------
    summary : `FrameMeasurementSummary`
        The counts and notes of the run. When the run was skipped because the
        target is already being measured (or, with ``wait_for_turn=False``,
        because another run has the turn), ``already_running`` is `True`, the
        counts are zero, and a note says why.
    """
    with _measurement_turn(target.id, wait_for_turn=wait_for_turn) as refusal:
        if refusal is not None:
            return FrameMeasurementSummary(target_id=target.id, already_running=True, notes=[refusal])
        return _measure_target_frames(target, session, frames, limit, save_target)


def _measure_target_frames(
    target: Target,
    session: FrameMeasurementSession,
    frames: Sequence[FrameRecord] | None,
    limit: int | None,
    save_target: Callable[[], None] | None,
) -> FrameMeasurementSummary:
    """Measure the frames that need it, with the run's turn already held.

    Parameters
    ----------
    target : `Target`
        The target.
    session : `FrameMeasurementSession`
        The store, bias finder and run identity.
    frames : `Sequence` [`FrameRecord`] or `None`
        Only these frames, or every light frame when `None`.
    limit : `int` or `None`
        The most frames to measure.
    save_target : `Callable` or `None`
        Saves the stamps (see `record_frame_measurements_for_target`).

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

    # One store query finds frames a profile edit made measurable.
    profile_gap_closed = frames_whose_camera_profile_gap_closed(
        session.store, target.id, lights, session.camera_profile
    )
    pending: list[tuple[FrameRecord, os.stat_result, int]] = []
    failure_notes: list[str] = []
    cut_short_notes: list[str] = []
    deferred_count = 0
    for frame in lights:
        file_state = _stat_or_none(frame.path)
        if file_state is None:
            summary.frames_failed += 1
            failure_notes.append(f"{os.path.basename(frame.path)}: the file cannot be read")
            continue
        if not frame_needs_measuring(frame, file_state) and frame.path not in profile_gap_closed:
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

    # Progress goes only to a job this run registered for itself. A run nested
    # in a parent job (stacking, ingestion) leaves the parent's progress alone.
    progress_job = session.progress_job
    caller_context = contextvars.copy_context()
    unsaved_stamp_count = 0
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

    pool: Executor
    submit: Callable[[tuple[FrameRecord, os.stat_result, int]], Future[list[QualityMeasurement]]]
    if session.use_worker_processes and len(pending) > 1:
        # "spawn", never "fork": a forked child of the backend would inherit
        # the state of its network and database libraries (the same reason as
        # in `utilities/parallel_batch.py`).
        pool = ProcessPoolExecutor(
            max_workers=min(MEASUREMENT_WORKER_COUNT, len(pending)),
            mp_context=multiprocessing.get_context("spawn"),
            initializer=_initialize_measurement_worker,
        )

        def submit(item: tuple[FrameRecord, os.stat_result, int]) -> Future[list[QualityMeasurement]]:
            """Send one frame to a worker process.

            Returns
            -------
            future : `Future`
                The frame's rows, when the worker finishes.
            """
            frame, _file_state, capture_setting_id = item
            return pool.submit(
                run_measurement_task, session.prepare_task(frame, target.id, capture_setting_id)
            )

    else:
        pool = ThreadPoolExecutor(max_workers=MEASUREMENT_WORKER_COUNT)

        def submit(item: tuple[FrameRecord, os.stat_result, int]) -> Future[list[QualityMeasurement]]:
            """Send one frame to a worker thread.

            Returns
            -------
            future : `Future`
                The frame's rows, when the worker finishes.
            """
            return pool.submit(measure_in_job_context, item)

    try:
        with pool:
            futures = {submit(item): item for item in pending}
            for done_count, future in enumerate(as_completed(futures), start=1):
                frame, file_state, _capture_setting_id = futures[future]
                failure: str | None = None
                cut_short = False
                try:
                    measurements = future.result()
                    if frame_was_unreadable(measurements):
                        failure = "the file could not be read"
                    else:
                        summary.rows_written += session.record(measurements_to_store(measurements))
                        cut_short = frame_was_cut_short(measurements)
                except Exception as error:  # one frame's failure, whatever its cause, must not end the run
                    # The image libraries (photutils, SciPy) can raise errors
                    # outside the named read and data errors, such as a
                    # RuntimeError or a MemoryError. They cost this frame only.
                    logger.exception("Measuring %s failed", frame.path)
                    failure = f"{type(error).__name__}: {error}"
                if failure is None and cut_short:
                    # Not stamped: the next run measures the frame again.
                    summary.frames_cut_short += 1
                    cut_short_notes.append(os.path.basename(frame.path))
                elif failure is None:
                    _stamp_frame_as_measured(frame, file_state)
                    if frame.timestamp is not None:
                        night_ids.add(observing_night_id(frame.timestamp))
                    unsaved_stamp_count += 1
                    summary.frames_measured += 1
                    if unsaved_stamp_count >= STAMP_SAVE_INTERVAL_FRAMES:
                        _save_stamps(save_target, failure_notes)
                        unsaved_stamp_count = 0
                else:
                    summary.frames_failed += 1
                    failure_notes.append(f"{os.path.basename(frame.path)}: {failure}")
                    logger.warning("Could not measure %s: %s", frame.path, failure)
                if progress_job is not None:
                    progress_job.stage(
                        int(100 * done_count / len(pending)),
                        f"Measured {done_count} of {len(pending)} frames",
                    )
    finally:
        if unsaved_stamp_count:
            _save_stamps(save_target, failure_notes)

    summary.observing_night_ids = sorted(night_ids)
    summary.notes = failure_notes[:FAILURE_NOTES_LIMIT]
    if cut_short_notes:
        summary.notes.append(
            f"{len(cut_short_notes)} frame(s) ran out of CPU time and will be measured again on the next run "
            f"(first: {cut_short_notes[0]})."
        )
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
    recording: FrameQualityRecording,
    measured_at: datetime,
    save_target: Callable[[], None] | None = None,
    actions_by_path: Mapping[str, str] | None = None,
) -> FrameMeasurementSummary:
    """Store what the stacking stage measured about a target's frames.

    The stacking stage calls this after the quarantine step, which runs only
    for imaging stacks with the quarantine turned on. It records two things:

    1. The quarantine step's own numbers (star roundness and median pixel
       value) for every frame it measured, kept or moved. Today only the
       moved frames' numbers reach a file.
    2. The full frame measurement for kept frames whose file has not been
       measured yet, up to `STACKING_STAGE_MEASUREMENT_LIMIT` frames, through
       `record_frame_measurements_for_target` (the same path as every other
       caller). The stage never waits for it: when a run for the target is
       already going, or another run has the library's turn, this step is
       skipped, and a note in the summary says so.

    The quarantine step reads each file into its own arrays and returns only
    numbers, so there are no loaded pixels to reuse. The full measurement
    therefore reads the frame again. Sharing the pixels would mean changing
    both readers, for a read that the file system cache usually still holds.

    Parameters
    ----------
    target : `Target`
        The target being stacked. Its kept frames are stamped in memory.
    kept_frames : `Sequence` [`FrameRecord`]
        The frames that stay in the stack.
    quarantine_measurements : `Sequence` [`dict`]
        `QuarantineReport.measurements`.
    all_checked_frames : `Sequence` [`FrameRecord`]
        Every frame the quarantine step looked at, moved ones included, to
        match the numbers to a frame by path.
    recording : `FrameQualityRecording`
        The configuration and the shared calibration library, given by the
        caller of the stage.
    measured_at : `datetime`
        When the stack started, with a time zone, supplied by the caller.
    save_target : `Callable`, optional
        Saves the frames' stamps (see `record_frame_measurements_for_target`).
        The stage saves the whole target itself later when no function is
        given.
    actions_by_path : `Mapping` [`str`, `str`], optional
        For each frame the quarantine step moved (by its original path), the
        ``action_taken`` text stored with its quarantine rows
        (`QuarantineReport.actions_by_path`). Frames not listed get none.

    Returns
    -------
    summary : `FrameMeasurementSummary`
        The counts of the full frame measurements. ``rows_written`` also
        counts the quarantine rows.
    """
    frames_by_path = {frame.path: frame for frame in all_checked_frames}
    needing = []
    kept_lights = [frame for frame in kept_frames if str(frame.role).upper() == "LIGHT"]
    profile_gap_closed = frames_whose_camera_profile_gap_closed(
        QualityStore.from_configuration(recording.configuration),
        target.id,
        kept_lights,
        lambda frame: resolve_camera_profile(frame.camera),
    )
    for frame in kept_lights:
        file_state = _stat_or_none(frame.path)
        # A frame whose file is missing is left to the stage's own errors.
        if file_state is not None and (
            frame_needs_measuring(frame, file_state) or frame.path in profile_gap_closed
        ):
            needing.append(frame)
    summary = FrameMeasurementSummary(target_id=target.id)
    if not quarantine_measurements and not needing:
        return summary
    session = FrameMeasurementSession(
        QualityStore.from_configuration(recording.configuration),
        recording.calibration_library,
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
                action_taken=(actions_by_path or {}).get(frame.path),
            )
            summary.rows_written += session.record(rows)
        except AstrometricsError as error:
            logger.warning("Could not store the quarantine measurements of %s: %s", frame.path, error)
    if needing:
        full_summary = record_frame_measurements_for_target(
            target,
            session,
            frames=needing,
            limit=STACKING_STAGE_MEASUREMENT_LIMIT,
            save_target=save_target,
            wait_for_turn=False,
        )
        full_summary.rows_written += summary.rows_written
        return full_summary
    return summary
