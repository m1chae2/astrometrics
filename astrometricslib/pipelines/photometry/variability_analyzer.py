"""Differential photometry and ensemble normalization for variability.

Used for detecting variable stars in image sequences.
"""

import logging
import math
import multiprocessing
import os
import statistics
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.stats import mad_std, sigma_clip

from astrometricslib.image_processing.fits_access import collapse_to_2d
from astrometricslib.image_processing.saturation import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)
from astrometricslib.image_processing.source_detection import SourceDetector
from astrometricslib.models.quality_summary import FrameEnsembleComposition
from astrometricslib.models.stellar_source import LightCurve, StellarObject

logger = logging.getLogger(__name__)

# The maximum brightness value a pixel can record before it maxes out
# ("saturates")
# and stops measuring light accurately. Because our cameras save 16-bit images,
# the absolute maximum is 65,535. We set our threshold just below that at
# 65,000.
_SATURATION_ADU_THRESHOLD = 65000.0

# --- Choosing Reference Stars for Comparison --------------------------------
#
# To tell if a target star is actually changing brightness, we compare it
# against
# a group (or "ensemble") of other stars in the same image that we assume are
# stable.
# We choose these reference stars based on how clean and consistent their data
# is,
# rather than just picking the brightest ones. Tests on real fields (like IC
# 1805)
# show this gives us much more accurate measurements.

# The percentage of images (80% or 0.8) where a star must be clearly visible
# to be used as a reference. This allows a star to still be used even if it
# gets
# briefly covered by a cloud or hit by a cosmic ray in a few pictures.
MINIMUM_ENSEMBLE_FRAME_COVERAGE = 0.8

# The maximum percentage of times (5% or 0.05) a star is allowed to hit
# the maximum brightness limit (saturation). If a star is too bright, its data
# gets cut off at the top, making it a bad reference point. We allow a tiny 5%
# margin in case the air suddenly gets very still and clear ("good seeing")
# and makes the star appear brighter for a moment.
MAXIMUM_ENSEMBLE_SATURATED_FRACTION = 0.05

# The ideal number of reference stars to use. Adding too many faint stars
# actually makes the math worse because faint stars have a lot of background
# noise.
# A smaller group of 100 bright, clean stars works much better.
TARGET_ENSEMBLE_SIZE = 100

# The absolute minimum number of reference stars we need for the math to work.
# If we can't find 10 good stars, we will lower our strict quality standards
# until we find enough.
MINIMUM_ENSEMBLE_SIZE = 10


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
    total_weight = weights.sum()
    if total_weight <= 0:
        return None

    cutout_y_indices, cutout_x_indices = np.indices(cutout.shape)
    centroid_x = float((cutout_x_indices * weights).sum() / total_weight)
    centroid_y = float((cutout_y_indices * weights).sum() / total_weight)
    return x0 + centroid_x, y0 + centroid_y


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


def _process_single_frame_worker(args):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Analyze a single picture.

    This aligns the picture and measures the brightness of every star.
    We measure brightness in "light per second" so we can compare a
    3-minute exposure fairly against a 5-minute exposure.

    Returns
    -------
    result : `tuple`
        The results, including star brightnesses and picture details.
    """
    path, reference_stars_list, reference_top_refs_minimal = args

    try:
        # 1. Load Header & Data
        with fits.open(path) as fits_handle:
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
        height, width = data.shape
        aperture_radius = 4.0
        # Increase cutout for a proper background annulus
        cutout_radius_integer = 15

        for reference_id, reference_x, reference_y in reference_stars_list:
            target_x, target_y = reference_x + delta_x_shift, reference_y + delta_y_shift
            target_x_int, target_y_int = round(target_x), round(target_y)

            # Bounds check with larger cutout
            if (
                target_x_int - cutout_radius_integer < 0
                or target_x_int + cutout_radius_integer >= width
                or target_y_int - cutout_radius_integer < 0
                or target_y_int + cutout_radius_integer >= height
            ):
                fluxes_dict[reference_id] = (0.0, False)
                continue

            cutout = data[
                target_y_int - cutout_radius_integer : target_y_int + cutout_radius_integer + 1,
                target_x_int - cutout_radius_integer : target_x_int + cutout_radius_integer + 1,
            ]
            cutout_y, cutout_x = np.indices(cutout.shape)
            # Distance from center of cutout
            distance_squared = (cutout_x - cutout_radius_integer) ** 2 + (
                cutout_y - cutout_radius_integer
            ) ** 2

            # Star mask
            star_mask = distance_squared <= aperture_radius**2

            # Background annulus: radius 7 to 12
            annulus_inner = 7.0
            annulus_outer = 12.0
            annulus_mask = (distance_squared > annulus_inner**2) & (distance_squared <= annulus_outer**2)

            # Median background from annulus
            if np.any(annulus_mask):
                background_level = np.median(cutout[annulus_mask])
            else:
                background_level = global_background

            net_flux = np.sum(cutout[star_mask]) - (np.count_nonzero(star_mask) * background_level)
            # Saturation is judged from raw ADU pixel values (against
            # _SATURATION_ADU_THRESHOLD, itself a raw-ADU constant), so
            # it must run before the ADU/second conversion below.
            saturated_fraction = compute_saturated_pixel_fraction(
                cutout[star_mask], _SATURATION_ADU_THRESHOLD
            )
            is_saturated = is_saturation_significant(saturated_fraction)
            fluxes_dict[reference_id] = (max(0.0, net_flux) / exposure_seconds, is_saturated)

        return path, (timestamp, fluxes_dict, delta_x_shift, delta_y_shift, global_background, airmass)

    except Exception as e:
        print(f"Error processing {path}: {e}")
        return path, None


def _compute_star_coefficients_of_variation(stellar_objects: list[StellarObject]) -> list[float]:
    """Calculate how much each star's brightness jumps around.

    We use the "Coefficient of Variation" (CV), which is the standard
    deviation divided by the mean. A higher CV means the star is more variable.

    Returns
    -------
    cv_list : `list` [`float`]
        The calculated scatter for each star.
    """
    cv_list = []
    for star in stellar_objects:
        raw_fluxes = (
            star.light_curve.fluxes_detrended
            if star.light_curve.fluxes_detrended
            else star.light_curve.fluxes_normalized
        )
        fluxes = np.array(raw_fluxes)
        fluxes = fluxes[fluxes > 0]

        if len(fluxes) >= 3:
            mean_flux, std_flux = np.mean(fluxes), np.std(fluxes)
            if mean_flux > 0:
                cv = float(std_flux / mean_flux)
                star.mean_flux = float(mean_flux)
                star.coefficient_of_variation = cv
                star.variability_score = float(cv * 100.0)
                cv_list.append(cv)

        if getattr(star, "star_data", None) and isinstance(star.star_data, dict):
            star.magnitude = star.star_data.get("mag", "")

    return cv_list


# A math multiplier used to find stars that are behaving very differently
# from the rest of the group. Setting this to 7.4 flags roughly the top 3%
# of stars that vary the most.
#
# We set this slightly low (meaning it will catch a few false alarms) on
# purpose.
# It's better to flag a normal star by mistake than to accidentally ignore
# a brand-new supernova!
DEFAULT_VARIABILITY_SIGMA_THRESHOLD = 7.4


def median_light_curve_scatter_mag(stellar_objects: list[StellarObject]) -> float | None:
    """Calculate the overall "noise level" of the entire image.

    This takes the scatter (how much the light jumps around) for every single
    star,
    and finds the median (middle) value. We use the median so that a few
    highly variable stars don't skew the average for the whole group.

    This number tells us what's possible to detect. For example, if the
    overall image noise causes a star's brightness to bounce around by 0.3,
    we will never be able to confidently detect a real variability of 0.05.

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        Stars carrying light curves, normalized where available.

    Returns
    -------
    scatter_mag : `float` or `None`
        Median scatter in magnitudes, or `None` when no star has enough
        points to measure.
    """
    scatters: list[float] = []
    for star in stellar_objects:
        light_curve = getattr(star, "light_curve", None)
        if light_curve is None:
            continue
        fluxes = light_curve.fluxes_detrended or light_curve.fluxes_normalized or light_curve.fluxes or []
        usable = [float(flux) for flux in fluxes if flux and flux > 0]
        if len(usable) < 3:
            continue
        mean_flux = statistics.fmean(usable)
        if mean_flux <= 0:
            continue
        fractional_scatter = statistics.stdev(usable) / mean_flux
        # -2.5log10 of a ratio; for the small ratios this is usually
        # applied to it is near-linear, but the exact form keeps a noisy
        # field from reporting a misleadingly modest magnitude.
        scatters.append(2.5 * math.log10(1.0 + fractional_scatter))

    return round(statistics.median(scatters), 4) if scatters else None


def _adaptive_cv_cutoff(cv_list: list[float], sigma_threshold: float) -> float:
    """Calculate the noise limit for this specific group of stars.

    We find the average noise for the whole group, and add our safety
    margin (sigma). Any star bouncing around more than this limit is
    flagged as variable.

    Parameters
    ----------
    cv_list : `list` of `float`
        The noise levels for all the stars.
    sigma_threshold : `float`
        How strict we want to be (higher = fewer false alarms).

    Returns
    -------
    cutoff : `float`
        The final noise limit.
    """
    # Compute robust statistics (Median and Median Absolute Deviation) to avoid
    # outliers artificially inflating the noise floor
    median_cv = float(np.median(cv_list))
    mad_cv = float(np.median(np.abs(np.array(cv_list) - median_cv)))

    # Floor the cutoff at 2% (0.02) to prevent micro-variability
    # flagging in exceptionally clean data
    return max(0.02, median_cv + sigma_threshold * max(1e-4, mad_cv))


def _flag_variable_stars_by_adaptive_cutoff(
    stellar_objects: list[StellarObject], sigma_threshold: float
) -> list[StellarObject]:
    """Find the stars that change brightness more than the noise limit.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The stars to check.
    sigma_threshold : `float`
        How strict we want to be.

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        The stars that passed the test and look like real variable stars.
    """
    variable_candidates = []
    cv_list = _compute_star_coefficients_of_variation(stellar_objects)

    if not cv_list:
        return variable_candidates

    adaptive_cutoff = _adaptive_cv_cutoff(cv_list, sigma_threshold)

    for star in stellar_objects:
        cv = getattr(star, "coefficient_of_variation", None)
        if cv is None:
            continue

        # Flag star if scatter exceeds the population's own adaptive
        # cutoff. (adaptive_cutoff already has its own 0.02 floor, so no
        # separate flat threshold is applied here -- a flat 0.10 `or`
        # clause would cap the effective cutoff at 10% and defeat the
        # point of raising it in noisy fields/sessions.)
        if cv > adaptive_cutoff:
            variable_candidates.append(star)

    return variable_candidates


def identify_long_term_variable_candidates(
    stellar_objects: list[StellarObject],
    sigma_threshold: float = DEFAULT_VARIABILITY_SIGMA_THRESHOLD,
) -> list[StellarObject]:
    """Find stars that slowly change brightness over days, weeks, or months.

    This looks at data gathered from multiple different nights to find
    slow-changing stars that we might miss if we only looked at one night.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The stars to check.
    sigma_threshold : `float`, optional
        How strict we want to be.

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        The stars that look like long-term variables.
    """
    return _flag_variable_stars_by_adaptive_cutoff(stellar_objects, sigma_threshold)


class VariabilityAnalyzer:
    """Analyzes a sequence of images to detect variable stars."""

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config
        self.light_curves: dict[str, LightCurve] = {}
        self.stellar_objects: list[StellarObject] = []
        self.frame_reference_flux = {}
        self.timestamp_to_path = {}
        self.rejected_files = []
        self.frame_ensemble_composition: list[FrameEnsembleComposition] = []

    def load_target_images(self, target_id: str) -> list[str]:
        """Not used anymore, kept only so older code doesn't break.

        Returns
        -------
        image_paths : `list` [`str`]
            Always empty.
        """
        return []

    def process(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        image_paths: list[str],
        max_workers: int | None = None,
        id_prefix: str = "",
        seed_stars: list[StellarObject] | None = None,
    ):
        """Measure the brightness of all stars across a sequence of images.

        Parameters
        ----------
        image_paths : `list` [`str`]
            The pictures to process. The first picture is used as the map.
        max_workers : `int`, optional
            How many CPU cores to use.
        id_prefix : `str`, optional
            A label to add to star names, like "Night1_Star_5".
        seed_stars : `list` [`StellarObject`], optional
            A list of specific stars to track instead of finding them
            ourselves.
        """
        if not image_paths:
            return

        # 1. Reference Frame: Deep Detection (Sequential)
        reference_path = image_paths[0]
        logger.info(f"[1/{len(image_paths)}] Processing Reference {os.path.basename(reference_path)}...")

        with fits.open(reference_path) as fits_handle:
            reference_data = collapse_to_2d(fits_handle[0].data.astype(float))
            reference_header = fits_handle[0].header
            reference_date = reference_header.get("DATE-OBS", datetime.now().isoformat())
            try:
                reference_timestamp = datetime.fromisoformat(reference_date)
            except ValueError, TypeError:
                reference_timestamp = datetime.now()
            reference_exposure_seconds = _read_exposure_seconds(reference_header)
            # Seeded below alongside the reference frame's flux. Leaving
            # it out started every light curve with one fewer airmass
            # than flux, and since the per-frame worker appends to both
            # thereafter, airmasses[i] described the frame at fluxes[i+1]
            # for the whole run -- so airmass detrending read the wrong
            # airmass for every point.
            reference_airmass = compute_frame_airmass(reference_header)

        # Retry with different parameters if detection fails
        detector = None
        detection_configs = [
            {"fwhm": 4.0, "sigma": 3.0},  # Standard
            {"fwhm": 3.0, "sigma": 2.0},  # Sharp/Faint
            {"fwhm": 5.0, "sigma": 3.0},  # Soft/Large
            {"fwhm": 6.0, "sigma": 4.0},  # Very Out of focus
            {"fwhm": 3.0, "sigma": 1.5},  # Very desperate
        ]

        for config in detection_configs:
            logger.info(f"  Attempting detection with FWHM={config['fwhm']}, Sigma={config['sigma']}...")
            detector = SourceDetector(threshold_sigma=config["sigma"], fwhm=config["fwhm"])
            reference_stars_detected = detector.detect(reference_data)
            if reference_stars_detected and len(reference_stars_detected) > 10:
                logger.info(f"  Success! Found {len(reference_stars_detected)} stars.")
                break

        if not reference_stars_detected:
            logger.info("No stars in reference frame (after retries). Aborting.")
            return

        # SourceDetector already returns a list sorted by flux
        max_stars = 2000

        # Alignment anchors (indices 50-100 to avoid saturation) always
        # come from this blind detection pass, regardless of whether
        # seed_stars is given -- frame-to-frame alignment doesn't care
        # which stars are being tracked/reported, only that enough of
        # them exist to measure a reliable shift.
        #
        # This used to also say the seeded population is "typically far
        # smaller than 100 stars". That was only true because
        # identify_session_stars capped it at 100; it now defers to
        # Processing.Astrometry.maximum_identified_stars and a seeded
        # population can be thousands.
        reference_top_refs_minimal = []
        for star_row in reference_stars_detected[50:100]:
            x_ref = star_row.get("xcentroid", star_row.get("x_centroid"))
            y_ref = star_row.get("ycentroid", star_row.get("y_centroid"))
            flux_ref = star_row.get("flux", 0.0)
            reference_top_refs_minimal.append((x_ref, y_ref, flux_ref))

        reference_stars_minimal = []
        if seed_stars is not None:
            # identify_session_stars defers to the now-uncapped
            # Processing.Astrometry.maximum_identified_stars, so
            # seed_stars can run into the thousands -- and every one
            # accepted here re-enters the per-frame parallel worker's
            # per-star cutout loop for every frame in the session. A
            # seed star with no detectable signal above local background
            # on the reference frame (net_flux clamped to 0.0 by
            # _measure_flux_numpy) will never contribute a usable
            # measurement in any frame either, so tracking it is pure
            # cost with no photometric benefit. Excluding it here only
            # decides whether this call gives it a light curve -- it
            # stays fully present in the catalog, since astrometry
            # identification and persistence are separate and untouched.
            #
            # On 2026-08-25, NGC 6888 seeded 2,439 stars this way across
            # a 166-frame session; two photometry workers reached ~6GB
            # RSS each, exhausted an 8GB swap, and got Siril OOM-killed.
            seed_stars_without_signal = 0
            for seed_star in seed_stars:
                star_data = seed_star.star_data
                x_ref = star_data.get("xcentroid", star_data.get("x_centroid"))
                y_ref = star_data.get("ycentroid", star_data.get("y_centroid"))
                if x_ref is None or y_ref is None:
                    continue
                # Measure initial flux using the same aperture
                # photometry for consistency; the seed star already
                # carries its real id/name/ra/dec/spectral_type from
                # astrometry identification, so those are left as-is.
                # Divided by exposure time (ADU/second, not raw ADU
                # counts) for the same reason as the per-frame worker
                # below -- see _read_exposure_seconds.
                flux, is_saturated = self._measure_flux_numpy(reference_data, x_ref, y_ref)
                flux = flux / reference_exposure_seconds
                if flux <= 0:
                    seed_stars_without_signal += 1
                    continue
                seed_star.flux = flux
                seed_star.light_curve = LightCurve(
                    timestamps=[reference_timestamp],
                    fluxes=[flux],
                    is_saturated=[is_saturated],
                    airmasses=[reference_airmass],
                )
                self.stellar_objects.append(seed_star)
                reference_stars_minimal.append((seed_star.id, x_ref, y_ref))
            if seed_stars_without_signal:
                logger.info(
                    f"  {seed_stars_without_signal} of {len(seed_stars)} seed stars had no detectable "
                    "signal above background on the reference frame; excluded from per-frame tracking."
                )
        else:
            for i, star_data in enumerate(reference_stars_detected[:max_stars]):
                new_star = StellarObject(id=f"{id_prefix}Star_{i + 1}")
                new_star.star_data = star_data
                # Handle column name variations in dict
                x_ref = star_data.get("xcentroid", star_data.get("x_centroid"))
                y_ref = star_data.get("ycentroid", star_data.get("y_centroid"))
                # Measure initial flux using the same aperture photometry
                # for consistency. Divided by exposure time (ADU/second)
                # for the same reason as the per-frame worker -- see
                # _read_exposure_seconds.
                flux, is_saturated = self._measure_flux_numpy(reference_data, x_ref, y_ref)
                flux = flux / reference_exposure_seconds
                new_star.flux = flux
                new_star.light_curve = LightCurve(
                    timestamps=[reference_timestamp],
                    fluxes=[flux],
                    is_saturated=[is_saturated],
                    airmasses=[reference_airmass],
                )
                self.stellar_objects.append(new_star)
                reference_stars_minimal.append((new_star.id, x_ref, y_ref))

        logger.info(f"  Initialized {len(self.stellar_objects)} reference stars.")

        if len(image_paths) > 1:
            # Default to a limit of 75% CPU cores unless the caller
            # specifies otherwise
            max_workers = max_workers if max_workers is not None else max(1, int(os.cpu_count() * 0.75))
            logger.info(
                f"Starting parallel processing for {len(image_paths) - 1} frames "
                f"using {max_workers} workers..."
            )
            worker_arguments = [
                (path, reference_stars_minimal, reference_top_refs_minimal) for path in image_paths[1:]
            ]

            # Explicit 'fork' context: Python 3.14 changed the default
            # start method on Linux to 'forkserver', which re-imports the
            # entry-point script in a fresh interpreter to bootstrap --
            # that breaks when this module is reached via a script
            # invoked as `python /path/to/script.py` rather than
            # `python -m`, raising "attempt has been made to start a new
            # process before the current process has finished its
            # bootstrapping phase". 'fork' duplicates the already-running
            # process instead, sidestepping that re-import entirely; it's
            # safe here because none of the pool workers touch the
            # SIMBAD/Gaia locks held by other threads in the parent.
            with ProcessPoolExecutor(
                max_workers=max_workers, mp_context=multiprocessing.get_context("fork")
            ) as executor:
                results = list(executor.map(_process_single_frame_worker, worker_arguments))

            # 3. Aggregate Results back into the StellarObjects
            stellar_object_map = {star.id: star for star in self.stellar_objects}

            for result in results:
                if result is None:
                    continue
                path, data = result
                if data is None:
                    continue

                timestamp, fluxes_dict, _delta_x, _delta_y, _bg, airmass = data
                self.timestamp_to_path[timestamp] = path

                # Update each StellarObject's light curve with the new
                # data point
                for star_id, (flux, is_saturated) in fluxes_dict.items():
                    if star_id in stellar_object_map:
                        star = stellar_object_map[star_id]
                        star.light_curve.timestamps.append(timestamp)
                        star.light_curve.fluxes.append(float(flux))
                        star.light_curve.is_saturated.append(is_saturated)
                        star.light_curve.airmasses.append(float(airmass))

            logger.info("Parallel processing complete.")

    def _measure_flux_numpy(self, data, x, y, radius=4.0):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Measure the brightness of a star inside a small circle.

        We add up all the light inside the circle, then subtract the background
        glow to get the star's true brightness.

        Returns
        -------
        result : `tuple[float, bool]`
            The total brightness, and a True/False flag if the star was
            too bright (saturated).
        """
        height, width = data.shape
        x_int, y_int = round(x), round(y)
        cutout_radius = 15

        # Bounds check
        if (
            x_int - cutout_radius < 0
            or x_int + cutout_radius >= width
            or y_int - cutout_radius < 0
            or y_int + cutout_radius >= height
        ):
            return 0.0, False

        cutout = data[
            y_int - cutout_radius : y_int + cutout_radius + 1,
            x_int - cutout_radius : x_int + cutout_radius + 1,
        ]
        cutout_y, cutout_x = np.indices(cutout.shape)
        distance_squared = (cutout_x - cutout_radius) ** 2 + (cutout_y - cutout_radius) ** 2
        star_mask = distance_squared <= radius**2

        # Background annulus: radius 7 to 12
        annulus_inner = 7.0
        annulus_outer = 12.0
        annulus_mask = (distance_squared > annulus_inner**2) & (distance_squared <= annulus_outer**2)

        if np.any(annulus_mask):
            background_level = np.median(cutout[annulus_mask])
        else:
            background_level = np.median(cutout)  # Fallback

        # Sum Flux
        net_flux = np.sum(cutout[star_mask]) - (np.count_nonzero(star_mask) * background_level)
        saturated_fraction = compute_saturated_pixel_fraction(cutout[star_mask], _SATURATION_ADU_THRESHOLD)
        is_saturated = is_saturation_significant(saturated_fraction)

        return max(0.0, net_flux), is_saturated

    def normalize_light_curves(self):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Perform differential photometry using ensemble normalization.

        Identifies stable reference stars to calculate a per-frame
        normalization factor.
        """
        if not self.stellar_objects:
            return

        # 1. Find our group of stable reference stars. We want stars that are
        # visible in almost every frame, don't get too bright (saturate), and
        # are generally as bright as possible.
        total_star_count = len(self.stellar_objects)
        # Count distinct timestamps across the whole run, not the longest
        # single light curve. Stars from different sessions carry
        # different timestamp sets, so measuring coverage against one
        # star's own frame count scores a star that only ever appears in
        # half the run as fully covered -- which is what let a selected
        # ensemble still trip the frame-coverage fallback below.
        frame_count = len({
            timestamp
            for star in self.stellar_objects
            if star.light_curve
            for timestamp in star.light_curve.timestamps
        })

        candidates = []
        for star in self.stellar_objects:
            light_curve = star.light_curve
            if not light_curve or not light_curve.fluxes:
                continue
            # We bundle the timestamp, brightness, and saturation flag
            # together.
            # This makes sure we only count a star as "visible" in a frame if
            # we
            # actually have all three pieces of data for it.
            measurements = list(
                zip(
                    light_curve.timestamps,
                    light_curve.fluxes,
                    light_curve.is_saturated,
                    strict=False,
                )
            )
            if not measurements:
                continue
            usable_timestamps = {
                timestamp
                for timestamp, flux, saturated in measurements
                if flux and flux > 0 and not saturated
            }
            saturated_fraction = sum(1 for _, _, saturated in measurements if saturated) / len(measurements)
            # We calculate the star's "coverage score" by comparing the number
            # of
            # good measurements against the total number of frames in this
            # specific
            # observation session. We don't compare it against the total
            # number of
            # frames forever, because a star might only have been observed
            # tonight.
            own_frames = {timestamp for timestamp, _, _ in measurements}
            coverage = len(usable_timestamps) / len(own_frames) if own_frames else 0.0
            candidates.append((star, coverage, saturated_fraction, star.flux or 0.0, frozenset(own_frames)))

        # Group by the frame set a star belongs to. Normalization is
        # per-frame, so a frame can only be normalized by stars measured
        # in it: one pooled top-N drawn across sessions leaves whichever
        # sessions lost the brightness contest with no ensemble at all,
        # which is how 100 selected stars spanned only 29 of 61 frames.
        # Each frame set therefore gets its own ensemble.
        candidates_by_frame_set: dict = {}
        for candidate in candidates:
            candidates_by_frame_set.setdefault(candidate[4], []).append(candidate)

        def _select(minimum_coverage: float) -> list:
            """Pick the best reference stars, prioritizing the brightest ones.

            Returns
            -------
            selected : `list`
                Our chosen list of reference stars.
            """
            chosen = []
            for group in candidates_by_frame_set.values():
                eligible = [
                    candidate
                    for candidate in group
                    if candidate[1] >= minimum_coverage
                    and candidate[2] <= MAXIMUM_ENSEMBLE_SATURATED_FRACTION
                ]
                eligible.sort(key=lambda candidate: -candidate[3])
                chosen.extend(eligible[:TARGET_ENSEMBLE_SIZE])
            return chosen

        selected = _select(MINIMUM_ENSEMBLE_FRAME_COVERAGE)
        relaxed_coverage = None
        # A sparse or short run can leave too few stars measured in most
        # frames. Relaxing coverage beats proceeding with an ensemble too
        # small for a stable median, so step down rather than give up.
        for fallback_coverage in (0.5, 0.25, 0.0):
            if len(selected) >= MINIMUM_ENSEMBLE_SIZE:
                break
            relaxed_coverage = fallback_coverage
            selected = _select(fallback_coverage)

        reference_ids = {candidate[0].id for candidate in selected}
        if selected:
            faintest = min(candidate[3] for candidate in selected)
            brightest = max(candidate[3] for candidate in selected)
            logger.info(
                f"  Normalization ensemble: {len(reference_ids)} of {total_star_count} stars "
                f"selected on coverage/saturation, flux {faintest:.4g}-{brightest:.4g}"
                # "is not None", not truthiness: relaxing all the way to
                # 0.0 is the most severe step and the one most worth
                # reporting, but it is falsy, so a plain truth test
                # reported the worst case as no relaxation at all.
                + (
                    f" (coverage requirement relaxed to {relaxed_coverage:.0%})"
                    if relaxed_coverage is not None
                    else ""
                )
            )
        else:
            logger.warning(
                f"  Normalization ensemble: no star of {total_star_count} met the coverage and "
                "saturation requirements."
            )

        # 2. Collect fluxes per timestamp for a candidate ensemble. A
        # comparison star saturated in a given frame is excluded from
        # that frame's median only -- it stays eligible in frames where
        # it isn't saturated, so ensemble composition (and size) is
        # tracked per frame rather than assumed constant across the run.
        def _collect_frame_flux_data(candidate_ids: set) -> tuple[dict, dict]:
            flux_data: dict = {}
            excluded: dict = {}
            for star in self.stellar_objects:
                if star.id not in candidate_ids:
                    continue
                for timestamp, flux, is_saturated in zip(
                    star.light_curve.timestamps,
                    star.light_curve.fluxes,
                    star.light_curve.is_saturated,
                    strict=False,
                ):
                    if is_saturated:
                        excluded.setdefault(timestamp, []).append(star.id)
                        continue
                    if flux > 0:
                        flux_data.setdefault(timestamp, []).append(flux)
            return flux_data, excluded

        frame_flux_data, frame_excluded_star_ids = _collect_frame_flux_data(reference_ids)

        # Selection promises each member covers most frames, so the
        # ensemble as a whole should span nearly all of them. When it
        # does not, the two disagree about what a "frame" is -- report
        # both sides rather than only the symptom, since the fallback
        # below otherwise hides why it triggered.
        if selected and frame_count and len(frame_flux_data) < frame_count:
            coverages = sorted(candidate[1] for candidate in selected)
            logger.info(
                f"  Ensemble spans {len(frame_flux_data)} of {frame_count} frames from "
                f"{len(reference_ids)} stars; member coverage min={coverages[0]:.2f} "
                f"median={coverages[len(coverages) // 2]:.2f}."
            )

        # Fallback: If our strict selection rules resulted in a group of
        # reference
        # stars that are missing from too many frames, the math will fail
        # later.
        # Before we give up, we will lower our standards and try using almost
        # any visible star as a reference point.
        total_frame_count = len({t for star in self.stellar_objects for t in star.light_curve.timestamps})
        min_required_frames = max(1, int(total_frame_count * 0.5)) if total_frame_count else 0
        if total_frame_count and len(frame_flux_data) < min_required_frames:
            # Widen in two steps rather than straight to everything. The
            # first keeps the saturation filter, which is the criterion
            # worth defending -- a saturated star's flux does not track
            # its brightness, so admitting one to fix frame coverage
            # trades a gap for a wrong answer. Only if that still leaves
            # too few frames does the ensemble fall back to every star.
            widened_ids = {
                candidate[0].id
                for candidate in candidates
                if candidate[2] <= MAXIMUM_ENSEMBLE_SATURATED_FRACTION
            }
            logger.warning(
                f"  Normalization ensemble only covered {len(frame_flux_data)}/{total_frame_count} "
                f"frames; widening to {len(widened_ids)} unsaturated stars."
            )
            reference_ids = widened_ids
            frame_flux_data, frame_excluded_star_ids = _collect_frame_flux_data(reference_ids)

            if len(frame_flux_data) < min_required_frames:
                logger.warning(
                    f"  Still only {len(frame_flux_data)}/{total_frame_count} frames covered; "
                    "falling back to all detected stars with positive flux."
                )
                reference_ids = {s.id for s in self.stellar_objects}
                frame_flux_data, frame_excluded_star_ids = _collect_frame_flux_data(reference_ids)

        self.frame_ensemble_composition = [
            FrameEnsembleComposition(
                frame_path=self.timestamp_to_path.get(timestamp, "Unknown"),
                ensemble_size=len(fluxes),
                excluded_comparison_star_ids=frame_excluded_star_ids.get(timestamp, []),
            )
            for timestamp, fluxes in frame_flux_data.items()
        ]

        # 3. Calculate Normalization Factor per Frame (Median of Ensemble)
        raw_normalization_factors = {t: np.median(fluxes) for t, fluxes in frame_flux_data.items() if fluxes}

        # 3b. Statistical Outlier Rejection (Pass 1: More Aggressive MAD)
        if len(raw_normalization_factors) > 10:
            factors_list = sorted(raw_normalization_factors.items())
            times = [f[0] for f in factors_list]
            factors = np.array([f[1] for f in factors_list])

            # MAD-based robust clipping via astropy.stats.sigma_clip
            # (Hampel 1974 convention). The historical in-house loop
            # used z = 0.6745*(x - median)/MAD with |z| < 3.0; astropy's
            # mad_std multiplies MAD by 1.4826 = 1/0.6745, so sigma=3.0
            # with stdfunc="mad_std" applies the identical rejection
            # criterion. maxiters=3 matches the previous three-round
            # loop. The mad_std == 0 guard preserves the historical
            # behavior of skipping clipping entirely when more than
            # half the factors are identical (MAD collapses to zero).
            if mad_std(factors) == 0:
                mask = np.ones(len(factors), dtype=bool)
            else:
                clipped_factors = sigma_clip(
                    factors, sigma=3.0, maxiters=3, cenfunc="median", stdfunc="mad_std"
                )
                mask = ~clipped_factors.mask

            for i, valid in enumerate(mask):
                timestamp = times[i]
                if valid:
                    self.frame_reference_flux[timestamp] = factors[i]
                else:
                    path = self.timestamp_to_path.get(timestamp, "Unknown")
                    if path not in self.rejected_files:
                        self.rejected_files.append(path)
                        logger.info(f"  [REJECTED] Global Frame Outlier: {os.path.basename(path)}")
        else:
            self.frame_reference_flux = raw_normalization_factors

        # 4. Apply Normalization to Valid Frames Only.
        # Safety net: if ensemble normalization couldn't establish a
        # usable per-frame factor for *any* frame (frame_reference_flux
        # empty), the loop below would previously wipe every star's raw
        # timestamps/fluxes down to nothing -- silently destroying
        # measured photometry data instead of just failing to normalize
        # it. Skip the destructive rebuild in that case and leave each
        # star's raw measurements intact, so a caller/UI can still show
        # (unnormalized) photometry rather than nothing at all.
        if not self.frame_reference_flux:
            logger.warning(
                "  Ensemble normalization produced no usable per-frame factors; "
                "leaving raw (unnormalized) light curves intact."
            )
            for star in self.stellar_objects:
                star.light_curve.fluxes_normalized = list(star.light_curve.fluxes)
            return

        for star in self.stellar_objects:
            star.light_curve.fluxes_normalized = []
            new_timestamps = []
            new_fluxes = []
            # is_saturated and airmasses are per-frame too, so they have
            # to be filtered with the same mask. Rewriting only
            # timestamps/fluxes left them at their original length and
            # positionally misaligned, which is how a persisted light
            # curve ended up with 52 fluxes against 61 saturation flags
            # -- every later zip of the two then paired a flux with some
            # other frame's saturation verdict.
            #
            # That reindexing is only valid when the source array
            # already lines up 1:1 with timestamps/fluxes -- a star
            # whose is_saturated/airmasses predates a fix that made
            # these three arrays grow together (or was otherwise
            # truncated) does not have that correspondence, and
            # reindexing it by position would just produce a different,
            # still-wrong pairing rather than fix anything. Left empty
            # in that case instead: every downstream reader already
            # tolerates a missing array (strict=False zips,
            # length-checked reindexing in merge_light_curve_segments)
            # but nothing tolerates a silently mispaired one.
            new_is_saturated = []
            new_airmasses = []
            saturation_flags = star.light_curve.is_saturated or []
            airmasses = star.light_curve.airmasses or []
            saturation_flags_aligned = len(saturation_flags) == len(star.light_curve.timestamps)
            airmasses_aligned = len(airmasses) == len(star.light_curve.timestamps)

            for index, (timestamp, flux) in enumerate(
                zip(star.light_curve.timestamps, star.light_curve.fluxes, strict=False)
            ):
                if timestamp in self.frame_reference_flux:
                    norm_factor = self.frame_reference_flux[timestamp]
                    if norm_factor > 0:
                        star.light_curve.fluxes_normalized.append(flux / norm_factor)
                        new_timestamps.append(timestamp)
                        new_fluxes.append(flux)
                        if saturation_flags_aligned:
                            new_is_saturated.append(saturation_flags[index])
                        if airmasses_aligned:
                            new_airmasses.append(airmasses[index])

            # Update the original light curve data to exclude rejected frames
            star.light_curve.timestamps = new_timestamps
            star.light_curve.fluxes = new_fluxes
            star.light_curve.is_saturated = new_is_saturated if saturation_flags_aligned else []
            star.light_curve.airmasses = new_airmasses if airmasses_aligned else []

            # --- Pass 3: Star-Level Sigma Clipping ---
            if len(star.light_curve.fluxes_normalized) > 10:
                flux_values = np.array(star.light_curve.fluxes_normalized)

                # Same astropy sigma_clip/mad_std equivalence as the
                # frame-level pass above, at the historical
                # more-permissive per-star threshold |z| < 5.0, single
                # pass (maxiters=1). The mad_std > 0 guard preserves
                # the historical skip when MAD collapses to zero.
                if mad_std(flux_values) > 0:
                    clipped_fluxes = sigma_clip(
                        flux_values, sigma=5.0, maxiters=1, cenfunc="median", stdfunc="mad_std"
                    )
                    valid_mask = ~clipped_fluxes.mask

                    if not np.all(valid_mask):
                        star.light_curve.timestamps = [
                            t for i, t in enumerate(star.light_curve.timestamps) if valid_mask[i]
                        ]
                        star.light_curve.fluxes = [
                            f for i, f in enumerate(star.light_curve.fluxes) if valid_mask[i]
                        ]
                        star.light_curve.fluxes_normalized = [
                            fn for i, fn in enumerate(star.light_curve.fluxes_normalized) if valid_mask[i]
                        ]
                        # Same reason as the rejected-frame filter above:
                        # every per-frame array shares one index space.
                        star.light_curve.is_saturated = [
                            s
                            for i, s in enumerate(star.light_curve.is_saturated)
                            if i < len(valid_mask) and valid_mask[i]
                        ]
                        star.light_curve.airmasses = [
                            a
                            for i, a in enumerate(star.light_curve.airmasses)
                            if i < len(valid_mask) and valid_mask[i]
                        ]

    def identify_variable_stars(
        self, sigma_threshold: float = DEFAULT_VARIABILITY_SIGMA_THRESHOLD
    ) -> list[StellarObject]:
        """Find the stars that change brightness more than the noise limit.

        Returns
        -------
        variable_candidates : `list[StellarObject]`
            The stars that look like real variable stars.
        """
        return _flag_variable_stars_by_adaptive_cutoff(self.stellar_objects, sigma_threshold)

    def detrend_light_curves_airmass(self) -> None:
        """Remove false dimming caused by looking through Earth's atmosphere.

        As stars get lower in the sky, we look through more air (airmass),
        which makes them look dimmer. This finds that pattern and removes it.
        """
        for star in self.stellar_objects:
            if not star.light_curve or not star.light_curve.fluxes_normalized:
                continue

            fluxes_norm = np.array(star.light_curve.fluxes_normalized)
            airmasses = (
                np.array(star.light_curve.airmasses)
                if star.light_curve.airmasses and len(star.light_curve.airmasses) == len(fluxes_norm)
                else np.ones(len(fluxes_norm))
            )

            if len(fluxes_norm) >= 5 and np.std(airmasses) > 1e-4:
                try:
                    poly = np.polyfit(airmasses - 1.0, fluxes_norm, 2)
                    trend = np.polyval(poly, airmasses - 1.0)
                    mean_trend = float(np.mean(trend))
                    if mean_trend > 0:
                        fluxes_detrended = (fluxes_norm / trend) * mean_trend
                        star.light_curve.fluxes_detrended = [float(f) for f in fluxes_detrended]
                    else:
                        star.light_curve.fluxes_detrended = [float(f) for f in fluxes_norm]
                except Exception:
                    star.light_curve.fluxes_detrended = [float(f) for f in fluxes_norm]
            else:
                star.light_curve.fluxes_detrended = [float(f) for f in fluxes_norm]

    def run_bls_transit_search(self, star: StellarObject) -> Any | None:
        """Look for repeating dips in brightness caused by a planet passing by.

        Returns
        -------
        candidate : `ExoplanetTransitCandidate` or `None`
            The details of the possible planet, or None if nothing was found.
        """
        if not star.light_curve or len(star.light_curve.timestamps) < 8:
            return None

        from astropy.timeseries import BoxLeastSquares

        from astrometricslib.models.stellar_source import ExoplanetTransitCandidate

        t_sec = np.array([
            (ts - star.light_curve.timestamps[0]).total_seconds() for ts in star.light_curve.timestamps
        ])
        t_days = t_sec / 86400.0

        raw_fluxes = (
            star.light_curve.fluxes_detrended
            if star.light_curve.fluxes_detrended
            else star.light_curve.fluxes_normalized
        )
        fluxes = np.array(raw_fluxes)
        if len(fluxes) < 8 or np.mean(fluxes) <= 0:
            return None

        norm_fluxes = fluxes / np.median(fluxes)
        model = BoxLeastSquares(t_days, norm_fluxes)

        duration_days = np.linspace(0.01, 0.05, 5)
        max_p = max(0.2, float(t_days[-1] - t_days[0]) * 2.0)
        results = model.autopower(duration_days, minimum_period=0.06, maximum_period=max_p)

        best_idx = int(np.argmax(results.power))
        best_period = float(results.period[best_idx])
        best_depth = float(results.depth[best_idx])
        best_duration = float(results.duration[best_idx]) * 24.0
        best_t0 = float(results.transit_time[best_idx])

        snr = float(best_depth / max(1e-4, np.std(norm_fluxes)))

        candidate = ExoplanetTransitCandidate(
            period_days=best_period,
            transit_depth_mag=float(best_depth * 1.0857),
            transit_duration_hours=best_duration,
            epoch_t0=best_t0,
            transit_snr=snr,
        )
        star.light_curve.transit_candidate = candidate
        return candidate

    def run_lomb_scargle_periodogram(self, star: StellarObject) -> Any | None:
        """Look for regular repeating patterns in the star's brightness.

        Returns
        -------
        periodogram : `PeriodogramResult` or `None`
            The analysis results, or None if the math failed.
        """
        if not star.light_curve or len(star.light_curve.timestamps) < 5:
            return None

        from astropy.timeseries import LombScargle

        from astrometricslib.models.stellar_source import PeriodogramResult

        t_sec = np.array([
            (ts - star.light_curve.timestamps[0]).total_seconds() for ts in star.light_curve.timestamps
        ])
        t_days = t_sec / 86400.0
        raw_fluxes = (
            star.light_curve.fluxes_detrended
            if star.light_curve.fluxes_detrended
            else star.light_curve.fluxes_normalized
        )
        fluxes = np.array(raw_fluxes)

        if len(fluxes) < 5:
            return None

        frequency, power = LombScargle(t_days, fluxes).autopower()
        best_idx = int(np.argmax(power))
        best_period = float(1.0 / frequency[best_idx]) if frequency[best_idx] > 0 else 0.0
        best_power = float(power[best_idx])

        result = PeriodogramResult(
            best_period_days=best_period,
            power=best_power,
            false_alarm_probability=0.01 if best_power > 0.5 else 0.5,
        )
        star.light_curve.periodogram = result
        return result
