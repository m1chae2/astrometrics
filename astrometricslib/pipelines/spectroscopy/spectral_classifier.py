"""Finds the bundled reference spectrum a star's spectrum most resembles.

A slitless grism can't resolve spectral lines finely enough to derive a
star's physical properties from first principles, and it can't tell you
a catalog name either. What it can do is compare the overall shape of an
observed spectrum -- continuum slope, Balmer line strength -- against a
library of reference stars and report which one it most resembles.

That comparison only means something after the instrument's own tilt has
been removed (see `instrument_response`). Without it, every reference
correlates 0.95 or better with every star and the "best" match is an
accident of that tilt: a spectrum of Vega (A0V) matched F6V. So this
module expects a spectrum that has already been corrected, compares it
with the references blurred to the instrument's resolution, and scores
each by the root-mean-square difference between the two normalized
curves. It is a match score, not a probability that the star has that
type.
"""

import csv
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from scipy.ndimage import gaussian_filter1d

if TYPE_CHECKING:
    from astrometricslib.models.stellar_source import StellarObject

_TEMPLATE_DIR = Path(__file__).parent / "data"

# The reference spectral types this classifier ships with, drawn from the
# Pickles (1998) stellar flux library (Pickles, A.J. 1998, PASP, 110, 863).
# Each covers 3000-10000 A at 5 A sampling, normalized to 1.0 at 5556 A --
# enough range to capture the Balmer lines and the overall continuum slope
# a low-resolution slitless grism can actually resolve. This is the full
# non-metallicity-variant main-sequence ladder the library offers (every
# single- or double-subtype rung from O5V to M6V), not just a coarse
# sample -- a denser ladder means a star between two rungs has a closer
# template to land on, instead of being forced into a near-tie between
# two rungs five subtypes apart.
REFERENCE_SPECTRAL_TYPES: tuple[str, ...] = (
    "O5V",
    "O9V",
    "B0V",
    "B1V",
    "B3V",
    "B8V",
    "B9V",
    "A0V",
    "A2V",
    "A3V",
    "A5V",
    "A7V",
    "F0V",
    "F2V",
    "F5V",
    "F6V",
    "F8V",
    "G0V",
    "G2V",
    "G5V",
    "G8V",
    "K0V",
    "K2V",
    "K3V",
    "K4V",
    "K5V",
    "K7V",
    "M0V",
    "M1V",
    "M2V",
    "M3V",
    "M4V",
    "M5V",
    "M6V",
)

# Below this many overlapping points, a comparison is too noisy to trust.
_MINIMUM_OVERLAP_POINTS = 20

# The widest range a comparison can use: where the reference spectra exist,
# which is also the camera's wavelength range (3000-10000 A for the ASI533MM
# Pro). The range actually compared is narrower: the instrument response
# marks the samples it does not cover as unusable (4200-8000 A by default,
# see `instrument_response.DEFAULT_RESPONSE_WAVELENGTH_RANGE_ANGSTROM`), and
# only finite samples are compared.
CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM = (3000.0, 10000.0)

# The observed spectrum must cover at least this much (in Angstroms) to be
# classified at all. Telling hot stars from cool ones depends on the slope
# across the spectrum, so a short piece (for example the trail of a star
# near the image edge, which only reaches 6800 A) cannot separate them.
# Two thirds of the 3800 A default comparison range (4200-8000 A) is
# required.
MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM = 2500.0

# The instrument's resolution element, in Angstroms. The references are
# much sharper than a grism spectrum, so they are blurred to this width
# before being compared; comparing sharp references with a blurred
# observation would add a mismatch that has nothing to do with the type.
_RESOLUTION_ELEMENT_ANGSTROM = 30.0

# Softmax temperature (in root-mean-square difference units) used to turn
# match scores into a weight for each type. Neighboring types differ by
# about 0.01 in this measure, so a temperature of 0.01 lets a near
# neighbor keep a visible share instead of one type taking everything.
_RANKING_SOFTMAX_TEMPERATURE = 0.01

# A best match with a root-mean-square difference above this is poor.
# Provisional value from the standard-star check in
# validate_spectral_and_period_analysis.py: stars that matched their
# known type had a difference of 0.07-0.11 (Alcor's second star,
# HD 151023, Vega itself), while stars that matched the wrong type had
# 0.19-0.36. 0.15 sits between them. It rests on only a handful of
# stars and should be revisited as more standards are observed.
POOR_MATCH_RMS_THRESHOLD = 0.15

# The score reported as "confidence" is 1 minus the root-mean-square
# difference, so the poor-match threshold above becomes this score.
LOW_CONFIDENCE_THRESHOLD = 1.0 - POOR_MATCH_RMS_THRESHOLD

# When the top two ranked types' weights are closer than this, the
# classifier can't meaningfully tell them apart, and reporting only the
# winner would hide a near-tie.
AMBIGUOUS_PROBABILITY_MARGIN = 0.15

_reference_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
_blurred_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}


_SPECTRAL_LETTER_ORDER = "OBAFGKM"


def nearest_reference_type(spectral_type_text: str | None) -> str | None:
    """Find the bundled reference type closest to a catalog spectral type.

    Catalog types come in many forms ("A0Va", "K0", "B7III",
    "A5V+M3-4V"). The letter and first number are read from the start
    (so for a double star the first, brighter component is used), and the
    closest bundled reference on the O to M ladder is returned.

    Parameters
    ----------
    spectral_type_text : `str` or `None`
        A catalog spectral type.

    Returns
    -------
    reference_type : `str` or `None`
        A label from `REFERENCE_SPECTRAL_TYPES`, or `None` when the text
        has no recognizable letter and number.
    """
    if not spectral_type_text:
        return None
    match = re.match(r"^\s*([OBAFGKM])\s*(\d(?:\.\d)?)", spectral_type_text.strip().upper())
    if match is None:
        return None
    position = _SPECTRAL_LETTER_ORDER.index(match.group(1)) * 10 + float(match.group(2))

    def ladder_position(reference_type: str) -> float:
        return _SPECTRAL_LETTER_ORDER.index(reference_type[0]) * 10 + float(reference_type[1])

    return min(REFERENCE_SPECTRAL_TYPES, key=lambda reference: abs(ladder_position(reference) - position))


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


def _get_blurred_templates() -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Give every reference spectrum blurred to the instrument's resolution.

    Returns
    -------
    templates : `dict`
        Spectral type label mapped to `(wavelength_angstrom, flux)`, the
        flux blurred with a gaussian as wide as one resolution element.
    """
    if not _blurred_cache:
        for spectral_type, (wavelength, flux) in _get_reference_templates().items():
            sigma_samples = _RESOLUTION_ELEMENT_ANGSTROM / 2.355 / float(np.median(np.diff(wavelength)))
            _blurred_cache[spectral_type] = (wavelength, gaussian_filter1d(flux, sigma_samples))
    return _blurred_cache


def _rank_by_probability(
    rms_by_type: dict[str, float], correlation_by_type: dict[str, float]
) -> list[dict[str, object]]:
    """Turn match scores into a sorted ranking with a weight for each type.

    This is a heuristic ranking, not a calibrated statistical probability:
    a softmax over the (negative) differences makes the weights
    non-negative and sum to 1, which makes close calls between types
    visible without claiming more rigor than shape matching supports.

    Returns
    -------
    ranked_types : `list` [`dict`]
        Every compared type, best first. Each entry has
        ``"spectral_type"``, ``"probability"`` (a weight; sums to 1
        across the list), ``"rms"`` (the root-mean-square difference; lower
        is better) and ``"correlation"`` (the Pearson coefficient, kept for
        comparison; it barely separates types).
    """
    if not rms_by_type:
        return []
    types = list(rms_by_type.keys())
    differences = np.array([rms_by_type[t] for t in types])
    scaled = -(differences - differences.min()) / _RANKING_SOFTMAX_TEMPERATURE
    weights = np.exp(scaled)
    probabilities = weights / weights.sum()
    ranked = [
        {
            "spectral_type": t,
            "probability": float(p),
            "rms": float(rms_by_type[t]),
            "correlation": float(correlation_by_type[t]),
        }
        for t, p in zip(types, probabilities, strict=True)
    ]
    ranked.sort(key=lambda entry: entry["rms"])
    return ranked


def unclassified_result(reason: str) -> dict[str, object]:
    """Build the result for a spectrum that could not be classified.

    Returns
    -------
    result : `dict`
        An ``"Unknown"`` result carrying the `reason` and empty rankings.
    """
    return {
        "spectral_type": "Unknown",
        "confidence": None,
        "rms": None,
        "match_quality": None,
        "reason": reason,
        "correlation_by_type": {},
        "ranked_types": [],
    }


def classify_spectral_type(wavelength_angstrom: np.ndarray, intensity: np.ndarray) -> dict[str, object]:
    """Find the bundled reference spectrum a star's spectrum most resembles.

    The observed spectrum must already have had the instrument's response
    removed (see `instrument_response`); this function does not do that,
    and a spectrum that still carries the instrument's tilt gives a
    meaningless answer.

    Each reference, blurred to the instrument's resolution, is compared
    with the observation over `CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM`.
    Both are divided by their own median first, so only shape matters, and
    the score is the root-mean-square difference between them.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelength grid, in Angstroms.
    intensity : `np.ndarray`
        The observed spectrum's brightness at each wavelength, already
        corrected for the instrument. Arbitrary units are fine; only the
        shape is used.

    Returns
    -------
    result : `dict`
        ``"spectral_type"``: the best-matching reference label, or
        ``"Unknown"`` when there was not enough of the spectrum to compare
        (then ``"reason"`` says why).
        ``"confidence"``: 1 minus the best root-mean-square difference,
        or `None` when unknown. A match score, not a probability.
        ``"rms"``: the best root-mean-square difference itself.
        ``"match_quality"``: ``"good"`` or ``"poor"`` (see
        `POOR_MATCH_RMS_THRESHOLD`).
        ``"correlation_by_type"``: every reference's Pearson correlation.
        ``"ranked_types"``: every compared type, best first (see
        `_rank_by_probability`).
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    valid = np.isfinite(wavelength_angstrom) & np.isfinite(intensity) & (intensity > 0)
    wavelength_angstrom = wavelength_angstrom[valid]
    intensity = intensity[valid]

    low, high = CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM
    in_range = (wavelength_angstrom >= low) & (wavelength_angstrom <= high)
    if in_range.sum() < _MINIMUM_OVERLAP_POINTS:
        return unclassified_result("too few samples inside the comparison range")
    order = np.argsort(wavelength_angstrom)
    wavelength_angstrom = wavelength_angstrom[order]
    intensity = intensity[order]

    coverage = min(wavelength_angstrom.max(), high) - max(wavelength_angstrom.min(), low)
    if coverage < MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:
        return unclassified_result(
            f"the spectrum covers only {max(coverage, 0.0):.0f} A, and at least "
            f"{MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:.0f} A is needed"
        )

    rms_by_type: dict[str, float] = {}
    correlation_by_type: dict[str, float] = {}
    for spectral_type, (template_wavelength, template_flux) in _get_blurred_templates().items():
        overlap_min = max(wavelength_angstrom.min(), template_wavelength.min(), low)
        overlap_max = min(wavelength_angstrom.max(), template_wavelength.max(), high)
        if overlap_max - overlap_min < MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:
            continue
        common_grid = template_wavelength[
            (template_wavelength >= overlap_min) & (template_wavelength <= overlap_max)
        ]
        if len(common_grid) < _MINIMUM_OVERLAP_POINTS:
            continue

        observed_norm = np.interp(common_grid, wavelength_angstrom, intensity)
        template_norm = np.interp(common_grid, template_wavelength, template_flux)
        observed_norm = observed_norm / np.median(observed_norm)
        template_norm = template_norm / np.median(template_norm)
        if np.std(observed_norm) == 0 or np.std(template_norm) == 0:
            continue

        rms_by_type[spectral_type] = float(np.sqrt(np.mean((observed_norm - template_norm) ** 2)))
        correlation_by_type[spectral_type] = float(np.corrcoef(observed_norm, template_norm)[0, 1])

    if not rms_by_type:
        return unclassified_result("no reference overlaps the spectrum enough to compare")

    best_type = min(rms_by_type, key=rms_by_type.get)
    best_rms = rms_by_type[best_type]
    return {
        "spectral_type": best_type,
        "confidence": max(0.0, 1.0 - best_rms),
        "rms": best_rms,
        "match_quality": "poor" if best_rms > POOR_MATCH_RMS_THRESHOLD else "good",
        "reason": None,
        "correlation_by_type": correlation_by_type,
        "ranked_types": _rank_by_probability(rms_by_type, correlation_by_type),
    }


def is_classification_low_confidence(
    confidence: float | None, threshold: float = LOW_CONFIDENCE_THRESHOLD
) -> bool:
    """Decide if a classification's match score is too weak to trust.

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
        spectroscopy = star.spectroscopy
        if spectroscopy is None or spectroscopy.self_determined_spectral_type in ("", "Unknown"):
            continue

        reasons = []
        if is_classification_low_confidence(spectroscopy.self_determined_spectral_type_confidence):
            reasons.append("low_confidence")
        if is_classification_ambiguous(spectroscopy.self_determined_spectral_type_candidates):
            reasons.append("ambiguous")

        if reasons:
            concerns.append({
                "star_id": star.id,
                "reason": ",".join(reasons),
                "spectral_type": spectroscopy.self_determined_spectral_type,
                "confidence": spectroscopy.self_determined_spectral_type_confidence,
            })
    return concerns
