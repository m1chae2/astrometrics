"""Looks for named absorption features (dips) in a star's spectrum.

A slitless grism at amateur resolution (R~100-300, roughly 2-5 nm per
resolution element) can't resolve individual metal lines the way a lab
spectrograph can, but it can resolve broad, well-separated features --
the hydrogen Balmer series, the Ca II H&K blend, the Mg b and Na D
blends -- as a measurable dip against the surrounding continuum. This
module measures a dip at each feature's known wavelength and then asks
the question that matters: *how often would noise, or the ordinary
wiggles in this same spectrum, produce a dip this deep?*

That question is answered with control measurements. The same dip
measurement is repeated at many "control" wavelengths in this spectrum
where none of the named features live. Those controls show how much this
particular spectrum really wiggles: its noise level, its oversampling
(neighboring samples are not independent) and its real structure. The
spread of the control results sets the width of the noise, and the
p-value is the chance that noise of that width produces a dip at least as
significant as the real one, after allowing for the several centers tried
inside the search tolerance.

Each feature is also compared with the depth a star of the expected
spectral type should show (measured on the bundled reference spectra the
same way), so "expected but absent" and "present but unexpected" are
visible instead of every dip looking equally meaningful.

This is a heuristic detector, not a chemical-abundance measurement. The
verdicts say how surprising a dip is; they do not measure how much of an
element is present.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats
from scipy.ndimage import gaussian_filter1d

# Rest wavelengths of features broad or strong enough for a low-resolution
# slitless grism to plausibly resolve as a distinct dip. window_angstrom is
# the half-width of the feature's own core; the continuum is measured on
# the bands just outside that core on each side.
NAMED_FEATURES: tuple[dict[str, object], ...] = (
    {"name": "Hydrogen Balmer series (H-alpha)", "wavelength_angstrom": 6563.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-beta)", "wavelength_angstrom": 4861.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-gamma)", "wavelength_angstrom": 4340.0, "window_angstrom": 20.0},
    {"name": "Hydrogen Balmer series (H-delta)", "wavelength_angstrom": 4102.0, "window_angstrom": 20.0},
    {"name": "Calcium II H & K", "wavelength_angstrom": 3950.0, "window_angstrom": 35.0},
    {"name": "Magnesium b triplet", "wavelength_angstrom": 5175.0, "window_angstrom": 20.0},
    {"name": "Sodium D doublet", "wavelength_angstrom": 5893.0, "window_angstrom": 15.0},
    {"name": "Iron/titanium blend (G band)", "wavelength_angstrom": 4300.0, "window_angstrom": 20.0},
)

# The three answers this module gives for each feature.
VERDICT_DETECTED = "detected"
VERDICT_POSSIBLE = "possible"
VERDICT_NOT_DETECTED = "not_detected"
# The spectrum does not reach the feature (or has too few points there).
VERDICT_NOT_COVERED = "not_covered"

# How wide one independent measurement is, in Angstroms. A slitless grism
# at R~100-300 has a resolution element of about lambda/R = 5000/150 =
# ~33 A near the middle of the visible range, so 30 A is used. Two
# samples closer together than this are not independent measurements, which
# is why the depth's uncertainty is scaled by the number of resolution
# elements in a feature's core, not by the number of samples.
DEFAULT_RESOLUTION_ELEMENT_ANGSTROM = 30.0

# How far from its rest wavelength a feature's dip may be centered and
# still count. The wavelength calibration of the instrument is good to
# about 3 nm (Vega's Balmer dips fit with 0.3 nm RMS after tuning, so 30 A
# leaves generous room for the untuned case). The best dip inside this
# window is used, and the control test searches the same size of window.
CENTER_SEARCH_TOLERANCE_ANGSTROM = 30.0

# Degree of the polynomial fitted as the continuum. 2 (a gentle curve)
# rather than 1 (a straight line): the instrument's own response bends the
# continuum, and on the Vega, Alcor and Alnath master stacks a straight
# line left that bend behind as "noise" and made obvious Balmer dips look
# marginal.
CONTINUUM_POLYNOMIAL_DEGREE = 2

# The continuum is a curve fitted to two bands on either side of
# the core. Each band starts one half-window from the center and ends
# this many half-windows out, so it is wide enough to fit a line but
# narrow enough that a quadratic can follow the continuum.
SHOULDER_OUTER_HALF_WINDOWS = 4.0

# Fewest samples allowed in a continuum band (both bands together must
# also give the line fit at least this many points) and in a core; below
# this the numbers are too noisy to trust.
_MINIMUM_SHOULDER_POINTS = 8
_MINIMUM_CORE_POINTS = 2

# A dip shallower than this fraction of the continuum is never reported,
# however statistically clean. A 1% dip is below what the standard-star
# checks in validate_spectral_and_period_analysis.py can tell from small
# calibration and response errors.
MINIMUM_REPORTED_DEPTH = 0.01

# p-value cutoffs for the verdicts. 0.01 means noise like this spectrum's
# gives a dip this significant about 1 time in 100. Eight features are
# tested per spectrum, so a pure-noise spectrum still gives roughly a 1 in
# 12 chance of one false "detected". The synthetic pure-noise check in
# validate_spectral_and_period_analysis.py measures the real rate.
DETECTED_P_VALUE = 0.01
POSSIBLE_P_VALUE = 0.05

# Number of control positions needed before the noise width is measured
# from them. With fewer, the width cannot be measured reliably, so the
# depth's own uncertainty is trusted as it is and the p-value is labeled
# as "gaussian" instead of "control_calibrated".
MINIMUM_CONTROL_POSITIONS = 20

# Spacing of the control positions, in multiples of the search tolerance.
# Half the tolerance gives about 250 controls across a 3800-8000 A
# spectrum. Neighboring controls overlap, so they are not independent
# measurements; they are only used to measure the spread of the noise.
_CONTROL_SPACING_TOLERANCES = 0.5

# The fitted noise width is never allowed to be narrower than the depth's
# own uncertainty (a width of 1.0), so a spectrum that looks unusually
# quiet cannot make the test more trusting than the measurement itself.
_MINIMUM_NOISE_WIDTH = 1.0

# The fitted Gumbel width (how spread out the best-of-several significances
# are) is never allowed below this fraction of one significance unit, so a
# spectrum whose controls happen to look identical cannot produce an
# infinitely confident p-value.
_GUMBEL_WIDTH_FLOOR_FRACTION = 0.25

# How uncertain the reference spectrum's expected depth is, as a fraction
# of that depth. A star's real line strength differs from its spectral
# type's template (luminosity class, metallicity, catalog type rounded to
# the nearest bundled rung), and 50% is a deliberately wide guess.
_EXPECTED_DEPTH_FRACTIONAL_UNCERTAINTY = 0.5

# Prior chance a line is present. 0.5 when the expected type predicts a
# clear dip, 0.1 when it predicts essentially none. These are not
# calibrated; they only stop the probability from being driven purely by
# the data when the type says the line should be absent.
_PRIOR_PRESENT_WHEN_EXPECTED = 0.5
_PRIOR_PRESENT_WHEN_NOT_EXPECTED = 0.1

# Fewest valid points needed to attempt any feature.
_MINIMUM_SPECTRUM_POINTS = 30


@dataclass(frozen=True)
class _DipMeasurement:
    """One dip measured against its local continuum.

    Attributes
    ----------
    center_angstrom : `float`
        Where the core of the dip was centered.
    depth : `float`
        How far below the local continuum the core is, as a fraction of
        the continuum (0.05 means 5% dimmer).
    depth_uncertainty : `float`
        The one-sigma uncertainty of `depth`.
    significance : `float`
        `depth` divided by `depth_uncertainty`.
    """

    center_angstrom: float
    depth: float
    depth_uncertainty: float
    significance: float


def _measure_dip(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    center: float,
    half_window: float,
    resolution_element_angstrom: float,
) -> _DipMeasurement | None:
    """Measure how deep the spectrum dips at one center.

    A straight line is fitted through the bands on either side of the
    core. That line is the continuum, the level the spectrum would have
    without the feature. The dip is the core's average shortfall below it,
    and its uncertainty comes from how far the band samples scatter about
    the fitted line.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        Wavelengths, sorted, in Angstroms.
    intensity : `np.ndarray`
        The brightness at each wavelength.
    center : `float`
        Where to center the core, in Angstroms.
    half_window : `float`
        The core's half-width, in Angstroms.
    resolution_element_angstrom : `float`
        The width of one independent measurement, in Angstroms.

    Returns
    -------
    measurement : `_DipMeasurement` or `None`
        The measurement, or `None` when there are too few samples in the
        bands or the core, or the continuum is not positive.
    """
    outer = SHOULDER_OUTER_HALF_WINDOWS * half_window
    distance = np.abs(wavelength_angstrom - center)
    in_core = distance <= half_window
    in_shoulder = (distance > half_window) & (distance <= outer)

    if in_core.sum() < _MINIMUM_CORE_POINTS or in_shoulder.sum() < _MINIMUM_SHOULDER_POINTS:
        return None
    # Both sides must contribute, or the line would just extrapolate.
    shoulder_wavelength = wavelength_angstrom[in_shoulder]
    if not ((shoulder_wavelength < center).any() and (shoulder_wavelength > center).any()):
        return None

    # Centering the wavelengths on the feature keeps the fit well behaved.
    shoulder_offsets = shoulder_wavelength - center
    coefficients = np.polyfit(shoulder_offsets, intensity[in_shoulder], CONTINUUM_POLYNOMIAL_DEGREE)
    residuals = intensity[in_shoulder] - np.polyval(coefficients, shoulder_offsets)
    continuum_at_core = np.polyval(coefficients, wavelength_angstrom[in_core] - center)
    mean_continuum = float(np.mean(continuum_at_core))
    if mean_continuum <= 0:
        return None

    # The fit used (degree + 1) numbers, so the scatter is divided by
    # (count - that many), the usual correction for a fitted curve.
    fitted_numbers = CONTINUUM_POLYNOMIAL_DEGREE + 1
    scatter = float(np.sqrt(np.sum(residuals**2) / max(1, residuals.size - fitted_numbers)))
    relative_scatter = scatter / mean_continuum

    depth = float(np.mean((continuum_at_core - intensity[in_core]) / continuum_at_core))
    independent_measurements = max(
        1.0, min(float(in_core.sum()), 2.0 * half_window / resolution_element_angstrom)
    )
    depth_uncertainty = relative_scatter / math.sqrt(independent_measurements)
    if depth_uncertainty <= 0:
        return None

    return _DipMeasurement(center, depth, depth_uncertainty, depth / depth_uncertainty)


def _candidate_dips(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    nominal_center: float,
    half_window: float,
    resolution_element_angstrom: float,
    tolerance_angstrom: float,
) -> list[_DipMeasurement]:
    """Measure the dip at every candidate center near a nominal center.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        Wavelengths, sorted, in Angstroms.
    intensity : `np.ndarray`
        The brightness at each wavelength.
    nominal_center : `float`
        The wavelength to search around, in Angstroms.
    half_window : `float`
        The core's half-width, in Angstroms.
    resolution_element_angstrom : `float`
        The width of one independent measurement, in Angstroms.
    tolerance_angstrom : `float`
        How far either side of `nominal_center` to try, in Angstroms.

    Returns
    -------
    candidates : `list` [`_DipMeasurement`]
        One measurement for each candidate center that could be measured.
    """
    step = max(5.0, float(np.median(np.diff(wavelength_angstrom))))
    candidate_centers = np.arange(
        nominal_center - tolerance_angstrom, nominal_center + tolerance_angstrom + 1e-9, step
    )
    measurements = []
    for candidate_center in candidate_centers:
        measurement = _measure_dip(
            wavelength_angstrom, intensity, float(candidate_center), half_window, resolution_element_angstrom
        )
        if measurement is not None:
            measurements.append(measurement)
    return measurements


def _control_centers(
    wavelength_angstrom: np.ndarray, half_window: float, tolerance_angstrom: float
) -> np.ndarray:
    """Choose wavelengths for the control measurements.

    Controls are spread across the spectrum, keep clear of the edges (so a
    full search window and continuum band fit), and keep clear of every
    named feature so a real feature is never counted as noise.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        Wavelengths, sorted, in Angstroms.
    half_window : `float`
        The core's half-width, in Angstroms.
    tolerance_angstrom : `float`
        The search tolerance, in Angstroms.

    Returns
    -------
    centers : `np.ndarray`
        Control center wavelengths, in Angstroms.
    """
    reach = tolerance_angstrom + SHOULDER_OUTER_HALF_WINDOWS * half_window
    first = wavelength_angstrom[0] + reach
    last = wavelength_angstrom[-1] - reach
    if last <= first:
        return np.array([])
    centers = np.arange(first, last + 1e-9, _CONTROL_SPACING_TOLERANCES * tolerance_angstrom)
    keep_clear = tolerance_angstrom + SHOULDER_OUTER_HALF_WINDOWS * 35.0  # the widest named core is 35 A
    feature_centers = np.array([float(feature["wavelength_angstrom"]) for feature in NAMED_FEATURES])
    far_from_features = np.all(np.abs(centers[:, None] - feature_centers[None, :]) > keep_clear, axis=1)
    return centers[far_from_features]


def _upper_tail_gaussian(significance: float) -> float:
    """Give the chance a standard normal value is at least `significance`.

    Returns
    -------
    probability : `float`
        The one-sided tail probability.
    """
    return 0.5 * math.erfc(significance / math.sqrt(2.0))


def _normal_density(value: float, mean: float, standard_deviation: float) -> float:
    """Give the normal probability density at `value`.

    Returns
    -------
    density : `float`
        The height of the normal curve at `value`.
    """
    standard_deviation = max(standard_deviation, 1e-9)
    z = (value - mean) / standard_deviation
    return math.exp(-0.5 * z * z) / (standard_deviation * math.sqrt(2.0 * math.pi))


def expected_feature_depth(
    reference_spectral_type: str,
    feature_name: str,
    resolution_element_angstrom: float = DEFAULT_RESOLUTION_ELEMENT_ANGSTROM,
) -> float | None:
    """Measure how deep a feature should be for a star of a given type.

    The bundled reference spectrum for the type is smoothed to the
    instrument's resolution and measured with exactly the same dip
    measurement used on the real spectrum.

    Parameters
    ----------
    reference_spectral_type : `str`
        A bundled reference type, such as ``"A0V"``.
    feature_name : `str`
        A name from `NAMED_FEATURES`.
    resolution_element_angstrom : `float`, optional
        The instrument's resolution element, in Angstroms.

    Returns
    -------
    depth : `float` or `None`
        The fractional depth the template shows, or `None` if the type
        has no template or the template does not cover the feature.
    """
    from astrometricslib.pipelines.spectroscopy.spectral_classifier import _get_reference_templates

    templates = _get_reference_templates()
    if reference_spectral_type not in templates:
        return None
    template_wavelength, template_flux = templates[reference_spectral_type]
    sample_spacing = float(np.median(np.diff(template_wavelength)))
    # A gaussian's width (sigma) is its FWHM divided by 2.355.
    smoothed = gaussian_filter1d(template_flux, resolution_element_angstrom / 2.355 / sample_spacing)

    for feature in NAMED_FEATURES:
        if feature["name"] == feature_name:
            measurement = _measure_dip(
                template_wavelength,
                smoothed,
                float(feature["wavelength_angstrom"]),
                float(feature["window_angstrom"]),
                resolution_element_angstrom,
            )
            # A template's own wiggles can dip "above" the fitted
            # continuum; a negative expected depth would mean nothing, so
            # it is cut at zero.
            return None if measurement is None else max(measurement.depth, 0.0)
    return None


def detect_named_features(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    reference_spectral_type: str | None = None,
    resolution_element_angstrom: float = DEFAULT_RESOLUTION_ELEMENT_ANGSTROM,
) -> list[dict[str, object]]:
    """Test a spectrum for each named absorption feature.

    For every feature in `NAMED_FEATURES` the spectrum is searched near
    the rest wavelength for the most significant dip, the dip is compared
    with control measurements from featureless parts of the same spectrum
    to get a p-value, and the verdict is set from that p-value.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelength grid, in Angstroms.
    intensity : `np.ndarray`
        The observed spectrum's brightness at each wavelength.
    reference_spectral_type : `str`, optional
        A bundled reference type (for example ``"A2V"``) saying what kind
        of star this should be. When given, each entry also says how deep
        the feature should be for that type and how likely the line is to
        be present. Leave `None` when the type is not known.
    resolution_element_angstrom : `float`, optional
        The width of one independent measurement, in Angstroms.

    Returns
    -------
    features : `list` [`dict`]
        One entry per named feature, most convincing first. Every entry
        has ``"feature"`` (name), ``"wavelength_angstrom"`` (its rest
        wavelength) and ``"verdict"`` (`VERDICT_DETECTED`,
        `VERDICT_POSSIBLE`, `VERDICT_NOT_DETECTED` or
        `VERDICT_NOT_COVERED`). A covered feature also has
        ``"measured_wavelength_angstrom"`` (where the best dip is),
        ``"depth"``, ``"depth_uncertainty"``, ``"significance"``,
        ``"p_value"`` (the chance that noise like this spectrum's gives
        a dip at least this significant), ``"p_value_method"``
        (``"control_calibrated"``, or ``"gaussian"`` when too few control
        positions were available), ``"expected_depth"`` and
        ``"probability_present"`` (both `None` without a reference type).
        The probability assumes the star really is the reference type and
        the 50% and prior settings above; it is not a calibrated
        probability.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    valid = np.isfinite(wavelength_angstrom) & np.isfinite(intensity)
    wavelength_angstrom = wavelength_angstrom[valid]
    intensity = intensity[valid]
    if wavelength_angstrom.size < _MINIMUM_SPECTRUM_POINTS:
        return []

    order = np.argsort(wavelength_angstrom)
    wavelength_angstrom = wavelength_angstrom[order]
    intensity = intensity[order]

    entries: list[dict[str, object]] = []
    for feature in NAMED_FEATURES:
        name = str(feature["name"])
        rest_wavelength = float(feature["wavelength_angstrom"])
        half_window = float(feature["window_angstrom"])
        entry: dict[str, object] = {
            "feature": name,
            "wavelength_angstrom": rest_wavelength,
            "verdict": VERDICT_NOT_COVERED,
        }

        observed_candidates = _candidate_dips(
            wavelength_angstrom,
            intensity,
            rest_wavelength,
            half_window,
            resolution_element_angstrom,
            CENTER_SEARCH_TOLERANCE_ANGSTROM,
        )
        if not observed_candidates:
            entries.append(entry)
            continue
        observed = max(observed_candidates, key=lambda candidate: candidate.significance)

        # Control measurements: the same search, repeated where no named
        # feature lives. `control_best_depths` is each control's best dip;
        # `control_significances` pools every candidate center, to measure
        # how wide the noise is.
        control_best_depths = []
        control_significances = []
        for control_center in _control_centers(
            wavelength_angstrom, half_window, CENTER_SEARCH_TOLERANCE_ANGSTROM
        ):
            control_candidates = _candidate_dips(
                wavelength_angstrom,
                intensity,
                float(control_center),
                half_window,
                resolution_element_angstrom,
                CENTER_SEARCH_TOLERANCE_ANGSTROM,
            )
            if control_candidates:
                best_control = max(control_candidates, key=lambda candidate: candidate.significance)
                control_best_depths.append(best_control.depth)
                control_significances.append(best_control.significance)

        if len(control_best_depths) >= MINIMUM_CONTROL_POSITIONS:
            # Each control's result is the best of several centers, exactly
            # like the real feature's, so how those "best of several"
            # numbers are spread already includes the price of trying
            # several centers. The largest of many noisy numbers follows
            # an extreme-value (Gumbel) curve, so one is fitted to them and
            # its upper tail gives the p-value. The tail can be reached far
            # below 1 / (controls + 1), which counting alone cannot.
            location, width = stats.gumbel_r.fit(np.array(control_significances))
            width = max(float(width), _MINIMUM_NOISE_WIDTH * _GUMBEL_WIDTH_FLOOR_FRACTION)
            p_value = float(stats.gumbel_r.sf(observed.significance, loc=location, scale=width))
            p_value_method = "control_calibrated"
            likelihood_absent = float(stats.gumbel_r.pdf(observed.significance, loc=location, scale=width))
        else:
            # Too few controls to fit anything: assume Gaussian noise, and
            # pay for the several centers tried with the Sidak formula
            # (independent centers = tolerance window / resolution element).
            independent_candidates = max(
                1, round(2.0 * CENTER_SEARCH_TOLERANCE_ANGSTROM / resolution_element_angstrom) + 1
            )
            single_chance = _upper_tail_gaussian(max(observed.significance, 0.0))
            p_value = float(1.0 - (1.0 - single_chance) ** independent_candidates)
            p_value_method = "gaussian"
            likelihood_absent = _normal_density(observed.significance, 0.0, 1.0)

        expected_depth = None
        probability_present = None
        if reference_spectral_type:
            expected_depth = expected_feature_depth(
                reference_spectral_type, name, resolution_element_angstrom
            )
        if expected_depth is not None:
            # Both hypotheses are compared on the significance scale, the
            # same scale the p-value uses, so the two numbers agree. If the
            # line is present, the significance should be near the depth the
            # reference type predicts divided by this measurement's
            # uncertainty; the spread allows one unit of measurement noise
            # plus the uncertainty in the expected depth itself.
            expected_significance = max(expected_depth, 0.0) / observed.depth_uncertainty
            spread_present = math.hypot(1.0, _EXPECTED_DEPTH_FRACTIONAL_UNCERTAINTY * expected_significance)
            likelihood_present = _normal_density(observed.significance, expected_significance, spread_present)
            prior = (
                _PRIOR_PRESENT_WHEN_EXPECTED
                if expected_depth >= MINIMUM_REPORTED_DEPTH
                else _PRIOR_PRESENT_WHEN_NOT_EXPECTED
            )
            denominator = prior * likelihood_present + (1.0 - prior) * likelihood_absent
            probability_present = float(prior * likelihood_present / denominator) if denominator > 0 else None

        if observed.depth >= MINIMUM_REPORTED_DEPTH and p_value <= DETECTED_P_VALUE:
            verdict = VERDICT_DETECTED
        elif observed.depth >= MINIMUM_REPORTED_DEPTH and p_value <= POSSIBLE_P_VALUE:
            verdict = VERDICT_POSSIBLE
        else:
            verdict = VERDICT_NOT_DETECTED

        entry.update({
            "verdict": verdict,
            "measured_wavelength_angstrom": observed.center_angstrom,
            "depth": observed.depth,
            "depth_uncertainty": observed.depth_uncertainty,
            "significance": observed.significance,
            "p_value": p_value,
            "p_value_method": p_value_method,
            "expected_depth": expected_depth,
            "probability_present": probability_present,
        })
        entries.append(entry)

    verdict_rank = {VERDICT_DETECTED: 0, VERDICT_POSSIBLE: 1, VERDICT_NOT_DETECTED: 2, VERDICT_NOT_COVERED: 3}
    entries.sort(key=lambda entry: (verdict_rank[str(entry["verdict"])], float(entry.get("p_value", 1.0))))
    return entries
