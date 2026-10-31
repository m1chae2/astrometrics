"""Measure one raw light frame and return its quality measurements.

`measure_frame` reads the pixels of one frame once and returns one
`QualityMeasurement` for every frame metric declared in `metrics.py`. It writes
nothing: the caller stores the result with `record_measurements`.

What is measured, in plain words:

- The width of the stars. Up to about 200 isolated, unsaturated stars spread
  on a grid over the field are fitted with a Moffat profile (a bright core with
  slowly fading wings, closer to a real star than a Gaussian). The frame's
  value is the median over the stars, with a bootstrap uncertainty. No star is
  dropped for being stretched, so a trailed frame reads wide, not sharp.
- The elongation of the stars (long axis divided by short axis) and the
  direction of the long axis, from the second moments of the same stars. The
  direction comes from the average of the stars' ellipticity components, so a
  round star, whose direction is only noise, counts for little.
- The sky brightness in electrons (a sigma-clipped mean, found with the same
  estimator as the bias level), the expected photon noise of that sky, and the
  read noise of the camera at the frame's setting. The ratio of the two noises
  is never stored. The sky includes dark current and amp glow; for a camera
  with no recorded sensor temperature it is stored as ``assumed``.
- The temperatures the frame recorded, and the metrics the library already
  measured (background level, saturated fraction, Gaussian star width of the
  brightest stars, count of bright regions).

A value that cannot be measured comes back with the status ``not_measured`` and
a reason. It is never replaced by a default number.

One-shot-color frames: a frame stored as separate color planes is measured per
plane, and the plane's name goes on the subject. A raw mosaic (one plane with a
color filter array, header card ``BAYERPAT``) has its star shapes measured on
2 x 2 super-pixels (the sum of each red, green, green, blue cell), so the
Bayer pattern does not bend the second moments or the angle; the widths are
given back in original pixels. Its sky and background use all color pixels
mixed. Its reasons say so.

A time budget keeps one slow frame from stalling a batch. It counts the CPU
time of the measuring thread, not the wall clock, so time spent waiting for
Python's global lock, for the disk or for other programs does not use it up. It
is checked between the steps; when it has run out, the remaining slow steps
(the star detections and fits) come back ``not_measured`` and the quick
header and background metrics are still returned.
"""

import hashlib
import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from functools import cache

import numpy as np
from astropy.io import fits
from astropy.stats import SigmaClip, sigma_clipped_stats
from photutils.background import Background2D, MedianBackground
from scipy import optimize
from scipy.spatial import cKDTree

from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.models.camera_profile import CameraProfile, ProvenancedValue, ProvenanceKind
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.quality.frame_statistics import (
    BOOTSTRAP_RESAMPLE_COUNT,
    BOOTSTRAP_SEED,
    bootstrap_median_error,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_BACKGROUND_LEVEL,
    FRAME_DETECTED_REGION_COUNT,
    FRAME_FOCUSER_TEMPERATURE,
    FRAME_METRICS,
    FRAME_READ_NOISE_E,
    FRAME_SATURATED_PIXEL_FRACTION,
    FRAME_SENSOR_TEMPERATURE,
    FRAME_SKY_NOISE_E,
    FRAME_SKY_RATE_E_PER_S_PER_PX,
    FRAME_STAR_ELONGATION,
    FRAME_STAR_ELONGATION_ANGLE_DEG,
    FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
    FRAME_STAR_WIDTH_EFFECTIVE_PX,
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
    FRAME_STAR_WIDTH_REGION_PX,
    REGION_CENTER,
    REGION_XHIGH_YHIGH,
    REGION_XHIGH_YLOW,
    REGION_XLOW_YHIGH,
    REGION_XLOW_YLOW,
)
from astrometricslib.pipelines.shared.quality.raw_frame_check import detect_stars
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.pipelines.shared.star_finding import find_sources_above_background
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

# How much CPU time one frame may use before the slow steps are skipped, in
# seconds. It counts the CPU time of the measuring thread
# (`time.thread_time`), not the wall clock: on real data (M 57, 61 frames, four
# threads, three test suites running) a wall-clock budget of 60 s cut 46 of 61
# frames short, because the threads waited for Python's global lock and for
# the other programs, which is time the frame did not use. Timed on this
# machine (12 cores) on the two 3008 x 3008 ASI533MM golden M 13 frames
# (M_13_Light_Luminance_019 and _020), 8 frames per row, 2026-10-22:
#
#   one at a time (one thread)        13.1 to 15.1 s of CPU time per frame
#   four in worker processes          20.3 to 23.0 s
#   four in worker threads (worst)    23.3 to 27.5 s
#
# The CPU time rises with the number of frames running at once because they
# share memory bandwidth and, for threads, fight over the global lock. The
# budget is 60 s: 4 times the lone-frame time and 2.2 times the worst measured
# case, so a normal frame never reaches it on a busy machine, and a frame that
# needs several times the normal work (a larger sensor, a crowded field) is cut
# off instead of stalling a batch. The budget is checked between the steps, not
# inside one, so one step can run past it. A frame that is cut off is not
# marked as measured and is tried again on the next run. Register row STK-D22
# holds the numbers.
FRAME_MEASUREMENT_TIME_BUDGET_SECONDS = 60.0

# The number of grid cells over the field. One star is taken from each cell, so
# this is the largest sample (the plan asks for up to about 200). 14 x 14 = 196
# cells give a star for each 0.5 percent of the frame area, which is
# enough to see a tilt or a coma pattern across the field and few enough that
# the fits finish in seconds.
MOFFAT_GRID_CELL_COUNT = 196

# The most candidates tried in one cell before the cell is given up. The
# brightest star of a cell is tried first; when its fit is refused the next
# brightest is tried. Three keeps a cell with one odd star from being empty
# without letting a hopeless cell cost a dozen fits.
MOFFAT_CANDIDATES_PER_CELL = 3

# Half the side, in pixels, of the box a star is fitted in (a 21 x 21 box). The
# box must hold the core and the start of the wings: 10 px is about 1.4 FWHM of
# a 7 px star and 3.3 FWHM of a 3 px star. A constant background in the fit
# absorbs the wings beyond the box, so a star wider than about 7 px reads a
# little narrow. It matches `FWHM_FIT_BOX_RADIUS_PX` of the Gaussian fit in
# `astrometry/pre_processing/fwhm.py`.
MOFFAT_FIT_BOX_HALF_WIDTH_PX = 10

# A star is isolated when no other detected source lies within this many pixels
# of it. The fit box reaches 10 px (14 px along its diagonal), and a neighbor
# just outside would still add its wing to the box corners. 14 px puts every
# neighbor's core beyond the corner of the box.
MOFFAT_ISOLATION_RADIUS_PX = 14.0

# A star is skipped when any pixel of its fit box reaches this fraction of the
# camera's usable top level (the smaller of the saturation threshold and the
# photometric linearity limit). Near the top the sensor stops responding in
# proportion to the light and the core flattens, which widens the fit. 0.9
# leaves a 10 percent margin; `FWHM_SATURATION_FRACTION` in `fwhm.py` uses 0.95
# on a stack, and a raw frame needs more room because a bright pixel's noise
# can push it over the line.
MOFFAT_SATURATION_MARGIN_FRACTION = 0.9

# Each pixel is modeled as the average of this many by this many sub-samples of
# the continuous profile, which stands in for integrating the profile over the
# pixel's area. With 3 x 3 the model error on the fitted width is below 1
# percent for stars of 2 px FWHM and above (checked on synthetic stars, see the
# tests); 1 x 1 reads 2 px stars about 5 percent wide.
MOFFAT_SUBPIXEL_FACTOR = 3

# The Moffat power index beta. A first pass fits it for every star; the median
# of those values is then held fixed for a second pass. Beta and the scale
# radius trade off against each other, so a free beta makes a faint star's
# width noisy; a shared beta removes that noise and still lets the frame's own
# wing shape decide the value. The limits keep a fit from wandering. The total
# flux of a Moffat profile, proportional to 1 / (beta - 1), grows without bound
# as beta falls to 1 and is infinite at beta <= 1, so beta must stay above 1.
# The lower limit of 1.2 is not that mathematical edge: it keeps the profile's
# wings from carrying five times the light of a beta of 2 (same amplitude and
# scale radius), which a 21 x 21 px fit box can hardly tell apart from the sky
# background because its constant background term absorbs the wings. It is a
# design choice, not a measured value. Above 8 the profile is a Gaussian to
# within the fit's noise.
MOFFAT_BETA_MINIMUM = 1.2
MOFFAT_BETA_MAXIMUM = 8.0
MOFFAT_BETA_START = 3.0

# A fit is kept only if it explains this fraction of the variance in its box (R
# squared). Same value as `FWHM_MINIMUM_FIT_R_SQUARED` in `fwhm.py`, which was
# chosen on the Bubble Nebula stack. It rejects stars with a bright neighbor
# or a cosmic ray in the box.
MOFFAT_MINIMUM_R_SQUARED = 0.8

# The fitted center must stay within this many pixels of the detected one; a
# fit that wanders further has locked onto a neighbor. Same as
# `FWHM_MAXIMUM_CENTRE_SHIFT_PX` in `fwhm.py`.
MOFFAT_MAXIMUM_CENTER_SHIFT_PX = 2.5

# A fitted width outside this range (in pixels, FWHM) is a fit gone wrong, not
# a star: a cosmic ray or hot pixel is narrower than 0.8 px, and a width above
# 25 px does not fit in the box at all.
MOFFAT_MINIMUM_FWHM_PX = 0.8
MOFFAT_MAXIMUM_FWHM_PX = 25.0

# The fewest stars that give a median and a bootstrap uncertainty worth
# storing. With fewer, a resample has too few distinct medians to say anything.
MOFFAT_MINIMUM_STAR_COUNT = 5

# The aperture the second moments are taken in, as a multiple of the star's
# fitted FWHM, and its limits in pixels. 1.5 FWHM holds about 90 percent of the
# light of a Moffat star with beta = 3 and is small enough to keep neighbors
# out. The window is elliptical, following the star's first-pass shape (see
# `_measure_second_moments`), so a trailed star is not cut short.
MOMENT_APERTURE_FWHM_MULTIPLE = 1.5
MOMENT_APERTURE_MINIMUM_RADIUS_PX = 3.0

# The blur a square pixel adds to a star, as a multiple of the pixel side
# squared (the "c" of the effective star width). A star is the sky's image
# convolved (smeared) with the pixel's square sensitive area. The variance of
# a position spread evenly over a side p is p^2 / 12, and variances add under
# convolution, so for a Gaussian-like core of width sigma the effective
# sigma^2 is sigma^2 + p^2 / 12 along each axis. The FWHM of a Gaussian is
# 2 sqrt(2 ln 2) sigma, so FWHM_eff^2 = FWHM^2 + 8 ln 2 * p^2 / 12 =
# FWHM^2 + (2 ln 2 / 3) * p^2, and the multiple is 2 ln 2 / 3 = 0.4621 (the
# decimal 2.3548^2 / 12 gives the same). It is a theoretical value, not fitted
# (user decision of 2026-10-23, science plan section 2.1); the register entry
# AST-18 states what the Gaussian-core assumption leaves out.
PIXEL_BLUR_FWHM_SQUARED_FACTOR = 2.0 * math.log(2.0) / 3.0

# The star-width regions of the field. The field is cut into the same 14 x 14
# cells the stars are chosen from (`MOFFAT_GRID_CELL_COUNT`). The center region
# is the middle 4 x 4 cells and each corner region is the 4 x 4 cells at that
# corner, so the five regions have the same area (16 of 196 cells, 8 percent
# of the frame each) and hold about the same number of stars. Four cells is
# the smallest block that holds enough stars to give a median with a
# bootstrap error (the grid takes at most one star per cell, so a block holds
# at most 16 and usually 5 to 12 in a sparse field). In every name below, x is
# the column and y is the array row, with row 0 first (see
# `metrics.REGION_XLOW_YLOW`).
FIELD_GRID_CELLS_PER_SIDE = 14
FIELD_REGION_CELLS_PER_SIDE = 4

# The names of the five regions, in the order the metrics are emitted. A corner
# is named by whether it lies at the low or the high end of the x axis (the
# columns) and of the y axis (the rows), counted from the first pixel, so the
# name does not depend on how a viewer flips the image.
FIELD_REGION_NAMES = (
    REGION_CENTER,
    REGION_XLOW_YLOW,
    REGION_XHIGH_YLOW,
    REGION_XLOW_YHIGH,
    REGION_XHIGH_YHIGH,
)

# What the reason of a pixel metric starts with when the frame could not be
# read at all. The measurement job tests for it so that an unreadable frame (a
# network mount that dropped out, a file still being written) is retried on the
# next run instead of being recorded as measured.
UNREADABLE_FRAME_REASON_PREFIX = "the frame could not be read"

# What the reason of a skipped step starts with when the frame's CPU time
# budget ran out. The measurement job tests for it (see
# `frame_needs_another_try`) so that a frame cut short is measured again on the
# next run instead of being recorded as measured.
TIME_BUDGET_REASON_PREFIX = "CPU time budget"

# The names of the planes of a frame stored as three color planes.
_COLOR_PLANE_NAMES = {3: ("R", "G", "B")}

# The side, in pixels, of the repeating cell of a Bayer color filter array: one
# red, two green and one blue pixel in a 2 x 2 block. The star shape metrics of
# a raw color mosaic are measured on blocks of this size summed into single
# super-pixels. The cell is 2 x 2 for every common Bayer order (RGGB, BGGR,
# GRBG, GBRG), so the factor does not depend on the header's BAYERPAT text.
BAYER_CELL_SIZE_PX = 2

_DEFINITION_VERSIONS = {spec.name: spec.definition_version for spec in FRAME_METRICS}


@cache
def frame_metric_set_fingerprint() -> str:
    """Summarize which frame metrics a measurement covers, and how defined.

    A frame is measured once and then skipped while its file is unchanged. When
    a metric is added to the frame registry, or an existing one is redefined
    (its ``definition_version`` is raised), the frames measured earlier lack
    the new numbers. The measurement job stores this fingerprint on every
    frame it measures and measures a frame again when its stored fingerprint
    differs.

    Returns
    -------
    fingerprint : `str`
        The first 12 hexadecimal digits of the SHA-256 hash of the sorted
        ``name:definition_version`` lines of `FRAME_METRICS`. It does not
        change when the order of the declarations changes.
    """
    lines = sorted(f"{spec.name}:{spec.definition_version}" for spec in FRAME_METRICS)
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:12]


class _Stopwatch:
    """Count the CPU seconds spent on one frame against a budget.

    Parameters
    ----------
    budget_seconds : `float`
        The seconds allowed for the whole frame.
    """

    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        self._start = time.thread_time()

    def elapsed(self) -> float:
        """Give the CPU seconds spent so far by the measuring thread.

        Returns
        -------
        seconds : `float`
            CPU seconds of the thread that made the stopwatch, since it was
            made. Waiting (a sleep, a lock, a disk read) adds nothing. Call it
            from that same thread.
        """
        return time.thread_time() - self._start

    def expired(self) -> bool:
        """Say whether the budget is used up.

        Returns
        -------
        expired : `bool`
            `True` once the elapsed time exceeds the budget.
        """
        return self.elapsed() > self.budget_seconds

    def reason(self) -> str:
        """Give the sentence stored when a step is skipped for lack of time.

        Returns
        -------
        reason : `str`
            "CPU time budget of N s exceeded after M s".
        """
        return (
            f"{TIME_BUDGET_REASON_PREFIX} of {self.budget_seconds:g} s exceeded after {self.elapsed():.1f} s"
        )


@dataclass(frozen=True)
class CandidateRefusals:
    """How many detected sources were refused at each cut, and why.

    A trailed star is more likely to touch a neighbor, to be wide enough to
    reach the saturation margin, or to fit badly. These cuts therefore remove
    the most trailed stars first, and the counts let a reader see how much of
    the field the width and elongation medians rest on.

    Attributes
    ----------
    detected : `int`
        All sources the star finder returned.
    near_edge : `int`
        Sources whose fit box would reach outside the frame.
    near_neighbor : `int`
        Sources with another detected source within
        `MOFFAT_ISOLATION_RADIUS_PX`.
    near_saturation : `int`
        Sources whose fit box reaches `MOFFAT_SATURATION_MARGIN_FRACTION` of
        the camera's usable top level.
    fit_refused : `int`
        Stars tried whose Moffat fit was refused (a poor fit, a center that
        wandered, a width out of range, or a fit that failed), in either pass.
    """

    detected: int = 0
    near_edge: int = 0
    near_neighbor: int = 0
    near_saturation: int = 0
    fit_refused: int = 0

    def describe(self) -> str:
        """Write the counts as one sentence fragment for a reason.

        Returns
        -------
        text : `str`
            For example ``"412 sources detected; refused: 3 near the frame
            edge, 120 with a neighbor within 14 px, ...``.
        """
        return (
            f"{self.detected} sources detected; refused: {self.near_edge} near the frame edge, "
            f"{self.near_neighbor} with a neighbor within {MOFFAT_ISOLATION_RADIUS_PX:g} px, "
            f"{self.near_saturation} near saturation, {self.fit_refused} by the fit"
        )


@dataclass(frozen=True)
class StarWidthSample:
    """The width of one fitted star and where it sits in the frame.

    Attributes
    ----------
    x_px, y_px : `float`
        The star's detected position in original pixels (the column and the
        row, counted from 0). For a raw color mosaic the position found on the
        super-pixels is given back in original pixels.
    width_px : `float`
        The star's Moffat FWHM (the geometric mean of the two axes) in original
        pixels, as it enters the frame's median.
    """

    x_px: float
    y_px: float
    width_px: float


@dataclass(frozen=True)
class StarProfileResult:
    """The star widths, elongations and angles of one plane of one frame.

    Attributes
    ----------
    width_px : `float` or `None`
        Median Moffat FWHM in pixels, or `None` when it could not be measured.
    width_px_uncertainty : `float` or `None`
        Bootstrap standard error of the median, in pixels.
    elongation : `float` or `None`
        Median elongation (long axis divided by short axis, at least 1).
    elongation_uncertainty : `float` or `None`
        Bootstrap standard error of the median elongation.
    angle_deg : `float` or `None`
        Direction of the long axis in degrees, from 0 up to but not including
        180, measured from the image x axis toward the y axis. It comes from
        the average of the stars' ellipticity components, so a round star
        counts for little and a stretched one for much.
    angle_uncertainty_deg : `float` or `None`
        Bootstrap standard deviation of that angle, in degrees.
    angle_coherence : `float` or `None`
        Length of the mean ellipticity vector divided by the mean ellipticity
        of the stars, from 0 (no preferred direction) to 1 (all stars point
        the same way).
    star_count : `int`
        Number of stars the medians are over.
    candidate_count : `int`
        Number of isolated, unsaturated stars that were tried.
    median_beta : `float` or `None`
        The shared Moffat power index of the second pass.
    refusals : `CandidateRefusals`
        How many detected sources each cut refused.
    reason : `str`
        Why the values are missing, or a note on how they were found.
    samples : `tuple` of `StarWidthSample`
        The position and width of every star behind the medians, from the same
        second-pass fits (no second fit is made for the regions).
    field_shape_px : `tuple` of `int`, or `None`
        The (rows, columns) of the measured plane in original pixels, for the
        star-width regions. `None` when nothing was measured.
    """

    width_px: float | None = None
    width_px_uncertainty: float | None = None
    elongation: float | None = None
    elongation_uncertainty: float | None = None
    angle_deg: float | None = None
    angle_uncertainty_deg: float | None = None
    angle_coherence: float | None = None
    star_count: int = 0
    candidate_count: int = 0
    median_beta: float | None = None
    refusals: CandidateRefusals = field(default_factory=CandidateRefusals)
    reason: str = ""
    samples: tuple[StarWidthSample, ...] = ()
    field_shape_px: tuple[int, int] | None = None


@dataclass(frozen=True)
class SkyElectrons:
    """The sky of one plane of one frame, in electrons.

    Attributes
    ----------
    rate_e_per_s_per_px : `float` or `None`
        Sky electrons per second per pixel.
    noise_e : `float` or `None`
        The expected photon (Poisson) noise of the sky in one exposure, per
        pixel: the square root of the sky electrons. It is not the measured
        scatter of the pixels, which also holds read noise, dark current and
        the faint stars.
    background_adu : `float` or `None`
        The sigma-clipped mean of the plane, in ADU.
    reason : `str`
        Why the values are missing, or the source of the gain.
    gain_is_measured : `bool`
        Whether the gain behind the values was measured (not assumed).
    """

    rate_e_per_s_per_px: float | None = None
    noise_e: float | None = None
    background_adu: float | None = None
    reason: str = ""
    gain_is_measured: bool = False


@dataclass
class _FitResult:
    """One accepted Moffat fit of one star."""

    center_x: float
    center_y: float
    fwhm_major_px: float
    fwhm_minor_px: float
    angle_rad: float
    beta: float
    background: float
    r_squared: float


@dataclass
class _Context:
    """What `measure_frame` carries from step to step."""

    frame: FrameRecord
    target_id: str
    run_id: str
    job_id: str | None
    measured_at: datetime
    capture_setting_id: int | None
    path: str
    measurements: list[QualityMeasurement] = field(default_factory=list)

    def add(
        self,
        metric: str,
        *,
        value: float | None = None,
        uncertainty: float | None = None,
        sample_count: int | None = None,
        reason: str = "",
        channel: str | None = None,
        status: MeasurementStatus | None = None,
    ) -> None:
        """Append one measurement; a missing value becomes ``not_measured``.

        Parameters
        ----------
        metric : `str`
            The registered metric name.
        value : `float` or `None`, optional
            The value. `None` (or a non-finite number) makes the measurement
            ``not_measured``, and `reason` must then say why.
        uncertainty : `float` or `None`, optional
            The uncertainty of the value.
        sample_count : `int` or `None`, optional
            The number of samples behind the value.
        reason : `str`, optional
            Why the value is missing, or a note on its source.
        channel : `str` or `None`, optional
            The color plane for a one-shot-color frame.
        status : `MeasurementStatus` or `None`, optional
            ``measured`` or ``assumed``. Default ``measured``.
        """
        if value is None or not math.isfinite(value):
            status = MeasurementStatus.NOT_MEASURED
            value = None
            uncertainty = None
            reason = reason or "the value could not be computed"
        elif uncertainty is not None and not math.isfinite(uncertainty):
            uncertainty = None
        self.measurements.append(
            QualityMeasurement(
                metric=metric,
                definition_version=_DEFINITION_VERSIONS[metric],
                subject=frame_subject(
                    self.path,
                    self.target_id,
                    self.frame.timestamp,
                    capture_setting_id=self.capture_setting_id,
                    channel=channel,
                ),
                status=status or MeasurementStatus.MEASURED,
                value=value,
                uncertainty=uncertainty,
                sample_count=sample_count,
                reason=reason,
                origin=MeasurementOrigin.MEASURED,
                run_id=self.run_id,
                job_id=self.job_id,
                measured_at=self.measured_at,
            )
        )

    def add_not_measured(self, metrics: tuple[str, ...], reason: str, channel: str | None = None) -> None:
        """Append a ``not_measured`` record for each metric.

        Parameters
        ----------
        metrics : `tuple` of `str`
            The metric names.
        reason : `str`
            Why none of them could be measured.
        channel : `str` or `None`, optional
            The color plane, if any.
        """
        for metric in metrics:
            self.add(metric, reason=reason, channel=channel)


def frame_subject_id(path: str, target_id: str) -> str:
    """Name a frame by its target and its place in the target's lights folder.

    The library keeps frames at ``<frames root>/lights/<target>/<telescope>/
    <camera>/<file>``. The id keeps what follows the target folder, so it does
    not change when the frames root moves to another mount.

    Parameters
    ----------
    path : `str`
        The frame's path.
    target_id : `str`
        The target's id.

    Returns
    -------
    subject_id : `str`
        ``"<target_id>/<path below the target's lights folder>"``. A path with
        no ``lights`` folder above a target folder gives the target id and the
        file name only, never the full path.
    """
    parts = os.path.normpath(path).split(os.sep)
    lights_positions = [
        index for index, part in enumerate(parts) if part.lower() == "lights" and index + 2 < len(parts)
    ]
    relative_parts = parts[lights_positions[-1] + 2 :] if lights_positions else parts[-1:]
    return "/".join([target_id, *relative_parts])


def frame_subject(
    path: str,
    target_id: str,
    timestamp: float | None,
    *,
    capture_setting_id: int | None = None,
    channel: str | None = None,
) -> QualitySubject:
    """Describe one frame as the subject of a measurement.

    Every row about a frame is built here: the frame job's rows, the
    quarantine rows, the stacking run's frame weight and rotation rows and the
    astrometry run's target-clipping rows. One builder means they all share
    one subject id, so a reader can join them.

    Parameters
    ----------
    path : `str`
        The frame's path.
    target_id : `str`
        The target's id.
    timestamp : `float` or `None`
        The frame's start time (the frame record's ``timestamp``), which names
        its observing night. `None` leaves the night out.
    capture_setting_id : `int` or `None`, optional
        The frame's capture setting row, when the caller has it.
    channel : `str` or `None`, optional
        The color plane, for a row about one plane.

    Returns
    -------
    subject : `QualitySubject`
        Level ``frame``, with the id of `frame_subject_id`.
    """
    return QualitySubject(
        level=SubjectLevel.FRAME,
        subject_id=frame_subject_id(path, target_id),
        target_id=target_id,
        observing_night_id=observing_night_id(timestamp) if timestamp is not None else None,
        capture_setting_id=capture_setting_id,
        channel=channel,
    )


def _read_planes(path: str) -> tuple[list[tuple[str | None, np.ndarray]], np.ndarray, fits.Header]:
    """Read the pixels once and split them into the planes to measure.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.

    Returns
    -------
    planes : `list` of `tuple`
        ``(channel name or None, 2-D float array)``. A plain frame, or one
        raw mosaic, is one plane with no name. A frame stored as color planes
        gives one entry per plane.
    collapsed : `numpy.ndarray`
        The planes averaged into one image, for the metrics declared on the
        collapsed frame.
    header : `astropy.io.fits.Header`
        The header of the HDU that holds the pixels.

    Raises
    ------
    ValueError
        If the file holds no pixels.
    """
    with open_primary_hdu(path) as hdu:
        data = hdu.data
        header = hdu.header.copy()
    if data is None:
        raise ValueError("the file holds no pixel data")
    data = np.asarray(data, dtype=float)
    collapsed = collapse_to_2d(data)
    if data.ndim == 3 and data.shape[0] in (1, 3, 4):
        stack = list(data)
    elif data.ndim == 3:
        stack = [data[..., index] for index in range(data.shape[-1])]
    else:
        stack = [data]
    if len(stack) == 1:
        return [(None, stack[0])], collapsed, header
    names = _COLOR_PLANE_NAMES.get(len(stack), tuple(f"plane{index}" for index in range(len(stack))))
    return list(zip(names, stack, strict=True)), collapsed, header


def _usable_top_level_adu(camera_profile: CameraProfile | None) -> float | None:
    """Find the pixel value above which the sensor can no longer be trusted.

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile.

    Returns
    -------
    level_adu : `float` or `None`
        The smaller of the saturation threshold (or the clip ceiling, when the
        threshold lies above it and no pixel could reach it) and the
        photometric linearity limit. `None` without a profile.
    """
    if camera_profile is None:
        return None
    return float(camera_profile.usable_peak_level_adu.value)


def _plateau_saturation_level_adu(plane: np.ndarray) -> float | None:
    """Guess a plane's saturation level from its own pixels.

    The brightest value counts as the saturation level only when at least
    three pixels sit at it, the flat top that clipped cores make. A single hot
    pixel does not count. This is the rule `measure_fwhm_from_data` uses, and
    it is the fallback when the frame has no camera profile.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.

    Returns
    -------
    level_adu : `float` or `None`
        The brightest value, or `None` when it is not a plateau.
    """
    brightest = float(np.nanmax(plane))
    return brightest if np.count_nonzero(plane >= brightest) >= 3 else None


def _moffat_fwhm_px(alpha: float, beta: float) -> float:
    """Convert a Moffat scale radius to a full width at half maximum.

    Parameters
    ----------
    alpha : `float`
        The scale radius, in pixels.
    beta : `float`
        The power index.

    Returns
    -------
    fwhm_px : `float`
        ``2 * alpha * sqrt(2 ** (1 / beta) - 1)``.
    """
    return 2.0 * alpha * math.sqrt(2.0 ** (1.0 / beta) - 1.0)


@dataclass
class _SubpixelGrid:
    """The sub-pixel sample positions of one fit box."""

    fine_x: np.ndarray
    fine_y: np.ndarray
    size: int


def _subpixel_grid(size: int) -> _SubpixelGrid:
    """Build the positions at which the profile is sampled inside each pixel.

    Parameters
    ----------
    size : `int`
        The side of the box, in pixels.

    Returns
    -------
    grid : `_SubpixelGrid`
        Pixel-center coordinates of every sub-sample along one axis. Pixel
        ``i`` spans ``i - 0.5`` to ``i + 0.5``.
    """
    factor = MOFFAT_SUBPIXEL_FACTOR
    offsets = (np.arange(factor) + 0.5) / factor - 0.5
    axis = (np.arange(size)[:, None] + offsets[None, :]).ravel()
    return _SubpixelGrid(fine_x=axis[None, :], fine_y=axis[:, None], size=size)


def _moffat_model(parameters: np.ndarray, grid: _SubpixelGrid, fixed_beta: float | None) -> np.ndarray:
    """Evaluate the pixel-integrated elliptical Moffat profile on a box.

    The profile is sampled on a sub-pixel grid and averaged over each pixel.

    Parameters
    ----------
    parameters : `numpy.ndarray`
        ``[amplitude, center_x, center_y, alpha_major, alpha_minor, angle,
        background]`` when `fixed_beta` is given, with ``beta`` inserted before
        ``background`` otherwise.
    grid : `_SubpixelGrid`
        The sample positions.
    fixed_beta : `float` or `None`
        The power index to hold, or `None` when it is a fit parameter.

    Returns
    -------
    model : `numpy.ndarray`
        The model pixel values, shape ``(size, size)``.
    """
    if fixed_beta is None:
        amplitude, center_x, center_y, alpha_major, alpha_minor, angle, beta, background = parameters
    else:
        amplitude, center_x, center_y, alpha_major, alpha_minor, angle, background = parameters
        beta = fixed_beta
    delta_x = grid.fine_x - center_x
    delta_y = grid.fine_y - center_y
    along = delta_x * math.cos(angle) + delta_y * math.sin(angle)
    across = -delta_x * math.sin(angle) + delta_y * math.cos(angle)
    radius_squared = (along / alpha_major) ** 2 + (across / alpha_minor) ** 2
    fine = amplitude * (1.0 + radius_squared) ** (-beta)
    factor = MOFFAT_SUBPIXEL_FACTOR
    pixels = fine.reshape(grid.size, factor, grid.size, factor).mean(axis=(1, 3))
    return pixels + background


def _fit_moffat(cutout: np.ndarray, grid: _SubpixelGrid, fixed_beta: float | None) -> _FitResult | None:
    """Fit one star with a pixel-integrated elliptical Moffat profile.

    Parameters
    ----------
    cutout : `numpy.ndarray`
        The square box of pixels, with the star near its center.
    grid : `_SubpixelGrid`
        The sub-pixel sample positions for that box size.
    fixed_beta : `float` or `None`
        Hold the power index at this value, or fit it when `None`.

    Returns
    -------
    fit : `_FitResult` or `None`
        The accepted fit, or `None` when the fit failed or was refused (see the
        ``MOFFAT_*`` limits above).
    """
    size = cutout.shape[0]
    half = size // 2
    background_start = float(np.median(cutout))
    peak = float(cutout[half - 2 : half + 3, half - 2 : half + 3].max()) - background_start
    if not math.isfinite(peak) or peak <= 0.0:
        return None
    alpha_start = 2.5
    start = [peak, half, half, alpha_start, alpha_start, 0.0]
    lower = [0.0, half - 3.0, half - 3.0, 0.3, 0.3, -math.pi]
    upper = [np.inf, half + 3.0, half + 3.0, size / 2.0, size / 2.0, math.pi]
    if fixed_beta is None:
        start.append(MOFFAT_BETA_START)
        lower.append(MOFFAT_BETA_MINIMUM)
        upper.append(MOFFAT_BETA_MAXIMUM)
    start.append(background_start)
    lower.append(-np.inf)
    upper.append(np.inf)

    def residuals(parameters: np.ndarray) -> np.ndarray:
        """Give model minus data for the least-squares solver.

        Returns
        -------
        residual : `numpy.ndarray`
            The flattened difference.
        """
        return (_moffat_model(parameters, grid, fixed_beta) - cutout).ravel()

    try:
        result = optimize.least_squares(residuals, np.array(start), bounds=(lower, upper), max_nfev=200)
    except ValueError, FloatingPointError:
        return None
    if not result.success:
        return None
    total_variance = float(np.sum((cutout - cutout.mean()) ** 2))
    if total_variance <= 0.0:
        return None
    r_squared = 1.0 - float(np.sum(result.fun**2)) / total_variance
    if r_squared < MOFFAT_MINIMUM_R_SQUARED:
        return None
    parameters = result.x
    beta = parameters[6] if fixed_beta is None else fixed_beta
    background = parameters[-1]
    center_shift = math.hypot(parameters[1] - half, parameters[2] - half)
    if center_shift > MOFFAT_MAXIMUM_CENTER_SHIFT_PX:
        return None
    fwhm_a = _moffat_fwhm_px(parameters[3], beta)
    fwhm_b = _moffat_fwhm_px(parameters[4], beta)
    fwhm_major, fwhm_minor = max(fwhm_a, fwhm_b), min(fwhm_a, fwhm_b)
    if not (MOFFAT_MINIMUM_FWHM_PX <= fwhm_minor and fwhm_major <= MOFFAT_MAXIMUM_FWHM_PX):
        return None
    angle = parameters[5] if fwhm_a >= fwhm_b else parameters[5] + math.pi / 2.0
    return _FitResult(
        center_x=float(parameters[1]),
        center_y=float(parameters[2]),
        fwhm_major_px=float(fwhm_major),
        fwhm_minor_px=float(fwhm_minor),
        angle_rad=float(angle),
        beta=float(beta),
        background=float(background),
        r_squared=float(r_squared),
    )


def _measure_second_moments(cutout: np.ndarray, fit: _FitResult) -> tuple[float, float, float] | None:
    """Measure a star's elongation and ellipticity from second moments.

    The pixels are weighted by their light above the fitted background
    (negative values count as zero), inside an elliptical window that follows
    the fitted star. The window is a multiple of the fitted FWHM
    (`MOMENT_APERTURE_FWHM_MULTIPLE`) along each fitted axis. A circular window
    would cut a trailed star short and read it too round.

    Parameters
    ----------
    cutout : `numpy.ndarray`
        The box of pixels around the star.
    fit : `_FitResult`
        The star's accepted Moffat fit.

    Returns
    -------
    shape : `tuple` [`float`, `float`, `float`] or `None`
        ``(elongation, first ellipticity component, second ellipticity
        component)``. The components are ``(Ixx - Iyy) / (Ixx + Iyy)`` and
        ``2 Ixy / (Ixx + Iyy)`` of the second moments ``I``. Their length is
        ``(a**2 - b**2) / (a**2 + b**2)`` for long and short axes ``a`` and
        ``b``, and the long axis points along half the angle of the vector.
        `None` when the weights sum to nothing.
    """
    size = cutout.shape[0]
    rows, columns = np.mgrid[:size, :size]
    delta_x = columns - fit.center_x
    delta_y = rows - fit.center_y
    cosine, sine = math.cos(fit.angle_rad), math.sin(fit.angle_rad)
    along = delta_x * cosine + delta_y * sine
    across = -delta_x * sine + delta_y * cosine
    semi_major = max(MOMENT_APERTURE_FWHM_MULTIPLE * fit.fwhm_major_px, MOMENT_APERTURE_MINIMUM_RADIUS_PX)
    semi_minor = max(MOMENT_APERTURE_FWHM_MULTIPLE * fit.fwhm_minor_px, MOMENT_APERTURE_MINIMUM_RADIUS_PX)
    inside = (along / semi_major) ** 2 + (across / semi_minor) ** 2 <= 1.0
    weights = np.where(inside, np.clip(cutout - fit.background, 0.0, None), 0.0)
    total = float(weights.sum())
    if total <= 0.0:
        return None
    mean_x = float((weights * columns).sum() / total)
    mean_y = float((weights * rows).sum() / total)
    variance_x = float((weights * (columns - mean_x) ** 2).sum() / total)
    variance_y = float((weights * (rows - mean_y) ** 2).sum() / total)
    covariance = float((weights * (columns - mean_x) * (rows - mean_y)).sum() / total)
    spread = math.hypot((variance_x - variance_y) / 2.0, covariance)
    mean_variance = (variance_x + variance_y) / 2.0
    largest, smallest = mean_variance + spread, mean_variance - spread
    if smallest <= 0.0:
        return None
    elongation = math.sqrt(largest / smallest)
    trace = variance_x + variance_y
    return elongation, (variance_x - variance_y) / trace, 2.0 * covariance / trace


def _mean_ellipticity_angle(
    first_components: np.ndarray, second_components: np.ndarray
) -> tuple[float, float]:
    """Find the direction the stars are stretched in, from their ellipticities.

    Averaging the ellipticity components (not the stars' angles) lets a
    stretched star count for more than a round one. A round star's angle is
    set by noise, so averaging angles with equal weight would let the round
    stars of a mostly round field wash out, or bias, the direction of the few
    stretched ones.

    Parameters
    ----------
    first_components : `numpy.ndarray`
        ``(Ixx - Iyy) / (Ixx + Iyy)`` of each star.
    second_components : `numpy.ndarray`
        ``2 Ixy / (Ixx + Iyy)`` of each star.

    Returns
    -------
    angle_deg : `float`
        The direction of the long axis, from 0 up to but not including 180
        degrees, measured from the x axis toward the y axis.
    coherence : `float`
        The length of the mean ellipticity vector divided by the mean length
        of the stars' own ellipticity vectors, from 0 to 1. It is 0 when the
        stars' stretches cancel and 1 when they all point the same way. A
        field of stars that are all exactly round gives 0.
    """
    mean_first = float(np.mean(first_components))
    mean_second = float(np.mean(second_components))
    angle_deg = (math.degrees(math.atan2(mean_second, mean_first)) / 2.0) % 180.0
    mean_length = float(np.mean(np.hypot(first_components, second_components)))
    coherence = math.hypot(mean_first, mean_second) / mean_length if mean_length > 0.0 else 0.0
    return angle_deg, coherence


def _bootstrap_angle_error_deg(
    first_components: np.ndarray, second_components: np.ndarray, generator: np.random.Generator
) -> float:
    """Estimate the error of the stretch direction by resampling the stars.

    Parameters
    ----------
    first_components : `numpy.ndarray`
        ``(Ixx - Iyy) / (Ixx + Iyy)`` of each star.
    second_components : `numpy.ndarray`
        ``2 Ixy / (Ixx + Iyy)`` of each star.
    generator : `numpy.random.Generator`
        The seeded random generator.

    Returns
    -------
    error_deg : `float`
        The spread of the resampled directions about their own circular mean,
        in degrees (each difference wrapped into -90 to +90 degrees).
    """
    draws = generator.integers(
        0, first_components.size, size=(BOOTSTRAP_RESAMPLE_COUNT, first_components.size)
    )
    resampled_first = first_components[draws].mean(axis=1)
    resampled_second = second_components[draws].mean(axis=1)
    angles_deg = np.degrees(np.arctan2(resampled_second, resampled_first)) / 2.0
    center = (
        np.degrees(
            np.arctan2(
                np.sin(np.radians(2.0 * angles_deg)).mean(), np.cos(np.radians(2.0 * angles_deg)).mean()
            )
        )
        / 2.0
    )
    wrapped = (angles_deg - center + 90.0) % 180.0 - 90.0
    return float(np.std(wrapped))


# The star finder's settings. The expected width (4 px) and the 5 sigma
# threshold are the defaults of `SourceDetector`, which finds stars for the
# astrometry stage. Its roundness cut is the one setting that is not copied:
# see `detect_stars_without_shape_cut`.
STAR_FINDER_EXPECTED_FWHM_PX = 4.0
STAR_FINDER_THRESHOLD_SIGMA = 5.0

# The background map for the star finder uses boxes of this fraction of the
# shorter side of the plane, limited to this range in pixels. The same numbers
# as `SourceDetector`: boxes much larger than a star, small enough to follow
# nebula-sized glow. Below three boxes per axis the map is not usable and one
# global value is used instead.
STAR_FINDER_BOX_FRACTION = 1.0 / 20.0
STAR_FINDER_BOX_MINIMUM_PX = 20
STAR_FINDER_BOX_MAXIMUM_PX = 200
STAR_FINDER_MINIMUM_BOXES_PER_AXIS = 3


def detect_stars_without_shape_cut(
    plane: np.ndarray, expected_fwhm_px: float = STAR_FINDER_EXPECTED_FWHM_PX
) -> list[dict]:
    """Find the stars of a plane without refusing the stretched ones.

    `SourceDetector` keeps only sources whose DAOStarFinder roundness lies in
    (-0.6, 0.6), which suits matching stars to a catalog. A star stretched to
    1.5 times as long as it is wide already fails that test, and on synthetic
    frames of 3 px stars the detector finds 4 sources at an elongation of 1.6
    where 294 exist. A width and elongation metric with no elongation cut needs
    those stars, so this function estimates the background the way
    `SourceDetector` does and runs the same star finder core
    (`pipelines/shared/star_finding.py`) with the roundness range left open.
    The sharpness range (0.2 to 1.0) still removes hot pixels and broad glow.
    This module sits in a layer below the astrometry pipeline and may not
    import `SourceDetector` in any case.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.
    expected_fwhm_px : `float`, optional
        How wide a star is expected to be, in the plane's own pixels. Defaults
        to `STAR_FINDER_EXPECTED_FWHM_PX`. A plane of 2 x 2 super-pixels needs
        half that.

    Returns
    -------
    sources : `list` of `dict`
        The detected sources, brightest first, with ``x_centroid``,
        ``y_centroid`` and ``flux``.
    """
    box_size = int(
        np.clip(
            min(plane.shape) * STAR_FINDER_BOX_FRACTION,
            STAR_FINDER_BOX_MINIMUM_PX,
            STAR_FINDER_BOX_MAXIMUM_PX,
        )
    )
    background: np.ndarray | float | None = None
    noise = 0.0
    if min(plane.shape) >= box_size * STAR_FINDER_MINIMUM_BOXES_PER_AXIS:
        try:
            estimate = Background2D(
                plane,
                box_size=box_size,
                sigma_clip=SigmaClip(sigma=3.0),
                bkg_estimator=MedianBackground(),
            )
            background = estimate.background
            noise = float(estimate.background_rms_median)
        except DATA_ERRORS as error:
            logger.warning("Background2D failed for the star finder, using one global value: %s", error)
            background = None
    if background is None:
        _, background, noise = sigma_clipped_stats(plane, sigma=3.0)
    return find_sources_above_background(
        plane - background,
        noise=noise,
        expected_fwhm_px=expected_fwhm_px,
        threshold_sigma=STAR_FINDER_THRESHOLD_SIGMA,
        roundness_range=None,
    )


def _grid_candidates(
    sources: list[dict], shape: tuple[int, int], plane: np.ndarray, top_level_adu: float | None
) -> tuple[list[list[tuple[float, float]]], CandidateRefusals]:
    """Pick the candidate stars of each grid cell.

    A candidate is isolated (no other detected source within
    `MOFFAT_ISOLATION_RADIUS_PX`), has its whole fit box inside the frame, and
    keeps every pixel of the box below `MOFFAT_SATURATION_MARGIN_FRACTION` of
    the top level. The cells are about square and number about
    `MOFFAT_GRID_CELL_COUNT`.

    Parameters
    ----------
    sources : `list` of `dict`
        Detected sources, brightest first, with ``x_centroid`` and
        ``y_centroid``.
    shape : `tuple` [`int`, `int`]
        The plane's ``(rows, columns)``.
    plane : `numpy.ndarray`
        The plane's pixels.
    top_level_adu : `float` or `None`
        The level above which a pixel is not trusted. `None` means no star is
        refused for saturation.

    Returns
    -------
    cells : `list` of `list`
        For each occupied cell, up to `MOFFAT_CANDIDATES_PER_CELL` ``(x, y)``
        centers, brightest first.
    refusals : `CandidateRefusals`
        How many sources each cut refused. A source is counted at the first
        cut it fails, in the order edge, neighbor, saturation. The fit's own
        refusals are added later by the caller.
    """
    rows, columns = shape
    cell_side = math.sqrt(rows * columns / MOFFAT_GRID_CELL_COUNT)
    cells_x = max(round(columns / cell_side), 1)
    cells_y = max(round(rows / cell_side), 1)
    positions = np.array([[source["x_centroid"], source["y_centroid"]] for source in sources], dtype=float)
    tree = cKDTree(positions)
    half = MOFFAT_FIT_BOX_HALF_WIDTH_PX
    cell_lists: dict[tuple[int, int], list[tuple[float, float]]] = {}
    near_edge = near_neighbor = near_saturation = 0
    for index, (x, y) in enumerate(positions):
        center_x, center_y = round(x), round(y)
        if center_x < half or center_y < half or center_x + half >= columns or center_y + half >= rows:
            near_edge += 1
            continue
        neighbors = tree.query_ball_point(positions[index], MOFFAT_ISOLATION_RADIUS_PX)
        if len(neighbors) > 1:
            near_neighbor += 1
            continue
        if top_level_adu is not None:
            box = plane[center_y - half : center_y + half + 1, center_x - half : center_x + half + 1]
            if box.max() >= MOFFAT_SATURATION_MARGIN_FRACTION * top_level_adu:
                near_saturation += 1
                continue
        key = (min(int(x / columns * cells_x), cells_x - 1), min(int(y / rows * cells_y), cells_y - 1))
        cell = cell_lists.setdefault(key, [])
        if len(cell) < MOFFAT_CANDIDATES_PER_CELL:
            cell.append((float(x), float(y)))
    refusals = CandidateRefusals(
        detected=len(sources),
        near_edge=near_edge,
        near_neighbor=near_neighbor,
        near_saturation=near_saturation,
    )
    return list(cell_lists.values()), refusals


def _sum_bayer_cells(plane: np.ndarray) -> np.ndarray:
    """Add up each 2 x 2 cell of a raw color mosaic into one super-pixel.

    A one-shot-color sensor puts a red, two green and a blue filter over every
    2 x 2 block of pixels (the Bayer pattern, named for its inventor). The four
    pixels of a block see the same star through different filters, so the
    pixel-to-pixel pattern of the mosaic adds a fixed 2-pixel ripple to every
    star. That ripple bends the star's second moments and its fitted angle.
    Summing each block removes the ripple, because every block holds one of
    each color, whatever the pattern order (RGGB, BGGR and so on).

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D raw mosaic.

    Returns
    -------
    super_pixels : `numpy.ndarray`
        The plane with an odd last row or column dropped, then every 2 x 2
        block summed. Its sides are half those of the input.
    """
    rows, columns = plane.shape
    trimmed = plane[: rows - rows % 2, : columns - columns % 2]
    return trimmed.reshape(rows // 2, 2, columns // 2, 2).sum(axis=(1, 3))


def measure_star_profiles(
    plane: np.ndarray,
    top_level_adu: float | None,
    stopwatch: _Stopwatch | None = None,
    sources: list[dict] | None = None,
    *,
    bayer_mosaic: bool = False,
) -> StarProfileResult:
    """Measure the Moffat width, elongation and angle of one plane's stars.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels, in ADU.
    top_level_adu : `float` or `None`
        The pixel value above which the sensor is not trusted (see
        `_usable_top_level_adu`). `None` falls back to the plane's own plateau
        of equal brightest pixels, and to no saturation test when there is
        none.
    stopwatch : `_Stopwatch` or `None`, optional
        The frame's time budget. When it runs out the result says so and holds
        no values.
    sources : `list` of `dict` or `None`, optional
        Detected sources of this plane, brightest first. They are detected
        here when not given. Ignored for a Bayer mosaic, whose sources are
        detected on the super-pixels.
    bayer_mosaic : `bool`, optional
        The plane is a raw color mosaic. Each 2 x 2 cell is summed into one
        super-pixel (`BAYER_CELL_SIZE_PX` pixels on a side) and the stars are
        measured on those. The widths are then multiplied by that factor to
        give original pixels. The elongation and the angle are ratios and a
        direction, so the factor does not change them.

    Returns
    -------
    result : `StarProfileResult`
        The medians over the accepted stars, with their bootstrap
        uncertainties, or the reason nothing was measured.

    Notes
    -----
    Pass 1 fits a Moffat profile with a free power index to the brightest
    isolated star of each grid cell (another candidate of the cell if the first
    is refused). Pass 2 holds the power index at the median of pass 1 and fits
    the same stars again. The width is the geometric mean of the two axis
    widths, so a trailed star reads wide. The elongation is not taken from the
    fit but from second moments, which do not depend on the profile shape.
    """
    factor = BAYER_CELL_SIZE_PX if bayer_mosaic else 1
    expected_fwhm_px = STAR_FINDER_EXPECTED_FWHM_PX / factor
    field_shape_px = (plane.shape[0] - plane.shape[0] % factor, plane.shape[1] - plane.shape[1] % factor)
    if bayer_mosaic:
        plane = _sum_bayer_cells(plane)
        sources = None
        # A super-pixel holds the sum of factor ** 2 pixels, so its top level
        # is that many times the pixel's.
        top_level_adu = None if top_level_adu is None else top_level_adu * factor**2
    if sources is None:
        sources = detect_stars_without_shape_cut(plane, expected_fwhm_px)
    if stopwatch is not None and stopwatch.expired():
        return StarProfileResult(reason=stopwatch.reason())
    if not sources:
        return StarProfileResult(reason="no stars were detected in the frame")
    if top_level_adu is None:
        top_level_adu = _plateau_saturation_level_adu(plane)
    cells, refusals = _grid_candidates(sources, plane.shape, plane, top_level_adu)
    half = MOFFAT_FIT_BOX_HALF_WIDTH_PX
    grid = _subpixel_grid(2 * half + 1)

    def cutout_at(x: float, y: float) -> np.ndarray:
        """Cut the fit box around a detected star.

        Returns
        -------
        cutout : `numpy.ndarray`
            The box of pixels.
        """
        center_x, center_y = round(x), round(y)
        return plane[center_y - half : center_y + half + 1, center_x - half : center_x + half + 1]

    first_pass: list[tuple[tuple[float, float], _FitResult]] = []
    candidate_count = 0
    for cell in cells:
        for x, y in cell:
            if stopwatch is not None and stopwatch.expired():
                return StarProfileResult(reason=stopwatch.reason(), candidate_count=candidate_count)
            candidate_count += 1
            fit = _fit_moffat(cutout_at(x, y), grid, fixed_beta=None)
            if fit is not None:
                first_pass.append(((x, y), fit))
                break
    first_pass_refused = candidate_count - len(first_pass)
    refusals = replace(refusals, fit_refused=first_pass_refused)
    if len(first_pass) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(first_pass),
            refusals=refusals,
            reason=(
                f"only {len(first_pass)} isolated, unsaturated stars fitted from {candidate_count} tried; "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed ({refusals.describe()})"
            ),
        )
    shared_beta = float(
        np.clip(np.median([fit.beta for _, fit in first_pass]), MOFFAT_BETA_MINIMUM, MOFFAT_BETA_MAXIMUM)
    )
    widths: list[float] = []
    samples: list[StarWidthSample] = []
    elongations: list[float] = []
    first_components: list[float] = []
    second_components: list[float] = []
    for (x, y), _ in first_pass:
        if stopwatch is not None and stopwatch.expired():
            return StarProfileResult(reason=stopwatch.reason(), candidate_count=candidate_count)
        cutout = cutout_at(x, y)
        fit = _fit_moffat(cutout, grid, fixed_beta=shared_beta)
        if fit is None:
            continue
        shape = _measure_second_moments(cutout, fit)
        if shape is None:
            continue
        widths.append(factor * math.sqrt(fit.fwhm_major_px * fit.fwhm_minor_px))
        # A super-pixel's center is at its first pixel plus (factor - 1) / 2
        # original pixels, so (x + 0.5) * factor - 0.5 puts the star back on
        # the original pixel grid (it is x itself when the factor is 1).
        samples.append(StarWidthSample((x + 0.5) * factor - 0.5, (y + 0.5) * factor - 0.5, widths[-1]))
        elongations.append(shape[0])
        first_components.append(shape[1])
        second_components.append(shape[2])
    refusals = replace(refusals, fit_refused=first_pass_refused + len(first_pass) - len(widths))
    if len(widths) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(widths),
            refusals=refusals,
            reason=(
                f"only {len(widths)} stars survived the second pass (shared beta {shared_beta:.2f}); "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed ({refusals.describe()})"
            ),
        )
    width_array = np.array(widths)
    elongation_array = np.array(elongations)
    first_array = np.array(first_components)
    second_array = np.array(second_components)
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    mean_angle, coherence = _mean_ellipticity_angle(first_array, second_array)
    return StarProfileResult(
        width_px=float(np.median(width_array)),
        width_px_uncertainty=bootstrap_median_error(width_array, generator),
        elongation=float(np.median(elongation_array)),
        elongation_uncertainty=bootstrap_median_error(elongation_array, generator),
        angle_deg=mean_angle,
        angle_uncertainty_deg=_bootstrap_angle_error_deg(first_array, second_array, generator),
        angle_coherence=coherence,
        star_count=len(widths),
        candidate_count=candidate_count,
        median_beta=shared_beta,
        refusals=refusals,
        samples=tuple(samples),
        field_shape_px=field_shape_px,
    )


@dataclass(frozen=True)
class RegionWidth:
    """The median star width in one region of the field.

    Attributes
    ----------
    region : `str`
        One of `FIELD_REGION_NAMES`.
    star_count : `int`
        How many fitted stars lie in the region.
    width_px : `float` or `None`
        Their median Moffat FWHM in original pixels, or `None` when the region
        holds fewer than `MOFFAT_MINIMUM_STAR_COUNT` stars.
    width_px_uncertainty : `float` or `None`
        The bootstrap standard error of that median.
    """

    region: str
    star_count: int
    width_px: float | None = None
    width_px_uncertainty: float | None = None


def _field_region_of(column_cell: int, row_cell: int) -> str | None:
    """Name the region a grid cell belongs to.

    Parameters
    ----------
    column_cell, row_cell : `int`
        The cell's index along x and along y, from 0 to
        `FIELD_GRID_CELLS_PER_SIDE` - 1.

    Returns
    -------
    region : `str` or `None`
        One of `FIELD_REGION_NAMES`, or `None` for a cell outside all five
        regions.
    """
    side = FIELD_GRID_CELLS_PER_SIDE
    block = FIELD_REGION_CELLS_PER_SIDE
    middle_start = (side - block) // 2

    def band(cell: int) -> str | None:
        """Name the part of the grid axis that a cell index falls in.

        Returns
        -------
        band : `str` or `None`
            ``"low"``, ``"middle"``, ``"high"`` or `None`.
        """
        if cell < block:
            return "low"
        if cell >= side - block:
            return "high"
        if middle_start <= cell < middle_start + block:
            return "middle"
        return None

    column_band, row_band = band(column_cell), band(row_cell)
    if column_band == "middle" and row_band == "middle":
        return REGION_CENTER
    corner_by_bands = {
        ("low", "low"): REGION_XLOW_YLOW,
        ("high", "low"): REGION_XHIGH_YLOW,
        ("low", "high"): REGION_XLOW_YHIGH,
        ("high", "high"): REGION_XHIGH_YHIGH,
    }
    return corner_by_bands.get((column_band, row_band))


def measure_field_region_widths(result: StarProfileResult) -> list[RegionWidth]:
    """Give the median star width of the center and of each corner.

    The stars are those the frame's width already rests on (their fits are not
    repeated). Each is put in one of the five equal regions described at
    `FIELD_REGION_NAMES`; a star between the regions is left out.

    Parameters
    ----------
    result : `StarProfileResult`
        The frame's star profile result, with its samples and field shape.

    Returns
    -------
    regions : `list` of `RegionWidth`
        One entry for each region, in `FIELD_REGION_NAMES` order. The width and
        its error are `None` for a region with fewer than
        `MOFFAT_MINIMUM_STAR_COUNT` stars; the count is always given.
    """
    members: dict[str, list[float]] = {name: [] for name in FIELD_REGION_NAMES}
    if result.field_shape_px is not None:
        rows, columns = result.field_shape_px
        side = FIELD_GRID_CELLS_PER_SIDE
        for sample in result.samples:
            column_cell = min(side - 1, max(0, int(sample.x_px / columns * side)))
            row_cell = min(side - 1, max(0, int(sample.y_px / rows * side)))
            region = _field_region_of(column_cell, row_cell)
            if region is not None:
                members[region].append(sample.width_px)
    regions = []
    for name in FIELD_REGION_NAMES:
        widths = np.array(members[name])
        if len(widths) < MOFFAT_MINIMUM_STAR_COUNT:
            regions.append(RegionWidth(name, len(widths)))
            continue
        generator = np.random.default_rng(BOOTSTRAP_SEED)
        regions.append(
            RegionWidth(
                name,
                len(widths),
                float(np.median(widths)),
                bootstrap_median_error(widths, generator),
            )
        )
    return regions


def effective_star_width_px(width_px: float) -> float:
    """Add the blur of one square pixel to a star's intrinsic width.

    Parameters
    ----------
    width_px : `float`
        The star's fitted Moffat FWHM in original pixels. The fit integrates
        the profile over each pixel, so this is the width before the pixel's
        blur.

    Returns
    -------
    effective_width_px : `float`
        ``sqrt(width_px ** 2 + c)`` with ``c`` equal to
        `PIXEL_BLUR_FWHM_SQUARED_FACTOR`, in original pixels (the pixel side
        is 1 in these units).
    """
    return math.sqrt(width_px**2 + PIXEL_BLUR_FWHM_SQUARED_FACTOR)


def _provenance_text(label: str, value: ProvenancedValue) -> str:
    """Say where a camera-profile number came from.

    Parameters
    ----------
    label : `str`
        What the number is, such as "gain".
    value : `ProvenancedValue`
        The number with its provenance.

    Returns
    -------
    text : `str`
        For example ``"gain 0.9 e-/ADU (measured: flat pairs 2026-10-20)"``.
    """
    return f"{label} {value.value:g} ({value.provenance.kind.value}: {value.provenance.source})"


def _frame_exposure_seconds(frame: FrameRecord) -> float | None:
    """Read the frame's exposure time.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame record, whose ``exposure`` is the header's ``EXPTIME`` text.

    Returns
    -------
    seconds : `float` or `None`
        The exposure in seconds, or `None` when it is missing, not a number, or
        not above zero.
    """
    try:
        seconds = float(frame.exposure)
    except TypeError, ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds > 0.0 else None


def look_up_frame_sensor_noise(
    camera_profile: CameraProfile | None, frame: FrameRecord, *, clipped_at_zero: bool | None = None
) -> tuple[ProvenancedValue | None, ProvenancedValue | None, str]:
    """Find the gain and read noise the profile holds for a frame's setting.

    An entry whose gain setting, offset and binning all match always wins.
    The frame record keeps the gain setting in its ``iso`` field (the header's
    ``GAIN`` card, or the ISO of a DSLR). With ``clipped_at_zero=False`` and
    no exact entry, the entry of the same gain and binning at the nearest
    other offset is used (see `CameraProfile.sensor_noise_for`), and its
    source text says so.

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile.
    frame : `FrameRecord`
        The frame.
    clipped_at_zero : `bool` or `None`, optional
        Whether the frame's pixels are cut off at zero. `False` allows the
        offset fallback; `True` and `None` (not known) do not.

    Returns
    -------
    gain : `ProvenancedValue` or `None`
        Electrons per ADU at the frame's setting.
    read_noise : `ProvenancedValue` or `None`
        Read noise in electrons at the frame's setting.
    reason : `str`
        Why a value is missing (empty when both were found).
    """
    if camera_profile is None:
        return None, None, "no camera profile was given for this frame"
    gap = camera_profile.sensor_noise_gap_reason(
        frame.iso, frame.offset, frame.binning, clipped_at_zero=clipped_at_zero
    )
    if gap is not None:
        return None, None, gap
    entry = camera_profile.sensor_noise_for(
        frame.iso, frame.offset, frame.binning, clipped_at_zero=clipped_at_zero
    )
    if entry is None:
        return None, None, "camera profile has no entry for this setting"
    reasons = []
    if entry.gain_e_per_adu is None:
        reasons.append("the profile entry for this setting holds no gain")
    if entry.read_noise_e is None:
        reasons.append("the profile entry for this setting holds no read noise")
    return entry.gain_e_per_adu, entry.read_noise_e, "; ".join(reasons)


def measure_sky_electrons(
    background_adu: float,
    bias_level_adu: float | None,
    gain: ProvenancedValue | None,
    exposure_seconds: float | None,
    gain_reason: str,
    bias_reason: str = "",
) -> SkyElectrons:
    """Turn a plane's background into electrons.

    The sky signal in ADU is the background minus the bias level (the constant
    the camera adds to every pixel, which the raw frame carries in its pixel
    values; the header's ``OFFSET`` card is a camera setting, not that level in
    ADU). The signal times the gain is the sky in electrons for one exposure,
    and its square root is the expected photon counting (Poisson) noise of that
    sky per pixel. Dark current and amp glow (light from the sensor's own
    electronics) are inside the signal, because no dark frame is subtracted.

    Parameters
    ----------
    background_adu : `float`
        The sigma-clipped mean of the plane, in ADU (see
        `measure_clipped_mean_adu`).
    bias_level_adu : `float` or `None`
        The bias level for the frame's setting, in ADU, found with the same
        estimator. `None` means unknown.
    gain : `ProvenancedValue` or `None`
        Electrons per ADU at the frame's setting.
    exposure_seconds : `float` or `None`
        The exposure time.
    gain_reason : `str`
        Why the gain is missing, used when it is.
    bias_reason : `str`, optional
        Why the bias level is missing, used when it is. A generic sentence is
        used when this is empty.

    Returns
    -------
    sky : `SkyElectrons`
        The rate and noise, or the reason they are missing.
    """
    if gain is None:
        return SkyElectrons(background_adu=background_adu, reason=gain_reason or "no gain for this setting")
    if bias_level_adu is None:
        return SkyElectrons(
            background_adu=background_adu,
            reason=bias_reason
            or (
                "the bias level of this setting in ADU is not known, and the raw frame does not state it "
                "(the OFFSET card is a camera setting, not a level in ADU)"
            ),
        )
    if exposure_seconds is None:
        return SkyElectrons(background_adu=background_adu, reason="the frame has no usable exposure time")
    signal_adu = background_adu - bias_level_adu
    if signal_adu <= 0.0:
        return SkyElectrons(
            background_adu=background_adu,
            reason=(
                f"the background ({background_adu:.1f} ADU) is not above the bias "
                f"level ({bias_level_adu:.1f} ADU)"
            ),
        )
    sky_electrons = signal_adu * gain.value
    return SkyElectrons(
        rate_e_per_s_per_px=sky_electrons / exposure_seconds,
        noise_e=math.sqrt(sky_electrons),
        background_adu=background_adu,
        reason=(
            f"clipped-mean background {background_adu:.1f} ADU minus bias level "
            f"{bias_level_adu:.1f} ADU, times "
            f"{_provenance_text('gain (e-/ADU)', gain)}"
        ),
        gain_is_measured=gain.provenance.kind == ProvenanceKind.MEASURED,
    )


def _pixel_scale_note(frame: FrameRecord) -> str:
    """Name the source of the pixel scale used for the arcsecond width.

    Parameters
    ----------
    frame : `FrameRecord`
        The frame.

    Returns
    -------
    note : `str`
        A sentence fragment for the measurement's reason.
    """
    return (
        f"pixel scale {frame.pixel_scale_arcsec:g} arcsec/px from the frame "
        "record (FrameRecord.pixel_scale_arcsec)"
    )


def _emit_star_profile_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, mosaic_note: str
) -> None:
    """Turn one plane's star profile result into the four star measurements.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    """
    star_metrics = (
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        *FRAME_STAR_WIDTH_REGION_PX.values(),
    )
    if result.width_px is None:
        context.add_not_measured(star_metrics, result.reason, channel)
        return
    fit_note = (
        f"{result.star_count} stars from {result.candidate_count} tried ({result.refusals.describe()}), "
        f"shared Moffat beta {result.median_beta:.2f}{mosaic_note}"
    )
    context.add(
        FRAME_STAR_WIDTH_MOFFAT_PX,
        value=result.width_px,
        uncertainty=result.width_px_uncertainty,
        sample_count=result.star_count,
        reason=fit_note,
        channel=channel,
    )
    pixel_scale = context.frame.pixel_scale_arcsec
    if pixel_scale is None or not math.isfinite(pixel_scale) or pixel_scale <= 0.0:
        context.add(
            FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
            reason="the frame record has no pixel scale, so the width cannot be given in arcseconds",
            channel=channel,
        )
    else:
        context.add(
            FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
            value=result.width_px * pixel_scale,
            uncertainty=(result.width_px_uncertainty or 0.0) * pixel_scale,
            sample_count=result.star_count,
            reason=f"{fit_note}; {_pixel_scale_note(context.frame)}",
            channel=channel,
        )
    context.add(
        FRAME_STAR_ELONGATION,
        value=result.elongation,
        uncertainty=result.elongation_uncertainty,
        sample_count=result.star_count,
        reason=fit_note,
        channel=channel,
    )
    context.add(
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        value=result.angle_deg,
        uncertainty=result.angle_uncertainty_deg,
        sample_count=result.star_count,
        reason=(
            f"{fit_note}; angles agree with a coherence of {result.angle_coherence:.2f} "
            "(0 means no preferred direction, so the angle means little)"
        ),
        channel=channel,
    )
    _emit_effective_width_measurements(context, result, channel, fit_note)
    _emit_field_region_measurements(context, result, channel, mosaic_note)


def _emit_effective_width_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, fit_note: str
) -> None:
    """Add the pixel's blur to the frame's star width and store it twice.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane, with a width.
    channel : `str` or `None`
        The plane's name.
    fit_note : `str`
        The sentence that says how many stars the width rests on.
    """
    width = result.width_px
    effective = effective_star_width_px(width)
    uncertainty = None
    if result.width_px_uncertainty is not None:
        # d(effective) / d(width) = width / effective: the pixel term is a
        # constant, so only the width's own error passes through.
        uncertainty = result.width_px_uncertainty * width / effective
    model_note = (
        f"model-derived: sqrt(FWHM^2 + c p^2) with c = 2 ln 2 / 3 = {PIXEL_BLUR_FWHM_SQUARED_FACTOR:.4f} "
        "(theory for a Gaussian core and a square pixel, not fitted) and p one original pixel; "
        "the error carries the width's bootstrap error only"
    )
    context.add(
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        value=effective,
        uncertainty=uncertainty,
        sample_count=result.star_count,
        reason=f"{fit_note}; {model_note}",
        channel=channel,
    )
    pixel_scale = context.frame.pixel_scale_arcsec
    if pixel_scale is None or not math.isfinite(pixel_scale) or pixel_scale <= 0.0:
        context.add(
            FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
            reason="the frame record has no pixel scale, so the width cannot be given in arcseconds",
            channel=channel,
        )
        return
    context.add(
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        value=effective * pixel_scale,
        uncertainty=None if uncertainty is None else uncertainty * pixel_scale,
        sample_count=result.star_count,
        reason=f"{fit_note}; {model_note}; {_pixel_scale_note(context.frame)}",
        channel=channel,
    )


def _emit_field_region_measurements(
    context: _Context, result: StarProfileResult, channel: str | None, mosaic_note: str
) -> None:
    """Store the median star width of the center and of each corner.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    result : `StarProfileResult`
        The result for one plane, with a width.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    """
    for region in measure_field_region_widths(result):
        metric = FRAME_STAR_WIDTH_REGION_PX[region.region]
        if region.width_px is None:
            context.add(
                metric,
                reason=(
                    f"only {region.star_count} fitted stars lie in this region of the field; "
                    f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed"
                ),
                channel=channel,
            )
            continue
        context.add(
            metric,
            value=region.width_px,
            uncertainty=region.width_px_uncertainty,
            sample_count=region.star_count,
            reason=(
                f"{region.star_count} of the frame's {result.star_count} fitted stars lie in this region "
                f"(a block of {FIELD_REGION_CELLS_PER_SIDE} x {FIELD_REGION_CELLS_PER_SIDE} of the "
                f"{FIELD_GRID_CELLS_PER_SIDE} x {FIELD_GRID_CELLS_PER_SIDE} grid cells){mosaic_note}"
            ),
            channel=channel,
        )


def _emit_sky_measurements(
    context: _Context,
    sky: SkyElectrons,
    read_noise: ProvenancedValue | None,
    read_noise_reason: str,
    channel: str | None,
    mosaic_note: str,
    uncooled_note: str = "",
) -> None:
    """Turn one plane's sky result and the read noise into measurements.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    sky : `SkyElectrons`
        The sky in electrons.
    read_noise : `ProvenancedValue` or `None`
        The read noise for the frame's setting.
    read_noise_reason : `str`
        Why the read noise is missing, when it is.
    channel : `str` or `None`
        The plane's name.
    mosaic_note : `str`
        Text added to the reasons for a raw mosaic, else empty.
    uncooled_note : `str`, optional
        Why the camera is taken as uncooled, or an empty string for a camera
        with a recorded sensor temperature. When it is given, the sky rows are
        stored as assumed, because the dark current and amp glow in the sky
        are then unknown and no dark frame was subtracted.
    """
    if sky.rate_e_per_s_per_px is None:
        context.add(FRAME_SKY_RATE_E_PER_S_PER_PX, reason=sky.reason, channel=channel)
        context.add(FRAME_SKY_NOISE_E, reason=sky.reason, channel=channel)
    else:
        is_measured = sky.gain_is_measured and not uncooled_note
        status = MeasurementStatus.MEASURED if is_measured else MeasurementStatus.ASSUMED
        note = sky.reason + ("" if sky.gain_is_measured else "; the gain is not measured")
        note += (f"; {uncooled_note}" if uncooled_note else "") + mosaic_note
        context.add(
            FRAME_SKY_RATE_E_PER_S_PER_PX,
            value=sky.rate_e_per_s_per_px,
            reason=note,
            channel=channel,
            status=status,
        )
        context.add(FRAME_SKY_NOISE_E, value=sky.noise_e, reason=note, channel=channel, status=status)
    context.add(FRAME_READ_NOISE_E, channel=channel, **_read_noise_arguments(read_noise, read_noise_reason))


def measure_frame(
    path: str,
    frame: FrameRecord,
    target_id: str,
    camera_profile: CameraProfile | None,
    *,
    run_id: str,
    job_id: str | None,
    measured_at: datetime,
    capture_setting_id: int | None,
    time_budget_seconds: float = FRAME_MEASUREMENT_TIME_BUDGET_SECONDS,
    bias_level_adu: float | None = None,
    measure_gaussian_width: Callable[[np.ndarray], float | None] | None = None,
    bias_level_reason: str = "",
    clipped_zero_fraction_limit: float | None = None,
) -> list[QualityMeasurement]:
    """Measure one light frame and return one measurement per frame metric.

    The pixels are read once. Nothing is written. A value that cannot be
    measured comes back ``not_measured`` with a reason.

    Parameters
    ----------
    path : `str`
        Path to the FITS file.
    frame : `FrameRecord`
        The frame's record: its gain setting (``iso``), offset, binning,
        exposure, pixel scale, timestamp and temperatures.
    target_id : `str`
        The target the frame belongs to.
    camera_profile : `CameraProfile` or `None`
        The camera's profile, for the saturation level, the gain and the read
        noise. `None` makes every metric that needs it ``not_measured``.
    run_id : `str`
        The job id, or a generated id when there was no job.
    job_id : `str` or `None`
        A soft link to the job's activity record.
    measured_at : `datetime`
        When the measurement is made. It must carry a time zone.
    capture_setting_id : `int` or `None`
        The row of the capture-setting table for this frame.
    time_budget_seconds : `float`, optional
        The seconds allowed for the frame. See
        `FRAME_MEASUREMENT_TIME_BUDGET_SECONDS`.
    bias_level_adu : `float` or `None`, optional
        The bias level in ADU for the frame's gain, offset and binning, such as
        the median of the master bias. A raw frame carries it inside its pixel
        values and does not state it, so without it the sky metrics in
        electrons are ``not_measured``. This argument is an addition to the
        planned interface; leaving it out is valid.
    measure_gaussian_width : `Callable` or `None`, optional
        The function that gives `frame.star_width_gaussian_brightest_px` from
        the collapsed 2-D pixels, which is
        ``pipelines.astrometry.pre_processing.fwhm.measure_fwhm_from_data``.
        This module sits in a layer below the astrometry pipeline and may not
        import it, so the caller passes it in. Without it that metric is
        ``not_measured``. This argument is an addition to the planned
        interface.
    bias_level_reason : `str`, optional
        Why `bias_level_adu` is `None` (for example, that the calibration
        library holds no bias frames at the frame's setting). It becomes the
        reason of the sky metrics that need the bias level.
    clipped_zero_fraction_limit : `float` or `None`, optional
        A frame with more than this fraction of its pixels at exactly zero is
        clipped at zero. The caller passes
        ``CLIPPED_FRAME_ZERO_FRACTION`` (this folder may not import the
        stacking pipeline). A frame at or below it may use the camera
        profile's gain and read noise from another offset (the offset
        fallback). `None` turns the fallback off.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One per frame metric (and per color plane for the star and sky
        metrics of a frame stored as color planes).
    """
    stopwatch = _Stopwatch(time_budget_seconds)
    context = _Context(
        frame=frame,
        target_id=target_id,
        run_id=run_id,
        job_id=job_id,
        measured_at=measured_at,
        capture_setting_id=capture_setting_id,
        path=path,
    )
    _emit_header_measurements(context)

    pixel_metrics = (
        FRAME_BACKGROUND_LEVEL,
        FRAME_SATURATED_PIXEL_FRACTION,
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        FRAME_DETECTED_REGION_COUNT,
        FRAME_STAR_WIDTH_MOFFAT_PX,
        FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
        FRAME_STAR_ELONGATION,
        FRAME_STAR_ELONGATION_ANGLE_DEG,
        FRAME_STAR_WIDTH_EFFECTIVE_PX,
        FRAME_STAR_WIDTH_EFFECTIVE_ARCSEC,
        *FRAME_STAR_WIDTH_REGION_PX.values(),
        FRAME_SKY_RATE_E_PER_S_PER_PX,
        FRAME_SKY_NOISE_E,
    )
    try:
        planes, collapsed, header = _read_planes(path)
    except FITS_READ_ERRORS as read_error:
        _, read_noise, noise_reason = look_up_frame_sensor_noise(camera_profile, frame)
        context.add_not_measured(pixel_metrics, f"{UNREADABLE_FRAME_REASON_PREFIX}: {read_error}")
        context.add(FRAME_READ_NOISE_E, **_read_noise_arguments(read_noise, noise_reason))
        return context.measurements

    # The offset fallback needs to know the frame is not cut off at zero.
    clipped_at_zero = (
        None
        if clipped_zero_fraction_limit is None
        else _frame_zero_fraction(planes) > clipped_zero_fraction_limit
    )
    gain, read_noise, noise_reason = look_up_frame_sensor_noise(
        camera_profile, frame, clipped_at_zero=clipped_at_zero
    )

    is_mosaic = bool(str(header.get("BAYERPAT", "")).strip()) and len(planes) == 1
    mosaic_note = (
        f"; raw color mosaic: the sky and background use all color pixels mixed, and the star shapes use "
        f"{BAYER_CELL_SIZE_PX} x {BAYER_CELL_SIZE_PX} super-pixels (widths given back in original pixels)"
        if is_mosaic
        else ""
    )
    uncooled_note = (
        ""
        if frame.sensor_temperature_c is not None
        else (
            "the frame records no sensor temperature (no CCD-TEMP card), so the camera is taken as "
            "uncooled, and the dark current and amp glow in the sky are unknown"
        )
    )
    top_level = _usable_top_level_adu(camera_profile)
    exposure_seconds = _frame_exposure_seconds(frame)

    # Quick metrics first: they are returned even when the budget runs out.
    _emit_background_and_saturation(context, collapsed, camera_profile)
    for channel, plane in planes:
        # The sky uses the clipped mean, not the median of the background
        # metric: a 14-bit sensor stores steps of 4 ADU, and a median of such
        # data takes only a few values (see `measure_clipped_mean_adu`).
        sky = measure_sky_electrons(
            measure_clipped_mean_adu(plane),
            bias_level_adu,
            gain,
            exposure_seconds,
            noise_reason,
            bias_level_reason,
        )
        _emit_sky_measurements(context, sky, read_noise, noise_reason, channel, mosaic_note, uncooled_note)

    # Slow steps: star detection and fits.
    _emit_region_count(context, collapsed, stopwatch)
    _emit_gaussian_width(context, collapsed, stopwatch, measure_gaussian_width)
    for channel, plane in planes:
        if stopwatch.expired():
            result = StarProfileResult(reason=stopwatch.reason())
        else:
            try:
                result = measure_star_profiles(plane, top_level, stopwatch, bayer_mosaic=is_mosaic)
            except DATA_ERRORS as error:
                logger.warning("Moffat star profiles failed for %s: %s", path, error)
                result = StarProfileResult(reason=f"the star fits failed: {error}")
        _emit_star_profile_measurements(context, result, channel, mosaic_note)
    return context.measurements


def _frame_zero_fraction(planes: list[tuple[str | None, np.ndarray]]) -> float:
    """Measure the fraction of a frame's pixels that are exactly zero.

    Parameters
    ----------
    planes : `list` of `tuple`
        The planes from `_read_planes`.

    Returns
    -------
    zero_fraction : `float`
        The share of all pixels, over every plane, at exactly zero. A frame
        cut off at zero has many; a frame with sky well above the offset has
        none.
    """
    total = sum(plane.size for _, plane in planes)
    zeros = sum(int(np.count_nonzero(plane == 0)) for _, plane in planes)
    return zeros / total if total else 0.0


def _read_noise_arguments(read_noise: ProvenancedValue | None, reason: str) -> dict:
    """Build the arguments of the read-noise measurement for `_Context.add`.

    Parameters
    ----------
    read_noise : `ProvenancedValue` or `None`
        The read noise for the frame's setting.
    reason : `str`
        Why it is missing.

    Returns
    -------
    arguments : `dict`
        Keyword arguments for `_Context.add`, without the metric name.
    """
    if read_noise is None:
        return {"reason": reason or "no read noise for this setting"}
    is_measured = read_noise.provenance.kind == ProvenanceKind.MEASURED
    return {
        "value": read_noise.value,
        "reason": _provenance_text("read noise", read_noise),
        "status": MeasurementStatus.MEASURED if is_measured else MeasurementStatus.ASSUMED,
    }


def _emit_header_measurements(context: _Context) -> None:
    """Emit the two temperatures from the frame record.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    """
    frame = context.frame
    for metric, value, card in (
        (FRAME_SENSOR_TEMPERATURE, frame.sensor_temperature_c, "sensor temperature (header card CCD-TEMP)"),
        (
            FRAME_FOCUSER_TEMPERATURE,
            frame.focuser_temperature_c,
            "focuser temperature (header card FOCUSTEM)",
        ),
    ):
        context.add(
            metric, value=value, reason="" if value is not None else f"the frame record has no {card}"
        )


def _clipped_median(plane: np.ndarray) -> float:
    """Give the sigma-clipped median of a plane, as the background metric does.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The pixels.

    Returns
    -------
    median : `float`
        The median after clipping at 3 sigma (astropy's default 5 passes).
    """
    _, median, _ = sigma_clipped_stats(plane, sigma=3.0)
    return float(median)


# The clipping of the sky and bias estimator. 3 sigma and astropy's default
# of up to 5 passes are the same as the background metric's, so the two
# estimators differ only in the statistic taken from the clipped pixels (mean,
# not median).
SKY_CLIP_SIGMA = 3.0


def measure_clipped_mean_adu(plane: np.ndarray) -> float:
    """Give the sigma-clipped mean of a plane, for the sky and the bias level.

    The sky in electrons and the bias level are both found with this one
    estimator, so they cancel each other's bias. The mean is used, not the
    median, because the stored values are whole steps. A 14-bit sensor such as
    the ASI 533 writes its values in steps of 4 ADU, and the median of such a
    plane takes only a few values: on 61 real M 57 frames the clipped median
    took 13 different values (792 to 952 ADU, in steps of 4), so the sky in
    electrons moved in steps of 4 ADU times the gain. The mean of millions of
    pixels varies smoothly. Clipping at `SKY_CLIP_SIGMA` sigma (up to 5 passes)
    removes stars and hot pixels, which a plain mean would not.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The pixels.

    Returns
    -------
    mean_adu : `float`
        The mean of the pixels left after sigma clipping.
    """
    mean, _, _ = sigma_clipped_stats(plane, sigma=SKY_CLIP_SIGMA)
    return float(mean)


def _emit_background_and_saturation(
    context: _Context, collapsed: np.ndarray, camera_profile: CameraProfile | None
) -> float:
    """Emit the background level and saturated fraction of the collapsed frame.

    The background uses the same call as `measure_frame_background_level`
    (sigma-clipped median at 3 sigma), applied to the pixels already in memory
    so the file is not read again.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The frame with its color planes averaged to one.
    camera_profile : `CameraProfile` or `None`
        The camera's profile, for the saturation threshold.

    Returns
    -------
    background_adu : `float`
        The collapsed frame's background, for the sky metrics of a
        single-plane frame.
    """
    background_adu = _clipped_median(collapsed)
    context.add(FRAME_BACKGROUND_LEVEL, value=background_adu)
    if camera_profile is None:
        context.add(
            FRAME_SATURATED_PIXEL_FRACTION,
            reason="no camera profile was given, so the saturation level is unknown",
        )
    else:
        threshold = float(camera_profile.pixel_saturation_level_adu.value)
        context.add(
            FRAME_SATURATED_PIXEL_FRACTION,
            value=compute_saturated_pixel_fraction(collapsed, threshold),
            reason=f"{camera_profile.camera_name} profile saturation level {threshold:g} ADU",
        )
    return background_adu


def _emit_region_count(context: _Context, collapsed: np.ndarray, stopwatch: _Stopwatch) -> None:
    """Emit the count of bright regions, if there is time left.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The collapsed frame.
    stopwatch : `_Stopwatch`
        The frame's time budget.
    """
    if stopwatch.expired():
        context.add(FRAME_DETECTED_REGION_COUNT, reason=stopwatch.reason())
        return
    try:
        _, region_count, _, _ = detect_stars(collapsed)
    except DATA_ERRORS as error:
        context.add(FRAME_DETECTED_REGION_COUNT, reason=f"the bright-region count failed: {error}")
        return
    context.add(FRAME_DETECTED_REGION_COUNT, value=float(region_count))


def _emit_gaussian_width(
    context: _Context,
    collapsed: np.ndarray,
    stopwatch: _Stopwatch,
    measure_gaussian_width: Callable[[np.ndarray], float | None] | None,
) -> None:
    """Emit the Gaussian width of the brightest stars, if there is time left.

    Parameters
    ----------
    context : `_Context`
        The measurement under way.
    collapsed : `numpy.ndarray`
        The collapsed frame.
    stopwatch : `_Stopwatch`
        The frame's time budget.
    measure_gaussian_width : `Callable` or `None`
        The width function the caller passed in, or `None`.
    """
    if measure_gaussian_width is None:
        context.add(
            FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
            reason="the caller did not pass the Gaussian width function",
        )
        return
    if stopwatch.expired():
        context.add(FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX, reason=stopwatch.reason())
        return
    try:
        width = measure_gaussian_width(collapsed)
    except DATA_ERRORS as error:
        context.add(FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX, reason=f"the Gaussian width failed: {error}")
        return
    context.add(
        FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
        value=width,
        reason="no star could be fitted" if width is None else "",
    )
