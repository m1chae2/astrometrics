"""Builds the measurements of the target's own clipped pixels, frame by frame.

An exposure that is too long for the target clips the target itself: its
brightest pixels stop at the camera's ceiling (the clip ceiling) or leave the
range where the sensor answers in proportion to the light (the photometric
linearity limit). A clipped target is flattened in the stack and its brightness
is wrong. The saturation check of `exposure_saturation.py` looks at the
brightest star of a frame, which is usually not the target. This file looks at
the target.

The check lives in the astrometry pipeline because it needs a plate solution of
the stack (the match of the stack's stars to a star catalog), and a fresh stack
has none. The astrometry record step calls it after a successful solve of the
stack and gives it that solve's coordinate solution.

Per frame, the steps are:

1. Find the target on the stack. The target record gives its catalog position
   (right ascension and declination). This run's plate solution turns it into a
   pixel on the stack. A run that did not solve the stack gets not-measured
   rows. The mount's commanded pointing, which the registration reference
   frame's header holds, is not good enough: on the M 57 night of 2026-10-03 it
   put the nebula 157 arcsec from its plate-solved place. The pixel center is
   never used in place of a position. A position outside the stack's pixels is
   not measured.
2. Confirm there is a source there. The brightest pixel of the target's disc on
   the stack must stand at least `MINIMUM_PEAK_SIGMA` times the sky noise above
   the local sky (the median of a ring around the disc). Without it the target
   record's position is probably wrong, and counting clipped pixels over empty
   sky would report "no clipping" for a target that was never looked at. In a
   crowded field any star passes, so this finds a position on blank sky and
   not a wrong star (validation register entry AST-19).
3. Move that pixel onto the frame. The stack sits on the registration
   reference frame's grid (less the rows and columns trimmed from its edges).
   Siril saves, for every frame, the transform that moves the frame onto the
   reference, so the inverse of it moves the target back onto the frame.
   Siril's y axis counts up from the last row of the image array
   (y = height - 1 - row). This was checked on the M 57 night of 2026-10-03
   (ten of its 61 frames, the reference's 60 brightest stars): with the inverse
   transform and this y axis, all 60 landed within 2.5 px of a detected star in
   every frame. The three other choices (the transform itself, or the y axis
   equal to the row) landed 0 or 1 of 60 in the four frames tested whose
   shifts exceeded 2 px in both axes.
4. Count, in a disc of 30 arcseconds around that pixel, the pixels at or above
   the clip ceiling and at or above the linearity limit, and the pixels of the
   disc that lie in the frame. The two shares are stored (the pixel count of
   the disc is the row's sample count, so a count is the share times it).

The disc is the larger of 30 arcseconds and the target's semi-major axis. No
target size is stored today, so it is 30 arcseconds, and a target larger than
that is examined in its core only. Every pixel of the disc counts as a target
pixel, so in a crowded field the clipped pixels of foreground stars that fall
in the disc are counted too: the value is the clipping of the disc, which is
the target's when the target dominates the disc.

The rows are about the frame, in the same subject as the frame's weight. At
most `MAXIMUM_FRAMES_READ` frames, spread evenly through the run, are read, and
reading stops after `MAXIMUM_READ_SECONDS`. A frame not read has a not-measured
row that says why. A failure never fails the astrometry run.

The registration record that the stacking run writes beside the stack
(`pipelines/shared/registration_record.py`) holds the frame list and the
transforms. A stack made before that record existed gets no rows.
"""

import logging
import math
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from astropy.wcs import WCS
from astropy.wcs.utils import proj_plane_pixel_scales

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import FITS_READ_ERRORS, collapse_to_2d, read_data
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION,
    ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION,
    ASTROMETRY_METRICS,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    ClipAndLinearityLevels,
    clip_and_linearity_levels,
)
from astrometricslib.pipelines.shared.registration_record import (
    RegistrationRecord,
    read_registration_record,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

__all__ = [
    "MAXIMUM_FRAMES_READ",
    "MAXIMUM_READ_SECONDS",
    "MINIMUM_PEAK_SIGMA",
    "TARGET_REGION_RADIUS_ARCSEC",
    "DiscCounts",
    "count_pixels_in_disc",
    "disc_radius_arcsec",
    "peak_sigma_above_sky",
    "reference_pixel_to_frame",
    "target_clipping_measurements",
]

# The smallest radius of the disc around the target, in arcseconds. 30
# arcseconds is about 16 px at the 1.915 arcsec/px of the ASI533 on the 75Q.
# That is about 7 times the 4 arcsec FWHM of the seeing on a good night, so it
# holds a point source with its wings, and it leaves room for a position error
# of several pixels in the target's place on the frame. A target larger than
# this is examined only in its core unless its size is stored (see
# `disc_radius_arcsec`); the Ring Nebula is 86 by 62 arcseconds.
TARGET_REGION_RADIUS_ARCSEC = 30.0

# The most frames read in one run. One real run (AST-19, 61 frames of
# 3008 x 3008 px on a local disk) took 0.014 s a frame, so 200 frames take
# about 3 s on a local disk. The cap keeps a night of several hundred frames
# from reading all of them; the frames are spread evenly, so every part of the
# night is sampled.
MAXIMUM_FRAMES_READ = 200

# The wall-clock time the frame reads may take. The raw frames usually sit on
# a network mount or a synced drive, which can be 10 to 100 times slower than
# the local disk of the AST-19 run. 120 seconds allows 0.6 s for each of 200
# frames, which is 40 times the local-disk time. Frames not reached get a
# not-measured row that names the limit.
MAXIMUM_READ_SECONDS = 120.0

# A frame is not measured when less than this share of the disc lies inside the
# image. A disc cut by the edge would count clipped pixels over a much smaller
# area than it names.
MINIMUM_DISC_SHARE_INSIDE = 0.5

# The disc's brightest pixel on the stack must stand at least this many sky
# noise sigma above the local sky for a source to count as seen. Under pure
# Gaussian noise a pixel passes 5 sigma with probability 2.9e-7, so among the
# 800 pixels of a 30 arcsecond disc at 1.9 arcsec/px blank sky passes with
# probability about 2e-4. The ring's own scatter is the sigma, which includes
# the slight correlation the registration's resampling puts into a stack's
# noise. AST-19 measured it on the M 57 stack: of 2,000 random discs, the
# blank ones sat between 1.5 and 4.5 sigma (the 1st percentile of all discs
# is 3.7), so 5 sigma lies above blank sky. The same measurement shows the
# limit of the check: 97.8 percent of random discs passed, because the field
# is full of stars. The check finds a target record placed on blank sky. It
# cannot tell the target from a field star in a crowded field.
MINIMUM_PEAK_SIGMA = 5.0

# The ring that gives the local sky and its noise reaches from this multiple of
# the disc radius to the next. The inner edge clears the target's wings and the
# outer edge stays near enough for the sky to be the same.
SKY_RING_INNER_FACTOR = 1.5
SKY_RING_OUTER_FACTOR = 2.5

# The fewest ring pixels for a sky estimate. A median and a scatter of fewer
# than this are too uncertain to set a 5 sigma bar against.
SKY_RING_MINIMUM_PIXELS = 100

# Converts the median absolute deviation of Gaussian noise to its sigma.
MAD_TO_SIGMA = 1.4826

_SPEC_BY_NAME = {spec.name: spec for spec in ASTROMETRY_METRICS}
_METRIC_ORDER = (ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION, ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION)

SPECTRAL_REASON = (
    "a spectral stack holds dispersed spectra, not images of the target, so the target's pixels cannot "
    "be counted on its frames"
)
NO_TARGET_POSITION_REASON = (
    "the target has no catalog position (its right ascension and declination are the placeholder)"
)
NO_SOLUTION_REASON = (
    "this run did not plate-solve the stack (it used the header's own coordinates or none), so the "
    "target cannot be placed on the stack (the commanded pointing and the pixel center are never "
    "used instead)"
)
DROPPED_REASON = "Siril could not register this frame, so the target cannot be placed on it"
NOT_SAMPLED_REASON = (
    "not sampled: the run reads at most {limit} frames, spread evenly through the run, and this frame "
    "was not one of them"
)
TIME_LIMIT_REASON = (
    "not read: the wall-clock limit of {seconds:g} seconds for reading frames was reached before this frame"
)
NO_FRAME_RECORD_REASON = "the target has no record of this frame, so its time and camera are not known"
NO_LINEARITY_LIMIT_REASON = "the camera profile has no photometric linearity limit"


@dataclass(frozen=True)
class _UnrecordedFrame:
    """A frame of the registration record that the target no longer holds.

    Attributes
    ----------
    path : `str`
        The frame's path.
    timestamp : `None`
        The frame's time is not known.
    """

    path: str
    timestamp: None = None


@dataclass(frozen=True)
class DiscCounts:
    """The pixel counts in the disc around the target in one frame.

    Parameters
    ----------
    inside : `int`
        Pixels of the disc that lie inside the image.
    clipped : `int`
        Of those, the pixels at or above the clip level.
    nonlinear : `int` or `None`
        Of those, the pixels at or above the linearity limit, or `None` when
        there is no limit.
    disc_area_px : `float`
        The full area of the disc, ``pi * radius ** 2``, for the share inside.
    """

    inside: int
    clipped: int
    nonlinear: int | None
    disc_area_px: float


def disc_radius_arcsec(target_semi_major_arcsec: float | None) -> float:
    """Choose the radius of the disc around the target.

    Parameters
    ----------
    target_semi_major_arcsec : `float` or `None`
        The target's semi-major axis in arcseconds when a size is stored. None
        is stored today, so callers pass `None`.

    Returns
    -------
    radius_arcsec : `float`
        The larger of `TARGET_REGION_RADIUS_ARCSEC` and the semi-major axis.
        A size that is missing, not finite or not positive is ignored.
    """
    if target_semi_major_arcsec is None or not math.isfinite(target_semi_major_arcsec):
        return TARGET_REGION_RADIUS_ARCSEC
    return max(TARGET_REGION_RADIUS_ARCSEC, float(target_semi_major_arcsec))


def reference_pixel_to_frame(
    reference_row: float, reference_column: float, homography: Sequence[float], height: int
) -> tuple[float, float]:
    """Move a pixel of the registration reference onto one frame.

    Parameters
    ----------
    reference_row, reference_column : `float`
        The pixel on the reference frame's grid, counted from 0.
    homography : `Sequence` [`float`]
        The nine numbers Siril saves for the frame (row order of the 3 x 3
        matrix), the transform that moves the frame onto the reference.
    height : `int`
        The number of rows of the image. Siril's y axis is ``height - 1 -
        row``.

    Returns
    -------
    row, column : `float`
        The pixel on the frame's own grid, counted from 0. Both are NaN when
        the matrix cannot be inverted.
    """
    matrix = np.asarray(homography, dtype=float).reshape(3, 3)
    try:
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError:
        return math.nan, math.nan
    moved = inverse @ np.array([reference_column, height - 1 - reference_row, 1.0])
    if moved[2] == 0.0:  # ruff: ignore[float-equality-comparison] -- exact zero scale of the matrix, not a measurement
        return math.nan, math.nan
    return float(height - 1 - moved[1] / moved[2]), float(moved[0] / moved[2])


def _disc_window(
    shape: tuple[int, int], center_row: float, center_column: float, outer_radius_px: float
) -> tuple[np.ndarray, np.ndarray, tuple[slice, slice]]:
    """Give the pixel coordinates of the part of an image near a position.

    Parameters
    ----------
    shape : `tuple` [`int`, `int`]
        The image's (rows, columns).
    center_row, center_column : `float`
        The position, counted from 0.
    outer_radius_px : `float`
        The distance from the position the window must reach.

    Returns
    -------
    rows, columns : `numpy.ndarray`
        The row and column of every pixel of the window.
    window : `tuple` [`slice`, `slice`]
        The window's place in the image.
    """
    height, width = shape
    row_start = max(0, math.floor(center_row - outer_radius_px))
    row_stop = min(height, math.ceil(center_row + outer_radius_px) + 1)
    column_start = max(0, math.floor(center_column - outer_radius_px))
    column_stop = min(width, math.ceil(center_column + outer_radius_px) + 1)
    window = (slice(row_start, max(row_start, row_stop)), slice(column_start, max(column_start, column_stop)))
    rows, columns = np.mgrid[window[0], window[1]]
    return rows, columns, window


def count_pixels_in_disc(
    pixels: np.ndarray,
    center_row: float,
    center_column: float,
    radius_px: float,
    clip_level: float,
    linearity_level: float | None,
) -> DiscCounts:
    """Count the pixels of a disc that reach two levels.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The 2-D raw frame in ADU. A color frame is reduced to the maximum of
        its planes before this call.
    center_row, center_column : `float`
        The disc center on the frame, counted from 0.
    radius_px : `float`
        The disc radius in pixels.
    clip_level : `float`
        A pixel at or above this value is clipped.
    linearity_level : `float` or `None`
        A pixel at or above this value is outside the linear range. `None`
        when the camera has no such limit.

    Returns
    -------
    counts : `DiscCounts`
        The pixels inside the image, the clipped ones and the nonlinear ones.
    """
    area = math.pi * radius_px**2
    rows, columns, window = _disc_window(pixels.shape, center_row, center_column, radius_px)
    if rows.size == 0:
        return DiscCounts(0, 0, None if linearity_level is None else 0, area)
    inside_disc = (rows - center_row) ** 2 + (columns - center_column) ** 2 <= radius_px**2
    values = pixels[window]
    clipped = int(np.count_nonzero(inside_disc & (values >= clip_level)))
    nonlinear = None
    if linearity_level is not None:
        nonlinear = int(np.count_nonzero(inside_disc & (values >= linearity_level)))
    return DiscCounts(int(np.count_nonzero(inside_disc)), clipped, nonlinear, area)


def peak_sigma_above_sky(
    pixels: np.ndarray, center_row: float, center_column: float, radius_px: float
) -> float | None:
    """Say how far the brightest pixel of the disc stands above the sky.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The 2-D stack.
    center_row, center_column : `float`
        The target's place on the stack, counted from 0.
    radius_px : `float`
        The disc radius in pixels.

    Returns
    -------
    sigma : `float` or `None`
        ``(peak - sky) / noise``, where the peak is the brightest finite pixel
        of the disc, the sky is the median of the ring from
        `SKY_RING_INNER_FACTOR` to `SKY_RING_OUTER_FACTOR` times the radius and
        the noise is 1.4826 times the median absolute deviation of that ring.
        `None` when the disc has no finite pixel, the ring has fewer than
        `SKY_RING_MINIMUM_PIXELS` finite pixels or its noise is zero.
    """
    rows, columns, window = _disc_window(
        pixels.shape, center_row, center_column, SKY_RING_OUTER_FACTOR * radius_px
    )
    if rows.size == 0:
        return None
    distance_squared = (rows - center_row) ** 2 + (columns - center_column) ** 2
    values = np.asarray(pixels[window], dtype=float)
    finite = np.isfinite(values)
    in_disc = finite & (distance_squared <= radius_px**2)
    in_ring = (
        finite
        & (distance_squared >= (SKY_RING_INNER_FACTOR * radius_px) ** 2)
        & (distance_squared <= (SKY_RING_OUTER_FACTOR * radius_px) ** 2)
    )
    if not in_disc.any() or np.count_nonzero(in_ring) < SKY_RING_MINIMUM_PIXELS:
        return None
    ring = values[in_ring]
    sky = float(np.median(ring))
    noise = MAD_TO_SIGMA * float(np.median(np.abs(ring - sky)))
    if not noise > 0.0:
        return None
    return (float(values[in_disc].max()) - sky) / noise


def _missing(frame: Any, context: PipelineRunContext, reason: str) -> list[QualityMeasurement]:
    """Build the two not-measured rows of one frame.

    Parameters
    ----------
    frame : `Any`
        The frame record (anything with ``path`` and ``timestamp``).
    context : `PipelineRunContext`
        The run's shared facts.
    reason : `str`
        Why there is no value.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        One for each of the two target-clipping metrics.
    """
    subject = frame_subject(frame.path, context.target_id, frame.timestamp)
    return [
        build_measurement(_SPEC_BY_NAME[name], None, context, subject, reason=reason)
        for name in _METRIC_ORDER
    ]


def _flat_pixels(data: np.ndarray) -> np.ndarray:
    """Reduce a frame to one 2-D image of the highest plane value.

    Parameters
    ----------
    data : `numpy.ndarray`
        A 2-D frame or a 3-D frame with its color planes first or last.

    Returns
    -------
    pixels : `numpy.ndarray`
        The 2-D image. A color frame clips when any plane clips, so the
        maximum is taken.
    """
    if data.ndim != 3:
        return data
    if data.shape[0] in (1, 3, 4):
        return np.max(data, axis=0)
    return np.max(data, axis=-1)


def _chosen_positions(positions: Sequence[int]) -> set[int]:
    """Choose which candidate frames to read, spread evenly through the run.

    Parameters
    ----------
    positions : `Sequence` [`int`]
        The positions (in Siril's order) of the frames that could be read.

    Returns
    -------
    chosen : `set` of `int`
        Every position when there are at most `MAXIMUM_FRAMES_READ`,
        otherwise that many, evenly spaced, the first and last included.
    """
    if len(positions) <= MAXIMUM_FRAMES_READ:
        return set(positions)
    indices = {round(index) for index in np.linspace(0, len(positions) - 1, MAXIMUM_FRAMES_READ)}
    return {positions[index] for index in indices}


def target_clipping_measurements(
    stack_path: str,
    target_frames: Sequence[Any],
    context: PipelineRunContext,
    *,
    solved_wcs: WCS | None,
    target_center_deg: tuple[float | None, float | None],
    target_semi_major_arcsec: float | None = None,
    configuration: object = None,
    report_progress: Callable[[str], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    maximum_read_seconds: float = MAXIMUM_READ_SECONDS,
) -> list[QualityMeasurement]:
    """Count the share of the target's pixels that are clipped, frame by frame.

    Parameters
    ----------
    stack_path : `str`
        The stack this run solved. Its registration record and its pixels are
        read.
    target_frames : `Sequence`
        The target's frame records (anything with ``path``, ``timestamp`` and
        ``camera``).
    context : `PipelineRunContext`
        The run's shared facts.
    solved_wcs : `astropy.wcs.WCS` or `None`
        The coordinate solution this run found for the stack, on the stack's
        pixel grid. `None` when the run did not solve it.
    target_center_deg : `tuple` of `float` or `None`
        The target's right ascension and declination in degrees as the target
        record held them before this run (see `resolve_target_center_hint`);
        `None` for the never-solved placeholder.
    target_semi_major_arcsec : `float` or `None`, optional
        The target's semi-major axis when a size is stored (none is today).
    configuration : `AppConfiguration`, optional
        Where the camera profiles live.
    report_progress : `Callable`, optional
        Called with a sentence every 25 frames read.
    clock : `Callable`, optional
        The wall clock, in seconds. Tests replace it.
    maximum_read_seconds : `float`, optional
        The wall-clock limit for reading frames.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        Two rows (the clipped share and the nonlinear share) for each frame in
        the stack's registration record. Rows are ``assumed`` when a
        camera-profile level is not a measured one, with the reason, and
        ``measured`` rows carry the target's place and the source check in
        their reason. Rows are not measured, with the reason, when the target
        cannot be placed, is not seen on the stack, or the frame was not read.
        The list is empty when the stack has no registration record.
    """
    record = read_registration_record(stack_path)
    if record is None:
        logger.info(
            "The stack %s has no registration record, so the target's clipped pixels were not counted; "
            "stacking the target again writes one.",
            stack_path,
        )
        return []
    frame_by_path = {frame.path: frame for frame in target_frames}
    frames = [
        _UnrecordedFrame(path) if frame_by_path.get(path) is None else frame_by_path[path]
        for path in record.light_paths
    ]

    def all_missing(reason: str) -> list[QualityMeasurement]:
        """Give every frame of the record the same not-measured rows.

        Returns
        -------
        rows : `list` of `QualityMeasurement`
            Two rows for each frame.
        """
        return [row for frame in frames for row in _missing(frame, context, reason)]

    if record.is_spectral:
        return all_missing(SPECTRAL_REASON)
    center_ra, center_dec = target_center_deg
    if center_ra is None or center_dec is None:
        return all_missing(NO_TARGET_POSITION_REASON)
    if solved_wcs is None:
        return all_missing(NO_SOLUTION_REASON)
    try:
        stack_pixels = collapse_to_2d(np.asarray(read_data(stack_path), dtype=float))
    except (*FITS_READ_ERRORS, *DATA_ERRORS, TypeError) as error:
        return all_missing(f"the stack could not be read to check for the target: {error}")
    column, row_on_stack = (float(value) for value in solved_wcs.world_to_pixel_values(center_ra, center_dec))
    place = f"RA {center_ra:.5f} deg, Dec {center_dec:.5f} deg"
    if not (math.isfinite(column) and math.isfinite(row_on_stack)):
        return all_missing(f"the target's position ({place}) cannot be placed on the stack")
    height, width = stack_pixels.shape
    pixel_place = f"stack pixel x {column:.1f}, y {row_on_stack:.1f}"
    if not (0.0 <= column <= width - 1 and 0.0 <= row_on_stack <= height - 1):
        return all_missing(
            f"the target's position ({place}) falls outside the stack ({pixel_place}; the stack is "
            f"{width} x {height} px)"
        )
    scale_arcsec = float(np.mean(proj_plane_pixel_scales(solved_wcs)) * 3600.0)
    radius_arcsec = disc_radius_arcsec(target_semi_major_arcsec)
    radius_px = radius_arcsec / scale_arcsec
    peak_sigma = peak_sigma_above_sky(stack_pixels, row_on_stack, column, radius_px)
    if peak_sigma is None:
        return all_missing(
            f"the sky around the target's position could not be measured on the stack, so a source there "
            f"cannot be confirmed (used {place}; {pixel_place})"
        )
    if peak_sigma < MINIMUM_PEAK_SIGMA:
        return all_missing(
            f"no source at the target position (used {place}; {pixel_place}): the disc's brightest pixel "
            f"stands {peak_sigma:.1f} sigma above the local sky, under the {MINIMUM_PEAK_SIGMA:g} sigma "
            "needed"
        )
    position_note = (
        f"target placed from its catalog position ({place}) through this run's plate solution of the "
        f"stack ({pixel_place}), then onto the frame by Siril's registration transform; a disc of "
        f"{radius_arcsec:g} arcsec ({radius_px:.1f} px); a source stands {peak_sigma:.1f} sigma above the "
        "local sky on the stack; every pixel of the disc counts, field stars included"
    )
    return _frame_measurements(
        record,
        frames,
        context,
        stack_reference=(row_on_stack + record.crop_offset[1], column + record.crop_offset[0]),
        radius_px=radius_px,
        position_note=position_note,
        configuration=configuration,
        report_progress=report_progress,
        clock=clock,
        maximum_read_seconds=maximum_read_seconds,
    )


def _frame_measurements(
    record: RegistrationRecord,
    frames: Sequence[Any],
    context: PipelineRunContext,
    *,
    stack_reference: tuple[float, float],
    radius_px: float,
    position_note: str,
    configuration: object,
    report_progress: Callable[[str], None] | None,
    clock: Callable[[], float],
    maximum_read_seconds: float,
) -> list[QualityMeasurement]:
    """Read the chosen frames and build the rows of every frame of the record.

    Parameters
    ----------
    record : `RegistrationRecord`
        Siril's registration of the stack.
    frames : `Sequence`
        One frame record (or stand-in) for each position of the record.
    context : `PipelineRunContext`
        The run's shared facts.
    stack_reference : `tuple` [`float`, `float`]
        The target's (row, column) on the registration reference's grid.
    radius_px : `float`
        The disc radius in pixels.
    position_note : `str`
        How the target was placed, for the reason of each row.
    configuration : `object`
        Where the camera profiles live.
    report_progress : `Callable` or `None`
        Called with a sentence every 25 frames read.
    clock : `Callable`
        The wall clock, in seconds.
    maximum_read_seconds : `float`
        The wall-clock limit for reading frames.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        Two rows for each frame, in the record's order.
    """
    candidates = [
        position
        for position, (frame, included) in enumerate(zip(frames, record.included, strict=True))
        if included and not isinstance(frame, _UnrecordedFrame)
    ]
    chosen = _chosen_positions(candidates)
    profiles: dict[str, CameraProfile] = {}
    started = clock()
    read_count = 0
    rows: list[QualityMeasurement] = []
    for position, frame in enumerate(frames):
        if not record.included[position]:
            rows.extend(_missing(frame, context, DROPPED_REASON))
        elif isinstance(frame, _UnrecordedFrame):
            rows.extend(_missing(frame, context, NO_FRAME_RECORD_REASON))
        elif position not in chosen:
            rows.extend(_missing(frame, context, NOT_SAMPLED_REASON.format(limit=MAXIMUM_FRAMES_READ)))
        elif clock() - started > maximum_read_seconds:
            rows.extend(_missing(frame, context, TIME_LIMIT_REASON.format(seconds=maximum_read_seconds)))
        else:
            read_count += 1
            if report_progress is not None and read_count % 25 == 1:
                report_progress(
                    f"Counting the target's clipped pixels: reading frame {read_count} of {len(chosen)}"
                )
            rows.extend(
                _rows_of_one_frame(
                    frame,
                    record.homographies[position],
                    stack_reference,
                    radius_px,
                    context,
                    profiles,
                    configuration,
                    position_note,
                )
            )
    return rows


def _levels_of(
    frame: Any, profiles: dict[str, CameraProfile], configuration: object
) -> ClipAndLinearityLevels:
    """Find the clip level and the linearity limit of a frame's camera.

    Parameters
    ----------
    frame : `Any`
        The frame record (it has ``camera``).
    profiles : `dict`
        Camera profiles already read, by camera name (filled here).
    configuration : `object`
        Where the camera profiles live.

    Returns
    -------
    levels : `ClipAndLinearityLevels`
        The levels of the camera's profile. `resolve_camera_profile` raises an
        `AstrometricsError` when no profile can be read; the caller catches it.
    """
    camera = frame.camera
    if camera not in profiles:
        profiles[camera] = resolve_camera_profile(camera, configuration)
    return clip_and_linearity_levels(profiles[camera])


def _rows_of_one_frame(
    frame: Any,
    homography: Sequence[float],
    reference_position: tuple[float, float],
    radius_px: float,
    context: PipelineRunContext,
    profiles: dict[str, CameraProfile],
    configuration: object,
    position_note: str,
) -> list[QualityMeasurement]:
    """Count the target's clipped pixels in one frame and build its two rows.

    A failure to read this frame, or to find its camera profile, gives
    not-measured rows for this frame only.

    Parameters
    ----------
    frame : `Any`
        The frame record.
    homography : `Sequence` [`float`]
        The nine numbers Siril saved for the frame.
    reference_position : `tuple` [`float`, `float`]
        The target's (row, column) on the registration reference's grid.
    radius_px : `float`
        The disc radius in pixels.
    context : `PipelineRunContext`
        The run's shared facts.
    profiles : `dict`
        Camera profiles already read, by camera name (filled here).
    configuration : `object`
        Where the camera profiles live.
    position_note : `str`
        How the target was placed, for the reason of each row.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        The two rows.
    """
    try:
        levels = _levels_of(frame, profiles, configuration)
    except AstrometricsError as error:
        logger.warning("No camera profile for %s: %s", frame.path, error)
        return _missing(frame, context, f"no camera profile could be read for this frame: {error}")
    try:
        data = read_data(frame.path)
    except (*FITS_READ_ERRORS, *DATA_ERRORS) as error:
        logger.warning("The target's clipping in %s could not be counted: %s", frame.path, error)
        return _missing(frame, context, f"the frame could not be read: {error}")
    if data is None:
        return _missing(frame, context, "the frame could not be read: it holds no pixels")
    pixels = _flat_pixels(np.asarray(data))
    row, column = reference_pixel_to_frame(*reference_position, homography, pixels.shape[0])
    if not (math.isfinite(row) and math.isfinite(column)):
        return _missing(frame, context, "Siril's transform for the frame cannot be inverted")
    counts = count_pixels_in_disc(
        pixels, row, column, radius_px, levels.clip_level_adu, levels.linearity_level_adu
    )
    if counts.inside < MINIMUM_DISC_SHARE_INSIDE * counts.disc_area_px:
        return _missing(
            frame,
            context,
            f"only {counts.inside} of the disc's {counts.disc_area_px:.0f} px lie inside the frame, "
            "so the target is at or beyond the edge",
        )
    status = MeasurementStatus.ASSUMED if levels.is_assumed else MeasurementStatus.MEASURED
    reason = "; ".join([position_note, *levels.notes])
    subject = frame_subject(frame.path, context.target_id, frame.timestamp)
    clipped = build_measurement(
        _SPEC_BY_NAME[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION],
        counts.clipped / counts.inside,
        context,
        subject,
        reason=reason,
        provenance=reason,
        sample_count=counts.inside,
        status=status,
    )
    if counts.nonlinear is None:
        nonlinear = build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION],
            None,
            context,
            subject,
            reason=NO_LINEARITY_LIMIT_REASON,
        )
    else:
        nonlinear = build_measurement(
            _SPEC_BY_NAME[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION],
            counts.nonlinear / counts.inside,
            context,
            subject,
            reason=reason,
            provenance=reason,
            sample_count=counts.inside,
            status=status,
        )
    return [clipped, nonlinear]
