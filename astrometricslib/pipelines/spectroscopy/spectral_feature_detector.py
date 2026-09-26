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

from astrometricslib.pipelines.spectroscopy.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    blur_sigma_in_samples,
)

# Rest wavelengths of features broad or strong enough for a low-resolution
# slitless grism to plausibly resolve as a distinct dip. window_angstrom is
# the half-width of the feature's own core; the continuum is measured on
# the bands just outside that core on each side.
#
# No core is narrower than 20 A. The two Na D lines are only 6 A apart, but
# at this resolution they blur into one dip tens of Angstroms wide, and a
# core narrower than the dip puts the continuum bands on the dip's own
# wings. Measured on the Vega-field star TYC 3105-899-1 (which has a clear
# dip there), a 15 A core gave depth 23% +/- 13% and a 48% chance of noise
# ("not seen"), while cores of 20, 30 and 40 A gave 28% +/- 2% (0.0%),
# 22% +/- 4% (1.7%) and 17% +/- 3% (2.0%). 20 A matches the narrowest of
# the other features (H-gamma, H-delta, Mg b, G band).
NAMED_FEATURES: tuple[dict[str, object], ...] = (
    {"name": "Hydrogen Balmer series (H-alpha)", "wavelength_angstrom": 6563.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-beta)", "wavelength_angstrom": 4861.0, "window_angstrom": 25.0},
    {"name": "Hydrogen Balmer series (H-gamma)", "wavelength_angstrom": 4340.0, "window_angstrom": 20.0},
    {"name": "Hydrogen Balmer series (H-delta)", "wavelength_angstrom": 4102.0, "window_angstrom": 20.0},
    {"name": "Calcium II H & K", "wavelength_angstrom": 3950.0, "window_angstrom": 35.0},
    {"name": "Magnesium b triplet", "wavelength_angstrom": 5175.0, "window_angstrom": 20.0},
    {"name": "Sodium D doublet", "wavelength_angstrom": 5893.0, "window_angstrom": 20.0},
    {"name": "Iron/titanium blend (G band)", "wavelength_angstrom": 4300.0, "window_angstrom": 20.0},
)

# The answers this module gives for each feature.
VERDICT_DETECTED = "detected"
VERDICT_POSSIBLE = "possible"
# A dip is measured at the line and is more than most noise wiggles, but
# this spectrum's noise is too large to call it real or rule it out.
VERDICT_INCONCLUSIVE = "inconclusive"
VERDICT_NOT_DETECTED = "not_detected"
# The spectrum does not reach the feature (or has too few points there).
VERDICT_NOT_COVERED = "not_covered"

# How far from its rest wavelength a feature's dip may be centered and
# still count. The wavelength calibration of the instrument is good to
# about 3 nm (Vega's Balmer dips fit with 0.3 nm RMS after tuning, so 30 A
# leaves generous room for the untuned case). The best dip inside this
# window is used, and the control test searches the same size of window.
CENTER_SEARCH_TOLERANCE_ANGSTROM = 30.0

# When two features share one dip (see `_mark_features_sharing_a_dip`), the
# reference type's expected depths decide which keeps it if they differ by more
# than this; otherwise the nearer rest wavelength does. Set to
# MINIMUM_REPORTED_DEPTH, the smallest depth the detector reports at all: a
# smaller difference is not a difference the detector reports anyway. On the
# bundled templates, H-gamma against the G band is 0.13 to 0.14 against 0.0 for
# A0V and A2V, and 0.0 against 0.08 for K0III and K3III (measured
# 2026-09-25); other pairs are not tested.
EXPECTED_DEPTH_TIE_BREAK_MARGIN = 0.01

# Degree of the polynomial fitted as the continuum. 2 (a gentle curve)
# rather than 1 (a straight line): the instrument's own response bends the
# continuum, and on the Vega, Alcor and Alnath master stacks a straight
# line left that bend behind as "noise" and made obvious Balmer dips look
# marginal.
CONTINUUM_POLYNOMIAL_DEGREE = 2

# The continuum is a curve fitted to two bands on either side of the core.
# Each band starts this many resolution elements from the center, or one
# half-window if that is farther. The instrument blurs a line to about one
# resolution element (45-50 A), and a blurred line's wings die away only
# after about 1.5 of them (a Gaussian is down to 0.2% of its depth at 1.5
# FWHM). Bands that start nearer, as they used to (one half-window, 25 A for
# H-beta), fit the wings as if they were continuum: the dip reads too
# shallow and the band scatter, which sets the uncertainty, includes the
# wings. On the Vega master stack (A0V) H-beta read 0.077 +/- 0.031 that
# way, and 0.134 +/- 0.008 with the bands starting at 75 A (the bundled A0V
# template gives 0.129 at 49 A resolution; a second Vega spectrum, 0.087 +/-
# 0.017 before, gave 0.134 +/- 0.008 too). Measured on those two spectra
# only, 2026-09-25.
SHOULDER_INNER_RESOLUTION_ELEMENTS = 1.5

# Each band is this many half-windows wide. It was 3 (from one half-window
# out to four), wide enough to fit a curve and narrow enough that a
# quadratic can follow the continuum; the width is unchanged.
SHOULDER_BAND_HALF_WINDOWS = 3.0

# Fewest samples allowed in a continuum band (both bands together must
# also give the continuum fit at least this many points) and in a core; below
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

# A feature that is neither detected nor possible is still called
# "inconclusive" (rather than "not detected") when its dip is at least
# this deep and noise like this spectrum's gives a dip that strong no more
# than this often. It means "worth a second look with more data", not
# "probably there".
#
# The p-value cutoff, 0.25, is one noise wiggle in four: the dip stands
# out from three quarters of the wiggles. A stricter cutoff would lose
# features that are visible by eye (on TYC 3105-899-1, Mg b has p = 0.07
# and Ca H & K p = 0.18). Because the p-values are calibrated, about a
# quarter of the features in ANY pure-noise spectrum fall below it, so the
# depth floor is what keeps quiet spectra clean. The depth floor, 6.5%, is a
# little below (0.88 of, the same proportion the old 5% floor had to its
# 5.7%) the median depth (7.3%) the bundled reference spectra show for the
# features that show a dip at all (>= 1%, 575 of the 720 type-and-feature
# pairs, 90 types), after blurring to the fallback resolution of 45 A
# (recomputed 2026-09-25 with the continuum bands starting 1.5 resolution
# elements out, see SHOULDER_INNER_RESOLUTION_ELEMENTS; with the old bands the
# same 720 pairs give a median of 4.9%, and the earlier 5.7% was for 272
# pairs on 2026-09-19; at the old 30 A blur it was 7.6%). A
# spectrum's own measured resolution moves this a little, but the floor
# only needs to sit near the typical depth. A shallower dip is below what
# a real star of most types shows.
#
# Measured with validate_spectral_and_period_analysis.py's synthetic
# spectra: 0.4% of features are called inconclusive in pure noise of 2%,
# 15% at 5%, 19% at 10% and 20% at 20% (about the p-value cutoff, reached
# once the noise is large enough that the depth floor no longer matters).
# On the 131 stored spectra, 17% of the 1,025 tested features are, and 95
# of the stars have at least one. (Measured before the resolution changed
# from 30 A to 45 A; not re-run since.)
INCONCLUSIVE_P_VALUE = 0.25
INCONCLUSIVE_MINIMUM_DEPTH = 0.065

# A feature measured with a depth uncertainty above this can be called no
# better than "inconclusive", however small its p-value. The uncertainty is
# the noise as a fraction of the continuum, so it is large where the
# spectrum is faint (typically the blue end of a red star, where the
# continuum falls toward zero). There a 2-sigma "possible" dip is more than
# 22% deep, four times the 5.7% median depth of a real feature (see
# INCONCLUSIVE_P_VALUE), and it is very unlikely to be a line. In the 160
# features of the stored catalog-typed spectra, verdicts above this
# uncertainty were both (2 of 2) for lines the catalog type does not expect,
# against 10-33% below it. 0.11 is twice the median depth. A small sample:
# not tested on a larger set.
MAXIMUM_UNCERTAINTY_FOR_A_VERDICT = 0.11

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


def _shoulder_edges(half_window: float, resolution_element_angstrom: float) -> tuple[float, float]:
    """Give how far from a feature's center its continuum bands run.

    Parameters
    ----------
    half_window : `float`
        The core's half-width, in Angstroms.
    resolution_element_angstrom : `float`
        The width of one independent measurement, in Angstroms.

    Returns
    -------
    inner, outer : `tuple` [`float`, `float`]
        The distances, in Angstroms, where each continuum band starts and
        ends (see `SHOULDER_INNER_RESOLUTION_ELEMENTS`).
    """
    inner = max(half_window, SHOULDER_INNER_RESOLUTION_ELEMENTS * resolution_element_angstrom)
    return inner, inner + SHOULDER_BAND_HALF_WINDOWS * half_window


def _measure_dip(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    center: float,
    half_window: float,
    resolution_element_angstrom: float,
) -> _DipMeasurement | None:
    """Measure how deep the spectrum dips at one center.

    A gentle curve is fitted through the bands on either side of the
    core: a polynomial of degree `CONTINUUM_POLYNOMIAL_DEGREE`, which is a
    quadratic (a simple bend) by default. That curve is the continuum, the
    level the spectrum would have without the feature. The dip is the
    core's average shortfall below it, and its uncertainty comes from how
    far the band samples scatter about the fitted curve.

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
    inner, outer = _shoulder_edges(half_window, resolution_element_angstrom)
    distance = np.abs(wavelength_angstrom - center)
    in_core = distance <= half_window
    in_shoulder = (distance > inner) & (distance <= outer)

    if in_core.sum() < _MINIMUM_CORE_POINTS or in_shoulder.sum() < _MINIMUM_SHOULDER_POINTS:
        return None
    # Both sides must contribute, or the curve would just extrapolate.
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
    wavelength_angstrom: np.ndarray,
    half_window: float,
    tolerance_angstrom: float,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
) -> np.ndarray:
    """Choose wavelengths for the control measurements.

    Controls are spread across the spectrum, keep clear of the edges (so a
    full search window and continuum band fit), and keep the search window
    and core clear of every named feature so a real feature is never counted
    as noise.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        Wavelengths, sorted, in Angstroms.
    half_window : `float`
        The core's half-width, in Angstroms.
    tolerance_angstrom : `float`
        The search tolerance, in Angstroms.
    resolution_element_angstrom : `float`, optional
        The width of one independent measurement, in Angstroms, which sets
        how far the continuum bands reach (see `_shoulder_edges`).

    Returns
    -------
    centers : `np.ndarray`
        Control center wavelengths, in Angstroms.
    """
    reach = tolerance_angstrom + _shoulder_edges(half_window, resolution_element_angstrom)[1]
    first = wavelength_angstrom[0] + reach
    last = wavelength_angstrom[-1] - reach
    if last <= first:
        return np.array([])
    centers = np.arange(first, last + 1e-9, _CONTROL_SPACING_TOLERANCES * tolerance_angstrom)
    # A control's search window and core must not reach a named feature's core.
    # Its continuum bands may reach a feature's wings: that makes the noise
    # distribution a little heavier where real lines are, which only makes
    # the p-values more cautious, and it leaves controls in the blue, where
    # the features are crowded. With the bands kept fully clear (as they were
    # until 2026-09-25) no control lay below 4558 A, and the blue features
    # (Ca H & K, H-delta) came out at 5-6% at p <= 0.01 in pure noise where
    # about 1% was expected.
    widest_core_half_window = 35.0  # the widest named core, Ca H & K
    keep_clear = tolerance_angstrom + half_window + widest_core_half_window
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
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
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
        The instrument's resolution element, in Angstroms. Defaults to
        `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM`; pass the spectrum's own
        measured value when there is one (see `spectral_resolution`).

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
    smoothed = gaussian_filter1d(
        template_flux, blur_sigma_in_samples(resolution_element_angstrom, sample_spacing)
    )

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


def _mark_features_sharing_a_dip(
    entries: list[dict[str, object]], resolution_element_angstrom: float
) -> None:
    """Stop one dip being reported as two different lines.

    Each feature looks for its best dip within
    `CENTER_SEARCH_TOLERANCE_ANGSTROM` of its rest wavelength, independently
    of the others. Two features whose rest wavelengths are closer together
    than the instrument can separate (the G band at 4300 A and H-gamma at
    4340 A, against a resolution element of 45 to 50 A) therefore find the
    same dip and both report it. On real Vega, Albireo B and Deneb spectra
    the G band came out with the same depth and p-value as H-gamma, and a G
    band "detected" in an A0V star is not real.

    When two reported features measure their dips less than one resolution
    element apart, only one keeps the dip. If a reference type was given and
    the two features' expected depths for it differ by more than
    `EXPECTED_DEPTH_TIE_BREAK_MARGIN`, the feature the type expects to be
    deeper keeps it (an A star shows H-gamma and not the G band; a K star the
    reverse). Otherwise the dip goes to the feature whose rest wavelength is
    nearer to it. The other is set to `VERDICT_INCONCLUSIVE` (the spectrum
    cannot say whether it adds anything of its own) and its ``"blended_with"``
    names the feature that keeps the dip. A feature that was not reported to
    begin with is left alone. Changes `entries` in place.

    Parameters
    ----------
    entries : `list` [`dict`]
        The per-feature entries built by `detect_named_features`, before they
        are sorted.
    resolution_element_angstrom : `float`
        Dips closer together than this are treated as one.
    """
    measured = [entry for entry in entries if "measured_wavelength_angstrom" in entry]

    def dip_center(entry: dict[str, object]) -> float:
        """Read where an entry's dip was measured.

        Returns
        -------
        center : `float`
            The measured wavelength, in Angstroms.
        """
        return float(entry["measured_wavelength_angstrom"])

    def nearness_rank(entry: dict[str, object]) -> tuple[float, float]:
        """Rank how near an entry's dip is to its rest wavelength.

        Returns
        -------
        rank : `tuple` [`float`, `float`]
            The distance from the rest wavelength to the dip, then the rest
            wavelength itself (so an exact tie has a fixed winner).
        """
        rest = float(entry["wavelength_angstrom"])
        return abs(dip_center(entry) - rest), rest

    def keeps_dip_over(candidate: dict[str, object], rival: dict[str, object]) -> bool:
        """Decide whether `candidate` has a stronger claim to the shared dip.

        Returns
        -------
        keeps : `bool`
            `True` when `candidate` should keep the dip rather than `rival`.
        """
        candidate_expected = candidate.get("expected_depth")
        rival_expected = rival.get("expected_depth")
        if (
            candidate_expected is not None
            and rival_expected is not None
            and abs(float(candidate_expected) - float(rival_expected)) > EXPECTED_DEPTH_TIE_BREAK_MARGIN
        ):
            return float(candidate_expected) > float(rival_expected)
        return nearness_rank(candidate) < nearness_rank(rival)

    for entry in measured:
        if entry["verdict"] not in (VERDICT_DETECTED, VERDICT_POSSIBLE):
            continue
        keeper = None
        for other in measured:
            if other is entry or abs(dip_center(other) - dip_center(entry)) >= resolution_element_angstrom:
                continue
            if keeps_dip_over(other, entry) and (keeper is None or keeps_dip_over(other, keeper)):
                keeper = other
        if keeper is not None:
            entry["blended_with"] = keeper["feature"]
            entry["verdict"] = VERDICT_INCONCLUSIVE


def detect_named_features(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    reference_spectral_type: str | None = None,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
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
        The width of one independent measurement, in Angstroms: how much
        the instrument blurs this spectrum. Defaults to
        `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM`; pass the spectrum's own
        measured value when there is one (see `spectral_resolution`).

    Returns
    -------
    features : `list` [`dict`]
        One entry per named feature, most convincing first. Every entry
        has ``"feature"`` (name), ``"wavelength_angstrom"`` (its rest
        wavelength) and ``"verdict"`` (`VERDICT_DETECTED`,
        `VERDICT_POSSIBLE`, `VERDICT_INCONCLUSIVE`, `VERDICT_NOT_DETECTED`
        or `VERDICT_NOT_COVERED`). A covered feature also has
        ``"measured_wavelength_angstrom"`` (where the best dip is),
        ``"depth"``, ``"depth_uncertainty"``, ``"significance"``,
        ``"p_value"`` (the chance that noise like this spectrum's gives
        a dip at least this significant), ``"p_value_method"``
        (``"control_calibrated"``, or ``"gaussian"`` when too few control
        positions were available), ``"expected_depth"`` and
        ``"probability_present"`` (both `None` without a reference type)
        and ``"blended_with"`` (the name of a nearby feature that keeps the
        same dip, or `None`; see `_mark_features_sharing_a_dip`).
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
            wavelength_angstrom, half_window, CENTER_SEARCH_TOLERANCE_ANGSTROM, resolution_element_angstrom
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
        elif observed.depth >= INCONCLUSIVE_MINIMUM_DEPTH and p_value <= INCONCLUSIVE_P_VALUE:
            verdict = VERDICT_INCONCLUSIVE
        else:
            verdict = VERDICT_NOT_DETECTED

        limited_by_noise = observed.depth_uncertainty > MAXIMUM_UNCERTAINTY_FOR_A_VERDICT
        if limited_by_noise and verdict in (VERDICT_DETECTED, VERDICT_POSSIBLE):
            verdict = VERDICT_INCONCLUSIVE

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
            "limited_by_noise": limited_by_noise,
            "blended_with": None,
        })
        entries.append(entry)

    _mark_features_sharing_a_dip(entries, resolution_element_angstrom)

    verdict_rank = {
        VERDICT_DETECTED: 0,
        VERDICT_POSSIBLE: 1,
        VERDICT_INCONCLUSIVE: 2,
        VERDICT_NOT_DETECTED: 3,
        VERDICT_NOT_COVERED: 4,
    }
    entries.sort(key=lambda entry: (verdict_rank[str(entry["verdict"])], float(entry.get("p_value", 1.0))))
    return entries
