"""Variability indices, a noise model of the field, and the candidate rule.

The photometry pipeline asks whether a star's light curve changes by more
than its measurement noise explains. The coefficient of variation (CV, the
standard deviation over the mean) cannot answer that: it grows for faint
stars simply because their measurements are noisier, so faint stars fill
the flagged set. This module adds three things.

1. A noise model of the field (`NoiseModel`). It gives the scatter that a
   constant star of a given brightness shows in this field, found from the
   field's own stars.
2. Three indices per star, each in a fixed unit:

   * ``excess_scatter``: measured scatter over the scatter the noise model
     expects at the star's brightness. No unit. A constant star is near 1.
   * ``reduced_chi_square``: the sum of squared deviations from the star's
     weighted mean, in units of each point's error, divided by the number
     of points minus one. No unit. A constant star with correct errors is
     near 1.
   * ``stetson_j``: the Stetson (1996) J index, which multiplies the
     deviations of neighbouring points. Noise averages out of those
     products and a smooth change adds up. No unit. A constant star is
     near 0.

3. The candidate rule (`assess_variability`): a star is a candidate when
   its reduced chi-square, its Stetson J and its excess scatter all exceed
   a threshold. The thresholds on chi-square and J are calibrated from the
   field. The one on excess scatter is fixed. The chi-square and J use the
   propagated error of each point, raised to the noise-model level when the
   propagated errors are smaller than the scatter the field's constant stars
   show (see `scaled_errors`).

Limits: the noise model assumes that most stars in each brightness bin are
constant. A field with fewer than `MINIMUM_STARS_FOR_NOISE_MODEL` stars with
usable light curves has no model, and the caller falls back to the CV
cutoff.

Reference: Stetson, P. B. 1996, PASP, 108, 851, "On the Automatic
Determination of Light-Curve Parameters for Cepheid Variables".
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
from scipy.stats import median_abs_deviation

from astrometricslib.models.stellar_source import StellarObject

# Converts a fractional flux change to magnitudes: 2.5 / ln(10) = 1.0857.
MAGNITUDES_PER_FRACTION = 2.5 / math.log(10.0)

# The fewest usable points a light curve needs for the indices. Stetson J
# multiplies neighbouring points and the chi-square has n - 1 degrees of
# freedom, so a handful of points say little.
MINIMUM_POINTS_FOR_INDICES = 5

# The fewest stars the noise model needs. Each bin of the model holds at
# least `MINIMUM_STARS_PER_NOISE_BIN` stars, and the model has at least two
# bins so that it can follow a trend with brightness.
MINIMUM_STARS_PER_NOISE_BIN = 10
MINIMUM_STARS_FOR_NOISE_MODEL = 2 * MINIMUM_STARS_PER_NOISE_BIN
MAXIMUM_NOISE_BINS = 10

# A star is a candidate only when it scatters at least this many times the
# scatter expected at its brightness. A design value: 1.5 is a 50 percent
# excess, large enough that the ordinary spread of the measured scatter of a
# constant star (about 1 / sqrt(2 (n - 1)), 10 to 20 percent for 25 to 50
# points) rarely reaches it.
EXCESS_SCATTER_THRESHOLD = 1.5

# The number of robust standard deviations above the field's median at
# which the chi-square and J thresholds sit. 2.326 is the one-sided 99th
# percentile of a normal distribution.
CALIBRATION_SIGMAS = 2.326

# Converts a median absolute deviation to a standard deviation for a
# normal distribution.
_MAD_TO_SIGMA = 1.4826


@dataclass(frozen=True)
class NoiseModel:
    """The scatter a constant star shows in this field, by brightness.

    The model is a curve through the median scatter of the field's stars in
    bins of equal star count. Between bin centres it interpolates the
    logarithm of the scatter linearly in magnitude. Brighter than the
    brightest bin it stays flat. Fainter than the faintest bin it continues
    with the slope of the last two bins, and never falls.

    Attributes
    ----------
    magnitudes : `tuple` [`float`]
        The median instrumental magnitude of each bin, brightest first.
    rms_mag : `tuple` [`float`]
        The median scatter of each bin, in magnitudes, made to rise
        (or stay level) with magnitude.
    star_counts : `tuple` [`int`]
        The number of stars in each bin.
    """

    magnitudes: tuple[float, ...]
    rms_mag: tuple[float, ...]
    star_counts: tuple[int, ...]

    def expected_rms_mag(self, magnitude: float) -> float:
        """Give the scatter a constant star of this brightness shows.

        Parameters
        ----------
        magnitude : `float`
            The star's mean instrumental magnitude.

        Returns
        -------
        rms_mag : `float`
            The expected scatter, in magnitudes.
        """
        mags, rms = self.magnitudes, self.rms_mag
        if len(mags) == 1 or magnitude <= mags[0]:
            return rms[0]
        if magnitude >= mags[-1]:
            slope = max(0.0, (math.log(rms[-1]) - math.log(rms[-2])) / max(mags[-1] - mags[-2], 1e-6))
            return float(rms[-1] * math.exp(slope * (magnitude - mags[-1])))
        return float(math.exp(np.interp(magnitude, mags, np.log(rms))))


def fit_noise_model(instrumental_magnitudes: Sequence[float], rms_mag: Sequence[float]) -> NoiseModel | None:
    """Fit the field's scatter-against-brightness curve.

    The stars are sorted by magnitude and cut into bins of equal star count
    (at least `MINIMUM_STARS_PER_NOISE_BIN` stars each, at most
    `MAXIMUM_NOISE_BINS` bins). The median scatter of each bin is robust
    against the few variable stars the field holds. A running maximum in
    magnitude order makes the curve rise or stay level with magnitude.

    Parameters
    ----------
    instrumental_magnitudes : `Sequence` [`float`]
        Each star's mean instrumental magnitude, ``-2.5 log10`` of its
        mean flux in ADU per second.
    rms_mag : `Sequence` [`float`]
        Each star's measured scatter, in magnitudes.

    Returns
    -------
    model : `NoiseModel` or `None`
        The fitted curve, or `None` when fewer than
        `MINIMUM_STARS_FOR_NOISE_MODEL` stars have a finite positive scatter.
    """
    mags = np.asarray(instrumental_magnitudes, dtype=float)
    rms = np.asarray(rms_mag, dtype=float)
    keep = np.isfinite(mags) & np.isfinite(rms) & (rms > 0.0)
    mags, rms = mags[keep], rms[keep]
    if mags.size < MINIMUM_STARS_FOR_NOISE_MODEL:
        return None
    order = np.argsort(mags)
    mags, rms = mags[order], rms[order]
    bin_count = min(MAXIMUM_NOISE_BINS, mags.size // MINIMUM_STARS_PER_NOISE_BIN)
    groups = np.array_split(np.arange(mags.size), bin_count)
    centres = tuple(float(np.median(mags[group])) for group in groups)
    medians = np.maximum.accumulate([float(np.median(rms[group])) for group in groups])
    counts = tuple(int(group.size) for group in groups)
    return NoiseModel(centres, tuple(float(value) for value in medians), counts)


def reduced_chi_square(values: Sequence[float], errors: Sequence[float]) -> float:
    """Measure how far a light curve is from constant, in units of its errors.

    The constant is the error-weighted mean of the values. The result is
    ``sum(((x_i - mean) / sigma_i)**2) / (n - 1)``. For a constant star
    with correct errors it averages 1. Larger means more change than the
    errors explain.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The brightness values.
    errors : `Sequence` [`float`]
        The 1-sigma error of each value, same unit, all above zero.

    Returns
    -------
    chi_square : `float`
        The reduced chi-square, with ``n - 1`` degrees of freedom.
    """
    x = np.asarray(values, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    weights = 1.0 / sigma**2
    mean = float(np.sum(weights * x) / np.sum(weights))
    return float(np.sum(((x - mean) / sigma) ** 2) / (x.size - 1))


def stetson_j(values: Sequence[float], errors: Sequence[float]) -> float:
    """Measure how much neighbouring points change together (Stetson 1996).

    Each point gets a normalized residual
    ``delta_i = sqrt(n / (n - 1)) * (x_i - mean) / sigma_i``, with the
    error-weighted mean. For each pair of consecutive points the product
    ``P = delta_i * delta_(i+1)`` is taken, and the index is the average of
    ``sign(P) * sqrt(|P|)`` over the ``n - 1`` pairs (every pair has weight
    1; Stetson's weights for unevenly spaced times are not used). Noise
    gives products of random sign, so the index stays near 0. A smooth
    change gives products that are mostly positive, so the index is
    positive. A single bad point gives two products of opposite sign and
    adds little. The square root keeps one very large product from
    dominating.

    Parameters
    ----------
    values : `Sequence` [`float`]
        The brightness values in time order.
    errors : `Sequence` [`float`]
        The 1-sigma error of each value, same unit, all above zero.

    Returns
    -------
    index : `float`
        The J index. About 0 for a constant star, positive for a
        correlated change, and negative for a series that alternates.

    References
    ----------
    Stetson, P. B. 1996, PASP, 108, 851.
    """
    x = np.asarray(values, dtype=float)
    sigma = np.asarray(errors, dtype=float)
    count = x.size
    weights = 1.0 / sigma**2
    mean = float(np.sum(weights * x) / np.sum(weights))
    delta = math.sqrt(count / (count - 1)) * (x - mean) / sigma
    products = delta[:-1] * delta[1:]
    return float(np.mean(np.sign(products) * np.sqrt(np.abs(products))))


@dataclass(frozen=True)
class VariabilityThresholds:
    """The three thresholds of the candidate rule, for one field.

    Attributes
    ----------
    reduced_chi_square : `float`
        A candidate's reduced chi-square must exceed this.
    stetson_j : `float`
        A candidate's Stetson J must exceed this.
    excess_scatter : `float`
        A candidate's excess scatter must exceed this.
    """

    reduced_chi_square: float
    stetson_j: float
    excess_scatter: float


def calibrate_thresholds(
    chi_squares: Sequence[float],
    stetson_js: Sequence[float],
    sigmas: float = CALIBRATION_SIGMAS,
) -> VariabilityThresholds:
    """Set the chi-square and J thresholds from the field's own stars.

    Both thresholds are the field's median plus `sigmas` robust standard
    deviations (1.4826 times the median absolute deviation, which a few
    variable stars barely move). The chi-square is measured as its
    logarithm, because its spread is proportional to its level. This
    absorbs a field whose errors are mis-scaled (for example, an assumed
    gain): all its chi-squares are high together, and the threshold rises
    with them. The excess-scatter threshold is the fixed
    `EXCESS_SCATTER_THRESHOLD`.

    Parameters
    ----------
    chi_squares : `Sequence` [`float`]
        The reduced chi-square of every star in the field.
    stetson_js : `Sequence` [`float`]
        The Stetson J of every star in the field.
    sigmas : `float`, optional
        How many robust standard deviations above the median.

    Returns
    -------
    thresholds : `VariabilityThresholds`
        The thresholds.
    """
    log_chi = np.log10(np.maximum(np.asarray(chi_squares, dtype=float), 1e-6))
    j_values = np.asarray(stetson_js, dtype=float)
    chi_threshold = 10.0 ** (
        float(np.median(log_chi)) + sigmas * _MAD_TO_SIGMA * float(median_abs_deviation(log_chi, scale=1.0))
    )
    j_threshold = float(np.median(j_values)) + sigmas * _MAD_TO_SIGMA * float(
        median_abs_deviation(j_values, scale=1.0)
    )
    return VariabilityThresholds(chi_threshold, j_threshold, EXCESS_SCATTER_THRESHOLD)


def variability_score(
    chi_square: float, j_index: float, excess_scatter: float, thresholds: VariabilityThresholds
) -> float:
    """Combine the three indices into one score for ranking.

    The score is the smaller of the chi-square and the excess scatter, each
    divided by its threshold. Stetson J acts as a veto: when it does not
    exceed its threshold, the score is capped at 1. The score is above 1
    exactly when the star passes all three thresholds, so ranking by the
    score and selecting by the rule agree. Stetson J does not enter the
    ranking itself, because dividing it into the minimum lowers the AUC on
    the synthetic fields (see the README), while its veto still keeps
    isolated bad points from making a candidate.

    Parameters
    ----------
    chi_square : `float`
        The star's reduced chi-square.
    j_index : `float`
        The star's Stetson J.
    excess_scatter : `float`
        The star's excess scatter.
    thresholds : `VariabilityThresholds`
        The field's thresholds.

    Returns
    -------
    score : `float`
        The score. Above 1 for a candidate.
    """
    score = min(chi_square / thresholds.reduced_chi_square, excess_scatter / thresholds.excess_scatter)
    return score if j_index > thresholds.stetson_j else min(score, 1.0)


@dataclass(frozen=True)
class LightCurveSeries:
    """The values and errors of one star that the indices are computed from.

    Attributes
    ----------
    values : `numpy.ndarray`
        The detrended (else normalized) brightness, positive and finite.
    errors : `numpy.ndarray` or `None`
        The 1-sigma error of each value, or `None` when the star has none.
    instrumental_magnitude : `float` or `None`
        ``-2.5 log10`` of the star's mean raw flux in ADU per second, or
        `None` when the star has no positive raw flux.
    rms_mag : `float`
        The scatter of the values, ``1.0857 * std / mean`` (the sample
        standard deviation), in magnitudes.
    error_mag : `float` or `None`
        The median propagated error, ``1.0857 * sigma / x``, in magnitudes,
        or `None` without errors.
    """

    values: np.ndarray
    errors: np.ndarray | None
    instrumental_magnitude: float | None
    rms_mag: float
    error_mag: float | None


def extract_series(star: StellarObject) -> LightCurveSeries | None:
    """Read the series the indices use from a star's light curve.

    The values are ``fluxes_detrended`` when present and
    ``fluxes_normalized`` otherwise, the same series the CV uses. Points
    that are not finite and above zero are dropped, together with their
    errors. Errors are used only when there is one per value and every
    kept one is finite and above zero.

    Parameters
    ----------
    star : `StellarObject`
        A star with a light curve.

    Returns
    -------
    series : `LightCurveSeries` or `None`
        The series, or `None` when fewer than `MINIMUM_POINTS_FOR_INDICES`
        usable points remain.
    """
    photometry = star.photometry
    if photometry is None:
        return None
    if photometry.fluxes_detrended:
        raw_values, raw_errors = photometry.fluxes_detrended, photometry.fluxes_detrended_errors
    else:
        raw_values, raw_errors = photometry.fluxes_normalized, photometry.fluxes_normalized_errors
    values = np.asarray(raw_values, dtype=float)
    keep = np.isfinite(values) & (values > 0.0)
    if int(keep.sum()) < MINIMUM_POINTS_FOR_INDICES:
        return None
    errors: np.ndarray | None = None
    if len(raw_errors) == values.size:
        candidate = np.asarray(raw_errors, dtype=float)[keep]
        if np.all(np.isfinite(candidate)) and np.all(candidate > 0.0):
            errors = candidate
    values = values[keep]
    mean = float(np.mean(values))
    rms_mag = MAGNITUDES_PER_FRACTION * float(np.std(values, ddof=1)) / mean
    error_mag = None if errors is None else float(np.median(MAGNITUDES_PER_FRACTION * errors / values))
    raw_fluxes = np.asarray(photometry.fluxes, dtype=float)
    raw_fluxes = raw_fluxes[np.isfinite(raw_fluxes) & (raw_fluxes > 0.0)]
    magnitude = None if raw_fluxes.size == 0 else float(-2.5 * math.log10(float(np.mean(raw_fluxes))))
    return LightCurveSeries(values, errors, magnitude, rms_mag, error_mag)


def expected_rms_mag(model: NoiseModel, series: LightCurveSeries) -> float:
    """Give the scatter a constant star like this one would show.

    The expected scatter is the noise-model curve at the star's brightness,
    but never below the star's own propagated error: a star cannot scatter
    less than its measurement errors say.

    Parameters
    ----------
    model : `NoiseModel`
        The field's noise model.
    series : `LightCurveSeries`
        The star's series. A star with no raw flux uses the model at its
        brightest bin.

    Returns
    -------
    rms_mag : `float`
        The expected scatter, in magnitudes.
    """
    magnitude = series.instrumental_magnitude
    curve = model.expected_rms_mag(model.magnitudes[0] if magnitude is None else magnitude)
    return max(curve, series.error_mag or 0.0)


def scaled_errors(model: NoiseModel, series: LightCurveSeries) -> np.ndarray:
    """Give the errors the chi-square and Stetson J are measured against.

    A star's propagated errors can be smaller than the scatter its constant
    neighbours show: the errors leave out centroid jitter, flat-field errors
    and an assumed gain. When the noise model sits above the star's median
    propagated error, every error of the star is multiplied by the ratio of
    the two, so that a constant star of this brightness has a chi-square
    near 1. Errors that are already larger than the model are left as they
    are. A star with no errors gets a constant error equal to the model's
    scatter.

    Parameters
    ----------
    model : `NoiseModel`
        The field's noise model.
    series : `LightCurveSeries`
        The star's series.

    Returns
    -------
    errors : `numpy.ndarray`
        One error per value, in the unit of the values.
    """
    expected = expected_rms_mag(model, series)
    if series.errors is None or not series.error_mag:
        return np.full(series.values.size, expected / MAGNITUDES_PER_FRACTION * float(np.mean(series.values)))
    return series.errors * max(1.0, expected / series.error_mag)


def star_indices(model: NoiseModel, series: LightCurveSeries) -> tuple[float, float, float]:
    """Compute the three indices of one star.

    Parameters
    ----------
    model : `NoiseModel`
        The field's noise model.
    series : `LightCurveSeries`
        The star's series.

    Returns
    -------
    indices : `tuple` [`float`, `float`, `float`]
        The excess scatter, the reduced chi-square and the Stetson J, in
        that order.
    """
    errors = scaled_errors(model, series)
    return (
        series.rms_mag / expected_rms_mag(model, series),
        reduced_chi_square(series.values, errors),
        stetson_j(series.values, errors),
    )


@dataclass
class FieldAssessment:
    """What the candidate rule found in one field.

    Attributes
    ----------
    noise_model : `NoiseModel` or `None`
        The fitted noise model, `None` when the field is too small.
    thresholds : `VariabilityThresholds` or `None`
        The calibrated thresholds, `None` without a noise model.
    candidates : `list` [`StellarObject`]
        The stars that pass all three thresholds, in field order.
    """

    noise_model: NoiseModel | None
    thresholds: VariabilityThresholds | None
    candidates: list[StellarObject]


def assess_variability(stars: Sequence[StellarObject]) -> FieldAssessment:
    """Fit the noise model, compute the indices, and pick the candidates.

    The steps are:

    1. Read each star's series (`extract_series`).
    2. Fit the noise model on every star's instrumental magnitude and scatter.
    3. For each star, find its expected scatter and the excess scatter.
    4. Compute the reduced chi-square and Stetson J of each star against its
       scaled errors (`scaled_errors`).
    5. Calibrate the chi-square and J thresholds from the field.
    6. Set each star's score and mark the candidates.

    Each star's ``instrumental_mag``, ``rms_mag``, ``excess_scatter``,
    ``reduced_chi_square``, ``stetson_j`` and ``variability_score`` are
    written onto its light curve. With no noise model they are left as they
    were.

    Parameters
    ----------
    stars : `Sequence` [`StellarObject`]
        The field's stars.

    Returns
    -------
    assessment : `FieldAssessment`
        The model, the thresholds and the candidate stars.
    """
    series_by_index: dict[int, LightCurveSeries] = {}
    for index, star in enumerate(stars):
        series = extract_series(star)
        if series is not None and series.instrumental_magnitude is not None:
            series_by_index[index] = series
    model = fit_noise_model(
        [
            series.instrumental_magnitude
            for series in series_by_index.values()
            if series.instrumental_magnitude is not None
        ],
        [series.rms_mag for series in series_by_index.values()],
    )
    if model is None:
        return FieldAssessment(None, None, [])

    indices = {index: star_indices(model, series) for index, series in series_by_index.items()}
    thresholds = calibrate_thresholds(
        [value[1] for value in indices.values()], [value[2] for value in indices.values()]
    )
    candidates: list[StellarObject] = []
    for index, (excess, chi_square, j_index) in indices.items():
        photometry = stars[index].photometry
        series = series_by_index[index]
        score = variability_score(chi_square, j_index, excess, thresholds)
        photometry.instrumental_mag = series.instrumental_magnitude
        photometry.rms_mag = series.rms_mag
        photometry.excess_scatter = excess
        photometry.reduced_chi_square = chi_square
        photometry.stetson_j = j_index
        photometry.variability_score = score
        if score > 1.0:
            candidates.append(stars[index])
    return FieldAssessment(model, thresholds, candidates)
