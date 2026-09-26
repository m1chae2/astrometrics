"""Looks for named emission lines in the spectrum of a glowing gas cloud.

A star's spectrum is a smooth glow with dark dips (absorption). A
planetary nebula such as M 57 is the opposite: a glowing gas shell that
shines only at a few wavelengths, so its spectrum is a faint continuum
with bright humps (emission). The dip detector in
`spectral_feature_detector` cannot describe that, so this module looks for
humps instead.

Two things make the humps different from a lab spectrum:

* **Wide lines.** A slitless grism has no slit, so every part of the
  nebula makes its own copy of each line, shifted sideways by where that
  part sits. The line is as wide as the nebula's image is across, not as
  wide as the instrument's blur. For M 57 the extraction box is 37 pixels
  wide, about 400 Angstroms at 11 Angstroms per pixel. Each line is
  therefore modelled as a flat-topped
  "box" whose half-width the caller supplies, softened by the instrument
  blur (see `_box_profile`).
* **Blends.** Boxes 400 Angstroms wide overlap their neighbors, so H-beta
  and the two [O III] lines cannot be told apart. The fit still finds the
  best mix, but lines whose boxes look too alike are reported together as
  one blend, with the amplitudes added up, instead of pretending they were
  separated.

How the fit works: the spectrum is modelled as a smooth continuum plus a
non-negative amount of each named line (a light cannot be negative). How
sure we are of each amount comes from the fit's own error bars, made
wider to allow for neighboring samples not being independent. A line is
called detected only when its amount is many error bars above zero.

Nothing above `MAXIMUM_FITTED_WAVELENGTH_ANGSTROM` is used, because the
instrument response is not valid there and second-order light lands there.
Line-strength ratios are not reported, since there is no correction for
dust and the response is not trustworthy in the red.

Every threshold here was set using M 57 only, the one nebula in the
library with a usable spectrum, so none of them is known to hold for other
nebulae.
"""

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import lsq_linear
from scipy.special import erf

from astrometricslib.pipelines.spectroscopy.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
)

# The answers this module gives for each line or blend.
VERDICT_DETECTED = "detected"
VERDICT_UNCLEAR = "unclear"
VERDICT_NOT_SEEN = "not_seen"
VERDICT_NOT_COVERED = "not_covered"

# Emission lines a nebula spectrum can show between the blue limit and
# `MAXIMUM_FITTED_WAVELENGTH_ANGSTROM`. Each entry is a name and the
# (rest wavelength in Angstroms, relative weight) of its member lines. The
# weights only set where the entry's box is centered, using the standard
# atomic ratios ([N II] 6584 is 3 times 6548). At the box widths used here
# the exact weights hardly change the model. The list is a first pass; it
# was not tuned on any data.
NEBULA_LINE_GROUPS: tuple[tuple[str, tuple[tuple[float, float], ...]], ...] = (
    ("He II 4686", ((4686.0, 1.0),)),
    ("H-beta", ((4861.0, 1.0),)),
    ("[O III] 4959+5007", ((4959.0, 1.0), (5007.0, 3.0))),
    ("He I 5876", ((5876.0, 1.0),)),
    ("[O I] 6300", ((6300.0, 1.0),)),
    ("H-alpha + [N II]", ((6548.0, 1.0), (6563.0, 3.0), (6584.0, 3.0))),
    ("[S II] 6717+6731", ((6717.0, 1.0), (6731.0, 1.0))),
    ("[Ar III] 7136", ((7136.0, 1.0),)),
)

# Light above this wavelength is never used: the instrument response is
# only valid up to 8000 A, and second-order light (blue light showing up at
# twice its wavelength) starts to matter from about 7600 A.
MAXIMUM_FITTED_WAVELENGTH_ANGSTROM = 8000.0

# The continuum is a polynomial of this degree. 2 is a gentle curve: a
# higher degree could bend into a hump 600 A wide and hide it. Chosen for
# M 57 only.
CONTINUUM_POLYNOMIAL_DEGREE = 2

# The fewest samples a spectrum needs before a fit is attempted.
_MINIMUM_SPECTRUM_POINTS = 30

# The caller should pass the box half-width as (extraction box width in
# pixels / 2) x (Angstroms per pixel). For M 57 that is 37 / 2 x 11.04 =
# 204 A. Scanning the half-width on M 57 (2026-09-23) gave the strongest
# fit at 200 A (blend 21.9 error bars, H-alpha 6.0) and weaker, blurrier
# results at 100 A and at 300 A (blend 6.6, H-alpha 2.6), so the box-width
# rule and the data agree.
#
# Lines whose boxes look at least this alike (correlation, after the
# smooth continuum is removed) are reported together as one blend. Measured
# at half-width 300 A (not re-measured at 204 A):
# H-beta vs [O III] 0.69, H-alpha vs [S II] 0.66, He II vs H-beta 0.52, and
# every other pair below 0.45. 0.6 is a judgement call that merges the
# first two pairs and no others. Validated on M 57 only.
BLEND_CORRELATION_THRESHOLD = 0.6

# How many error bars above zero a line must be. 5 means "very unlikely to
# be noise"; between 3 and 5 is called unclear. Judgement calls, checked
# against noise-only spectra in the tests and against M 57 only.
DETECTED_SIGNIFICANCE = 5.0
UNCLEAR_SIGNIFICANCE = 3.0

# A line is "covered" only when at least this much of its box lies inside
# the wavelength range that was actually fitted. Half is a judgement call.
MINIMUM_COVERED_FRACTION = 0.5

# A spectrum is called an emission-line source when at least this many
# separate lines or blends are detected. Two is the least that tells a
# nebula from one noisy bump. Validated on M 57 only.
MINIMUM_DETECTED_FOR_EMISSION_SOURCE = 2

# The noise inflation for correlated samples is never allowed to be larger
# than this fraction of the sample count, so a nearly perfect residual
# autocorrelation cannot make the error bars infinite.
_MAXIMUM_INFLATION_FRACTION = 0.25


@dataclass(frozen=True)
class _LineColumn:
    """One named line or group of lines, as a column of the fit.

    Attributes
    ----------
    name : `str`
        The group's display name.
    rest_wavelengths_angstrom : `tuple` [`float`, ...]
        The rest wavelengths of the group's member lines.
    profile : `numpy.ndarray`
        The group's box model on the spectrum's wavelength grid.
    covered_fraction : `float`
        How much of the box lies inside the fitted range (0 to 1).
    """

    name: str
    rest_wavelengths_angstrom: tuple[float, ...]
    profile: np.ndarray
    covered_fraction: float


def _box_profile(
    wavelength_angstrom: np.ndarray,
    center_angstrom: float,
    half_width_angstrom: float,
    blur_sigma_angstrom: float,
) -> np.ndarray:
    """Make a flat-topped box softened by the instrument blur.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The wavelengths to evaluate the box at.
    center_angstrom : `float`
        The middle of the box.
    half_width_angstrom : `float`
        Half the box's width (how far the line spreads either side of its
        center because the source is not a point).
    blur_sigma_angstrom : `float`
        The Gaussian softening of the box's edges (the instrument blur).

    Returns
    -------
    profile : `numpy.ndarray`
        1 in the middle of the box, falling to 0 outside, with soft edges.
    """
    scale = math.sqrt(2.0) * max(blur_sigma_angstrom, 1e-6)
    upper = (wavelength_angstrom - center_angstrom + half_width_angstrom) / scale
    lower = (wavelength_angstrom - center_angstrom - half_width_angstrom) / scale
    return 0.5 * (erf(upper) - erf(lower))


def _build_columns(
    wavelength_angstrom: np.ndarray,
    half_width_angstrom: float,
    blur_sigma_angstrom: float,
) -> list[_LineColumn]:
    """Build the model column for each named line group.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The fitted wavelength grid, in increasing order.
    half_width_angstrom : `float`
        Half the width of each line's box.
    blur_sigma_angstrom : `float`
        The Gaussian softening of the box edges.

    Returns
    -------
    columns : `list` [`_LineColumn`]
        One column per entry of `NEBULA_LINE_GROUPS`, in the same order.
    """
    columns: list[_LineColumn] = []
    for name, members in NEBULA_LINE_GROUPS:
        total_weight = sum(weight for _, weight in members)
        profile = np.zeros_like(wavelength_angstrom)
        for rest_wavelength, weight in members:
            profile += weight * _box_profile(
                wavelength_angstrom, rest_wavelength, half_width_angstrom, blur_sigma_angstrom
            )
        profile /= total_weight

        weighted_center = sum(rest * weight for rest, weight in members) / total_weight
        box_low = weighted_center - half_width_angstrom
        box_high = weighted_center + half_width_angstrom
        inside = min(box_high, wavelength_angstrom[-1]) - max(box_low, wavelength_angstrom[0])
        covered_fraction = float(np.clip(inside / (box_high - box_low), 0.0, 1.0))
        columns.append(
            _LineColumn(
                name=name,
                rest_wavelengths_angstrom=tuple(rest for rest, _ in members),
                profile=profile,
                covered_fraction=covered_fraction,
            )
        )
    return columns


def _group_into_blends(columns: list[_LineColumn], continuum_design: np.ndarray) -> list[list[int]]:
    """Group the lines whose boxes look too alike to tell apart.

    Parameters
    ----------
    columns : `list` [`_LineColumn`]
        The covered line columns.
    continuum_design : `numpy.ndarray`
        The continuum's polynomial columns (samples by terms), which are
        removed from each line before comparing, since a smooth trend is
        not what tells two lines apart.

    Returns
    -------
    blends : `list` [`list` [`int`]]
        Indices into `columns`, one list per blend. A line with no look-alike
        is a blend of one.
    """
    projection = continuum_design @ np.linalg.pinv(continuum_design)
    residual_profiles = [column.profile - projection @ column.profile for column in columns]

    parent = list(range(len(columns)))

    def find(index: int) -> int:
        """Find the blend an index currently belongs to.

        Parameters
        ----------
        index : `int`
            The column index to look up.

        Returns
        -------
        root : `int`
            The index that stands for the blend.
        """
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    for first in range(len(columns)):
        for second in range(first + 1, len(columns)):
            norm_product = np.linalg.norm(residual_profiles[first]) * np.linalg.norm(
                residual_profiles[second]
            )
            if norm_product <= 0.0:
                continue
            correlation = float(residual_profiles[first] @ residual_profiles[second] / norm_product)
            if correlation >= BLEND_CORRELATION_THRESHOLD:
                parent[find(second)] = find(first)

    grouped: dict[int, list[int]] = {}
    for index in range(len(columns)):
        grouped.setdefault(find(index), []).append(index)
    return list(grouped.values())


def _correlated_noise_inflation(residual: np.ndarray) -> float:
    """Estimate how much neighboring samples' shared noise widens error bars.

    Parameters
    ----------
    residual : `numpy.ndarray`
        The fit residuals, in wavelength order.

    Returns
    -------
    inflation : `float`
        The factor to multiply the noise variance by, at least 1. It uses
        the lag-1 correlation r of the residuals as (1 + r) / (1 - r), the
        standard correction for noise that carries over from one sample to
        the next.
    """
    centered = residual - residual.mean()
    denominator = float(centered @ centered)
    if denominator <= 0.0:
        return 1.0
    lag_one = float(centered[:-1] @ centered[1:]) / denominator
    lag_one = min(max(lag_one, 0.0), 0.999)
    inflation = (1.0 + lag_one) / (1.0 - lag_one)
    return float(min(inflation, max(1.0, _MAXIMUM_INFLATION_FRACTION * residual.size)))


def detect_emission_lines(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    line_half_width_angstrom: float,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
) -> list[dict[str, object]]:
    """Test a spectrum for each named nebula emission line.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelength grid, in Angstroms.
    intensity : `numpy.ndarray`
        The spectrum's brightness at each wavelength.
    line_half_width_angstrom : `float`
        Half the width each line spreads to because the source is not a
        point: the source's radius in pixels times the spectrum's
        Angstroms per pixel. For a point-like source pass a value near the
        resolution element.
    resolution_element_angstrom : `float`, optional
        How much the instrument blurs one wavelength, in Angstroms (see
        `spectral_resolution`). It softens the box edges.

    Returns
    -------
    lines : `list` [`dict`]
        One entry per line or blend, most convincing first. Every entry has
        ``"line"`` (display name; blends are joined with " / "),
        ``"members"`` (the named lines it stands for),
        ``"rest_wavelengths_angstrom"`` (their rest wavelengths),
        ``"is_blend"`` (`True` when more than one named line is inside),
        ``"second_order_ghost_angstrom"`` (twice the lightest member's
        wavelength, where its second-order copy lands; not fitted) and
        ``"verdict"`` (`VERDICT_DETECTED`, `VERDICT_UNCLEAR`,
        `VERDICT_NOT_SEEN` or `VERDICT_NOT_COVERED`). A covered entry also
        has ``"amplitude"`` (height above the continuum, in the spectrum's
        own units), ``"amplitude_uncertainty"`` and ``"significance"``
        (amplitude divided by its uncertainty). Empty when the spectrum is
        too short to fit.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    brightness = np.asarray(intensity, dtype=float)
    valid = (
        np.isfinite(wavelengths)
        & np.isfinite(brightness)
        & (wavelengths <= MAXIMUM_FITTED_WAVELENGTH_ANGSTROM)
    )
    wavelengths = wavelengths[valid]
    brightness = brightness[valid]
    if wavelengths.size < _MINIMUM_SPECTRUM_POINTS:
        return []
    order = np.argsort(wavelengths)
    wavelengths = wavelengths[order]
    brightness = brightness[order]

    # Blur (Gaussian sigma) from the resolution element, taken as FWHM.
    blur_sigma = resolution_element_angstrom / 2.355
    all_columns = _build_columns(wavelengths, line_half_width_angstrom, blur_sigma)
    covered = [column for column in all_columns if column.covered_fraction >= MINIMUM_COVERED_FRACTION]

    entries: list[dict[str, object]] = []
    covered_names = {column.name for column in covered}
    for column in all_columns:
        if column.name not in covered_names:
            entries.append(_entry_for([column], VERDICT_NOT_COVERED))

    if covered:
        entries.extend(_fit_covered(wavelengths, brightness, covered))

    verdict_rank = {VERDICT_DETECTED: 0, VERDICT_UNCLEAR: 1, VERDICT_NOT_SEEN: 2, VERDICT_NOT_COVERED: 3}
    entries.sort(
        key=lambda entry: (
            verdict_rank[str(entry["verdict"])],
            -float(entry.get("significance", 0.0)),  # type: ignore[arg-type]
        )
    )
    return entries


def _fit_covered(
    wavelengths: np.ndarray, brightness: np.ndarray, covered: list[_LineColumn]
) -> list[dict[str, object]]:
    """Fit the continuum and covered lines, and score each blend.

    Parameters
    ----------
    wavelengths : `numpy.ndarray`
        The fitted wavelength grid, in increasing order.
    brightness : `numpy.ndarray`
        The brightness at those wavelengths.
    covered : `list` [`_LineColumn`]
        The line columns that lie inside the fitted range.

    Returns
    -------
    entries : `list` [`dict`]
        One entry per blend of covered lines.
    """
    normalized = 2.0 * (wavelengths - wavelengths[0]) / (wavelengths[-1] - wavelengths[0]) - 1.0
    continuum_design = np.vander(normalized, CONTINUUM_POLYNOMIAL_DEGREE + 1, increasing=True)
    continuum_term_count = continuum_design.shape[1]
    line_design = np.column_stack([column.profile for column in covered])
    design = np.hstack([continuum_design, line_design])

    lower_bounds = np.concatenate([np.full(continuum_term_count, -np.inf), np.zeros(len(covered))])
    solution = lsq_linear(design, brightness, bounds=(lower_bounds, np.full(design.shape[1], np.inf)))
    coefficients = solution.x
    residual = brightness - design @ coefficients

    degrees_of_freedom = max(brightness.size - design.shape[1], 1)
    noise_variance = float(residual @ residual) / degrees_of_freedom
    noise_variance *= _correlated_noise_inflation(residual)
    covariance = noise_variance * np.linalg.pinv(design.T @ design)

    entries: list[dict[str, object]] = []
    for blend in _group_into_blends(covered, continuum_design):
        indices = np.array([continuum_term_count + index for index in blend])
        amplitude = float(coefficients[indices].sum())
        variance = float(covariance[np.ix_(indices, indices)].sum())
        uncertainty = math.sqrt(max(variance, 0.0))
        significance = amplitude / uncertainty if uncertainty > 0.0 else 0.0
        if significance >= DETECTED_SIGNIFICANCE:
            verdict = VERDICT_DETECTED
        elif significance >= UNCLEAR_SIGNIFICANCE:
            verdict = VERDICT_UNCLEAR
        else:
            verdict = VERDICT_NOT_SEEN
        entry = _entry_for([covered[index] for index in blend], verdict)
        entry["amplitude"] = amplitude
        entry["amplitude_uncertainty"] = uncertainty
        entry["significance"] = significance
        entries.append(entry)
    return entries


def _entry_for(columns: list[_LineColumn], verdict: str) -> dict[str, object]:
    """Make the result entry describing one line or blend.

    Parameters
    ----------
    columns : `list` [`_LineColumn`]
        The named lines that make up the entry.
    verdict : `str`
        The verdict to store.

    Returns
    -------
    entry : `dict`
        The entry without any fit numbers.
    """
    rest_wavelengths = [wavelength for column in columns for wavelength in column.rest_wavelengths_angstrom]
    return {
        "line": " / ".join(column.name for column in columns),
        "members": [column.name for column in columns],
        "rest_wavelengths_angstrom": rest_wavelengths,
        "is_blend": len(columns) > 1,
        "second_order_ghost_angstrom": 2.0 * min(rest_wavelengths),
        "verdict": verdict,
    }


def line_half_width_angstrom(wavelength_angstrom: np.ndarray, extraction_box_width_px: float) -> float:
    """Work out how far a line spreads either side of its center.

    Parameters
    ----------
    wavelength_angstrom : `numpy.ndarray`
        The spectrum's wavelength grid, in Angstroms (one sample per pixel
        along the trail).
    extraction_box_width_px : `float`
        The width, in pixels, of the box the spectrum was extracted from
        across the trail. It is as wide as the source's image, so it is how
        far a line smears sideways.

    Returns
    -------
    half_width_angstrom : `float`
        Half the box width times the Angstroms per pixel. Measured on M 57:
        37 px at 11.04 A per pixel gives 204 A, which is also where the
        line fit is strongest.
    """
    wavelengths = np.asarray(wavelength_angstrom, dtype=float)
    angstrom_per_pixel = float(np.median(np.diff(wavelengths))) if wavelengths.size > 1 else 0.0
    return 0.5 * float(extraction_box_width_px) * abs(angstrom_per_pixel)


def is_emission_line_source(lines: list[dict[str, object]]) -> bool:
    """Say whether a spectrum looks like glowing gas rather than a star.

    Parameters
    ----------
    lines : `list` [`dict`]
        The entries from `detect_emission_lines`.

    Returns
    -------
    is_emission_source : `bool`
        `True` when at least `MINIMUM_DETECTED_FOR_EMISSION_SOURCE` lines or
        blends were detected.
    """
    detected_count = sum(1 for entry in lines if entry["verdict"] == VERDICT_DETECTED)
    return detected_count >= MINIMUM_DETECTED_FOR_EMISSION_SOURCE
