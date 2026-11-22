"""Measure the particle hits, tracks and bright pixels of one dark frame.

A dark frame is taken with the shutter closed, so the only bright things in it
should be the sensor's own hot pixels (the same in every dark of the set) and
the occasional particle hit. This module finds what is left once the set's
usual pixels are taken away:

1. The set's reference is the per-pixel median of a few of its frames
   (`build_set_reference`). A hot pixel is in every frame, so it is in the
   reference. A particle hit is in one frame only, so the median ignores it.
2. The reference is subtracted from the frame. A pixel more than
   `DARK_DEFECT_OUTLIER_NOISE_WIDTHS` noise widths above zero is an outlier.
   The noise width is the larger of the camera profile's read noise (in ADU)
   and the frame's own spread after the subtraction, so a frame cut off at
   zero (whose own spread is zero) still has a width.
3. Outliers that touch each other, or lie within a pixel or two, form one
   group. For each frame the function reports the number of short groups
   (particle hits), the length of the longest group, whether one group runs
   from one frame edge to another, and the total number of outlier pixels.

The numbers are stored with the frame's other calibration numbers. What to do
with them (keep, warn or leave the dark out) is decided in
`pipelines/stacking/pre_processing/dark_defect_judgement.py`, from the set's
numbers together. Reading a frame writes nothing.
"""

import logging
import math
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy import ndimage

from astrometricslib._internal.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
)
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.calibration_frame_measurement import (
    calibration_subject,
    measure_value_step,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import UNREADABLE_FRAME_REASON_PREFIX
from astrometricslib.pipelines.shared.quality.metrics import (
    DARK_DEFECT_METRIC_NAMES,
    DARK_DEFECT_METRICS,
    FRAME_DARK_EDGE_STREAK,
    FRAME_DARK_HIT_COUNT,
    FRAME_DARK_LONGEST_STREAK,
    FRAME_DARK_OUTLIER_COUNT,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "DARK_DEFECT_EDGE_STREAK_MINIMUM_FRACTION",
    "DARK_DEFECT_GROUP_BRIDGE_PX",
    "DARK_DEFECT_LONG_TRACK_PX",
    "DARK_DEFECT_MINIMUM_SET_FRAMES",
    "DARK_DEFECT_OUTLIER_NOISE_WIDTHS",
    "DARK_DEFECT_REFERENCE_FRAMES",
    "DARK_DEFECT_WHOLE_FRAME_FRACTION",
    "DarkDefectFacts",
    "build_set_reference",
    "choose_reference_paths",
    "dark_defect_metric_set_fingerprint",
    "measure_dark_defect_frame",
    "measure_dark_defects",
]

# How many frames of the set go into the reference. The median of seven frames
# ignores a hit that lies on the same pixel in up to three of them, and a
# particle hit happens about once in a frame (register entry STK-D77), so a
# shared hit is very unlikely. Seven also keeps the memory of the biggest
# sensor (6000 x 4000, 2 bytes a pixel) near 340 MB while the reference is
# built.
DARK_DEFECT_REFERENCE_FRAMES = TunedParameter(
    name="shared.dark_defect_reference_frames",
    default=7,
    unit="frames",
    description="Frames of a dark set whose per-pixel median is the reference of the defect check.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D77",
)

# A reference built from fewer frames than this is not trusted: the median of
# two frames is their mean, which keeps half of every hit.
DARK_DEFECT_MINIMUM_SET_FRAMES = TunedParameter(
    name="shared.dark_defect_minimum_set_frames",
    default=5,
    unit="frames",
    description="Fewest frames a dark set needs before its frames are checked for defects.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D77",
)

# A pixel is an outlier when it lies this many noise widths above the set's
# median. Measured on the 139 30 s ASI 533MM darks at gain 0, offset 0 (read
# noise 4.88 ADU): at 10 widths (49 ADU) a typical frame still holds 738
# pixels, because the pixels cut off at zero have a heavy upper tail; at 16
# widths (78 ADU) it holds 47; the pixels of the one cosmic ray track
# (100 to 700 ADU) are all above 20 widths. 16 sits above the tail and below
# the track (register entry STK-D77, a design estimate).
DARK_DEFECT_OUTLIER_NOISE_WIDTHS = TunedParameter(
    name="shared.dark_defect_outlier_noise_widths",
    default=16.0,
    unit="noise widths",
    description="How many noise widths above the set's median a dark pixel must be to count as an outlier.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D77",
)

# Each outlier is grown by this many pixels before grouping, so two pieces of
# one track with one or two missing pixels between them join. The faint pixels
# of a track fall below the limit here and there (the 147 pixel track in
# Dark_636 had gaps of 1 to 2 pixels at the limit above).
DARK_DEFECT_GROUP_BRIDGE_PX = TunedParameter(
    name="shared.dark_defect_group_bridge_px",
    default=1,
    unit="pixels",
    description="Pixels each outlier is grown by before outliers are grouped into tracks.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="STK-D77",
)

# A group at least this long (the longer side of its box) is a long track, not
# an ordinary hit. Over the library's 2,011 darks the longest group that is not
# a track was 23 pixels (a 300 s dark); the three tracks were 60, 100 and 153
# pixels. 30 lies in the gap, the length the first scan of the library used
# (register entry STK-D77).
DARK_DEFECT_LONG_TRACK_PX = TunedParameter(
    name="shared.dark_defect_long_track_px",
    default=30,
    unit="pixels",
    description="Length from which a group of outlier pixels in a dark frame is called a long track.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.STATISTICS,
    register_entry="STK-D77",
)

# A track that touches two different frame edges is called edge to edge only
# when it is also at least this share of the short side of the frame long, so
# a hit near a corner (which touches two edges within a few pixels) is not one.
# A satellite or aircraft crosses the whole frame; one half is far below that.
DARK_DEFECT_EDGE_STREAK_MINIMUM_FRACTION = TunedParameter(
    name="shared.dark_defect_edge_streak_minimum_fraction",
    default=0.5,
    unit="fraction",
    description="Least share of the frame's short side that an edge-to-edge track must cover.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D77",
)

# When more than this share of the pixels are outliers, the whole frame is
# different from its set (a light leak, a warm sensor or another setting), so
# grouping them into tracks says nothing and is skipped. One track is at most
# a few thousand pixels (0.05% of a 9 megapixel frame); 5% is far above that.
DARK_DEFECT_WHOLE_FRAME_FRACTION = TunedParameter(
    name="shared.dark_defect_whole_frame_fraction",
    default=0.05,
    unit="fraction",
    description="Share of outlier pixels above which a dark frame is not searched for separate tracks.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D77",
)

# Rows use the same version numbers as the metric declarations.
_DEFINITION_VERSIONS = {spec.name: spec.definition_version for spec in DARK_DEFECT_METRICS}

# The band of rows of a row's reference text.
_NOT_MEASURED_SET_TOO_SMALL = "the set has too few frames to build a reference"


def dark_defect_metric_set_fingerprint() -> str:
    """Summarize which defect metrics, with which versions, are stored.

    Returns
    -------
    fingerprint : `str`
        The sorted ``name:definition_version`` lines joined with commas, so a
        frame measured with older definitions is measured again.
    """
    return ",".join(sorted(f"{name}:{_DEFINITION_VERSIONS[name]}" for name in DARK_DEFECT_METRIC_NAMES))


@dataclass(frozen=True)
class DarkDefectFacts:
    """What one dark frame holds beyond its set's usual pixels.

    Attributes
    ----------
    hit_count : `int`
        The groups of outlier pixels shorter than `DARK_DEFECT_LONG_TRACK_PX`.
        Zero when the frame was too different from its set to group.
    longest_streak_px : `int`
        The longer side of the box around the longest group. Zero when there
        is no group.
    edge_to_edge : `bool`
        Whether one group touches two different frame edges and is at least
        `DARK_DEFECT_EDGE_STREAK_MINIMUM_FRACTION` of the short side long.
    outlier_pixel_count : `int`
        The pixels more than `DARK_DEFECT_OUTLIER_NOISE_WIDTHS` noise widths
        above the set's median.
    """

    hit_count: int
    longest_streak_px: int
    edge_to_edge: bool
    outlier_pixel_count: int


def choose_reference_paths(paths: Sequence[str], levels: Sequence[float | None] | None = None) -> list[str]:
    """Pick the frames whose median is the set's reference.

    A set can hold a minority of frames at a different level (a few darks
    taken at another setting and filed with it). The reference must come from
    the majority, so when the frames' mean levels are known the frames
    nearest the median level are used.

    Parameters
    ----------
    paths : `Sequence` [`str`]
        The set's frame paths.
    levels : `Sequence` [`float` or `None`], optional
        The mean level of each frame, in the order of `paths`, `None` where
        it is not known. When fewer than half are known, the frames are
        chosen from the whole set.

    Returns
    -------
    reference_paths : `list` [`str`]
        Up to `DARK_DEFECT_REFERENCE_FRAMES` paths, spread evenly over the
        sorted candidates, in path order.
    """
    wanted = resolve(DARK_DEFECT_REFERENCE_FRAMES).value
    ordered = sorted(range(len(paths)), key=lambda index: paths[index])
    candidates = ordered
    if levels is not None and sum(level is not None for level in levels) * 2 >= len(paths):
        known = [levels[index] for index in ordered if levels[index] is not None]
        middle = float(np.median(known))
        by_distance = sorted(
            (index for index in ordered if levels[index] is not None),
            key=lambda index: abs(float(levels[index]) - middle),  # ty: ignore[invalid-argument-type]
        )
        # The nearer half of the frames: a majority at one level is all in it.
        nearest = by_distance[: max(wanted, (len(by_distance) + 1) // 2)]
        candidates = sorted(nearest, key=lambda index: paths[index])
    if len(candidates) <= wanted:
        chosen = candidates
    else:
        chosen = [candidates[round(i * (len(candidates) - 1) / (wanted - 1))] for i in range(wanted)]
    return [paths[index] for index in chosen]


def _read_pixels(path: str) -> np.ndarray:
    """Read the pixels of a frame as they are stored, as a 2-D array.

    Parameters
    ----------
    path : `str`
        The FITS file.

    Returns
    -------
    pixels : `numpy.ndarray`
        The frame (color planes averaged).

    Raises
    ------
    ValueError
        If the file holds no pixel data.
    """
    with open_primary_hdu(path) as hdu:
        raw = hdu.data
        if raw is None:
            raise ValueError("the file holds no pixel data")
        # Averaging planes makes a float array; a single plane stays as stored
        # (16-bit), which keeps the memory of the reference small.
        return raw if raw.ndim == 2 else collapse_to_2d(np.asarray(raw, dtype=np.float32))


def build_set_reference(reference_paths: Sequence[str]) -> np.ndarray:
    """Build the per-pixel median of some frames of a set.

    The median is taken in bands of rows, so only a band of every frame is
    converted to floating point at one time.

    Parameters
    ----------
    reference_paths : `Sequence` [`str`]
        The frames to combine, all of one size.

    Returns
    -------
    reference : `numpy.ndarray`
        The median frame, as 32-bit floating point.

    Raises
    ------
    ValueError
        If no path is given, a file holds no pixels, or the sizes differ.
    """
    if not reference_paths:
        raise ValueError("no reference frames were given")
    frames = [_read_pixels(path) for path in reference_paths]
    if any(frame.shape != frames[0].shape for frame in frames):
        raise ValueError("the reference frames differ in size")
    reference = np.empty(frames[0].shape, dtype=np.float32)
    band_rows = 256
    for start in range(0, reference.shape[0], band_rows):
        band = np.stack([frame[start : start + band_rows] for frame in frames]).astype(np.float32)
        reference[start : start + band_rows] = np.median(band, axis=0)
    return reference


def measure_dark_defects(
    pixels: np.ndarray, reference: np.ndarray, read_noise_adu: float | None
) -> DarkDefectFacts:
    """Find the outlier pixels and tracks of one dark frame.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The dark frame (2-D, or 3-D with color planes, which are averaged).
    reference : `numpy.ndarray`
        The set's per-pixel median, the size of the frame.
    read_noise_adu : `float` or `None`
        The camera profile's read noise at this setting, in ADU. `None` when
        the profile has none: the frame's own spread then sets the noise
        width.

    Returns
    -------
    facts : `DarkDefectFacts`
        The hit count, the longest track, the edge-to-edge flag and the
        number of outlier pixels.

    Raises
    ------
    ValueError
        If the frame and the reference differ in size.
    """
    data = collapse_to_2d(np.asarray(pixels, dtype=np.float32))
    if data.shape != reference.shape:
        raise ValueError("the frame and its set's reference differ in size")
    residual = data - reference
    # 1.4826 turns the median absolute deviation into a standard deviation.
    spread = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    value_step = measure_value_step(data)
    # The last term (1 ADU) only matters for a frame with no read noise, no
    # spread and no value step.
    noise_width = max(read_noise_adu or 0.0, spread, value_step or 0.0, 1.0)
    outliers = residual > resolve(DARK_DEFECT_OUTLIER_NOISE_WIDTHS).value * noise_width
    outlier_count = int(outliers.sum())
    if outlier_count == 0 or outlier_count > resolve(DARK_DEFECT_WHOLE_FRAME_FRACTION).value * outliers.size:
        return DarkDefectFacts(0, 0, False, outlier_count)

    structure = np.ones((3, 3), dtype=bool)
    grown = ndimage.binary_dilation(
        outliers, structure=structure, iterations=resolve(DARK_DEFECT_GROUP_BRIDGE_PX).value
    )
    labels, _ = ndimage.label(grown, structure=structure)
    # The boxes are taken round the outliers themselves, not the grown ones.
    boxes = ndimage.find_objects(np.where(outliers, labels, 0))
    height, width = data.shape
    long_track_px = resolve(DARK_DEFECT_LONG_TRACK_PX).value
    minimum_edge_length = resolve(DARK_DEFECT_EDGE_STREAK_MINIMUM_FRACTION).value * min(height, width)
    hit_count = 0
    longest = 0
    edge_to_edge = False
    for box in boxes:
        if box is None:
            continue
        rows, columns = box
        length = max(rows.stop - rows.start, columns.stop - columns.start)
        longest = max(longest, length)
        if length < long_track_px:
            hit_count += 1
        edges_touched = (
            (rows.start == 0) + (rows.stop == height) + (columns.start == 0) + (columns.stop == width)
        )
        if edges_touched >= 2 and length >= minimum_edge_length:
            edge_to_edge = True
    return DarkDefectFacts(hit_count, longest, edge_to_edge, outlier_count)


def measure_dark_defect_frame(
    path: str,
    kind: str,
    reference: np.ndarray | None,
    read_noise_adu: float | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
) -> list[QualityMeasurement]:
    """Measure one dark frame against its set's reference and give the rows.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.
    kind : `str`
        ``"dark"``.
    reference : `numpy.ndarray` or `None`
        The set's per-pixel median. `None` when the set could not give one
        (too few frames): every row is ``not_measured`` with the reason.
    read_noise_adu : `float` or `None`
        The profile's read noise in ADU, `None` when there is none.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the measurement is made, with a time zone.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row per name in `DARK_DEFECT_METRIC_NAMES`. A frame that cannot
        be read gives ``not_measured`` rows whose reason starts with
        `UNREADABLE_FRAME_REASON_PREFIX`.
    """
    subject = calibration_subject(path, kind)

    def rows(values: Sequence[float | None], reason: str) -> list[QualityMeasurement]:
        """Build one row per metric from values in the order of the names.

        Returns
        -------
        measurements : `list` of `QualityMeasurement`
            The rows.
        """
        built = []
        for name, value in zip(DARK_DEFECT_METRIC_NAMES, values, strict=True):
            missing = value is None or not math.isfinite(value)
            built.append(
                QualityMeasurement(
                    metric=name,
                    definition_version=_DEFINITION_VERSIONS[name],
                    subject=subject,
                    status=MeasurementStatus.NOT_MEASURED if missing else MeasurementStatus.MEASURED,
                    value=None if missing else float(value),  # ty: ignore[invalid-argument-type]
                    reason=reason if missing else "",
                    origin=MeasurementOrigin.MEASURED,
                    run_id=run_id,
                    job_id=job_id,
                    measured_at=measured_at,
                )
            )
        return built

    if reference is None:
        return rows([None] * len(DARK_DEFECT_METRIC_NAMES), _NOT_MEASURED_SET_TOO_SMALL)
    try:
        facts = measure_dark_defects(_read_pixels(path), reference, read_noise_adu)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as read_error:
        reason = f"{UNREADABLE_FRAME_REASON_PREFIX}: {os.path.basename(path)}: {read_error}"
        return rows([None] * len(DARK_DEFECT_METRIC_NAMES), reason)
    by_name = {
        FRAME_DARK_HIT_COUNT: float(facts.hit_count),
        FRAME_DARK_LONGEST_STREAK: float(facts.longest_streak_px),
        FRAME_DARK_EDGE_STREAK: 1.0 if facts.edge_to_edge else 0.0,
        FRAME_DARK_OUTLIER_COUNT: float(facts.outlier_pixel_count),
    }
    return rows([by_name[name] for name in DARK_DEFECT_METRIC_NAMES], "")
