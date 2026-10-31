"""Measure one dark or bias frame and return its quality measurements.

`measure_calibration_frame` follows the pattern of `measure_frame`, which does
the same for a light frame. It reads the pixels of the frame once and returns
one `QualityMeasurement` for every metric in `CALIBRATION_FRAME_METRIC_NAMES`.
It writes nothing. The caller stores the rows with `record_measurements`.

A light frame has a target and a place under the target's ``lights`` folder. A
calibration frame has neither, so its rows have no target, and the subject id
comes from `calibration_subject_id`: the kind and the path below the kind's
folder (``darks`` or ``biases``) of the frames root. The id does not change
when the frames root moves to another mount.

What is measured, in plain words:

- The level: the sigma-clipped median (the light frames' background metric)
  and the sigma-clipped mean. A dark should sit at the bias level of its
  setting, and a bias at the level the camera profile stores for it.
- The share of pixels at exactly zero, and the share at or above the camera's
  saturation level. A frame cut off at zero has lost its noise, and a dark
  with many saturated pixels was struck by light.
- The noise of the frame, from the difference between neighboring pixels.
- The block range: how much the large-scale shape varies. A light leak or a
  glow raises it.
- The value step of the sensor, and a checksum of the pixels so that two
  copies of one file can be told apart from two exposures.
- The sensor temperature from the header.

A value that cannot be measured comes back ``not_measured`` with a reason. It
is never replaced by a default number. A frame that cannot be read gives
``not_measured`` rows that start with `UNREADABLE_FRAME_REASON_PREFIX`, so the
job can try it again on the next run.
"""

import hashlib
import logging
import math
import os
import zlib
from dataclasses import dataclass
from datetime import datetime
from functools import cache

import numpy as np
from astropy.stats import sigma_clipped_stats

from astrometricslib.drivers.calibration_library import header_temperature_c
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import (
    SKY_CLIP_SIGMA,
    UNREADABLE_FRAME_REASON_PREFIX,
    frame_zero_fraction,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    CALIBRATION_FRAME_METRIC_NAMES,
    CALIBRATION_FRAME_METRICS,
    FRAME_BACKGROUND_LEVEL,
    FRAME_BLOCK_RANGE,
    FRAME_CLIPPED_MEAN_LEVEL,
    FRAME_METRICS,
    FRAME_NEIGHBOR_NOISE,
    FRAME_PIXEL_CHECKSUM,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_VALUE_STEP,
    FRAME_ZERO_PIXEL_FRACTION,
)
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "CALIBRATION_FOLDER_BY_KIND",
    "CALIBRATION_SUBJECT_PREFIX",
    "CalibrationPixelFacts",
    "calibration_metric_set_fingerprint",
    "calibration_subject",
    "calibration_subject_id",
    "measure_block_range",
    "measure_calibration_frame",
    "measure_calibration_pixels",
    "measure_neighbor_noise",
    "measure_value_step",
]

# The folder of the frames root that holds each kind of calibration frame.
# These are the folders `CalibrationLibrary` scans.
CALIBRATION_FOLDER_BY_KIND = {"dark": "darks", "bias": "biases"}

# Every calibration subject id starts with this word, so a reader can pick the
# calibration rows out of the frame-level rows.
CALIBRATION_SUBJECT_PREFIX = "calibration"

# The block range grid. Every third pixel is kept in both directions, and the
# result is cut into 12 x 12 cells. These are the settings of the validation
# run on 2,367 real frames (register entry STK-50); a smaller step makes the
# grid cost more with no gain, because the range is dominated by smooth
# patterns that are many cells wide.
BLOCK_RANGE_PIXEL_STEP = 3
BLOCK_RANGE_GRID_CELLS = 12

# The noise estimate compares each pixel with the one two columns away. Two
# keeps pixels of one color together on a color sensor and does no harm on a
# mono one (register entry STK-49).
NEIGHBOR_NOISE_COLUMN_STEP = 2

# The part of the frame used to find the value step: a block of this many
# pixels on a side, taken from the top-left corner of the central region of
# this many pixels on a side. 300 pixels already hold hundreds of distinct
# values, and the step is the same everywhere on a sensor.
VALUE_STEP_CENTRAL_REGION_PX = 1000
VALUE_STEP_BLOCK_PX = 300

_DEFINITION_VERSIONS = {
    spec.name: spec.definition_version for spec in (*FRAME_METRICS, *CALIBRATION_FRAME_METRICS)
}


@cache
def calibration_metric_set_fingerprint() -> str:
    """Summarize which metrics a calibration measurement covers.

    A frame is measured once and then skipped while its file is unchanged. When
    a metric is added to the calibration list, or one is redefined (its
    ``definition_version`` is raised), the frames measured earlier lack the new
    numbers. The measurement job stores this fingerprint with each frame's
    stamp and measures a frame again when it differs.

    Returns
    -------
    fingerprint : `str`
        The first 12 hexadecimal digits of the SHA-256 hash of the sorted
        ``name:definition_version`` lines of `CALIBRATION_FRAME_METRIC_NAMES`.
    """
    lines = sorted(f"{name}:{_DEFINITION_VERSIONS[name]}" for name in CALIBRATION_FRAME_METRIC_NAMES)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


def calibration_subject_id(path: str, kind: str) -> str:
    """Name a calibration frame by its kind and its place in the library.

    Parameters
    ----------
    path : `str`
        The frame's path.
    kind : `str`
        ``"dark"`` or ``"bias"``.

    Returns
    -------
    subject_id : `str`
        ``"calibration/<kind>/<path below the darks or biases folder>"``. The
        last folder of that name in the path is used. A path with no such
        folder gives the file name only, never the full path.
    """
    folder_name = CALIBRATION_FOLDER_BY_KIND[kind]
    parts = os.path.normpath(path).split(os.sep)
    folder_positions = [
        index for index, part in enumerate(parts) if part.lower() == folder_name and index + 1 < len(parts)
    ]
    relative_parts = parts[folder_positions[-1] + 1 :] if folder_positions else parts[-1:]
    return "/".join([CALIBRATION_SUBJECT_PREFIX, kind, *relative_parts])


def calibration_subject(path: str, kind: str) -> QualitySubject:
    """Describe one calibration frame as the subject of a measurement.

    Parameters
    ----------
    path : `str`
        The frame's path.
    kind : `str`
        ``"dark"`` or ``"bias"``.

    Returns
    -------
    subject : `QualitySubject`
        Level ``frame`` with the id of `calibration_subject_id` and no target.
    """
    return QualitySubject(level=SubjectLevel.FRAME, subject_id=calibration_subject_id(path, kind))


@dataclass(frozen=True)
class CalibrationPixelFacts:
    """The numbers one pass over a calibration frame's pixels gives.

    Attributes
    ----------
    clipped_mean_adu, clipped_median_adu : `float`
        The sigma-clipped mean and median.
    zero_fraction : `float`
        The share of pixels at exactly zero.
    saturated_fraction : `float` or `None`
        The share of pixels at or above the saturation level. `None` when no
        saturation level was given.
    neighbor_noise_adu : `float` or `None`
        The noise from neighboring pixels. `None` when the frame is too narrow.
    block_range_adu : `float`
        The largest minus the smallest cell median of the block grid.
    value_step_adu : `float` or `None`
        The smallest step between distinct pixel values, or `None`.
    checksum : `int`
        The CRC-32 checksum of the pixel array as stored.
    """

    clipped_mean_adu: float
    clipped_median_adu: float
    zero_fraction: float
    saturated_fraction: float | None
    neighbor_noise_adu: float | None
    block_range_adu: float
    value_step_adu: float | None
    checksum: int


def measure_neighbor_noise(data: np.ndarray) -> float | None:
    """Measure the pixel-to-pixel noise of one frame.

    The noise of each pixel is independent of its neighbor's, so the
    difference of two pixels has the variance of two. A smooth pattern is the
    same in both pixels and cancels.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.

    Returns
    -------
    noise_adu : `float` or `None`
        The standard deviation of the difference between each pixel and the
        pixel `NEIGHBOR_NOISE_COLUMN_STEP` columns to its right, divided by
        the square root of 2. `None` when the frame has too few columns.
    """
    if data.ndim != 2 or data.shape[1] <= NEIGHBOR_NOISE_COLUMN_STEP:
        return None
    difference = data[:, NEIGHBOR_NOISE_COLUMN_STEP:] - data[:, :-NEIGHBOR_NOISE_COLUMN_STEP]
    return float(np.std(difference) / math.sqrt(2.0))


def measure_block_range(data: np.ndarray) -> float:
    """Measure how much the large-scale shape of a frame varies.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.

    Returns
    -------
    block_range_adu : `float`
        Every `BLOCK_RANGE_PIXEL_STEP` th pixel is kept in both directions and
        cut into a `BLOCK_RANGE_GRID_CELLS` x `BLOCK_RANGE_GRID_CELLS` grid.
        The value is the largest cell median minus the smallest.
    """
    subsampled = data[::BLOCK_RANGE_PIXEL_STEP, ::BLOCK_RANGE_PIXEL_STEP]
    row_bands = np.array_split(subsampled, BLOCK_RANGE_GRID_CELLS, axis=0)
    cell_medians = [
        float(np.median(cell))
        for band in row_bands
        for cell in np.array_split(band, BLOCK_RANGE_GRID_CELLS, axis=1)
    ]
    return max(cell_medians) - min(cell_medians)


def measure_value_step(data: np.ndarray) -> float | None:
    """Find the smallest step between distinct pixel values.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame.

    Returns
    -------
    value_step_adu : `float` or `None`
        The smallest difference between neighboring distinct values in the
        top-left `VALUE_STEP_BLOCK_PX` pixels square of the central
        `VALUE_STEP_CENTRAL_REGION_PX` pixels square. `None` when the block
        holds one value only.
    """
    height, width = data.shape
    top = max(0, (height - VALUE_STEP_CENTRAL_REGION_PX) // 2)
    left = max(0, (width - VALUE_STEP_CENTRAL_REGION_PX) // 2)
    block = data[top : top + VALUE_STEP_BLOCK_PX, left : left + VALUE_STEP_BLOCK_PX]
    values = np.unique(block)
    return float(np.min(np.diff(values))) if values.size > 1 else None


def measure_calibration_pixels(
    pixels: np.ndarray, saturation_level_adu: float | None, checksum: int
) -> CalibrationPixelFacts:
    """Measure everything about a frame's pixels in one pass.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The frame (2-D, or 3-D with color planes, which are averaged).
    saturation_level_adu : `float` or `None`
        A pixel at or above this counts as saturated. `None` leaves the
        saturated share unmeasured.
    checksum : `int`
        The checksum of the pixels as stored, found before they were changed
        to floating point.

    Returns
    -------
    facts : `CalibrationPixelFacts`
        The measured numbers.
    """
    data = collapse_to_2d(np.asarray(pixels, dtype=np.float32))
    mean_adu, median_adu, _ = sigma_clipped_stats(data, sigma=SKY_CLIP_SIGMA)
    return CalibrationPixelFacts(
        clipped_mean_adu=float(mean_adu),
        clipped_median_adu=float(median_adu),
        zero_fraction=frame_zero_fraction([(None, data)]),
        saturated_fraction=(
            None
            if saturation_level_adu is None
            else compute_saturated_pixel_fraction(data, saturation_level_adu)
        ),
        neighbor_noise_adu=measure_neighbor_noise(data),
        block_range_adu=measure_block_range(data),
        value_step_adu=measure_value_step(data),
        checksum=checksum,
    )


def _row(
    metric: str,
    subject: QualitySubject,
    value: float | None,
    reason: str,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
) -> QualityMeasurement:
    """Build one measurement row; a missing value becomes ``not_measured``.

    Parameters
    ----------
    metric : `str`
        The registered metric name.
    subject : `QualitySubject`
        The frame.
    value : `float` or `None`
        The value, or `None` if it could not be measured.
    reason : `str`
        Why the value is missing (required then), or a note.
    run_id, job_id, measured_at
        The run's facts, copied onto the row.

    Returns
    -------
    measurement : `QualityMeasurement`
        The row.
    """
    missing = value is None or not math.isfinite(value)
    return QualityMeasurement(
        metric=metric,
        definition_version=_DEFINITION_VERSIONS[metric],
        subject=subject,
        status=MeasurementStatus.NOT_MEASURED if missing else MeasurementStatus.MEASURED,
        value=None if missing else value,
        reason=(reason or "the value could not be computed") if missing else reason,
        origin=MeasurementOrigin.MEASURED,
        run_id=run_id,
        job_id=job_id,
        measured_at=measured_at,
    )


def measure_calibration_frame(
    path: str,
    kind: str,
    camera_profile: CameraProfile | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
) -> list[QualityMeasurement]:
    """Measure one dark or bias frame and return one row per metric.

    The pixels are read once. Nothing is written.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.
    kind : `str`
        ``"dark"`` or ``"bias"``.
    camera_profile : `CameraProfile` or `None`
        The camera's profile, for the saturation level. `None` leaves the
        saturated share ``not_measured``.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the measurement is made. It must carry a time zone.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One per name in `CALIBRATION_FRAME_METRIC_NAMES`. When the file cannot
        be read, every row is ``not_measured`` and its reason starts with
        `UNREADABLE_FRAME_REASON_PREFIX`.

    Raises
    ------
    ValueError
        Never to the caller: a file with no pixels is reported as an
        unreadable frame in the returned rows.
    """
    subject = calibration_subject(path, kind)
    context = {"run_id": run_id, "job_id": job_id, "measured_at": measured_at}
    try:
        with open_primary_hdu(path) as hdu:
            raw = hdu.data
            header = hdu.header.copy()
        if raw is None:
            raise ValueError("the file holds no pixel data")
        checksum = zlib.crc32(np.ascontiguousarray(raw).tobytes())
        saturation_level = (
            None if camera_profile is None else float(camera_profile.pixel_saturation_level_adu.value)
        )
        facts = measure_calibration_pixels(raw, saturation_level, checksum)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as read_error:
        reason = f"{UNREADABLE_FRAME_REASON_PREFIX}: {read_error}"
        return [_row(name, subject, None, reason, **context) for name in CALIBRATION_FRAME_METRIC_NAMES]

    temperature_c = header_temperature_c(header)
    values: dict[str, tuple[float | None, str]] = {
        FRAME_BACKGROUND_LEVEL: (facts.clipped_median_adu, ""),
        FRAME_CLIPPED_MEAN_LEVEL: (facts.clipped_mean_adu, ""),
        FRAME_ZERO_PIXEL_FRACTION: (facts.zero_fraction, ""),
        FRAME_SATURATED_PIXEL_FRACTION: (
            facts.saturated_fraction,
            "no camera profile was given, so the saturation level is unknown",
        ),
        FRAME_NEIGHBOR_NOISE: (facts.neighbor_noise_adu, "the frame is too narrow to compare neighbors"),
        FRAME_BLOCK_RANGE: (facts.block_range_adu, ""),
        FRAME_VALUE_STEP: (facts.value_step_adu, "the central block holds one pixel value only"),
        FRAME_PIXEL_CHECKSUM: (float(facts.checksum), ""),
        FRAME_SENSOR_TEMPERATURE: (temperature_c, "the frame header has no CCD-TEMP card"),
    }
    return [_row(name, subject, *values[name], **context) for name in CALIBRATION_FRAME_METRIC_NAMES]
