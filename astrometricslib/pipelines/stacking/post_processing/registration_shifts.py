"""Measures how the pointing stepped and drifted between exposures.

Siril lines every frame up with one reference frame and saves, for each frame,
the transform that moves it onto the reference. Where the transform puts the
middle of the frame says where the telescope pointed, in pixels, compared with
the reference. Two things can be read from the series of those pointings.

The registration step is the distance between the pointings of two exposures
taken one after the other. Dithering means moving the telescope a few pixels
between exposures on purpose, so that a defect fixed on the sensor (a hot
pixel, a column offset) lands on different sky each time and averages out in
the stack. The step is the distance moved, whatever the cause: a dither, a
slow drift of the mount, or the noise of Siril's fit. A median step of 0.3 to
0.6 pixel is a drift plus noise and does not show that the run was dithered.
To help tell them apart, the steps are also measured after a straight line
has been taken out of each night's pointings. What is left is the dither (if
any) plus the noise of the fit. A dither made only every few exposures changes
a minority of the steps and leaves the median at the drift step, so the
number of steps far above the usual step (the jump count) is stored too. It
counts the dithers whatever the interval between them; on the M 57 night of
2026-10-03 the dither was every 10 exposures and the median step was 0.45 px.

The drift is the slow, steady walk of the pointing through a night, found as
the slope of a straight line fitted to each night's pointings against time.
Its direction matters for the walking-noise measurement
(`half_pattern.py`): a pattern fixed on the sensor is smeared along the
direction in which the pointing moves.

A step across two observing nights is not a dither, so only pairs of
exposures from one night are counted. A step longer than
`MAXIMUM_PLAUSIBLE_STEP_PX` is not a dither either. DSLR groups of some
targets carry transforms thousands of pixels long, and those pairs are
counted as degenerate and left out. When more than `MAXIMUM_DEGENERATE_SHARE`
of the pairs of one night are degenerate, or more than `MAXIMUM_IDENTITY_SHARE`
of the frames have exactly the identity transform, the transforms are not
pointings and the step rows are not measured (the count of degenerate pairs
is kept, because it is the evidence). A stack that joins several exposure
groups has no one list of transforms and is not measured.

Pointings are given at the middle of the image, not at the corner that
Siril's matrix writes down. When a frame is turned by a small angle, the
corner moves by the angle times the distance to the middle (a turn of 0.01
degree moves a corner by 0.26 pixel), and that part is not a pointing change.
When the image size is unknown, the corner shift is used and the reason says
so.

Directions are in array coordinates: the angle runs from the column axis
toward the row axis, in degrees, and a direction is an axis (0 up to, but not
including, 180 degrees), because the sign of a drift does not matter for a
smear.
"""

import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

import numpy as np

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, read_header
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.registration_record import (
    HOMOGRAPHY_LENGTH,
    RegistrationRecord,
    is_already_registered,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_METRICS,
    STACKING_REGISTRATION_DRIFT_DIRECTION_DEG,
    STACKING_REGISTRATION_DRIFT_EXTENT_PX,
    STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR,
    STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX,
    STACKING_REGISTRATION_STEP_DEGENERATE_COUNT,
    STACKING_REGISTRATION_STEP_JUMP_COUNT,
    STACKING_REGISTRATION_STEP_MAD_PX,
    STACKING_REGISTRATION_STEP_MEDIAN_PX,
)
from astrometricslib.pipelines.stacking.post_processing.field_rotation import (
    ALREADY_REGISTERED_REASON,
    UNMATCHED_REASON,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import MAD_TO_SIGMA
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

__all__ = [
    "DRIFT_SIGNIFICANCE",
    "MAXIMUM_DEGENERATE_SHARE",
    "MAXIMUM_IDENTITY_SHARE",
    "MAXIMUM_PLAUSIBLE_STEP_PX",
    "MAXIMUM_SLOPE_PAIRS",
    "MINIMUM_AXIS_AGREEMENT",
    "MINIMUM_FRAMES_FOR_DRIFT_FIT",
    "MINIMUM_STEPS",
    "DriftSummary",
    "FrameShift",
    "ShiftSeries",
    "StepSummary",
    "drift_of_runs",
    "drift_summary",
    "fit_nights",
    "pointing_measurements",
    "shifts_from_registration",
    "step_summary",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# A step longer than this is not a dither. The survey of the stored shifts
# (2026-10-23) found real dithers and drifts of at most a few pixels (the
# largest ZWO dither median is 2.5 px) and found DSLR groups carrying 5,000 to
# 7,400 px "shifts" that come from the debayered, differently sized frames and
# not from pointing. 50 px sits between the two with a wide margin on both
# sides. Pairs above it are counted, not used. Register entry STK-44.
MAXIMUM_PLAUSIBLE_STEP_PX = 50.0

# The step rows are not measured when more than this share of the pairs of one
# night is degenerate. The survey (register entry STK-44) found 0 to 2 of 120
# pairs degenerate in every ZWO night but one (M 27 on 2026-10-02: 3 of 18,
# a run Siril left with no single reference) and 11% to 24% in the DSLR groups
# whose medians were not pointings (NGC 1499 9 of 80, IC 1805 14 of 80, M 31
# 113 of 464); a 10% limit splits the two sets, and a lone bad pair in a long
# run (2% in NGC 7331) stays below it and is only counted.
MAXIMUM_DEGENERATE_SHARE = 0.10

# The step rows are not measured when more than this share of the frames has
# exactly the identity transform. Siril leaves the identity on a frame it did
# not register and on the reference frame; a run in which most frames have it
# was mostly not registered (a median step of 0.0 px in NGC 1499). Half is the
# majority; the reference frame alone is one frame.
MAXIMUM_IDENTITY_SHARE = 0.5

# A pointing of exactly zero is an identity transform. Siril writes the
# identity as exact zeros, and a fitted transform is never closer to it than
# this, so the tolerance only absorbs the float round-off of the shift at the
# middle of the image.
IDENTITY_TOLERANCE_PX = 1e-9

# A step counts as a jump when it exceeds the median step by this many robust
# standard deviations (1.4826 times the median absolute deviation, but not
# less than MINIMUM_STEP_SCALE_PX). The 90th percentile of the steps was
# tried first and does not see a dither made every 10 exposures (one step in
# 10 is 10% of the steps, at the percentile's edge): the M 57 night of
# 2026-10-03 has jumps at frames 11, 21, 31, 41 and 51. For the steps of
# independent Gaussian jitter (a Rayleigh distribution) the median plus 5
# robust deviations is 5.5 times the jitter of one coordinate, which a step
# passes with probability about 3e-7 when the deviation is known. It is
# estimated from the steps of one run, and a simulation of 200 runs of 60
# frames of jitter alone (register entry STK-44) gave 2 false jumps in 11,800
# steps, about one run in a hundred.
STEP_JUMP_SIGMAS = 5.0

# The floor of the robust deviation, in pixels. A pure drift has a median
# absolute deviation of zero, and then every step above the median would
# count. The smallest median absolute deviation of the steps of a real run is
# 0.05 px (M 81 on 2026-01-14, 1.4826 x 0.05 = 0.07 px); 0.1 px is above it
# and below the 0.2 px of the usual run.
MINIMUM_STEP_SCALE_PX = 0.1

# The robust drift line takes the median slope over all pairs of frames of a
# night. A night of n frames has n (n - 1) / 2 pairs; above this many pairs
# (448 frames) a fixed random sample of this size is used instead, which
# bounds the memory (a few arrays of this length) and the time. The median of
# 100,000 slopes is within a few percent of the median of all of them.
MAXIMUM_SLOPE_PAIRS = 100_000
SLOPE_SAMPLE_SEED = 20261024

# Fewest steps for which a median step is reported. With fewer, one odd pair
# decides the median. 5 is the smallest odd count for which the median of the
# steps is still set by a majority of three.
MINIMUM_STEPS = 5

# Fewest frames of one night for a straight-line fit of its pointings. A line
# has two parameters for each of the two coordinates, so 5 frames leave 3
# degrees of freedom for the scatter used in the significance test.
MINIMUM_FRAMES_FOR_DRIFT_FIT = 5

# A night's drift counts only when its length (slope) is at least this many
# times the standard error of the slope. The same 3 sigma that
# `stack_noise.ROTATION_SIGNIFICANCE` uses.
DRIFT_SIGNIFICANCE = 3.0

# The axes of the nights must agree at least this well for one axis to be
# named. The agreement is the length of the mean of the unit vectors at twice
# each axis angle: 1 when all axes are the same, 0 when they point every way.
# 0.5 is where the axes are spread over about +-30 degrees.
MINIMUM_AXIS_AGREEMENT = 0.5

SECONDS_PER_HOUR = 3600.0

# The third row of a registration transform is (0, 0, 1) for a shift, a turn
# and a stretch, and differs from it only slightly for a perspective term. A
# divisor smaller than this is not a transform of an image.
MINIMUM_HOMOGRAPHY_SCALE = 1e-9


@dataclass(frozen=True)
class FrameShift:
    """Where one exposure pointed, compared with the registration reference.

    Parameters
    ----------
    path : `str`
        The frame file.
    timestamp : `float`
        The start of the exposure, in seconds since the Unix epoch.
    night : `str`
        The observing night the exposure belongs to.
    column_px : `float`
        The shift along the columns, in pixels (positive toward larger column
        numbers).
    row_px : `float`
        The shift along the rows, in pixels (positive toward larger row
        numbers). Siril counts its y axis up from the last row, so this is the
        negative of the matrix's y shift.
    """

    path: str
    timestamp: float
    night: str
    column_px: float
    row_px: float


@dataclass(frozen=True)
class ShiftSeries:
    """The pointings of the registered frames of a stack.

    Parameters
    ----------
    shifts : `tuple` of `FrameShift`
        One entry for each registered frame that has a capture time, in the
        order given.
    at_image_center : `bool`
        Whether each shift is the shift of the middle of the image (`True`) or
        the corner shift that Siril's matrix writes down (`False`, when the
        image size was unknown).
    frames_without_time : `int`
        How many registered frames were left out for lack of a capture time.
    """

    shifts: tuple[FrameShift, ...]
    at_image_center: bool
    frames_without_time: int = 0


@dataclass(frozen=True)
class StepSummary:
    """The steps between exposures taken one after the other.

    Parameters
    ----------
    median_px : `float` or `None`
        The median step, over the pairs used. `None` with the reason in
        `unusable_reason` when the steps cannot be trusted or are too few.
    mad_px : `float` or `None`
        The median absolute deviation of the steps from their median (not
        scaled).
    jump_count : `int` or `None`
        How many used steps were jumps: larger than the median step plus
        `STEP_JUMP_SIGMAS` robust deviations (at least `MINIMUM_STEP_SCALE_PX`
        each).
    jump_threshold_px : `float` or `None`
        The step above which a step is a jump.
    used_count : `int`
        How many pairs were used.
    degenerate_count : `int`
        How many pairs of one night were left out for a step longer than
        `MAXIMUM_PLAUSIBLE_STEP_PX`.
    other_night_count : `int`
        How many consecutive pairs were left out because the two exposures
        belong to different observing nights.
    after_drift_median_px : `float` or `None`
        The median step after a straight line was taken out of each night's
        pointings, or `None` when no night could be fitted or fewer than
        `MINIMUM_STEPS` such steps exist.
    after_drift_count : `int`
        How many pairs the previous value rests on.
    unusable_reason : `str`
        Why the step values are missing. Empty when they are given.
    is_suspect : `bool`
        Whether the values were refused because the transforms do not look
        like pointings (too many degenerate pairs, or mostly identity
        transforms), as against too few pairs.
    """

    median_px: float | None
    mad_px: float | None
    jump_count: int | None
    jump_threshold_px: float | None
    used_count: int
    degenerate_count: int
    other_night_count: int
    after_drift_median_px: float | None = None
    after_drift_count: int = 0
    unusable_reason: str = ""
    is_suspect: bool = False


@dataclass(frozen=True)
class NightDrift:
    """The straight line fitted to one night's pointings.

    Parameters
    ----------
    night : `str`
        The observing night.
    frame_count : `int`
        Frames in the fit (those within `MAXIMUM_PLAUSIBLE_STEP_PX` of the
        line).
    column_rate_px_per_hour, row_rate_px_per_hour : `float`
        The slope of the shift along the columns and along the rows.
    rate_px_per_hour : `float`
        The length of the slope.
    rate_error_px_per_hour : `float`
        The standard error of the length (the two slopes' errors added in
        quadrature).
    significant : `bool`
        Whether the length is at least `DRIFT_SIGNIFICANCE` times its error.
    """

    night: str
    frame_count: int
    column_rate_px_per_hour: float
    row_rate_px_per_hour: float
    rate_px_per_hour: float
    rate_error_px_per_hour: float
    significant: bool


@dataclass(frozen=True)
class DriftSummary:
    """The steady walk of the pointing through the nights of a stack.

    Parameters
    ----------
    rate_px_per_hour : `float` or `None`
        The mean, weighted by frame count, of the nights' drift lengths, or
        `None` when no night had `MINIMUM_FRAMES_FOR_DRIFT_FIT` frames.
    rate_error_px_per_hour : `float` or `None`
        Its standard error.
    direction_deg : `float` or `None`
        The drift axis, 0 up to but not including 180 degrees from the column
        axis toward the row axis; `None` with the reason in `direction_reason`.
    direction_reason : `str`
        Why there is no axis.
    extent_px : `float` or `None`
        The longest, over the significant nights, of the range the pointings
        cover along the axis.
    nights : `tuple` of `NightDrift`
        The fit of each night that had enough frames.
    rate_reason : `str`
        Why there is no rate, or, when there is one that is below
        `DRIFT_SIGNIFICANCE` times its error, the warning that the noise has
        made it too large. Empty when the rate is well above its error.
    """

    rate_px_per_hour: float | None
    rate_error_px_per_hour: float | None
    direction_deg: float | None
    direction_reason: str
    extent_px: float | None
    nights: tuple[NightDrift, ...] = field(default_factory=tuple)
    rate_reason: str = ""


def _shift_at(homography: Sequence[float], x_px: float, y_px: float) -> tuple[float, float]:
    """Find where a transform puts a point and how far that is from the point.

    Parameters
    ----------
    homography : `Sequence` [`float`]
        The nine numbers of Siril's 3 x 3 transform, in row order.
    x_px, y_px : `float`
        The point, in Siril's image coordinates.

    Returns
    -------
    shift : `tuple` [`float`, `float`]
        The (x, y) shift in pixels. NaN when the transform sends the point
        to infinity.
    """
    h00, h01, h02, h10, h11, h12, h20, h21, h22 = (float(value) for value in homography[:9])
    scale = h20 * x_px + h21 * y_px + h22
    if not math.isfinite(scale) or abs(scale) < MINIMUM_HOMOGRAPHY_SCALE:
        return math.nan, math.nan
    return (h00 * x_px + h01 * y_px + h02) / scale - x_px, (h10 * x_px + h11 * y_px + h12) / scale - y_px


def shifts_from_registration(
    paths: Sequence[str],
    homographies: Sequence[Sequence[float]],
    included: Sequence[bool],
    timestamps: Mapping[str, float | None],
    image_size: tuple[int, int] | None,
) -> ShiftSeries:
    """Turn Siril's transforms into the pointing of each registered frame.

    Parameters
    ----------
    paths : `Sequence` [`str`]
        The frame files in Siril's order.
    homographies : `Sequence` [`Sequence` [`float`]]
        For each frame, the nine numbers of its transform.
    included : `Sequence` [`bool`]
        For each frame, `False` when Siril could not register it. An empty
        sequence, or one of another length, counts every frame as registered.
    timestamps : `Mapping` [`str`, `float` or `None`]
        The capture time of each frame by path.
    image_size : `tuple` [`int`, `int`] or `None`
        The (width, height) in pixels of the frames Siril registered, or
        `None` when unknown (the shift is then taken at the corner).

    Returns
    -------
    series : `ShiftSeries`
        The pointings, in the order of ``paths``.
    """
    flags = list(included) if len(included) == len(paths) else [True] * len(paths)
    if image_size is not None:
        center_x, center_y = image_size[0] / 2.0, image_size[1] / 2.0
    else:
        center_x = center_y = 0.0
    shifts: list[FrameShift] = []
    without_time = 0
    for path, homography, flag in zip(paths, homographies, flags, strict=True):
        if flag is False or len(homography) < HOMOGRAPHY_LENGTH:
            continue
        timestamp = timestamps.get(path)
        if timestamp is None:
            without_time += 1
            continue
        x_shift, y_shift = _shift_at(homography, center_x, center_y)
        if not (math.isfinite(x_shift) and math.isfinite(y_shift)):
            continue
        shifts.append(
            FrameShift(
                path=path,
                timestamp=float(timestamp),
                night=observing_night_id(float(timestamp)),
                column_px=x_shift,
                row_px=-y_shift,
            )
        )
    return ShiftSeries(tuple(shifts), image_size is not None, without_time)


def _by_night(series: ShiftSeries) -> dict[str, list[FrameShift]]:
    """Group the pointings by observing night, each night in time order.

    Parameters
    ----------
    series : `ShiftSeries`
        The pointings.

    Returns
    -------
    nights : `dict` [`str`, `list` of `FrameShift`]
        The nights in time order of their first exposure.
    """
    ordered = sorted(series.shifts, key=lambda shift: (shift.timestamp, shift.path))
    nights: dict[str, list[FrameShift]] = {}
    for shift in ordered:
        nights.setdefault(shift.night, []).append(shift)
    return nights


def _fit_line(times: np.ndarray, values: np.ndarray) -> tuple[float, float, np.ndarray]:
    """Fit a straight line to values against time.

    Parameters
    ----------
    times : `numpy.ndarray`
        The times in hours.
    values : `numpy.ndarray`
        The values.

    Returns
    -------
    slope, intercept : `float`
        The line's slope (per hour) and its value at time 0.
    residuals : `numpy.ndarray`
        The values minus the line.
    """
    slope, intercept = np.polyfit(times, values, 1)
    return float(slope), float(intercept), values - (slope * times + intercept)


def _night_arrays(frames: Sequence[FrameShift]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Give a night's times (hours from its first frame) and pointings.

    Parameters
    ----------
    frames : `Sequence` of `FrameShift`
        One night's pointings in time order.

    Returns
    -------
    times, columns, rows : `numpy.ndarray`
        The times in hours and the two shifts in pixels.
    """
    first = frames[0].timestamp
    times = np.array([(frame.timestamp - first) / SECONDS_PER_HOUR for frame in frames])
    columns = np.array([frame.column_px for frame in frames])
    rows = np.array([frame.row_px for frame in frames])
    return times, columns, rows


def _robust_residuals(times: np.ndarray, values: np.ndarray) -> np.ndarray:
    """Give the residuals of a straight line that a few bad points cannot tilt.

    The slope is the median of the slopes between pairs of points with
    different times (the Theil-Sen line) and the intercept is the median of
    the values minus the slope times the time. A straight least-squares line
    would be pulled toward a point thousands of pixels away and would put the
    good points far from it. All pairs are used up to `MAXIMUM_SLOPE_PAIRS`
    of them; a longer night uses a fixed random sample of that many pairs, so
    the memory and the time stay bounded.

    Parameters
    ----------
    times : `numpy.ndarray`
        The times in hours.
    values : `numpy.ndarray`
        The values.

    Returns
    -------
    residuals : `numpy.ndarray`
        The values minus the line.
    """
    count = times.size
    if count * (count - 1) // 2 <= MAXIMUM_SLOPE_PAIRS:
        first, second = np.triu_indices(count, k=1)
    else:
        generator = np.random.default_rng(SLOPE_SAMPLE_SEED)
        first = generator.integers(0, count, size=MAXIMUM_SLOPE_PAIRS)
        second = generator.integers(0, count, size=MAXIMUM_SLOPE_PAIRS)
    separation = times[second] - times[first]
    usable = np.abs(separation) > 0.0
    slope = float(np.median((values[second] - values[first])[usable] / separation[usable]))
    intercept = float(np.median(values - slope * times))
    return values - (slope * times + intercept)


def _fit_night(
    frames: Sequence[FrameShift],
) -> tuple[NightDrift, np.ndarray, np.ndarray, list[FrameShift]] | None:
    """Fit one night's pointings with a straight line, dropping far outliers.

    A frame more than `MAXIMUM_PLAUSIBLE_STEP_PX` from the robust line
    (`_robust_residuals`) is left out, and the line is then fitted by least
    squares to the frames that remain.

    Parameters
    ----------
    frames : `Sequence` of `FrameShift`
        One night's pointings in time order.

    Returns
    -------
    fit : `tuple` or `None`
        The night's `NightDrift`, the residuals along the columns and along
        the rows of the frames kept, and those frames; `None` when fewer than
        `MINIMUM_FRAMES_FOR_DRIFT_FIT` frames remain or the exposures all
        start at one moment.
    """
    kept = list(frames)
    if len(kept) < MINIMUM_FRAMES_FOR_DRIFT_FIT:
        return None
    times, columns, rows = _night_arrays(kept)
    if np.ptp(times) <= 0.0:
        return None
    distance = np.hypot(_robust_residuals(times, columns), _robust_residuals(times, rows))
    kept = [frame for frame, near in zip(kept, distance <= MAXIMUM_PLAUSIBLE_STEP_PX, strict=True) if near]
    if len(kept) < MINIMUM_FRAMES_FOR_DRIFT_FIT:
        return None
    times, columns, rows = _night_arrays(kept)
    if np.ptp(times) <= 0.0:
        return None
    column_slope, _, column_residuals = _fit_line(times, columns)
    row_slope, _, row_residuals = _fit_line(times, rows)
    degrees_of_freedom = len(kept) - 2
    spread_squared = float(np.sum((times - times.mean()) ** 2))
    column_error = math.sqrt(float(np.sum(column_residuals**2)) / degrees_of_freedom / spread_squared)
    row_error = math.sqrt(float(np.sum(row_residuals**2)) / degrees_of_freedom / spread_squared)
    rate = math.hypot(column_slope, row_slope)
    # The error of the length: the slope's error along the drift axis, which
    # is the two errors weighted by the axis. For a drift along one coordinate
    # it is that coordinate's error.
    if rate > 0.0:
        error = math.hypot(column_slope * column_error, row_slope * row_error) / rate
    else:
        error = math.hypot(column_error, row_error)
    drift = NightDrift(
        night=kept[0].night,
        frame_count=len(kept),
        column_rate_px_per_hour=column_slope,
        row_rate_px_per_hour=row_slope,
        rate_px_per_hour=rate,
        rate_error_px_per_hour=error,
        significant=rate > 0.0 and rate >= DRIFT_SIGNIFICANCE * error,
    )
    return drift, column_residuals, row_residuals, kept


_NightFit = tuple[NightDrift, np.ndarray, np.ndarray, list[FrameShift]]


def fit_nights(series: ShiftSeries) -> list[_NightFit]:
    """Fit a straight line to the pointings of each night, once.

    Both `step_summary` and `drift_summary` need the fits, so a caller that
    wants both calls this once and gives the result to each.

    Parameters
    ----------
    series : `ShiftSeries`
        The pointings of the registered frames.

    Returns
    -------
    fits : `list` of `tuple`
        For each night that `_fit_night` could fit, in time order: its
        `NightDrift`, the residuals along the columns and along the rows, and
        the frames kept.
    """
    return [fit for fit in (_fit_night(frames) for frames in _by_night(series).values()) if fit is not None]


def step_summary(series: ShiftSeries, fits: Sequence[_NightFit] | None = None) -> StepSummary:
    """Measure the steps between exposures taken one after the other.

    Parameters
    ----------
    series : `ShiftSeries`
        The pointings of the registered frames.
    fits : `Sequence` of `tuple`, optional
        The nights' straight-line fits (`fit_nights`). Fitted here if left
        out.

    Returns
    -------
    summary : `StepSummary`
        The median step, its spread and its number of jumps, the pairs used
        and the pairs left out, and the median step after each night's drift
        line is taken out. The values are `None`, with the reason, when more
        than `MAXIMUM_IDENTITY_SHARE` of the frames have exactly the identity
        transform, when more than `MAXIMUM_DEGENERATE_SHARE` of the pairs of
        one night are degenerate, or when fewer than `MINIMUM_STEPS` pairs
        remain.
    """
    ordered = sorted(series.shifts, key=lambda shift: (shift.timestamp, shift.path))
    steps: list[float] = []
    degenerate = 0
    other_night = 0
    for first, second in pairwise(ordered):
        if first.night != second.night:
            other_night += 1
            continue
        step = math.hypot(second.column_px - first.column_px, second.row_px - first.row_px)
        if step > MAXIMUM_PLAUSIBLE_STEP_PX:
            degenerate += 1
            continue
        steps.append(step)
    counts = {"used_count": len(steps), "degenerate_count": degenerate, "other_night_count": other_night}

    identity_count = sum(
        1 for shift in ordered if math.hypot(shift.column_px, shift.row_px) < IDENTITY_TOLERANCE_PX
    )
    pairs_in_nights = len(steps) + degenerate
    if ordered and identity_count / len(ordered) > MAXIMUM_IDENTITY_SHARE:
        reason = (
            f"{identity_count} of {len(ordered)} frames have exactly the identity transform (more than "
            f"{MAXIMUM_IDENTITY_SHARE:.0%}), so most of the run was not registered and the steps are not "
            "pointings"
        )
        return StepSummary(None, None, None, None, **counts, unusable_reason=reason, is_suspect=True)
    if pairs_in_nights and degenerate / pairs_in_nights > MAXIMUM_DEGENERATE_SHARE:
        reason = (
            f"{degenerate} of {pairs_in_nights} pairs of one night have a step longer than "
            f"{MAXIMUM_PLAUSIBLE_STEP_PX:.0f} px (more than {MAXIMUM_DEGENERATE_SHARE:.0%}), so the "
            "transforms are not pointings"
        )
        return StepSummary(None, None, None, None, **counts, unusable_reason=reason, is_suspect=True)
    if len(steps) < MINIMUM_STEPS:
        reason = (
            f"fewer than {MINIMUM_STEPS} usable pairs of neighboring exposures of one night among "
            f"{len(series.shifts)} registered frames with a capture time"
        )
        return StepSummary(None, None, None, None, **counts, unusable_reason=reason)
    values = np.array(steps)
    median = float(np.median(values))
    mad = float(np.median(np.abs(values - median)))
    threshold = median + STEP_JUMP_SIGMAS * max(MAD_TO_SIGMA * mad, MINIMUM_STEP_SCALE_PX)
    residual_steps: list[float] = []
    night_fits = fit_nights(series) if fits is None else fits
    for _, column_residuals, row_residuals, _ in night_fits:
        residual_steps.extend(np.hypot(np.diff(column_residuals), np.diff(row_residuals)).tolist())
    after_median = float(np.median(residual_steps)) if len(residual_steps) >= MINIMUM_STEPS else None
    return StepSummary(
        median_px=median,
        mad_px=mad,
        jump_count=int(np.count_nonzero(values > threshold)),
        jump_threshold_px=float(threshold),
        **counts,
        after_drift_median_px=after_median,
        after_drift_count=len(residual_steps) if after_median is not None else 0,
    )


def drift_summary(series: ShiftSeries, fits: Sequence[_NightFit] | None = None) -> DriftSummary:
    """Measure the steady walk of the pointing through the nights of a stack.

    Each night with `MINIMUM_FRAMES_FOR_DRIFT_FIT` or more frames is fitted
    with a straight line (`_fit_night`). The rate is the mean of the nights'
    drift lengths, weighted by their frame counts. The axis and the extent
    come only from nights whose drift is at least `DRIFT_SIGNIFICANCE` times
    its error, because the axis of a drift that is lost in the noise is
    chosen by the noise. The nights' axes are averaged on twice their angle
    (so an axis and its opposite agree), weighted by frame count.

    Parameters
    ----------
    series : `ShiftSeries`
        The pointings of the registered frames.
    fits : `Sequence` of `tuple`, optional
        The nights' straight-line fits (`fit_nights`). Fitted here if left
        out.

    Returns
    -------
    summary : `DriftSummary`
        The rate, axis and extent, each `None` (with a reason for the axis)
        when it cannot be named. A rate below `DRIFT_SIGNIFICANCE` times its
        error carries the warning that the noise has made it too large.
    """
    fits = fit_nights(series) if fits is None else list(fits)
    if not fits:
        reason = f"no night has {MINIMUM_FRAMES_FOR_DRIFT_FIT} registered frames that a straight line fits"
        return DriftSummary(None, None, None, reason, None, rate_reason=reason)
    nights = tuple(fit[0] for fit in fits)
    weights = np.array([night.frame_count for night in nights], dtype=float)
    rates = np.array([night.rate_px_per_hour for night in nights])
    errors = np.array([night.rate_error_px_per_hour for night in nights])
    rate = float(np.sum(weights * rates) / weights.sum())
    rate_error = float(math.sqrt(np.sum((weights * errors) ** 2)) / weights.sum())
    rate_reason = ""
    if rate < DRIFT_SIGNIFICANCE * rate_error:
        rate_reason = (
            f"the rate is {rate / rate_error if rate_error > 0 else 0.0:.1f} times its error, below "
            f"{DRIFT_SIGNIFICANCE:.0f}: the length of a slope is never below zero, so the noise makes it "
            "too large (about 1.25 errors when there is no drift)"
        )
    significant = [fit for fit in fits if fit[0].significant]
    if not significant:
        return DriftSummary(
            rate,
            rate_error,
            None,
            f"no night drifts by {DRIFT_SIGNIFICANCE:.0f} times the scatter of its own straight-line fit, "
            "so the axis would be set by noise",
            None,
            nights,
            rate_reason,
        )
    angles = np.array([
        math.atan2(fit[0].row_rate_px_per_hour, fit[0].column_rate_px_per_hour) for fit in significant
    ])
    significant_weights = np.array([fit[0].frame_count for fit in significant], dtype=float)
    mean_vector = np.sum(significant_weights * np.exp(2j * angles)) / significant_weights.sum()
    agreement = float(abs(mean_vector))
    if agreement < MINIMUM_AXIS_AGREEMENT:
        return DriftSummary(
            rate,
            rate_error,
            None,
            f"the drift axes of the nights disagree (agreement {agreement:.2f}, below "
            f"{MINIMUM_AXIS_AGREEMENT:.1f}), so no one axis names the smear",
            None,
            nights,
            rate_reason,
        )
    axis = 0.5 * math.atan2(mean_vector.imag, mean_vector.real)
    direction_deg = math.degrees(axis) % 180.0
    unit_column, unit_row = math.cos(axis), math.sin(axis)
    extents = []
    for _, _, _, kept in significant:
        along = [frame.column_px * unit_column + frame.row_px * unit_row for frame in kept]
        extents.append(max(along) - min(along))
    return DriftSummary(rate, rate_error, direction_deg, "", float(max(extents)), nights, rate_reason)


def _image_size(reference_path: str | None) -> tuple[int, int] | None:
    """Read the (width, height) of the frames Siril registered.

    Parameters
    ----------
    reference_path : `str` or `None`
        The registration reference frame.

    Returns
    -------
    size : `tuple` [`int`, `int`] or `None`
        The pixel size from the frame's header, or `None` when there is no
        reference frame or its header cannot be read.
    """
    if not reference_path:
        return None
    try:
        header = read_header(reference_path)
        width, height = int(header["NAXIS1"]), int(header["NAXIS2"])
    except (*FITS_READ_ERRORS, *DATA_ERRORS, KeyError):
        return None
    return (width, height) if width > 0 and height > 0 else None


def _missing_rows(context: PipelineRunContext, subject: Any, reason: str) -> list[QualityMeasurement]:
    """Build a not-measured row for every pointing metric.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.
    reason : `str`
        Why nothing was measured.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row for each metric, not measured, with the reason.
    """
    return [
        build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=reason)
        for name in _SPEC_BY_NAME_ORDER
    ]


_STEP_NAMES = (
    STACKING_REGISTRATION_STEP_MEDIAN_PX,
    STACKING_REGISTRATION_STEP_MAD_PX,
    STACKING_REGISTRATION_STEP_JUMP_COUNT,
    STACKING_REGISTRATION_STEP_DEGENERATE_COUNT,
    STACKING_REGISTRATION_STEP_AFTER_DRIFT_MEDIAN_PX,
)
_SPEC_BY_NAME_ORDER = (
    *_STEP_NAMES,
    STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR,
    STACKING_REGISTRATION_DRIFT_DIRECTION_DEG,
    STACKING_REGISTRATION_DRIFT_EXTENT_PX,
)


def _step_rows(
    steps: StepSummary, context: PipelineRunContext, subject: Any, note: str
) -> list[QualityMeasurement]:
    """Build the five step rows.

    Parameters
    ----------
    steps : `StepSummary`
        The steps.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.
    note : `str`
        Where the pointing was taken and how many frames were used.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        The median, spread, jump count, degenerate count and median after
        the drift. When the steps are unusable every row is not measured, with
        the reason, except that a count of degenerate pairs is kept when the
        transforms were refused as not being pointings (it is the evidence).
    """
    if steps.unusable_reason:
        rows = [
            build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=steps.unusable_reason)
            for name in _STEP_NAMES
        ]
        if steps.is_suspect:
            rows[3] = build_measurement(
                _SPEC_BY_NAME[STACKING_REGISTRATION_STEP_DEGENERATE_COUNT],
                float(steps.degenerate_count),
                context,
                subject,
                provenance=f"{note}; the step rows were refused: {steps.unusable_reason}",
            )
        return rows
    detail = (
        f"{note}; {steps.used_count} pairs used, {steps.degenerate_count} longer than "
        f"{MAXIMUM_PLAUSIBLE_STEP_PX:.0f} px, {steps.other_night_count} across two nights; a jump is a step "
        f"above {steps.jump_threshold_px:.2f} px"
    )
    after_reason = (
        f"fewer than {MINIMUM_STEPS} pairs of neighboring exposures remain in nights that have "
        f"{MINIMUM_FRAMES_FOR_DRIFT_FIT} or more registered frames"
    )
    values = (
        (steps.median_px, steps.used_count, ""),
        (steps.mad_px, steps.used_count, ""),
        (None if steps.jump_count is None else float(steps.jump_count), steps.used_count, ""),
        (float(steps.degenerate_count), None, ""),
        (steps.after_drift_median_px, steps.after_drift_count or None, after_reason),
    )
    return [
        build_measurement(
            _SPEC_BY_NAME[name], value, context, subject, sample_count=count, reason=reason, provenance=detail
        )
        for name, (value, count, reason) in zip(_STEP_NAMES, values, strict=True)
    ]


def pointing_measurements(
    target_frames: Sequence[Any],
    record: RegistrationRecord | None,
    context: PipelineRunContext,
    stack_file_name: str,
    *,
    missing_reason: str = UNMATCHED_REASON,
) -> list[QualityMeasurement]:
    """Build the step and drift rows of a stacking run.

    Parameters
    ----------
    target_frames : `Sequence`
        The frames given to the stacking program (frame records with a path
        and a capture time).
    record : `RegistrationRecord` or `None`
        Siril's registration of the run (see `build_registration_record`), or
        `None` when it could not be matched to the frames. A spectral stack's
        record is used the same way: Siril registers it by shift only, and
        the shift of the middle of the image is that shift.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_file_name : `str`
        The stack's file name, for its subject.
    missing_reason : `str`, optional
        The reason given to every row when ``record`` is `None`.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One row for each of the eight pointing metrics, on the stack's
        subject. A value that cannot be named is not measured, with the
        reason. The reason of a measured row says where in the image the
        pointing was taken.
    """
    subject = context.stack_subject(stack_file_name)
    if record is None:
        return _missing_rows(context, subject, missing_reason)
    if is_already_registered(record):
        return _missing_rows(context, subject, ALREADY_REGISTERED_REASON)
    size = _image_size(record.reference_frame)
    series = shifts_from_registration(
        record.light_paths,
        record.homographies,
        record.included,
        {frame.path: frame.timestamp for frame in target_frames},
        size,
    )
    where = (
        f"pointing taken at the middle of the {size[0]} x {size[1]} px image"
        if series.at_image_center and size is not None
        else "pointing taken at the corner Siril's matrix names (the image size is unknown)"
    )
    if record.is_spectral:
        where += "; spectral stack, registered by shift only"
    where += f"; {len(series.shifts)} registered frames with a capture time"
    fits = fit_nights(series)
    steps = step_summary(series, fits)
    drift = drift_summary(series, fits)
    rows = _step_rows(steps, context, subject, where)
    rate_note = f"; {drift.rate_reason}" if drift.rate_px_per_hour is not None and drift.rate_reason else ""
    rows.append(
        build_measurement(
            _SPEC_BY_NAME[STACKING_REGISTRATION_DRIFT_RATE_PX_PER_HOUR],
            drift.rate_px_per_hour,
            context,
            subject,
            uncertainty=drift.rate_error_px_per_hour,
            sample_count=sum(night.frame_count for night in drift.nights) or None,
            reason=drift.rate_reason,
            provenance=f"{where}; {len(drift.nights)} nights fitted{rate_note}",
        )
    )
    significant_nights = sum(1 for night in drift.nights if night.significant)
    rows.append(
        build_measurement(
            _SPEC_BY_NAME[STACKING_REGISTRATION_DRIFT_DIRECTION_DEG],
            drift.direction_deg,
            context,
            subject,
            reason=drift.direction_reason,
            provenance=f"{where}; {significant_nights} nights drift significantly",
        )
    )
    rows.append(
        build_measurement(
            _SPEC_BY_NAME[STACKING_REGISTRATION_DRIFT_EXTENT_PX],
            drift.extent_px,
            context,
            subject,
            reason=drift.direction_reason,
            provenance=where,
        )
    )
    return rows


def drift_of_runs(
    records: Sequence[RegistrationRecord | None],
    timestamps: Mapping[str, float | None],
    image_size: tuple[int, int] | None,
) -> DriftSummary:
    """Measure the drift over the frames of several runs on one reference.

    The two half stacks of the half-stack difference job are two runs that
    share one registration reference, so their transforms are all relative to
    the same frame. A frame that is in more than one run (the reference frame)
    is counted once.

    Parameters
    ----------
    records : `Sequence` of `RegistrationRecord` or `None`
        Siril's registration of each run.
    timestamps : `Mapping` [`str`, `float` or `None`]
        The capture time of each frame by path.
    image_size : `tuple` [`int`, `int`] or `None`
        The (width, height) of the registered frames.

    Returns
    -------
    summary : `DriftSummary`
        The drift of the frames of all the runs together. A run with no
        record adds no frame.
    """
    paths: list[str] = []
    homographies: list[Sequence[float]] = []
    included: list[bool] = []
    seen: set[str] = set()
    for record in records:
        if record is None:
            continue
        for path, homography, flag in zip(
            record.light_paths, record.homographies, record.included, strict=True
        ):
            if path in seen:
                continue
            seen.add(path)
            paths.append(path)
            homographies.append(homography)
            included.append(flag)
    return drift_summary(shifts_from_registration(paths, homographies, included, timestamps, image_size))
