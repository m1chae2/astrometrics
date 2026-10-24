"""Measures whether the flats' dust shadows still match the lights'.

Dust on the camera's window or on a filter casts faint ring-shaped shadows
("donuts") on every frame. A flat frame records the same shadows, so dividing
the lights by the master flat removes them. That only works while the shadows
sit in the same place. If the dust moved, new dust arrived, or dust was
cleaned off after the flats were taken, the lights' shadows differ from the
flat's, and the division leaves a bright and a dark copy of every donut in the
stack. This module measures that mismatch. It does not judge it. Whether to
retake the flats is a decision outside this library.

What the shift and the rotation mean. The dust shadows of this observatory's
flats are discs about 300 pixels across. Seen from an f/5.4 beam, a shadow
that wide comes from a speck about 6 mm in front of a sensor with 3.76
micrometer pixels, so the dust sits on the camera's window, which turns with
the sensor. The shadows therefore cannot show whether the camera turned
relative to the telescope's optics (a speck on the window turns with the
camera and the shadow pattern stays put on the sensor). The shift and rotation
found here are the shift and rotation of the shadow pattern on the sensor.
They are not the camera's rotation.

The master flat and the calibrated lights are not kept by the stacking step,
so both maps are rebuilt from the raw frames. The flat map is the mean of up
to 16 raw flats (not the master flat the stack used: that one is built by
Siril and is not kept). The light map is the median of up to 24 raw lights.
Both flats and lights are read twice per stack: once when the stack is
assessed (the flat assessment) and again here.

1. Each frame is averaged into cells of 8 x 8 sensor pixels (a color camera's
   2 x 2 groups are merged first, so a cell is always 8 x 8 raw pixels) and
   divided by its own median. For a light frame the bias level (the constant
   the camera adds to every pixel) is subtracted first, so the division gives
   the sky's true relative variation. The dust shadows of this observatory's
   flats are discs about 300 pixels across, so a cell of 8 pixels keeps their
   shape and cuts the pixel noise by 8.
2. A light frame is used only if its sky (above the bias level) is at least
   10 times the camera's read noise, in electrons. A fainter frame is left
   out and counted. Fewer than 8 usable frames: no map.
3. A disc of 25 cells (200 pixels) around the frame centre is set aside in
   every frame, flat and light. The pointing puts the target at the centre, so
   this keeps an extended target (a galaxy, a nebula) out of the map. A target
   larger than the disc, or one framed off the centre, is not covered. Cell
   level masks like this one and the star masks below cannot reuse the
   stacking step's empty-aperture mask: that mask is made on the stack in
   stack pixels, not on raw frames in sensor cells.
4. The light map is the pixelwise median of the frames, which are the frames
   that went into the stack, in time order. Stars are set aside in two
   passes. In each frame, the cells more than 4 robust standard deviations
   above the local sky, and the connected cells above 2 deviations around
   them, are set aside and enlarged by 2 cells. A faint star sits at the same
   pixels in every frame (the mount tracks), so the frame's own limit cannot
   find it, but the median of 24 frames is far quieter: a second pass on the
   median finds the cells more than 3 deviations above their neighbors at
   the scale of 3 cells (and the wings above 1.5), and sets them aside in
   every frame.
5. Both maps are band-passed. First the vignetting (darker corners) is
   removed: a polynomial of degree 4 in each direction is fitted to the map
   and divided out (see `_smooth_trend`). Then the map smoothed with a
   Gaussian of 2 cells (16 sensor pixels, which removes the noise and the
   pixel-scale fixed pattern of the sensor) is divided by the map smoothed
   with a Gaussian of 30 cells (240 sensor pixels, the local level), minus 1.
   The result is the relative dip or rise on the scale of the dust shadows.
   Where cells were set aside, the smoothing uses only the cells that remain
   (normalized convolution). Cells within 16 cells (128 pixels) of the edge
   are left out: there the smoothing leans on one side only.

The match is then measured on the two band-passed maps:

- The correlation coefficient (Pearson) at zero offset.
- The same coefficient with the flat's dust shadows masked out (a control).
  If the correlation stays high without the shadows, something other than
  dust is shared by the flat and the lights (a leftover of the vignetting, a
  fixed pattern), and the first value must not be read as dust.
- The shift and rotation of the lights' shadow pattern that give the highest
  correlation, and the correlation there. The search runs a cross-correlation
  by fast Fourier transform for every rotation angle. Angles run over plus or
  minus 3 degrees in steps of 0.25 degree. The best angle is refined in steps
  of 0.05 degree, and the shift and the angle get a parabola fit for a
  sub-step value. A shift is searched over plus or minus 20 cells (160 sensor
  pixels).
- The chance level of that search. Trying 41 x 41 shifts at 25 angles finds a
  high score by luck alone. The same search is therefore run at 8 displaced
  placements (the lights' map moved by 60 cells along and across the
  axes and diagonally, where the two patterns cannot be the same dust), and
  the best score of each is kept. The best score of the real search is read
  against the mean and the spread of those 8 (``excess sigmas``).
- The standard deviation of the correlation at displacements of 40 to 100
  cells (320 to 800 pixels) with no rotation: a rougher chance spread for
  reading the zero-offset correlation.
- How well the placement is known. The same search is run on the light map of
  the odd frames and on the light map of the even frames. Half the difference
  between the two placements is a noise-only estimate from one split pair
  (each half has twice the noise variance of the whole). It is combined in
  quadrature with the method error found by injection at real shadow depths
  (`METHOD_SHIFT_ERROR_PX`, `METHOD_ROTATION_ERROR_DEG`).
- The number of dust shadows found in the flat map: connected groups of at
  least 25 cells that dip below the larger of 5 standard deviations of the
  flat map's noise (from the difference of the maps of the odd and even
  flats) and 0.15% of the level. This count is a diagnostic only. It depends
  on those limits and it does not gate any other row.
- The correlation between a light map built from the odd frames and one built
  from the even frames. It says how repeatable the light map is. The light
  map holds the dust and also whatever else repeats from frame to frame (the
  sensor's fixed pattern, stars too faint to find), so the value is a ceiling
  for how much of the light map any flat could explain, not a measure of dust.

Position convention. A pattern point at ``(x, y)`` (column, row, counted from
the array's first element) in the flat appears in the lights at
``R(theta) (x + sx - cx, y + sy - cy) + (cx, cy)``: the point is moved by the
shift ``(sx, sy)`` first and then turned by ``theta`` about the sensor centre
``(cx, cy)``. ``R`` is the usual rotation matrix, so a positive angle turns
the pattern clockwise when the array is drawn with its first row at the top.

A shift and a rotation are given only when the best score stands at least
``PLACEMENT_LIMIT_SIGMAS`` null spreads above the mean best score at displaced
placements, and the best placement does not sit at the edge of the search.
Otherwise there is no placement to describe, so those rows are not measured.
The dust shadows are broad, so the correlation changes slowly with the
placement: read the placement together with its uncertainty.

A time limit (from the configuration) is checked before each frame is read.
When it is reached nothing is measured and the reason says how many frames
were read. Nothing here judges the numbers. A value that cannot be worked out
is stored as not measured, with the reason, and a failure never fails the
stack.
"""

import logging
import math
import os
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import scipy.fft
from scipy import ndimage

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, open_primary_hdu
from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject
from astrometricslib.pipelines.shared.quality.frame_measurement import measure_clipped_mean_adu
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_FLAT_DUST_SHADOW_COUNT,
    STACKING_FLAT_LIGHT_BEST_CORRELATION,
    STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
    STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
    STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
    STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS,
    STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD,
    STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET,
    STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS,
    STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION,
    STACKING_METRICS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TIME_LIMIT_SECONDS",
    "MAP_CELL_PX",
    "FlatMatchOutcome",
    "SkyRequirement",
    "band_pass_dust_map",
    "correlation_outside_shadows",
    "count_dust_shadows",
    "find_dust_shadows",
    "flat_match_measurements",
    "flat_match_rows",
    "match_dust_maps",
    "measure_flat_match",
    "sky_requirement_for_frame",
]

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}

# Sensor pixels along one side of a map cell. The flats of this observatory
# show dust shadows about 300 pixels across (a speck on a window about 6 mm
# from the sensor, seen from an f/5.4 beam, casts a shadow 1.1 mm, or about
# 300 pixels of 3.76 microns, wide), so 8 pixels keeps their edges, and
# averaging 64 pixels lowers the pixel noise 8 times. Dust nearer the sensor
# casts smaller shadows, which an 8-pixel cell still resolves down to about
# 50 pixels.
MAP_CELL_PX = 8

# Gaussian widths of the band pass, in cells. The low width (2 cells, 16
# pixels) removes the noise and the sensor's fixed pattern, which repeats from
# frame to frame in the raw lights and has a scale of 1 to 2 cells (it gave a
# repeatable "map" with a correlation of 0.73 between the odd and even frames
# and almost no relation to the flat when the width was 1 cell). The high
# width (30 cells, 240 pixels) is the local level: about the width of a dust
# shadow's edge region and far narrower than the vignetting, which spans the
# sensor. Both were chosen on the M 57 night, the flat of the same afternoon
# (register entry STK-46).
LOW_PASS_SIGMA_CELLS = 2.0
HIGH_PASS_SIGMA_CELLS = 30.0

# Frames read at most. The flats are bright (about 28,000 ADU) and the dust
# shows as a fraction of a percent to a few percent, so 16 flats put the map's
# noise (0.003% on the real set) far below it. The lights are faint (a sky of
# 400 to 800 ADU), so 24 are read: the median of N frames has about 1.25 /
# sqrt(N) of one frame's noise. The cap keeps the arrays at 24 x 750 x 500
# floats (36 MB) for the largest camera.
MAXIMUM_FLAT_FRAMES = 16
MAXIMUM_LIGHT_FRAMES = 24
# The odd and even halves need at least 4 frames each to have a median that
# means anything.
MINIMUM_LIGHT_FRAMES = 8

# A light frame is used only when its sky, above the bias level, is at least
# this many times the camera's read noise (in electrons). Below that the
# frame's cells are dominated by read noise, not by the sky's relative
# variation that carries the dust. The factor 10 is a round number chosen
# for the brief, not tuned; at the ASI 533MM's 3.8 e- it asks for 38 e- of sky,
# 49 ADU, and the real lights have 400 to 800 ADU.
SKY_READ_NOISE_FACTOR = 10.0

# Bias frames read to find the bias level: the median of the clipped means of
# up to this many frames (the same estimator and count as the frame
# measurement's bias finder, `BIAS_FRAMES_FOR_LEVEL`).
BIAS_FRAMES_FOR_LEVEL = 5

# The target is at the frame centre (the mount is pointed at it), so a disc
# of this radius around the centre is set aside in every frame. 25 cells is
# 200 pixels: 6.4 arcminutes on the ASI 533MM at 1.9 arcsec/px. A target that
# is larger, or framed off the centre, is not covered.
TARGET_MASK_RADIUS_CELLS = 25.0

# A cell this many robust standard deviations above the local sky is a star or
# a hot pixel; the connected cells above the lower limit around it are its
# wings; all are set aside together with the cells within 2 cells of them.
STAR_MASK_SIGMAS = 4.0
STAR_WING_SIGMAS = 2.0
STAR_MASK_GROW_CELLS = 2
# The second pass on the median map (see `_build_light_maps`).
MAP_STAR_LOCAL_SIGMA_CELLS = 3.0
MAP_STAR_CORE_SIGMAS = 3.0
MAP_STAR_WING_SIGMAS = 1.5

# A dust shadow in the flat map: at least this deep (standard deviations of the
# flat map's noise) and at least this deep as a fraction of the level, and at
# least this many cells (40 x 40 pixels). On the M 57 flat the shadows are
# 0.15 to 0.4% deep in the band-passed map, and the light map's noise is
# 0.03%, so 0.15% is 5 times the light map's noise: a shadow of that depth
# can show in the lights. The count depends on these limits (register entry
# STK-46 lists it for others), so it is a count of shadows above them, not of
# all the dust. It is a diagnostic: no other row waits on it.
SHADOW_MINIMUM_SIGMAS = 5.0
SHADOW_MINIMUM_DEPTH_FRACTION = 0.0015
SHADOW_MINIMUM_AREA_CELLS = 25
# The shadows are enlarged by this many cells before the control correlation
# leaves them out, so their soft edges are out too (a shadow's edge is about
# 10 pixels wide, a cell is 8, and the band pass spreads it by 2 cells).
SHADOW_MASK_GROW_CELLS = 4

# The degree of the polynomial that removes the vignetting before the band pass
# (see `_smooth_trend`).
TREND_DEGREE = 4

# Cells this close to the edge are not compared: near the edge the smoothing
# leans on cells on one side only. Two low-pass widths (32 pixels) is far
# enough in that the flattened maps have no gradient left to bias it.
EDGE_MARGIN_CELLS = 16

# Search range, in cells. Dust that moved more than 160 pixels is not the same
# dust.
MAXIMUM_SHIFT_CELLS = 20
COARSE_ANGLE_RANGE_DEG = 3.0
COARSE_ANGLE_STEP_DEG = 0.25
FINE_ANGLE_STEP_DEG = 0.05
FINE_ANGLE_COUNT = 3

# The chance level of the correlation. Two maps of a few broad shadows
# correlate by chance with a spread of 0.1 to 0.3, so the size of a
# correlation means little by itself. The spread is measured on the same
# maps: the correlation at displacements of 40 to 100 cells (320 to 800
# pixels) is that of patterns that cannot be the same dust (the shadows are
# about 40 cells across, so beyond 40 cells their own overlap is gone).
DISPLACED_MINIMUM_SHIFT_CELLS = 40
DISPLACED_MAXIMUM_SHIFT_CELLS = 100

# The null of the search. The search takes the best of 25 angles and 41 x 41
# shifts, so a high score happens by chance. The same search is repeated with
# the lights' map moved by these offsets, in cells (rows, columns). Each is 60
# cells, three times the search window's half width and 1.5 times a shadow's
# width, so the search window around it does not touch the dust's own
# overlap.
NULL_OFFSETS_CELLS: tuple[tuple[int, int], ...] = (
    (-60, 0),
    (60, 0),
    (0, -60),
    (0, 60),
    (-60, -60),
    (-60, 60),
    (60, -60),
    (60, 60),
)
# A placement is described only when the best score stands this many null
# spreads above the mean of the null's best scores. 3.1 is the 99th
# percentile of that statistic on 101 pairs with unrelated dust (56 synthetic
# pairs at real shadow depth and 45 real lights scored against flipped copies
# of the real flat map; the largest value was 3.29). Measured false-alarm
# rate at this limit: 1 pair of 101 (register entry STK-46). The 5 spreads
# this replaces had no measurement behind it.
PLACEMENT_LIMIT_SIGMAS = 3.1

# The error of the method itself, from injecting 8 known displacements into 8
# random dust sets at real shadow depths (0.15 to 0.4%) and real noise: the
# root-mean-square error was 1.06 pixel along x, 0.98 along y and 0.19 degree
# in rotation (register entry STK-46). It is added in quadrature to the
# odd/even half-difference, which sees noise only.
METHOD_SHIFT_ERROR_PX = 1.1
METHOD_ROTATION_ERROR_DEG = 0.2

# A sensor of fewer cells than this cannot hold the band pass.
MINIMUM_MAP_CELLS = 64

# The time limit used when the configuration gives none.
DEFAULT_TIME_LIMIT_SECONDS = 300.0

_NO_FLATS_REASON = "the stack's flat frame paths were not recorded, or the stack used no flat frames"
_SPECTRAL_REASON = (
    "a spectral stack's lights carry the grating's spectra, which the star mask does not remove, "
    "so the dust map of the lights is not built"
)
_NO_BIAS_REASON = (
    "the stack used no bias frames, so the bias level of the lights is unknown (the camera profile "
    "holds no bias level), and the lights' relative variation cannot be found"
)


@dataclass(frozen=True)
class SkyRequirement:
    """How bright a light frame's sky must be to be used.

    Parameters
    ----------
    minimum_adu : `float` or `None`
        The least sky above the bias level, in ADU. `None` when it cannot be
        worked out (no read noise or gain for the camera setting).
    reason : `str`, optional
        Why `minimum_adu` is `None`, or empty.
    """

    minimum_adu: float | None
    reason: str = ""


@dataclass(frozen=True)
class DustMatch:
    """What the search found about the two maps' alignment.

    Parameters
    ----------
    correlation_at_zero : `float`
        Pearson correlation of the two band-passed maps with no shift or
        rotation.
    best_correlation : `float`
        Pearson correlation at the best shift and rotation.
    shift_x_px, shift_y_px : `float`
        The best shift, in sensor pixels.
    rotation_deg : `float`
        The best rotation of the shadow pattern, in degrees, about the sensor
        centre.
    at_edge : `bool`
        Whether the peak sits at the edge of the search range.
    displaced_std : `float` or `None`
        The standard deviation of the search score at displaced placements
        with no rotation (see ``DISPLACED_MINIMUM_SHIFT_CELLS``); `None` when
        the maps are too small to have any or the null was not run.
    excess_sigmas : `float` or `None`
        How many null spreads the best coarse score stands above the mean of
        the null's best scores; `None` when the null was not run or has no
        spread.
    """

    correlation_at_zero: float
    best_correlation: float
    shift_x_px: float
    shift_y_px: float
    rotation_deg: float
    at_edge: bool
    displaced_std: float | None
    excess_sigmas: float | None


@dataclass(frozen=True)
class DustShadows:
    """The dust shadows found in a band-passed flat map.

    Parameters
    ----------
    count : `int`
        Connected groups of at least ``SHADOW_MINIMUM_AREA_CELLS`` cells that
        dip below the threshold.
    threshold : `float`
        The depth a cell had to reach (a positive fraction of the level).
    mask : `numpy.ndarray`
        `True` in the cells of those groups (not enlarged).
    """

    count: int
    threshold: float
    mask: np.ndarray


@dataclass(frozen=True)
class FlatMatchOutcome:
    """Everything measured about the flat and the lights, with reasons.

    Parameters
    ----------
    flat_frame_count, light_frame_count : `int`
        How many frames were read to build each map.
    flat_shadow_count : `int` or `None`
        Dust shadows in the flat map (a diagnostic); `None` when the flat map
        was not built.
    correlation_at_zero : `float` or `None`
        Correlation at zero offset.
    correlation_outside_shadows : `float` or `None`
        The same with the flat's shadows (enlarged) masked out.
    best_correlation, shift_x_px, shift_y_px, rotation_deg : `float` or `None`
        The best match (see `DustMatch`); `None` when there is none to give.
    shift_x_uncertainty_px, shift_y_uncertainty_px : `float` or `None`
        Half the difference between the shifts found from the odd and the even
        light frames (one split pair, noise only) combined in quadrature with
        ``METHOD_SHIFT_ERROR_PX``; `None` when either half found no
        placement.
    rotation_uncertainty_deg : `float` or `None`
        The same for the rotation, with ``METHOD_ROTATION_ERROR_DEG``.
    displaced_correlation_std : `float` or `None`
        The chance spread of the correlation (see `DustMatch.displaced_std`).
    search_excess_sigmas : `float` or `None`
        See `DustMatch.excess_sigmas`.
    split_half_correlation : `float` or `None`
        Correlation between the maps of the odd and even light frames.
    lights_left_out_faint : `int`
        Sampled light frames left out because their sky was under the
        requirement.
    reasons : `dict` [`str`, `str`]
        The reason for each metric that has no value, by metric name.
    """

    flat_frame_count: int = 0
    light_frame_count: int = 0
    flat_shadow_count: int | None = None
    correlation_at_zero: float | None = None
    correlation_outside_shadows: float | None = None
    best_correlation: float | None = None
    shift_x_px: float | None = None
    shift_y_px: float | None = None
    rotation_deg: float | None = None
    shift_x_uncertainty_px: float | None = None
    shift_y_uncertainty_px: float | None = None
    rotation_uncertainty_deg: float | None = None
    displaced_correlation_std: float | None = None
    search_excess_sigmas: float | None = None
    split_half_correlation: float | None = None
    lights_left_out_faint: int = 0
    reasons: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _FrameCells:
    """A frame averaged into cells.

    Parameters
    ----------
    cells : `numpy.ndarray` or `None`
        The cells divided by the frame's median; `None` when that median
        (after the bias is subtracted) is not positive.
    level_adu : `float`
        The median of the cells, in ADU above the bias level subtracted.
    """

    cells: np.ndarray | None
    level_adu: float


@dataclass(frozen=True)
class _LightMaps:
    """The light maps and what went into them.

    Parameters
    ----------
    full_map, odd_map, even_map : `numpy.ndarray` or `None`
        The median of all the frames used, of the 1st, 3rd, ... and of the
        2nd, 4th, ...; `None` when too few frames were usable.
    used : `int`
        How many frames were used.
    sampled : `int`
        How many frames were chosen for reading.
    too_faint : `int`
        How many were left out for a sky under the requirement.
    unusable : `int`
        How many could not be read, differ in size from the first, or have a
        median that is not positive.
    """

    full_map: np.ndarray | None
    odd_map: np.ndarray | None
    even_map: np.ndarray | None
    used: int
    sampled: int
    too_faint: int
    unusable: int


class _TimeLimitReachedError(Exception):
    """The time limit passed before a frame was read.

    Parameters
    ----------
    limit_seconds : `float`
        The limit.
    frames_read : `int`
        How many frames had been read.
    """

    def __init__(self, limit_seconds: float, frames_read: int) -> None:
        super().__init__(f"time limit of {limit_seconds:g} s reached after {frames_read} frames")
        self.limit_seconds = limit_seconds
        self.frames_read = frames_read


class _FrameReader:
    """Reads frames and stops when the time limit has passed.

    The clock is checked before each frame is read, so a slow mount cannot
    hold up the stack's quality step for more than the limit plus one read.

    Parameters
    ----------
    limit_seconds : `float` or `None`
        The limit, counted from the reader's creation; `None` for none.
    clock : `Callable` [[], `float`]
        Gives the time in seconds (a monotonic clock; tests pass a fake).
    """

    def __init__(self, limit_seconds: float | None, clock: Callable[[], float]) -> None:
        self.limit_seconds = limit_seconds
        self.clock = clock
        self.started = clock() if limit_seconds is not None else 0.0
        self.frames_read = 0

    def check(self) -> None:
        """Count one more frame, or stop if the limit has passed.

        Raises
        ------
        _TimeLimitReachedError
            If the limit has passed.
        """
        if self.limit_seconds is not None and self.clock() - self.started >= self.limit_seconds:
            raise _TimeLimitReachedError(self.limit_seconds, self.frames_read)
        self.frames_read += 1

    def read_cells(self, path: str, bias_level_adu: float = 0.0) -> _FrameCells | None:
        """Read one frame into cells after checking the clock.

        Parameters
        ----------
        path : `str`
            A FITS file.
        bias_level_adu : `float`, optional
            The bias level to subtract, in ADU.

        Returns
        -------
        frame : `_FrameCells` or `None`
            The cells; `None` when the file cannot be read or is not a
            single plane of enough size. Raises `_TimeLimitReachedError` if
            the limit has passed.
        """
        self.check()
        return _read_cells(path, bias_level_adu)

    def read_level(self, path: str) -> float | None:
        """Read one frame's sigma-clipped mean after checking the clock.

        Parameters
        ----------
        path : `str`
            A FITS file (a bias frame).

        Returns
        -------
        level_adu : `float` or `None`
            The clipped mean, or `None` when the file cannot be read. Raises
            `_TimeLimitReachedError` if the limit has passed.
        """
        self.check()
        try:
            with open_primary_hdu(path) as hdu:
                if hdu.data is None:
                    return None
                plane = np.asarray(collapse_to_2d(hdu.data), dtype=np.float64)
            level = measure_clipped_mean_adu(plane)
        except (*FITS_READ_ERRORS, OSError):
            logger.debug("Could not read %s for the bias level", path)
            return None
        return level if math.isfinite(level) else None


def _all_metrics_missing(reason: str, **kept: Any) -> FlatMatchOutcome:
    """Build an outcome in which every value is missing for one reason.

    Parameters
    ----------
    reason : `str`
        Why nothing was measured.
    **kept
        Fields of `FlatMatchOutcome` that do have a value.

    Returns
    -------
    outcome : `FlatMatchOutcome`
        The outcome, with the reason against every metric the caller left out.
    """
    names = (
        STACKING_FLAT_DUST_SHADOW_COUNT,
        STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET,
        STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS,
        STACKING_FLAT_LIGHT_BEST_CORRELATION,
        STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
        STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
        STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
        STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD,
        STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS,
        STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION,
    )
    return FlatMatchOutcome(reasons=dict.fromkeys(names, reason), **kept)


def _evenly_spaced(items: Sequence[Any], cap: int) -> list[Any]:
    """Pick at most `cap` items spread evenly through a sequence.

    Parameters
    ----------
    items : `Sequence`
        The items, in the order that should be spread over (capture time).
    cap : `int`
        The most items to pick.

    Returns
    -------
    picked : `list`
        All items when there are `cap` or fewer; otherwise `cap` items at
        evenly spaced positions, first and last included.
    """
    if len(items) <= cap:
        return list(items)
    positions = np.unique(np.round(np.linspace(0, len(items) - 1, cap)).astype(int))
    return [items[int(position)] for position in positions]


def _read_cells(path: str, bias_level_adu: float = 0.0) -> _FrameCells | None:
    """Read a frame and average it into map cells, divided by its median.

    Parameters
    ----------
    path : `str`
        A FITS file.
    bias_level_adu : `float`, optional
        The bias level, subtracted from the cells before they are divided by
        their median. Left 0 for a flat, whose level is thousands of ADU.

    Returns
    -------
    frame : `_FrameCells` or `None`
        The cells of ``MAP_CELL_PX`` raw pixels, divided by the median of the
        frame (the cells are `None` when that median is not positive). `None`
        when the file cannot be read, is not a single plane, or is too small.
    """
    try:
        with open_primary_hdu(path) as hdu:
            data = hdu.data
            header = hdu.header
            if data is None:
                return None
            uses_color_filter_array = bool(str(header.get("BAYERPAT", "")).strip())
            plane = np.asarray(collapse_to_2d(data), dtype=np.float32)
    except (*FITS_READ_ERRORS, OSError):
        logger.debug("Could not read %s for the dust map", path)
        return None
    if plane.ndim != 2:
        return None
    block = MAP_CELL_PX
    if uses_color_filter_array:
        # Merge each 2 x 2 group of the mosaic first, so the cell does not
        # depend on which color a pixel is.
        rows, columns = (size - size % 2 for size in plane.shape)
        plane = plane[:rows, :columns].reshape(rows // 2, 2, columns // 2, 2).mean(axis=(1, 3))
        block = MAP_CELL_PX // 2
    rows, columns = (size - size % block for size in plane.shape)
    if rows < MINIMUM_MAP_CELLS * block or columns < MINIMUM_MAP_CELLS * block:
        return None
    cells = plane[:rows, :columns].reshape(rows // block, block, columns // block, block).mean(axis=(1, 3))
    cells = cells - np.float32(bias_level_adu)
    median = float(np.median(cells))
    if not math.isfinite(median):
        return None
    if median <= 0:
        return _FrameCells(None, median)
    return _FrameCells((cells / median).astype(np.float32), median)


def _target_disc(shape: tuple[int, int], radius_cells: float) -> np.ndarray:
    """Mark the cells of the disc around the frame centre.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        The map's shape (rows, columns).
    radius_cells : `float`
        The disc's radius in cells.

    Returns
    -------
    disc : `numpy.ndarray`
        `True` in the cells whose centres lie within the radius of the map's
        centre.
    """
    rows = np.arange(shape[0], dtype=np.float64) - (shape[0] - 1) / 2.0
    columns = np.arange(shape[1], dtype=np.float64) - (shape[1] - 1) / 2.0
    return np.hypot(rows[:, None], columns[None, :]) <= radius_cells


def _smooth_without_gaps(cells: np.ndarray, weights: np.ndarray, sigma: float) -> np.ndarray:
    """Smooth a map with a Gaussian, counting only the cells that have a value.

    Parameters
    ----------
    cells : `numpy.ndarray`
        The map. The values where `weights` is 0 are ignored.
    weights : `numpy.ndarray`
        1.0 where a cell has a value, 0.0 where it was set aside.
    sigma : `float`
        The Gaussian width in cells.

    Returns
    -------
    smoothed : `numpy.ndarray`
        The weighted mean of the nearby cells; NaN where less than a
        thousandth of a cell's weight is near.
    """
    values = np.where(weights > 0, cells, 0.0).astype(np.float32)
    numerator = ndimage.gaussian_filter(values * weights, sigma)
    denominator = ndimage.gaussian_filter(weights, sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(denominator > 1e-3, numerator / denominator, np.nan).astype(np.float32)


def _smooth_trend(cells: np.ndarray, usable: np.ndarray) -> np.ndarray:
    """Fit the slow brightness change across a map (the vignetting).

    A polynomial of degree 4 in each direction (25 terms) is fitted to the
    cells by least squares on every 4th cell in each direction, and the fit is
    repeated twice leaving out the cells that sit more than 3 robust standard
    deviations from it (dust, stars). The polynomial cannot follow a dust
    shadow that is a tenth of the sensor wide, so the dust stays in the
    residual, while the vignetting (which has no sharp feature) is removed.
    Without this step the 30-cell Gaussian would leave a false dip or rise of
    a few tenths of a percent where the vignetting curves.

    Parameters
    ----------
    cells : `numpy.ndarray`
        A map of the level in each cell.
    usable : `numpy.ndarray`
        `True` where the cell holds a finite value.

    Returns
    -------
    trend : `numpy.ndarray`
        The fitted level in every cell, never less than a thousandth.
    """
    rows, columns = cells.shape
    row_axis = np.linspace(-1.0, 1.0, rows)
    column_axis = np.linspace(-1.0, 1.0, columns)
    stride = 4
    sample_rows = row_axis[::stride]
    sample_columns = column_axis[::stride]
    design = np.polynomial.legendre.legvander2d(
        *(axis.ravel() for axis in np.meshgrid(sample_rows, sample_columns, indexing="ij")),
        [TREND_DEGREE, TREND_DEGREE],
    )
    values = cells[::stride, ::stride].astype(np.float64).ravel()
    keep = usable[::stride, ::stride].ravel().copy()
    coefficients = np.zeros(design.shape[1])
    for _pass in range(3):
        if int(np.count_nonzero(keep)) < design.shape[1] * 4:
            break
        coefficients, *_unused = np.linalg.lstsq(design[keep], values[keep], rcond=None)
        residual = values - design @ coefficients
        spread = 1.4826 * float(np.median(np.abs(residual[keep])))
        keep = usable[::stride, ::stride].ravel() & (np.abs(residual) < 3.0 * max(spread, 1e-6))
    full = np.polynomial.legendre.legvander2d(
        *(axis.ravel() for axis in np.meshgrid(row_axis, column_axis, indexing="ij")),
        [TREND_DEGREE, TREND_DEGREE],
    )
    trend = (full @ coefficients).reshape(rows, columns)
    return np.clip(trend, 1e-3, None).astype(np.float32)


def band_pass_dust_map(
    cells: np.ndarray, has_value: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Keep the structure on the scale of a dust shadow and drop the rest.

    The slow trend (the vignetting) is divided out first, then the map smoothed
    with ``LOW_PASS_SIGMA_CELLS`` is divided by the map smoothed with
    ``HIGH_PASS_SIGMA_CELLS``.

    Parameters
    ----------
    cells : `numpy.ndarray`
        A map of the level in each cell (about 1 for a frame divided by its
        median). NaN, or a cell where `has_value` is `False`, is skipped.
    has_value : `numpy.ndarray`, optional
        `True` where the cell holds a usable value. Omitted, the finite cells.

    Returns
    -------
    band : `numpy.ndarray`
        The relative dip (negative) or rise of each cell. 0 where the cell is
        not usable.
    valid : `numpy.ndarray`
        `True` where `band` can be compared: usable and at least
        ``EDGE_MARGIN_CELLS`` from the edge.
    """
    usable = np.isfinite(cells)
    if has_value is not None:
        usable &= has_value
    flattened = np.where(usable, cells, np.nan) / _smooth_trend(np.where(usable, cells, 0.0), usable)
    weights = usable.astype(np.float32)
    low = _smooth_without_gaps(flattened, weights, LOW_PASS_SIGMA_CELLS)
    high = _smooth_without_gaps(flattened, weights, HIGH_PASS_SIGMA_CELLS)
    with np.errstate(divide="ignore", invalid="ignore"):
        band = low / high - 1.0
    valid = usable & np.isfinite(band) & (high > 0)
    margin = EDGE_MARGIN_CELLS
    valid[:margin, :] = False
    valid[-margin:, :] = False
    valid[:, :margin] = False
    valid[:, -margin:] = False
    return np.where(valid, band, 0.0).astype(np.float32), valid


def find_dust_shadows(band: np.ndarray, valid: np.ndarray, noise_sigma: float | None = None) -> DustShadows:
    """Find the dust shadows in a band-passed flat map.

    Parameters
    ----------
    band : `numpy.ndarray`
        The band-passed flat map (see `band_pass_dust_map`).
    valid : `numpy.ndarray`
        Which cells of `band` can be used.
    noise_sigma : `float`, optional
        The noise of the band-passed map (a standard deviation). Left out, the
        robust spread of the map itself is used, which also holds the dust and
        so makes the threshold higher.

    Returns
    -------
    shadows : `DustShadows`
        The number of connected groups (neighbors on a side or a corner) of at
        least ``SHADOW_MINIMUM_AREA_CELLS`` cells that dip below the threshold,
        the threshold (the larger of ``SHADOW_MINIMUM_SIGMAS`` times the noise
        and ``SHADOW_MINIMUM_DEPTH_FRACTION``) and the cells of those groups.
    """
    values = band[valid]
    empty = np.zeros(band.shape, dtype=bool)
    if values.size == 0:
        return DustShadows(0, SHADOW_MINIMUM_DEPTH_FRACTION, empty)
    if noise_sigma is None:
        noise_sigma = 1.4826 * float(np.median(np.abs(values - np.median(values))))
    threshold = max(SHADOW_MINIMUM_SIGMAS * noise_sigma, SHADOW_MINIMUM_DEPTH_FRACTION)
    labels, group_count = ndimage.label((band < -threshold) & valid, structure=np.ones((3, 3), dtype=int))
    if group_count == 0:
        return DustShadows(0, threshold, empty)
    areas = np.bincount(labels.ravel())[1:]
    large = np.concatenate([[False], areas >= SHADOW_MINIMUM_AREA_CELLS])
    return DustShadows(int(np.count_nonzero(large)), threshold, large[labels])


def correlation_outside_shadows(
    flat_band: np.ndarray,
    flat_valid: np.ndarray,
    light_band: np.ndarray,
    light_valid: np.ndarray,
    shadow_mask: np.ndarray,
) -> float | None:
    """Correlate the two maps with the flat's shadows left out (a control).

    The shadows are enlarged by ``SHADOW_MASK_GROW_CELLS`` first. The band
    pass leaves a faint halo of the opposite sign around each shadow (the
    local level it divides by is lowered by the shadow). The halo is dust
    signal too and the enlarged mask does not reach it, so a flat and lights
    that share only dust still give a positive value when the dust is deep
    compared with the noise. Read the value against the one with the
    shadows: a value as large as that one means the correlation does not
    come from the shadows' cores.

    Parameters
    ----------
    flat_band, light_band : `numpy.ndarray`
        The band-passed maps (see `band_pass_dust_map`), of the same shape.
    flat_valid, light_valid : `numpy.ndarray`
        Where each map can be used.
    shadow_mask : `numpy.ndarray`
        The cells of the flat's dust shadows (see `find_dust_shadows`).

    Returns
    -------
    correlation : `float` or `None`
        The Pearson coefficient at zero shift and rotation over the cells
        that remain; `None` when fewer than 100 remain or a map has no spread.
    """
    outside = ~ndimage.binary_dilation(shadow_mask, iterations=SHADOW_MASK_GROW_CELLS)
    return _pearson(flat_band, light_band, flat_valid & light_valid & outside)


def count_dust_shadows(
    band: np.ndarray, valid: np.ndarray, noise_sigma: float | None = None
) -> tuple[int, float]:
    """Count the dust shadows in a band-passed flat map.

    Parameters
    ----------
    band : `numpy.ndarray`
        The band-passed flat map (see `band_pass_dust_map`).
    valid : `numpy.ndarray`
        Which cells of `band` can be used.
    noise_sigma : `float`, optional
        The noise of the band-passed map (see `find_dust_shadows`).

    Returns
    -------
    count : `int`
        Connected groups of at least ``SHADOW_MINIMUM_AREA_CELLS`` cells that
        dip below the threshold.
    threshold : `float`
        The depth a cell had to reach.
    """
    shadows = find_dust_shadows(band, valid, noise_sigma)
    return shadows.count, shadows.threshold


def _star_mask(cells: np.ndarray, local_sigma: float, core_sigmas: float, wing_sigmas: float) -> np.ndarray:
    """Find the cells of a map that hold stars or hot pixels.

    Parameters
    ----------
    cells : `numpy.ndarray`
        A frame's or a median map's cells, divided by the frame's median. NaN
        cells are ignored.
    local_sigma : `float`
        The Gaussian width, in cells, that gives the local level a cell is
        compared with.
    core_sigmas : `float`
        A cell this many robust standard deviations above its local level is
        the core of a star.
    wing_sigmas : `float`
        The wings of the star are the connected cells above this lower limit.

    Returns
    -------
    mask : `numpy.ndarray`
        `True` for the cells of every star (core and wings) and the cells
        within ``STAR_MASK_GROW_CELLS`` of them.
    """
    usable = np.isfinite(cells)
    local = _smooth_without_gaps(cells, usable.astype(np.float32), local_sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        residual = cells / local - 1.0
    residual = np.nan_to_num(residual, nan=0.0, posinf=0.0, neginf=0.0)
    sigma = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    if sigma <= 0:
        return np.zeros(cells.shape, dtype=bool)
    cores = residual > core_sigmas * sigma
    # A star's wings stay above the sky well past its core. Take every
    # connected group of cells above the lower limit that holds a core cell.
    wings = residual > wing_sigmas * sigma
    labels, group_count = ndimage.label(wings, structure=np.ones((3, 3), dtype=int))
    if group_count == 0:
        return cores
    hit = np.zeros(group_count + 1, dtype=bool)
    hit[np.unique(labels[cores])] = True
    hit[0] = False
    return ndimage.binary_dilation(hit[labels], iterations=STAR_MASK_GROW_CELLS)


def _median_without_warnings(stack: np.ndarray) -> np.ndarray:
    """Take the median over the first axis, ignoring NaN, without warnings.

    Parameters
    ----------
    stack : `numpy.ndarray`
        Frames along the first axis; NaN marks a cell that was set aside.

    Returns
    -------
    median : `numpy.ndarray`
        The median of the cells that remain; NaN where none remain.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", category=RuntimeWarning)
        return np.nanmedian(stack, axis=0).astype(np.float32)


def _read_flat_cells(
    flat_paths: Sequence[str], reader: _FrameReader, target_radius_cells: float
) -> list[np.ndarray]:
    """Read the flat frames into maps of cells.

    Parameters
    ----------
    flat_paths : `Sequence` [`str`]
        The flat frames the stack was calibrated with.
    reader : `_FrameReader`
        Reads each frame and enforces the time limit.
    target_radius_cells : `float`
        The radius of the disc set aside around the frame centre.

    Returns
    -------
    flat_cells : `list` of `numpy.ndarray`
        The cells of at most ``MAXIMUM_FLAT_FRAMES`` frames spread evenly
        through the set, with the target disc set to NaN. Frames that cannot
        be read, or that differ in size from the first one read, are skipped.
        Raises `_TimeLimitReachedError` if the time limit passes before a
        frame is read.
    """
    frames: list[np.ndarray] = []
    for path in _evenly_spaced(sorted(flat_paths), MAXIMUM_FLAT_FRAMES):
        frame = reader.read_cells(path)
        if frame is None or frame.cells is None or (frames and frame.cells.shape != frames[0].shape):
            continue
        cells = frame.cells.copy()
        cells[_target_disc(cells.shape, target_radius_cells)] = np.nan
        frames.append(cells)
    return frames


def _bias_level(bias_paths: Sequence[str], reader: _FrameReader) -> float | None:
    """Find the bias level from the run's bias frames.

    Parameters
    ----------
    bias_paths : `Sequence` [`str`]
        The bias frames the stack was calibrated with.
    reader : `_FrameReader`
        Reads each frame and enforces the time limit.

    Returns
    -------
    level_adu : `float` or `None`
        The median of the sigma-clipped means of up to
        ``BIAS_FRAMES_FOR_LEVEL`` frames; `None` when none could be read.
        Raises `_TimeLimitReachedError` if the time limit passes before a
        frame is read.
    """
    levels = [
        level
        for path in sorted(bias_paths)[:BIAS_FRAMES_FOR_LEVEL]
        if (level := reader.read_level(path)) is not None
    ]
    return float(np.median(levels)) if levels else None


def _build_light_maps(
    light_paths: Sequence[str],
    reader: _FrameReader,
    bias_level_adu: float,
    minimum_sky_adu: float,
    target_radius_cells: float,
) -> _LightMaps:
    """Build the median map of the lights and the maps of its two halves.

    Parameters
    ----------
    light_paths : `Sequence` [`str`]
        The raw light frames that went into the stack, in capture order.
    reader : `_FrameReader`
        Reads each frame and enforces the time limit.
    bias_level_adu : `float`
        Subtracted from every frame before its cells are divided by their
        median.
    minimum_sky_adu : `float`
        A frame whose sky above the bias is lower is left out.
    target_radius_cells : `float`
        The radius of the disc set aside around the frame centre.

    Returns
    -------
    maps : `_LightMaps`
        The maps (the star cells and the target disc are left out of every
        median) and the counts of frames used, sampled and left out. The maps
        are `None` when fewer than ``MINIMUM_LIGHT_FRAMES`` frames were usable.
        Raises `_TimeLimitReachedError` if the time limit passes before a
        frame is read.
    """
    frames: list[np.ndarray] = []
    sampled = _evenly_spaced(list(light_paths), MAXIMUM_LIGHT_FRAMES)
    too_faint = 0
    for path in sampled:
        frame = reader.read_cells(path, bias_level_adu)
        if frame is None:
            continue
        if frame.level_adu < minimum_sky_adu or frame.cells is None:
            too_faint += 1
            continue
        if frames and frame.cells.shape != frames[0].shape:
            continue
        cells = frame.cells.copy()
        cells[_target_disc(cells.shape, target_radius_cells)] = np.nan
        cells[_star_mask(cells, HIGH_PASS_SIGMA_CELLS, STAR_MASK_SIGMAS, STAR_WING_SIGMAS)] = np.nan
        frames.append(cells)
    counts = {
        "used": len(frames),
        "sampled": len(sampled),
        "too_faint": too_faint,
        "unusable": len(sampled) - len(frames) - too_faint,
    }
    if len(frames) < MINIMUM_LIGHT_FRAMES:
        return _LightMaps(None, None, None, **counts)
    stack = np.stack(frames)
    full_map = _median_without_warnings(stack)
    # The frames' own limits leave the faint stars that sit at the same place
    # in every frame (the mount tracks, so they do not move). The median of 24
    # frames is far quieter than one frame, so a second, lower limit on the
    # median finds them. It looks at the scale of a star (3 cells), so the
    # broad donuts do not trigger it.
    faint_stars = _star_mask(full_map, MAP_STAR_LOCAL_SIGMA_CELLS, MAP_STAR_CORE_SIGMAS, MAP_STAR_WING_SIGMAS)
    stack[:, faint_stars] = np.nan
    return _LightMaps(
        _median_without_warnings(stack),
        _median_without_warnings(stack[0::2]),
        _median_without_warnings(stack[1::2]),
        **counts,
    )


def _pearson(first: np.ndarray, second: np.ndarray, valid: np.ndarray) -> float | None:
    """Give the correlation coefficient of two maps over the cells that count.

    Parameters
    ----------
    first, second : `numpy.ndarray`
        The maps.
    valid : `numpy.ndarray`
        Which cells to use.

    Returns
    -------
    correlation : `float` or `None`
        The Pearson coefficient, or `None` when fewer than 100 cells count or
        a map has no spread.
    """
    if int(np.count_nonzero(valid)) < 100:
        return None
    a = first[valid].astype(np.float64)
    b = second[valid].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    denominator = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b)))
    if denominator <= 0:
        return None
    return float(np.dot(a, b)) / denominator


def _resample(
    array: np.ndarray, angle_deg: float, shift_cells: tuple[float, float], *, fill: float = 0.0
) -> np.ndarray:
    """Sample a map at the positions the module's convention names.

    The output cell at ``p`` takes the input at ``R(theta) (p + s - c) + c``,
    where ``s`` is the shift, ``c`` the map's centre and ``R`` the rotation.

    Parameters
    ----------
    array : `numpy.ndarray`
        The map to sample.
    angle_deg : `float`
        The rotation ``theta``, in degrees.
    shift_cells : `tuple` [`float`, `float`]
        The shift ``(sx, sy)`` (columns, rows), in cells.
    fill : `float`, optional
        The value outside the map.

    Returns
    -------
    resampled : `numpy.ndarray`
        The map sampled with bilinear interpolation.
    """
    angle = math.radians(angle_deg)
    cos, sin = math.cos(angle), math.sin(angle)
    centre = np.array([(array.shape[0] - 1) / 2.0, (array.shape[1] - 1) / 2.0])
    # Array order is (row, column), the reverse of the convention's (x, y).
    matrix = np.array([[cos, sin], [-sin, cos]])
    shift = np.array([shift_cells[1], shift_cells[0]])
    offset = matrix @ (shift - centre) + centre
    return ndimage.affine_transform(
        array, matrix, offset=offset, order=1, mode="constant", cval=fill, output_shape=array.shape
    ).astype(np.float32)


def _moved_by_whole_cells(array: np.ndarray, rows: int, columns: int) -> np.ndarray:
    """Move a map by whole cells, filling the uncovered cells with zeros.

    Parameters
    ----------
    array : `numpy.ndarray`
        The map (a float map or a boolean validity mask).
    rows, columns : `int`
        The move: the cell at ``(r, c)`` goes to ``(r + rows, c + columns)``.

    Returns
    -------
    moved : `numpy.ndarray`
        The moved map; 0 or `False` where nothing came from.
    """
    moved = np.zeros_like(array)
    height, width = array.shape
    target_rows = slice(max(rows, 0), height + min(rows, 0))
    source_rows = slice(max(-rows, 0), height + min(-rows, 0))
    target_columns = slice(max(columns, 0), width + min(columns, 0))
    source_columns = slice(max(-columns, 0), width + min(-columns, 0))
    moved[target_rows, target_columns] = array[source_rows, source_columns]
    return moved


class _LagSearch:
    """Cross-correlates a fixed map with other maps over a window of shifts.

    The fixed map's spectra are computed once. The score of a shift is the
    uncentered normalized correlation
    ``sum(F L) / sqrt(sum(F^2 M) sum(L^2 N))`` over the cells where both
    maps are valid (``M`` and ``N`` are the validity masks). The band-passed
    maps have a mean near 0, so this is close to the Pearson coefficient and
    needs half the transforms.

    Parameters
    ----------
    fixed : `numpy.ndarray`
        The fixed map (the flat's), 0 where not valid.
    fixed_valid : `numpy.ndarray`
        Where the fixed map is valid.
    max_shift : `int`
        The window of shifts, in cells, along each axis.
    """

    def __init__(self, fixed: np.ndarray, fixed_valid: np.ndarray, max_shift: int) -> None:
        """Compute the fixed map's spectra for padded transforms."""
        self.max_shift = max_shift
        self.shape = tuple(scipy.fft.next_fast_len(size + max_shift) for size in fixed.shape)
        self.fixed_valid = fixed_valid.astype(np.float32)
        self.fixed_spectrum = self._forward(fixed)
        self.fixed_squared_spectrum = self._forward(fixed * fixed)
        self.fixed_valid_spectrum = self._forward(self.fixed_valid)

    def _forward(self, array: np.ndarray) -> np.ndarray:
        """Transform a map, padded with zeros.

        Parameters
        ----------
        array : `numpy.ndarray`
            A map.

        Returns
        -------
        spectrum : `numpy.ndarray`
            Its real-input Fourier transform at the padded size.
        """
        return scipy.fft.rfft2(array, s=self.shape)

    def _correlate(self, fixed_spectrum: np.ndarray, moving_spectrum: np.ndarray) -> np.ndarray:
        """Give the correlation of the two maps for the window of shifts.

        The value at shift ``s`` is ``sum over p of fixed(p) moving(p + s)``.

        Parameters
        ----------
        fixed_spectrum, moving_spectrum : `numpy.ndarray`
            The two maps' spectra.

        Returns
        -------
        window : `numpy.ndarray`
            An array of shape (2S+1, 2S+1), indexed by ``[sy + S, sx + S]``.
        """
        full = scipy.fft.irfft2(np.conj(fixed_spectrum) * moving_spectrum, s=self.shape)
        rows = np.arange(-self.max_shift, self.max_shift + 1) % self.shape[0]
        columns = np.arange(-self.max_shift, self.max_shift + 1) % self.shape[1]
        return full[np.ix_(rows, columns)]

    def score(self, moving: np.ndarray, moving_valid: np.ndarray) -> np.ndarray:
        """Score every shift of a moving map against the fixed map.

        Parameters
        ----------
        moving : `numpy.ndarray`
            The moving map, 0 where not valid.
        moving_valid : `numpy.ndarray`
            Where the moving map is valid.

        Returns
        -------
        scores : `numpy.ndarray`
            The normalized correlation for each shift, shape (2S+1, 2S+1),
            indexed by ``[sy + S, sx + S]``.
        """
        moving_valid_float = moving_valid.astype(np.float32)
        numerator = self._correlate(self.fixed_spectrum, self._forward(moving))
        fixed_power = self._correlate(self.fixed_squared_spectrum, self._forward(moving_valid_float))
        moving_power = self._correlate(self.fixed_valid_spectrum, self._forward(moving * moving))
        denominator = np.sqrt(np.clip(fixed_power, 0, None) * np.clip(moving_power, 0, None))
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(denominator > 1e-9, numerator / denominator, 0.0)


def _parabola_offset(before: float, centre: float, after: float) -> float:
    """Give the position of the top of a parabola through three equal steps.

    Parameters
    ----------
    before, centre, after : `float`
        The values one step before, at, and one step after the highest sample.

    Returns
    -------
    offset : `float`
        The top's position from the centre, in steps, limited to plus or minus
        half a step. 0 when the three values do not form a peak.
    """
    curvature = before - 2.0 * centre + after
    if curvature >= 0:
        return 0.0
    return float(np.clip(0.5 * (before - after) / curvature, -0.5, 0.5))


def _peak_of(scores: np.ndarray, max_shift: int) -> tuple[float, float, float, bool]:
    """Find the highest score of a shift window, to a fraction of a cell.

    Parameters
    ----------
    scores : `numpy.ndarray`
        The score window (see `_LagSearch.score`).
    max_shift : `int`
        The window's half width, in cells.

    Returns
    -------
    peak : `float`
        The highest score.
    shift_x_cells, shift_y_cells : `float`
        Its position with a parabola fit on each axis.
    at_edge : `bool`
        Whether the peak is on the window's outer ring.
    """
    row, column = np.unravel_index(int(np.argmax(scores)), scores.shape)
    peak = float(scores[row, column])
    at_edge = row in (0, scores.shape[0] - 1) or column in (0, scores.shape[1] - 1)
    offset_y = offset_x = 0.0
    if not at_edge:
        offset_y = _parabola_offset(scores[row - 1, column], peak, scores[row + 1, column])
        offset_x = _parabola_offset(scores[row, column - 1], peak, scores[row, column + 1])
    return peak, column - max_shift + offset_x, row - max_shift + offset_y, at_edge


def _displaced_scores(
    flat_band: np.ndarray, flat_valid: np.ndarray, light_band: np.ndarray, light_valid: np.ndarray
) -> tuple[float | None, float | None]:
    """Measure the chance level of the search score from displaced placements.

    Parameters
    ----------
    flat_band, light_band : `numpy.ndarray`
        The band-passed maps.
    flat_valid, light_valid : `numpy.ndarray`
        Where each map can be used.

    Returns
    -------
    mean, std : `float` or `None`
        The mean and the standard deviation of the score (see `_LagSearch`) at
        every shift whose larger axis lies between
        ``DISPLACED_MINIMUM_SHIFT_CELLS`` and ``DISPLACED_MAXIMUM_SHIFT_CELLS``
        (smaller if the map is small, at most a third of its size), with no
        rotation. `None` when the maps are too small for a ring of shifts that
        far out.
    """
    reach = int(min(DISPLACED_MAXIMUM_SHIFT_CELLS, min(flat_band.shape) // 3))
    if reach <= DISPLACED_MINIMUM_SHIFT_CELLS + 4:
        return None, None
    scores = _LagSearch(flat_band, flat_valid, reach).score(
        np.where(light_valid, light_band, 0.0), light_valid
    )
    offsets = np.abs(np.arange(-reach, reach + 1))
    far = np.maximum(offsets[:, None], offsets[None, :]) >= DISPLACED_MINIMUM_SHIFT_CELLS
    return float(scores[far].mean()), float(scores[far].std())


def match_dust_maps(
    flat_band: np.ndarray,
    flat_valid: np.ndarray,
    light_band: np.ndarray,
    light_valid: np.ndarray,
    *,
    max_shift_cells: int = MAXIMUM_SHIFT_CELLS,
    with_null: bool = True,
) -> DustMatch | None:
    """Find the shift and rotation that line the dust up.

    Parameters
    ----------
    flat_band, light_band : `numpy.ndarray`
        The band-passed maps (see `band_pass_dust_map`), of the same shape.
    flat_valid, light_valid : `numpy.ndarray`
        Where each map can be used.
    max_shift_cells : `int`, optional
        The window of shifts, in cells.
    with_null : `bool`, optional
        Whether to run the same search at the displaced placements
        (``NULL_OFFSETS_CELLS``) and the displaced spread. The odd/even
        placements do not need it.

    Returns
    -------
    match : `DustMatch` or `None`
        The correlations and the best placement; `None` when the maps share
        fewer than 100 valid cells.
    """
    both_valid = flat_valid & light_valid
    correlation_at_zero = _pearson(flat_band, light_band, both_valid)
    if correlation_at_zero is None:
        return None
    max_shift = int(min(max_shift_cells, min(flat_band.shape) // 4))
    search = _LagSearch(flat_band, flat_valid, max_shift)

    def peak_of_turned(
        turned: np.ndarray, turned_valid: np.ndarray, offset_cells: tuple[int, int] = (0, 0)
    ) -> tuple[float, float, float, bool]:
        """Score a turned map moved by whole cells against every shift.

        Parameters
        ----------
        turned : `numpy.ndarray`
            The lights' band-passed map after the rotation.
        turned_valid : `numpy.ndarray`
            Where it is valid.
        offset_cells : `tuple` [`int`, `int`], optional
            Extra move (rows, columns) for the null searches.

        Returns
        -------
        peak : `tuple` [`float`, `float`, `float`, `bool`]
            The highest score, its shift in cells (x, y) and whether it sits
            at the edge of the window (see `_peak_of`).
        """
        if offset_cells != (0, 0):
            turned = _moved_by_whole_cells(turned, *offset_cells)
            turned_valid = _moved_by_whole_cells(turned_valid, *offset_cells)
        return _peak_of(search.score(np.where(turned_valid, turned, 0.0), turned_valid), max_shift)

    def turned_at(angle_deg: float) -> tuple[np.ndarray, np.ndarray]:
        """Turn the lights' map and its validity mask by one angle.

        Parameters
        ----------
        angle_deg : `float`
            The rotation of the lights, in degrees.

        Returns
        -------
        turned, turned_valid : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
            The map and where it is valid (fully inside the original).
        """
        return (
            _resample(light_band, angle_deg, (0.0, 0.0)),
            _resample(light_valid.astype(np.float32), angle_deg, (0.0, 0.0)) > 0.999,
        )

    null_offsets = NULL_OFFSETS_CELLS if with_null else ()
    coarse_angles = np.arange(-COARSE_ANGLE_RANGE_DEG, COARSE_ANGLE_RANGE_DEG + 1e-9, COARSE_ANGLE_STEP_DEG)
    coarse: dict[float, tuple[float, float, float, bool]] = {}
    null_best = [-math.inf] * len(null_offsets)
    for angle in coarse_angles:
        turned, turned_valid = turned_at(float(angle))
        coarse[float(angle)] = peak_of_turned(turned, turned_valid)
        for index, offset in enumerate(null_offsets):
            null_best[index] = max(null_best[index], peak_of_turned(turned, turned_valid, offset)[0])
    best_angle = max(coarse, key=lambda angle: coarse[angle][0])
    at_angle_edge = abs(best_angle) >= COARSE_ANGLE_RANGE_DEG - 1e-9

    fine_angles = [
        best_angle + step * FINE_ANGLE_STEP_DEG for step in range(-FINE_ANGLE_COUNT, FINE_ANGLE_COUNT + 1)
    ]
    fine = {angle: peak_of_turned(*turned_at(angle)) for angle in fine_angles}
    top = max(fine_angles, key=lambda angle: fine[angle][0])
    index = fine_angles.index(top)
    angle_offset = 0.0
    if 0 < index < len(fine_angles) - 1:
        angle_offset = FINE_ANGLE_STEP_DEG * _parabola_offset(
            fine[fine_angles[index - 1]][0], fine[top][0], fine[fine_angles[index + 1]][0]
        )
    best_rotation = top + angle_offset
    _peak, shift_x_cells, shift_y_cells, at_shift_edge = fine[top]

    aligned = _resample(light_band, best_rotation, (shift_x_cells, shift_y_cells))
    aligned_valid = _resample(light_valid.astype(np.float32), best_rotation, (shift_x_cells, shift_y_cells))
    best_correlation = _pearson(flat_band, aligned, flat_valid & (aligned_valid > 0.999))
    if best_correlation is None:
        return None
    displaced_std = None
    excess_sigmas = None
    if with_null:
        _displaced_mean, displaced_std = _displaced_scores(flat_band, flat_valid, light_band, light_valid)
        null_spread = float(np.std(null_best, ddof=1))
        if null_spread > 0:
            excess_sigmas = (coarse[best_angle][0] - float(np.mean(null_best))) / null_spread
    return DustMatch(
        correlation_at_zero=correlation_at_zero,
        best_correlation=best_correlation,
        shift_x_px=shift_x_cells * MAP_CELL_PX,
        shift_y_px=shift_y_cells * MAP_CELL_PX,
        rotation_deg=_wrap_angle(best_rotation),
        at_edge=bool(at_shift_edge or at_angle_edge),
        displaced_std=displaced_std,
        excess_sigmas=excess_sigmas,
    )


def _wrap_angle(angle_deg: float) -> float:
    """Bring an angle into (-180, 180] degrees.

    Parameters
    ----------
    angle_deg : `float`
        An angle in degrees.

    Returns
    -------
    wrapped : `float`
        The same angle, at least -180 (exclusive) and at most 180.
    """
    wrapped = (angle_deg + 180.0) % 360.0 - 180.0
    return 180.0 if math.isclose(wrapped, -180.0) else wrapped


def _flat_noise(flat_frames: list[np.ndarray], flat_valid: np.ndarray) -> float | None:
    """Estimate the noise of the band-passed flat map from two halves.

    Parameters
    ----------
    flat_frames : `list` of `numpy.ndarray`
        The flats' cells.
    flat_valid : `numpy.ndarray`
        The valid cells of the band-passed mean map.

    Returns
    -------
    noise : `float` or `None`
        The standard deviation of the mean map's noise: the maps of the odd and
        even flats each carry the noise of half the frames, so their difference
        has twice the whole map's noise. `None` when there are fewer than 2
        flats.
    """
    if len(flat_frames) < 2:
        return None
    odd_band, odd_valid = band_pass_dust_map(np.mean(flat_frames[0::2], axis=0, dtype=np.float32))
    even_band, even_valid = band_pass_dust_map(np.mean(flat_frames[1::2], axis=0, dtype=np.float32))
    both = odd_valid & even_valid & flat_valid
    if int(np.count_nonzero(both)) < 100:
        return None
    return float(np.std((odd_band - even_band)[both])) / 2.0


def _placement_uncertainty(
    odd: DustMatch | None, even: DustMatch | None
) -> tuple[float | None, float | None, float | None]:
    """Give the uncertainty of the placement from the two light halves.

    Half the difference between the odd and even placements sees noise only,
    from one split pair. It is combined in quadrature with the method error
    from injection (``METHOD_SHIFT_ERROR_PX``, ``METHOD_ROTATION_ERROR_DEG``).

    Parameters
    ----------
    odd, even : `DustMatch` or `None`
        The match found with the odd frames' map and with the even frames' map.

    Returns
    -------
    shift_x_px, shift_y_px, rotation_deg : `float` or `None`
        The combined value for each; `None` when either half found no usable
        match, or its peak was at the edge of the search.
    """
    if odd is None or even is None or odd.at_edge or even.at_edge:
        return None, None, None
    return (
        math.hypot(abs(odd.shift_x_px - even.shift_x_px) / 2.0, METHOD_SHIFT_ERROR_PX),
        math.hypot(abs(odd.shift_y_px - even.shift_y_px) / 2.0, METHOD_SHIFT_ERROR_PX),
        math.hypot(abs(_wrap_angle(odd.rotation_deg - even.rotation_deg)) / 2.0, METHOD_ROTATION_ERROR_DEG),
    )


def _without_match_rows(reasons: dict[str, str], reason: str) -> None:
    """Give the rows that depend on the match one reason for having no value.

    Parameters
    ----------
    reasons : `dict` [`str`, `str`]
        The reasons by metric name, extended in place.
    reason : `str`
        Why the match rows have no value.
    """
    for name in (
        STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET,
        STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS,
        STACKING_FLAT_LIGHT_BEST_CORRELATION,
        STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
        STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
        STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
        STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD,
        STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS,
    ):
        reasons[name] = reason


def _light_shortfall_reason(maps: _LightMaps, minimum_sky_adu: float) -> str:
    """Say why too few light frames were usable.

    Parameters
    ----------
    maps : `_LightMaps`
        The counts from the map build.
    minimum_sky_adu : `float`
        The sky requirement.

    Returns
    -------
    reason : `str`
        A sentence with the counts.
    """
    return (
        f"only {maps.used} of {maps.sampled} sampled light frame(s) could be used ("
        f"{maps.too_faint} had a sky under {minimum_sky_adu:.0f} ADU above the bias, "
        f"{SKY_READ_NOISE_FACTOR:g} times the read noise; {maps.unusable} could not be read or differ "
        f"from the flat's size); {MINIMUM_LIGHT_FRAMES} are needed for a map that the star mask and "
        "the median can clean"
    )


def measure_flat_match(
    flat_paths: Sequence[str],
    light_paths: Sequence[str],
    *,
    bias_paths: Sequence[str] = (),
    sky_requirement: SkyRequirement | None = None,
    time_limit_seconds: float | None = None,
    clock: Callable[[], float] = time.monotonic,
    target_radius_cells: float | None = None,
) -> FlatMatchOutcome:
    """Measure whether a stack's flats still show the dust of its lights.

    Parameters
    ----------
    flat_paths : `Sequence` [`str`]
        The flat frames the stack was calibrated with.
    light_paths : `Sequence` [`str`]
        The raw light frames that went into the stack, in capture order.
    bias_paths : `Sequence` [`str`], optional
        The bias frames the stack was calibrated with. Their level is
        subtracted from the lights. With none, nothing is measured.
    sky_requirement : `SkyRequirement`, optional
        The least sky a light frame needs. Left out, nothing is measured.
    time_limit_seconds : `float`, optional
        The time allowed for reading frames, checked before each frame.
    clock : `Callable` [[], `float`], optional
        Gives the time in seconds. Tests pass a fake.
    target_radius_cells : `float`, optional
        The radius of the disc set aside around the frame centre.
        ``TARGET_MASK_RADIUS_CELLS`` if omitted.

    Returns
    -------
    outcome : `FlatMatchOutcome`
        The values, or the reason for each one that is missing.
    """
    if not flat_paths:
        return _all_metrics_missing(_NO_FLATS_REASON)
    if sky_requirement is None or sky_requirement.minimum_adu is None:
        reason = "the sky requirement is unknown"
        if sky_requirement is not None and sky_requirement.reason:
            reason = sky_requirement.reason
        return _all_metrics_missing(reason)
    reader = _FrameReader(time_limit_seconds, clock)
    try:
        return _measure(
            flat_paths,
            light_paths,
            bias_paths,
            sky_requirement.minimum_adu,
            reader,
            TARGET_MASK_RADIUS_CELLS if target_radius_cells is None else target_radius_cells,
        )
    except _TimeLimitReachedError as stop:
        return _all_metrics_missing(str(stop))


def _measure(
    flat_paths: Sequence[str],
    light_paths: Sequence[str],
    bias_paths: Sequence[str],
    minimum_sky_adu: float,
    reader: _FrameReader,
    target_radius_cells: float,
) -> FlatMatchOutcome:
    """Build the maps, search and collect the outcome.

    See `measure_flat_match`.

    Parameters
    ----------
    flat_paths, light_paths, bias_paths : `Sequence` [`str`]
        The frames.
    minimum_sky_adu : `float`
        The sky a light frame needs above the bias level.
    reader : `_FrameReader`
        Reads each frame and enforces the time limit.
    target_radius_cells : `float`
        The radius of the disc set aside around the frame centre.

    Returns
    -------
    outcome : `FlatMatchOutcome`
        The values, or the reason for each one that is missing. Raises
        `_TimeLimitReachedError` if the time limit passes before a frame is
        read.
    """
    flat_frames = _read_flat_cells(flat_paths, reader, target_radius_cells)
    if not flat_frames:
        return _all_metrics_missing("none of the flat frames could be read as a single-plane image")
    flat_count = len(flat_frames)
    flat_cells = np.mean(flat_frames, axis=0, dtype=np.float32)
    flat_band, flat_valid = band_pass_dust_map(flat_cells)
    shadows = find_dust_shadows(flat_band, flat_valid, _flat_noise(flat_frames, flat_valid))
    del flat_frames
    bias_level = _bias_level(bias_paths, reader) if bias_paths else None
    if bias_level is None:
        return _all_metrics_missing(
            _NO_BIAS_REASON if not bias_paths else "none of the bias frames could be read",
            flat_frame_count=flat_count,
            flat_shadow_count=shadows.count,
        )
    maps = _build_light_maps(light_paths, reader, bias_level, minimum_sky_adu, target_radius_cells)
    kept: dict[str, Any] = {
        "flat_frame_count": flat_count,
        "light_frame_count": maps.used,
        "flat_shadow_count": shadows.count,
        "lights_left_out_faint": maps.too_faint,
    }
    if maps.full_map is None or maps.odd_map is None or maps.even_map is None:
        return _all_metrics_missing(_light_shortfall_reason(maps, minimum_sky_adu), **kept)
    if maps.full_map.shape != flat_cells.shape:
        return _all_metrics_missing("the flat frames and the light frames differ in size or binning", **kept)
    light_band, light_valid = band_pass_dust_map(maps.full_map)
    odd_band, odd_valid = band_pass_dust_map(maps.odd_map)
    even_band, even_valid = band_pass_dust_map(maps.even_map)
    split_half = _pearson(odd_band, even_band, odd_valid & even_valid)
    reasons: dict[str, str] = {}
    if split_half is None:
        reasons[STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION] = (
            "the odd and even light maps share too few cells"
        )
    counts = {**kept, "split_half_correlation": split_half}
    match = match_dust_maps(flat_band, flat_valid, light_band, light_valid)
    if match is None:
        _without_match_rows(reasons, "the two maps share too few valid cells or one of them has no spread")
        return FlatMatchOutcome(**counts, reasons=reasons)
    correlation_outside = correlation_outside_shadows(
        flat_band, flat_valid, light_band, light_valid, shadows.mask
    )
    if correlation_outside is None:
        reasons[STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS] = (
            "fewer than 100 cells remain once the flat's dust shadows are masked out "
            f"(the flat shows {shadows.count} shadow(s) deeper than {shadows.threshold:.2%} of the level)"
        )
    shared = {
        **counts,
        "correlation_at_zero": match.correlation_at_zero,
        "correlation_outside_shadows": correlation_outside,
        "best_correlation": match.best_correlation,
        "displaced_correlation_std": match.displaced_std,
        "search_excess_sigmas": match.excess_sigmas,
    }
    why_no_placement = ""
    if match.excess_sigmas is None:
        why_no_placement = "the maps are too small or too flat to measure the chance level of the search"
    elif match.excess_sigmas < PLACEMENT_LIMIT_SIGMAS:
        why_no_placement = (
            f"the best correlation, {match.best_correlation:.3f}, stands only {match.excess_sigmas:.1f} "
            "null spreads above the best correlation of the same search at displaced placements "
            f"(needs {PLACEMENT_LIMIT_SIGMAS:g})"
        )
    elif match.at_edge:
        why_no_placement = (
            "the best placement is at the edge of the search (plus or minus "
            f"{MAXIMUM_SHIFT_CELLS * MAP_CELL_PX} pixels, plus or minus {COARSE_ANGLE_RANGE_DEG:g} "
            "degrees), so the true displacement may be larger"
        )
    if why_no_placement:
        for name in (
            STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX,
            STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX,
            STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
        ):
            reasons[name] = why_no_placement
        return FlatMatchOutcome(**shared, reasons=reasons)
    uncertainty = _placement_uncertainty(
        match_dust_maps(flat_band, flat_valid, odd_band, odd_valid, with_null=False),
        match_dust_maps(flat_band, flat_valid, even_band, even_valid, with_null=False),
    )
    return FlatMatchOutcome(
        **shared,
        shift_x_px=match.shift_x_px,
        shift_y_px=match.shift_y_px,
        rotation_deg=match.rotation_deg,
        shift_x_uncertainty_px=uncertainty[0],
        shift_y_uncertainty_px=uncertainty[1],
        rotation_uncertainty_deg=uncertainty[2],
        reasons=reasons,
    )


def flat_match_measurements(
    outcome: FlatMatchOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the stack's flat-to-light match rows.

    Parameters
    ----------
    outcome : `FlatMatchOutcome`
        What was measured.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row for each of the ten metrics; a missing value is not
        measured, with its reason. The shadow count is based on the flat
        frames; the others on the light frames read.
    """
    lights = outcome.light_frame_count
    rows = (
        (STACKING_FLAT_DUST_SHADOW_COUNT, outcome.flat_shadow_count, outcome.flat_frame_count, None),
        (STACKING_FLAT_LIGHT_DUST_CORRELATION_AT_ZERO_OFFSET, outcome.correlation_at_zero, lights, None),
        (STACKING_FLAT_LIGHT_CORRELATION_OUTSIDE_SHADOWS, outcome.correlation_outside_shadows, lights, None),
        (STACKING_FLAT_LIGHT_BEST_CORRELATION, outcome.best_correlation, lights, None),
        (STACKING_FLAT_LIGHT_BEST_SHIFT_X_PX, outcome.shift_x_px, lights, outcome.shift_x_uncertainty_px),
        (STACKING_FLAT_LIGHT_BEST_SHIFT_Y_PX, outcome.shift_y_px, lights, outcome.shift_y_uncertainty_px),
        (
            STACKING_FLAT_LIGHT_BEST_ROTATION_DEG,
            outcome.rotation_deg,
            lights,
            outcome.rotation_uncertainty_deg,
        ),
        (STACKING_FLAT_LIGHT_DISPLACED_CORRELATION_STD, outcome.displaced_correlation_std, lights, None),
        (STACKING_FLAT_LIGHT_SEARCH_EXCESS_SIGMAS, outcome.search_excess_sigmas, lights, None),
        (STACKING_LIGHT_DUST_SPLIT_HALF_CORRELATION, outcome.split_half_correlation, lights, None),
    )
    return [
        build_measurement(
            _SPEC_BY_NAME[name],
            None if value is None else float(value),
            context,
            subject,
            reason=outcome.reasons.get(name, "the value was not worked out"),
            uncertainty=uncertainty,
            sample_count=count if value is not None and count else None,
        )
        for name, value, count, uncertainty in rows
    ]


def sky_requirement_for_frame(frame: Any) -> SkyRequirement:
    """Work out the sky a light frame needs from the camera profile.

    The sky above the bias must be at least ``SKY_READ_NOISE_FACTOR`` times
    the read noise, in electrons, so the requirement in ADU is that many times
    the read noise over the gain.

    Parameters
    ----------
    frame : `Any`
        A light frame record (``camera``, ``iso``, ``offset`` and ``binning``
        are read).

    Returns
    -------
    requirement : `SkyRequirement`
        The requirement, or the reason the camera profile cannot give it (no
        entry for this gain, offset and binning, or no gain or read noise in
        it).
    """
    camera = str(getattr(frame, "camera", "") or "")
    profile = resolve_camera_profile(camera)
    entry = profile.sensor_noise_for(
        getattr(frame, "iso", None), getattr(frame, "offset", None), getattr(frame, "binning", None)
    )
    if entry is None or entry.read_noise_e is None or entry.gain_e_per_adu is None:
        return SkyRequirement(
            None,
            f"the camera profile of {camera or 'the camera'} holds no gain and read noise for this "
            "gain, offset and binning, so the sky requirement of the light frames cannot be set",
        )
    return SkyRequirement(SKY_READ_NOISE_FACTOR * entry.read_noise_e.value / entry.gain_e_per_adu.value)


def _time_limit_from(configuration: Any) -> float:
    """Read the time limit from a configuration, or use the default.

    Parameters
    ----------
    configuration : `Any`
        The application configuration, or `None` for the process-wide one.

    Returns
    -------
    seconds : `float`
        The configured limit; ``DEFAULT_TIME_LIMIT_SECONDS`` when the
        configuration has no such setting.
    """
    if configuration is None:
        from astrometricslib.foundation.config import get_configuration

        configuration = get_configuration()
    getter = getattr(configuration, "get_flat_match_time_limit_seconds", None)
    return float(getter()) if callable(getter) else DEFAULT_TIME_LIMIT_SECONDS


def flat_match_rows(
    stack_file_name: str,
    context: PipelineRunContext,
    flat_paths: Sequence[str] | None,
    target_frames: Sequence[Any],
    *,
    is_spectral: bool,
    bias_paths: Sequence[str] | None = None,
    member_paths: Sequence[str] | None = None,
    corrupt_paths: Sequence[str] = (),
    configuration: Any = None,
) -> list[QualityMeasurement]:
    """Measure the match for one stack and build its rows, never raising.

    Parameters
    ----------
    stack_file_name : `str`
        The stack's file name (not a mount path).
    context : `PipelineRunContext`
        The run's shared facts.
    flat_paths : `Sequence` [`str`] or `None`
        The flat frames the stack was calibrated with (the staging step's
        ``flat_frame_paths``); `None` when it recorded none.
    target_frames : `Sequence`
        The frames given to the stacking program (their ``path`` and
        ``timestamp`` are read).
    is_spectral : `bool`
        Whether the stack is spectral.
    bias_paths : `Sequence` [`str`] or `None`, optional
        The bias frames the stack was calibrated with (``bias_frame_paths``).
    member_paths : `Sequence` [`str`] or `None`, optional
        The paths of the frames that went into the stack. Left out (the member
        list could not be worked out), every submitted frame that was not
        skipped as corrupt is used.
    corrupt_paths : `Sequence` [`str`], optional
        The frames the staging step skipped as unreadable.
    configuration : `AppConfiguration`, optional
        Gives the time limit. The process-wide one if omitted.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        The ten rows. A failure while measuring becomes a not-measured row
        for each, with the reason.
    """
    subject = context.stack_subject(stack_file_name)
    started = time.monotonic()
    try:
        if is_spectral:
            outcome = _all_metrics_missing(_SPECTRAL_REASON)
        else:
            chosen = set(member_paths) if member_paths is not None else None
            skipped = set(corrupt_paths)
            used_frames = [
                frame
                for frame in target_frames
                if getattr(frame, "path", None)
                and (frame.path in chosen if chosen is not None else frame.path not in skipped)
            ]
            ordered = sorted(
                used_frames, key=lambda frame: (getattr(frame, "timestamp", None) or 0.0, str(frame.path))
            )
            light_paths = [str(frame.path) for frame in ordered]
            requirement = (
                sky_requirement_for_frame(ordered[0])
                if ordered
                else SkyRequirement(None, "the stack has no light frames to read")
            )
            logger.info(
                "Flat-to-light match of %s: reading up to %d flat and %d light frame(s) of %d, "
                "and up to %d bias frame(s).",
                os.path.basename(stack_file_name),
                min(len(flat_paths or ()), MAXIMUM_FLAT_FRAMES),
                min(len(light_paths), MAXIMUM_LIGHT_FRAMES),
                len(light_paths),
                min(len(bias_paths or ()), BIAS_FRAMES_FOR_LEVEL),
            )
            outcome = measure_flat_match(
                list(flat_paths or ()),
                light_paths,
                bias_paths=list(bias_paths or ()),
                sky_requirement=requirement,
                time_limit_seconds=_time_limit_from(configuration),
            )
    except Exception as error:  # a by-product: no failure here may fail the stack
        logger.exception("The flat-to-light match of the stack %s could not be measured", stack_file_name)
        outcome = _all_metrics_missing(f"the flat-to-light match failed: {type(error).__name__}")
    logger.info(
        "Measured the flat-to-light match of %s in %.1f s.",
        os.path.basename(stack_file_name),
        time.monotonic() - started,
    )
    return flat_match_measurements(outcome, context, subject)
