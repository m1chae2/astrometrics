"""Guesses a star's broad spectral type by matching its spectrum to references.

A slitless grism can't resolve spectral lines finely enough to derive a
star's physical properties from first principles, and it can't tell you
a catalog name either. What it can do is compare the overall shape of an
observed spectrum -- continuum slope, Balmer line strength -- against a
library of known reference stars and report which one it most resembles.
That's enough to place a star in its broad O/B/A/F/G/K/M class, using the
same technique real low-resolution spectroscopy tools use, without ever
looking the star up.
"""

import csv
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from astrometricslib.models.stellar_source import StellarObject

_TEMPLATE_DIR = Path(__file__).parent / "data"

# The reference spectral types this classifier ships with, drawn from the
# Pickles (1998) stellar flux library (Pickles, A.J. 1998, PASP, 110, 863).
# Each covers 3500-8000 A at 5 A sampling, normalized to 1.0 at 5556 A --
# enough range to capture the Balmer lines and the overall continuum slope
# a low-resolution slitless grism can actually resolve.
REFERENCE_SPECTRAL_TYPES: tuple[str, ...] = (
    "O5V",
    "B0V",
    "B8V",
    "A0V",
    "A5V",
    "F0V",
    "F5V",
    "G0V",
    "G5V",
    "K0V",
    "K5V",
    "M0V",
    "M5V",
)

# Below this many overlapping points, a correlation is too noisy to trust.
_MINIMUM_OVERLAP_POINTS = 20
# Reference and observed spectra must share at least this much real
# wavelength range (in Angstroms) before they're compared at all.
_MINIMUM_OVERLAP_ANGSTROM = 500.0
# Softmax temperature (in correlation-coefficient units) used to turn
# correlations into a probability-like ranking. Correlations for the
# right type are typically 0.99+ while wrong types land around 0.85-0.98,
# so a small temperature is needed for the ranking to separate them at
# all instead of spreading probability almost evenly across every type.
_RANKING_SOFTMAX_TEMPERATURE = 0.02

# Below this winning correlation, the "best" match still doesn't
# resemble the observed spectrum closely enough to trust on its own
# (a guess, like _MINIMUM_OVERLAP_POINTS above -- may need tuning
# against more real data).
LOW_CONFIDENCE_THRESHOLD = 0.5

# When the top two ranked types' probabilities are closer than this,
# the classifier can't meaningfully tell them apart, and reporting only
# the winner would hide a near-tie.
AMBIGUOUS_PROBABILITY_MARGIN = 0.15

_reference_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}


def _load_reference_template(spectral_type: str) -> tuple[np.ndarray, np.ndarray]:
    """Read one bundled reference spectrum from disk.

    Returns
    -------
    wavelength_angstrom, normalized_flux : `tuple` [`np.ndarray`, `np.ndarray`]
        The reference star's wavelength grid and its flux, normalized to
        1.0 at 5556 A per the source library's convention.
    """
    # .txt, not .csv: this repo's .gitattributes routes *.csv through Git
    # LFS, and CI's checkout doesn't fetch LFS content, so these tiny
    # bundled tables need to stay plain blobs.
    path = _TEMPLATE_DIR / f"{spectral_type.lower()}.txt"
    wavelengths = []
    fluxes = []
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            wavelengths.append(float(row["wavelength_angstrom"]))
            fluxes.append(float(row["normalized_flux"]))
    return np.array(wavelengths), np.array(fluxes)


def _get_reference_templates() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Load and cache every bundled reference spectrum.

    Returns
    -------
    templates : `dict`
        Spectral type label mapped to its `(wavelength_angstrom,
        normalized_flux)` arrays.
    """
    if not _reference_cache:
        for spectral_type in REFERENCE_SPECTRAL_TYPES:
            _reference_cache[spectral_type] = _load_reference_template(spectral_type)
    return _reference_cache


def _continuum_normalize(flux: np.ndarray) -> np.ndarray:
    """Rescale a flux array onto a common brightness scale for comparison.

    Observed intensities are in arbitrary sensor counts, while the
    reference library is flux-calibrated -- dividing both by their own
    median puts them on the same footing before comparing shapes.

    Returns
    -------
    normalized_flux : `np.ndarray`
        The input scaled so its median is 1.0.
    """
    median = np.median(flux)
    if median <= 0:
        return flux
    return flux / median


def _rank_by_probability(correlation_by_type: dict[str, float]) -> list[dict[str, object]]:
    """Turn correlation scores into a sorted, probability-like ranking.

    This is a heuristic ranking, not a calibrated statistical probability:
    it applies a softmax to the correlation coefficients so the reported
    weights are non-negative and sum to 1, which makes close calls between
    types visible without claiming more rigor than a shape-matching score
    supports.

    Returns
    -------
    ranked_types : `list` [`dict`]
        Every compared type, most probable first. Each entry has
        ``"spectral_type"``, ``"probability"`` (sums to 1 across the
        list), and ``"correlation"`` (the underlying Pearson coefficient).
    """
    if not correlation_by_type:
        return []

    types = list(correlation_by_type.keys())
    correlations = np.array([correlation_by_type[t] for t in types])
    scaled = correlations / _RANKING_SOFTMAX_TEMPERATURE
    scaled -= scaled.max()
    weights = np.exp(scaled)
    probabilities = weights / weights.sum()

    ranked = [
        {"spectral_type": t, "probability": float(p), "correlation": float(c)}
        for t, p, c in zip(types, probabilities, correlations, strict=True)
    ]
    ranked.sort(key=lambda entry: entry["probability"], reverse=True)
    return ranked


def classify_spectral_type(wavelength_angstrom: np.ndarray, intensity: np.ndarray) -> dict[str, object]:
    """Guess a star's broad spectral type by matching it to a reference.

    Resamples the observed spectrum onto each bundled reference star's
    wavelength grid, continuum-normalizes both, and scores the match with
    a Pearson correlation coefficient. The reference type with the
    highest correlation wins.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelength grid, in Angstroms.
    intensity : `np.ndarray`
        The observed spectrum's brightness at each wavelength, in
        whatever units extraction produced -- arbitrary sensor counts
        are fine, see `_continuum_normalize`.

    Returns
    -------
    result : `dict`
        ``"spectral_type"``: the best-matching reference label, or
        ``"Unknown"`` if there wasn't enough usable data to compare.
        ``"confidence"``: the winning correlation coefficient (-1 to 1;
        higher is a better match), or `None` when unknown.
        ``"correlation_by_type"``: every reference type's correlation
        coefficient, for inspecting close calls yourself.
        ``"ranked_types"``: every compared type as a probability-ranked
        list (see `_rank_by_probability`), most probable first.
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    valid = np.isfinite(wavelength_angstrom) & np.isfinite(intensity)
    wavelength_angstrom = wavelength_angstrom[valid]
    intensity = intensity[valid]

    if len(wavelength_angstrom) < _MINIMUM_OVERLAP_POINTS:
        return {"spectral_type": "Unknown", "confidence": None, "correlation_by_type": {}, "ranked_types": []}

    order = np.argsort(wavelength_angstrom)
    wavelength_angstrom = wavelength_angstrom[order]
    intensity = intensity[order]

    correlation_by_type: dict[str, float] = {}
    for spectral_type, (template_wavelength, template_flux) in _get_reference_templates().items():
        overlap_min = max(wavelength_angstrom.min(), template_wavelength.min())
        overlap_max = min(wavelength_angstrom.max(), template_wavelength.max())
        if overlap_max - overlap_min < _MINIMUM_OVERLAP_ANGSTROM:
            continue

        common_grid = template_wavelength[
            (template_wavelength >= overlap_min) & (template_wavelength <= overlap_max)
        ]
        if len(common_grid) < _MINIMUM_OVERLAP_POINTS:
            continue

        observed_on_grid = np.interp(common_grid, wavelength_angstrom, intensity)
        template_on_grid = np.interp(common_grid, template_wavelength, template_flux)

        observed_norm = _continuum_normalize(observed_on_grid)
        template_norm = _continuum_normalize(template_on_grid)

        if np.std(observed_norm) == 0 or np.std(template_norm) == 0:
            continue

        correlation_by_type[spectral_type] = float(np.corrcoef(observed_norm, template_norm)[0, 1])

    if not correlation_by_type:
        return {"spectral_type": "Unknown", "confidence": None, "correlation_by_type": {}, "ranked_types": []}

    best_type = max(correlation_by_type, key=correlation_by_type.get)
    return {
        "spectral_type": best_type,
        "confidence": correlation_by_type[best_type],
        "correlation_by_type": correlation_by_type,
        "ranked_types": _rank_by_probability(correlation_by_type),
    }


def is_classification_low_confidence(
    confidence: float | None, threshold: float = LOW_CONFIDENCE_THRESHOLD
) -> bool:
    """Decide if a classification's winning correlation is too weak to trust.

    Returns
    -------
    is_low_confidence : `bool`
        `True` if there was a classification and its confidence fell
        below `threshold`. `False` for an unclassified star (`None`) --
        that is a separate "nothing to compare" case, not a shaky match.
    """
    return confidence is not None and confidence < threshold


def is_classification_ambiguous(
    ranked_types: list[dict[str, object]], margin_threshold: float = AMBIGUOUS_PROBABILITY_MARGIN
) -> bool:
    """Decide if the top two candidate types are too close to call.

    Returns
    -------
    is_ambiguous : `bool`
        `True` if there are at least two ranked candidates and the top
        two probabilities are closer than `margin_threshold`.
    """
    if len(ranked_types) < 2:
        return False
    top, runner_up = ranked_types[0], ranked_types[1]
    return (top["probability"] - runner_up["probability"]) < margin_threshold


def build_spectral_classification_concerns(
    stellar_objects: Iterable[StellarObject],
) -> list[dict[str, object]]:
    """Flag classified stars whose spectral type shouldn't be trusted as-is.

    Skips stars that were never classified in the first place (an empty
    or ``"Unknown"`` `self_determined_spectral_type`) -- there's nothing
    to doubt about a type that was never guessed.

    Returns
    -------
    concerns : `list` [`dict`]
        One entry per flagged star, with ``"star_id"``, ``"reason"``
        (``"low_confidence"``, ``"ambiguous"``, or both joined by a
        comma), ``"spectral_type"``, and ``"confidence"``.
    """
    concerns: list[dict[str, object]] = []
    for star in stellar_objects:
        if star.self_determined_spectral_type in ("", "Unknown"):
            continue

        reasons = []
        if is_classification_low_confidence(star.self_determined_spectral_type_confidence):
            reasons.append("low_confidence")
        if is_classification_ambiguous(star.self_determined_spectral_type_candidates):
            reasons.append("ambiguous")

        if reasons:
            concerns.append({
                "star_id": star.id,
                "reason": ",".join(reasons),
                "spectral_type": star.self_determined_spectral_type,
                "confidence": star.self_determined_spectral_type_confidence,
            })
    return concerns
