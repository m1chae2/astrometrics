"""Per-frame photometry helpers used by `VariabilityAnalyzer`.

These are the raw, per-frame measurement steps -- reading a frame's
exposure time, airmass, and capture time from its FITS header,
re-locating a star's centroid, fitting how a frame sits against the
reference frame (shift, rotation and scale change), and measuring a
star's aperture flux -- with no shared state across frames. They are
module-level functions rather than methods because
`_process_single_frame_worker` is dispatched to a `ProcessPoolExecutor`
and needs to be picklable/forkable independent of any analyzer instance.

Three rules keep the measured light curves honest:

* Aperture centers are never rounded to whole pixels. Each star is placed
  at its reference position moved by the frame's alignment (a shift, a
  rotation and a scale change), then re-centered on its own
  brightness-weighted centroid (see `refine_star_centroid`). When that
  refinement is not trustworthy, the star keeps the aligned reference
  position and the frame result records why.
* A frame whose ``DATE-OBS`` is missing or unreadable is rejected with a
  reason (see `read_observation_time`). It never gets the wall-clock time.
* The frame's alignment is measured on stars chosen for being bright
  enough to centroid but not saturated (see `select_alignment_anchors`),
  never on noise peaks.
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

from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import DetectorNoise
from astrometricslib.pipelines.shared.quality.saturation import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)

logger = logging.getLogger(__name__)


def _read_exposure_seconds(header: Any) -> float:
    """Read how long the camera shutter was open (exposure time).

    We need this to calculate light-per-second. If a picture has no
    exposure time recorded, we assume 1 second to avoid math errors.

    Returns
    -------
    exposure_seconds : `float`
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


class ObservationTimeError(ValueError):
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


def locate_star_centroid(
    data: np.ndarray, expected_x: float, expected_y: float, search_half_width: int = 40
) -> tuple[float, float] | None:
    """Find the exact center of a star if we already know roughly where it is.

    Instead of searching the whole picture, we just look in a small box
    around where we expect the star to be. This is much faster. Pixels
    within 3 noise widths of the background are ignored, so sky noise does
    not drag the answer toward the middle of the box.

    Returns
    -------
    centroid : `tuple` [`float`, `float`] or `None`
        The exact `(x, y)` center, or None if we couldn't find the star.
    """
    height, width = data.shape
    x0 = round(expected_x) - search_half_width
    x1 = round(expected_x) + search_half_width + 1
    y0 = round(expected_y) - search_half_width
    y1 = round(expected_y) + search_half_width + 1
    if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
        return None

    cutout = data[y0:y1, x0:x1]
    background_level = np.median(cutout)
    # Noise alone puts about half the pixels above the median. Counted as
    # light, those pixels add up to more than a faint star and pull the
    # centroid toward the middle of the window, so the measured shift comes
    # out too small. Only pixels more than 3 noise widths above the median
    # count. The noise width is the median absolute deviation (MAD, a spread
    # that ignores the star) scaled to match a standard deviation. On a
    # noise-free window the MAD is zero and nothing changes.
    noise_width = 1.4826 * np.median(np.abs(cutout - background_level))
    weights = np.clip(cutout - background_level - 3.0 * noise_width, 0.0, None)
    if weights.sum() <= 0:
        return None

    centroid_x, centroid_y = centroid_com(weights)
    return x0 + float(centroid_x), y0 + float(centroid_y)


# The fewest located anchors for which a frame's alignment is fitted at all.
# With fewer, the frame is taken as not shifted rather than guessed.
MINIMUM_ANCHORS_FOR_SHIFT = 5

# The fewest located anchors for which a rotation and a scale change are
# fitted as well as the shift. A similarity transform has four numbers, so
# ten stars leave six more than the fit needs.
MINIMUM_ANCHORS_FOR_SIMILARITY = 10

# The least spread of the anchors, as a fraction of the distance from the
# middle of the frame to a corner, for which a rotation and a scale change are
# fitted. The spread is the root-mean-square distance of the anchors from
# their own mean. Anchors bunched in a small part of the field (a cluster
# core) cannot tell a rotation from a shift. A centroid error of 0.1 pixel on
# ten anchors then moves a corner by about 0.1 / (0.07 * sqrt(10)) = 0.45
# pixel, under half of the 1 pixel the `registration_drift` gate allows. A
# design estimate.
MINIMUM_ANCHOR_SPREAD_FRACTION = 0.07

# Anchors that sit farther than this many scaled median absolute deviations
# from the fitted alignment are left out of the fit, then the fit is made
# again. This is the usual cut for a mistaken star (a blend or a hot pixel).
ALIGNMENT_CLIP_SIGMAS = 3.0

# How many times the fit is made again with the clipped anchors.
ALIGNMENT_CLIP_PASSES = 3

# How many times the anchors are re-centered and the rotation and scale fitted
# again (see `calculate_frame_alignment`). The first pass starts from the
# shift alone and keeps the anchors that the shift puts within reach of the
# box. On NGC 2403 the rotation puts the corners 2.5 px from the shifted
# positions. Each pass brings more anchors within reach. Three passes were
# enough to bring in all of them there. A design estimate.
ALIGNMENT_REFINEMENT_PASSES = 3

# How far, in pixels, an anchor's centroid may sit from where the alignment so
# far puts it and still be used in the fit, when the usual reach
# (`MAX_CENTROID_SHIFT_PX`) leaves fewer than `MINIMUM_ANCHORS_FOR_SIMILARITY`
# anchors. A rotation that the shift alone has not yet described can put the
# far anchors outside the usual reach, and a wider one brings them back. It is
# only a second try, because a wide reach lets blends in: on M 81 the corner
# displacement came out 0.9 px with the usual reach and 1.3 to 1.6 px with 3
# to 4 px, where an independent fit on 800 bright stars gave about 0.6 px.
# The fit's own clipping cuts the blends that remain. A design estimate.
ALIGNMENT_ANCHOR_REACH_PX = 4.0


@dataclass(frozen=True)
class FrameAlignment:
    """How one frame sits against the reference frame.

    The model is a shift plus a rotation and a scale change about the middle
    of the frame, which is what a polar-alignment error, a lens change with
    temperature, or atmospheric refraction does to a field over a night. A
    star at ``(x, y)`` in the reference frame is expected at::

        x + shift_x + scale_change * (x - centre_x) - rotation * (y - centre_y)
        y + shift_y + rotation * (x - centre_x) + scale_change * (y - centre_y)

    Attributes
    ----------
    shift_x, shift_y : `float`
        The shift of the middle of the frame, in pixels.
    centre_x, centre_y : `float`
        The middle of the frame, in pixels.
    rotation : `float`
        The rotation, in radians. Positive turns the field from +x toward +y.
    scale_change : `float`
        The change in scale, as a fraction (0.001 is 0.1 percent larger).
    half_diagonal_px : `float`
        Distance from the middle of the frame to a corner, in pixels.
    anchor_count : `int`
        How many anchors the fit used. 0 when none were located.
    includes_similarity : `bool`
        Whether the rotation and scale were fitted. When `False` they are
        zero and the frame is only shifted.
    """

    shift_x: float
    shift_y: float
    centre_x: float = 0.0
    centre_y: float = 0.0
    rotation: float = 0.0
    scale_change: float = 0.0
    half_diagonal_px: float = 0.0
    anchor_count: int = 0
    includes_similarity: bool = False

    def predict(self, x: float, y: float) -> tuple[float, float]:
        """Say where a star of the reference frame should sit in this frame.

        Parameters
        ----------
        x, y : `float`
            The star's position in the reference frame, in pixels.

        Returns
        -------
        position : `tuple` [`float`, `float`]
            The expected ``(x, y)`` in this frame.
        """
        relative_x, relative_y = x - self.centre_x, y - self.centre_y
        return (
            x + self.shift_x + self.scale_change * relative_x - self.rotation * relative_y,
            y + self.shift_y + self.rotation * relative_x + self.scale_change * relative_y,
        )

    @property
    def corner_displacement_px(self) -> float:
        """Give how far the rotation and scale move a corner, in pixels.

        Returns
        -------
        displacement : `float`
            The distance a corner moves because of the rotation and the
            scale change alone, not the shift. It is the part of the
            alignment that a single shift cannot describe.
        """
        return math.hypot(self.rotation, self.scale_change) * self.half_diagonal_px


def fit_frame_alignment(
    anchors: Sequence[tuple[float, float]],
    located: Sequence[tuple[float, float] | None],
    image_shape: tuple[int, int],
    *,
    fit_similarity: bool = True,
) -> FrameAlignment:
    """Fit the shift, rotation and scale change of a frame from its anchors.

    The shift comes first, as the median shift of the anchors, because the
    median ignores a few wrong stars. When there are enough anchors that
    cover enough of the field, the shift, rotation and scale are then fitted
    together by least squares, leaving out the anchors that sit far from the
    fit and fitting again. The fitted result replaces the median only when
    at least `MINIMUM_ANCHORS_FOR_SIMILARITY` anchors are left.

    Parameters
    ----------
    anchors : `Sequence` [`tuple` [`float`, `float`]]
        The anchors' ``(x, y)`` in the reference frame.
    located : `Sequence` [`tuple` [`float`, `float`] or `None`]
        Where each anchor was found in this frame, or `None` when it was
        not found. Same order as ``anchors``.
    image_shape : `tuple` [`int`, `int`]
        The frame's ``(height, width)``, in pixels.
    fit_similarity : `bool`, optional
        Whether to fit the rotation and scale at all. `False` gives the
        median shift alone, which is the right answer for rough positions
        (see `calculate_frame_alignment`).

    Returns
    -------
    alignment : `FrameAlignment`
        The fit. It has no shift when fewer than `MINIMUM_ANCHORS_FOR_SHIFT`
        anchors were located.
    """
    height, width = image_shape
    centre_x, centre_y = (width - 1) / 2.0, (height - 1) / 2.0
    half_diagonal = math.hypot(centre_x, centre_y)
    pairs = [(anchor, found) for anchor, found in zip(anchors, located, strict=True) if found is not None]
    if len(pairs) < MINIMUM_ANCHORS_FOR_SHIFT:
        return FrameAlignment(0.0, 0.0, centre_x, centre_y, half_diagonal_px=half_diagonal)

    reference = np.array([anchor for anchor, _ in pairs], dtype=float)
    found_positions = np.array([found for _, found in pairs], dtype=float)
    displacement = found_positions - reference
    median_x, median_y = float(np.median(displacement[:, 0])), float(np.median(displacement[:, 1]))
    shift_only = FrameAlignment(
        median_x, median_y, centre_x, centre_y, half_diagonal_px=half_diagonal, anchor_count=len(pairs)
    )
    spread = float(np.sqrt(np.var(reference[:, 0]) + np.var(reference[:, 1])))
    if (
        not fit_similarity
        or len(pairs) < MINIMUM_ANCHORS_FOR_SIMILARITY
        or spread < MINIMUM_ANCHOR_SPREAD_FRACTION * half_diagonal
    ):
        return shift_only

    relative_x, relative_y = reference[:, 0] - centre_x, reference[:, 1] - centre_y
    keep = np.ones(len(pairs), dtype=bool)
    parameters = np.array([median_x, median_y, 0.0, 0.0])
    for _ in range(ALIGNMENT_CLIP_PASSES + 1):
        rows_x = np.column_stack([np.ones(len(pairs)), np.zeros(len(pairs)), relative_x, -relative_y])
        rows_y = np.column_stack([np.zeros(len(pairs)), np.ones(len(pairs)), relative_y, relative_x])
        design = np.vstack([rows_x[keep], rows_y[keep]])
        target = np.concatenate([displacement[keep, 0], displacement[keep, 1]])
        parameters, *_ = np.linalg.lstsq(design, target, rcond=None)
        residual = np.hypot(
            displacement[:, 0] - rows_x @ parameters, displacement[:, 1] - rows_y @ parameters
        )
        scatter = 1.4826 * float(np.median(residual[keep]))
        if scatter <= 0:
            break
        updated = residual <= ALIGNMENT_CLIP_SIGMAS * max(scatter, 1e-3)
        if updated.sum() < MINIMUM_ANCHORS_FOR_SIMILARITY:
            return shift_only
        if np.array_equal(updated, keep):
            break
        keep = updated
    return FrameAlignment(
        shift_x=float(parameters[0]),
        shift_y=float(parameters[1]),
        centre_x=centre_x,
        centre_y=centre_y,
        rotation=float(parameters[3]),
        scale_change=float(parameters[2]),
        half_diagonal_px=half_diagonal,
        anchor_count=int(keep.sum()),
        includes_similarity=True,
    )


def calculate_frame_alignment(
    data: np.ndarray,
    reference_top_refs_minimal: Sequence[tuple[float, float, float]],
    *,
    saturation_threshold_adu: float = math.inf,
) -> FrameAlignment:
    """Figure out how the frame sits against the first picture.

    We look at a few bright stars and see where they moved since the
    first picture. This takes two steps:

    1. Find each anchor in a wide box (see `locate_star_centroid`) and take
       the median shift. The wide box is easily pulled off by a neighbour or
       a galaxy, with errors of several pixels on a few stars in M 81, but
       the median ignores them.
    2. Re-center every anchor on its own centroid in a small box (see
       `refine_star_centroid`), starting where the alignment so far says it
       should be. Then fit a shift, rotation and scale change to those
       accurate positions (see `fit_frame_alignment`). The small box refuses
       a wrong star by itself, because it will not move more than
       `MAX_CENTROID_SHIFT_PX` (or `ALIGNMENT_ANCHOR_REACH_PX`, when that
       leaves too few anchors). The step is repeated
       `ALIGNMENT_REFINEMENT_PASSES` times, because a better alignment puts
       more of the anchors inside the small box. A field that turned a
       little during the night is still lined up.

    When there are too few anchors, or they are bunched together, only the
    shift is used.

    Parameters
    ----------
    data : `numpy.ndarray`
        The frame, shape ``(ny, nx)``, in ADU.
    reference_top_refs_minimal : `Sequence` [`tuple`]
        The alignment anchors as ``(x, y, flux)`` in the reference frame.
    saturation_threshold_adu : `float`, optional
        A pixel at or above this value counts as saturated, and an anchor
        with one in its box is not used for the rotation fit. Nothing is
        saturated by default.

    Returns
    -------
    alignment : `FrameAlignment`
        The fit.
    """
    anchors = [(reference_x, reference_y) for reference_x, reference_y, _ in reference_top_refs_minimal]
    located = [locate_star_centroid(data, reference_x, reference_y) for reference_x, reference_y in anchors]
    image_shape = (data.shape[0], data.shape[1])
    alignment = fit_frame_alignment(anchors, located, image_shape, fit_similarity=False)
    if alignment.anchor_count < MINIMUM_ANCHORS_FOR_SIMILARITY:
        return alignment

    for _ in range(ALIGNMENT_REFINEMENT_PASSES):
        for reach in (MAX_CENTROID_SHIFT_PX, ALIGNMENT_ANCHOR_REACH_PX):
            refined: list[tuple[float, float] | None] = []
            for reference_x, reference_y in anchors:
                expected_x, expected_y = alignment.predict(reference_x, reference_y)
                position = refine_star_centroid(
                    data,
                    expected_x,
                    expected_y,
                    saturation_threshold_adu=saturation_threshold_adu,
                    max_shift_px=reach,
                )
                refined.append((position.x, position.y) if position.is_refined else None)
            if sum(position is not None for position in refined) >= MINIMUM_ANCHORS_FOR_SIMILARITY:
                break
        fitted = fit_frame_alignment(anchors, refined, image_shape)
        if not fitted.includes_similarity:
            break
        alignment = fitted
    return alignment


# The two ways `select_alignment_anchors` can pick the stars that measure a
# frame's global shift. The analyzer records which one a session used.
ANCHOR_RULE_SIGNAL_TO_NOISE = "signal-to-noise ranked"
ANCHOR_RULE_RANK_BAND = "detections ranked 50 to 100"

# How many alignment anchors to take.
ALIGNMENT_ANCHOR_COUNT = 50

# The share of the brightest unsaturated detections that is skipped. The
# brightest stars are the ones most likely to be nearly saturated, and a
# flattened core gives a poor centroid. A design estimate.
ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION = 0.05

# The least peak signal-to-noise (the detection's peak above the sky, divided
# by the sky noise per pixel) an anchor may have. At 20 the centroid of a
# star is good to a small fraction of a pixel, and a noise peak is far below
# it. A design estimate.
ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR = 20.0

# The fewest anchors the signal-to-noise rule must find. With fewer, the
# median shift rests on too few stars, and the older rank band is used.
ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING = 10

# Half-width, in pixels, of the box around a detection searched for a
# saturated pixel.
ALIGNMENT_ANCHOR_SATURATION_BOX_HALF_WIDTH_PX = 3

# The older rule's band of detections, ranked by flux, brightest first.
ALIGNMENT_ANCHOR_FALLBACK_BAND = (50, 100)


@dataclass(frozen=True)
class AlignmentAnchors:
    """The stars chosen to measure a frame's global shift.

    Attributes
    ----------
    stars : `tuple` [`tuple` [`float`, `float`, `float`]]
        Each anchor as ``(x, y, flux)`` in the reference frame.
    rule : `str`
        Which rule chose them: `ANCHOR_RULE_SIGNAL_TO_NOISE` or
        `ANCHOR_RULE_RANK_BAND`.
    """

    stars: tuple[tuple[float, float, float], ...]
    rule: str


def estimate_pixel_noise(data: np.ndarray) -> float:
    """Estimate the sky noise per pixel of a frame, ignoring gradients.

    The function subtracts pixels three columns apart, so a smooth sky
    gradient cancels. It takes the median absolute deviation (MAD, a spread
    that ignores stars and outliers) of those differences, scales it to a
    standard deviation, and divides by the square root of 2 because a
    difference of two noisy pixels is that much noisier than one pixel. It
    reads every fourth row, which is enough for a stable estimate.

    Parameters
    ----------
    data : `numpy.ndarray`
        The frame, shape ``(ny, nx)``.

    Returns
    -------
    noise_adu : `float`
        The noise per pixel, in ADU. It is 0.0 for a frame narrower than
        four columns or one with no noise at all.
    """
    if data.shape[1] < 4:
        return 0.0
    rows = data[::4, :]
    differences = rows[:, 3:] - rows[:, :-3]
    return float(1.4826 * np.median(np.abs(differences - np.median(differences))) / math.sqrt(2.0))


def select_alignment_anchors(
    data: np.ndarray,
    detections: Sequence[Mapping[str, Any]],
    *,
    saturation_threshold_adu: float,
) -> AlignmentAnchors:
    """Choose the stars whose shift measures a frame's drift.

    The old rule took the detections ranked 50 to 100 by flux. In a sparse
    field those do not exist or are noise peaks, and a noise peak has no
    centroid to follow. The rule here picks real stars:

    1. Drop detections with a saturated pixel next to them.
    2. Rank the rest by flux, brightest first.
    3. Keep the stars whose peak is at least 20 times the sky noise (see
       `estimate_pixel_noise`).
    4. Skip the brightest 5 percent of those, which are the most likely to
       be nearly saturated, and take the next 50.
    5. If fewer than 10 stars qualify, return the detections ranked 50 to
       100 instead, as the older rule did.

    The result says which rule was used.

    Parameters
    ----------
    data : `numpy.ndarray`
        The reference frame, shape ``(ny, nx)``, in ADU.
    detections : `Sequence` [`Mapping`]
        The detected stars, brightest first, each with ``xcentroid`` (or
        ``x_centroid``), ``ycentroid`` (or ``y_centroid``), ``flux``, and
        ``peak`` (the peak above the sky, in ADU).
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated.

    Returns
    -------
    anchors : `AlignmentAnchors`
        The anchors and the rule that chose them.
    """
    height, width = data.shape
    noise = estimate_pixel_noise(data)
    box = ALIGNMENT_ANCHOR_SATURATION_BOX_HALF_WIDTH_PX
    unsaturated: list[tuple[float, float, float, float]] = []
    for detection in detections:
        x = detection.get("xcentroid", detection.get("x_centroid"))
        y = detection.get("ycentroid", detection.get("y_centroid"))
        if x is None or y is None:
            continue
        ix, iy = round(float(x)), round(float(y))
        cutout = data[
            max(0, iy - box) : min(height, iy + box + 1), max(0, ix - box) : min(width, ix + box + 1)
        ]
        if cutout.size and np.any(cutout >= saturation_threshold_adu):
            continue
        peak = detection.get("peak")
        unsaturated.append((
            float(x),
            float(y),
            float(detection.get("flux", 0.0)),
            float(peak) if peak is not None else 0.0,
        ))

    unsaturated.sort(key=lambda row: row[2], reverse=True)
    # The skip is a share of the stars that pass the signal-to-noise cut, not
    # of every detection: in a sparse field most detections are noise peaks,
    # and a share of those would skip real stars.
    bright_enough = [
        (x, y, flux)
        for x, y, flux, peak in unsaturated
        if noise > 0 and peak >= ALIGNMENT_ANCHOR_MINIMUM_PEAK_SNR * noise
    ]
    skipped = int(ALIGNMENT_ANCHOR_SKIPPED_BRIGHTEST_FRACTION * len(bright_enough))
    chosen = bright_enough[skipped : skipped + ALIGNMENT_ANCHOR_COUNT]
    if len(chosen) >= ALIGNMENT_ANCHOR_MINIMUM_QUALIFYING:
        return AlignmentAnchors(tuple(chosen), ANCHOR_RULE_SIGNAL_TO_NOISE)

    first, last = ALIGNMENT_ANCHOR_FALLBACK_BAND
    band: list[tuple[float, float, float]] = []
    for detection in detections[first:last]:
        x = detection.get("xcentroid", detection.get("x_centroid"))
        y = detection.get("ycentroid", detection.get("y_centroid"))
        if x is not None and y is not None:
            band.append((float(x), float(y), float(detection.get("flux", 0.0))))
    return AlignmentAnchors(tuple(band), ANCHOR_RULE_RANK_BAND)


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
CENTROID_BOX_HALF_WIDTH_PX = 5

# The largest move, in pixels, a re-centering may make from the shifted
# reference position. A larger move means the box found a neighbor, a hot
# pixel, or noise instead of the star, so the refinement is discarded. A
# design estimate: the global shift is good to well under a pixel on a
# clean frame, so 1.5 px leaves room for an imperfect shift but not for
# jumping to another star.
MAX_CENTROID_SHIFT_PX = 1.5

# Half-width, in pixels, of the square cutout whose median is the local sky.
CENTROID_SKY_CUTOUT_HALF_WIDTH_PX = 15

# How many times the centroid box is re-centered on its own result. One
# pass is biased when the star sits off the box center, because the box then
# cuts more of the wings on one side. Three passes remove that bias.
CENTROID_REFINEMENT_PASSES = 3


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
        rejected. ``(x, y)`` is then the reference position plus the
        frame's global shift. The analyzer counts these per star.
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
    box_half_width: int = CENTROID_BOX_HALF_WIDTH_PX,
    max_shift_px: float = MAX_CENTROID_SHIFT_PX,
) -> StarPosition:
    """Re-center one star on its brightness-weighted centroid.

    The frame's global shift only moves every star by the same amount.
    Each star's true position differs slightly from that estimate, and an
    aperture that is off-center loses part of the star's light. This
    function measures the star's own center in a small box.

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
        Starting position in pixels: the reference position plus the
        frame's global shift.
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
    height, width = data.shape
    centre_x, centre_y = x, y
    for _ in range(CENTROID_REFINEMENT_PASSES):
        ix, iy = round(centre_x), round(centre_y)
        x0, x1 = ix - box_half_width, ix + box_half_width + 1
        y0, y1 = iy - box_half_width, iy + box_half_width + 1
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
            return StarPosition(x, y, "centroid box falls outside the frame")

        box = data[y0:y1, x0:x1]
        if np.any(box >= saturation_threshold_adu):
            return StarPosition(x, y, "saturated pixel in the centroid box")

        sky_x0, sky_x1 = (
            max(0, ix - CENTROID_SKY_CUTOUT_HALF_WIDTH_PX),
            min(width, ix + CENTROID_SKY_CUTOUT_HALF_WIDTH_PX + 1),
        )
        sky_y0, sky_y1 = (
            max(0, iy - CENTROID_SKY_CUTOUT_HALF_WIDTH_PX),
            min(height, iy + CENTROID_SKY_CUTOUT_HALF_WIDTH_PX + 1),
        )
        sky_level = np.median(data[sky_y0:sky_y1, sky_x0:sky_x1])

        weights = np.clip(box - sky_level, 0.0, None)
        if not np.all(np.isfinite(weights)) or weights.sum() <= 0:
            return StarPosition(x, y, "no light above the sky in the centroid box")

        centroid_x, centroid_y = centroid_com(weights)
        centre_x, centre_y = x0 + float(centroid_x), y0 + float(centroid_y)

    if np.hypot(centre_x - x, centre_y - y) > max_shift_px:
        return StarPosition(x, y, f"centroid moved more than {max_shift_px:g} px from the shifted position")
    return StarPosition(centre_x, centre_y)


def centroid_offsets_px(
    positions: Mapping[str, StarPosition],
    reference_positions: Mapping[str, tuple[float, float]],
    shift_x: float,
    shift_y: float,
    *,
    alignment: FrameAlignment | None = None,
) -> tuple[list[float], int]:
    """Measure how far each star's own centroid sits from the aligned position.

    The frame's alignment says where each star should be. A star whose own
    centroid lands elsewhere has a per-star offset. With the shift alone
    (no ``alignment``), the offsets are close to zero on a frame that only
    translated, and grow with the star's distance from the middle of the
    anchors on a frame that also rotated or changed scale. With the fitted
    alignment, a rotation and a scale change are already taken out, so what
    is left is the centroid noise and any distortion the fit does not
    describe.

    A star whose centroid was refused sits at the aligned position by
    construction, so its offset says nothing. It is counted instead.

    Parameters
    ----------
    positions : `Mapping` [`str`, `StarPosition`]
        Where each star was measured in one frame.
    reference_positions : `Mapping` [`str`, `tuple` [`float`, `float`]]
        Each star's ``(x, y)`` in the reference frame.
    shift_x, shift_y : `float`
        The frame's global shift, in pixels. Used when ``alignment`` is not
        given.
    alignment : `FrameAlignment`, optional
        The frame's fitted alignment. It replaces the shift.

    Returns
    -------
    offsets_px : `list` [`float`]
        The offset of each star with a refined centroid, in pixels.
    fallback_count : `int`
        How many stars kept the aligned position.
    """
    if alignment is None:
        alignment = FrameAlignment(shift_x, shift_y)
    offsets: list[float] = []
    fallback_count = 0
    for star_id, position in positions.items():
        reference = reference_positions.get(star_id)
        if reference is None:
            continue
        if not position.is_refined:
            fallback_count += 1
            continue
        expected_x, expected_y = alignment.predict(reference[0], reference[1])
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
        How many of those measurements kept the shifted reference position
        because the star's own centroid was refused.
    median_offset_px : `float` or `None`
        The median per-star offset, in pixels, over the measurements with a
        refined centroid. `None` when there were none.
    p95_offset_px : `float` or `None`
        The 95th percentile of the same offsets, in pixels. It is large when
        the field rotated or changed scale between frames.
    worst_frame : `datetime.datetime` or `None`
        The capture time of the frame with the largest per-frame 95th
        percentile.
    worst_frame_p95_offset_px : `float` or `None`
        That frame's 95th percentile, in pixels.
    anchor_rule : `str` or `None`
        Which rule chose the alignment anchors (see
        `select_alignment_anchors`).
    anchor_count : `int`
        How many anchors the session used.
    corner_displacement_p95_px : `float` or `None`
        The 95th percentile, over the frames, of how far the fitted
        rotation and scale change move a corner of the frame (see
        `FrameAlignment.corner_displacement_px`). It is what a single shift
        cannot describe. `None` when no frame had a rotation and scale
        fitted.
    worst_corner_frame : `datetime.datetime` or `None`
        The capture time of the frame with the largest corner displacement.
    worst_corner_displacement_px : `float` or `None`
        That frame's corner displacement, in pixels.
    fitted_frame_count : `int`
        How many frames had a rotation and scale fitted.
    """

    frame_count: int
    measurement_count: int
    fallback_count: int
    median_offset_px: float | None
    p95_offset_px: float | None
    worst_frame: datetime | None
    worst_frame_p95_offset_px: float | None
    anchor_rule: str | None = None
    anchor_count: int = 0
    corner_displacement_p95_px: float | None = None
    worst_corner_frame: datetime | None = None
    worst_corner_displacement_px: float | None = None
    fitted_frame_count: int = 0

    @property
    def fallback_fraction(self) -> float:
        """Give the share of measurements that kept the shifted position.

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
    anchor_rule: str | None = None,
    anchor_count: int = 0,
    alignments_by_frame: Mapping[datetime, FrameAlignment] | None = None,
) -> CentroidShiftSummary | None:
    """Summarize a session's per-star centroid offsets and frame alignments.

    Parameters
    ----------
    offsets_by_frame : `Mapping` [`datetime.datetime`, `Sequence` [`float`]]
        For each frame, the per-star offsets in pixels (see
        `centroid_offsets_px`).
    measurement_count : `int`
        Star measurements in all the frames, refined or not.
    fallback_count : `int`
        How many of them kept the shifted position.
    anchor_rule : `str`, optional
        The rule that chose the alignment anchors.
    anchor_count : `int`, optional
        The number of anchors.
    alignments_by_frame : `Mapping`, optional
        Each frame's `FrameAlignment`, keyed by capture time.

    Returns
    -------
    summary : `CentroidShiftSummary` or `None`
        The summary, or `None` when nothing was measured (a one-frame
        session).
    """
    if measurement_count <= 0:
        return None
    corner_displacements = {
        timestamp: alignment.corner_displacement_px
        for timestamp, alignment in (alignments_by_frame or {}).items()
        if alignment.includes_similarity
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
        anchor_rule=anchor_rule,
        anchor_count=anchor_count,
        corner_displacement_p95_px=(
            float(np.percentile(list(corner_displacements.values()), 95)) if corner_displacements else None
        ),
        worst_corner_frame=worst_corner_frame,
        worst_corner_displacement_px=(
            corner_displacements[worst_corner_frame] if worst_corner_frame is not None else None
        ),
        fitted_frame_count=len(corner_displacements),
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
        The sky-subtracted sum of the aperture, in ADU. It is never
        negative and is not divided by the exposure time.
    flux_error_adu : `float`
        The 1-sigma uncertainty of `net_flux_adu`, in ADU. It is 0.0 when
        the star was too close to the frame edge to measure.
    is_saturated : `bool`
        Whether the aperture holds a significant number of saturated pixels.
    """

    net_flux_adu: float
    flux_error_adu: float
    is_saturated: bool


def measure_aperture_photometry(
    data: np.ndarray,
    x: float,
    y: float,
    radius: float = 4.0,
    annulus_inner: float = 7.0,
    annulus_outer: float = 12.0,
    cutout_radius: int = 15,
    fallback_background: float | None = None,
    *,
    saturation_threshold_adu: float,
    noise: DetectorNoise | None = None,
    dark_current_e_per_pixel: float = 0.0,
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

    The uncertainty comes from `aperture_flux_error_adu`. It uses the
    aperture's true area, the number of pixels in the sky ring, and the
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
    fallback_background : `float`, optional
        The sky level, in ADU per pixel, to use if the ring holds no pixels.
    saturation_threshold_adu : `float`
        A pixel at or above this value counts as saturated.
    noise : `DetectorNoise`, optional
        The gain and read noise. By default 1 electron per ADU and no read
        noise.
    dark_current_e_per_pixel : `float`, optional
        Dark current per pixel over the exposure, in electrons.

    Returns
    -------
    measurement : `ApertureMeasurement`
        The flux, its uncertainty and the saturation flag.
    """
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

    # The background level is a median, which (per photutils' own
    # ApertureStats) comes out the same regardless of which masking
    # method is used, so the plain unweighted mask is enough here.
    annulus_values = annulus.to_mask(method="center").get_values(data)

    sky_pixel_count = 0
    if annulus_values is not None and annulus_values.size > 0:
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

    net_flux = max(0.0, float(star_flux_sum - aperture.area * background_level))
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

    return ApertureMeasurement(net_flux, flux_error, is_saturated)


def _measure_aperture_flux(
    data: np.ndarray,
    x: float,
    y: float,
    radius: float = 4.0,
    annulus_inner: float = 7.0,
    annulus_outer: float = 12.0,
    cutout_radius: int = 15,
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
    args: tuple[str, list[tuple[str, float, float]], list[tuple[str, float, float]], float, DetectorNoise],
) -> tuple[str, tuple[Any, ...] | FrameRejection | None]:
    """Analyze a single picture.

    This aligns the picture and measures the brightness of every star.
    We measure brightness in "light per second" so we can compare a
    3-minute exposure fairly against a 5-minute exposure.

    The steps are:

    1. Read the capture time from ``DATE-OBS``. A picture with no usable
       time is rejected (see `read_observation_time`).
    2. Fit the picture's shift, rotation and scale change from the
       reference stars chosen as anchors (see `calculate_frame_alignment`).
    3. For each star, start at its reference position moved by that
       alignment, then re-center it on its own centroid (see
       `refine_star_centroid`). If the centroid is refused, keep the
       aligned position.
    4. Measure each star's flux in an aperture centered on that position,
       and its 1-sigma uncertainty from the CCD equation.

    Parameters
    ----------
    args : `tuple`
        The picture's path, the reference stars as (id, x, y), the
        brightest reference stars used to line the picture up, the
        camera's saturation threshold in ADU, and the camera's
        `DetectorNoise` (gain and read noise).

    Returns
    -------
    result : `tuple`
        The picture's path and one of:

        * A tuple ``(timestamp, fluxes, shift_x, shift_y, background,
          airmass, positions, exposure_seconds, alignment)``.
          ``timestamp`` is the exposure start in UTC. ``fluxes`` maps
          each star id to ``(flux in ADU per second, is_saturated, flux
          error in ADU per second)``. ``positions`` maps each star id to its
          `StarPosition`, which says where the aperture sat and whether
          the centroid was refused. ``exposure_seconds`` is the exposure
          time used to turn ADU into ADU per second. ``alignment`` is the
          picture's `FrameAlignment`; ``shift_x`` and ``shift_y`` are its
          shift of the middle of the frame.
        * A `FrameRejection` when the picture has no usable ``DATE-OBS``.
        * `None` when the picture could not be read or measured.
    """
    path, reference_stars_list, reference_top_refs_minimal, saturation_threshold_adu, noise = args

    try:
        # 1. Load Header & Data
        with fits.open(path, memmap=False) as fits_handle:
            header = fits_handle[0].header
            data = collapse_to_2d(fits_handle[0].data.astype(float))
            try:
                timestamp = read_observation_time(header)
            except ObservationTimeError as error:
                logger.warning("Rejecting %s: %s", path, error)
                return path, FrameRejection(str(error))
            airmass = compute_frame_airmass(header)
            exposure_seconds = _read_exposure_seconds(header)

        # Use sampling for median to speed up worker
        sampled_data = data[::4, ::4]  # 1/16th of pixels
        global_background = np.median(sampled_data)

        # 2. Alignment: re-locate the known reference stars locally
        # rather than re-running full-frame detection every frame.
        alignment = calculate_frame_alignment(
            data, reference_top_refs_minimal, saturation_threshold_adu=saturation_threshold_adu
        )

        # 3. Forced Photometry
        fluxes_dict = {}
        positions_dict = {}

        for reference_id, reference_x, reference_y in reference_stars_list:
            # The frame's alignment (shift, rotation and scale change) says
            # where each star should be. Each star is then re-centered on
            # its own centroid, and keeps the aligned position when that
            # refinement is refused.
            expected_x, expected_y = alignment.predict(reference_x, reference_y)
            position = refine_star_centroid(
                data,
                expected_x,
                expected_y,
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
            )
            fluxes_dict[reference_id] = (
                measurement.net_flux_adu / exposure_seconds,
                measurement.is_saturated,
                measurement.flux_error_adu / exposure_seconds,
            )

        return path, (
            timestamp,
            fluxes_dict,
            alignment.shift_x,
            alignment.shift_y,
            global_background,
            airmass,
            positions_dict,
            exposure_seconds,
            alignment,
        )

    except Exception:
        logger.exception("Error processing %s", path)
        return path, None
