"""Measure a folder of raw frames with the `frame.*` metrics, storing nothing.

`record_frame_measurements` measures a library target's frames and stores the
numbers. This module does the same measurement for any folder of FITS files
and returns the numbers instead: for checking a staging folder before frames
enter the library, or a set of frames drawn by a camera simulator. Each frame
goes through `measure_frame`, the function that serves the stored path, so the
numbers agree.

Frames are measured one after another in this process. A full-size frame takes
about 12 s of one core.
"""

from __future__ import annotations

import logging
import os
import statistics
from collections.abc import Sequence
from datetime import datetime

from astrometricslib._internal.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.quality.folder_measurement import FolderFrameMeasurement, FolderMeasurementReport
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_scanning import create_frame_record_from_fits
from astrometricslib.pipelines.shared.quality import frame_selection
from astrometricslib.pipelines.shared.quality.frame_notices import FRAME_NOTICE_METRICS
from astrometricslib.pipelines.stacking.frame_measurement_run import (
    BiasLevelFinder,
    FrameMeasurementTask,
    run_measurement_task,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = ["measure_folder_frames"]

_NOTICE_METRIC_NAMES = frozenset(spec.name for spec in FRAME_NOTICE_METRICS)
"""The names of the metrics that carry a warning, not a measurement."""

_UNSTORED_RUN_ID_PREFIX = "folder-measurement"
"""The start of the run id given to unstored rows; it never reaches a store."""

_NO_CAPTURE_SETTING_ID = 0
"""The capture-setting row of a frame measured without a store: none."""


def _metric_key(row: QualityMeasurement) -> str:
    """Name a row's entry in a frame's value table.

    Returns
    -------
    key : `str`
        The metric name, with ``[plane]`` after it for a color plane.
    """
    channel = row.subject.channel
    return row.metric if channel is None else f"{row.metric}[{channel}]"


def _frame_result(
    frame: FrameRecord, rows: Sequence[QualityMeasurement]
) -> tuple[FolderFrameMeasurement, set[str]]:
    """Reduce one frame's rows to the values the report carries.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.
    rows : `Sequence` [`QualityMeasurement`]
        The rows `measure_frame` returned.

    Returns
    -------
    result, notices : `tuple`
        The frame's values, and the reasons of any notice rows.
    """
    try:
        exposure = float(frame.exposure)
    except ValueError:
        exposure = None
    result = FolderFrameMeasurement(
        file_name=os.path.basename(frame.path), exposure_seconds=exposure, timestamp=frame.timestamp
    )
    notices: set[str] = set()
    for row in rows:
        if row.metric in _NOTICE_METRIC_NAMES:
            notices.add(row.reason)
            continue
        key = _metric_key(row)
        if row.status == MeasurementStatus.NOT_MEASURED or row.value is None:
            result.not_measured[key] = row.reason
            continue
        result.values[key] = float(row.value)
        if row.uncertainty is not None:
            result.uncertainties[key] = float(row.uncertainty)
    return result, notices


def measure_folder_frames(
    folder_path: str,
    selection: frame_selection.FrameSelection,
    limit: int,
    config: AppConfiguration,
    measured_at: datetime,
    *,
    camera: str | None = None,
    bias_level_adu: float | None = None,
    bias_finder: BiasLevelFinder | None = None,
) -> FolderMeasurementReport:
    """Measure the frames of a folder and return the numbers.

    Parameters
    ----------
    folder_path : `str`
        A folder of ``*.fits`` files. It need not be in the library.
    selection : `FrameSelection`
        Which files to keep (filter, file range, time window).
    limit : `int`
        The most frames to measure; the first ones in file-name order.
    config : `AppConfiguration`
        The settings, for the camera and the optic a frame was taken with.
    measured_at : `datetime`
        When the measurement is made, with a time zone. The caller supplies
        it: code in a pipeline never reads the clock.
    camera : `str`, optional
        Treat every frame as taken with this camera (a name in the camera
        profiles). The header's camera is used when omitted.
    bias_level_adu : `float`, optional
        The bias level for the sky metrics, in ADU, for every frame. Without
        it the level is read from the calibration library's bias frames at
        each frame's setting (`bias_finder`), and the sky metrics are not
        measured when there are none.
    bias_finder : `BiasLevelFinder`, optional
        Finds the library's bias level for a setting.

    Returns
    -------
    report : `FolderMeasurementReport`
        The values of each frame and their medians. Nothing is stored.
    """
    paths = frame_selection.select_folder_paths(folder_path, selection)
    report = FolderMeasurementReport(
        folder_path=folder_path, frames_matching=len(paths), bias_level_adu=bias_level_adu
    )
    run_id = f"{_UNSTORED_RUN_ID_PREFIX}-{measured_at:%Y%m%dT%H%M%S}"
    notices: set[str] = set()
    bias_reasons: set[str] = set()
    for path in paths[:limit]:
        try:
            frame = create_frame_record_from_fits(path, camera=camera, config=config)
            if bias_level_adu is not None:
                level, reason = bias_level_adu, ""
            elif bias_finder is not None:
                level, reason = bias_finder.find(frame)
            else:
                level, reason = None, "no bias level was given and no calibration library is available"
            rows = run_measurement_task(
                FrameMeasurementTask(
                    frame=frame,
                    target_id=os.path.basename(os.path.normpath(folder_path)),
                    camera_profile=resolve_camera_profile(frame.camera),
                    run_id=run_id,
                    job_id=None,
                    measured_at=measured_at,
                    capture_setting_id=_NO_CAPTURE_SETTING_ID,
                    bias_level_adu=level,
                    bias_level_reason=reason,
                )
            )
        except (AstrometricsError, OSError, *DATA_ERRORS) as error:
            logger.warning("Could not measure %s: %s", path, error)
            report.notes.append(f"{os.path.basename(path)}: {error}")
            continue
        if level is None and reason:
            bias_reasons.add(reason)
        result, frame_notices = _frame_result(frame, rows)
        report.frames.append(result)
        notices |= frame_notices
    report.frames_measured = len(report.frames)
    by_metric: dict[str, list[float]] = {}
    for result in report.frames:
        for key, value in result.values.items():
            by_metric.setdefault(key, []).append(value)
    report.medians = {key: statistics.median(values) for key, values in sorted(by_metric.items())}
    report.notices = sorted(notices)
    report.notes.extend(sorted(bias_reasons))
    return report
