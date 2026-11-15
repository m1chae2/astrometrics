"""Tune the absorption-feature detector's continuum bands from stored spectra.

The feature detector (`spectral_feature_detector`) measures a dip against a
continuum curve fitted to a band on each side of the feature's core. How the
bands are placed and how many samples each needs were set by hand on Vega and
two other stars. This module derives them from the spectra the library
holds, with one tuner (`FEATURE_CONTINUUM_BANDS_TUNER`):

* The curve's degree and the band width are chosen by how well the continuum
  predicts the core at control positions, where no feature lives. The core
  there is held out of the fit, so the control depth is a pure prediction
  error. The lowest degree and narrowest band within a small margin of the
  best are kept.
* The inner edge of the bands is where the measured depth of the Balmer dips
  stops changing as the bands move out, which means the line wings no longer
  leak into the continuum.
* The fewest samples in a core and in a band, and the fewest in a spectrum,
  come from how many samples real stored spectra provide for the named
  features.

It tunes the diffraction grating's spectra only. The numbers the method uses
are declared below with their reasons.
"""

import itertools
import logging
import math
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
    smallest_degree_within_tolerance,
    smallest_passing_value,
)
from astrometricslib.pipelines.spectroscopy.processing.spectral_feature_detector import (
    _CONTROL_SPACING_TOLERANCES,
    _MINIMUM_CORE_POINTS,
    _MINIMUM_SHOULDER_POINTS,
    _MINIMUM_SPECTRUM_POINTS,
    CENTER_SEARCH_TOLERANCE_ANGSTROM,
    CONTINUUM_POLYNOMIAL_DEGREE,
    MINIMUM_REPORTED_DEPTH,
    NAMED_FEATURES,
    SHOULDER_BAND_HALF_WINDOWS,
    SHOULDER_INNER_RESOLUTION_ELEMENTS,
)
from astrometricslib.pipelines.spectroscopy.tuning_data import (
    StoredSpectrum,
    quantile_of,
    spectral_target_stars,
    stored_spectra,
)

logger = logging.getLogger(__name__)


def _hardcoded(name: str, default: Any, unit: str, description: str, kind: HardcodedKind) -> TunedParameter:
    """Declare one number that the feature tuner's method uses.

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
        The hardcoded declaration, under register entry SPE-D41.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit=unit,
        description=description,
        role=ParameterRole.JUDGMENT,
        source=ParameterSource.HARDCODED,
        kind=kind,
        register_entry="SPE-D41",
    )


# The highest continuum degree tried. Above a cubic or quartic the curve can
# follow the feature's own wings through a band a few hundred Angstroms wide.
_MAXIMUM_CANDIDATE_DEGREE = _hardcoded(
    "spectroscopy.feature_tuning.maximum_candidate_degree",
    4,
    "degree",
    "The highest continuum degree the feature tuner tries.",
    HardcodedKind.METHOD_CHOICE,
)

# The band widths tried, in half-windows of the feature's core.
_CANDIDATE_BAND_HALF_WINDOWS = _hardcoded(
    "spectroscopy.feature_tuning.candidate_band_half_windows",
    (1.0, 2.0, 3.0, 4.0, 5.0, 6.0),
    "× half-window",
    "The band widths the feature tuner tries, in half-windows of the core.",
    HardcodedKind.METHOD_CHOICE,
)

# The inner edges tried, in resolution elements from the feature's center.
_CANDIDATE_INNER_ELEMENTS = _hardcoded(
    "spectroscopy.feature_tuning.candidate_inner_elements",
    (0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0),
    "× resolution",
    "The inner band edges the feature tuner tries, in resolution elements.",
    HardcodedKind.METHOD_CHOICE,
)

# How much worse than the best control scatter a lower degree or a narrower
# band may be and still win. A difference under one part in twenty is below
# what a few hundred spectra can tell apart, and the simpler choice is the
# safer one for the dip it must not absorb.
_CONTINUUM_TOLERANCE = _hardcoded(
    "spectroscopy.feature_tuning.continuum_tolerance",
    0.05,
    "fraction",
    "How much worse than the best control scatter a simpler continuum may be and still be chosen.",
    HardcodedKind.RELATIVE,
)

# The share of real cases whose counts the fewest-samples limits must let
# through. Nineteen in twenty keeps one coarsely sampled spectrum from
# deciding the limit.
_COUNT_COVERAGE = _hardcoded(
    "spectroscopy.feature_tuning.count_coverage",
    0.95,
    "fraction",
    "The share of real feature cases that the fewest-samples limits must let through.",
    HardcodedKind.STATISTICS,
)


def band_depth(
    wavelength: np.ndarray,
    intensity: np.ndarray,
    center: float,
    half_window: float,
    inner: float,
    outer: float,
    degree: int,
    minimum_core: int,
    minimum_shoulder: int,
) -> float | None:
    """Measure the depth of the core below a continuum fitted to the bands.

    This is the detector's own depth, with the bands given by the caller.

    Parameters
    ----------
    wavelength : `numpy.ndarray`
        The wavelengths, in increasing order.
    intensity : `numpy.ndarray`
        The brightness at each wavelength.
    center : `float`
        The middle of the core, in Angstroms.
    half_window : `float`
        The core's half-width, in Angstroms.
    inner, outer : `float`
        The distances from the center where each band starts and ends.
    degree : `int`
        The continuum polynomial degree.
    minimum_core, minimum_shoulder : `int`
        The fewest samples in the core and in the two bands together.

    Returns
    -------
    depth : `float` or `None`
        The mean shortfall of the core below the continuum, as a fraction of
        the continuum, or `None` when a band or the core has too few samples,
        a band lies on one side only, or the continuum is not positive.
    """
    distance = np.abs(wavelength - center)
    in_core = distance <= half_window
    in_shoulder = (distance > inner) & (distance <= outer)
    if in_core.sum() < minimum_core or in_shoulder.sum() < max(minimum_shoulder, degree + 2):
        return None
    shoulder_wavelength = wavelength[in_shoulder]
    if not ((shoulder_wavelength < center).any() and (shoulder_wavelength > center).any()):
        return None
    coefficients = np.polyfit(shoulder_wavelength - center, intensity[in_shoulder], degree)
    continuum = np.polyval(coefficients, wavelength[in_core] - center)
    if continuum.mean() <= 0.0:
        return None
    return float(np.mean((continuum - intensity[in_core]) / continuum))


def control_centers(
    wavelength: np.ndarray,
    reach: float,
    step: float,
    feature_centers: np.ndarray,
    keep_clear: float,
) -> np.ndarray:
    """Choose control wavelengths clear of the named features.

    Parameters
    ----------
    wavelength : `numpy.ndarray`
        The wavelengths, in increasing order.
    reach : `float`
        How far a control's search window and bands extend from its center.
    step : `float`
        The spacing of the controls, in Angstroms.
    feature_centers : `numpy.ndarray`
        The rest wavelengths of the named features.
    keep_clear : `float`
        How far a control must be from every named feature.

    Returns
    -------
    centers : `numpy.ndarray`
        The control wavelengths.
    """
    first = wavelength[0] + reach
    last = wavelength[-1] - reach
    if last <= first:
        return np.array([])
    centers = np.arange(first, last + 1e-9, step)
    far = np.all(np.abs(centers[:, np.newaxis] - feature_centers[np.newaxis, :]) > keep_clear, axis=1)
    return centers[far]


def control_scatter(depths: list[float]) -> float | None:
    """Measure the spread of the control depths of one spectrum.

    Parameters
    ----------
    depths : `list` [`float`]
        The depth at each control.

    Returns
    -------
    scatter : `float` or `None`
        The scaled median absolute deviation, or `None` with fewer than
        three depths.
    """
    if len(depths) < 3:
        return None
    values = np.asarray(depths)
    return 1.4826 * float(np.median(np.abs(values - np.median(values))))


def feature_geometry(
    half_window: float, resolution: float, inner_elements: float, band_half_windows: float
) -> tuple[float, float]:
    """Give where a feature's continuum bands start and end.

    This is the detector's own rule (see `_shoulder_edges`).

    Parameters
    ----------
    half_window : `float`
        The core's half-width, in Angstroms.
    resolution : `float`
        The resolution element, in Angstroms.
    inner_elements : `float`
        The inner edge, in resolution elements.
    band_half_windows : `float`
        The band width, in core half-windows.

    Returns
    -------
    inner, outer : `tuple` [`float`, `float`]
        The distances from the center where each band starts and ends.
    """
    inner = max(half_window, inner_elements * resolution)
    return inner, inner + band_half_windows * half_window


def moving_band_changes(depths_by_edge: list[list[float | None]]) -> list[float | None]:
    """Find how much the median depth changes between neighboring inner edges.

    Parameters
    ----------
    depths_by_edge : `list` [`list` [`float` or `None`]]
        For each candidate inner edge in increasing order, the depth of each
        case (the same cases in the same order), or `None` where it could
        not be measured.

    Returns
    -------
    changes : `list` [`float` or `None`]
        For each candidate but the last, the median over the cases of the
        absolute depth change to the next candidate, or `None` when no case
        was measured at both.
    """
    changes: list[float | None] = []
    for current, following in itertools.pairwise(depths_by_edge):
        differences = [
            abs(first - second)
            for first, second in zip(current, following, strict=True)
            if first is not None and second is not None
        ]
        changes.append(float(np.median(differences)) if differences else None)
    return changes


def sample_counts(
    spectrum: StoredSpectrum,
    resolution: float,
    inner_elements: float,
    band_half_windows: float,
) -> list[tuple[int, int]]:
    """Count the core and band samples of each fully covered named feature.

    Parameters
    ----------
    spectrum : `StoredSpectrum`
        The spectrum.
    resolution : `float`
        The spectrum's resolution element, in Angstroms.
    inner_elements : `float`
        The inner band edge, in resolution elements.
    band_half_windows : `float`
        The band width, in core half-windows.

    Returns
    -------
    counts : `list` [`tuple` [`int`, `int`]]
        For each named feature whose bands lie wholly inside the spectrum,
        the samples in its core and in its two bands together.
    """
    wavelength = spectrum.wavelength_angstrom
    counts = []
    for feature in NAMED_FEATURES:
        center = float(feature["wavelength_angstrom"])  # type: ignore[arg-type]
        half_window = float(feature["window_angstrom"])  # type: ignore[arg-type]
        inner, outer = feature_geometry(half_window, resolution, inner_elements, band_half_windows)
        if center - outer < wavelength[0] or center + outer > wavelength[-1]:
            continue
        distance = np.abs(wavelength - center)
        counts.append((
            int((distance <= half_window).sum()),
            int(((distance > inner) & (distance <= outer)).sum()),
        ))
    return counts


def spectrum_point_limit(spans: list[float], coverage: float) -> int:
    """Find the fewest samples that let a stored spectrum hold one feature.

    Parameters
    ----------
    spans : `list` [`float`]
        For each spectrum, the samples that its narrowest feature's whole
        measurement (core and both bands) spans at that spectrum's sample
        spacing.
    coverage : `float`
        The share of spectra the limit must be enough for.

    Returns
    -------
    count : `int`
        The coverage quantile of the spans, rounded up.
    """
    return math.ceil(quantile_of(spans, coverage))


def _resolution_of(spectrum: StoredSpectrum) -> float:
    """Give a spectrum's resolution element, or the fallback.

    Returns
    -------
    resolution : `float`
        The resolution element, in Angstroms.
    """
    return spectrum.resolution_element_angstrom or resolve(FALLBACK_RESOLUTION_ELEMENT_ANGSTROM).value


def _median_control_scatter(
    spectra: list[StoredSpectrum], degree: int, inner_elements: float, band_half_windows: float
) -> float | None:
    """Find the typical control scatter for one continuum choice.

    Returns
    -------
    scatter : `float` or `None`
        The median over the spectra of their control-depth scatter, or
        `None` when no spectrum has controls.
    """
    tolerance = resolve(CENTER_SEARCH_TOLERANCE_ANGSTROM).value
    step = resolve(_CONTROL_SPACING_TOLERANCES).value * tolerance
    feature_centers = np.array([float(feature["wavelength_angstrom"]) for feature in NAMED_FEATURES])  # type: ignore[arg-type]
    windows = [float(feature["window_angstrom"]) for feature in NAMED_FEATURES]  # type: ignore[arg-type]
    half_window = float(np.median(windows))
    minimum_core = int(resolve(_MINIMUM_CORE_POINTS).value)
    minimum_shoulder = int(resolve(_MINIMUM_SHOULDER_POINTS).value)
    scatters = []
    for spectrum in spectra:
        inner, outer = feature_geometry(
            half_window, _resolution_of(spectrum), inner_elements, band_half_windows
        )
        centers = control_centers(
            spectrum.wavelength_angstrom,
            tolerance + outer,
            step,
            feature_centers,
            tolerance + half_window + max(windows),
        )
        depths = []
        for center in centers:
            depth = band_depth(
                spectrum.wavelength_angstrom,
                spectrum.intensity,
                float(center),
                half_window,
                inner,
                outer,
                degree,
                minimum_core,
                minimum_shoulder,
            )
            if depth is not None:
                depths.append(depth)
        scatter = control_scatter(depths)
        if scatter is not None:
            scatters.append(scatter)
    return float(np.median(scatters)) if scatters else None


def _balmer_depths(
    spectra: list[StoredSpectrum], inner_elements: float, band_half_windows: float, degree: int
) -> list[float | None]:
    """Measure the deepest Balmer dip of each case for one inner edge.

    Returns
    -------
    depths : `list` [`float` or `None`]
        One depth per (spectrum, Balmer feature) case, in a fixed order, or
        `None` where it could not be measured. The depth is the largest over
        centers within the search tolerance of the rest wavelength.
    """
    tolerance = resolve(CENTER_SEARCH_TOLERANCE_ANGSTROM).value
    minimum_core = int(resolve(_MINIMUM_CORE_POINTS).value)
    minimum_shoulder = int(resolve(_MINIMUM_SHOULDER_POINTS).value)
    depths: list[float | None] = []
    for spectrum in spectra:
        wavelength = spectrum.wavelength_angstrom
        inner_resolution = _resolution_of(spectrum)
        for feature in NAMED_FEATURES:
            if "Balmer" not in str(feature["name"]):
                continue
            rest = float(feature["wavelength_angstrom"])  # type: ignore[arg-type]
            half_window = float(feature["window_angstrom"])  # type: ignore[arg-type]
            inner, outer = feature_geometry(half_window, inner_resolution, inner_elements, band_half_windows)
            candidate_centers = wavelength[np.abs(wavelength - rest) <= tolerance]
            measured = [
                depth
                for center in candidate_centers
                if (
                    depth := band_depth(
                        wavelength,
                        spectrum.intensity,
                        float(center),
                        half_window,
                        inner,
                        outer,
                        degree,
                        minimum_core,
                        minimum_shoulder,
                    )
                )
                is not None
            ]
            depths.append(max(measured) if measured else None)
    return depths


def tune_feature_continuum_bands(inputs: TuningInputs) -> TuningResult:
    """Tune the continuum degree, the bands and the fewest samples.

    Parameters
    ----------
    inputs : `TuningInputs`
        The setup, the grating filter and the ``"astrometrics"`` service.

    Returns
    -------
    result : `TuningResult`
        The six values, from the stored spectra of the setup's camera.

    Raises
    ------
    TuningSkippedError
        If the filter is not the grating or no spectrum is stored.
    """
    if inputs.filter_name != FilterType.SPEC.name:
        raise TuningSkippedError("only the diffraction grating has stored spectra")
    astrometrics: Any = inputs.service("astrometrics")
    spectra = stored_spectra(
        spectral_target_stars(astrometrics, inputs.setup.camera_name, inputs.configuration)
    )
    if not spectra:
        raise TuningSkippedError("no stored spectrum of this camera")
    tolerance = resolve(_CONTINUUM_TOLERANCE).value
    inner_elements = resolve(SHOULDER_INNER_RESOLUTION_ELEMENTS).value
    band_half_windows = resolve(SHOULDER_BAND_HALF_WINDOWS).value
    values: dict[str, float | int] = {}

    # The degree: the lowest whose control scatter is near the best.
    scatter_by_degree = {}
    for degree in range(1, int(resolve(_MAXIMUM_CANDIDATE_DEGREE).value) + 1):
        scatter = _median_control_scatter(spectra, degree, inner_elements, band_half_windows)
        if scatter is not None:
            scatter_by_degree[degree] = scatter
    degree = smallest_degree_within_tolerance(scatter_by_degree, tolerance)
    if degree is None:
        raise TuningSkippedError("no stored spectrum has room for control positions")
    values[CONTINUUM_POLYNOMIAL_DEGREE.name] = degree

    # The band width: the narrowest whose control scatter is near the best.
    widths = [float(width) for width in resolve(_CANDIDATE_BAND_HALF_WINDOWS).value]
    scatter_by_width = {}
    for width in widths:
        scatter = _median_control_scatter(spectra, degree, inner_elements, width)
        if scatter is not None:
            scatter_by_width[width] = scatter
    if scatter_by_width:
        best = min(scatter_by_width.values())
        band_half_windows = next(
            width for width in sorted(scatter_by_width) if scatter_by_width[width] <= (1.0 + tolerance) * best
        )
        values[SHOULDER_BAND_HALF_WINDOWS.name] = band_half_windows

    # The inner edge: where the Balmer dips stop deepening.
    edges = [float(edge) for edge in resolve(_CANDIDATE_INNER_ELEMENTS).value]
    depths_by_edge = [_balmer_depths(spectra, edge, band_half_windows, degree) for edge in edges]
    minimum_depth = resolve(MINIMUM_REPORTED_DEPTH).value
    real_dips = [
        case
        for case in range(len(depths_by_edge[0]))
        if all(depths[case] is not None and depths[case] >= minimum_depth for depths in depths_by_edge[-1:])
    ]
    if real_dips:
        changes = moving_band_changes([[depths[case] for case in real_dips] for depths in depths_by_edge])
        settled = [None if change is None else float(change <= minimum_depth) for change in changes]
        inner_edge = smallest_passing_value(edges[:-1], settled, 1.0)
        if inner_edge is not None:
            values[SHOULDER_INNER_RESOLUTION_ELEMENTS.name] = inner_edge
            inner_elements = inner_edge

    # The fewest samples: what real spectra give the named features.
    coverage = resolve(_COUNT_COVERAGE).value
    core_counts: list[int] = []
    shoulder_counts: list[int] = []
    spans: list[float] = []
    narrowest = min(float(feature["window_angstrom"]) for feature in NAMED_FEATURES)  # type: ignore[arg-type]
    for spectrum in spectra:
        resolution = _resolution_of(spectrum)
        for core, shoulder in sample_counts(spectrum, resolution, inner_elements, band_half_windows):
            core_counts.append(core)
            shoulder_counts.append(shoulder)
        _, outer = feature_geometry(narrowest, resolution, inner_elements, band_half_windows)
        spans.append(2.0 * outer / spectrum.sample_spacing_angstrom + 1.0)
    if core_counts:
        values[_MINIMUM_CORE_POINTS.name] = max(1, math.floor(quantile_of(core_counts, 1.0 - coverage)))
        # The fit uses degree + 1 numbers; the scatter needs one more.
        values[_MINIMUM_SHOULDER_POINTS.name] = max(
            degree + 2, math.floor(quantile_of(shoulder_counts, 1.0 - coverage))
        )
    values[_MINIMUM_SPECTRUM_POINTS.name] = spectrum_point_limit(spans, coverage)
    return TuningResult(
        values=values,
        sample=len(spectra),
        source=(
            f"Control-position scatter, Balmer dip depth against band edge and feature sample counts of "
            f"{len(spectra)} stored spectra of this camera"
        ),
    )


FEATURE_CONTINUUM_BANDS_TUNER = Tuner(
    name="spectroscopy.feature_continuum_bands",
    parameters=(
        CONTINUUM_POLYNOMIAL_DEGREE,
        SHOULDER_INNER_RESOLUTION_ELEMENTS,
        SHOULDER_BAND_HALF_WINDOWS,
        _MINIMUM_SHOULDER_POINTS,
        _MINIMUM_CORE_POINTS,
        _MINIMUM_SPECTRUM_POINTS,
    ),
    method=(
        "On the stored spectra of the grating: the lowest degree and narrowest band whose control-position "
        "depth scatter is near the best; the inner band edge where the Balmer dip depth stops changing; "
        "and the fewest core, band and spectrum samples that nineteen in twenty real feature cases provide."
    ),
    tune=tune_feature_continuum_bands,
    per_filter=True,
)
