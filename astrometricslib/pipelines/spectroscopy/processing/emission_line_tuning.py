"""Tune the emission-line detector's checks from stored spectra.

The emission-line detector (`emission_line_detector`) fits a smooth continuum
and a box for each named nebula line. Every limit in it was set on one nebula,
M 57. This module derives them from the spectra the library holds.

Two tuners share the work:

* `EMISSION_SPECTRUM_CHECKS_TUNER` sets what depends on the continuum, the
  noise and the stars: the continuum degree, the fewest samples, the cap on
  the noise inflation and the number of lines that make a source.
* `EMISSION_LINE_BOXES_TUNER` sets what depends on the width of a nebula's
  lines: the blend limit and the covered share. It needs stored spectra of
  nebulae, which are the spectra the library calls emission-line sources.

Both tune the diffraction grating's spectra only; a normal filter has none.
The numbers each method uses are declared below with their reasons.
"""

import logging
from typing import Any

import numpy as np

from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.processing.detector_tuning_statistics import (
    blocked_cross_validation_error,
    point_to_point_noise,
    raw_noise_inflation,
    smallest_degree_within_tolerance,
    smallest_passing_value,
    widest_gap_midpoint,
    window_variance_spread,
)
from astrometricslib.pipelines.spectroscopy.processing.emission_line_detector import (
    _MAXIMUM_INFLATION_FRACTION,
    _MINIMUM_SPECTRUM_POINTS,
    BLEND_CORRELATION_THRESHOLD,
    CONTINUUM_POLYNOMIAL_DEGREE,
    MAXIMUM_FITTED_WAVELENGTH_ANGSTROM,
    MINIMUM_COVERED_FRACTION,
    MINIMUM_DETECTED_FOR_EMISSION_SOURCE,
    NEBULA_LINE_GROUPS,
    VERDICT_DETECTED,
    _build_columns,
    _fit_covered,
    detect_emission_lines,
    line_half_width_angstrom,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import (
    StoredSpectrum,
    quantile_of,
    spectral_target_stars,
    stored_spectra,
)

logger = logging.getLogger(__name__)


def _hardcoded(name: str, default: Any, unit: str, description: str, kind: HardcodedKind) -> TunedParameter:
    """Declare one number that the emission tuners' methods use.

    Parameters
    ----------
    name : `str`
        The parameter name.
    default : `Any`
        The value.
    unit : `str`
        Its unit.
    description : `str`
        What it is.
    kind : `HardcodedKind`
        Why it may stay in the code.

    Returns
    -------
    parameter : `TunedParameter`
        The hardcoded declaration, under register entry SPE-D40.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit=unit,
        description=description,
        role=ParameterRole.JUDGMENT,
        source=ParameterSource.HARDCODED,
        kind=kind,
        register_entry="SPE-D40",
    )


# The highest continuum degree tried. A polynomial of higher degree can bend
# into a hump 600 Angstroms wide and hide it, which is the reason the
# detector uses a gentle curve (see `CONTINUUM_POLYNOMIAL_DEGREE`).
_MAXIMUM_CANDIDATE_DEGREE = _hardcoded(
    "spectroscopy.emission_tuning.maximum_candidate_degree",
    4,
    "degree",
    "The highest continuum degree the emission tuner tries.",
    HardcodedKind.METHOD_CHOICE,
)

# The number of contiguous blocks held out in turn when a continuum degree is
# tried. Five blocks leave each held-out stretch about a fifth of the spectrum
# (more than the width of the widest line box), so a degree is judged on how
# it extends across a line, not between neighboring samples.
_CONTINUUM_BLOCK_COUNT = _hardcoded(
    "spectroscopy.emission_tuning.continuum_block_count",
    5,
    "count",
    "How many blocks of a spectrum are held out in turn to test a continuum degree.",
    HardcodedKind.METHOD_CHOICE,
)

# How much worse than the best degree a lower one may be and still win. Under
# one part in twenty is below what a single spectrum's held-out error can
# tell apart, and the lower degree is the safer one for hiding no line.
_DEGREE_TOLERANCE = _hardcoded(
    "spectroscopy.emission_tuning.degree_tolerance",
    0.05,
    "fraction",
    "How much worse than the best continuum degree a lower degree may be and still be chosen.",
    HardcodedKind.RELATIVE,
)

# The window sizes tried for the fewest samples, in samples.
_CANDIDATE_POINT_COUNTS = _hardcoded(
    "spectroscopy.emission_tuning.candidate_point_counts",
    (12, 16, 20, 24, 30, 40, 50, 60, 80),
    "count",
    "The sample counts the emission tuner tries for the fewest samples of a fit.",
    HardcodedKind.METHOD_CHOICE,
)

# How much the noise estimate may vary from window to window, relative to its
# own size. A noise estimate from n samples has a relative spread of about
# the square root of 2/n, so 0.3 asks for about 20 samples of noise.
_NOISE_SPREAD_TOLERANCE = _hardcoded(
    "spectroscopy.emission_tuning.noise_spread_tolerance",
    0.3,
    "fraction",
    "How much the noise estimate may vary from window to window, relative to its size.",
    HardcodedKind.STATISTICS,
)

# The share of spectra that must pass a check at a candidate value. Nine in
# ten leaves out the few spectra with a fault the check cannot see.
_SPECTRA_COVERAGE = _hardcoded(
    "spectroscopy.emission_tuning.spectra_coverage",
    0.9,
    "fraction",
    "The share of stored spectra that must pass a check at a candidate value.",
    HardcodedKind.STATISTICS,
)

# The share of real spectra whose noise inflation the cap must leave
# untouched, so the cap only limits the odd spectrum with almost perfectly
# correlated residuals.
_INFLATION_COVERAGE = _hardcoded(
    "spectroscopy.emission_tuning.inflation_coverage",
    0.95,
    "fraction",
    "The share of real spectra whose noise inflation the cap leaves untouched.",
    HardcodedKind.STATISTICS,
)

# The share of ordinary stars that may be called an emission-line source by
# chance. A stored star is almost never a gas cloud, so one in a hundred
# is the most that the false-source rate should reach.
_FALSE_SOURCE_RATE = _hardcoded(
    "spectroscopy.emission_tuning.false_source_rate",
    0.01,
    "fraction",
    "The share of ordinary stars that may be called an emission-line source.",
    HardcodedKind.STATISTICS,
)

# The covered shares of a line box tried, from a sliver to nearly all.
_CANDIDATE_COVERED_FRACTIONS = _hardcoded(
    "spectroscopy.emission_tuning.candidate_covered_fractions",
    (0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9),
    "fraction",
    "The covered shares of a line box the emission tuner tries.",
    HardcodedKind.METHOD_CHOICE,
)

# The strength of the injected line, in times the spectrum's sample-to-sample
# noise. It must be strong enough for the whole box to be detected with
# margin, so a miss comes from the cut-off box, not from a faint line.
_INJECTED_AMPLITUDE_SIGMAS = _hardcoded(
    "spectroscopy.emission_tuning.injected_amplitude_sigmas",
    20.0,
    "× noise",
    "The strength of the line injected into a stored spectrum, in sample-to-sample noise.",
    HardcodedKind.METHOD_CHOICE,
)

# How far the recovered amplitude may be from the injected one. A line
# counts as recovered when its fitted height is within a quarter of the
# true height, the accuracy of the ratios the detector never reports.
_RECOVERY_TOLERANCE = _hardcoded(
    "spectroscopy.emission_tuning.recovery_tolerance",
    0.25,
    "fraction",
    "How far the recovered amplitude of an injected line may be from the true one.",
    HardcodedKind.RELATIVE,
)

# The fewest pairs of line boxes that make a gap between look-alikes and
# distinct pairs mean anything.
_MINIMUM_PAIRS_FOR_BLEND_GAP = _hardcoded(
    "spectroscopy.emission_tuning.minimum_pairs_for_blend_gap",
    3,
    "count",
    "The fewest positively correlated line pairs needed to place a blend limit.",
    HardcodedKind.METHOD_CHOICE,
)


def _fitted_arrays(spectrum: StoredSpectrum, maximum_wavelength: float) -> tuple[np.ndarray, np.ndarray]:
    """Cut a stored spectrum to the range the detector fits.

    Returns
    -------
    wavelength, intensity : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
        The samples at or below the maximum wavelength.
    """
    keep = spectrum.wavelength_angstrom <= maximum_wavelength
    return spectrum.wavelength_angstrom[keep], spectrum.intensity[keep]


def _normalized_positions(wavelength: np.ndarray) -> np.ndarray:
    """Scale wavelengths to run from -1 to 1, as the detector does.

    Returns
    -------
    positions : `numpy.ndarray`
        The scaled wavelengths.
    """
    return 2.0 * (wavelength - wavelength[0]) / (wavelength[-1] - wavelength[0]) - 1.0


def continuum_errors_by_degree(
    arrays: list[tuple[np.ndarray, np.ndarray]], degrees: range, block_count: int
) -> dict[int, float]:
    """Find the typical held-out error of each continuum degree.

    Parameters
    ----------
    arrays : `list` [`tuple` [`numpy.ndarray`, `numpy.ndarray`]]
        The wavelengths and brightness of each spectrum.
    degrees : `range`
        The degrees to try.
    block_count : `int`
        The blocks held out in turn.

    Returns
    -------
    errors : `dict` [`int`, `float`]
        For each degree, the median over the spectra of the blocked
        cross-validation error. A degree with no testable spectrum is left
        out.
    """
    errors: dict[int, float] = {}
    for degree in degrees:
        per_spectrum = []
        for wavelength, intensity in arrays:
            if len(wavelength) < 2:
                continue
            error = blocked_cross_validation_error(
                _normalized_positions(wavelength), intensity, degree, block_count
            )
            if error is not None:
                per_spectrum.append(error)
        if per_spectrum:
            errors[degree] = float(np.median(per_spectrum))
    return errors


def inflation_cap_fraction(
    arrays: list[tuple[np.ndarray, np.ndarray]], degree: int, coverage: float
) -> float | None:
    """Find the cap on the noise inflation as a share of the sample count.

    Parameters
    ----------
    arrays : `list` [`tuple` [`numpy.ndarray`, `numpy.ndarray`]]
        The wavelengths and brightness of each spectrum.
    degree : `int`
        The continuum degree fitted before the residuals are taken.
    coverage : `float`
        The share of spectra the cap must leave untouched.

    Returns
    -------
    fraction : `float` or `None`
        The coverage quantile of each spectrum's uncapped inflation divided
        by its sample count, or `None` when no spectrum is long enough.
    """
    fractions = []
    for wavelength, intensity in arrays:
        if len(wavelength) <= degree + 2:
            continue
        positions = _normalized_positions(wavelength)
        residual = intensity - np.polyval(np.polyfit(positions, intensity, degree), positions)
        fractions.append(raw_noise_inflation(residual) / len(wavelength))
    return quantile_of(fractions, coverage) if fractions else None


def point_count_shares(
    arrays: list[tuple[np.ndarray, np.ndarray]],
    candidates: tuple[int, ...],
    degree: int,
    tolerance: float,
) -> list[float | None]:
    """Find the share of spectra with a steady noise estimate at each count.

    Parameters
    ----------
    arrays : `list` [`tuple` [`numpy.ndarray`, `numpy.ndarray`]]
        The wavelengths and brightness of each spectrum.
    candidates : `tuple` [`int`, ...]
        The window sizes to try.
    degree : `int`
        The polynomial degree fitted in each window.
    tolerance : `float`
        The largest relative spread of the window noise variances.

    Returns
    -------
    shares : `list` [`float` or `None`]
        For each candidate, the share of testable spectra whose spread is at
        most the tolerance, or `None` when no spectrum was long enough.
    """
    shares: list[float | None] = []
    for count in candidates:
        spreads = [
            window_variance_spread(wavelength, intensity, count, degree) for wavelength, intensity in arrays
        ]
        tested = [spread for spread in spreads if spread is not None]
        shares.append(sum(spread <= tolerance for spread in tested) / len(tested) if tested else None)
    return shares


def source_line_count_limit(detected_counts: list[int], false_rate: float) -> int:
    """Find the fewest detected lines that few ordinary stars reach.

    Parameters
    ----------
    detected_counts : `list` [`int`]
        The number of lines or blends detected in each ordinary star.
    false_rate : `float`
        The share of stars allowed to reach the count.

    Returns
    -------
    count : `int`
        The smallest count of at least one that no more than ``false_rate``
        of the stars reach.
    """
    counts = np.asarray(detected_counts)
    limit = 1
    while float(np.mean(counts >= limit)) > false_rate:
        limit += 1
    return limit


def box_half_width_angstrom(spectrum: StoredSpectrum) -> float | None:
    """Work out the line half-width of a stored nebula spectrum.

    Parameters
    ----------
    spectrum : `StoredSpectrum`
        The spectrum.

    Returns
    -------
    half_width : `float` or `None`
        The half-width, in Angstroms, from the extraction box the spectrum
        was read with, or `None` when the box was not recorded.
    """
    if spectrum.extraction_half_width_px is None:
        return None
    return line_half_width_angstrom(spectrum.wavelength_angstrom, 2.0 * spectrum.extraction_half_width_px)


def pairwise_box_correlations(
    wavelength: np.ndarray,
    half_width: float,
    blur_sigma: float,
    degree: int,
    minimum_covered_fraction: float,
) -> dict[tuple[str, str], float]:
    """Correlate the line boxes of one spectrum, with the continuum removed.

    Parameters
    ----------
    wavelength : `numpy.ndarray`
        The fitted wavelength grid.
    half_width : `float`
        The half-width of each line box, in Angstroms.
    blur_sigma : `float`
        The softening of the box edges, in Angstroms.
    degree : `int`
        The continuum degree whose terms are removed from each box.
    minimum_covered_fraction : `float`
        Boxes covered less than this are left out.

    Returns
    -------
    correlations : `dict` [`tuple` [`str`, `str`], `float`]
        The correlation of each pair of covered line groups, by group names.
    """
    columns = [
        column
        for column in _build_columns(wavelength, half_width, blur_sigma)
        if column.covered_fraction >= minimum_covered_fraction
    ]
    design = np.vander(_normalized_positions(wavelength), degree + 1, increasing=True)
    projection = design @ np.linalg.pinv(design)
    residuals = [column.profile - projection @ column.profile for column in columns]
    correlations = {}
    for first in range(len(columns)):
        for second in range(first + 1, len(columns)):
            norm_product = np.linalg.norm(residuals[first]) * np.linalg.norm(residuals[second])
            if norm_product > 0.0:
                correlations[columns[first].name, columns[second].name] = float(
                    residuals[first] @ residuals[second] / norm_product
                )
    return correlations


def blend_limit(per_spectrum: list[dict[tuple[str, str], float]], minimum_pairs: int) -> float | None:
    """Place the blend limit in the widest gap between line pairs.

    Parameters
    ----------
    per_spectrum : `list` [`dict`]
        The pair correlations of each nebula spectrum.
    minimum_pairs : `int`
        The fewest positively correlated pairs for a gap to mean anything.

    Returns
    -------
    limit : `float` or `None`
        The middle of the widest gap between the pairs' median correlations
        over the spectra, counting only positive correlations (a pair that
        looks opposite is not a blend), or `None` when too few pairs.
    """
    pooled: dict[tuple[str, str], list[float]] = {}
    for correlations in per_spectrum:
        for pair, value in correlations.items():
            pooled.setdefault(pair, []).append(value)
    medians = [float(np.median(values)) for values in pooled.values()]
    return widest_gap_midpoint([value for value in medians if value > 0.0], minimum_pairs)


def injection_recovered(
    wavelength: np.ndarray,
    intensity: np.ndarray,
    group_index: int,
    covered_fraction: float,
    half_width: float,
    blur_sigma: float,
    amplitude_sigmas: float,
    tolerance: float,
) -> bool | None:
    """Inject a partly covered line and try to recover it.

    The spectrum is cut at the wavelength that leaves ``covered_fraction`` of
    the line's box inside, a box of known height is added, and the detector's
    own fit (the continuum and that one line) is run.

    Parameters
    ----------
    wavelength : `numpy.ndarray`
        The spectrum's wavelengths, in increasing order.
    intensity : `numpy.ndarray`
        The brightness at each wavelength.
    group_index : `int`
        Which entry of the named line groups to inject.
    covered_fraction : `float`
        The share of the box that stays inside the cut spectrum.
    half_width : `float`
        The half-width of the box, in Angstroms.
    blur_sigma : `float`
        The softening of the box edges, in Angstroms.
    amplitude_sigmas : `float`
        The injected height, in times the spectrum's sample noise.
    tolerance : `float`
        How far the recovered height may be from the injected one, as a
        fraction of it.

    Returns
    -------
    recovered : `bool` or `None`
        Whether the line was detected at the injected height within the
        tolerance, or `None` when the spectrum cannot hold the test (the box
        starts before it or the cut lies beyond it).
    """
    members = NEBULA_LINE_GROUPS[group_index][1]
    center = sum(rest * weight for rest, weight in members) / sum(weight for _, weight in members)
    box_low = center - half_width
    cutoff = box_low + covered_fraction * 2.0 * half_width
    if box_low < wavelength[0] or cutoff > wavelength[-1]:
        return None
    keep = wavelength <= cutoff
    cut_wavelength = wavelength[keep]
    cut_intensity = intensity[keep]
    # The fit needs more samples than it has numbers to fit.
    if len(cut_wavelength) < 3 * (resolve(CONTINUUM_POLYNOMIAL_DEGREE).value + 2):
        return None
    column = _build_columns(cut_wavelength, half_width, blur_sigma)[group_index]
    amplitude = amplitude_sigmas * point_to_point_noise(cut_intensity)
    if amplitude <= 0.0:
        return None
    entry = _fit_covered(cut_wavelength, cut_intensity + amplitude * column.profile, [column])[0]
    recovered_amplitude = float(entry["amplitude"])  # type: ignore[arg-type]
    return entry["verdict"] == VERDICT_DETECTED and abs(recovered_amplitude / amplitude - 1.0) <= tolerance


def _require_grating(inputs: TuningInputs) -> None:
    """Refuse a filter that has no spectra.

    Raises
    ------
    TuningSkippedError
        If the filter is not the diffraction grating.
    """
    if inputs.filter_name != FilterType.SPEC.name:
        raise TuningSkippedError("only the diffraction grating has stored spectra")


def tune_emission_spectrum_checks(inputs: TuningInputs) -> TuningResult:
    """Tune the continuum, sample count, inflation cap and source count.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup, the grating filter and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The four values, from the stored spectra of the setup's camera.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum is stored.
    """
    _require_grating(inputs)
    astrometrics: Any = inputs.service("astrometrics")
    spectra = stored_spectra(
        spectral_target_stars(astrometrics, inputs.setup.camera_name, inputs.configuration)
    )
    maximum_wavelength = resolve(MAXIMUM_FITTED_WAVELENGTH_ANGSTROM).value
    arrays = [_fitted_arrays(spectrum, maximum_wavelength) for spectrum in spectra]
    arrays = [pair for pair in arrays if len(pair[0]) >= 2]
    if not arrays:
        raise TuningSkippedError("no stored spectrum of this camera")

    values: dict[str, float | int] = {}
    errors = continuum_errors_by_degree(
        arrays,
        range(1, int(resolve(_MAXIMUM_CANDIDATE_DEGREE).value) + 1),
        int(resolve(_CONTINUUM_BLOCK_COUNT).value),
    )
    degree = smallest_degree_within_tolerance(errors, resolve(_DEGREE_TOLERANCE).value)
    if degree is None:
        raise TuningSkippedError("no spectrum is long enough to test a continuum degree")
    values[CONTINUUM_POLYNOMIAL_DEGREE.name] = degree

    inflation_fraction = inflation_cap_fraction(arrays, degree, resolve(_INFLATION_COVERAGE).value)
    if inflation_fraction is not None:
        values[_MAXIMUM_INFLATION_FRACTION.name] = round(inflation_fraction, 3)

    candidates = tuple(int(count) for count in resolve(_CANDIDATE_POINT_COUNTS).value)
    shares = point_count_shares(arrays, candidates, degree, resolve(_NOISE_SPREAD_TOLERANCE).value)
    point_count = smallest_passing_value(candidates, shares, resolve(_SPECTRA_COVERAGE).value)
    if point_count is not None:
        values[_MINIMUM_SPECTRUM_POINTS.name] = int(point_count)

    stars = [spectrum for spectrum in spectra if not spectrum.is_emission_line_source]
    if stars:
        detected_counts = []
        for spectrum in stars:
            resolution = (
                spectrum.resolution_element_angstrom or resolve(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM).value
            )
            lines = detect_emission_lines(
                spectrum.wavelength_angstrom,
                spectrum.intensity,
                resolution,
                resolution_element_angstrom=resolution,
            )
            detected_counts.append(sum(1 for entry in lines if entry["verdict"] == VERDICT_DETECTED))
        values[MINIMUM_DETECTED_FOR_EMISSION_SOURCE.name] = source_line_count_limit(
            detected_counts, resolve(_FALSE_SOURCE_RATE).value
        )
    return TuningResult(
        values=values,
        sample=len(arrays),
        source=(
            f"Held-out continuum error, noise-estimate steadiness, residual correlation and false-line "
            f"counts of {len(arrays)} stored spectra of this camera"
        ),
    )


def tune_emission_line_boxes(inputs: TuningInputs) -> TuningResult:
    """Tune the blend limit and the covered share from nebula line widths.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup, the grating filter and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The blend limit and the covered share, from the stored nebula
        spectra and injections into the stored star spectra.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating, or no nebula spectrum with a
        recorded extraction box is stored.
    """
    _require_grating(inputs)
    astrometrics: Any = inputs.service("astrometrics")
    spectra = stored_spectra(
        spectral_target_stars(astrometrics, inputs.setup.camera_name, inputs.configuration)
    )
    nebulae = [
        (spectrum, half_width)
        for spectrum in spectra
        if spectrum.is_emission_line_source and (half_width := box_half_width_angstrom(spectrum)) is not None
    ]
    if not nebulae:
        raise TuningSkippedError("no stored nebula spectrum with a recorded extraction box")
    maximum_wavelength = resolve(MAXIMUM_FITTED_WAVELENGTH_ANGSTROM).value
    fallback_resolution = resolve(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM).value
    degree = resolve(CONTINUUM_POLYNOMIAL_DEGREE).value

    per_spectrum = []
    for spectrum, half_width in nebulae:
        wavelength, _ = _fitted_arrays(spectrum, maximum_wavelength)
        if len(wavelength) < 2:
            continue
        resolution = spectrum.resolution_element_angstrom or fallback_resolution
        per_spectrum.append(
            pairwise_box_correlations(
                wavelength,
                max(half_width, resolution),
                resolution / 2.355,
                degree,
                resolve(MINIMUM_COVERED_FRACTION).value,
            )
        )
    values: dict[str, float | int] = {}
    limit = blend_limit(per_spectrum, int(resolve(_MINIMUM_PAIRS_FOR_BLEND_GAP).value))
    if limit is not None:
        values[BLEND_CORRELATION_THRESHOLD.name] = round(limit, 2)

    typical_half_width = float(np.median([max(width, 1.0) for _, width in nebulae]))
    typical_resolution = float(
        np.median([spectrum.resolution_element_angstrom or fallback_resolution for spectrum, _ in nebulae])
    )
    stars = [spectrum for spectrum in spectra if not spectrum.is_emission_line_source]
    fractions = tuple(float(fraction) for fraction in resolve(_CANDIDATE_COVERED_FRACTIONS).value)
    shares: list[float | None] = []
    for fraction in fractions:
        outcomes = []
        for spectrum in stars:
            wavelength, intensity = _fitted_arrays(spectrum, maximum_wavelength)
            if len(wavelength) < 2:
                continue
            for group_index in range(len(NEBULA_LINE_GROUPS)):
                outcome = injection_recovered(
                    wavelength,
                    intensity,
                    group_index,
                    fraction,
                    typical_half_width,
                    typical_resolution / 2.355,
                    resolve(_INJECTED_AMPLITUDE_SIGMAS).value,
                    resolve(_RECOVERY_TOLERANCE).value,
                )
                if outcome is not None:
                    outcomes.append(outcome)
        shares.append(sum(outcomes) / len(outcomes) if outcomes else None)
    covered = smallest_passing_value(fractions, shares, resolve(_SPECTRA_COVERAGE).value)
    if covered is not None:
        values[MINIMUM_COVERED_FRACTION.name] = round(covered, 2)
    if not values:
        raise TuningSkippedError("the stored spectra gave neither a blend limit nor a covered share")
    return TuningResult(
        values=values,
        sample=len(per_spectrum),
        source=(
            f"Line-box correlations of {len(per_spectrum)} stored nebula spectra and recovery of lines "
            f"injected into {len(stars)} stored star spectra"
        ),
    )


EMISSION_SPECTRUM_CHECKS_TUNER = Tuner(
    name="spectroscopy.emission_spectrum_checks",
    parameters=(
        CONTINUUM_POLYNOMIAL_DEGREE,
        _MINIMUM_SPECTRUM_POINTS,
        _MAXIMUM_INFLATION_FRACTION,
        MINIMUM_DETECTED_FOR_EMISSION_SOURCE,
    ),
    method=(
        "On the stored spectra of the grating: the lowest continuum degree whose blocked cross-validation "
        "error is near the best; the smallest window whose noise estimate is steady in nine of ten spectra; "
        "the inflation cap at the 95th percentile of real residual correlation; and the fewest detected "
        "lines that no more than one in a hundred ordinary stars reach."
    ),
    tune=tune_emission_spectrum_checks,
    per_filter=True,
)

EMISSION_LINE_BOXES_TUNER = Tuner(
    name="spectroscopy.emission_line_boxes",
    parameters=(BLEND_CORRELATION_THRESHOLD, MINIMUM_COVERED_FRACTION),
    method=(
        "From the extraction boxes of stored nebula spectra: the blend limit in the widest gap between "
        "look-alike and distinct line-box pairs; the covered share as the smallest share at which a line "
        "injected into stored star spectra is recovered in nine of ten cases."
    ),
    tune=tune_emission_line_boxes,
    per_filter=True,
)
