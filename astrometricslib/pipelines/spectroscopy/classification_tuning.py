"""Purpose: Derive the spectral classifier's limits from stored spectra.

Description: Issue #175 Phase D. Three tuners for the values that decide when
a spectrum's best reference match is believed (validation entries SPE-16,
SPE-17, SPE-18 and SPE-D47):

* ``spectroscopy.match_limits``: the poor-match and no-match limits, from
  the stored spectra of stars with a catalog type.
* ``spectroscopy.ambiguity_gap``: the least gap between two references that
  the classifier can tell apart, from the bundled reference ladder blurred to
  this instrument's resolution.
* ``spectroscopy.classification_limits``: the widest range, the least
  coverage and the fewest overlapping points, from cutting stored spectra
  short and seeing when the class changes.

The stored spectra are classified again here with the limits opened wide,
because the stored result keeps no score for a spectrum the limits turned
away. Each tuner keeps its arithmetic in functions that take plain numbers.
"""

from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np

from astrometricslib.foundation.parameter_context import (
    ParameterContext,
    current_parameter_context,
    default_parameter_context,
    parameter_context,
)
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningResult, TuningSkippedError
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    NO_GOOD_MATCH_RMS,
    UNRELIABLE_MATCH_RMS,
    ladder_position,
)
from astrometricslib.models.tuned_parameter import HardcodedKind, TunedParameter
from astrometricslib.pipelines.spectroscopy.instrument_tuning import RANGE_BAND_ANGSTROM
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask import atmospheric_band_mask
from astrometricslib.pipelines.spectroscopy.pre_processing.instrument_response import (
    DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    _MINIMUM_OVERLAP_POINTS,
    CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM,
    MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM,
    REFERENCE_SPECTRAL_TYPES,
    _get_reference_templates,
    blurred_reference_spectrum,
    classify_spectral_type,
)
from astrometricslib.pipelines.spectroscopy.spectrum_tuning_data import (
    StoredStarSpectrum,
    declare_method_choice,
    load_stored_star_spectra,
    require_spectral_filter,
)

# A spectrum is a right match when its type is within this many steps of the
# catalog type on the ladder (ten steps to a class). The entry counts the
# stars matched within three steps as right.
RIGHT_MATCH_WITHIN_STEPS = declare_method_choice(
    "spectroscopy.tuning_right_match_within_steps",
    3.0,
    "subtype steps",
    "How near the catalog type a matched type must be to count as a right match.",
    "SPE-16",
    HardcodedKind.METHOD_CHOICE,
)

# The share of stars above the no-match limit that must have no catalog type.
# Such stars were matched to the wrong part of the sky, so the closest
# reference would only invent a type for them. The entry found 18 of 19.
UNTYPED_SHARE_ABOVE_LIMIT = declare_method_choice(
    "spectroscopy.tuning_untyped_share_above_limit",
    0.9,
    "fraction",
    "The share of stars above the no-match limit that have no catalog type.",
    "SPE-17",
    HardcodedKind.STATISTICS,
)

# The share of cut spectra that must keep their class letter. The coverage
# and the point count are read where this share of spectra stays stable.
STABLE_CLASS_SHARE = declare_method_choice(
    "spectroscopy.tuning_stable_class_share",
    0.9,
    "fraction",
    "The share of cut spectra that must keep their class letter.",
    "SPE-D47",
    HardcodedKind.STATISTICS,
)

# How much of the red end is cut away between two trials of the coverage.
COVERAGE_STEP_ANGSTROM = declare_method_choice(
    "spectroscopy.tuning_coverage_step_angstrom",
    200.0,
    "Å",
    "How much of the spectrum is cut away between two trials of the coverage.",
    "SPE-D47",
    HardcodedKind.METHOD_CHOICE,
)


class _FixedValues:
    """Give `resolve` chosen values for some tuned parameters during a trial.

    Parameters
    ----------
    chosen : `dict` [`str`, `float`]
        The value to give, by parameter name. Any other parameter gets its
        code default.
    """

    def __init__(self, chosen: dict[str, float]) -> None:
        self._chosen = chosen

    def tuned_value(self, parameter: TunedParameter) -> object:
        """Give one parameter's value.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration.

        Returns
        -------
        value : `object`
            The chosen value, or the parameter's default.
        """
        return self._chosen.get(parameter.name, parameter.default)


@contextmanager
def trial_context(chosen: dict[str, float]) -> Generator[ParameterContext]:
    """Run a trial in which some tuned parameters take chosen values.

    The trial keeps the configuration and equipment of the running context,
    so only the tuned values change: the chosen ones, and every other tuned
    parameter at its code default.

    Parameters
    ----------
    chosen : `dict` [`str`, `float`]
        The value to give each chosen parameter, by name.

    Yields
    ------
    context : `ParameterContext`
        The trial's context.
    """
    outer = current_parameter_context() or default_parameter_context()
    with parameter_context(
        configuration=outer.configuration, equipment=outer.equipment, tuned=_FixedValues(chosen)
    ) as context:
        yield context


@dataclass(frozen=True)
class ClassifiedSpectrum:
    """A stored spectrum with the best match the classifier gives it now.

    Parameters
    ----------
    spectrum : `StoredStarSpectrum`
        The stored spectrum.
    best_type : `str` or `None`
        The best reference label, or `None` when none could be compared.
    best_rms : `float` or `None`
        The relative RMS of that match.
    """

    spectrum: StoredStarSpectrum
    best_type: str | None
    best_rms: float | None


def classify_with_limits_open(spectrum: StoredStarSpectrum) -> ClassifiedSpectrum | None:
    """Classify a stored spectrum with the no-match limit at its widest.

    Parameters
    ----------
    spectrum : `StoredStarSpectrum`
        The stored spectrum; it needs a response-corrected brightness.

    Returns
    -------
    classified : `ClassifiedSpectrum` or `None`
        The best match, or `None` when the spectrum has no response-corrected
        brightness.
    """
    if spectrum.response_corrected is None:
        return None
    widest = UNRELIABLE_MATCH_RMS.allowed_range[1]
    with trial_context({UNRELIABLE_MATCH_RMS.name: widest}):
        result = classify_spectral_type(
            spectrum.wavelength_angstrom,
            spectrum.response_corrected,
            resolution_element_angstrom=spectrum.resolution_element_angstrom,
            intensity_errors=None,
        )
    best_type = result["spectral_type"]
    return ClassifiedSpectrum(
        spectrum=spectrum,
        best_type=None if best_type == "Unknown" else str(best_type),
        best_rms=None if result["classification_rms"] is None else float(result["classification_rms"]),
    )


# --- Match limits (SPE-16, SPE-17) ------------------------------------------


def separating_limit(right_rms: Sequence[float], wrong_rms: Sequence[float]) -> float | None:
    """Find the score that best separates right matches from wrong ones.

    Parameters
    ----------
    right_rms : `Sequence` [`float`]
        The relative RMS of the right matches.
    wrong_rms : `Sequence` [`float`]
        The relative RMS of the wrong matches.

    Returns
    -------
    limit : `float` or `None`
        The limit with the most right matches below it plus wrong matches
        above it. When several limits tie, the middle of the tied ones.
        `None` when either list is empty.
    """
    if not len(right_rms) or not len(wrong_rms):
        return None
    right, wrong = np.asarray(right_rms, dtype=float), np.asarray(wrong_rms, dtype=float)
    levels = np.unique(np.concatenate([right, wrong]))
    if levels.size < 2:
        return None
    candidates = 0.5 * (levels[:-1] + levels[1:])
    scores = np.array([np.mean(right <= limit) + np.mean(wrong > limit) for limit in candidates])
    tied = candidates[scores == scores.max()]
    return float(0.5 * (tied.min() + tied.max()))


def limit_above_which_untyped(
    typed_rms: Sequence[float], untyped_rms: Sequence[float], required_share: float
) -> float | None:
    """Find the score above which almost every star has no catalog type.

    Parameters
    ----------
    typed_rms : `Sequence` [`float`]
        The relative RMS of stars that have a catalog type.
    untyped_rms : `Sequence` [`float`]
        The relative RMS of stars that have none.
    required_share : `float`
        The share of stars above the limit that must have none, at the limit
        and at every higher score.

    Returns
    -------
    limit : `float` or `None`
        The lowest score from which the share holds all the way up. `None`
        when it never holds.
    """
    scores = np.concatenate([np.asarray(typed_rms, dtype=float), np.asarray(untyped_rms, dtype=float)])
    is_untyped = np.concatenate([np.zeros(len(typed_rms), dtype=bool), np.ones(len(untyped_rms), dtype=bool)])
    limit: float | None = None
    for candidate in sorted(np.unique(scores).tolist(), reverse=True)[1:]:
        above = scores > candidate
        if float(np.mean(is_untyped[above])) < required_share:
            break
        limit = float(candidate)
    return limit


def tune_match_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the poor-match limit and the no-match limit.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The limits that could be derived.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or neither limit can be derived.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    right_rms: list[float] = []
    wrong_rms: list[float] = []
    typed_rms: list[float] = []
    untyped_rms: list[float] = []
    for spectrum in spectra:
        classified = classify_with_limits_open(spectrum)
        if classified is None or classified.best_type is None or classified.best_rms is None:
            continue
        catalog_position = ladder_position(spectrum.catalog_type)
        if catalog_position is None:
            if not spectrum.catalog_type.strip():
                untyped_rms.append(classified.best_rms)
            continue
        typed_rms.append(classified.best_rms)
        own_position = ladder_position(classified.best_type)
        if own_position is None:
            continue
        if abs(own_position - catalog_position) <= RIGHT_MATCH_WITHIN_STEPS.default:
            right_rms.append(classified.best_rms)
        else:
            wrong_rms.append(classified.best_rms)
    values: dict[str, float] = {}
    poor_match = separating_limit(right_rms, wrong_rms)
    if poor_match is not None:
        values[NO_GOOD_MATCH_RMS.name] = poor_match
    no_match = limit_above_which_untyped(typed_rms, untyped_rms, UNTYPED_SHARE_ABOVE_LIMIT.default)
    if no_match is not None and (poor_match is None or no_match > poor_match):
        values[UNRELIABLE_MATCH_RMS.name] = no_match
    if not values:
        raise TuningSkippedError(
            "the stored spectra hold no right and wrong matches or no untyped stars to separate"
        )
    return TuningResult(
        values=values,
        sample=len(typed_rms) + len(untyped_rms),
        source=(
            f"Poor-match limit separates {len(right_rms)} right from {len(wrong_rms)} wrong matches; "
            f"no-match limit is where 90% of stars above it ({len(untyped_rms)} untyped) have no catalog type"
        ),
    )


MATCH_LIMITS_TUNER = Tuner(
    name="spectroscopy.match_limits",
    parameters=(NO_GOOD_MATCH_RMS, UNRELIABLE_MATCH_RMS),
    method=(
        "Classifies the stored star spectra again with the limits opened wide. The poor-match limit is "
        "the score that best separates matches within three steps of the catalog type from the others. "
        "The no-match limit "
        "is the lowest score above which 90% of stars have no catalog type."
    ),
    tune=tune_match_limits,
    per_filter=True,
)


# --- Ambiguity gap (SPE-18) --------------------------------------------------


def smallest_neighbour_distance(
    wavelength_angstrom: Sequence[np.ndarray], flux: Sequence[np.ndarray], low: float, high: float
) -> tuple[float, int]:
    """Find the smallest relative RMS distance between neighbouring references.

    Parameters
    ----------
    wavelength_angstrom : `Sequence` [`numpy.ndarray`]
        The wavelengths of each reference, in ladder order.
    flux : `Sequence` [`numpy.ndarray`]
        The blurred brightness of each reference.
    low, high : `float`
        The wavelength range to compare over, in Angstroms.

    Returns
    -------
    smallest : `float`
        The smallest distance. For each neighbouring pair, the second is
        scaled to the first by least squares and the distance is the RMS
        difference over the mean of the first.
    pair_count : `int`
        How many neighbouring pairs were compared.
    """
    distances = []
    for index in range(len(flux) - 1):
        grid = wavelength_angstrom[index]
        keep = (grid >= low) & (grid <= high)
        first = flux[index][keep]
        second = np.interp(grid[keep], wavelength_angstrom[index + 1], flux[index + 1])
        scale = float(np.dot(first, second) / np.dot(second, second))
        distances.append(float(np.sqrt(np.mean((first - scale * second) ** 2)) / np.mean(first)))
    return min(distances), len(distances)


def tune_ambiguity_gap(inputs: TuningInputs) -> TuningResult:
    """Derive the ambiguity gap from the reference ladder at this resolution.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The smallest distance between neighbouring references.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum gives a resolution.
    """
    require_spectral_filter(inputs)
    spectra = load_stored_star_spectra(inputs.service("astrometrics"))
    resolutions = [s.resolution_element_angstrom for s in spectra if s.resolution_element_angstrom]
    if not resolutions:
        raise TuningSkippedError("no stored spectrum records its resolution element")
    resolution = float(np.median(resolutions))
    blurred = [
        blurred_reference_spectrum(label, resolution_element_angstrom=resolution)
        for label in REFERENCE_SPECTRAL_TYPES
    ]
    low, high = resolve(DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM).value
    smallest, pair_count = smallest_neighbour_distance(
        [wavelength for wavelength, _ in blurred], [flux for _, flux in blurred], low, high
    )
    return TuningResult(
        values={AMBIGUOUS_RMS_GAP.name: smallest},
        sample=pair_count,
        source=(
            f"Smallest distance between {pair_count} neighbouring bundled references blurred to the median "
            f"resolution element of {len(resolutions)} stored spectra ({resolution:.0f} Å)"
        ),
    )


AMBIGUITY_GAP_TUNER = Tuner(
    name="spectroscopy.ambiguity_gap",
    parameters=(AMBIGUOUS_RMS_GAP,),
    method=(
        "Blurs the bundled reference ladder to the median resolution of the stored spectra "
        "and takes the smallest "
        "relative RMS distance between two neighbouring references."
    ),
    tune=tune_ambiguity_gap,
    per_filter=True,
)


# --- Classification limits (SPE-D47) ----------------------------------------


def coverage_levels(full_coverage: float, step: float, lowest: float) -> list[float]:
    """List the coverages to try, from the whole spectrum downward.

    Parameters
    ----------
    full_coverage : `float`
        The spectrum's whole coverage, in Angstroms.
    step : `float`
        How much less each trial covers than the one before.
    lowest : `float`
        The least coverage tried.

    Returns
    -------
    levels : `list` [`float`]
        The coverages, largest first.
    """
    levels = []
    level = full_coverage
    while level >= lowest:
        levels.append(level)
        level -= step
    return levels


def last_stable_index(letters: Sequence[str | None], full_letter: str) -> int:
    """Find how far down a list of trials the class letter stays the same.

    Parameters
    ----------
    letters : `Sequence` [`str` or `None`]
        The class letter found at each trial, from the largest coverage down.
    full_letter : `str`
        The letter found for the whole spectrum.

    Returns
    -------
    index : `int`
        The index of the last trial before the first one whose letter
        differs; -1 when the very first trial differs.
    """
    last = -1
    for index, letter in enumerate(letters):
        if letter != full_letter:
            break
        last = index
    return last


def range_from_extents(
    lows: Sequence[float], highs: Sequence[float], template_low: float, template_high: float, rounding: float
) -> tuple[float, float]:
    """Find the widest range a comparison can use.

    Parameters
    ----------
    lows, highs : `Sequence` [`float`]
        The shortest and longest wavelength of each stored spectrum.
    template_low, template_high : `float`
        The range every bundled reference covers.
    rounding : `float`
        The step the range is rounded outward to, in Angstroms.

    Returns
    -------
    widest : `tuple` [`float`, `float`]
        The range the references and the typical spectrum share.
    """
    low = float(np.floor(np.median(lows) / rounding) * rounding)
    high = float(np.ceil(np.median(highs) / rounding) * rounding)
    return max(low, template_low), min(high, template_high)


def _letter(result: dict[str, object]) -> str | None:
    """Read the class letter of a classification result.

    Parameters
    ----------
    result : `dict`
        What `classify_spectral_type` gave.

    Returns
    -------
    letter : `str` or `None`
        The first letter of the type, or `None` for an unclassified spectrum.
    """
    spectral_type = str(result["spectral_type"])
    return None if spectral_type == "Unknown" else spectral_type[0]


def tune_classification_limits(inputs: TuningInputs) -> TuningResult:
    """Derive the classifier's widest range, least coverage and fewest points.

    Parameters
    ----------
    inputs : `TuningInputs`
        What the tuner may read.

    Returns
    -------
    result : `TuningResult`
        The three values.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no stored spectrum matches well
        enough to cut short.
    """
    require_spectral_filter(inputs)
    spectra = [
        s
        for s in load_stored_star_spectra(inputs.service("astrometrics"))
        if s.response_corrected is not None
    ]
    if not spectra:
        raise TuningSkippedError("no stored spectrum has a response-corrected brightness")
    templates = _get_reference_templates()
    template_low = max(float(wavelength.min()) for wavelength, _ in templates.values())
    template_high = min(float(wavelength.max()) for wavelength, _ in templates.values())
    extents = []
    for spectrum in spectra:
        finite = spectrum.wavelength_angstrom[np.isfinite(spectrum.wavelength_angstrom)]
        extents.append((float(finite.min()), float(finite.max())))
    widest = range_from_extents(
        [low for low, _ in extents],
        [high for _, high in extents],
        template_low,
        template_high,
        RANGE_BAND_ANGSTROM.default,
    )

    poor_match = resolve(NO_GOOD_MATCH_RMS).value
    lowest_coverage = MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM.allowed_range[0]
    lowest_points = _MINIMUM_OVERLAP_POINTS.allowed_range[0]
    trial = {
        MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM.name: lowest_coverage,
        _MINIMUM_OVERLAP_POINTS.name: lowest_points,
    }
    stable_coverages: list[float] = []
    stable_points: list[float] = []
    for spectrum in spectra:
        classified = classify_with_limits_open(spectrum)
        if classified is None or classified.best_rms is None or classified.best_rms > poor_match:
            continue
        wavelength = spectrum.wavelength_angstrom
        brightness = spectrum.response_corrected
        finite = np.isfinite(wavelength) & np.isfinite(brightness) & (brightness > 0)
        if finite.sum() < 2:
            continue
        start = float(wavelength[finite].min())
        levels = coverage_levels(
            float(wavelength[finite].max()) - start, COVERAGE_STEP_ANGSTROM.default, lowest_coverage
        )
        letters: list[str | None] = []
        points: list[int] = []
        with trial_context(trial):
            for level in levels:
                kept = finite & (wavelength <= start + level)
                result = classify_spectral_type(
                    wavelength[kept],
                    brightness[kept],
                    resolution_element_angstrom=spectrum.resolution_element_angstrom,
                )
                letters.append(_letter(result))
                points.append(int((~atmospheric_band_mask(wavelength[kept])).sum()))
        stable = last_stable_index(letters, str(letters[0]) if letters and letters[0] else "")
        if stable < 0:
            continue
        stable_coverages.append(levels[stable])
        stable_points.append(float(points[stable]))
    if not stable_coverages:
        raise TuningSkippedError("no stored spectrum matches a reference well enough to cut short")
    share = STABLE_CLASS_SHARE.default
    return TuningResult(
        values={
            CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM.name: widest,
            MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM.name: float(
                np.quantile(stable_coverages, share, method="higher")
            ),
            _MINIMUM_OVERLAP_POINTS.name: int(np.quantile(stable_points, share, method="higher")),
        },
        sample=len(stable_coverages),
        source=(
            f"Range shared by the references and {len(spectra)} stored spectra; coverage and point count "
            f"where 90% of {len(stable_coverages)} well-matched spectra cut short from the red "
            "keep their class letter"
        ),
    )


CLASSIFICATION_LIMITS_TUNER = Tuner(
    name="spectroscopy.classification_limits",
    parameters=(
        CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM,
        MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM,
        _MINIMUM_OVERLAP_POINTS,
    ),
    method=(
        "Cuts well-matched stored spectra short from the red end in 200 Å steps and classifies each piece. "
        "The coverage and point count are those at which 90% of the spectra keep their class "
        "letter; the range "
        "is the one the references and the typical stored spectrum share."
    ),
    tune=tune_classification_limits,
    per_filter=True,
)
