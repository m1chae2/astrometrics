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
each by how far the observation is from the reference once the reference
has been scaled to the same overall brightness (the best-fit scale). It is
a match score, not a probability that the star has that type.

The scale is a best fit, not a divide-by-the-median. An earlier version
divided both spectra by their own median and compared them. The median of
a spectrum that falls toward the red sits at one particular wavelength, and
that wavelength moves whenever samples are left out (a different upper
wavelength limit, or the atmospheric mask), so the score, and sometimes the
type, changed for reasons that had nothing to do with the star. On the
Vega-field A stars, leaving out the atmospheric bands moved the median from
6130 A to 5940 A and flipped the best type from A0V to B9V. A best-fit scale
does not depend on any one wavelength.
"""

import csv
import re
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from scipy.ndimage import gaussian_filter1d

from astrometricslib.pipelines.spectroscopy.atmospheric_mask import atmospheric_band_mask
from astrometricslib.pipelines.spectroscopy.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    blur_sigma_in_samples,
)

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

# Giant, bright-giant and supergiant references from the same Pickles library
# (luminosity classes III, II and I; 56 spectra, downloaded 2026-09-24). They
# are NOT ranked against the main-sequence ladder when a spectrum is
# classified: on 2026-09-24 the best giant beat the best dwarf by at most
# 0.015 in relative RMS for every star with a known catalog class (dwarfs
# Vega +0.002, Alcor -0.001, HD 172149 -0.008; giants HD 183753 +0.006,
# Arcturus +0.014, Albireo A +0.015), so a slitless spectrum at this
# resolution cannot tell the classes apart, and ranking them together
# relabelled dwarfs as "A0III" and "G8I". They are used to look up the
# expected feature depths of a catalog giant, and to name the closest giant
# reference in the note on a catalog giant (see `luminosity_class_note`).
# `REFERENCE_SPECTRAL_TYPES` stays the ladder that the classification, the
# instrument response and the catalog-type lookup for dwarfs use. The library's
# labels are kept as they are: "B12III" is B1-2 III, "K01II" is K0-1 II and
# "K34II" is K3-4 II. M9III and M10III are left out: the source has small
# negative fluxes near 4700-4800 A for both, and no star here is that late.
GIANT_REFERENCE_SPECTRAL_TYPES: tuple[str, ...] = (
    "A0I",
    "A2I",
    "B0I",
    "B1I",
    "B3I",
    "B5I",
    "B8I",
    "F0I",
    "F5I",
    "F8I",
    "G0I",
    "G2I",
    "G5I",
    "G8I",
    "K2I",
    "K3I",
    "K4I",
    "M2I",
    "B2II",
    "B5II",
    "F0II",
    "F2II",
    "G5II",
    "K01II",
    "K34II",
    "M3II",
    "A0III",
    "A3III",
    "A5III",
    "A7III",
    "B12III",
    "B3III",
    "B5III",
    "B9III",
    "F0III",
    "F2III",
    "F5III",
    "G0III",
    "G5III",
    "G8III",
    "K0III",
    "K1III",
    "K2III",
    "K3III",
    "K4III",
    "K5III",
    "M0III",
    "M1III",
    "M2III",
    "M3III",
    "M4III",
    "M5III",
    "M6III",
    "M7III",
    "M8III",
    "O8III",
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

# Softmax temperature (in root-mean-square difference units) used to turn
# match scores into a weight for each type. It has to be about as big as
# the gap between neighboring types, so a near neighbor keeps a visible
# share instead of one type taking everything.
#
# 0.005: when the score changed from median-normalized to best-fit scale,
# the median gap between the best and second-best type fell from 0.0110 to
# 0.0060, and the median gap between neighbors among the top six types fell
# from 0.0284 to 0.0135 (14 stars with a known type or a standard, the
# instrument response applied, atmospheric bands excluded, 2026-09-19; see
# logs/spectral_score_calibration_20260919.json). The old temperature was
# 0.01, so it is halved to keep the same spread of weights.
_RANKING_SOFTMAX_TEMPERATURE = 0.005

# A best match with a root-mean-square difference above this is poor.
# Provisional value. It rests on only a handful of stars and should be
# revisited as more standards are observed.
#
# 0.15 sits between the two groups in the best-fit score (14 stars with a
# known type or a standard, 2026-09-19; see
# logs/spectral_score_calibration_20260919.json). Stars matched to within
# three spectral-type steps of their catalog type: Alcor's second star
# 0.049, g UMa 0.046, Vega 0.053, HD 151023 0.105. Stars matched to the
# wrong type: BD+36 2764 0.283, HD 151086 0.218, HD 150293 0.205, BD+36 2775
# 0.202, HD 150679 0.181. The same value fell between the two groups under
# the old median-normalized score (0.064-0.113 against 0.196-0.375), so it
# did not need to change.
#
# Two known-wrong matches score well below it and are NOT caught: HD 150998
# (K2 matched as M0, 0.086) and the first star of the Alcor pair (A2 matched
# as F2, 0.048, possibly saturated). A low score means the reference is close
# to the spectrum, not that the type is right.
POOR_MATCH_RMS_THRESHOLD = 0.15

# A best match with a root-mean-square difference above this is not a match at
# all: the spectrum is off by nearly half its own brightness from every
# reference, so naming the least-bad one would only invent a type.
# Provisional, and a judgement call from one data set. Of the 55 stars
# classified in the catalog on 2026-09-24, 19 scored above 0.45 (up to 1.66).
# 18 of those have no catalog type and are stars matched to the wrong part of
# the sky (see `REGISTRATION_REFERENCE_FIELD_RADIUS_DEG`), so their "type" was
# a fit to a faint, noisy spectrum. The one exception, beta Lyr B (catalog
# B7V, matched B8V at 0.58 with a signal-to-noise of 5.9), is lost by this
# cut. The highest scores among stars with a catalog type are V* HM Lyr (M6,
# matched M6V at 0.39), HD 183987 (0.32) and HD 183931 (0.28), all matched
# correctly and all kept: cool stars score high because their blue end is
# faint and noisy, so the cut is set above them.
UNRELIABLE_MATCH_RMS_THRESHOLD = 0.45

# The score reported as "confidence" is 1 minus the root-mean-square
# difference, so the poor-match threshold above becomes this score.
LOW_CONFIDENCE_THRESHOLD = 1.0 - POOR_MATCH_RMS_THRESHOLD

# When the top two ranked types' weights are closer than this, the
# classifier can't meaningfully tell them apart, and reporting only the
# winner would hide a near-tie.
AMBIGUOUS_PROBABILITY_MARGIN = 0.15

_reference_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
# Blurred references, kept for each resolution seen so far. The key is the
# resolution rounded to a whole Angstrom: a spectrum's resolution is
# measured from its own trail width, so it differs a little from one
# spectrum to the next, and blurring 34 references again for every one
# would be wasted work. A 0.5 A difference in blur is far below anything
# the comparison can see.
_blurred_cache: dict[int, dict[str, tuple[np.ndarray, np.ndarray]]] = {}


_SPECTRAL_LETTER_ORDER = "OBAFGKM"


# The luminosity class in a spectral type such as "K3II", "G8III/IV" or
# "B9.5V": a supergiant (I, Ia, Iab, Ib), bright giant (II), giant (III),
# subgiant (IV) or dwarf (V). The first one named is used.
_LUMINOSITY_CLASS = re.compile(r"[OBAFGKM]\d+(?:\.\d)?(?:-\d)?\s*(Ia\+?|Iab|Ib|III|II|IV|V|I)(?![IVab])")


def _luminosity_class(spectral_type_text: str | None) -> str | None:
    """Read the luminosity class of a spectral type.

    Parameters
    ----------
    spectral_type_text : `str`, optional
        A spectral type such as "K3II" or "A0V".

    Returns
    -------
    luminosity_class : `str` or `None`
        "I" (all supergiants), "II", "III", "IV" or "V", or `None` when the
        text names none.
    """
    match = _LUMINOSITY_CLASS.search(str(spectral_type_text or ""))
    if match is None:
        return None
    return (
        "I"
        if match.group(1).startswith("I") and match.group(1) not in ("II", "III", "IV")
        else match.group(1)
    )


def is_catalog_giant(catalog_spectral_type: str | None) -> bool:
    """Tell whether the catalog calls a star a giant or supergiant.

    Parameters
    ----------
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type, such as "K3II".

    Returns
    -------
    is_giant : `bool`
        `True` for luminosity class I, II or III (not IV or V).
    """
    return _luminosity_class(catalog_spectral_type) in ("I", "II", "III")


def luminosity_class_note(
    catalog_spectral_type: str | None,
    classified_type: str | None,
    closest_giant: tuple[str, float] | None = None,
) -> str:
    """Explain the type of a star the catalog says is not a dwarf.

    The classification compares with main-sequence references only, because
    a slitless spectrum at this resolution cannot tell luminosity classes
    apart (see `GIANT_REFERENCE_SPECTRAL_TYPES`). So a catalog giant's type
    is the dwarf that looks most alike, and this note says so, and says which
    giant reference is closest.

    Parameters
    ----------
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type, such as "K3II".
    classified_type : `str`, optional
        The type matched from the spectrum, such as "K7V".
    closest_giant : `tuple` [`str`, `float`], optional
        The closest giant reference and its score (relative RMS), when it
        has been worked out.

    Returns
    -------
    note : `str`
        The note, or an empty string when the catalog gives no giant class
        (I, II or III) or no type was matched.
    """
    catalog_class = _luminosity_class(catalog_spectral_type)
    if not is_catalog_giant(catalog_spectral_type) or not classified_type or classified_type == "Unknown":
        return ""
    note = (
        f"the catalog gives this star luminosity class {catalog_class} ({catalog_spectral_type}), "
        f"but the type above is the closest main-sequence reference ({classified_type})"
    )
    if closest_giant is not None:
        giant_type, giant_rms = closest_giant
        note += f"; the closest giant or supergiant reference is {giant_type} (score {giant_rms:.2f})"
    return note + ". A slitless spectrum cannot reliably tell giants from dwarfs"


# A classified type more than this many subclass steps from the catalog type
# is flagged. A step is a tenth of a letter class, so 20 is two whole classes
# (for example B to F). Of the 29 stars with a catalog type classified on
# 2026-09-24, 27 were within 12 steps (F8 matched K0 was the worst), and the
# only two beyond that were both wrong for reasons other than the templates:
# Elnath (catalog B7III, matched M2V, 45 steps, with a blue end that looks like
# the zero order's glare) and the star named beta1 Cyg B (catalog B9.5V,
# matched K4V, 34.5 steps, which is really the K3II primary). A judgement call
# from that one data set.
CATALOG_DISAGREEMENT_SUBCLASS_STEPS = 20.0


def _subclass_position(spectral_type_text: str | None) -> float | None:
    """Place a spectral type on a single scale, ten steps per letter class.

    Parameters
    ----------
    spectral_type_text : `str`, optional
        A spectral type such as "K3V", "B9.5V" or "A5V+M3-4V". Only the
        first letter and subclass are read.

    Returns
    -------
    position : `float` or `None`
        Steps from the start of O (so B0 is 10 and M0 is 60), taking a
        missing subclass as 5, or `None` if the text has no O to M letter.
    """
    match = re.match(r"\s*([OBAFGKM])\s*(\d(?:\.\d)?)?", str(spectral_type_text or ""))
    if match is None:
        return None
    subclass = float(match.group(2)) if match.group(2) else 5.0
    return _SPECTRAL_LETTER_ORDER.index(match.group(1)) * 10.0 + subclass


def catalog_disagreement_note(catalog_spectral_type: str | None, classified_type: str | None) -> str:
    """Warn when the spectrum's type is far from the catalog's.

    A mismatch this large usually means the spectrum is not the catalog
    star's: a bright neighbour's light, glare from a very bright star's zero
    order, or the star's name having been given to the wrong object.

    Parameters
    ----------
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type.
    classified_type : `str`, optional
        The type matched from the spectrum.

    Returns
    -------
    note : `str`
        The warning, or an empty string when either type is missing or the
        two are within `CATALOG_DISAGREEMENT_SUBCLASS_STEPS`.
    """
    catalog_position = _subclass_position(catalog_spectral_type)
    classified_position = _subclass_position(classified_type)
    if catalog_position is None or classified_position is None:
        return ""
    if abs(classified_position - catalog_position) <= CATALOG_DISAGREEMENT_SUBCLASS_STEPS:
        return ""
    return (
        f"the spectrum matches {classified_type} but the catalog gives {catalog_spectral_type}, "
        "more than two spectral classes apart: the spectrum may not be this star's "
        "(a bright neighbour, glare from a very bright star, or a wrong name)"
    )


def _template_position(reference_type: str) -> float:
    """Place a reference label on the ladder, ten steps per letter class.

    Parameters
    ----------
    reference_type : `str`
        A label such as "K3V", "B12III" (B1-2) or "K34II" (K3-4).

    Returns
    -------
    position : `float`
        Steps from the start of O. A two-digit subclass is the average of
        its two digits.
    """
    match = re.match(r"([OBAFGKM])(\d+)", reference_type)
    digits = match.group(2)
    subclass = sum(int(digit) for digit in digits) / len(digits) if len(digits) == 2 else float(digits)
    return _SPECTRAL_LETTER_ORDER.index(match.group(1)) * 10 + subclass


def nearest_reference_type(spectral_type_text: str | None) -> str | None:
    """Find the bundled reference type closest to a catalog spectral type.

    Catalog types come in many forms ("A0Va", "K0", "B7III",
    "A5V+M3-4V"). The letter and first number are read from the start
    (so for a double star the first, brighter component is used), and the
    closest bundled reference on the O to M ladder is returned. When the
    catalog names a giant or supergiant class (I, II, III) the closest
    reference of that class is used; anything else uses the main-sequence
    ladder.

    Parameters
    ----------
    spectral_type_text : `str` or `None`
        A catalog spectral type.

    Returns
    -------
    reference_type : `str` or `None`
        A label from `REFERENCE_SPECTRAL_TYPES` (or, for a giant class,
        `GIANT_REFERENCE_SPECTRAL_TYPES`), or `None` when the text has no
        recognizable letter and number.
    """
    if not spectral_type_text:
        return None
    match = re.match(r"^\s*([OBAFGKM])\s*(\d(?:\.\d)?)", spectral_type_text.strip().upper())
    if match is None:
        return None
    position = _SPECTRAL_LETTER_ORDER.index(match.group(1)) * 10 + float(match.group(2))

    catalog_class = _luminosity_class(spectral_type_text.strip())
    candidates = REFERENCE_SPECTRAL_TYPES
    if catalog_class in ("I", "II", "III"):
        same_class = [
            reference
            for reference in GIANT_REFERENCE_SPECTRAL_TYPES
            if _luminosity_class(reference) == catalog_class
        ]
        candidates = tuple(same_class) or REFERENCE_SPECTRAL_TYPES
    return min(candidates, key=lambda reference: abs(_template_position(reference) - position))


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
        normalized_flux)` arrays, for the main-sequence ladder and the
        giant references.
    """
    if not _reference_cache:
        for spectral_type in (*REFERENCE_SPECTRAL_TYPES, *GIANT_REFERENCE_SPECTRAL_TYPES):
            _reference_cache[spectral_type] = _load_reference_template(spectral_type)
    return _reference_cache


def _get_blurred_templates(resolution_element_angstrom: float) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Give every reference spectrum blurred to a given resolution.

    The references are much sharper than a grism spectrum, so they are
    blurred to the instrument's resolution before being compared.
    Comparing sharp references with a blurred observation would add a
    mismatch that has nothing to do with the star's type.

    Parameters
    ----------
    resolution_element_angstrom : `float`
        The resolution element to blur to, in Angstroms.

    Returns
    -------
    templates : `dict`
        Spectral type label mapped to `(wavelength_angstrom, flux)`, the
        flux blurred with a gaussian as wide as one resolution element.
    """
    cache_key = round(resolution_element_angstrom)
    if cache_key not in _blurred_cache:
        _blurred_cache[cache_key] = {
            spectral_type: (
                wavelength,
                gaussian_filter1d(
                    flux, blur_sigma_in_samples(float(cache_key), float(np.median(np.diff(wavelength))))
                ),
            )
            for spectral_type, (wavelength, flux) in _get_reference_templates().items()
        }
    return _blurred_cache[cache_key]


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


def classify_spectral_type(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    exclude_atmospheric_bands: bool = True,
    reference_types: Iterable[str] | None = None,
) -> dict[str, object]:
    """Find the bundled reference spectrum a star's spectrum most resembles.

    The observed spectrum must already have had the instrument's response
    removed (see `instrument_response`); this function does not do that,
    and a spectrum that still carries the instrument's tilt gives a
    meaningless answer.

    Each reference, blurred to the instrument's resolution, is compared
    with the observation over `CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM`.
    The reference is first scaled to the observation's overall brightness
    with the best-fit (least-squares) scale, so only shape matters. The
    score is the root-mean-square difference between the observation and
    the scaled reference, as a fraction of the observation's average
    brightness: 0.05 means a typical sample is off by 5% of the average.

    Parameters
    ----------
    wavelength_angstrom : `np.ndarray`
        The observed spectrum's wavelength grid, in Angstroms.
    intensity : `np.ndarray`
        The observed spectrum's brightness at each wavelength, already
        corrected for the instrument. Arbitrary units are fine; only the
        shape is used.
    resolution_element_angstrom : `float`, optional
        How much the instrument blurs this spectrum, in Angstroms; the
        references are blurred to this width. Defaults to
        `FALLBACK_RESOLUTION_ELEMENT_ANGSTROM`; pass the spectrum's own
        measured value when there is one (see `spectral_resolution`).
    exclude_atmospheric_bands : `bool`, optional
        Leave the wavelengths where Earth's air absorbs light out of the
        comparison (see `atmospheric_mask`), so a dip that comes from the
        air is not counted as a difference between the star and the
        reference. Defaults to `True`.
    reference_types : `Iterable` [`str`], optional
        The references to compare with. Defaults to the main-sequence
        ladder, `REFERENCE_SPECTRAL_TYPES`; pass
        `GIANT_REFERENCE_SPECTRAL_TYPES` to find the closest giant.

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
    allowed_types = set(REFERENCE_SPECTRAL_TYPES if reference_types is None else reference_types)
    for spectral_type, (template_wavelength, template_flux) in _get_blurred_templates(
        resolution_element_angstrom
    ).items():
        if spectral_type not in allowed_types:
            continue
        overlap_min = max(wavelength_angstrom.min(), template_wavelength.min(), low)
        overlap_max = min(wavelength_angstrom.max(), template_wavelength.max(), high)
        if overlap_max - overlap_min < MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:
            continue
        common_grid = template_wavelength[
            (template_wavelength >= overlap_min) & (template_wavelength <= overlap_max)
        ]
        if exclude_atmospheric_bands:
            common_grid = common_grid[~atmospheric_band_mask(common_grid)]
        if len(common_grid) < _MINIMUM_OVERLAP_POINTS:
            continue

        observed_on_grid = np.interp(common_grid, wavelength_angstrom, intensity)
        template_on_grid = np.interp(common_grid, template_wavelength, template_flux)
        if np.std(observed_on_grid) == 0 or np.std(template_on_grid) == 0:
            continue
        observed_average = float(np.mean(observed_on_grid))
        template_power = float(np.dot(template_on_grid, template_on_grid))
        if observed_average <= 0 or template_power <= 0:
            continue

        # The scale that brings the reference as close as possible to the
        # observation (least squares). Unlike dividing each spectrum by its
        # median, it does not depend on any one wavelength.
        best_fit_scale = float(np.dot(observed_on_grid, template_on_grid)) / template_power
        difference = observed_on_grid - best_fit_scale * template_on_grid
        rms_by_type[spectral_type] = float(np.sqrt(np.mean(difference**2))) / observed_average
        correlation_by_type[spectral_type] = float(np.corrcoef(observed_on_grid, template_on_grid)[0, 1])

    if not rms_by_type:
        return unclassified_result("no reference overlaps the spectrum enough to compare")

    best_type = min(rms_by_type, key=rms_by_type.get)
    best_rms = rms_by_type[best_type]
    if best_rms > UNRELIABLE_MATCH_RMS_THRESHOLD:
        return unclassified_result(
            f"no reference matches this spectrum: even the closest ({best_type}) is off by "
            f"{best_rms:.0%} of its brightness, and more than {UNRELIABLE_MATCH_RMS_THRESHOLD:.0%} "
            "is too far to call a match"
        )
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
