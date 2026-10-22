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
  direction of the long axis, from the second moments of the same stars.
- The sky brightness in electrons, the noise of that sky, and the read noise of
  the camera at the frame's setting. The ratio of the two noises is never
  stored.
- The temperatures the frame recorded, and the metrics the library already
  measured (background level, saturated fraction, Gaussian star width of the
  brightest stars, count of bright regions).

A value that cannot be measured comes back with the status ``not_measured`` and
a reason. It is never replaced by a default number.

One-shot-color frames: a frame stored as separate color planes is measured per
plane, and the plane's name goes on the subject. A raw mosaic (one plane with a
color filter array, header card ``BAYERPAT``) is measured as it is, so its
red, green and blue pixels are mixed. Its reasons say so.

A time budget keeps one slow frame from stalling a batch. It is checked between
the steps; when it has run out, the remaining slow steps (the star detections
and fits) come back ``not_measured`` and the quick header and background
metrics are still returned.
"""

import logging
import math
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from astropy.io import fits
from astropy.stats import SigmaClip, sigma_clipped_stats
from photutils.background import Background2D, MedianBackground
from photutils.detection import DAOStarFinder
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
    FRAME_STAR_WIDTH_GAUSSIAN_BRIGHTEST_PX,
    FRAME_STAR_WIDTH_MOFFAT_ARCSEC,
    FRAME_STAR_WIDTH_MOFFAT_PX,
)
from astrometricslib.pipelines.shared.quality.raw_frame_check import _detect_stars
from astrometricslib.pipelines.shared.quality.saturation import compute_saturated_pixel_fraction
from astrometricslib.utilities.exceptions import DATA_ERRORS
from astrometricslib.utilities.observing_night import observing_night_id

logger = logging.getLogger(__name__)

# How long one frame may take before the slow steps are skipped, in seconds.
# Timed on this machine (12 cores, serial) on the 3008 x 3008 ASI533MM golden
# M 13 frames (M_13_Light_Luminance_019 and _020): the whole measurement took
# 11.6 s and 13.5 s, with 175 to 179 stars fitted. The budget is about 5 times
# that, so a normal frame never reaches it even on a busy machine (twelve
# tests running at once took 15.5 s), and a frame that is several times slower
# than normal (a busy disk, a larger sensor, a crowded field) is cut off
# instead of stalling a batch. The budget is checked between the steps, not
# inside one, so one step can run past it.
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
# wing shape decide the value. The limits keep a fit from wandering: below 1.2
# the profile has an unbounded flux, above 8 it is a Gaussian.
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

# The number of resamples for the bootstrap of a median. 1000 puts the error
# of the uncertainty itself near 2 percent.
BOOTSTRAP_RESAMPLE_COUNT = 1000

# Seed of the bootstrap, so one frame always gives the same uncertainty. The
# value itself is arbitrary.
BOOTSTRAP_SEED = 20261022

# The names of the planes of a frame stored as three color planes.
_COLOR_PLANE_NAMES = {3: ("R", "G", "B")}

_DEFINITION_VERSIONS = {spec.name: spec.definition_version for spec in FRAME_METRICS}


class _Stopwatch:
    """Count the seconds spent on one frame against a budget.

    Parameters
    ----------
    budget_seconds : `float`
        The seconds allowed for the whole frame.
    """

    def __init__(self, budget_seconds: float) -> None:
        self.budget_seconds = budget_seconds
        self._start = time.monotonic()

    def elapsed(self) -> float:
        """Give the seconds spent so far.

        Returns
        -------
        seconds : `float`
            Seconds since the frame measurement began.
        """
        return time.monotonic() - self._start

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
            "time budget of N s exceeded after M s".
        """
        return f"time budget of {self.budget_seconds:g} s exceeded after {self.elapsed():.1f} s"


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
        Circular mean angle of the long axis in degrees, from 0 up to but not
        including 180, measured from the image x axis toward the y axis.
    angle_uncertainty_deg : `float` or `None`
        Bootstrap standard deviation of the circular mean, in degrees.
    angle_coherence : `float` or `None`
        Mean resultant length of the angles, from 0 (no preferred direction)
        to 1 (all stars point the same way).
    star_count : `int`
        Number of stars the medians are over.
    candidate_count : `int`
        Number of isolated, unsaturated stars that were tried.
    median_beta : `float` or `None`
        The shared Moffat power index of the second pass.
    reason : `str`
        Why the values are missing, or a note on how they were found.
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
    reason: str = ""


@dataclass(frozen=True)
class SkyElectrons:
    """The sky of one plane of one frame, in electrons.

    Attributes
    ----------
    rate_e_per_s_per_px : `float` or `None`
        Sky electrons per second per pixel.
    noise_e : `float` or `None`
        Square root of the sky electrons in one exposure, per pixel.
    background_adu : `float` or `None`
        The sigma-clipped median of the plane, in ADU.
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
    subject_id: str
    night_id: str | None
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
                subject=QualitySubject(
                    level=SubjectLevel.FRAME,
                    subject_id=self.subject_id,
                    target_id=self.target_id,
                    observing_night_id=self.night_id,
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
    ceiling = float(camera_profile.clip_ceiling_adu.value)
    threshold = float(camera_profile.saturation_threshold_adu.value)
    level = threshold if camera_profile.saturation_threshold_can_be_reached else ceiling
    if camera_profile.photometric_linearity_limit_adu is not None:
        level = min(level, float(camera_profile.photometric_linearity_limit_adu.value))
    return level


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


def _measure_second_moments(cutout: np.ndarray, fit: _FitResult) -> tuple[float, float] | None:
    """Measure a star's elongation and angle from flux-weighted second moments.

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
    shape : `tuple` [`float`, `float`] or `None`
        ``(elongation, angle in degrees from 0 up to 180)``. `None` when the
        weights sum to nothing.
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
    angle = math.degrees(0.5 * math.atan2(2.0 * covariance, variance_x - variance_y)) % 180.0
    return elongation, angle


def _circular_mean_axial_deg(angles_deg: np.ndarray) -> tuple[float, float]:
    """Average angles of lines, where 0 and 180 degrees are the same line.

    The angles are doubled, averaged as unit vectors, and halved.

    Parameters
    ----------
    angles_deg : `numpy.ndarray`
        Angles in degrees.

    Returns
    -------
    mean_deg : `float`
        The mean angle, from 0 up to but not including 180.
    coherence : `float`
        The mean resultant length, from 0 to 1.
    """
    doubled = np.radians(2.0 * angles_deg)
    sine_mean, cosine_mean = float(np.mean(np.sin(doubled))), float(np.mean(np.cos(doubled)))
    mean_deg = (math.degrees(math.atan2(sine_mean, cosine_mean)) / 2.0) % 180.0
    return mean_deg, math.hypot(sine_mean, cosine_mean)


def _bootstrap_median_error(values: np.ndarray, generator: np.random.Generator) -> float:
    """Estimate the standard error of a median by resampling.

    Parameters
    ----------
    values : `numpy.ndarray`
        The sample.
    generator : `numpy.random.Generator`
        The seeded random generator.

    Returns
    -------
    error : `float`
        The standard deviation of the medians of
        `BOOTSTRAP_RESAMPLE_COUNT` resamples drawn with replacement.
    """
    draws = generator.integers(0, values.size, size=(BOOTSTRAP_RESAMPLE_COUNT, values.size))
    return float(np.std(np.median(values[draws], axis=1)))


def _bootstrap_angle_error_deg(angles_deg: np.ndarray, generator: np.random.Generator) -> float:
    """Estimate the error of the circular mean angle by resampling.

    Parameters
    ----------
    angles_deg : `numpy.ndarray`
        The sample of angles.
    generator : `numpy.random.Generator`
        The seeded random generator.

    Returns
    -------
    error_deg : `float`
        The spread of the resampled means about their own circular mean, in
        degrees (each difference wrapped into -90 to +90 degrees).
    """
    draws = generator.integers(0, angles_deg.size, size=(BOOTSTRAP_RESAMPLE_COUNT, angles_deg.size))
    doubled = np.radians(2.0 * angles_deg[draws])
    means_deg = np.degrees(np.arctan2(np.sin(doubled).mean(axis=1), np.cos(doubled).mean(axis=1))) / 2.0
    center = (
        np.degrees(
            np.arctan2(np.sin(np.radians(2.0 * means_deg)).mean(), np.cos(np.radians(2.0 * means_deg)).mean())
        )
        / 2.0
    )
    wrapped = (means_deg - center + 90.0) % 180.0 - 90.0
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


def detect_stars_without_shape_cut(plane: np.ndarray) -> list[dict]:
    """Find the stars of a plane without refusing the stretched ones.

    `SourceDetector` keeps only sources whose DAOStarFinder roundness lies in
    (-0.6, 0.6), which suits matching stars to a catalog. A star stretched to
    1.5 times as long as it is wide already fails that test, and on synthetic
    frames of 3 px stars the detector finds 4 sources at an elongation of 1.6
    where 294 exist. A width and elongation metric with no elongation cut needs
    those stars, so this function repeats `SourceDetector`'s background
    estimate and star finder settings but leaves the roundness range open. The
    sharpness range (0.2 to 1.0) still removes hot pixels and broad glow.
    This module sits in a layer below the astrometry pipeline and may not
    import `SourceDetector` in any case.

    Parameters
    ----------
    plane : `numpy.ndarray`
        The 2-D pixels.

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
    finder = DAOStarFinder(
        fwhm=STAR_FINDER_EXPECTED_FWHM_PX,
        threshold=STAR_FINDER_THRESHOLD_SIGMA * noise,
        exclude_border=True,
        sharpness_range=(0.2, 1.0),
        roundness_range=None,
    )
    found = finder(plane - background)
    if found is None:
        return []
    found.sort("flux")
    found.reverse()
    return [dict(zip(found.colnames, row, strict=False)) for row in found]


def _grid_candidates(
    sources: list[dict], shape: tuple[int, int], plane: np.ndarray, top_level_adu: float | None
) -> list[list[tuple[float, float]]]:
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
    """
    rows, columns = shape
    cell_side = math.sqrt(rows * columns / MOFFAT_GRID_CELL_COUNT)
    cells_x = max(round(columns / cell_side), 1)
    cells_y = max(round(rows / cell_side), 1)
    positions = np.array([[source["x_centroid"], source["y_centroid"]] for source in sources], dtype=float)
    tree = cKDTree(positions)
    half = MOFFAT_FIT_BOX_HALF_WIDTH_PX
    cell_lists: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for index, (x, y) in enumerate(positions):
        center_x, center_y = round(x), round(y)
        if center_x < half or center_y < half or center_x + half >= columns or center_y + half >= rows:
            continue
        neighbors = tree.query_ball_point(positions[index], MOFFAT_ISOLATION_RADIUS_PX)
        if len(neighbors) > 1:
            continue
        if top_level_adu is not None:
            box = plane[center_y - half : center_y + half + 1, center_x - half : center_x + half + 1]
            if box.max() >= MOFFAT_SATURATION_MARGIN_FRACTION * top_level_adu:
                continue
        key = (min(int(x / columns * cells_x), cells_x - 1), min(int(y / rows * cells_y), cells_y - 1))
        cell = cell_lists.setdefault(key, [])
        if len(cell) < MOFFAT_CANDIDATES_PER_CELL:
            cell.append((float(x), float(y)))
    return list(cell_lists.values())


def measure_star_profiles(
    plane: np.ndarray,
    top_level_adu: float | None,
    stopwatch: _Stopwatch | None = None,
    sources: list[dict] | None = None,
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
        here when not given.

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
    if sources is None:
        sources = detect_stars_without_shape_cut(plane)
    if stopwatch is not None and stopwatch.expired():
        return StarProfileResult(reason=stopwatch.reason())
    if not sources:
        return StarProfileResult(reason="no stars were detected in the frame")
    if top_level_adu is None:
        top_level_adu = _plateau_saturation_level_adu(plane)
    cells = _grid_candidates(sources, plane.shape, plane, top_level_adu)
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
    if len(first_pass) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(first_pass),
            reason=(
                f"only {len(first_pass)} isolated, unsaturated stars fitted from {candidate_count} tried; "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed"
            ),
        )
    shared_beta = float(
        np.clip(np.median([fit.beta for _, fit in first_pass]), MOFFAT_BETA_MINIMUM, MOFFAT_BETA_MAXIMUM)
    )
    widths: list[float] = []
    elongations: list[float] = []
    angles: list[float] = []
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
        widths.append(math.sqrt(fit.fwhm_major_px * fit.fwhm_minor_px))
        elongations.append(shape[0])
        angles.append(shape[1])
    if len(widths) < MOFFAT_MINIMUM_STAR_COUNT:
        return StarProfileResult(
            candidate_count=candidate_count,
            star_count=len(widths),
            reason=(
                f"only {len(widths)} stars survived the second pass (shared beta {shared_beta:.2f}); "
                f"at least {MOFFAT_MINIMUM_STAR_COUNT} are needed"
            ),
        )
    width_array = np.array(widths)
    elongation_array = np.array(elongations)
    angle_array = np.array(angles)
    generator = np.random.default_rng(BOOTSTRAP_SEED)
    mean_angle, coherence = _circular_mean_axial_deg(angle_array)
    return StarProfileResult(
        width_px=float(np.median(width_array)),
        width_px_uncertainty=_bootstrap_median_error(width_array, generator),
        elongation=float(np.median(elongation_array)),
        elongation_uncertainty=_bootstrap_median_error(elongation_array, generator),
        angle_deg=mean_angle,
        angle_uncertainty_deg=_bootstrap_angle_error_deg(angle_array, generator),
        angle_coherence=coherence,
        star_count=len(widths),
        candidate_count=candidate_count,
        median_beta=shared_beta,
    )


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
    camera_profile: CameraProfile | None, frame: FrameRecord
) -> tuple[ProvenancedValue | None, ProvenancedValue | None, str]:
    """Find the gain and read noise the profile holds for a frame's setting.

    The lookup is exact: gain setting, offset and binning must all match. The
    frame record keeps the gain setting in its ``iso`` field (the header's
    ``GAIN`` card, or the ISO of a DSLR).

    Parameters
    ----------
    camera_profile : `CameraProfile` or `None`
        The camera's profile.
    frame : `FrameRecord`
        The frame.

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
    gap = camera_profile.sensor_noise_gap_reason(frame.iso, frame.offset, frame.binning)
    if gap is not None:
        return None, None, gap
    entry = camera_profile.sensor_noise_for(frame.iso, frame.offset, frame.binning)
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
) -> SkyElectrons:
    """Turn a plane's background into electrons.

    The sky signal in ADU is the background minus the bias level (the constant
    the camera adds to every pixel, which the raw frame carries in its pixel
    values; the header's ``OFFSET`` card is a camera setting, not that level in
    ADU). The signal times the gain is the sky in electrons for one exposure,
    and its square root is the noise of that sky per pixel (photon counting
    noise).

    Parameters
    ----------
    background_adu : `float`
        The sigma-clipped median of the plane, in ADU.
    bias_level_adu : `float` or `None`
        The bias level for the frame's setting, in ADU. `None` means unknown.
    gain : `ProvenancedValue` or `None`
        Electrons per ADU at the frame's setting.
    exposure_seconds : `float` or `None`
        The exposure time.
    gain_reason : `str`
        Why the gain is missing, used when it is.

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
            reason=(
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
            f"background {background_adu:.1f} ADU minus bias level {bias_level_adu:.1f} ADU, times "
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
    )
    if result.width_px is None:
        context.add_not_measured(star_metrics, result.reason, channel)
        return
    fit_note = (
        f"{result.star_count} stars from {result.candidate_count} tried, shared Moffat beta "
        f"{result.median_beta:.2f}{mosaic_note}"
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


def _emit_sky_measurements(
    context: _Context,
    sky: SkyElectrons,
    read_noise: ProvenancedValue | None,
    read_noise_reason: str,
    channel: str | None,
    mosaic_note: str,
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
    """
    if sky.rate_e_per_s_per_px is None:
        context.add(FRAME_SKY_RATE_E_PER_S_PER_PX, reason=sky.reason, channel=channel)
        context.add(FRAME_SKY_NOISE_E, reason=sky.reason, channel=channel)
    else:
        status = MeasurementStatus.MEASURED if sky.gain_is_measured else MeasurementStatus.ASSUMED
        note = sky.reason + ("" if sky.gain_is_measured else "; the gain is not measured") + mosaic_note
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

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One per frame metric (and per color plane for the star and sky
        metrics of a frame stored as color planes).
    """
    stopwatch = _Stopwatch(time_budget_seconds)
    night_id = observing_night_id(frame.timestamp) if frame.timestamp is not None else None
    context = _Context(
        frame=frame,
        target_id=target_id,
        run_id=run_id,
        job_id=job_id,
        measured_at=measured_at,
        capture_setting_id=capture_setting_id,
        subject_id=frame_subject_id(path, target_id),
        night_id=night_id,
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
        FRAME_SKY_RATE_E_PER_S_PER_PX,
        FRAME_SKY_NOISE_E,
    )
    gain, read_noise, noise_reason = look_up_frame_sensor_noise(camera_profile, frame)
    try:
        planes, collapsed, header = _read_planes(path)
    except FITS_READ_ERRORS as read_error:
        context.add_not_measured(pixel_metrics, f"the frame could not be read: {read_error}")
        context.add(FRAME_READ_NOISE_E, **_read_noise_arguments(read_noise, noise_reason))
        return context.measurements

    is_mosaic = bool(str(header.get("BAYERPAT", "")).strip()) and len(planes) == 1
    mosaic_note = "; raw color mosaic measured as it is, color pixels mixed" if is_mosaic else ""
    top_level = _usable_top_level_adu(camera_profile)
    exposure_seconds = _frame_exposure_seconds(frame)

    # Quick metrics first: they are returned even when the budget runs out.
    background_adu = _emit_background_and_saturation(context, collapsed, camera_profile)
    plane_backgrounds = {
        channel: background_adu if len(planes) == 1 else _clipped_median(plane) for channel, plane in planes
    }
    for channel, _plane in planes:
        sky = measure_sky_electrons(
            plane_backgrounds[channel], bias_level_adu, gain, exposure_seconds, noise_reason
        )
        _emit_sky_measurements(context, sky, read_noise, noise_reason, channel, mosaic_note)

    # Slow steps: star detection and fits.
    _emit_region_count(context, collapsed, stopwatch)
    _emit_gaussian_width(context, collapsed, stopwatch, measure_gaussian_width)
    for channel, plane in planes:
        if stopwatch.expired():
            result = StarProfileResult(reason=stopwatch.reason())
        else:
            try:
                result = measure_star_profiles(plane, top_level, stopwatch)
            except DATA_ERRORS as error:
                logger.warning("Moffat star profiles failed for %s: %s", path, error)
                result = StarProfileResult(reason=f"the star fits failed: {error}")
        _emit_star_profile_measurements(context, result, channel, mosaic_note)
    return context.measurements


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
        threshold = (
            float(camera_profile.saturation_threshold_adu.value)
            if camera_profile.saturation_threshold_can_be_reached
            else float(camera_profile.clip_ceiling_adu.value)
        )
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
        _, region_count, _, _ = _detect_stars(collapsed)
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
