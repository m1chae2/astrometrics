"""Differential photometry and ensemble normalization for variability.

Used for detecting variable stars in image sequences.

Every light curve carries its measurement uncertainties next to its values:

* ``flux_errors`` (ADU per second) go with ``fluxes``. They come from the
  CCD equation in `frame_photometry.aperture_flux_error_adu`.
* ``fluxes_normalized_errors`` go with ``fluxes_normalized``. Normalizing
  divides a star's flux ``F`` by the frame's comparison-ensemble level ``N``.
  The level is the inverse-variance weighted mean of the fixed comparison
  set's fluxes, each divided by its own session mean (see
  `comparison_ensemble`). The error of the ratio adds the star's own error
  ``sigma_F`` and the ensemble's error ``sigma_N`` in quadrature:
  ``sigma = sqrt((sigma_F / N)**2 + (F * sigma_N / N**2)**2)``. The ensemble
  error is ``1 / sqrt(sum of weights)`` with weights ``1 / sigma_i**2``. A
  comparison star is divided by the ensemble of the other members, so its own
  noise is not in its divisor.
* ``fluxes_detrended_errors`` go with ``fluxes_detrended``. Detrending leaves
  the normalized values and errors as they are, unless the option for an
  ensemble-derived airmass correction is on. The target's own flux is never
  fitted against airmass.

Each frame's time is recorded twice: ``timestamps`` keeps the exposure start
in UTC, and ``time_bjd_tdb`` holds the mid-exposure BJD_TDB in days (see
`observation_times`). The period searches use ``time_bjd_tdb`` when a star
has it.
"""

import logging
import math
import multiprocessing
import os
import statistics
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

import numpy as np
from astropy.io import fits
from astropy.stats import mad_std, sigma_clip
from scipy.stats import median_abs_deviation

from astrometricslib.drivers.camera_profile_store import resolve_camera_profile
from astrometricslib.drivers.fits_access import collapse_to_2d
from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.models.photometry_quality import InputQualityAssessment
from astrometricslib.models.quality_summary import ExcludedFrame, FrameEnsembleComposition
from astrometricslib.models.stellar_source import (
    MINIMUM_POINTS_FOR_PERIOD_SEARCH,
    MINIMUM_POINTS_FOR_TRANSIT_SEARCH,
    PhotometryResult,
    StellarObject,
)
from astrometricslib.pipelines.astrometry.pre_processing.source_detection import SourceDetector
from astrometricslib.pipelines.photometry.post_processing.assess_output_quality import (
    assess_output_quality,
)
from astrometricslib.pipelines.photometry.pre_processing.assess_input_quality import (
    assess_input_quality,
)
from astrometricslib.pipelines.photometry.pre_processing.detector_noise import (
    DetectorNoise,
    resolve_detector_noise,
)
from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    CentroidShiftSummary,
    FrameRejection,
    ObservationTimeError,
    _measure_aperture_flux,
    _process_single_frame_worker,
    _read_exposure_seconds,
    centroid_offsets_px,
    compute_frame_airmass,
    measure_aperture_photometry,
    read_observation_time,
    select_alignment_anchors,
    summarize_centroid_shifts,
)
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    barycentric_julian_dates,
    time_basis_for,
)
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    DEFAULT_MAXIMUM_COMPARISON_STARS,
    MINIMUM_COMPARISON_STARS,
    ComparisonSetResult,
    build_ensemble_signal,
    build_flux_table,
    fractional_to_magnitudes,
    select_comparison_set,
    set_result,
)
from astrometricslib.pipelines.photometry.processing.variability_indices import (
    FieldAssessment,
    NoiseModel,
    VariabilityThresholds,
    assess_variability,
)
from astrometricslib.utilities.exceptions import DATA_ERRORS

logger = logging.getLogger(__name__)

# --- Choosing Reference Stars for Comparison --------------------------------
#
# The comparison stars are chosen once per session, from stars that are
# measured, unsaturated and positive in every usable frame, that no catalog
# lists as variable, and that pass a constancy check. See
# `comparison_ensemble` for the rules, their limits and the constants
# (`MINIMUM_COMPARISON_STARS`, `DEFAULT_MAXIMUM_COMPARISON_STARS`).

# The fewest frames a session needs before we'll try to reject a bad one
# (a cloud, a tracking loss) as a statistical outlier. Median/MAD need a
# real population to call anything an "outlier" against; below this we'd
# risk rejecting a frame just because a short run has little spread to
# begin with. This used to be a flat >10, which let two real 10-frame
# sessions (a night's worth of Algol and M 1 data, both hit by tracking
# loss partway through) through with zero rejection at all -- their own
# per-frame registration data showed centroid drift of 30-140+ px and the
# detected star count collapsing to near zero, exactly what this check
# exists to catch.
MINIMUM_FRAMES_FOR_OUTLIER_REJECTION = 5

# A run of this many consecutive points on the same side of the median, each
# beyond the clipping limit, is treated as a real change and kept. A single
# point is treated as a glitch (a cosmic ray, a bad centroid) and clipped.
MINIMUM_RUN_LENGTH_KEPT_AS_REAL_CHANGE = 2


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
            star.photometry.fluxes_detrended
            if star.photometry.fluxes_detrended
            else star.photometry.fluxes_normalized
        )
        fluxes = np.array(raw_fluxes)
        fluxes = fluxes[fluxes > 0]

        if len(fluxes) >= 3:
            mean_flux, std_flux = np.mean(fluxes), np.std(fluxes)
            if mean_flux > 0:
                cv = float(std_flux / mean_flux)
                star.photometry.mean_flux = float(mean_flux)
                star.photometry.coefficient_of_variation = cv
                cv_list.append(cv)

    # `star.magnitude` is deliberately left alone. The "mag" the star finder
    # keeps in `star_data` is an instrument magnitude (-2.5 log10 of the
    # counts), not a catalog one. An earlier version copied it over
    # `star.magnitude`, which turned catalog values into numbers such as -14.5
    # (BD+33 3249, catalog V of about 9) and replaced them with `None` for
    # stars that had no "mag" at all.
    return cv_list


# The multiplier in the CV cutoff, median + 7.4 x MAD of the field's
# scatter. The cutoff now picks candidates only in a field too small for a
# noise model (see `variability_indices`); it still sets each star's
# `output_quality` and the `detectable_amplitude` gate's fallback. It was
# chosen to flag "roughly the top 3%" of stars, and
# set slightly low on purpose so a new supernova is not missed. Measured on
# the library on 2026-10-09 (`scripts/measure_variability_cutoff.py`): it
# flags 5.2% of stars, 3.8% of the stars the catalogs list as variable and
# 5.2% of the stars they do not list, and the scatter separates the two no
# better than chance (AUC 0.46 between stars of similar brightness). The
# multiplier is therefore not what limits the search: the photometry's own
# scatter (median 0.29 mag across targets) hides most variables. See the
# `variability_discrimination` and `detectable_amplitude` gates.
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
        light_curve = getattr(star, "photometry", None)
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


# Converts a fractional flux error to magnitudes: 2.5 / ln(10) = 1.0857.
MAGNITUDES_PER_FRACTIONAL_FLUX_ERROR = 2.5 / math.log(10.0)


def median_flux_error_mag(stellar_objects: list[StellarObject]) -> float | None:
    """Find the typical per-point measurement error of a run, in magnitudes.

    For every point of every light curve that has normalized errors, the
    error in magnitudes is ``1.0857 * sigma / flux`` (a small fractional error
    changes the magnitude by 2.5 / ln(10) times that fraction). The result
    is the median of all those points. It is the noise the measurement
    process says each point has, not the scatter actually seen in the light
    curve (see `median_light_curve_scatter_mag`).

    Parameters
    ----------
    stellar_objects : `list` [`StellarObject`]
        Stars carrying light curves.

    Returns
    -------
    error_mag : `float` or `None`
        The median error in magnitudes, or `None` when no star has
        normalized errors.
    """
    errors_mag: list[float] = []
    for star in stellar_objects:
        light_curve = getattr(star, "photometry", None)
        if light_curve is None:
            continue
        fluxes, errors = light_curve.fluxes_normalized, light_curve.fluxes_normalized_errors
        if not errors or len(errors) != len(fluxes):
            continue
        errors_mag.extend(
            MAGNITUDES_PER_FRACTIONAL_FLUX_ERROR * error / flux
            for flux, error in zip(fluxes, errors, strict=True)
            if flux > 0
        )
    return float(np.median(errors_mag)) if errors_mag else None


@dataclass(frozen=True)
class AdaptiveCVCutoff:
    """The adaptive variability cutoff and the statistics it was built from.

    Carrying `median_cv`/`mad_cv` alongside the cutoff lets a caller
    (like `assess_output_quality`) reuse the exact same statistics the
    cutoff was built from, instead of recomputing them from `cv_list` a
    second time and risking the two falling out of sync.
    """

    cutoff: float
    median_cv: float
    mad_cv: float


def _adaptive_cv_cutoff(cv_list: list[float], sigma_threshold: float) -> AdaptiveCVCutoff:
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
    cutoff : `AdaptiveCVCutoff`
        The final noise limit, and the statistics it was built from.
    """
    # Compute robust statistics (Median and Median Absolute Deviation) to avoid
    # outliers artificially inflating the noise floor
    median_cv = float(np.median(cv_list))
    mad_cv = float(median_abs_deviation(cv_list, scale=1.0))

    # Floor the cutoff at 2% (0.02) to prevent micro-variability
    # flagging in exceptionally clean data
    cutoff = max(0.02, median_cv + sigma_threshold * max(1e-4, mad_cv))
    return AdaptiveCVCutoff(cutoff=cutoff, median_cv=median_cv, mad_cv=mad_cv)


# The public name of `_adaptive_cv_cutoff`, for code outside this module that
# needs the same cutoff the pipeline uses (for example the script that checks
# it against the catalogs).
adaptive_cv_cutoff = _adaptive_cv_cutoff


def flag_variable_stars(
    stellar_objects: list[StellarObject], sigma_threshold: float
) -> tuple[list[StellarObject], FieldAssessment]:
    """Find the stars that change brightness more than their noise explains.

    The steps are:

    1. Compute each star's CV, and the field's CV cutoff (median plus
       ``sigma_threshold`` MADs, with a floor of 0.02). Each star's
       `output_quality` is built from its CV against that cutoff.
    2. Fit the field's noise model and compute the three variability
       indices of each star (see `variability_indices`). A star is a
       candidate when its reduced chi-square, Stetson J and excess scatter
       all pass their thresholds.
    3. When the field has too few stars for a noise model, flag the stars
       whose CV exceeds the cutoff instead.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The stars to check.
    sigma_threshold : `float`
        The multiplier of the MAD in the CV cutoff. Used for the CV cutoff
        only; the thresholds of the indices are calibrated in
        `variability_indices.calibrate_thresholds`.

    Returns
    -------
    result : `tuple` [`list` [`StellarObject`], `FieldAssessment`]
        The candidate stars, and the noise model and thresholds that picked
        them (both `None` when the CV cutoff was used).
    """
    cv_list = _compute_star_coefficients_of_variation(stellar_objects)

    if not cv_list:
        return [], FieldAssessment(None, None, [])

    cutoff_stats = _adaptive_cv_cutoff(cv_list, sigma_threshold)

    for star in stellar_objects:
        cv = getattr(star.photometry, "coefficient_of_variation", None)
        if cv is None:
            continue
        # The margin stays a statement about the CV against its cutoff. It is
        # not the margin of the candidate rule below.
        star.photometry.output_quality = assess_output_quality(
            coefficient_of_variation=cv,
            adaptive_cutoff=cutoff_stats.cutoff,
            mad_cv=cutoff_stats.mad_cv,
        )

    assessment = assess_variability(stellar_objects)
    if assessment.noise_model is not None:
        return assessment.candidates, assessment

    # Too few stars for a noise model: flag by the CV cutoff. (The cutoff
    # already has its own 0.02 floor, so no separate flat threshold is
    # applied here; a flat 0.10 would cap the cutoff at 10% and defeat the
    # point of raising it in noisy fields.)
    variable_candidates = [
        star
        for star in stellar_objects
        if star.photometry.coefficient_of_variation is not None
        and star.photometry.coefficient_of_variation > cutoff_stats.cutoff
    ]
    return variable_candidates, assessment


def _flag_variable_stars_by_adaptive_cutoff(
    stellar_objects: list[StellarObject], sigma_threshold: float
) -> list[StellarObject]:
    """Find the stars that change brightness more than their noise explains.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The stars to check.
    sigma_threshold : `float`
        The multiplier of the MAD in the CV cutoff (see
        `flag_variable_stars`).

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        The stars that passed the test and look like real variable stars.
    """
    return flag_variable_stars(stellar_objects, sigma_threshold)[0]


# --- Long-term (between-session) change --------------------------------------
#
# The long-term search compares the star's median normalized flux in each
# observing session. A median needs a few points to mean anything, and a
# change needs two sessions to be measured at all.
MINIMUM_SESSIONS_FOR_LONG_TERM_SEARCH = 2
MINIMUM_POINTS_PER_SESSION_FOR_LONG_TERM_SEARCH = 3

# How many times its expected error the difference between the brightest and
# faintest session medians must be before the star is flagged.
DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD = 3.0

# The smallest between-session change, in magnitudes, that is flagged however
# well it is measured. A bright star's median is so well determined that a
# 0.5% difference between nights can be many times its error, yet the
# comparison ensembles of two nights differ by about this much (see
# `SessionPhotometrySummary`). 0.02 mag matches the 2% floor of the
# within-session CV cutoff.
MINIMUM_LONG_TERM_AMPLITUDE_MAG = 0.02

# The standard error of a sample median of normally distributed values is
# sqrt(pi / 2) = 1.2533 times the standard error of their mean.
_MEDIAN_STANDARD_ERROR_FACTOR = 1.2533

# A noiseless light curve has a zero error and so an infinite significance.
# The value is capped so it can be stored and sent as JSON.
_MAXIMUM_REPORTED_SIGNIFICANCE = 1.0e6


def _between_session_change(star: StellarObject) -> tuple[float, float] | None:
    """Measure how much a star's level changed from one session to another.

    Uses the per-session summaries recorded when the sessions were merged.
    Takes the session with the highest median normalized flux and the one
    with the lowest. Their difference, divided by the combined expected
    error of the two medians, is the significance. The expected error of
    one session's median is 1.2533 x scatter / sqrt(points), where scatter
    is the robust within-session standard deviation of the normalized flux.

    Parameters
    ----------
    star : `StellarObject`
        A star with a merged light curve.

    Returns
    -------
    change : `tuple` [`float`, `float`] or `None`
        The amplitude in magnitudes (2.5 log10 of the highest median over
        the lowest) and the significance (difference over combined error,
        unitless). `None` when fewer than two sessions have enough usable
        points.
    """
    if star.photometry is None:
        return None
    usable = [
        summary
        for summary in star.photometry.session_summaries
        if summary.median_normalized_flux is not None
        and summary.median_normalized_flux > 0
        and summary.normalized_flux_scatter is not None
        and summary.point_count >= MINIMUM_POINTS_PER_SESSION_FOR_LONG_TERM_SEARCH
    ]
    if len(usable) < MINIMUM_SESSIONS_FOR_LONG_TERM_SEARCH:
        return None

    brightest = max(usable, key=lambda summary: summary.median_normalized_flux)
    faintest = min(usable, key=lambda summary: summary.median_normalized_flux)
    amplitude_mag = 2.5 * math.log10(brightest.median_normalized_flux / faintest.median_normalized_flux)

    def _median_error(summary: Any) -> float:
        """Estimate the error of one session's median normalized flux.

        Returns
        -------
        error : `float`
            Unitless, on the scale of the normalized flux.
        """
        scatter = summary.normalized_flux_scatter
        return _MEDIAN_STANDARD_ERROR_FACTOR * scatter / math.sqrt(summary.point_count)

    combined_error = math.hypot(_median_error(brightest), _median_error(faintest))
    difference = brightest.median_normalized_flux - faintest.median_normalized_flux
    if combined_error > 0:
        significance = min(difference / combined_error, _MAXIMUM_REPORTED_SIGNIFICANCE)
    else:
        significance = _MAXIMUM_REPORTED_SIGNIFICANCE if difference > 0 else 0.0
    return amplitude_mag, significance


def identify_long_term_variable_candidates(
    stellar_objects: list[StellarObject],
    significance_threshold: float = DEFAULT_LONG_TERM_SIGNIFICANCE_THRESHOLD,
) -> list[StellarObject]:
    """Find stars whose brightness differs between observing sessions.

    The search needs light curves merged across sessions, with the
    per-session summaries that the merge records. For each star it:

    1. Takes each session's median normalized flux. The merge does not
       rescale sessions, so these medians keep the between-night level.
    2. Finds the highest and the lowest of those medians and converts the
       ratio to an amplitude in magnitudes: ``2.5 log10(highest / lowest)``.
    3. Divides the difference of the two medians by their combined
       expected error. The error of one median is
       ``1.2533 x scatter / sqrt(points)``, where ``scatter`` is the
       session's within-session scatter (1.4826 x the median absolute
       deviation of its normalized flux). The result is the significance,
       in units of that error.
    4. Flags the star when the significance exceeds `significance_threshold`
       and the amplitude is at least 0.02 mag.

    A star needs at least two sessions with at least three usable points
    each. Other stars are never flagged, and their between-session fields
    are cleared.

    The star's `coefficient_of_variation` is not changed. It stays the
    within-session scatter measured per session, so this search compares
    two separate quantities and does not mix them.

    The normalized levels of two sessions are only a fair comparison when
    their comparison ensembles behave alike. The ensembles are chosen per
    session, so a shift shared by every star in the field points to the
    ensemble, not the star. Compare the star's amplitude with the other
    stars' amplitudes and with the `session_summaries` ensemble fields
    before treating a flag as a real variable.

    Parameters
    ----------
    stellar_objects : `list` of `StellarObject`
        The stars to check, with light curves merged across sessions.
    significance_threshold : `float`, optional
        How many times its expected error the difference between the
        brightest and faintest session medians must be. Unitless.

    Returns
    -------
    variable_candidates : `list` [`StellarObject`]
        The stars that look like long-term variables. Each one carries
        `between_session_amplitude_mag` (magnitudes) and
        `between_session_significance` (unitless) on its photometry.
    """
    variable_candidates = []
    for star in stellar_objects:
        if star.photometry is None:
            continue
        change = _between_session_change(star)
        if change is None:
            star.photometry.between_session_amplitude_mag = None
            star.photometry.between_session_significance = None
            continue
        amplitude_mag, significance = change
        star.photometry.between_session_amplitude_mag = round(amplitude_mag, 4)
        star.photometry.between_session_significance = round(significance, 2)
        if significance > significance_threshold and amplitude_mag >= MINIMUM_LONG_TERM_AMPLITUDE_MAG:
            variable_candidates.append(star)
    return variable_candidates


def _isolated_flagged_points(flagged: np.ndarray, above: np.ndarray) -> np.ndarray:
    """Pick the flagged points that are not part of a run on one side.

    Parameters
    ----------
    flagged : `numpy.ndarray`
        One `bool` per point: the point is beyond the clipping limit.
    above : `numpy.ndarray`
        One `bool` per point: the point is above the median (`False` means
        below).

    Returns
    -------
    isolated : `numpy.ndarray`
        One `bool` per point. `True` for a flagged point that has fewer than
        `MINIMUM_RUN_LENGTH_KEPT_AS_REAL_CHANGE` flagged neighbours in a row
        on the same side of the median, including itself.
    """
    isolated = np.zeros(flagged.size, dtype=bool)
    start = 0
    while start < flagged.size:
        if not flagged[start]:
            start += 1
            continue
        end = start
        while end + 1 < flagged.size and flagged[end + 1] and above[end + 1] == above[start]:
            end += 1
        if end - start + 1 < MINIMUM_RUN_LENGTH_KEPT_AS_REAL_CHANGE:
            isolated[start : end + 1] = True
        start = end + 1
    return isolated


def _normalized_flux_error(flux: float, flux_error: float, level: float, level_error: float) -> float:
    """Find the uncertainty of a flux divided by the ensemble level.

    For the ratio ``flux / level`` with independent errors, the variances
    add: ``(flux_error / level)**2 + (flux * level_error / level**2)**2``.

    Parameters
    ----------
    flux, flux_error : `float`
        The star's flux and its 1-sigma uncertainty, in ADU per second.
    level, level_error : `float`
        The ensemble level and its 1-sigma uncertainty, in ADU per second.
        The level must be above zero.

    Returns
    -------
    error : `float`
        The 1-sigma uncertainty of the normalized flux. It has no unit.
    """
    return math.hypot(flux_error / level, flux * level_error / level**2)


def _keep_where(values: list[Any], keep: Any) -> list[Any]:
    """Keep the entries of a per-frame list that a mask marks as valid.

    Parameters
    ----------
    values : `list`
        One value per frame, or empty.
    keep : `numpy.ndarray`
        A boolean mask with one entry per frame.

    Returns
    -------
    kept : `list`
        The values where the mask is `True`. A list that is empty, or not
        as long as the mask, is returned unchanged, because pairing its
        entries with the mask by position would be a guess.
    """
    if not values or len(values) != len(keep):
        return values
    return [value for value, valid in zip(values, keep, strict=True) if valid]


class VariabilityAnalyzer:
    """Analyzes a sequence of images to detect variable stars."""

    def __init__(
        self,
        config: Any = None,
        *,
        maximum_comparison_stars: int = DEFAULT_MAXIMUM_COMPARISON_STARS,
        ensemble_airmass_correction: bool = False,
    ) -> None:
        """Start an analyzer with no stars and no frames measured yet.

        Parameters
        ----------
        config : `Any`, optional
            Kept on the analyzer as ``self.config``. Nothing in this class
            reads it.
        maximum_comparison_stars : `int`, optional
            The most stars the session's comparison set may have. Values
            below `MINIMUM_COMPARISON_STARS` are raised to it.
        ensemble_airmass_correction : `bool`, optional
            Whether `detrend_light_curves_airmass` applies the correction
            derived from the comparison stars. Off by default, in which case
            the detrended light curve equals the normalized one.
        """
        self.config = config
        self.maximum_comparison_stars = max(maximum_comparison_stars, MINIMUM_COMPARISON_STARS)
        self.ensemble_airmass_correction = ensemble_airmass_correction
        self.light_curves: dict[str, PhotometryResult] = {}
        self.stellar_objects: list[StellarObject] = []
        self.frame_reference_flux = {}
        self.timestamp_to_path = {}
        self.rejected_files = []
        # Frames left out because `DATE-OBS` is missing or unreadable, each
        # with the reason. Filled during `process()`. The photometry runner
        # reads it to build the `capture_timestamps` gate.
        self.frames_without_usable_date_obs: list[ExcludedFrame] = []
        # How many frames each star's own centroid was refused in (it kept
        # the shifted reference position instead), keyed by star id. Only
        # stars with at least one such frame appear. Filled during
        # `process()`; see `refine_star_centroid`.
        self.centroid_fallback_counts: dict[str, int] = {}
        # For each frame, keyed by timestamp: how far every star with a
        # refined centroid sat from its shifted reference position, in
        # pixels (see `centroid_offsets_px`). A frame in which every star
        # fell back has an empty list. Filled during `process()`;
        # `centroid_shift_summary()` summarizes it for the
        # `registration_drift` gate.
        self.centroid_offsets_by_frame: dict[datetime, list[float]] = {}
        # Star measurements made in the frames after the reference frame, one
        # per star per frame. The denominator of the fallback fraction.
        self.centroid_measurement_count = 0
        # Which rule chose the stars that measure each frame's global shift
        # (see `select_alignment_anchors`), and how many it chose. `None`
        # and 0 before `process()` has run.
        self.alignment_anchor_rule: str | None = None
        self.alignment_anchor_count = 0
        self.frame_ensemble_composition: list[FrameEnsembleComposition] = []
        # How far each frame's alignment drifted from the reference
        # frame, keyed by timestamp: `(delta_x_shift, delta_y_shift)` in
        # pixels. Populated during `process()`; used by
        # `_apply_frame_normalization` to build each star's
        # `input_quality`.
        self.frame_registration_drift: dict[datetime, tuple[float, float]] = {}
        # The number of frames in this session, set at the top of
        # `process()`. `input_quality.coverage_fraction`'s denominator.
        self.total_frames = 0
        # The gain and read noise the flux errors were computed with. Set
        # during `process()`.
        self.detector_noise = DetectorNoise()
        # The exposure time of each frame, in seconds, keyed by the frame's
        # UTC exposure start. Filled during `process()`.
        self.frame_exposure_seconds: dict[datetime, float] = {}
        # The uncertainty of each frame's comparison-ensemble level, keyed
        # by timestamp, in the same units as `frame_reference_flux` (ADU per
        # second). Filled by `normalize_light_curves()`. Empty when the
        # comparison stars carry no errors.
        self.frame_reference_flux_error: dict[datetime, float] = {}
        # What a comparison star is divided by: the ensemble of the other
        # members, as `(level, level_error)` per timestamp, keyed by star id.
        # `level_error` is `None` without errors. A star that is not a member
        # is divided by `frame_reference_flux` instead.
        self._member_references: dict[str, dict[datetime, tuple[float, float | None]]] = {}
        # The comparison set chosen for this session, or `None` before
        # `normalize_light_curves()` has run on stars with light curves.
        self.comparison_set: ComparisonSetResult | None = None
        # The airmass slope the optional correction removed, as a fractional
        # change of flux per unit of airmass. `None` when it was not applied.
        self.ensemble_airmass_slope: float | None = None
        # The noise model and the thresholds of the last
        # `identify_variable_stars()`. `None` before it runs, and when the
        # field was too small for a noise model.
        self.noise_model: NoiseModel | None = None
        self.variability_thresholds: VariabilityThresholds | None = None

    def load_target_images(self, target_id: str) -> list[str]:
        """Not used anymore, kept only so older code doesn't break.

        Returns
        -------
        image_paths : `list` [`str`]
            Always empty.
        """
        return []

    def process(
        self,
        image_paths: list[str],
        max_workers: int | None = None,
        id_prefix: str = "",
        seed_stars: list[StellarObject] | None = None,
        target_position_deg: tuple[float, float] | None = None,
        observer_site: ObservatorySite | None = None,
    ) -> None:
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
        target_position_deg : `tuple` [`float`, `float`], optional
            The right ascension and declination of the field, in degrees
            (ICRS). With it, every light curve also gets mid-exposure
            BJD_TDB times (``time_bjd_tdb``). Without it, only the UTC
            exposure starts are recorded.
        observer_site : `ObservatorySite`, optional
            The observatory, for the BJD_TDB conversion. Without it, the
            observer is taken at Earth's center and the light curves say so
            in ``time_basis``.
        """
        if not image_paths:
            return

        self.total_frames = len(image_paths)

        # 1. Reference Frame: Deep Detection (Sequential)
        reference_path = image_paths[0]
        logger.info("[1/%s] Processing Reference %s...", len(image_paths), os.path.basename(reference_path))

        with fits.open(reference_path, memmap=False) as fits_handle:
            reference_data = collapse_to_2d(fits_handle[0].data.astype(float))
            reference_header = fits_handle[0].header
            # Every later frame is lined up with this one pixel for pixel,
            # so they are assumed to come from the same camera. That makes
            # the reference frame's camera decide the saturation threshold.
            camera_profile = resolve_camera_profile(
                reference_header.get("INSTRUME", reference_header.get("CAMERA"))
            )
            saturation_threshold_adu = camera_profile.saturation_threshold_adu.value
            # The gain and read noise behind the flux errors. Every later
            # frame is assumed to come from the same camera at the same
            # settings, like the saturation threshold above.
            self.detector_noise = resolve_detector_noise(camera_profile, reference_header)
            try:
                reference_timestamp = read_observation_time(reference_header)
            except ObservationTimeError as error:
                # Without a capture time the reference frame cannot anchor
                # the session. Record why and stop; never use the clock.
                logger.warning("Reference frame %s rejected: %s", reference_path, error)
                self.frames_without_usable_date_obs.append(
                    ExcludedFrame(path=reference_path, reason=str(error))
                )
                return
            reference_exposure_seconds = _read_exposure_seconds(reference_header)
            self.frame_exposure_seconds[reference_timestamp] = reference_exposure_seconds
            # Seeded below alongside the reference frame's flux. Leaving
            # it out started every light curve with one fewer airmass
            # than flux, and since the per-frame worker appends to both
            # thereafter, airmasses[i] described the frame at fluxes[i+1]
            # for the whole run -- so airmass detrending read the wrong
            # airmass for every point.
            reference_airmass = compute_frame_airmass(reference_header)
            # The reference frame is what every later frame is aligned
            # against, so it has no computed offset of its own -- its
            # drift is zero by definition.
            self.frame_registration_drift[reference_timestamp] = (0.0, 0.0)

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
            logger.info("  Attempting detection with FWHM=%s, Sigma=%s...", config["fwhm"], config["sigma"])
            detector = SourceDetector(threshold_sigma=config["sigma"], fwhm=config["fwhm"])
            reference_stars_detected = detector.detect(reference_data)
            if reference_stars_detected and len(reference_stars_detected) > 10:
                logger.info("  Success! Found %s stars.", len(reference_stars_detected))
                break

        if not reference_stars_detected:
            logger.info("No stars in reference frame (after retries). Aborting.")
            return

        # SourceDetector already returns a list sorted by flux
        max_stars = 2000

        # Alignment anchors always come from this blind detection pass,
        # regardless of whether seed_stars is given: frame-to-frame
        # alignment does not care which stars are being tracked or
        # reported, only that enough real stars exist to measure a
        # reliable shift. The anchors are unsaturated stars with a high
        # peak signal-to-noise, not the detections ranked 50 to 100 (which
        # are noise peaks in a sparse field); see `select_alignment_anchors`.
        anchors = select_alignment_anchors(
            reference_data, reference_stars_detected, saturation_threshold_adu=saturation_threshold_adu
        )
        reference_top_refs_minimal = list(anchors.stars)
        self.alignment_anchor_rule = anchors.rule
        self.alignment_anchor_count = len(anchors.stars)
        logger.info("  Alignment anchors: %s stars (%s).", len(anchors.stars), anchors.rule)

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
            # identification and recording are separate and untouched.
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
                reference_measurement = measure_aperture_photometry(
                    reference_data,
                    x_ref,
                    y_ref,
                    saturation_threshold_adu=saturation_threshold_adu,
                    noise=self.detector_noise,
                )
                flux = reference_measurement.net_flux_adu / reference_exposure_seconds
                if flux <= 0:
                    seed_stars_without_signal += 1
                    continue
                seed_star.flux = flux
                seed_star.photometry = PhotometryResult(
                    timestamps=[reference_timestamp],
                    fluxes=[flux],
                    flux_errors=[reference_measurement.flux_error_adu / reference_exposure_seconds],
                    errors_assume_unit_gain=self.detector_noise.gain_is_assumed,
                    errors_assume_zero_read_noise=self.detector_noise.read_noise_is_assumed,
                    is_saturated=[reference_measurement.is_saturated],
                    airmasses=[reference_airmass],
                )
                self.stellar_objects.append(seed_star)
                reference_stars_minimal.append((seed_star.id, x_ref, y_ref))
            if seed_stars_without_signal:
                logger.info(
                    "  %s of %s seed stars had no detectable signal above background on the "
                    "reference frame; excluded from per-frame tracking.",
                    seed_stars_without_signal,
                    len(seed_stars),
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
                reference_measurement = measure_aperture_photometry(
                    reference_data,
                    x_ref,
                    y_ref,
                    saturation_threshold_adu=saturation_threshold_adu,
                    noise=self.detector_noise,
                )
                flux = reference_measurement.net_flux_adu / reference_exposure_seconds
                new_star.flux = flux
                new_star.photometry = PhotometryResult(
                    timestamps=[reference_timestamp],
                    fluxes=[flux],
                    flux_errors=[reference_measurement.flux_error_adu / reference_exposure_seconds],
                    errors_assume_unit_gain=self.detector_noise.gain_is_assumed,
                    errors_assume_zero_read_noise=self.detector_noise.read_noise_is_assumed,
                    is_saturated=[reference_measurement.is_saturated],
                    airmasses=[reference_airmass],
                )
                self.stellar_objects.append(new_star)
                reference_stars_minimal.append((new_star.id, x_ref, y_ref))

        logger.info("  Initialized %s reference stars.", len(self.stellar_objects))

        if len(image_paths) > 1:
            # Default to a limit of 75% CPU cores unless the caller
            # specifies otherwise
            max_workers = max_workers if max_workers is not None else max(1, int(os.cpu_count() * 0.75))
            logger.info(
                "Starting parallel processing for %s frames using %s workers...",
                len(image_paths) - 1,
                max_workers,
            )
            worker_arguments = [
                (
                    path,
                    reference_stars_minimal,
                    reference_top_refs_minimal,
                    saturation_threshold_adu,
                    self.detector_noise,
                )
                for path in image_paths[1:]
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
            reference_positions = {star_id: (x, y) for star_id, x, y in reference_stars_minimal}

            for result in results:
                if result is None:
                    continue
                path, data = result
                if data is None:
                    continue
                if isinstance(data, FrameRejection):
                    self.frames_without_usable_date_obs.append(ExcludedFrame(path=path, reason=data.reason))
                    continue

                timestamp, fluxes_dict, delta_x, delta_y, _bg, airmass, star_positions, exposure_seconds = (
                    data
                )
                for star_id, star_position in star_positions.items():
                    if not star_position.is_refined:
                        previous_count = self.centroid_fallback_counts.get(star_id, 0)
                        self.centroid_fallback_counts[star_id] = previous_count + 1
                offsets, _ = centroid_offsets_px(star_positions, reference_positions, delta_x, delta_y)
                self.centroid_offsets_by_frame[timestamp] = offsets
                self.centroid_measurement_count += sum(
                    1 for star_id in star_positions if star_id in reference_positions
                )
                self.timestamp_to_path[timestamp] = path
                self.frame_registration_drift[timestamp] = (float(delta_x), float(delta_y))
                self.frame_exposure_seconds[timestamp] = float(exposure_seconds)

                # Update each StellarObject's light curve with the new
                # data point
                for star_id, (flux, is_saturated, flux_error) in fluxes_dict.items():
                    if star_id in stellar_object_map:
                        star = stellar_object_map[star_id]
                        star.photometry.timestamps.append(timestamp)
                        star.photometry.fluxes.append(float(flux))
                        star.photometry.flux_errors.append(float(flux_error))
                        star.photometry.is_saturated.append(is_saturated)
                        star.photometry.airmasses.append(float(airmass))

            logger.info("Parallel processing complete.")

        self._record_barycentric_times(target_position_deg, observer_site)

    def centroid_shift_summary(self) -> CentroidShiftSummary | None:
        """Summarize how far star centroids sat from their shifted positions.

        The result feeds the ``registration_drift`` gate. It is built from
        the per-frame offsets that `process()` recorded.

        Returns
        -------
        summary : `CentroidShiftSummary` or `None`
            The median and 95th-percentile per-star offset, the fraction of
            star measurements that fell back, the worst frame, and the
            anchor rule. `None` when the session had no frame after the
            reference frame.
        """
        return summarize_centroid_shifts(
            self.centroid_offsets_by_frame,
            self.centroid_measurement_count,
            sum(self.centroid_fallback_counts.values()),
            anchor_rule=self.alignment_anchor_rule,
            anchor_count=self.alignment_anchor_count,
        )

    def _record_barycentric_times(
        self, target_position_deg: tuple[float, float] | None, observer_site: ObservatorySite | None
    ) -> None:
        """Give every light curve its mid-exposure BJD_TDB times.

        The conversion runs once for all frames of the session, then each
        star takes the value for each of its timestamps. Nothing is
        recorded without a target position, because the barycentric
        correction depends on the direction of the target.

        Parameters
        ----------
        target_position_deg : `tuple` [`float`, `float`] or `None`
            Right ascension and declination of the field, in degrees.
        observer_site : `ObservatorySite` or `None`
            The observatory, or `None` to take the observer at Earth's
            center.
        """
        if target_position_deg is None:
            return
        frame_starts = sorted(self.frame_exposure_seconds)
        if not frame_starts:
            return
        try:
            bjd_values = barycentric_julian_dates(
                frame_starts,
                [self.frame_exposure_seconds[start] for start in frame_starts],
                target_position_deg[0],
                target_position_deg[1],
                observer_site,
            )
        except (ValueError, OverflowError) as time_error:
            # An out-of-range position or date. The UTC times stay usable.
            logger.warning("BJD_TDB times could not be computed: %s", time_error)
            return
        bjd_by_start = dict(zip(frame_starts, (float(value) for value in bjd_values), strict=True))
        basis = time_basis_for(observer_site)
        for star in self.stellar_objects:
            light_curve = star.photometry
            if light_curve is None or not all(stamp in bjd_by_start for stamp in light_curve.timestamps):
                continue
            light_curve.time_bjd_tdb = [bjd_by_start[stamp] for stamp in light_curve.timestamps]
            light_curve.time_basis = basis

    def _measure_flux_numpy(
        self, data: np.ndarray, x: float, y: float, radius: float = 4.0, *, saturation_threshold_adu: float
    ) -> tuple[float, bool]:
        """Measure the brightness of a star inside a small circle.

        We add up all the light inside the circle, then subtract the background
        glow to get the star's true brightness. This is a thin wrapper around
        `_measure_aperture_flux`, which is the module-level function every flux
        measurement in this file actually goes through.

        Parameters
        ----------
        data : `np.ndarray`
            The picture, as a 2-D array.
        x, y : `float`
            The star's position in pixels.
        radius : `float`, optional
            The radius of the circle, in pixels.
        saturation_threshold_adu : `float`
            A pixel at or above this value counts as saturated. Take it from
            the camera's profile.

        Returns
        -------
        result : `tuple[float, bool]`
            The total brightness, and a True/False flag if the star was
            too bright (saturated).
        """
        return _measure_aperture_flux(
            data, x, y, radius=radius, saturation_threshold_adu=saturation_threshold_adu
        )

    def normalize_light_curves(self) -> None:
        """Perform differential photometry against a fixed comparison set.

        Chooses the session's comparison stars once (see
        `comparison_ensemble`), builds their weighted ensemble signal for
        every frame, rejects frames whose signal is an outlier (a cloud, a
        tracking loss), and divides every star's flux by the signal. A
        comparison star is divided by the ensemble of the other members. Each
        star's own outlying measurements are then sigma-clipped.

        Frames in which too few stars were measured, and frames rejected as
        outliers, are removed from every light curve. When no usable
        comparison set can be built, every light curve is left unnormalized
        and `comparison_set` records the stars that were found.
        """
        if not self.stellar_objects:
            return

        self.comparison_set = None
        self.frame_reference_flux = {}
        self.frame_reference_flux_error = {}
        self._member_references = {}
        self.frame_ensemble_composition = []

        table = build_flux_table(self.stellar_objects)
        if table is None:
            logger.warning("  Normalization: no star has a positive flux in any frame.")
            self._apply_frame_normalization()
            return
        selection = select_comparison_set(table, self.maximum_comparison_stars)
        self.comparison_set = set_result(table, selection)
        member_count = len(selection.member_rows)
        logger.info(
            "  Comparison set: %s of %s candidates kept (%s dropped as not constant, %s left out as "
            "listed variable), %s frames, errors %s.",
            member_count,
            selection.candidate_count,
            len(selection.vetted_out_rows),
            len(selection.listed_variable_rows),
            len(table.timestamps),
            "from the CCD equation" if selection.uses_errors else "estimated from the light curves",
        )
        if member_count < 2:
            logger.warning(
                "  Normalization: %s usable comparison star(s); leaving raw light curves intact.",
                member_count,
            )
            self._apply_frame_normalization()
            return

        member_rows = list(selection.member_rows)
        member_fluxes = table.fluxes[member_rows]
        member_errors = table.errors[member_rows] if selection.uses_errors else None
        every_frame = np.ones(len(table.timestamps), dtype=bool)
        signal = build_ensemble_signal(member_fluxes, member_errors, every_frame)
        self.frame_ensemble_composition = [
            FrameEnsembleComposition(
                frame_path=self.timestamp_to_path.get(timestamp, "Unknown"),
                ensemble_size=member_count,
                excluded_comparison_star_ids=[],
            )
            for timestamp in table.timestamps
        ]
        kept = self._reject_outlier_frames(dict(zip(table.timestamps, signal.level.tolist(), strict=True)))
        frame_mask = np.array([timestamp in kept for timestamp in table.timestamps])
        if not frame_mask.all() and frame_mask.sum() >= 2:
            signal = build_ensemble_signal(member_fluxes, member_errors, frame_mask)
        self._record_ensemble_signal(
            table.timestamps, frame_mask, signal, [table.star_ids[row] for row in member_rows]
        )
        self._apply_frame_normalization()
        self._record_comparison_scatter()

    def _record_ensemble_signal(
        self, timestamps: tuple[datetime, ...], frame_mask: np.ndarray, signal: Any, member_ids: list[str]
    ) -> None:
        """Store the ensemble signal of the frames that were kept.

        Parameters
        ----------
        timestamps : `tuple` [`datetime.datetime`]
            The usable frames, oldest first.
        frame_mask : `numpy.ndarray`
            One `bool` per frame, `True` for a frame that was kept.
        signal : `EnsembleSignal`
            The ensemble level of every usable frame.
        member_ids : `list` [`str`]
            The comparison stars, in the order of `signal.member_levels`.
        """
        levels = signal.level.tolist()
        errors = signal.level_error.tolist() if signal.level_error is not None else None
        member_levels = signal.member_levels.tolist()
        member_errors = (
            signal.member_level_errors.tolist() if signal.member_level_errors is not None else None
        )
        kept_columns = [column for column, keep in enumerate(frame_mask.tolist()) if keep]
        for column in kept_columns:
            self.frame_reference_flux[timestamps[column]] = levels[column]
            if errors is not None:
                self.frame_reference_flux_error[timestamps[column]] = errors[column]
        for row, star_id in enumerate(member_ids):
            self._member_references[star_id] = {
                timestamps[column]: (
                    member_levels[row][column],
                    member_errors[row][column] if member_errors is not None else None,
                )
                for column in kept_columns
            }

    def _record_comparison_scatter(self) -> None:
        """Measure how well the comparison stars agree after normalization.

        For each comparison star the scatter of its normalized light curve is
        ``2.5 log10(1 + CV)``, and the error its propagated uncertainties
        predict is ``2.5 log10(1 + median(sigma / flux))``. The set's values
        are the medians over its members. Also records how many comparison
        stars were used in each frame, which is the same number for every
        frame of a fixed set.
        """
        if self.comparison_set is None:
            return
        member_ids = set(self.comparison_set.star_ids)
        scatters: list[float] = []
        expected: list[float] = []
        for star in self.stellar_objects:
            if star.id not in member_ids or star.photometry is None:
                continue
            fluxes = np.array(star.photometry.fluxes_normalized, dtype=float)
            if fluxes.size < 3 or np.any(fluxes <= 0):
                continue
            scatters.append(fractional_to_magnitudes(float(np.std(fluxes, ddof=1) / np.mean(fluxes))))
            errors = np.array(star.photometry.fluxes_normalized_errors, dtype=float)
            if errors.size == fluxes.size:
                expected.append(fractional_to_magnitudes(float(np.median(errors / fluxes))))
        self.comparison_set = replace(
            self.comparison_set,
            scatter_mag=float(np.median(scatters)) if scatters else None,
            expected_error_mag=float(np.median(expected)) if expected else None,
            frame_sizes=tuple(composition.ensemble_size for composition in self.frame_ensemble_composition),
        )

    def _reject_outlier_frames(self, levels: dict[datetime, float]) -> set[datetime]:
        """Reject the frames whose ensemble level is a statistical outlier.

        A frame whose ensemble level is an outlier against every other
        frame's is more likely a clouded-out or otherwise bad frame than a
        real brightness signal, so it is rejected outright (its path is
        added to `self.rejected_files`). The test is a 3-sigma clip about the
        median, using the median absolute deviation. It needs at least
        `MINIMUM_FRAMES_FOR_OUTLIER_REJECTION` frames.

        Parameters
        ----------
        levels : `dict` [`datetime.datetime`, `float`]
            The ensemble level of each frame, in ADU per second.

        Returns
        -------
        kept : `set` [`datetime.datetime`]
            The frames that were not rejected.
        """
        factors_list = sorted(levels.items())
        if len(factors_list) < MINIMUM_FRAMES_FOR_OUTLIER_REJECTION:
            return {timestamp for timestamp, _ in factors_list}

        times = [item[0] for item in factors_list]
        factors = np.array([item[1] for item in factors_list])

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
            clipped_factors = sigma_clip(factors, sigma=3.0, maxiters=3, cenfunc="median", stdfunc="mad_std")
            mask = ~clipped_factors.mask

        kept: set[datetime] = set()
        for timestamp, valid in zip(times, mask.tolist(), strict=True):
            if valid:
                kept.add(timestamp)
                continue
            path = self.timestamp_to_path.get(timestamp, "Unknown")
            if path not in self.rejected_files:
                self.rejected_files.append(path)
                logger.info("  [REJECTED] Global Frame Outlier: %s", os.path.basename(path))
        return kept

    def _reference_for(self, star_id: str, timestamp: datetime) -> tuple[float, float | None] | None:
        """Find what a star's flux is divided by in one frame.

        Parameters
        ----------
        star_id : `str`
            The star being normalized.
        timestamp : `datetime.datetime`
            The frame's exposure start.

        Returns
        -------
        reference : `tuple` [`float`, `float` or `None`] or `None`
            The ensemble level and its error, both in ADU per second. A
            comparison star gets the level built from the other members. Any
            other star gets the full ensemble. The error is `None` when the
            comparison stars have no errors. `None` when the frame has no
            ensemble level (it was rejected, or too few stars were measured).
        """
        own = self._member_references.get(star_id)
        if own is not None:
            return own.get(timestamp)
        if timestamp not in self.frame_reference_flux:
            return None
        return self.frame_reference_flux[timestamp], self.frame_reference_flux_error.get(timestamp)

    def _apply_frame_normalization(self) -> bool:
        """Divide each star's flux by its frame's normalization factor.

        Frames with no usable normalization factor are dropped from
        that star's light curve entirely, since a flux with no
        normalization is not comparable across the run.

        Returns
        -------
        applied : `bool`
            Whether normalization was actually applied. `False` if no
            frame had a usable factor at all, in which case every
            star's raw (unnormalized) light curve is left untouched
            rather than destroying its measured photometry.
        """
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
                star.photometry.fluxes_normalized = list(star.photometry.fluxes)
                star.photometry.fluxes_normalized_errors = list(star.photometry.flux_errors)
                star.photometry.input_quality = self._build_input_quality_for_star(star)
            return False

        for star in self.stellar_objects:
            star.photometry.fluxes_normalized = []
            new_timestamps = []
            new_fluxes = []
            # is_saturated and airmasses are per-frame too, so they have
            # to be filtered with the same mask. Rewriting only
            # timestamps/fluxes left them at their original length and
            # positionally misaligned, which is how a recorded light
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
            saturation_flags = star.photometry.is_saturated or []
            airmasses = star.photometry.airmasses or []
            saturation_flags_aligned = len(saturation_flags) == len(star.photometry.timestamps)
            airmasses_aligned = len(airmasses) == len(star.photometry.timestamps)
            # The error and BJD_TDB arrays follow the same rule, with one
            # difference: an empty array stays empty without counting as
            # misaligned, because light curves without them are normal.
            new_flux_errors = []
            new_normalized_errors = []
            new_bjd_times = []
            flux_errors = star.photometry.flux_errors or []
            bjd_times = star.photometry.time_bjd_tdb or []
            flux_errors_aligned = bool(flux_errors) and len(flux_errors) == len(star.photometry.timestamps)
            bjd_times_aligned = bool(bjd_times) and len(bjd_times) == len(star.photometry.timestamps)
            normalized_errors_complete = flux_errors_aligned

            for index, (timestamp, flux) in enumerate(
                zip(star.photometry.timestamps, star.photometry.fluxes, strict=False)
            ):
                reference = self._reference_for(star.id, timestamp)
                if reference is not None:
                    norm_factor, ensemble_error = reference
                    if norm_factor > 0:
                        star.photometry.fluxes_normalized.append(flux / norm_factor)
                        new_timestamps.append(timestamp)
                        new_fluxes.append(flux)
                        if saturation_flags_aligned:
                            new_is_saturated.append(saturation_flags[index])
                        if airmasses_aligned:
                            new_airmasses.append(airmasses[index])
                        if flux_errors_aligned:
                            new_flux_errors.append(flux_errors[index])
                            if ensemble_error is None:
                                normalized_errors_complete = False
                            else:
                                new_normalized_errors.append(
                                    _normalized_flux_error(
                                        flux, flux_errors[index], norm_factor, ensemble_error
                                    )
                                )
                        if bjd_times_aligned:
                            new_bjd_times.append(bjd_times[index])

            # Update the original light curve data to exclude rejected frames
            star.photometry.timestamps = new_timestamps
            star.photometry.fluxes = new_fluxes
            star.photometry.is_saturated = new_is_saturated if saturation_flags_aligned else []
            star.photometry.airmasses = new_airmasses if airmasses_aligned else []
            star.photometry.flux_errors = new_flux_errors if flux_errors_aligned else []
            star.photometry.fluxes_normalized_errors = (
                new_normalized_errors if normalized_errors_complete else []
            )
            star.photometry.time_bjd_tdb = new_bjd_times if bjd_times_aligned else []
            if not bjd_times_aligned:
                star.photometry.time_basis = None
            self._reject_outlier_measurements_for_star(star)
            star.photometry.input_quality = self._build_input_quality_for_star(star)

        return True

    def _build_input_quality_for_star(self, star: StellarObject) -> InputQualityAssessment:
        """Build one star's input-quality assessment from its light curve.

        Called after a star's final (post-rejection) timestamps/fluxes/
        is_saturated have settled, so `frames_measured` and
        `saturated_fraction` reflect what actually survived normalization
        and outlier rejection, not the raw per-frame measurements.

        Returns
        -------
        assessment : `InputQualityAssessment`
            See that class for what each field means.
        """
        timestamps = star.photometry.timestamps
        saturation_flags = star.photometry.is_saturated or []
        saturated_fraction = sum(saturation_flags) / len(saturation_flags) if saturation_flags else 0.0
        drifts = [
            math.hypot(*self.frame_registration_drift[timestamp])
            for timestamp in timestamps
            if timestamp in self.frame_registration_drift
        ]
        return assess_input_quality(
            frames_measured=len(timestamps),
            frames_available=self.total_frames,
            saturated_fraction=saturated_fraction,
            max_registration_drift_px=max(drifts) if drifts else None,
        )

    def _reject_outlier_measurements_for_star(self, star: StellarObject) -> None:
        """Clip one star's isolated outlying measurements.

        Run right after that star's frame-level normalization above,
        this catches the star's own measurement outliers (a cosmic ray,
        a bad centroid) that global frame-level clipping wouldn't catch,
        since they're specific to one star rather than one frame.

        A point is a candidate for clipping when it is more than 5 robust
        standard deviations from the light curve's median. Only isolated
        candidates are clipped. Two or more neighbouring candidates on the
        same side of the median are a real change that lasts several frames
        (a transit, an eclipse, a pulsation), and they are kept. Clipping
        every candidate removed all the points of a 1 percent dip as soon as
        the measurements were precise enough for the dip to stand five
        standard deviations above the scatter.
        """
        if len(star.photometry.fluxes_normalized) > 10:
            flux_values = np.array(star.photometry.fluxes_normalized)

            # Same astropy sigma_clip/mad_std equivalence as the
            # frame-level pass above, at the historical
            # more-permissive per-star threshold |z| < 5.0, single
            # pass (maxiters=1). The mad_std > 0 guard preserves
            # the historical skip when MAD collapses to zero.
            if mad_std(flux_values) > 0:
                clipped_fluxes = sigma_clip(
                    flux_values, sigma=5.0, maxiters=1, cenfunc="median", stdfunc="mad_std"
                )
                above_median = flux_values > np.median(flux_values)
                valid_mask = ~_isolated_flagged_points(clipped_fluxes.mask, above_median)

                if not np.all(valid_mask):
                    star.photometry.timestamps = [
                        t for i, t in enumerate(star.photometry.timestamps) if valid_mask[i]
                    ]
                    star.photometry.fluxes = [
                        f for i, f in enumerate(star.photometry.fluxes) if valid_mask[i]
                    ]
                    star.photometry.fluxes_normalized = [
                        fn for i, fn in enumerate(star.photometry.fluxes_normalized) if valid_mask[i]
                    ]
                    # Same reason as the rejected-frame filter above:
                    # every per-frame array shares one index space.
                    star.photometry.is_saturated = [
                        s
                        for i, s in enumerate(star.photometry.is_saturated)
                        if i < len(valid_mask) and valid_mask[i]
                    ]
                    star.photometry.airmasses = [
                        a
                        for i, a in enumerate(star.photometry.airmasses)
                        if i < len(valid_mask) and valid_mask[i]
                    ]
                    # The error and BJD_TDB arrays are cut only when they
                    # line up with the flux array. A misaligned or empty
                    # array is left as it is rather than mispaired.
                    star.photometry.flux_errors = _keep_where(star.photometry.flux_errors, valid_mask)
                    star.photometry.fluxes_normalized_errors = _keep_where(
                        star.photometry.fluxes_normalized_errors, valid_mask
                    )
                    star.photometry.time_bjd_tdb = _keep_where(star.photometry.time_bjd_tdb, valid_mask)
                    if not star.photometry.time_bjd_tdb:
                        star.photometry.time_basis = None

    def identify_variable_stars(
        self, sigma_threshold: float = DEFAULT_VARIABILITY_SIGMA_THRESHOLD
    ) -> list[StellarObject]:
        """Find the stars that vary more than their noise explains.

        Sets the variability indices on each star's light curve and keeps the
        field's noise model and thresholds in `noise_model` and
        `variability_thresholds` (see `flag_variable_stars`).

        Parameters
        ----------
        sigma_threshold : `float`, optional
            The multiplier of the MAD in the CV cutoff, used when the field
            has too few stars for a noise model.

        Returns
        -------
        variable_candidates : `list[StellarObject]`
            The stars that look like real variable stars.
        """
        candidates, assessment = flag_variable_stars(self.stellar_objects, sigma_threshold)
        self.noise_model = assessment.noise_model
        self.variability_thresholds = assessment.thresholds
        return candidates

    def detrend_light_curves_airmass(self) -> None:
        """Fill the detrended light curves without fitting any star's own flux.

        By default the detrended light curve is the normalized one, copied
        with its errors. Dividing by the ensemble has already removed what
        every star shares, including the dimming of the whole field with
        airmass. An older version fitted a quadratic of each star's own
        normalized flux against airmass and divided it out. Airmass rises
        steadily over half a night, so such a fit also removed a transit or
        half a pulsation cycle. No star's own flux is fitted against airmass
        now, the target's least of all.

        With ``ensemble_airmass_correction`` on, a straight line of flux
        against airmass is fitted to each comparison star's normalized
        light curve. The median slope of the comparison stars (leaving out
        the star being corrected, if it is one) is removed from every star.
        This is the part of the airmass dependence that the comparison stars
        share beyond what their weighted mean absorbs, as when most of them
        have a colour that differs from the average. It cannot tell a
        star's own colour term from its variability, so it is off by default.
        """
        self.ensemble_airmass_slope = None
        member_slopes = self._comparison_star_airmass_slopes() if self.ensemble_airmass_correction else {}
        for star in self.stellar_objects:
            if not star.photometry or not star.photometry.fluxes_normalized:
                continue

            fluxes_norm = np.array(star.photometry.fluxes_normalized, dtype=float)
            normalized_errors = star.photometry.fluxes_normalized_errors
            normalized_errors = (
                np.array(normalized_errors, dtype=float)
                if len(normalized_errors) == len(fluxes_norm)
                else None
            )
            correction = self._airmass_correction_factor(star, member_slopes, fluxes_norm.size)
            if correction is None:
                correction = np.ones(fluxes_norm.size)
            star.photometry.fluxes_detrended = (fluxes_norm / correction).tolist()
            star.photometry.fluxes_detrended_errors = (
                (normalized_errors / correction).tolist() if normalized_errors is not None else []
            )

    def _comparison_star_airmass_slopes(self) -> dict[str, float]:
        """Fit flux against airmass for each comparison star.

        Returns
        -------
        slopes : `dict` [`str`, `float`]
            For each comparison star with at least five points and a
            changing airmass, the slope of its normalized flux (divided by
            its mean) against airmass. The unit is the fractional change of
            flux per unit of airmass.
        """
        if self.comparison_set is None:
            return {}
        members = set(self.comparison_set.star_ids)
        slopes: dict[str, float] = {}
        for star in self.stellar_objects:
            if star.id not in members or star.photometry is None:
                continue
            fluxes = np.array(star.photometry.fluxes_normalized, dtype=float)
            airmasses = np.array(star.photometry.airmasses, dtype=float)
            if fluxes.size < 5 or airmasses.size != fluxes.size or np.std(airmasses) <= 1e-4:
                continue
            mean_flux = float(np.mean(fluxes))
            if mean_flux <= 0:
                continue
            try:
                slopes[star.id] = np.polyfit(airmasses - np.mean(airmasses), fluxes / mean_flux, 1)[0].item()
            except DATA_ERRORS:
                continue
        return slopes

    def _airmass_correction_factor(
        self, star: StellarObject, member_slopes: dict[str, float], point_count: int
    ) -> np.ndarray | None:
        """Build the divisor of the ensemble-derived airmass correction.

        Parameters
        ----------
        star : `StellarObject`
            The star being corrected.
        member_slopes : `dict` [`str`, `float`]
            The comparison stars' slopes, from
            `_comparison_star_airmass_slopes`. Empty when the correction is
            off.
        point_count : `int`
            The length of the star's normalized light curve.

        Returns
        -------
        factor : `numpy.ndarray` or `None`
            ``1 + slope * (airmass - mean airmass)`` for each point, with
            ``slope`` the median of the other comparison stars' slopes.
            `None` when the correction is off, there are fewer than three
            other comparison stars with a slope, the star's airmasses do not
            line up with its points, or the factor is not positive.
        """
        others = [slope for star_id, slope in member_slopes.items() if star_id != star.id]
        airmasses = np.array(star.photometry.airmasses, dtype=float)
        if len(others) < 3 or airmasses.size != point_count:
            return None
        slope = float(np.median(others))
        factor = 1.0 + slope * (airmasses - np.mean(airmasses))
        if np.any(factor <= 0):
            return None
        self.ensemble_airmass_slope = slope
        return factor

    def run_bls_transit_search(self, star: StellarObject, shuffle_count: int | None = None) -> Any | None:
        """Look for a repeating, box-shaped dip in brightness.

        A planet passing in front of its star and one star of an
        eclipsing binary passing in front of the other both produce
        this same box-shaped dip -- this search doesn't try to tell
        the two apart. See `periodicity_search` for how the result is
        judged: the strongest dip always exists, so the returned candidate
        carries a verdict saying whether it stands out from noise.

        Parameters
        ----------
        star : `StellarObject`
            The star whose light curve is searched.
        shuffle_count : `int`, optional
            How many noise-only versions to compare with; the search picks
            a default when omitted.

        Returns
        -------
        candidate : `TransitCandidate` or `None`
            The search result (check its ``verdict``), or `None` if there
            were fewer than 8 measurements.
        """
        if not star.can_run_transit_search:
            return None

        from astrometricslib.pipelines.photometry.processing.periodicity_search import box_search

        time_days, fluxes = self._light_curve_arrays(star)
        if fluxes.size < MINIMUM_POINTS_FOR_TRANSIT_SEARCH or np.mean(fluxes) <= 0:
            return None
        candidate = box_search(
            time_days, fluxes, shuffle_count=shuffle_count, flux_errors=self.light_curve_errors(star)
        )
        star.photometry.transit_candidate = candidate
        return candidate

    def run_lomb_scargle_periodogram(
        self, star: StellarObject, shuffle_count: int | None = None
    ) -> Any | None:
        """Look for regular repeating patterns in the star's brightness.

        See `periodicity_search` for how the result is judged: the
        strongest cycle always exists, so the returned result carries a
        verdict saying whether it stands out from noise.

        Parameters
        ----------
        star : `StellarObject`
            The star whose light curve is searched.
        shuffle_count : `int`, optional
            How many noise-only versions to compare with; the search picks
            a default when omitted.

        Returns
        -------
        periodogram : `PeriodogramResult` or `None`
            The search result (check its ``verdict``), or `None` if there
            were fewer than 5 measurements.
        """
        if not star.can_run_period_search:
            return None

        from astrometricslib.pipelines.photometry.processing.periodicity_search import lomb_scargle_search

        time_days, fluxes = self._light_curve_arrays(star)
        if fluxes.size < MINIMUM_POINTS_FOR_PERIOD_SEARCH:
            return None
        result = lomb_scargle_search(
            time_days, fluxes, shuffle_count=shuffle_count, flux_errors=self.light_curve_errors(star)
        )
        star.photometry.periodogram = result
        return result

    @staticmethod
    def light_curve_arrays(star: StellarObject) -> tuple[np.ndarray, np.ndarray]:
        """Give a star's measurement times and brightness as arrays.

        The public name of `_light_curve_arrays`, for the checks that need the
        same arrays the searches used.

        Returns
        -------
        time_days, fluxes : `tuple` [`np.ndarray`, `np.ndarray`]
            Days since the first measurement, and the detrended brightness
            (or the normalized brightness when no detrended one exists).
            The days come from the mid-exposure BJD_TDB times when the star
            has them, and from the exposure start times otherwise.
        """
        return VariabilityAnalyzer._light_curve_arrays(star)

    @staticmethod
    def _light_curve_arrays(star: StellarObject) -> tuple[np.ndarray, np.ndarray]:
        """Give a star's measurement times and brightness as arrays.

        Returns
        -------
        time_days, fluxes : `tuple` [`np.ndarray`, `np.ndarray`]
            Days since the first measurement, and the detrended brightness
            (or the normalized brightness when no detrended one exists).
            The days come from ``time_bjd_tdb`` when it has one finite value
            for every timestamp, and from ``timestamps`` otherwise.
        """
        photometry = star.photometry
        raw_fluxes = (
            photometry.fluxes_detrended if photometry.fluxes_detrended else photometry.fluxes_normalized
        )
        fluxes = np.array(raw_fluxes, dtype=float)
        count = min(len(photometry.timestamps), fluxes.size)
        bjd_times = np.array(photometry.time_bjd_tdb, dtype=float)
        if (
            bjd_times.size == len(photometry.timestamps)
            and bjd_times.size > 0
            and np.all(np.isfinite(bjd_times))
        ):
            time_days = bjd_times[:count] - bjd_times[0]
        else:
            time_days = np.array([
                (stamp - photometry.timestamps[0]).total_seconds() / 86400.0
                for stamp in photometry.timestamps[:count]
            ])
        return time_days, fluxes[:count]

    @staticmethod
    def light_curve_errors(star: StellarObject) -> np.ndarray | None:
        """Give the uncertainties that go with `light_curve_arrays`.

        Parameters
        ----------
        star : `StellarObject`
            The star whose light curve is searched.

        Returns
        -------
        errors : `numpy.ndarray` or `None`
            The 1-sigma uncertainty of each brightness value (no unit; the
            detrended errors when the detrended brightness is used, the
            normalized errors otherwise), trimmed to the same length as the
            arrays from `light_curve_arrays`. `None` when the star has no
            errors, when they are not one per brightness value, or when
            any is not a finite number above zero. The searches then fall
            back to a single scatter estimated from the light curve.
        """
        photometry = star.photometry
        if photometry.fluxes_detrended:
            values, errors = photometry.fluxes_detrended, photometry.fluxes_detrended_errors
        else:
            values, errors = photometry.fluxes_normalized, photometry.fluxes_normalized_errors
        if not errors or len(errors) != len(values):
            return None
        error_array = np.array(errors, dtype=float)
        if not np.all(np.isfinite(error_array)) or np.any(error_array <= 0):
            return None
        count = min(len(photometry.timestamps), error_array.size)
        return error_array[:count]
