"""Measures how the sky background of a stack is tilted and curved.

The stack's own sky gives the measurement. `sky_plane` cuts the stack into a
grid of bin medians and keeps the bins that are robustly sky (it drops the
bins a bright object or a nebula lifts). This module describes those sky bins
with a few numbers. It measures only. It does not judge a stack, and no number
here is a limit. (The decision between a fitted plane and GraXpert in
`sky_plane` uses the same measure on the same bins.) GraXpert's own model of
the background is no longer measured. Its level is not in the stack's units,
so fractions of it came out about 30 times too large, and no decision used it.

The steps are these.

1. The stack is cut into a grid of 64 by 64 bins and each bin is replaced by
   the median of its pixels. Everything below works on the 64 by 64 map, so
   the numbers do not depend on the stack's size. A color stack's planes are
   averaged first. A bin that is not sky is empty (not a number).
2. The sky level is the median of the sky bins, in the stack's own units.
   Every other number is a fraction of it, so stacks of different depth can be
   compared.
3. The spread is the 5th to 95th percentile range of the map. The range is
   the largest minus the smallest value.
4. A plane (a flat, tilted sheet) is fitted to the map. Its tilt is the
   change across the frame, and its direction is where the sky is brighter.
   The direction is given in the array's own coordinates (the angle from the
   column axis toward the row axis) so stacks of one field compare directly.
   If the stack's header has a sky coordinate system (a WCS), the same
   direction is also given as a position angle on the sky, east of north.
5. A second fit adds a round term, a smooth bowl or dome centered on the
   frame. Its size is how much brighter the corner is than the center. A
   round term looks like a flat-field error, while a tilt looks like a sky
   gradient (see the quality measurements paper).
6. The residual of each fit is the root-mean-square of what the fit leaves
   behind, so a reader can see how well a plane or a plane plus a round term
   describes the sky.

The 64 by 64 map is also saved as a small FITS file next to the stack's
preview files (`background_model_path_for`; the file name says "model" for
historical reasons, and it holds the sky map). A later night of the same field
has the same array coordinates, so the maps can be compared pixel by pixel.
The header keyword ``BGLEVEL`` is the sky level.

A stack that was not measured gets a reason. The reason says whether the
preview was skipped for a bright object, the stack could not be read, or too
few bins were sky, so no skipped measurement goes unreported.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np
from astropy.coordinates import SkyCoord
from astropy.io import fits

from astrometricslib._internal.drivers.fits_access import write_image
from astrometricslib.models.quality.measurement import QualityMeasurement, QualitySubject
from astrometricslib.models.quality.quality_summary import STACKING_PIPELINE_VERSION
from astrometricslib.pipelines.shared.binned_sky_plane import (
    block_medians,
    even_block_edges,
    least_squares_fit,
)
from astrometricslib.pipelines.shared.header_wcs import celestial_wcs
from astrometricslib.pipelines.shared.quality.pipeline_measurements import (
    PipelineRunContext,
    build_measurement,
    record_run_measurements,
)
from astrometricslib.pipelines.stacking.metrics import (
    STACKING_BACKGROUND_MODEL_LEVEL_STACK_UNIT,
    STACKING_BACKGROUND_PLANE_FIT_RMS_FRACTION,
    STACKING_BACKGROUND_RADIAL_FIT_RMS_FRACTION,
    STACKING_BACKGROUND_RADIAL_FRACTION,
    STACKING_BACKGROUND_RANGE_FRACTION,
    STACKING_BACKGROUND_SPREAD_FRACTION,
    STACKING_BACKGROUND_TILT_ANGLE_DEG,
    STACKING_BACKGROUND_TILT_FRACTION,
    STACKING_BACKGROUND_TILT_POSITION_ANGLE_DEG,
    STACKING_METRICS,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BACKGROUND_BIN_COUNT",
    "BackgroundGradient",
    "BackgroundGradientOutcome",
    "background_gradient_measurements",
    "crop_cards_of",
    "measure_sky_background",
    "not_measured_outcome",
    "record_background_gradient",
    "write_binned_sky",
]

# The sky is cut into this many bins along each side before it is measured.
# 64 is the grid of the M 52 trial (2026-11-22, on GraXpert's smooth
# model): fine enough
# that a bowl or tilt spans many bins, coarse enough that the median of each
# bin (over about 2,000 to 8,000 pixels of a typical stack) is smooth, because
# a sky is smooth on that scale. Not tuned on other stacks
# (register entry STK-D74).
BACKGROUND_BIN_COUNT = 64

# The spread is the distance between these two percentiles of the binned
# bins, in percent. The 5th and 95th ignore the few bins at the extreme
# corners, which an edge effect can pull away from the rest. Chosen by
# reasoning; the M 52 trial gave 0.82% against a full range of 1.25%
# (STK-D74).
SPREAD_LOW_PERCENTILE = 5.0
SPREAD_HIGH_PERCENTILE = 95.0

# At least this share of the 64 by 64 bins must hold finite numbers (a stack
# with large empty borders has bins of not-a-number values, and the sky bins
# of a stack with a big nebula are only some of them). Below it the fits
# would describe a corner of the frame, not the frame. 0.3 matches
# `sky_plane.MINIMUM_SKY_BIN_SHARE`, the least share of the measurable bins
# the sky selection keeps, so every stack the selection accepts is measured.
# It was 0.5 until 2026-11-28, which left seven library stacks with 43 to 49%
# sky bins not measured; not tuned (STK-D74).
MINIMUM_FINITE_BIN_FRACTION = 0.3

# The distance, in pixels, used to turn a direction in the array into a
# direction on the sky. Any step well inside the frame gives the same angle;
# the sky coordinate system is smooth on this scale.
_SKY_DIRECTION_STEP_PIXELS = 100.0

# The corner of the frame lies at r^2 = 2 in the fit's coordinates, where the
# frame's edges are at -1 and +1.
_CORNER_RADIUS_SQUARED = 2.0

_SPEC_BY_NAME = {spec.name: spec for spec in STACKING_METRICS}


@dataclass(frozen=True)
class BackgroundGradient:
    """The numbers that describe one stack's sky background.

    Fractions are of the sky level, so 0.01 means 1 percent.

    Attributes
    ----------
    level : `float`
        The median of the sky bins, in the stack's own units.
    spread_fraction : `float`
        The 5th to 95th percentile range of the sky bins.
    range_fraction : `float`
        The largest minus the smallest value of the sky bins.
    tilt_fraction : `float`
        How much the fitted plane changes across the frame: the length of the
        vector made of its change across the full width and across the full
        height.
    tilt_angle_deg : `float`
        The direction in which the plane rises, in degrees from the column
        axis toward the row axis of the array, from 0 up to 360.
    tilt_position_angle_deg : `float` or `None`
        The same direction as a position angle on the sky, in degrees east
        of north. `None` when the stack's header has no sky coordinates.
    position_angle_reason : `str`
        Why `tilt_position_angle_deg` is `None`; empty otherwise.
    radial_fraction : `float`
        The round term of the fit, as the corner level minus the center
        level. Positive means the corners are brighter than the center.
    plane_fit_rms_fraction : `float`
        The root-mean-square residual of the plane fit.
    radial_fit_rms_fraction : `float`
        The root-mean-square residual of the plane plus round-term fit.
    bin_count : `int`
        How many of the bins held finite numbers and were fitted.
    """

    level: float
    spread_fraction: float
    range_fraction: float
    tilt_fraction: float
    tilt_angle_deg: float
    tilt_position_angle_deg: float | None
    position_angle_reason: str
    radial_fraction: float
    plane_fit_rms_fraction: float
    radial_fit_rms_fraction: float
    bin_count: int


@dataclass(frozen=True)
class BackgroundGradientOutcome:
    """What the background measurement of one stack gave.

    Attributes
    ----------
    gradient : `BackgroundGradient` or `None`
        The numbers, or `None` when the stack was not measured.
    reason : `str`
        Why it was not measured; empty when it was.
    binned_sky : `numpy.ndarray` or `None`
        The 64 by 64 map of sky bins the numbers came from (not a number
        where a bin is not sky), for the side file.
    crop_cards : `dict` [`str`, `object`]
        The crop keywords of the measured picture (``CROPX0`` and the
        others), copied into the side file so the map can be placed on the
        stack. Empty when the picture was not cropped.
    """

    gradient: BackgroundGradient | None
    reason: str = ""
    binned_sky: np.ndarray | None = None
    crop_cards: dict[str, object] = field(default_factory=dict)


def not_measured_outcome(reason: str) -> BackgroundGradientOutcome:
    """Give the outcome of a stack whose background was not measured.

    Parameters
    ----------
    reason : `str`
        A sentence that says why.

    Returns
    -------
    outcome : `BackgroundGradientOutcome`
        An outcome with no numbers and that reason.
    """
    return BackgroundGradientOutcome(gradient=None, reason=reason)


def _two_dimensional_sky(model: np.ndarray) -> np.ndarray:
    """Reduce a sky array to one two-dimensional plane.

    Parameters
    ----------
    model : `numpy.ndarray`
        The array: two-dimensional, or color planes in front.

    Returns
    -------
    plane : `numpy.ndarray`
        A two-dimensional array of floating-point numbers. Color planes are
        averaged.

    Raises
    ------
    ValueError
        If the array cannot be read as one image or a few color planes.
    """
    array = np.squeeze(np.asarray(model, dtype=float))
    if array.ndim == 3 and array.shape[0] <= 4:
        array = np.nanmean(array, axis=0)
    if array.ndim != 2 or min(array.shape) < 2:
        raise ValueError(f"the sky array has the shape {tuple(model.shape)}")
    return array


def _sky_position_angle(
    header: fits.Header, row_slope: float, column_slope: float, shape: tuple[int, int]
) -> tuple[float | None, str]:
    """Turn the tilt direction into a position angle on the sky.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header of the picture the sky was measured on.
    row_slope : `float`
        How fast the plane rises per pixel along the rows.
    column_slope : `float`
        How fast the plane rises per pixel along the columns.
    shape : `tuple` [`int`, `int`]
        The picture's size as (rows, columns).

    Returns
    -------
    position_angle_deg : `float` or `None`
        Degrees east of north, from 0 up to 360, or `None` when the header
        has no usable sky coordinates.
    reason : `str`
        Why it is `None`; empty otherwise.
    """
    wcs = celestial_wcs(header)
    if wcs is None:
        return None, "the stack's header has no sky coordinates (WCS)"
    try:
        center_column, center_row = (shape[1] - 1) / 2.0, (shape[0] - 1) / 2.0
        length = math.hypot(row_slope, column_slope)
        step_column = _SKY_DIRECTION_STEP_PIXELS * column_slope / length
        step_row = _SKY_DIRECTION_STEP_PIXELS * row_slope / length
        start = wcs.pixel_to_world(center_column, center_row)
        end = wcs.pixel_to_world(center_column + step_column, center_row + step_row)
        angle = float(SkyCoord(start).position_angle(SkyCoord(end)).deg) % 360.0
    except (ValueError, TypeError, ZeroDivisionError, fits.verify.VerifyError, KeyError) as error:
        return None, f"the stack's sky coordinates could not be used: {error}"
    if not math.isfinite(angle):
        return None, "the stack's sky coordinates gave no direction"
    return angle, ""


def measure_sky_background(
    sky: np.ndarray, header: fits.Header | None = None, picture_shape: tuple[int, int] | None = None
) -> BackgroundGradientOutcome:
    """Describe a stack's sky with the numbers listed in the module text.

    Parameters
    ----------
    sky : `numpy.ndarray`
        The sky: the stack's pixels, or bin medians of it (such as the 64 by
        64 map of `sky_plane`), with not-a-number where a pixel or bin is
        not sky.
    header : `astropy.io.fits.Header`, optional
        The header of the picture the sky came from. Its sky coordinates, if
        any, give the tilt's position angle.
    picture_shape : `tuple` [`int`, `int`], optional
        The picture's size as (rows, columns), when `sky` is already binned
        and so has lost the picture's shape. The tilt direction needs it.
        Left out, the shape of `sky` is used.

    Returns
    -------
    outcome : `BackgroundGradientOutcome`
        The numbers and the 64 by 64 map, or the reason nothing was measured.
        This never raises for a sky that cannot be measured.
    """
    try:
        plane = _two_dimensional_sky(sky)
    except ValueError as error:
        return not_measured_outcome(f"the sky could not be read: {error}")
    rows, columns = picture_shape or plane.shape
    binned = block_medians(
        plane,
        even_block_edges(plane.shape[0], BACKGROUND_BIN_COUNT),
        even_block_edges(plane.shape[1], BACKGROUND_BIN_COUNT),
    )
    finite = np.isfinite(binned)
    if finite.mean() < MINIMUM_FINITE_BIN_FRACTION:
        return not_measured_outcome(f"only {100 * finite.mean():.0f}% of the bins of the sky hold numbers")
    level = float(np.median(binned[finite]))
    if not math.isfinite(level) or level <= 0.0:
        return not_measured_outcome("the sky level is not above zero")
    values = binned[finite]
    low, high = np.percentile(values, [SPREAD_LOW_PERCENTILE, SPREAD_HIGH_PERCENTILE])
    row_index, column_index = np.nonzero(finite)
    # Coordinates run from -1 to +1 across each side, so the fitted numbers do
    # not depend on the stack's size.
    x = (column_index + 0.5) / BACKGROUND_BIN_COUNT * 2.0 - 1.0
    y = (row_index + 0.5) / BACKGROUND_BIN_COUNT * 2.0 - 1.0
    radius_squared = x**2 + y**2
    one = np.ones_like(x)
    plane_coefficients, plane_rms = least_squares_fit([one, x, y], values)
    radial_coefficients, radial_rms = least_squares_fit(
        [one, x, y, radius_squared, radius_squared**2], values
    )
    tilt = 2.0 * math.hypot(plane_coefficients[1], plane_coefficients[2])
    # The slopes per pixel: the coordinate runs over half the side per unit.
    column_slope = plane_coefficients[1] / (columns / 2.0)
    row_slope = plane_coefficients[2] / (rows / 2.0)
    tilt_angle = math.degrees(math.atan2(row_slope, column_slope)) % 360.0
    position_angle, position_reason = (
        (None, "the stack's header was not given")
        if header is None
        else _sky_position_angle(header, row_slope, column_slope, (rows, columns))
    )
    radial = (
        radial_coefficients[3] * _CORNER_RADIUS_SQUARED + radial_coefficients[4] * _CORNER_RADIUS_SQUARED**2
    )
    gradient = BackgroundGradient(
        level=level,
        spread_fraction=float((high - low) / level),
        range_fraction=float((values.max() - values.min()) / level),
        tilt_fraction=float(tilt / level),
        tilt_angle_deg=float(tilt_angle),
        tilt_position_angle_deg=position_angle,
        position_angle_reason=position_reason,
        radial_fraction=float(radial / level),
        plane_fit_rms_fraction=float(plane_rms / level),
        radial_fit_rms_fraction=float(radial_rms / level),
        bin_count=int(finite.sum()),
    )
    return BackgroundGradientOutcome(gradient=gradient, binned_sky=binned)


def crop_cards_of(header: fits.Header) -> dict[str, object]:
    """Pick the crop keywords out of a picture's header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The header of the picture that was measured.

    Returns
    -------
    crop_cards : `dict` [`str`, `object`]
        ``CROPX0``, ``CROPY0``, ``CROPW`` and ``CROPH`` as far as the header
        has them; empty for a picture that was not cropped.
    """
    return {key: header[key] for key in ("CROPX0", "CROPY0", "CROPW", "CROPH") if key in header}


def write_binned_sky(outcome: BackgroundGradientOutcome, path: str) -> bool:
    """Save the 64 by 64 map of a measured stack's sky as a small FITS file.

    Parameters
    ----------
    outcome : `BackgroundGradientOutcome`
        The measurement. Nothing is written when it has no map.
    path : `str`
        The file to write (see `background_model_path_for`).

    Returns
    -------
    written : `bool`
        `True` if the file was written. A failure to write is logged as a
        warning.
    """
    if outcome.binned_sky is None or outcome.gradient is None:
        return False
    header = fits.Header()
    header["BINCOUNT"] = (BACKGROUND_BIN_COUNT, "Bins along each side of the map")
    header["BGLEVEL"] = (outcome.gradient.level, "Sky level (stack units)")
    header["COMMENT"] = "Median of the stack's pixels in each bin, in the picture's array order."
    header["COMMENT"] = "A bin that is not sky (a bright object or nebula lifts it) is empty (NaN)."
    header["COMMENT"] = "Bins are evenly spaced over the picture. CROP cards place the picture on the stack."
    for key, value in outcome.crop_cards.items():
        header[key] = value
    try:
        write_image(path, outcome.binned_sky.astype(np.float32), header)
    except (OSError, ValueError) as error:
        logger.warning("The sky background map '%s' could not be written: %s", path, error)
        return False
    return True


def background_gradient_measurements(
    outcome: BackgroundGradientOutcome, context: PipelineRunContext, subject: QualitySubject
) -> list[QualityMeasurement]:
    """Build the stack's background rows.

    Parameters
    ----------
    outcome : `BackgroundGradientOutcome`
        What was measured, or why nothing was.
    context : `PipelineRunContext`
        The run's shared facts.
    subject : `QualitySubject`
        The stack.

    Returns
    -------
    measurements : `list` of `QualityMeasurement`
        One row for each of the nine metrics. A value that is missing is
        not measured, with its reason.
    """
    gradient = outcome.gradient
    reason = outcome.reason or "the background was not measured"
    provenance = (
        ""
        if gradient is None
        else f"{gradient.bin_count} bins of {BACKGROUND_BIN_COUNT} by {BACKGROUND_BIN_COUNT}, "
        "of the stack's own sky"
    )
    rows = (
        (STACKING_BACKGROUND_MODEL_LEVEL_STACK_UNIT, None if gradient is None else gradient.level, reason),
        (STACKING_BACKGROUND_SPREAD_FRACTION, None if gradient is None else gradient.spread_fraction, reason),
        (STACKING_BACKGROUND_RANGE_FRACTION, None if gradient is None else gradient.range_fraction, reason),
        (STACKING_BACKGROUND_TILT_FRACTION, None if gradient is None else gradient.tilt_fraction, reason),
        (STACKING_BACKGROUND_TILT_ANGLE_DEG, None if gradient is None else gradient.tilt_angle_deg, reason),
        (
            STACKING_BACKGROUND_TILT_POSITION_ANGLE_DEG,
            None if gradient is None else gradient.tilt_position_angle_deg,
            reason if gradient is None else gradient.position_angle_reason,
        ),
        (STACKING_BACKGROUND_RADIAL_FRACTION, None if gradient is None else gradient.radial_fraction, reason),
        (
            STACKING_BACKGROUND_PLANE_FIT_RMS_FRACTION,
            None if gradient is None else gradient.plane_fit_rms_fraction,
            reason,
        ),
        (
            STACKING_BACKGROUND_RADIAL_FIT_RMS_FRACTION,
            None if gradient is None else gradient.radial_fit_rms_fraction,
            reason,
        ),
    )
    return [
        build_measurement(
            _SPEC_BY_NAME[name],
            value,
            context,
            subject,
            reason=row_reason,
            sample_count=None if gradient is None or value is None else gradient.bin_count,
            provenance=provenance,
        )
        for name, value, row_reason in rows
    ]


def record_background_gradient(
    outcomes: list[BackgroundGradientOutcome],
    target_id: str,
    stack_file_name: str,
    job_id: str | None,
    measured_at: datetime | None,
    configuration: Any | None,
) -> None:
    """Store the background rows of a stack once its preview has been made.

    This never stops a preview or a stack. Nothing is stored when the caller
    gave no measurement time (pipeline code does not read the clock). A
    failure to store is logged as a warning.

    Parameters
    ----------
    outcomes : `list` of `BackgroundGradientOutcome`
        What `write_stack_preview` collected. Empty when the preview never got
        as far as the background step (no Siril, or the stack file is
        missing), which is stored as a not-measured row with that reason.
    target_id : `str`
        The target's id.
    stack_file_name : `str`
        The stack's file name.
    job_id : `str` or `None`
        The job the preview ran in.
    measured_at : `datetime` or `None`
        When the run started, with a time zone.
    configuration : `AppConfiguration` or `None`
        Where the quality store lives. The process-wide one if `None`.
    """
    if measured_at is None:
        return
    outcome = (
        outcomes[-1]
        if outcomes
        else not_measured_outcome(
            "no preview was made (Siril is not configured or the stack file is missing), "
            "so the sky was not measured"
        )
    )
    try:
        context = PipelineRunContext.from_options(
            target_id, "stacking", STACKING_PIPELINE_VERSION, {"job_id": job_id, "measured_at": measured_at}
        )
        if context is None:
            return
        rows = background_gradient_measurements(outcome, context, context.stack_subject(stack_file_name))
        record_run_measurements(rows, config=configuration)
    except Exception:  # a by-product of the stack: no failure here may fail it
        logger.warning("The background rows of the stack %s were not stored", stack_file_name, exc_info=True)
