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

from astrometricslib.models.quality_summary import FrameEnsembleComposition
from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.shared.saturation_analysis import (
    compute_saturated_pixel_fraction,
    is_saturation_significant,
)
from astrometricslib.tasks.shared.source_detection_shared import SourceDetector

logger = logging.getLogger(__name__)

# Just under the raw 16-bit unsigned max (65535) -- same default and
# caveats as image_quality_metrics.DEFAULT_SATURATION_ADU_THRESHOLD (not
# reused directly to avoid pulling astropy/photutils-heavy imports into
# this module's worker-process path).
_SATURATION_ADU_THRESHOLD = 65000.0

# --- Normalization ensemble selection ---------------------------------------
#
# The ensemble used to be a positional slice of the flux-sorted star list,
# which silently changed meaning with the population size: at 100 stars it
# took indices 10-70 (bright, well-measured), but at 992 it took 99-300 --
# far deeper and fainter. Measured on IC 1805 and M 106 (2026-08-25), the
# larger, fainter ensemble was worse on both, significantly so on M 106
# (median per-star coefficient of variation 0.5014 vs 0.4469 with the
# smaller bright ensemble; 32 of 103 paired stars improved, sign-test
# p=0.00015). Selecting on photometric merit instead keeps the ensemble
# anchored to the stars that actually normalize well, whatever the
# population size.

# A comparison star is only useful in frames where it was measured, so
# require it to carry usable flux in most of them. 0.8 keeps stars that
# miss the occasional frame to cloud or a cosmic ray while rejecting ones
# that dropped out of half the run -- the failure mode behind the
# "ensemble only covered 17/61 frames" fallback seen on M 106.
MINIMUM_ENSEMBLE_FRAME_COVERAGE = 0.8

# Saturated stars have a flat-topped profile whose measured flux no longer
# tracks real brightness, so they poison a normalization reference. Binning
# IC 1805 by brightness showed the brightest 61 stars had *worse* median
# scatter (0.768) than the brightest 500 (0.565) -- the signature of
# saturation at the bright end. A small allowance absorbs a star that
# clips in a frame or two of unusually good seeing.
MAXIMUM_ENSEMBLE_SATURATED_FRACTION = 0.05

# Ensemble size is subject to diminishing returns: the per-frame median is
# already well determined by this many good stars, and every additional one
# is necessarily fainter and noisier than the last. The measurements above
# found 60 bright stars beat 201 fainter ones, so bias toward quality.
TARGET_ENSEMBLE_SIZE = 100

# Floor below which a merit-selected ensemble is too small to give a stable
# per-frame median, at which point the coverage requirement is relaxed
# rather than proceeding with too few comparison stars.
MINIMUM_ENSEMBLE_SIZE = 10


def _read_exposure_seconds(header: Any) -> float:
    """Read a FITS header's exposure duration, in seconds.

    Checks ``EXPTIME`` then ``EXPOSURE`` (both in common use across
    capture software), matching the fallback name/default already used
    for exposure metadata elsewhere (see
    `data_access.frame_scanning`). Guards against a missing, zero, or
    negative value, any of which would make a later flux-per-second
    division meaningless or blow up.

    Returns
    -------
    exposure_seconds : `float`
        The frame's exposure duration in seconds, or `1.0` if the
        header has no usable value (i.e. flux is left un-rescaled).
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
    """Re-locate a star's centroid near an expected position.

    Uses a small background-subtracted, flux-weighted center-of-mass
    within a local window, instead of a full-frame star detection --
    frame-to-frame drift between exposures of the same session is
    normally a few pixels at most, so a local search around each
    known reference star's expected position finds the same result
    as full-frame detection at a fraction of the cost.

    Returns
    -------
    centroid : `tuple` [`float`, `float`] or `None`
        The `(x, y)` centroid in frame pixel coordinates, or `None` if
        the search window falls outside the frame or contains no
        signal above background.
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
    """Estimate a frame's pixel shift relative to the reference frame.

    Re-locates each of the top reference stars near its expected
    position (see `locate_star_centroid`) and takes the median
    per-axis offset, which is robust to the occasional star lost to
    noise, a cosmic ray, or a near-neighbor in one window.

    Returns
    -------
    offset : `tuple` [`float`, `float`]
        The `(delta_x, delta_y)` shift, `(0.0, 0.0)` if fewer than 5
        reference stars were successfully re-located.
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
    """Extract or calculate airmass X = sec(z) from FITS header metadata.

    Returns
    -------
    airmass : `float`
        Calculated or extracted airmass value, defaulted to 1.0.
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
    """Worker function to process a single frame in parallel.

    Performs alignment and forced photometry. Measured flux is
    expressed in ADU/second (divided by the frame's own `EXPTIME`/
    `EXPOSURE`), not raw ADU counts -- otherwise a target observed
    across sessions with different exposure lengths (e.g. 180s and
    300s subs of the same object) would show a spurious step-change
    in raw flux purely from exposure, indistinguishable from real
    variability without going through ensemble normalization first.
    Normalizing at the measurement source makes flux directly
    comparable across exposure lengths even before that step.

    Returns
    -------
    result : `tuple`
        A tuple ``(path, data)`` where ``data`` is
        ``(timestamp, fluxes_dict, delta_x, delta_y, background, airmass)``,
        and each flux in ``fluxes_dict`` is in ADU/second.
    """
    path, reference_stars_list, reference_top_refs_minimal = args

    try:
        # 1. Load Header & Data
        with fits.open(path) as fits_handle:
            header = fits_handle[0].header
            data = fits_handle[0].data.astype(float)
            if data.ndim == 3:
                if data.shape[0] in [1, 3, 4]:
                    data = np.mean(data, axis=0)
                else:
                    data = np.mean(data, axis=-1)
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
    """Compute and store each star's coefficient of variation in place.

    Prefers `fluxes_detrended` over `fluxes_normalized` where available.
    Shared by both single-session (`identify_variable_stars`) and
    cross-session-merged (`identify_long_term_variable_candidates`)
    variability flagging, since the per-star CV computation itself
    doesn't depend on which population it's drawn from.

    Returns
    -------
    cv_list : `list` [`float`]
        Coefficient of variation for every star with enough valid flux
        points to compute one (mutates `mean_flux`,
        `coefficient_of_variation`, `variability_score`, and
        `magnitude` on those stars as a side effect).
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


# Multiplier applied to the population's MAD when deciding which stars
# are variable. MAD is about 0.6745 sigma for a normal distribution, so
# the previous value of 2.0 was only ~1.35 sigma -- roughly the 91st
# percentile, flagging about one star in ten before any skew. Measured on
# the 2026-08-24 catalog it flagged 630 of 3,177 stars (19.8%), and
# per-target rates ran to 32% (M 81, 45 of 142). Real variable fractions
# in an arbitrary field are 1-3%, so essentially every candidate was
# noise.
#
# 7.4 MAD is about 5 sigma and yields 3.0% on the same population, which
# is at the generous end of plausible rather than absurd. Chosen over a
# tighter cut because this stage produces *candidates* for follow-up: a
# few false positives cost a look, whereas a missed variable is never
# revisited.
DEFAULT_VARIABILITY_SIGMA_THRESHOLD = 7.4


def median_light_curve_scatter_mag(stellar_objects: list[StellarObject]) -> float | None:
    """Summarise a field's photometric precision as a magnitude scatter.

    The population median of each star's fractional flux scatter,
    converted to magnitudes. Reported as a median rather than a mean so
    a handful of genuine variables cannot stand in for the field's
    precision.

    This is the number that says whether a variability search is worth
    believing: candidates are picked out against this floor, so a field
    scattering at 0.3 mag cannot support detecting a 0.05 mag signal no
    matter where the threshold sits.

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
    """Compute a population's adaptive ensemble noise floor cutoff.

    Field scatter median + `sigma_threshold` * MAD, floored at a
    Field scatter median + `sigma_threshold` * MAD, floored at a
    minimum cutoff of 2%.

    Parameters
    ----------
    cv_list : `list` of `float`
        The list of coefficients of variation for the population.
    sigma_threshold : `float`
        The threshold multiplier for the Median Absolute Deviation (MAD).

    Returns
    -------
    cutoff : `float`
        The coefficient-of-variation threshold above which a star is
        flagged as variable.
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
    """Flag stars whose CV exceeds their population's own adaptive cutoff.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The population of stars to evaluate.
    sigma_threshold : `float`
        The threshold multiplier for the Median Absolute Deviation (MAD).

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        Stars whose coefficient of variation exceeds the adaptive
        ensemble noise floor cutoff, in `stellar_objects` order.
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
    """Identify cross-session-merged stars with variability past the floor.

    Mirrors `VariabilityAnalyzer.identify_variable_stars`'s adaptive
    median+MAD cutoff, but operates on an arbitrary population of
    already cross-session-merged `StellarObject`s (see
    `pipeline_tasks._match_and_merge_across_sessions`) rather than a
    single analyzer's own tracked stars -- computed separately from
    each session's own single-session-scoped variability flagging, so a
    star's long-term (multi-session) variability status is independent
    of whether any one session alone flagged it.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The population of stars to evaluate across multiple sessions.
    sigma_threshold : `float`, optional
        The threshold multiplier for the Median Absolute Deviation
        (MAD). Defaults to 2.0.

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        Stars whose merged-light-curve coefficient of variation exceeds
        the merged population's adaptive ensemble noise floor cutoff.
    """
    return _flag_variable_stars_by_adaptive_cutoff(stellar_objects, sigma_threshold)


class VariabilityAnalyzer:
    """Analyzes a sequence of images to detect variable stars.

    Performs alignment, forced photometry, and ensemble normalization.
    """

    def __init__(self, config=None):  # ruff: ignore[missing-type-function-argument, missing-return-type-special-method]
        self.config = config
        self.light_curves: dict[str, LightCurve] = {}
        self.stellar_objects: list[StellarObject] = []
        self.frame_reference_flux = {}
        self.timestamp_to_path = {}
        self.rejected_files = []
        self.frame_ensemble_composition: list[FrameEnsembleComposition] = []

    def load_target_images(self, target_id: str) -> list[str]:
        """Return nothing; deprecated and replaced by ImageService.

        Returns
        -------
        image_paths : `list[str]`
            Always an empty list; retained only for backward
            compatibility.
        """
        return []

    def process(  # ruff: ignore[missing-return-type-undocumented-public-function]
        self,
        image_paths: list[str],
        max_workers: int | None = None,
        id_prefix: str = "",
        seed_stars: list[StellarObject] | None = None,
    ):
        """Process a sequence of images to extract light curves.

        1. Detect stars in all images.
        2. Match stars to the reference frame (first image) by pixel
           distance.
        3. Build light curves.

        Parameters
        ----------
        image_paths : `list` [`str`]
            Paths to the frames to process; the first is treated as the
            reference frame. All frames should share consistent
            framing/rotation (e.g. one observing session) -- pixel-position
            re-centroiding against a single reference frame does not hold
            up across sessions with different dither/rotation.
        max_workers : `int`, optional
            Number of processes to use for the per-frame parallel
            photometry step. Defaults to 75% of the available CPU cores
            when not given, preserving today's behavior for callers
            that don't need to coordinate this with other concurrent
            work.
        id_prefix : `str`, optional
            Prefix applied to every generated ``Star_{i+1}`` id (default
            ``""``, preserving today's plain naming). Callers running
            multiple independent `process()` calls against the same
            target (e.g. once per observing session) must pass a distinct,
            deterministic prefix per call, since ids are otherwise only
            unique within a single `process()` call and would collide
            across calls when persisted. Ignored when `seed_stars` is
            given, since seed stars already carry their own real ids.
        seed_stars : `list` [`StellarObject`], optional
            Pre-identified stars (e.g. from
            `session_identification.identify_session_stars`) to track
            instead of blindly detecting the reference frame's own
            stars. Each seed star's `star_data["xcentroid"/"ycentroid"]`
            must already be expressed in this method's own reference
            frame's pixel space (`image_paths[0]`) -- when seeded from
            a session identification pass run against that exact same
            file, this holds with no reprojection needed. When
            `None` (default), behavior is unchanged from before this
            parameter existed: stars are detected blindly on the
            reference frame and assigned synthetic `Star_N` ids. Blind
            detection still always runs regardless, since its output is
            also used to build frame-to-frame alignment anchors
            (`reference_top_refs_minimal`) -- seeding only replaces
            which stars are *tracked and reported*, not how frames are
            aligned to each other.
        """
        if not image_paths:
            return

        # 1. Reference Frame: Deep Detection (Sequential)
        reference_path = image_paths[0]
        logger.info(f"[1/{len(image_paths)}] Processing Reference {os.path.basename(reference_path)}...")

        with fits.open(reference_path) as fits_handle:
            reference_data = fits_handle[0].data.astype(float)
            if reference_data.ndim == 3:
                if reference_data.shape[0] in [1, 3, 4]:
                    reference_data = np.mean(reference_data, axis=0)
                else:
                    reference_data = np.mean(reference_data, axis=-1)
            reference_header = fits_handle[0].header
            reference_date = reference_header.get("DATE-OBS", datetime.now().isoformat())
            try:
                reference_timestamp = datetime.fromisoformat(reference_date)
            except ValueError, TypeError:
                reference_timestamp = datetime.now()
            reference_exposure_seconds = _read_exposure_seconds(reference_header)

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
                seed_star.flux = flux
                seed_star.light_curve = LightCurve(
                    timestamps=[reference_timestamp], fluxes=[flux], is_saturated=[is_saturated]
                )
                self.stellar_objects.append(seed_star)
                reference_stars_minimal.append((seed_star.id, x_ref, y_ref))
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
                    timestamps=[reference_timestamp], fluxes=[flux], is_saturated=[is_saturated]
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
        """Perform simple circular aperture photometry using numpy.

        Calculates net flux within radius, subtracting background from
        annulus.

        Returns
        -------
        result : `tuple[float, bool]`
            A tuple ``(flux, is_saturated)`` -- ``is_saturated`` flags
            whether the star's own aperture (not the whole cutout,
            which includes background/annulus pixels where saturation
            there wouldn't taint the flux measurement the same way)
            has a significant fraction of pixels at or above the
            saturation threshold, so callers know not to trust this
            particular flux/magnitude measurement.
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

        # 1. Identify "Stable Reference Stars" on photometric merit --
        # well-covered, unsaturated, and then brightest-first -- rather
        # than by position in the flux-sorted list. See the ensemble
        # selection constants above for why the positional slice this
        # replaces degraded as the detected population grew.
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
            # Zip all three the way _collect_frame_flux_data does, so
            # coverage measures the frames that will actually receive a
            # measurement. Counting fluxes alone overstates it whenever a
            # star's timestamp list is shorter than its flux list, which
            # is how an ensemble could pass an 80% coverage filter and
            # still populate only 29 of 61 frames.
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
            coverage = len(usable_timestamps) / frame_count if frame_count else 0.0
            candidates.append((star, coverage, saturated_fraction, star.flux or 0.0))

        def _select(minimum_coverage: float) -> list:
            """Filter candidates on merit and rank them brightest first.

            Returns
            -------
            selected : `list`
                Up to `TARGET_ENSEMBLE_SIZE` candidate tuples meeting
                `minimum_coverage` and the saturation limit, brightest
                first.
            """
            eligible = [
                candidate
                for candidate in candidates
                if candidate[1] >= minimum_coverage and candidate[2] <= MAXIMUM_ENSEMBLE_SATURATED_FRACTION
            ]
            eligible.sort(key=lambda candidate: -candidate[3])
            return eligible[:TARGET_ENSEMBLE_SIZE]

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
                + (f" (coverage requirement relaxed to {relaxed_coverage:.0%})" if relaxed_coverage else "")
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

        # Fallback: if the chosen ensemble covers too few frames to be
        # useful (e.g. it landed on faint sources embedded in bright,
        # structured nebular background where local aperture photometry
        # frequently nets zero flux -- see _measure_flux_numpy's max(0.0,
        # ...) clamp), widen to every detected star with at least one
        # positive-flux measurement rather than silently producing a
        # near-empty normalization that then wipes every star's data in
        # step 4 below.
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
            new_is_saturated = []
            new_airmasses = []
            saturation_flags = star.light_curve.is_saturated or []
            airmasses = star.light_curve.airmasses or []

            for index, (timestamp, flux) in enumerate(
                zip(star.light_curve.timestamps, star.light_curve.fluxes, strict=False)
            ):
                if timestamp in self.frame_reference_flux:
                    norm_factor = self.frame_reference_flux[timestamp]
                    if norm_factor > 0:
                        star.light_curve.fluxes_normalized.append(flux / norm_factor)
                        new_timestamps.append(timestamp)
                        new_fluxes.append(flux)
                        if index < len(saturation_flags):
                            new_is_saturated.append(saturation_flags[index])
                        if index < len(airmasses):
                            new_airmasses.append(airmasses[index])

            # Update the original light curve data to exclude rejected frames
            star.light_curve.timestamps = new_timestamps
            star.light_curve.fluxes = new_fluxes
            star.light_curve.is_saturated = new_is_saturated
            star.light_curve.airmasses = new_airmasses

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
        """Identify stars with variability exceeding the adaptive floor.

        Returns
        -------
        variable_candidates : `list[StellarObject]`
            Stars whose coefficient of variation exceeds the adaptive
            ensemble noise floor cutoff, in stellar-object iteration order.
        """
        return _flag_variable_stars_by_adaptive_cutoff(self.stellar_objects, sigma_threshold)

    def detrend_light_curves_airmass(self) -> None:
        """Perform airmass systematic extinction detrending.

        Fits a 2nd-order polynomial of normalized flux vs airmass X(t)
        to remove systematic extinction slopes.
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
        """Run Box-fitting Least Squares (BLS) exoplanet transit search.

        Returns
        -------
        candidate : `ExoplanetTransitCandidate` or `None`
            BLS transit candidate object if detected; `None` otherwise.
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
        """Run Lomb-Scargle periodogram analysis to recover periodic signals.

        Returns
        -------
        periodogram : `PeriodogramResult` or `None`
            Lomb-Scargle periodogram result object if analyzed;
            `None` otherwise.
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
