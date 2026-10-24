"""Measures a camera's gain and read noise from flat and bias frames.

Two numbers describe how a sensor turns light into pixel values:

- The conversion gain, in electrons per ADU (ADU is analog-to-digital unit,
  one step of the stored number). The photon transfer method finds it. Light
  arrives in random particles, so the noise in a bright, even frame follows
  from its brightness: the variance (the squared scatter) of the pixel
  values, counted in electrons, equals the signal, counted in electrons.
  Counted in ADU the same statement is ``variance = signal / gain``, so
  ``gain = signal / variance``.
- The read noise, the electronic noise every frame gets when it is read out.
  A bias frame (a zero-length exposure) shows nothing else.

The method follows the hand measurement behind the ASI 533MM Pro profile.

1. Pair the frames. The difference of two frames taken one after the other
   cancels the vignette, the dust shadows and the pixel-to-pixel sensitivity
   pattern exactly, so it holds only noise. One frame's noise variance is
   half the variance of the difference. Flat frames are paired by the time
   they were taken (the header's ``DATE-OBS``), and only when the two are no
   more than `MAXIMUM_PAIR_GAP_SECONDS` apart, so a pair never spans two
   flat-taking sessions, whose lamp level, dust and focus differ. Two frames
   of different sessions would leave that difference in the "noise".
2. Read noise, from each bias pair: the standard deviation of the
   difference divided by the square root of 2, in ADU. This root-mean-square
   figure is the one the noise model needs. A robust figure from the median
   absolute deviation is also kept for the record, because it ignores hot
   pixels.
3. Gain, from each flat pair: remove the mean of the bias frames, then
   ``gain = signal / (single-frame variance - read variance)``, where the
   read variance is the square of the read noise in ADU.
4. Report the median over the pairs and the pair-to-pair spread.

Everything is measured in the central region of the frame, 1000 by 1000
pixels by default. The center is the part of the sensor least affected by
vignetting and amplifier glow.

Limits worth knowing. A flat brighter than the camera profile's photometric
linearity limit (`photometric_linearity_limit_adu`, the value above which the
sensor stops responding in proportion to the light) is refused, because
photon transfer assumes a linear response: past the limit the variance no
longer tracks the signal and the gain reads wrong. One signal level cannot
show whether the sensor is linear. The full photon transfer curve fits the
slope of variance against signal over many levels. This module measures at the
one level the flats sit at, and records that level so a reader knows where the
gain holds. The variance of a frame also includes the rounding of the stored
value (this sensor stores 14-bit values in steps of 4 ADU). The bias and flat
variances both contain that rounding, and the subtraction in step 3 removes
it.

The pure functions work on frames already in memory. `load_calibration_frame`
reads a file and the facts the checks need from its header.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import numpy as np

from astrometricslib.drivers.calibration_library import header_binning, header_temperature_c
from astrometricslib.drivers.image import AstrometricsImage
from astrometricslib.foundation.errors import NotFoundError, StorageError
from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)

logger = logging.getLogger(__name__)

# The size, in pixels, of the central region measured (height, width). The
# hand measurement behind the ASI 533MM Pro profile used the central
# 1000 x 1000 pixels. A million pixels pin each pair's variance to about
# 0.1% (the relative error of a variance from N samples is sqrt(2 / N)), so
# the pair-to-pair spread is set by the lamp, not the sample size. The
# region is clipped to the sensor for a smaller frame.
CENTRAL_REGION_SHAPE_PIXELS = (1000, 1000)

# The fewest flat pairs a measurement may use. The result is the median of the
# pairs' gains, and the median of five survives two bad pairs (it is the third
# of five sorted values). The standard error of a median of N pairs is about
# 1.25 * sigma / sqrt(N), where sigma is the pair-to-pair spread, so five pairs
# give a standard error of about 0.56 sigma. The spread is not assumed: every
# measurement computes it (`gain_spread_e_per_adu`) and keeps it, with each
# pair's gain, in the saved profile entry, so a reader can judge a result from
# its own pairs. For scale, the hand measurement behind the ASI 533MM Pro
# profile (STK-35) found a spread of 0.05% over 15 pairs. A design estimate,
# checked only against that one data set.
MINIMUM_FLAT_PAIRS = 5

# The fewest bias pairs a measurement may use. Each pair already holds a
# million pixels, so the read noise of one pair is good to about 0.1%.
# Three pairs let the median survive one bad pair, such as one with a
# cosmic-ray hit. The hand measurement used 5. A design estimate.
MINIMUM_BIAS_PAIRS = 3

# The most the sensor temperature may vary across the frames, in degrees C.
# Read noise and gain change slowly with temperature, but a cooled camera's
# set point jitters by a few tenths of a degree. 1.0 C is tighter than the
# 3 C that groups dark frames (DEFAULT_DARK_TEMPERATURE_TOLERANCE_C), since
# this is a precision measurement. A design estimate.
MAXIMUM_TEMPERATURE_SPREAD_C = 1.0

# Two exposures count as the same when they differ by less than this
# fraction. Header exposure times are written to a few digits, so exact
# equality would reject frames of one exposure.
EXPOSURE_RELATIVE_TOLERANCE = 1.0e-3

# The longest time between the two frames of a flat pair, in seconds. Flat
# frames of one session are taken back to back (an Ekos or NINA flat sequence
# runs one frame every few seconds), so a gap of five minutes is already longer
# than any one sequence needs, while the next session is hours or days away. A
# pair this close shows the same lamp, dust and focus; the small drift of the
# lamp inside it is removed by scaling the second frame to the first's level.
# A design estimate: it separates sessions by orders of magnitude, so its exact
# value does not matter, and it has not been tuned on this library's flats.
MAXIMUM_PAIR_GAP_SECONDS = 300.0

# Turns a median absolute deviation into a standard deviation for Gaussian
# noise (the same constant `flat_calibration.py` uses).
MAD_TO_STANDARD_DEVIATION = 1.4826

# The factor between the standard deviation of a difference of two frames
# with equal noise and the noise of one frame.
_PAIR_DIFFERENCE_FACTOR = math.sqrt(2.0)

METHOD_NAME = "measure_sensor_noise"


@dataclass(frozen=True)
class CalibrationFrame:
    """One calibration frame and the facts the noise measurement checks.

    Attributes
    ----------
    pixels : `numpy.ndarray`
        The pixel values in ADU, as a 2-D float array.
    gain_setting : `str`
        The camera gain setting (or ISO), in standard form.
    offset : `str`
        The camera offset, in standard form.
    binning : `str`
        The binning, such as ``"1x1"``.
    exposure_seconds : `float` or `None`
        The exposure time, or `None` when the header gives none.
    temperature_c : `float` or `None`
        The sensor temperature, or `None` when the header gives none.
    path : `str`
        The file the frame came from, for messages. Empty for a frame made
        in memory.
    observed_at : `float` or `None`
        When the exposure started, in seconds since 1970 (UTC), from the
        header's ``DATE-OBS``. `None` when the header gives none or it cannot
        be read.
    """

    pixels: np.ndarray
    gain_setting: str
    offset: str
    binning: str
    exposure_seconds: float | None = None
    temperature_c: float | None = None
    path: str = ""
    observed_at: float | None = None

    @property
    def setting(self) -> tuple[str, str, str]:
        """Give the camera setting the frame was taken at.

        Returns
        -------
        setting : `tuple` [`str`, `str`, `str`]
            Gain setting, offset and binning.
        """
        return (self.gain_setting, self.offset, self.binning)


def _parse_observation_time(value: object) -> float | None:
    """Read a header's ``DATE-OBS`` text as seconds since 1970 (UTC).

    Parameters
    ----------
    value : `object`
        The header value, such as ``"2026-10-03T03:12:11.123"``. A time with
        no zone is taken as UTC, which is what the FITS standard says.

    Returns
    -------
    seconds : `float` or `None`
        The time, or `None` when the value is missing or not a date and time.
    """
    if value is None:
        return None
    try:
        observed = datetime.fromisoformat(str(value).strip())
    except ValueError:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return observed.timestamp()


def load_calibration_frame(path: str) -> CalibrationFrame | None:
    """Read a calibration frame and its setting from a FITS file.

    Parameters
    ----------
    path : `str`
        Path of the FITS file.

    Returns
    -------
    frame : `CalibrationFrame` or `None`
        The frame, or `None` when the file cannot be read or is not a
        single 2-D image.
    """
    try:
        image = AstrometricsImage(path)
        pixels = np.asarray(image.data, dtype=np.float64)
        header = image.header
    except (OSError, ValueError, KeyError, NotFoundError, StorageError) as error:
        logger.debug("Could not read calibration frame '%s': %s", path, error)
        return None
    if pixels.ndim == 3 and pixels.shape[0] == 1:
        pixels = pixels[0]
    if pixels.ndim != 2 or pixels.size == 0:
        return None
    # The same card order the calibration library files frames under.
    gain = header.get("ISOSPEED", header.get("GAIN", "800"))
    offset = header.get("OFFSET", header.get("BLKLEVEL"))
    exposure = header.get("EXPTIME", header.get("EXPOSURE"))
    try:
        exposure_seconds = None if exposure is None else float(exposure)
    except TypeError, ValueError:
        exposure_seconds = None
    return CalibrationFrame(
        pixels=pixels,
        gain_setting=canonical_gain_setting(gain),
        offset=canonical_offset_setting(offset),
        binning=canonical_binning_setting(header_binning(header)),
        exposure_seconds=exposure_seconds,
        temperature_c=header_temperature_c(header),
        path=path,
        observed_at=_parse_observation_time(header.get("DATE-OBS")),
    )


def pair_consecutive(frames: list[CalibrationFrame]) -> list[tuple[CalibrationFrame, CalibrationFrame]]:
    """Pair frames two at a time, in the order given.

    Pairs must not share a frame, or their noise would not be independent.
    Frames taken one after the other make the best pair, so the caller
    sorts them by time first. An odd last frame is left out.

    Parameters
    ----------
    frames : `list` [`CalibrationFrame`]
        The frames, in the order they were taken.

    Returns
    -------
    pairs : `list` [`tuple` [`CalibrationFrame`, `CalibrationFrame`]]
        Frames 0 and 1, 2 and 3, and so on.
    """
    return [(frames[index], frames[index + 1]) for index in range(0, len(frames) - 1, 2)]


@dataclass(frozen=True)
class FramePairing:
    """The pairs made from a set of frames, and what was left out.

    Attributes
    ----------
    pairs : `list` [`tuple` [`CalibrationFrame`, `CalibrationFrame`]]
        The pairs, each of two frames taken within the allowed gap.
    undated_count : `int`
        Frames with no usable ``DATE-OBS``. They cannot be paired by time.
    unpaired_count : `int`
        Dated frames left without a partner because the next frame was too
        far away in time.
    """

    pairs: list[tuple[CalibrationFrame, CalibrationFrame]] = field(default_factory=list)
    undated_count: int = 0
    unpaired_count: int = 0


def pair_by_observation_time(
    frames: list[CalibrationFrame], maximum_gap_seconds: float = MAXIMUM_PAIR_GAP_SECONDS
) -> FramePairing:
    """Pair frames that were taken one after the other, by their time.

    The frames are put in the order they were taken (``DATE-OBS``), not in
    file-name order, and each frame is paired with the next one only when the
    two are at most `maximum_gap_seconds` apart. A frame whose next neighbor
    is farther away starts a new session and is paired with the frame after
    it, or left out if it has none. No frame is in two pairs, or their noise
    would not be independent.

    Parameters
    ----------
    frames : `list` [`CalibrationFrame`]
        The frames, in any order.
    maximum_gap_seconds : `float`, optional
        The longest allowed time between the two frames of a pair. Defaults
        to `MAXIMUM_PAIR_GAP_SECONDS`.

    Returns
    -------
    pairing : `FramePairing`
        The pairs and the counts of frames that could not be paired.
    """
    dated = sorted((frame for frame in frames if frame.observed_at is not None), key=lambda f: f.observed_at)
    pairs = []
    index = 0
    while index + 1 < len(dated):
        if dated[index + 1].observed_at - dated[index].observed_at <= maximum_gap_seconds:
            pairs.append((dated[index], dated[index + 1]))
            index += 2
        else:
            index += 1
    return FramePairing(
        pairs=pairs,
        undated_count=len(frames) - len(dated),
        unpaired_count=len(dated) - 2 * len(pairs),
    )


def _central_slices(shape: tuple[int, int], region_shape: tuple[int, int]) -> tuple[slice, slice]:
    """Find the central region of a frame, clipped to the frame.

    Returns
    -------
    slices : `tuple` [`slice`, `slice`]
        The row and column slices.
    """
    row_count = min(region_shape[0], shape[0])
    column_count = min(region_shape[1], shape[1])
    row_start = (shape[0] - row_count) // 2
    column_start = (shape[1] - column_count) // 2
    return (slice(row_start, row_start + row_count), slice(column_start, column_start + column_count))


def make_unmeasured_result(
    camera_id: str, setting: tuple[str, str, str], reason: str, flat_frame_count: int, bias_frame_count: int
) -> SensorNoiseMeasurement:
    """Make a result that says why nothing was measured.

    Returns
    -------
    result : `SensorNoiseMeasurement`
        A ``not_measured`` result holding no numbers.
    """
    return SensorNoiseMeasurement(
        status=MeasurementStatus.NOT_MEASURED,
        reason=reason,
        camera_id=camera_id,
        gain_setting=setting[0],
        offset=setting[1],
        binning=setting[2],
        flat_frame_count=flat_frame_count,
        bias_frame_count=bias_frame_count,
    )


def _find_condition_problem(
    flat_frames: list[CalibrationFrame], bias_frames: list[CalibrationFrame]
) -> str | None:
    """Check that the frames share one setting, exposure, temperature and size.

    Returns
    -------
    problem : `str` or `None`
        One sentence saying what differs, or `None` when the frames agree.
    """
    every_frame = [*flat_frames, *bias_frames]
    settings = {frame.setting for frame in every_frame}
    if len(settings) > 1:
        listed = ", ".join(f"gain {g} / offset {o} / {b}" for g, o, b in sorted(settings))
        return f"the frames were taken at more than one camera setting ({listed})"
    if len({frame.pixels.shape for frame in every_frame}) > 1:
        return "the frames do not all have the same size"
    exposures = [frame.exposure_seconds for frame in flat_frames if frame.exposure_seconds is not None]
    if exposures and any(
        not math.isclose(value, exposures[0], rel_tol=EXPOSURE_RELATIVE_TOLERANCE) for value in exposures
    ):
        listed = ", ".join(f"{value:g} s" for value in sorted(set(exposures)))
        return f"the flat frames were not all taken with one exposure ({listed})"
    temperatures = [frame.temperature_c for frame in every_frame if frame.temperature_c is not None]
    if temperatures and max(temperatures) - min(temperatures) > MAXIMUM_TEMPERATURE_SPREAD_C:
        return (
            f"the frame temperatures span {min(temperatures):.1f} to {max(temperatures):.1f} C, "
            f"more than {MAXIMUM_TEMPERATURE_SPREAD_C:g} C"
        )
    return None


def measure_sensor_noise(
    flat_pairs: list[tuple[CalibrationFrame, CalibrationFrame]],
    bias_pairs: list[tuple[CalibrationFrame, CalibrationFrame]],
    region_shape: tuple[int, int] = CENTRAL_REGION_SHAPE_PIXELS,
    *,
    camera_id: str = "",
    measured_on: date,
    linearity_limit_adu: float | None = None,
) -> SensorNoiseMeasurement:
    """Measure the gain and read noise from flat pairs and bias pairs.

    The frames must share one camera setting, one flat exposure and one
    temperature (within `MAXIMUM_TEMPERATURE_SPREAD_C`). If they do not, or
    there are fewer than `MINIMUM_FLAT_PAIRS` flat pairs or
    `MINIMUM_BIAS_PAIRS` bias pairs, nothing is measured and the result says
    why. Every bias frame in the pairs goes into the mean bias that is
    removed from the flats.

    A flat pair whose second frame is slightly brighter or dimmer than the
    first (a lamp that drifted) has the second frame scaled to the first's
    level before the difference, so the drift does not read as noise. A pair
    whose noise is not above the read noise gives no gain and is left out.

    A flat pair is left out when either frame's central region has a mean
    pixel value above `linearity_limit_adu` (the camera profile's photometric
    linearity limit), because past that level the sensor no longer responds
    in proportion to the light and the photon transfer gain reads wrong. If
    fewer than `MINIMUM_FLAT_PAIRS` pairs remain, nothing is measured and the
    reason names the limit.

    One signal level cannot show whether the sensor responds linearly. The
    level used is in the result.

    Parameters
    ----------
    flat_pairs : `list` [`tuple` [`CalibrationFrame`, `CalibrationFrame`]]
        Pairs of flat frames, each pair taken one after the other.
    bias_pairs : `list` [`tuple` [`CalibrationFrame`, `CalibrationFrame`]]
        Pairs of bias frames.
    region_shape : `tuple` [`int`, `int`], optional
        The height and width of the central region measured, clipped to the
        frame. Defaults to `CENTRAL_REGION_SHAPE_PIXELS`.
    camera_id : `str`, optional
        The camera's name, copied into the result.
    measured_on : `datetime.date`
        The date written in the provenance sentence. The caller gives it,
        because pipeline code does not read the wall clock.
    linearity_limit_adu : `float`, optional
        The pixel value above which the sensor is not linear, from the camera
        profile. `None` (the default) applies no limit.

    Returns
    -------
    result : `SensorNoiseMeasurement`
        The gain, read noise, spreads and counts, or a ``not_measured``
        result with a reason.
    """
    flat_frames = [frame for pair in flat_pairs for frame in pair]
    bias_frames = [frame for pair in bias_pairs for frame in pair]
    first_frame = (flat_frames or bias_frames or [None])[0]
    setting = first_frame.setting if first_frame is not None else ("", "", "")
    counts = (len(flat_frames), len(bias_frames))
    if len(flat_pairs) < MINIMUM_FLAT_PAIRS:
        reason = (
            f"{len(flat_pairs)} flat pair(s) found; at least {MINIMUM_FLAT_PAIRS} are needed "
            f"({2 * MINIMUM_FLAT_PAIRS} flat frames)"
        )
        return make_unmeasured_result(camera_id, setting, reason, *counts)
    if len(bias_pairs) < MINIMUM_BIAS_PAIRS:
        reason = (
            f"{len(bias_pairs)} bias pair(s) found; at least {MINIMUM_BIAS_PAIRS} are needed "
            f"({2 * MINIMUM_BIAS_PAIRS} bias frames)"
        )
        return make_unmeasured_result(camera_id, setting, reason, *counts)
    slices = _central_slices(first_frame.pixels.shape, region_shape)
    linearity_notes: list[str] = []
    if linearity_limit_adu is not None:
        linear_pairs = [
            pair
            for pair in flat_pairs
            if max(float(np.mean(frame.pixels[slices])) for frame in pair) <= linearity_limit_adu
        ]
        refused_count = len(flat_pairs) - len(linear_pairs)
        if refused_count:
            levels = [float(np.mean(frame.pixels[slices])) for pair in flat_pairs for frame in pair]
            linearity_notes.append(
                f"{refused_count} flat pair(s) were left out because their mean level is above the "
                f"camera profile's photometric linearity limit of {linearity_limit_adu:.0f} ADU "
                f"(the flats span {min(levels):.0f} to {max(levels):.0f} ADU)"
            )
        flat_pairs = linear_pairs
        flat_frames = [frame for pair in flat_pairs for frame in pair]
        if len(flat_pairs) < MINIMUM_FLAT_PAIRS:
            reason = (
                f"{len(flat_pairs)} flat pair(s) are below the camera profile's photometric linearity "
                f"limit of {linearity_limit_adu:.0f} ADU; at least {MINIMUM_FLAT_PAIRS} are needed. "
                f"{linearity_notes[0] if linearity_notes else ''}".strip()
            )
            return make_unmeasured_result(camera_id, setting, reason, *counts)
    problem = _find_condition_problem(flat_frames, bias_frames)
    if problem is not None:
        return make_unmeasured_result(camera_id, setting, problem, *counts)

    region_shape_pixels = [slices[0].stop - slices[0].start, slices[1].stop - slices[1].start]
    mean_bias = np.mean([frame.pixels[slices] for frame in bias_frames], axis=0)

    pair_read_noises_adu = []
    pair_robust_read_noises_adu = []
    for first, second in bias_pairs:
        difference = first.pixels[slices] - second.pixels[slices]
        pair_read_noises_adu.append(float(np.std(difference) / _PAIR_DIFFERENCE_FACTOR))
        mad = float(np.median(np.abs(difference - np.median(difference))))
        pair_robust_read_noises_adu.append(MAD_TO_STANDARD_DEVIATION * mad / _PAIR_DIFFERENCE_FACTOR)
    read_noise_adu = float(np.median(pair_read_noises_adu))
    read_variance_adu2 = read_noise_adu**2

    pair_gains: list[float] = []
    pair_signals: list[float] = []
    for first, second in flat_pairs:
        first_pixels = first.pixels[slices] - mean_bias
        second_pixels = second.pixels[slices] - mean_bias
        first_signal = float(np.mean(first_pixels))
        second_signal = float(np.mean(second_pixels))
        if first_signal <= 0.0 or second_signal <= 0.0:
            continue
        difference = first_pixels - second_pixels * (first_signal / second_signal)
        single_frame_variance = float(np.var(difference)) / 2.0
        shot_variance = single_frame_variance - read_variance_adu2
        if shot_variance <= 0.0:
            continue
        pair_gains.append(first_signal / shot_variance)
        pair_signals.append(first_signal)
    if len(pair_gains) < MINIMUM_FLAT_PAIRS:
        reason = (
            f"only {len(pair_gains)} of {len(flat_pairs)} flat pairs gave a gain (the others were dark or "
            f"no noisier than the read noise); at least {MINIMUM_FLAT_PAIRS} are needed"
        )
        return make_unmeasured_result(camera_id, setting, reason, *counts)

    gain = float(np.median(pair_gains))
    spread = float(np.std(pair_gains, ddof=1))
    signal = float(np.median(pair_signals))
    temperatures = [f.temperature_c for f in [*flat_frames, *bias_frames] if f.temperature_c is not None]
    temperature_c = float(np.mean(temperatures)) if temperatures else None
    exposures = [f.exposure_seconds for f in flat_frames if f.exposure_seconds is not None]
    exposure_seconds = float(np.mean(exposures)) if exposures else None
    date_text = measured_on.isoformat()
    conditions = []
    if exposure_seconds is not None:
        conditions.append(f"{exposure_seconds:g} s")
    if temperature_c is not None:
        conditions.append(f"{temperature_c:.0f} C")
    conditions_text = f" ({', '.join(conditions)})" if conditions else ""
    provenance = (
        f"Photon transfer on {len(pair_gains)} flat pairs{conditions_text}, central "
        f"{region_shape_pixels[0]} x {region_shape_pixels[1]} px, bias-subtracted, one signal level of "
        f"{signal:.0f} ADU: signal over the single-frame variance minus the read variance, median of the "
        f"pairs (spread {spread:.4f}). Read noise: standard deviation of the difference of "
        f"{len(bias_pairs)} bias pairs over sqrt(2), {read_noise_adu:.2f} ADU. "
        f"Measured {date_text} by {METHOD_NAME}."
    )
    return SensorNoiseMeasurement(
        status=MeasurementStatus.MEASURED,
        camera_id=camera_id,
        gain_setting=setting[0],
        offset=setting[1],
        binning=setting[2],
        temperature_c=temperature_c,
        exposure_seconds=exposure_seconds,
        gain_e_per_adu=gain,
        gain_spread_e_per_adu=spread,
        read_noise_adu=read_noise_adu,
        read_noise_adu_robust=float(np.median(pair_robust_read_noises_adu)),
        read_noise_e=read_noise_adu * gain,
        signal_level_adu=signal,
        region_shape_pixels=region_shape_pixels,
        flat_pair_count=len(pair_gains),
        bias_pair_count=len(bias_pairs),
        pair_gains_e_per_adu=pair_gains,
        pair_read_noises_adu=pair_read_noises_adu,
        flat_frame_count=len(flat_frames),
        bias_frame_count=len(bias_frames),
        provenance=provenance,
        notes=linearity_notes,
    )
