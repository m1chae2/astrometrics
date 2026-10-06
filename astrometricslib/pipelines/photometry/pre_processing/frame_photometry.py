"""Per-frame photometry helpers used by `VariabilityAnalyzer`.

These are the raw, per-frame measurement steps -- reading a frame's
exposure time and airmass from its FITS header, re-locating a star's
centroid, estimating how far a frame drifted from the reference frame,
and measuring a star's aperture flux -- with no shared state across
frames. They are module-level functions rather than methods because
`_process_single_frame_worker` is dispatched to a `ProcessPoolExecutor`
and needs to be picklable/forkable independent of any analyzer instance.
"""

import logging
from datetime import datetime
from typing import Any

import numpy as np
from astropy.io import fits
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


def locate_star_centroid(
    data: np.ndarray, expected_x: float, expected_y: float, search_half_width: int = 40
) -> tuple[float, float] | None:
    """Find the exact center of a star if we already know roughly where it is.

    Instead of searching the whole picture, we just look in a small box
    around where we expect the star to be. This is much faster.

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
    weights = np.clip(cutout - background_level, 0.0, None)
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

    Returns
    -------
    result : `tuple[float, bool]`
        The total brightness, and a True/False flag if the star was
        too bright (saturated).
    """
    height, width = data.shape
    x_int, y_int = round(x), round(y)

    # Bounds check
    if (
        x_int - cutout_radius < 0
        or x_int + cutout_radius >= width
        or y_int - cutout_radius < 0
        or y_int + cutout_radius >= height
    ):
        return 0.0, False

    aperture = CircularAperture((x_int, y_int), r=radius)
    annulus = CircularAnnulus((x_int, y_int), r_in=annulus_inner, r_out=annulus_outer)

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


def _process_single_frame_worker(
    args: tuple[str, list[tuple[str, float, float]], list[tuple[str, float, float]], float],
) -> tuple[str, tuple[Any, ...] | None]:
    """Analyze a single picture.

    This aligns the picture and measures the brightness of every star.
    We measure brightness in "light per second" so we can compare a
    3-minute exposure fairly against a 5-minute exposure.

    Parameters
    ----------
    args : `tuple`
        The picture's path, the reference stars as (id, x, y), the
        brightest reference stars used to line the picture up, and the
        camera's saturation threshold in ADU.

    Returns
    -------
    result : `tuple`
        The results, including star brightnesses and picture details.
    """
    path, reference_stars_list, reference_top_refs_minimal, saturation_threshold_adu = args

    try:
        # 1. Load Header & Data
        with fits.open(path, memmap=False) as fits_handle:
            header = fits_handle[0].header
            data = collapse_to_2d(fits_handle[0].data.astype(float))
            date_observed = header.get("DATE-OBS", datetime.now().isoformat())
            try:
                timestamp = datetime.fromisoformat(date_observed)
            except ValueError, TypeError:
                timestamp = datetime.now()
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

        for reference_id, reference_x, reference_y in reference_stars_list:
            target_x, target_y = reference_x + delta_x_shift, reference_y + delta_y_shift
            # Saturation is judged from raw ADU pixel values (against the
            # camera's saturation threshold) inside _measure_aperture_flux,
            # so it happens before the ADU/second conversion below.
            net_flux, is_saturated = _measure_aperture_flux(
                data,
                target_x,
                target_y,
                fallback_background=global_background,
                saturation_threshold_adu=saturation_threshold_adu,
            )
            fluxes_dict[reference_id] = (net_flux / exposure_seconds, is_saturated)

        return path, (timestamp, fluxes_dict, delta_x_shift, delta_y_shift, global_background, airmass)

    except Exception:
        logger.exception("Error processing %s", path)
        return path, None
