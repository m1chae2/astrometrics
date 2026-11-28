"""Per-frame photometry helpers used by `VariabilityAnalyzer`.

These are the raw, per-frame measurement steps -- reading a frame's
exposure time, airmass, and capture time from its FITS header,
re-locating a star's centroid, and measuring a star's aperture flux --
with no shared state across frames. They are module-level functions rather
than methods because `_process_single_frame_worker` is dispatched to a
`ProcessPoolExecutor` and needs to be picklable/forkable independent of any
analyzer instance.

Three rules keep the measured light curves honest:

* Aperture centers are never rounded to whole pixels. Each star is placed
  where the frame's own plate solution puts it (see `FrameMapping`), then
  re-centered on its own brightness-weighted centroid (see
  `refine_star_centroid`). When that refinement is not trustworthy, the star
  keeps the solution's position and the frame result records why.
* A frame whose ``DATE-OBS`` is missing or unreadable is rejected with a
  reason (see `read_observation_time`). It never gets the wall-clock time.
* Every flux comes with its 1-sigma uncertainty, from the CCD equation (see
  `aperture_flux_error_adu`). The uncertainty uses the camera's gain and
  read noise (see `detector_noise`). The flux values themselves do not
  depend on it.
"""

import logging
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.time import Time
from photutils.aperture import CircularAnnulus, CircularAperture
from photutils.centroids import centroid_com

from astrometricslib._internal.drivers.fits_access import collapse_to_2d, read_header_and_data
from astrometricslib.foundation.errors import ProcessingError
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.run_bases import factor_for
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    RunBasis,
    TunedParameter,
)
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.photometry.pre_processing.frame_mapping import FrameMapping
from astrometricslib.pipelines.shared.neighbor_masking import (
    NeighborField,
    clipped_median_and_sigma,
    neighbor_mask,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)
from astrometricslib.pipelines.shared.quality.star_profile_fitting import (
    MOFFAT_BETA_START,
    detect_stars_without_shape_cut,
)
from astrometricslib.pipelines.shared.quality.star_ring import (
    STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE,
    STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE,
    STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX,
    STAR_RING_TARGET_MATCH_RADIUS_PX,
)

logger = logging.getLogger(__name__)


def _read_exposure_seconds(header: Any) -> float:
    """Read how long the camera shutter was open (exposure time).

    We need this to calculate light-per-second. If a picture has no
    exposure time recorded, we assume 1 second to avoid math errors.

    Returns
    -------
    exposure_s : `float`
        The exposure time in seconds.
    """
    for key in ("EXPTIME", "EXPOSURE"):
        value = header.get(key)
        if value is not None:
            try:
                value = float(value)
            except TypeError, ValueError:
                continue
            if value > 0:
                return value
    return 1.0


class ObservationTimeError(ProcessingError):
    """A frame's capture time is missing or cannot be read.

    The message is a short sentence saying why. Callers store it as the
    reason the frame was left out of the run.
    """


# A FITS date and time with an optional time zone suffix: ``Z`` or a UTC
# offset such as ``+02:00``, ``-0530`` or ``+02``. INDI and Ekos write no
# suffix (UTC is understood), so the suffix is an accepted extra, not the
# usual form.
_DATE_OBS_WITH_ZONE = re.compile(
    r"(?P<stamp>\d{4}-\d{2}-\d{2}[Tt][0-9:.]+)\s*"
    r"(?:(?P<utc>[Zz])|(?P<sign>[+-])(?P<hours>\d{2})(?::?(?P<minutes>\d{2}))?)?"
)

# The largest UTC offset, in hours, a ``DATE-OBS`` suffix may carry. Real
# time zones run from -12 to +14 hours.
_MAXIMUM_UTC_OFFSET_HOURS = 14


def parse_observation_time(value: Any) -> datetime:
    """Turn a ``DATE-OBS`` header value into a UTC capture time.

    The value must be a full FITS date and time. These forms are accepted:

    * ``2026-05-24T04:58:30.570``: no suffix. This is what INDI and Ekos
      write, and it means UTC.
    * ``2026-05-24T04:58:30.570Z`` or ``...+00:00``: explicit UTC.
    * ``2026-05-24T06:58:30.570+02:00``: a UTC offset (also ``-0530`` or
      ``+02``). The function subtracts the offset to get UTC, so this
      example returns 04:58:30.570.

    astropy's `~astropy.time.Time` reads the date and time part, which
    accepts the FITS forms that `datetime.fromisoformat` rejects on older
    Python versions. A date with no time of day is refused: it would put
    every frame of a night at midnight and flatten the light curve's time
    axis.

    Parameters
    ----------
    value : `Any`
        The header value. Normally a `str`.

    Returns
    -------
    observed_at : `datetime.datetime`
        The capture time in UTC, with no time zone attached (the same
        convention as the capture times stored on frames).

    Raises
    ------
    ObservationTimeError
        If the value is not a string, has no time of day, has a UTC offset
        outside -14 to +14 hours, or is not a valid date and time.
    """
    if not isinstance(value, str) or not value.strip():
        raise ObservationTimeError(f"DATE-OBS is not a date string (got {value!r})")
    text = value.strip()
    if "T" not in text.upper():
        raise ObservationTimeError(f"DATE-OBS {text!r} has no time of day")
    offset = timedelta(0)
    zone_match = _DATE_OBS_WITH_ZONE.fullmatch(text)
    if zone_match is not None:
        text = zone_match["stamp"]
        if zone_match["sign"] is not None:
            hours, minutes = int(zone_match["hours"]), int(zone_match["minutes"] or 0)
            if hours > _MAXIMUM_UTC_OFFSET_HOURS or minutes > 59:
                raise ObservationTimeError(f"DATE-OBS {value.strip()!r} has an impossible UTC offset")
            offset = timedelta(hours=hours, minutes=minutes)
            if zone_match["sign"] == "-":
                offset = -offset
    for time_format in ("isot", "fits"):
        try:
            return Time(text, format=time_format, scale="utc").to_datetime() - offset
        except ValueError:
            continue
    raise ObservationTimeError(f"DATE-OBS {value.strip()!r} is not a valid FITS date and time")


def read_observation_time(header: Any) -> datetime:
    """Read a frame's capture time from its FITS header.

    Parameters
    ----------
    header : `astropy.io.fits.Header`
        The frame's primary header.

    Returns
    -------
    observed_at : `datetime.datetime`
        The capture time in UTC (see `parse_observation_time`).

    Raises
    ------
    ObservationTimeError
        If ``DATE-OBS`` is absent or unreadable. The caller must reject
        the frame. This function never falls back to the current time.
    """
    value = header.get("DATE-OBS")
    if value is None:
        raise ObservationTimeError("DATE-OBS is missing from the FITS header")
    return parse_observation_time(value)


def compute_frame_airmass(header: fits.Header) -> float:
    """Figure out how much atmosphere we are looking through.

    "Airmass" is 1.0 when looking straight up, and gets higher as you
    look toward the horizon (because you look through more air).

    Returns
    -------
    airmass : `float`
        The airmass value, or 1.0 if we can't figure it out.
    """
    for key in ["AIRMASS", "CENTAIRM", "AIRM"]:
        if key in header:
            try:
                val = float(header[key])
                if 1.0 <= val <= 10.0:
                    return val
            except ValueError, TypeError:
                pass

    for alt_key in ["CENTALT", "ALTITUDE", "ALT"]:
        if alt_key in header:
            try:
                alt_deg = float(header[alt_key])
                if 0 < alt_deg <= 90:
                    zenith_rad = np.radians(90.0 - alt_deg)
                    return float(1.0 / np.cos(zenith_rad))
            except ValueError, TypeError:
                pass

    return 1.0


# Half-width, in pixels, of the square box used to re-center one star. The
# box is 2 * 5 + 1 = 11 pixels wide, about 2.5 times the 4 pixel FWHM (full
# width at half maximum, the star's apparent size) the default aperture is
# sized for. A box much narrower than 2 FWHM cuts off the star's wings
# unevenly, which pulls the centroid toward the box center.
# The half-width stands for about 1.7 star widths (5 px at the reference
# setup's 3 px width) and follows the measured star width.
CENTROID_BOX_HALF_WIDTH_PX = TunedParameter(
    name="photometry.centroid_box_half_width_px",
    default=factor_for(5, RunBasis.STAR_WIDTH_PX),
    reference_value=5,
    unit="× star width",
    description="Half-width of the square box used to re-center one star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D02",
)

# The largest move, in pixels, a re-centering may make from the position the
# frame's plate solution gives. A larger move means the box found a neighbor,
# a hot pixel, or noise instead of the star, so the refinement is discarded. A
# design estimate: the solution's positions agreed with the stars' measured
# centers to well under a pixel (PHO-24), so 1.5 px leaves room for an
# imperfect solution but not for jumping to another star.
# The move stands for half a star width (1.5 px at the reference setup's 3 px
# width) and follows the measured star width.
MAX_CENTROID_SHIFT_PX = TunedParameter(
    name="photometry.max_centroid_shift_px",
    default=factor_for(1.5, RunBasis.STAR_WIDTH_PX),
    reference_value=1.5,
    unit="× star width",
    description="Largest move a re-centering may make from the plate solution's position.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-04",
)

# Half-width, in pixels, of the square cutout whose median is the local sky.
# The half-width stands for five star widths (15 px at the reference setup's 3
# px width) and follows the measured star width.
CENTROID_SKY_CUTOUT_HALF_WIDTH_PX = TunedParameter(
    name="photometry.centroid_sky_cutout_half_width_px",
    default=factor_for(15, RunBasis.STAR_WIDTH_PX),
    reference_value=15,
    unit="× star width",
    description="Half-width of the square cutout whose median is the local sky.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D02",
)

# How many times the centroid box is re-centered on its own result. One
# pass is biased when the star sits off the box center, because the box then
# cuts more of the wings on one side. Three passes remove that bias.
CENTROID_REFINEMENT_PASSES = TunedParameter(
    name="photometry.centroid_refinement_passes",
    default=3,
    unit="passes",
    description="How many times the centroid box is re-centered on its own result.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="PHO-D02",
)


@dataclass(frozen=True)
class StarPosition:
    """Where one star was measured in one frame, and how that was decided.

    Attributes
    ----------
    x : `float`
        Column of the aperture center, in pixels. Not rounded.
    y : `float`
        Row of the aperture center, in pixels. Not rounded.
    fallback_reason : `str` or `None`
        `None` when ``(x, y)`` is the star's own brightness-weighted
        centroid. Otherwise a short phrase saying why the centroid was
        rejected. ``(x, y)`` is then the position the frame's plate solution
        gives. The analyzer counts these per star.
    """

    x: float
    y: float
    fallback_reason: str | None = None

    @property
    def is_refined(self) -> bool:
        """Whether ``(x, y)`` came from the star's own centroid.

        Returns
        -------
        is_refined : `bool`
            `True` when no fallback was needed.
        """
        return self.fallback_reason is None


def refine_star_centroid(
    data: np.ndarray,
    x: float,
    y: float,
    *,
    saturation_threshold_adu: float,
    box_half_width: int | None = None,
    max_shift_px: float | None = None,
) -> StarPosition:
    """Re-center one star on its brightness-weighted centroid.

    The frame's plate solution gives each star's position to a fraction of a
    pixel, but not exactly, and an aperture that is off-center loses part of
    the star's light. This function measures the star's own center in a
    small box.

    The steps are:

    1. Cut a square box around ``(x, y)`` and subtract the local sky, the
       median of a larger cutout around the same point.
    2. Set negative pixels to zero, so noise below the sky does not count
       as light.
    3. Take the brightness-weighted mean of the pixel positions (the
       centroid).
    4. Repeat from the new position a few times, so the box ends up
       centered on the star.
    5. Refuse the result if the box holds a saturated pixel, holds no
       light, leaves the frame, or the centroid moved more than
       ``max_shift_px`` from ``(x, y)``. Then return ``(x, y)`` unchanged
       with the reason.

    Parameters
    ----------
    data : `numpy.ndarray`
        The frame, shape ``(ny, nx)``.
    x, y : `float`
        Starting position in pixels: where the frame's plate solution puts
        the star.
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated. Take it from
        the camera's profile.
    box_half_width : `int`, optional
        Half the width of the box, in whole pixels.
    max_shift_px : `float`, optional
        The largest allowed move from ``(x, y)``, in pixels.

    Returns
    -------
    position : `StarPosition`
        The centroid, or the starting position with the reason the
        centroid was refused.
    """
    centroid_refinement_passes = resolve(CENTROID_REFINEMENT_PASSES).value
    centroid_sky_cutout_half_width_px = resolve(CENTROID_SKY_CUTOUT_HALF_WIDTH_PX).value
    if box_half_width is None:
        box_half_width = resolve(CENTROID_BOX_HALF_WIDTH_PX).value
    if max_shift_px is None:
        max_shift_px = resolve(MAX_CENTROID_SHIFT_PX).value
    height, width = data.shape
    centre_x, centre_y = x, y
    for _ in range(centroid_refinement_passes):
        ix, iy = round(centre_x), round(centre_y)
        x0, x1 = ix - box_half_width, ix + box_half_width + 1
        y0, y1 = iy - box_half_width, iy + box_half_width + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return StarPosition(x, y, "centroid box falls outside the frame")

        box = data[y0:y1, x0:x1]
        if np.any(box >= saturation_threshold_adu):
            return StarPosition(x, y, "saturated pixel in the centroid box")

        sky_x0, sky_x1 = (
            max(0, ix - centroid_sky_cutout_half_width_px),
            min(width, ix + centroid_sky_cutout_half_width_px + 1),
        )
        sky_y0, sky_y1 = (
            max(0, iy - centroid_sky_cutout_half_width_px),
            min(height, iy + centroid_sky_cutout_half_width_px + 1),
        )
        sky_level = np.median(data[sky_y0:sky_y1, sky_x0:sky_x1])

        weights = np.clip(box - sky_level, 0.0, None)
        if not np.all(np.isfinite(weights)) or weights.sum() <= 0:
            return StarPosition(x, y, "no light above the sky in the centroid box")

        centroid_x, centroid_y = centroid_com(weights)
        centre_x, centre_y = x0 + float(centroid_x), y0 + float(centroid_y)

    if np.hypot(centre_x - x, centre_y - y) > max_shift_px:
        return StarPosition(x, y, f"centroid moved more than {max_shift_px:g} px from the solution")
    return StarPosition(centre_x, centre_y)


def centroid_offsets_px(
    positions: Mapping[str, StarPosition],
    reference_positions: Mapping[str, tuple[float, float]],
    mapping: FrameMapping,
) -> tuple[list[float], int]:
    """Measure how far each star's centroid sits from the solution's place.

    The frame's plate solution says where each star should be. A star whose
    own centroid lands elsewhere has a per-star offset. What is left of the
    offset is the centroid noise and any distortion the solution does not
    describe.

    A star whose centroid was refused sits at the solution's position by
    construction, so its offset says nothing. It is counted instead.

    Parameters
    ----------
    positions : `Mapping` [`str`, `StarPosition`]
        Where each star was measured in one frame.
    reference_positions : `Mapping` [`str`, `tuple` [`float`, `float`]]
        Each star's ``(x, y)`` in the reference frame.
    mapping : `FrameMapping`
        The frame's mapping from the reference frame.

    Returns
    -------
    offsets_px : `list` [`float`]
        The offset of each star with a refined centroid, in pixels.
    fallback_count : `int`
        How many stars kept the solution's position.
    """
    offsets: list[float] = []
    fallback_count = 0
    for star_id, position in positions.items():
        reference = reference_positions.get(star_id)
        if reference is None:
            continue
        if not position.is_refined:
            fallback_count += 1
            continue
        expected_x, expected_y = mapping.predict(reference[0], reference[1])
        offsets.append(math.hypot(position.x - expected_x, position.y - expected_y))
    return offsets, fallback_count


@dataclass(frozen=True)
class CentroidShiftSummary:
    """How well one session's frames lined up star by star.

    Attributes
    ----------
    frame_count : `int`
        Frames with at least one measured star, not counting the reference
        frame.
    measurement_count : `int`
        Star measurements in those frames (one per star per frame).
    fallback_count : `int`
        How many of those measurements kept the plate solution's position
        because the star's own centroid was refused.
    median_offset_px : `float` or `None`
        The median per-star offset, in pixels, over the measurements with a
        refined centroid. `None` when there were none.
    p95_offset_px : `float` or `None`
        The 95th percentile of the same offsets, in pixels. It is large when
        the solutions do not describe the stars' places, for example when
        the stars were found in a frame with a different distortion.
    worst_frame : `datetime.datetime` or `None`
        The capture time of the frame with the largest per-frame 95th
        percentile.
    worst_frame_p95_offset_px : `float` or `None`
        That frame's 95th percentile, in pixels.
    corner_displacement_p95_px : `float` or `None`
        The 95th percentile, over the frames, of how far a corner of the
        frame moves beyond the shift of its middle (see
        `FrameMapping.corner_displacement_px`). It is what a single shift
        cannot describe. `None` when no frame was mapped.
    worst_corner_frame : `datetime.datetime` or `None`
        The capture time of the frame with the largest corner displacement.
    worst_corner_displacement_px : `float` or `None`
        That frame's corner displacement, in pixels.
    mapped_frame_count : `int`
        How many frames the plate solutions mapped onto the reference frame.
    """

    frame_count: int
    measurement_count: int
    fallback_count: int
    median_offset_px: float | None
    p95_offset_px: float | None
    worst_frame: datetime | None
    worst_frame_p95_offset_px: float | None
    corner_displacement_p95_px: float | None = None
    worst_corner_frame: datetime | None = None
    worst_corner_displacement_px: float | None = None
    mapped_frame_count: int = 0

    @property
    def fallback_fraction(self) -> float:
        """Give the share of measurements that kept the solution's position.

        Returns
        -------
        fraction : `float`
            Between 0 and 1. It is 0.0 when nothing was measured.
        """
        return self.fallback_count / self.measurement_count if self.measurement_count else 0.0


def summarize_centroid_shifts(
    offsets_by_frame: Mapping[datetime, Sequence[float]],
    measurement_count: int,
    fallback_count: int,
    *,
    mappings_by_frame: Mapping[datetime, FrameMapping] | None = None,
) -> CentroidShiftSummary | None:
    """Summarize a session's per-star centroid offsets and frame mappings.

    Parameters
    ----------
    offsets_by_frame : `Mapping` [`datetime.datetime`, `Sequence` [`float`]]
        For each frame, the per-star offsets in pixels (see
        `centroid_offsets_px`).
    measurement_count : `int`
        Star measurements in all the frames, refined or not.
    fallback_count : `int`
        How many of them kept the solution's position.
    mappings_by_frame : `Mapping`, optional
        Each frame's `FrameMapping`, keyed by capture time.

    Returns
    -------
    summary : `CentroidShiftSummary` or `None`
        The summary, or `None` when nothing was measured (a one-frame
        session).
    """
    if measurement_count <= 0:
        return None
    corner_displacements = {
        timestamp: mapping.corner_displacement_px for timestamp, mapping in (mappings_by_frame or {}).items()
    }
    worst_corner_frame = max(corner_displacements, key=corner_displacements.__getitem__, default=None)
    all_offsets = [offset for offsets in offsets_by_frame.values() for offset in offsets]
    worst_frame: datetime | None = None
    worst_p95: float | None = None
    for timestamp, offsets in offsets_by_frame.items():
        if not len(offsets):
            continue
        frame_p95 = float(np.percentile(offsets, 95))
        if worst_p95 is None or frame_p95 > worst_p95:
            worst_frame, worst_p95 = timestamp, frame_p95
    return CentroidShiftSummary(
        frame_count=len(offsets_by_frame),
        measurement_count=measurement_count,
        fallback_count=fallback_count,
        median_offset_px=float(np.median(all_offsets)) if all_offsets else None,
        p95_offset_px=float(np.percentile(all_offsets, 95)) if all_offsets else None,
        worst_frame=worst_frame,
        worst_frame_p95_offset_px=worst_p95,
        corner_displacement_p95_px=(
            float(np.percentile(list(corner_displacements.values()), 95)) if corner_displacements else None
        ),
        worst_corner_frame=worst_corner_frame,
        worst_corner_displacement_px=(
            corner_displacements[worst_corner_frame] if worst_corner_frame is not None else None
        ),
        mapped_frame_count=len(corner_displacements),
    )


# The aperture the photometry measures every star in, in pixels. The star
# circle has a radius of 4 px, about 1 to 2 times the star's FWHM (full width
# at half maximum) on a good night, and the sky ring runs from 7 to 12 px,
# which keeps it clear of the star's wings. These are the defaults of
# `measure_aperture_photometry` and the geometry behind the noise budget in
# `processing/noise_budget.py`, so they are defined once, here.
# The radius stands for about 1.3 star widths (4 px at the reference setup's 3
# px width) and follows the measured star width. The two sky ring radii below
# follow it the same way.
APERTURE_RADIUS_PX = TunedParameter(
    name="photometry.aperture_radius_px",
    default=factor_for(4.0, RunBasis.STAR_WIDTH_PX),
    reference_value=4.0,
    unit="× star width",
    description="Radius of the circle in which photometry measures every star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D01",
)
# The inner radius stands for about 2.3 star widths (7 px at the reference
# setup) and follows the measured star width.
SKY_ANNULUS_INNER_RADIUS_PX = TunedParameter(
    name="photometry.sky_annulus_inner_radius_px",
    default=factor_for(7.0, RunBasis.STAR_WIDTH_PX),
    reference_value=7.0,
    unit="× star width",
    description="Inner radius of the sky ring around a star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D01",
)
# The outer radius stands for four star widths (12 px at the reference setup)
# and follows the measured star width.
SKY_ANNULUS_OUTER_RADIUS_PX = TunedParameter(
    name="photometry.sky_annulus_outer_radius_px",
    default=factor_for(12.0, RunBasis.STAR_WIDTH_PX),
    reference_value=12.0,
    unit="× star width",
    description="Outer radius of the sky ring around a star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D01",
)


# The star width the neighbor mask draws a star's profile with. It is one
# star width (the FWHM, full width at half maximum) of the measured frames, so
# it follows the run's star width.
NEIGHBOR_PROFILE_WIDTH_PX = TunedParameter(
    name="photometry.neighbor_profile_width_px",
    default=factor_for(2.33, RunBasis.STAR_WIDTH_PX),
    reference_value=2.33,
    unit="× star width",
    description="Width of the star profile used to size the mask of a neighbor star in the sky ring.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D35",
)
# A star is crowded when a bright neighbor's center lies within the aperture
# radius plus this margin. At 1.5 star widths beyond the circle's edge, a
# Gaussian star has fallen to 0.2 percent of its peak at the edge, and a
# Moffat star with beta = 3 (wider wings) to about 1 percent, so a neighbor
# farther than that adds under 1 percent of its own peak to the circle. The
# margin follows the star width (3.5 px at the reference setup's 2.33 px).
CROWDING_MARGIN_PX = TunedParameter(
    name="photometry.crowding_margin_px",
    default=factor_for(3.5, RunBasis.STAR_WIDTH_PX),
    reference_value=3.5,
    unit="× star width",
    description="How far beyond the aperture's edge a neighbor's center can lie and still crowd the star.",
    role=ParameterRole.MEASUREMENT,
    source=ParameterSource.DETERMINED_EACH_RUN,
    basis=RunBasis.STAR_WIDTH_PX,
    equipment=frozenset({EquipmentKind.TELESCOPE, EquipmentKind.CAMERA}),
    register_entry="PHO-D35",
)
# A neighbor only crowds a star when it is bright enough to matter. A
# neighbor with a tenth of the star's peak, at the edge of the crowding
# margin, adds about 0.1 percent of the star's peak to the circle (the 1
# percent wing level above times 0.1), well under the 1 percent flux error
# that photometry aims at. Closer neighbors add more, so the margin and this
# fraction together hold the flux bias near 1 percent.
CROWDING_NEIGHBOR_PEAK_FRACTION = TunedParameter(
    name="photometry.crowding_neighbor_peak_fraction",
    default=0.1,
    unit="fraction of the star's peak",
    description="Smallest neighbor peak, as a share of the star's own peak, that makes a star crowded.",
    role=ParameterRole.RECOMMENDATION,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.METHOD_CHOICE,
    register_entry="PHO-D35",
)

# The sky ring's clipping, the same as the ring of the star-ring analysis and
# the sky bands of the spectrum extractor: pixels more than 3 robust sigmas
# from the median are dropped, over at most 5 passes. The ring needs at
# least 10 pixels, as the star-ring analysis does.
_SKY_CLIP_SIGMA = 3.0
_SKY_CLIP_MAXIMUM_PASSES = 5
_SKY_MINIMUM_PIXELS = 10


def detect_neighbor_field(data: np.ndarray) -> NeighborField | None:
    """List the stars of a frame, including close pairs.

    The run's own star list keeps stars at least 2.5 star widths (about 10 px)
    apart, which hides the faint partner of a close pair, the neighbor that
    matters most for a star's aperture. This runs the same star finder as the
    astrometry stage, with the minimum separation lowered to one star width.

    Parameters
    ----------
    data : `numpy.ndarray`
        The 2-D frame, in ADU.

    Returns
    -------
    field : `NeighborField` or `None`
        The stars with their peaks above the local background. `None` when no
        star was found.
    """
    star_width = float(resolve(NEIGHBOR_PROFILE_WIDTH_PX).value)
    sources = detect_stars_without_shape_cut(data, expected_fwhm_px=star_width, min_separation=star_width)
    if not sources:
        return None
    return NeighborField(
        np.array([[source["x_centroid"], source["y_centroid"]] for source in sources], dtype=float),
        np.array([source["peak"] for source in sources], dtype=float),
        np.array([source["flux"] for source in sources], dtype=float),
    )


def aperture_flux_error_adu(
    net_flux_adu: float,
    sky_adu_per_pixel: float,
    aperture_pixels: float,
    sky_pixels: float,
    noise: DetectorNoise,
    dark_current_e_per_pixel: float = 0.0,
) -> float:
    """Find the 1-sigma uncertainty of a sky-subtracted aperture sum.

    This is the CCD equation (the noise budget of a sensor). It works in
    electrons, because the random scatter of a count is set by the number of
    electrons collected:

    ``variance_e = F_e + n_pix * (S_e + RN**2 + D_e)
    + (n_pix**2 / n_sky) * (S_e + RN**2 + D_e)``

    where ``F_e`` is the star's net counts in electrons (its own shot
    noise), ``S_e`` the sky level per pixel in electrons, ``RN`` the read
    noise in electrons, ``D_e`` the dark current per pixel in electrons
    (heat-made electrons collected during the exposure), ``n_pix`` the
    number of pixels in the aperture and ``n_sky`` the number of pixels in
    the sky ring. The first term is the star's own noise. The second is the
    noise of the sky, read-out and dark counts inside the aperture. The third
    is the noise in the sky level itself, which the sky ring measures from
    finitely many pixels. Each ring pixel carries the same sky, read-out and
    dark noise as an aperture pixel, so the third term has the same three
    parts. The result is converted back to ADU by dividing by
    the gain.

    The sky level is in the same units as the data. The equation assumes the
    data have had their bias level (the fixed offset the camera adds) and
    dark signal subtracted, as calibrated frames do. On a frame with a bias
    offset still in it, the sky term is overestimated.

    Parameters
    ----------
    net_flux_adu : `float`
        The sky-subtracted sum of the aperture, in ADU. A negative value is
        treated as zero.
    sky_adu_per_pixel : `float`
        The sky level in the ring, in ADU per pixel. A negative value is
        treated as zero.
    aperture_pixels : `float`
        The area of the aperture, in pixels.
    sky_pixels : `float`
        The number of pixels in the sky ring. With zero, the sky level is
        treated as known exactly and the third term is left out.
    noise : `DetectorNoise`
        The gain (electrons per ADU) and read noise (electrons).
    dark_current_e_per_pixel : `float`, optional
        Dark current collected per pixel during the exposure, in electrons.
        Zero when unknown.

    Returns
    -------
    error_adu : `float`
        The 1-sigma uncertainty of `net_flux_adu`, in ADU. It is not divided
        by the exposure time.
    """
    gain = noise.gain_e_per_adu
    source_e = max(net_flux_adu, 0.0) * gain
    sky_e = max(sky_adu_per_pixel, 0.0) * gain
    background_per_pixel_e = sky_e + noise.read_noise_e**2 + dark_current_e_per_pixel
    variance_e = source_e + aperture_pixels * background_per_pixel_e
    if sky_pixels > 0:
        variance_e += (aperture_pixels**2 / sky_pixels) * background_per_pixel_e
    return math.sqrt(variance_e) / gain


@dataclass(frozen=True)
class ApertureMeasurement:
    """One aperture measurement of one star in one frame.

    Attributes
    ----------
    net_flux_adu : `float`
        The sky-subtracted sum of the aperture, in ADU. It can be below zero
        for a faint star in a noisy frame (the sky estimate is a little too
        high); its error says how far below zero is expected. It is not
        divided by the exposure time.
    flux_error_adu : `float`
        The 1-sigma uncertainty of `net_flux_adu`, in ADU. It is 0.0 when
        the star was too close to the frame edge to measure.
    is_saturated : `bool`
        Whether the aperture holds a significant number of saturated pixels.
    peak_adu : `float`
        The largest raw pixel value inside the aperture, in ADU (sky
        included). It is NaN when the star was too close to the frame edge to
        measure. The catalog zero point leaves out a star whose peak lies
        above the camera's photometric linearity limit.
    nearest_neighbor_distance_px : `float`
        The distance to the nearest other detected star within the search
        radius, in pixels. NaN when no detections were given or none lies
        within it.
    nearest_neighbor_flux_ratio : `float`
        That neighbor's peak height divided by the star's own peak height
        (peaks stand in for fluxes, because stars of one frame share a
        shape). NaN when `nearest_neighbor_distance_px` is NaN.
    masked_sky_fraction : `float`
        The share of the sky ring's pixels left out because a bright
        neighbor lights them. 0.0 when no detections were given.
    crowded : `bool`
        Whether a neighbor with at least `CROWDING_NEIGHBOR_PEAK_FRACTION` of
        the star's peak lies within the aperture radius plus
        `CROWDING_MARGIN_PX`. Its light may add to the star's flux.
    """

    net_flux_adu: float
    flux_error_adu: float
    is_saturated: bool
    peak_adu: float = math.nan
    nearest_neighbor_distance_px: float = math.nan
    nearest_neighbor_flux_ratio: float = math.nan
    masked_sky_fraction: float = 0.0
    crowded: bool = False


def measure_aperture_photometry(
    data: np.ndarray,
    x: float,
    y: float,
    radius: float | None = None,
    annulus_inner: float | None = None,
    annulus_outer: float | None = None,
    cutout_radius: int | None = None,
    fallback_background: float | None = None,
    *,
    saturation_threshold_adu: float,
    noise: DetectorNoise | None = None,
    dark_current_e_per_pixel: float = 0.0,
    neighbors: NeighborField | None = None,
) -> ApertureMeasurement:
    """Measure the brightness of a star inside a small circle, with its error.

    We add up all the light inside the circle, then subtract the background
    glow to get the star's true brightness. This is the one place that
    actually does the aperture math -- every other function in this file
    that needs a star's flux calls this one, so the math only lives here.

    The saturation threshold is a required keyword argument: it is a
    property of the camera, so the caller takes it from the camera's
    profile.

    The circle and the background ring are centered on ``(x, y)`` exactly,
    with the fractional part kept. Callers that want the circle on the
    star's own center pass the position from `refine_star_centroid`.

    The sky level is the median of the ring after clipping outlying pixels
    (see `clipped_median_and_sigma`). With `neighbors`, the pixels that a
    bright neighbor lights are first left out of the ring (see
    `neighbor_mask`), so a neighbor does not lift the sky and make the star
    read too faint. A neighbor inside the circle cannot be removed, so the
    star is flagged `crowded` instead.

    The uncertainty comes from `aperture_flux_error_adu`. It uses the
    aperture's true area, the number of pixels kept in the sky ring, and the
    median sky level. It does not change the flux.

    Parameters
    ----------
    data : `numpy.ndarray`
        The frame, shape ``(ny, nx)``, in ADU.
    x, y : `float`
        The aperture center, in pixels.
    radius : `float`, optional
        The aperture radius, in pixels.
    annulus_inner, annulus_outer : `float`, optional
        The inner and outer radius of the sky ring, in pixels.
    cutout_radius : `int`, optional
        The star must be at least this far from the frame edge, in pixels.
        By default one pixel more than the outer radius of the sky ring
        (rounded up), so the whole ring fits in the frame. That is 13 px on
        the reference setup (the ring's outer radius is 12 px).
    fallback_background : `float`, optional
        The sky level, in ADU per pixel, to use if the ring holds no pixels.
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated.
    noise : `DetectorNoise`, optional
        The gain and read noise. By default 1 electron per ADU and no read
        noise.
    dark_current_e_per_pixel : `float`, optional
        Dark current per pixel over the exposure, in electrons.
    neighbors : `NeighborField`, optional
        The frame's detected stars, at their positions in this frame. When
        given, the sky ring leaves out the pixels lit by bright neighbors,
        and the result says how crowded the star is. Without it, the ring is
        used whole and the crowding fields keep their defaults.

    Returns
    -------
    measurement : `ApertureMeasurement`
        The flux, its uncertainty, the saturation flag and the crowding.
    """
    if radius is None:
        radius = resolve(APERTURE_RADIUS_PX).value
    if annulus_inner is None:
        annulus_inner = resolve(SKY_ANNULUS_INNER_RADIUS_PX).value
    if annulus_outer is None:
        annulus_outer = resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value
    if cutout_radius is None:
        cutout_radius = math.ceil(annulus_outer) + 1
    height, width = data.shape
    noise = noise if noise is not None else DetectorNoise()
    # The whole-pixel position is used only for the bounds check and the
    # fallback cutout below. The apertures sit at the exact (x, y): a star
    # half a pixel off the aperture center loses 1 to 2 percent of its
    # light, and that loss changes as the field drifts.
    x_int, y_int = round(x), round(y)

    # Bounds check
    if (
        x_int - cutout_radius < 0
        or x_int + cutout_radius >= width
        or y_int - cutout_radius < 0
        or y_int + cutout_radius >= height
    ):
        return ApertureMeasurement(0.0, 0.0, False)

    aperture = CircularAperture((x, y), r=radius)
    annulus = CircularAnnulus((x, y), r_in=annulus_inner, r_out=annulus_outer)

    # "exact" is photutils' own default for aperture_photometry(): it
    # weighs each boundary pixel by how much of it actually falls inside
    # the circle, instead of an all-or-nothing pixel-center test. A
    # circle of radius 4.0 doesn't divide evenly into whole pixels, so
    # any binary mask has to make an arbitrary call on those boundary
    # pixels; "exact" is the one photutils itself recommends, and its
    # weighted sum has to be paired with the aperture's true analytic
    # area (below), not a raw pixel count, when subtracting background.
    star_flux_sum = aperture.to_mask(method="exact").get_values(data).sum()

    # Saturation is judged from actual pixel ADU values, not the
    # area-weighted fractions above, so it uses its own unweighted mask.
    star_raw_values = aperture.to_mask(method="center").get_values(data)

    # The sky ring's pixels, with where they sit in the frame, so the pixels
    # lit by neighbor stars can be left out.
    annulus_mask = annulus.to_mask(method="center")
    annulus_values = annulus_mask.get_values(data)
    peak_adu = float(np.max(star_raw_values)) if star_raw_values.size else math.nan
    sky_pixel_count = 0
    masked_sky_fraction = 0.0
    ring_selection: np.ndarray = annulus_values
    if neighbors is not None and annulus_values is not None and annulus_values.size > 0:
        ring_selection, masked_sky_fraction = _unmasked_ring_values(
            data, annulus_mask, (x, y), annulus_outer, peak_adu, neighbors
        )
    sky_statistics = (
        clipped_median_and_sigma(
            ring_selection,
            minimum_count=_SKY_MINIMUM_PIXELS,
            maximum_passes=_SKY_CLIP_MAXIMUM_PASSES,
            clip_sigma=_SKY_CLIP_SIGMA,
        )
        if ring_selection is not None
        else None
    )
    if sky_statistics is not None:
        background_level = sky_statistics[0]
        sky_pixel_count = sky_statistics[2]
    elif annulus_values is not None and annulus_values.size > 0:
        background_level = np.median(annulus_values)
        sky_pixel_count = int(annulus_values.size)
    elif fallback_background is not None:
        background_level = fallback_background
    else:
        local_cutout = data[
            y_int - cutout_radius : y_int + cutout_radius + 1,
            x_int - cutout_radius : x_int + cutout_radius + 1,
        ]
        background_level = np.median(local_cutout)

    # The measured value is kept even when it is below zero. Faint stars in a
    # noisy frame measure a little below zero about half the time, and
    # clamping them to zero would bias their mean upward and hide the frames
    # from every later step. The error below already allows for it.
    net_flux = float(star_flux_sum - aperture.area * background_level)
    saturated_fraction = compute_saturated_pixel_fraction(star_raw_values, saturation_threshold_adu)
    is_saturated = is_saturation_significant(saturated_fraction)
    flux_error = aperture_flux_error_adu(
        net_flux,
        float(background_level),
        float(aperture.area),
        float(sky_pixel_count),
        noise,
        dark_current_e_per_pixel,
    )

    distance_px, flux_ratio, crowded = (
        _crowding(neighbors, (x, y), radius, annulus_outer, peak_adu - float(background_level))
        if neighbors is not None
        else (math.nan, math.nan, False)
    )
    return ApertureMeasurement(
        net_flux,
        flux_error,
        is_saturated,
        peak_adu,
        nearest_neighbor_distance_px=distance_px,
        nearest_neighbor_flux_ratio=flux_ratio,
        masked_sky_fraction=masked_sky_fraction,
        crowded=crowded,
    )


def crowding_fields(measurement: ApertureMeasurement) -> dict[str, float | bool | None]:
    """Turn a measurement's crowding numbers into the fields of a light curve.

    Parameters
    ----------
    measurement : `ApertureMeasurement`
        A measurement made with the frame's detections.

    Returns
    -------
    fields : `dict`
        ``nearest_neighbor_distance_px``, ``nearest_neighbor_flux_ratio``,
        ``masked_sky_fraction`` and ``crowded`` for `PhotometryResult`. The
        first two are `None` when no neighbor was near. An infinite ratio
        (the star has no peak above the sky) is stored as `None` too, because
        JSON cannot hold it; ``crowded`` still tells the story.
    """
    ratio = measurement.nearest_neighbor_flux_ratio
    return {
        "nearest_neighbor_distance_px": (
            None
            if math.isnan(measurement.nearest_neighbor_distance_px)
            else measurement.nearest_neighbor_distance_px
        ),
        "nearest_neighbor_flux_ratio": ratio if math.isfinite(ratio) else None,
        "masked_sky_fraction": measurement.masked_sky_fraction,
        "crowded": measurement.crowded,
    }


def _own_peak_above_sky(neighbors: NeighborField, center: tuple[float, float], measured_peak: float) -> float:
    """Give the star's own peak above the sky, for sizing neighbors against it.

    The finder's peak for the detection at the star's position is used when
    there is one. A neighbor inside the circle can raise the largest pixel of
    the circle, so the finder's value is the steadier one.

    Parameters
    ----------
    neighbors : `NeighborField`
        The frame's detections.
    center : `tuple` [`float`, `float`]
        The star's position, 0-based ``(column, row)``.
    measured_peak : `float`
        The largest pixel of the circle above the sky, used when no detection
        sits at the star's position.

    Returns
    -------
    peak : `float`
        The star's peak above the sky, in ADU.
    """
    own = neighbors.around(center[0], center[1], resolve(STAR_RING_TARGET_MATCH_RADIUS_PX).value)
    if own.size and math.isfinite(float(neighbors.peaks[own[0]])):
        return float(neighbors.peaks[own[0]])
    return measured_peak


def _unmasked_ring_values(
    data: np.ndarray,
    annulus_mask: Any,
    center: tuple[float, float],
    annulus_outer: float,
    peak_adu: float,
    neighbors: NeighborField,
) -> tuple[np.ndarray, float]:
    """Pick the sky ring's pixels that no bright neighbor lights.

    The first sky and noise come from the clipped ring. They size the mask of
    each neighbor (see `neighbor_mask`). If the mask would leave fewer than
    `_SKY_MINIMUM_PIXELS` pixels, the whole ring is used.

    Parameters
    ----------
    data : `numpy.ndarray`
        The frame.
    annulus_mask : `photutils.aperture.ApertureMask`
        The ring's mask, from ``CircularAnnulus.to_mask``.
    center : `tuple` [`float`, `float`]
        The star's position, 0-based ``(column, row)``.
    annulus_outer : `float`
        The ring's outer radius, in pixels.
    peak_adu : `float`
        The largest raw pixel of the circle.
    neighbors : `NeighborField`
        The frame's detections.

    Returns
    -------
    values, masked_fraction : `numpy.ndarray`, `float`
        The ring pixels left after masking, and the share of the ring that
        was masked.
    """
    overlap = annulus_mask.get_overlap_slices(data.shape)
    if overlap is None or overlap[0] is None:
        return annulus_mask.get_values(data), 0.0
    large, small = overlap
    window = data[large]
    in_ring = annulus_mask.data[small] > 0
    ring_values = window[in_ring]
    first = clipped_median_and_sigma(
        ring_values,
        minimum_count=_SKY_MINIMUM_PIXELS,
        maximum_passes=_SKY_CLIP_MAXIMUM_PASSES,
        clip_sigma=_SKY_CLIP_SIGMA,
    )
    if first is None:
        return ring_values, 0.0
    sky, noise, _ = first
    margin = resolve(STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX).value
    nearby = neighbors.around(
        center[0], center[1], annulus_outer + margin, resolve(STAR_RING_TARGET_MATCH_RADIUS_PX).value
    )
    listed = [
        (
            float(neighbors.positions[index, 0]),
            float(neighbors.positions[index, 1]),
            float(neighbors.peaks[index]),
        )
        for index in nearby
    ]
    mask, _ = neighbor_mask(
        window.shape,
        center,
        listed,
        star_width_px=resolve(NEIGHBOR_PROFILE_WIDTH_PX).value,
        target_peak=_own_peak_above_sky(neighbors, center, peak_adu - sky),
        sky_noise=noise,
        minimum_signal_to_noise=resolve(STAR_RING_NEIGHBOR_MINIMUM_SIGNAL_TO_NOISE).value,
        mask_noise_multiple=resolve(STAR_RING_NEIGHBOR_MASK_NOISE_MULTIPLE).value,
        beta=MOFFAT_BETA_START,
        target_match_radius_px=resolve(STAR_RING_TARGET_MATCH_RADIUS_PX).value,
        offset=(large[1].start, large[0].start),
    )
    kept = in_ring & ~mask
    masked_fraction = 1.0 - float(kept.sum()) / float(in_ring.sum())
    if int(kept.sum()) < _SKY_MINIMUM_PIXELS:
        return ring_values, masked_fraction
    return window[kept], masked_fraction


def _crowding(
    neighbors: NeighborField,
    center: tuple[float, float],
    radius: float,
    annulus_outer: float,
    measured_peak: float,
) -> tuple[float, float, bool]:
    """Say how crowded a star is by the detections around it.

    Parameters
    ----------
    neighbors : `NeighborField`
        The frame's detections.
    center : `tuple` [`float`, `float`]
        The star's position, 0-based ``(column, row)``.
    radius : `float`
        The aperture radius, in pixels.
    annulus_outer : `float`
        The sky ring's outer radius, in pixels. The nearest neighbor is looked
        for out to this plus the neighbor search margin.
    measured_peak : `float`
        The largest pixel of the circle above the sky, used when no
        detection sits at the star's position.

    Returns
    -------
    distance_px, flux_ratio, crowded : `float`, `float`, `bool`
        The distance to the nearest neighbor and its peak over the star's,
        both NaN when none is near, and whether a neighbor of at least
        `CROWDING_NEIGHBOR_PEAK_FRACTION` of the star's peak lies within
        `radius` plus `CROWDING_MARGIN_PX`.
    """
    match_radius = resolve(STAR_RING_TARGET_MATCH_RADIUS_PX).value
    margin = resolve(STAR_RING_NEIGHBOR_SEARCH_MARGIN_PX).value
    nearby = neighbors.around(center[0], center[1], annulus_outer + margin, match_radius)
    if nearby.size == 0:
        return math.nan, math.nan, False
    own_peak = _own_peak_above_sky(neighbors, center, measured_peak)
    peaks = neighbors.peaks[nearby]
    distances = np.hypot(
        neighbors.positions[nearby, 0] - center[0], neighbors.positions[nearby, 1] - center[1]
    )
    nearest_peak = float(peaks[0])
    ratio = nearest_peak / own_peak if own_peak > 0.0 else math.inf
    within = distances <= radius + resolve(CROWDING_MARGIN_PX).value
    bright = np.isfinite(peaks) & (
        peaks >= resolve(CROWDING_NEIGHBOR_PEAK_FRACTION).value * max(own_peak, 0.0)
    )
    return float(distances[0]), float(ratio), bool(np.any(within & bright))


def _measure_aperture_flux(
    data: np.ndarray,
    x: float,
    y: float,
    radius: float | None = None,
    annulus_inner: float | None = None,
    annulus_outer: float | None = None,
    cutout_radius: int | None = None,
    fallback_background: float | None = None,
    *,
    saturation_threshold_adu: float,
) -> tuple[float, bool]:
    """Measure the brightness of a star inside a small circle.

    The flux and saturation part of `measure_aperture_photometry`, for
    callers that do not need the uncertainty. The arguments are the same.

    Returns
    -------
    result : `tuple[float, bool]`
        The total brightness, and a True/False flag if the star was
        too bright (saturated).
    """
    if radius is None:
        radius = resolve(APERTURE_RADIUS_PX).value
    if annulus_inner is None:
        annulus_inner = resolve(SKY_ANNULUS_INNER_RADIUS_PX).value
    if annulus_outer is None:
        annulus_outer = resolve(SKY_ANNULUS_OUTER_RADIUS_PX).value
    measurement = measure_aperture_photometry(
        data,
        x,
        y,
        radius,
        annulus_inner,
        annulus_outer,
        cutout_radius,
        fallback_background,
        saturation_threshold_adu=saturation_threshold_adu,
    )
    return measurement.net_flux_adu, measurement.is_saturated


@dataclass(frozen=True)
class FrameRejection:
    """A frame the worker left out of the run, and why.

    Attributes
    ----------
    reason : `str`
        A short sentence. The photometry run copies it into the
        ``capture_timestamps`` gate and the list of excluded frames.
    """

    reason: str


def _process_single_frame_worker(
    args: tuple[Any, ...],
) -> tuple[str, tuple[Any, ...] | FrameRejection | None]:
    """Analyze a single picture.

    This places a circle on every star and measures its brightness. We
    measure brightness in "light per second" so we can compare a 3-minute
    exposure fairly against a 5-minute exposure.

    The steps are:

    1. Read the capture time from ``DATE-OBS``. A picture with no usable
       time is rejected (see `read_observation_time`).
    2. For each star, start at the position the picture's own plate solution
       gives (see `FrameMapping`), then re-center it on its own centroid (see
       `refine_star_centroid`). If the centroid is refused, keep the
       solution's position.
    3. Measure each star's flux in an aperture centered on that position,
       and its 1-sigma uncertainty from the CCD equation.

    Parameters
    ----------
    args : `tuple`
        The picture's path, the reference stars as (id, x, y), the picture's
        `FrameMapping` from the reference frame, the camera's saturation
        threshold in ADU, and the camera's `DetectorNoise` (gain and read
        noise). An optional sixth item is the reference frame's
        `NeighborField`; with it, each star's sky ring leaves out the pixels
        lit by bright neighbors.

    Returns
    -------
    result : `tuple`
        The picture's path and one of:

        * A tuple ``(timestamp, fluxes, shift_x, shift_y, background,
          airmass, positions, exposure_s, mapping, peaks)``.
          ``timestamp`` is the exposure start in UTC. ``fluxes`` maps
          each star id to ``(flux in ADU per second, is_saturated, flux
          error in ADU per second)``. ``positions`` maps each star id to its
          `StarPosition`, which says where the aperture sat and whether
          the centroid was refused. ``exposure_s`` is the exposure
          time used to turn ADU into ADU per second. ``mapping`` is the
          picture's `FrameMapping`; ``shift_x`` and ``shift_y`` are the
          shift of the middle of the frame. The last item, ``peaks``, maps
          each star id to the largest raw pixel value in its aperture, in
          ADU.
        * A `FrameRejection` when the picture has no usable ``DATE-OBS``.
        * `None` when the picture could not be read or measured.
    """
    path, reference_stars_list, mapping, saturation_threshold_adu, noise, *optional = args
    reference_neighbors: NeighborField | None = optional[0] if optional else None

    try:
        # 1. Load Header & Data
        header, raw_data = read_header_and_data(path, first_hdu_only=True)
        data = collapse_to_2d(raw_data.astype(float))
        try:
            timestamp = read_observation_time(header)
        except ObservationTimeError as error:
            logger.warning("Rejecting %s: %s", path, error)
            return path, FrameRejection(str(error))
        airmass = compute_frame_airmass(header)
        exposure_s = _read_exposure_seconds(header)

        # Use sampling for median to speed up worker
        sampled_data = data[::4, ::4]  # 1/16th of pixels
        global_background = np.median(sampled_data)

        # 2. Forced Photometry. The neighbors sit where the frame's solution
        # puts them.
        frame_neighbors = (
            reference_neighbors.moved(mapping.predict_many(reference_neighbors.positions))
            if reference_neighbors is not None and len(reference_neighbors.positions)
            else None
        )
        fluxes_dict = {}
        positions_dict = {}
        peaks_dict = {}

        reference_positions = np.array([[x, y] for _, x, y in reference_stars_list], dtype=float)
        expected_positions = mapping.predict_many(reference_positions)
        for (reference_id, _, _), (expected_x, expected_y) in zip(
            reference_stars_list, expected_positions, strict=True
        ):
            if not (math.isfinite(expected_x) and math.isfinite(expected_y)):
                # The frame's solution cannot place this star. It gets no
                # measurement, the same as a star outside the frame.
                positions_dict[reference_id] = StarPosition(
                    math.nan, math.nan, "the plate solution does not place the star"
                )
                fluxes_dict[reference_id] = (0.0, False, 0.0)
                peaks_dict[reference_id] = math.nan
                continue
            # The frame's plate solution says where each star should be.
            # Each star is then re-centered on its own centroid, and keeps
            # the solution's position when that refinement is refused.
            position = refine_star_centroid(
                data,
                float(expected_x),
                float(expected_y),
                saturation_threshold_adu=saturation_threshold_adu,
            )
            positions_dict[reference_id] = position
            # Saturation is judged from raw ADU pixel values (against the
            # camera's saturation threshold) inside
            # measure_aperture_photometry, so it happens before the
            # ADU/second conversion below.
            measurement = measure_aperture_photometry(
                data,
                position.x,
                position.y,
                fallback_background=global_background,
                saturation_threshold_adu=saturation_threshold_adu,
                noise=noise,
                neighbors=frame_neighbors,
            )
            fluxes_dict[reference_id] = (
                measurement.net_flux_adu / exposure_s,
                measurement.is_saturated,
                measurement.flux_error_adu / exposure_s,
            )
            peaks_dict[reference_id] = measurement.peak_adu

        return path, (
            timestamp,
            fluxes_dict,
            mapping.shift_x,
            mapping.shift_y,
            global_background,
            airmass,
            positions_dict,
            exposure_s,
            mapping,
            peaks_dict,
        )

    except Exception:
        logger.exception("Error processing %s", path)
        return path, None
