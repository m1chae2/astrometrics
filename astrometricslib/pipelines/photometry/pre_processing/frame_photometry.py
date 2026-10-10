"""Per-frame photometry helpers used by `VariabilityAnalyzer`.

These are the raw, per-frame measurement steps -- reading a frame's
exposure time, airmass, and capture time from its FITS header,
re-locating a star's centroid, estimating how far a frame drifted from
the reference frame, and measuring a star's aperture flux -- with no
shared state across frames. They are module-level functions rather than
methods because `_process_single_frame_worker` is dispatched to a
`ProcessPoolExecutor` and needs to be picklable/forkable independent of
any analyzer instance.

Two rules keep the measured light curves honest:

* Aperture centers are never rounded to whole pixels. Each star is placed
  at its global-shifted reference position, then re-centered on its own
  brightness-weighted centroid (see `refine_star_centroid`). When that
  refinement is not trustworthy, the star keeps the shifted reference
  position and the frame result records why.
* A frame whose ``DATE-OBS`` is missing or unreadable is rejected with a
  reason (see `read_observation_time`). It never gets the wall-clock time.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.time import Time
from photutils.aperture import CircularAnnulus, CircularAperture
from photutils.centroids import centroid_com

from astrometricslib.drivers.fits_access import collapse_to_2d
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


def parse_observation_time(value: Any) -> datetime:
    """Turn a ``DATE-OBS`` header value into a UTC capture time.

    The value must be a full FITS date and time such as
    ``2026-05-24T04:58:30.570``. astropy's `~astropy.time.Time` reads it,
    which accepts the FITS forms that `datetime.fromisoformat` rejects
    (for example a trailing ``Z`` on older Python versions). A date with
    no time of day is refused: it would put every frame of a night at
    midnight and flatten the light curve's time axis.

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
        If the value is not a string, has no time of day, or is not a
        valid date and time.
    """
    if not isinstance(value, str) or not value.strip():
        raise ObservationTimeError(f"DATE-OBS is not a date string (got {value!r})")
    text = value.strip()
    if "T" not in text.upper():
        raise ObservationTimeError(f"DATE-OBS {text!r} has no time of day")
    for time_format in ("isot", "fits"):
        try:
            return Time(text, format=time_format, scale="utc").to_datetime()
        except ValueError:
            continue
    raise ObservationTimeError(f"DATE-OBS {text!r} is not a valid FITS date and time")


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


def _calculate_frame_offset(
    data: np.ndarray, reference_top_refs_minimal: list[tuple[float, float, float]]
) -> tuple[float, float]:
    """Figure out how much the telescope drifted between pictures.

    We look at a few bright stars and see how far they moved since the
    first picture. We take the median (middle) movement to ignore any
    weird mistakes.

    Returns
    -------
    offset : `tuple` [`float`, `float`]
        How many pixels the image shifted `(x_shift, y_shift)`.
    """
    shifts_x, shifts_y = [], []
    for reference_x, reference_y, _ in reference_top_refs_minimal:
        located = locate_star_centroid(data, reference_x, reference_y)
        if located is None:
            continue
        located_x, located_y = located
        shifts_x.append(located_x - reference_x)
        shifts_y.append(located_y - reference_y)

    if len(shifts_x) < 5:
        return 0.0, 0.0
    return float(np.median(shifts_x)), float(np.median(shifts_y))


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

    Returns
    -------
    result : `tuple[float, bool]`
        The total brightness, and a True/False flag if the star was
        too bright (saturated).
    """
    height, width = data.shape
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
        return 0.0, False

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

    if annulus_values is not None and annulus_values.size > 0:
        background_level = np.median(annulus_values)
    elif fallback_background is not None:
        background_level = fallback_background
    else:
        local_cutout = data[
            y_int - cutout_radius : y_int + cutout_radius + 1,
            x_int - cutout_radius : x_int + cutout_radius + 1,
        ]
        background_level = np.median(local_cutout)

    net_flux = star_flux_sum - aperture.area * background_level
    saturated_fraction = compute_saturated_pixel_fraction(star_raw_values, saturation_threshold_adu)
    is_saturated = is_saturation_significant(saturated_fraction)

    return max(0.0, float(net_flux)), is_saturated


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
    args: tuple[str, list[tuple[str, float, float]], list[tuple[str, float, float]], float],
) -> tuple[str, tuple[Any, ...] | FrameRejection | None]:
    """Analyze a single picture.

    This aligns the picture and measures the brightness of every star.
    We measure brightness in "light per second" so we can compare a
    3-minute exposure fairly against a 5-minute exposure.

    The steps are:

    1. Read the capture time from ``DATE-OBS``. A picture with no usable
       time is rejected (see `read_observation_time`).
    2. Estimate the picture's overall shift from the brightest reference
       stars.
    3. For each star, start at its reference position plus that shift,
       then re-center it on its own centroid (see `refine_star_centroid`).
       If the centroid is refused, keep the shifted position.
    4. Measure each star's flux in an aperture centered on that position.

    Parameters
    ----------
    args : `tuple`
        The picture's path, the reference stars as (id, x, y), the
        brightest reference stars used to line the picture up, and the
        camera's saturation threshold in ADU.

    Returns
    -------
    result : `tuple`
        The picture's path and one of:

        * A tuple ``(timestamp, fluxes, shift_x, shift_y, background,
          airmass, positions)``. ``fluxes`` maps each star id to
          ``(flux in ADU per second, is_saturated)``. ``positions`` maps
          each star id to its `StarPosition`, which says where the
          aperture sat and whether the centroid was refused.
        * A `FrameRejection` when the picture has no usable ``DATE-OBS``.
        * `None` when the picture could not be read or measured.
    """
    path, reference_stars_list, reference_top_refs_minimal, saturation_threshold_adu = args

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
        delta_x_shift, delta_y_shift = _calculate_frame_offset(data, reference_top_refs_minimal)

        # 3. Forced Photometry
        fluxes_dict = {}
        positions_dict = {}

        for reference_id, reference_x, reference_y in reference_stars_list:
            # The global shift moves every star by the same amount. Each
            # star is then re-centered on its own centroid, and keeps the
            # shifted position when that refinement is refused.
            position = refine_star_centroid(
                data,
                reference_x + delta_x_shift,
                reference_y + delta_y_shift,
                saturation_threshold_adu=saturation_threshold_adu,
            )
            positions_dict[reference_id] = position
            # Saturation is judged from raw ADU pixel values (against the
            # camera's saturation threshold) inside _measure_aperture_flux,
            # so it happens before the ADU/second conversion below.
            net_flux, is_saturated = _measure_aperture_flux(
                data,
                position.x,
                position.y,
                fallback_background=global_background,
                saturation_threshold_adu=saturation_threshold_adu,
            )
            fluxes_dict[reference_id] = (net_flux / exposure_seconds, is_saturated)

        return path, (
            timestamp,
            fluxes_dict,
            delta_x_shift,
            delta_y_shift,
            global_background,
            airmass,
            positions_dict,
        )

    except Exception:
        logger.exception("Error processing %s", path)
        return path, None
