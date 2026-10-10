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
a match score (a relative RMS, lower is closer), not a probability that the
star has that type.

The scale is a best fit, not a divide-by-the-median. An earlier version
divided both spectra by their own median and compared them. The median of
a spectrum that falls toward the red sits at one particular wavelength, and
that wavelength moves whenever samples are left out (a different upper
wavelength limit, or the atmospheric mask), so the score, and sometimes the
type, changed for reasons that had nothing to do with the star. On the
Vega-field A stars, leaving out the atmospheric bands moved the median from
6130 A to 5940 A and flipped the best type from A0V to B9V. A best-fit scale
does not depend on any one wavelength.

What the match leaves out
-------------------------
The score is a plain comparison of shapes, so anything the references do not
contain counts against the star, or is absorbed by a wrong type:

* Reddening. The references are not reddened, and dust between us and the
  star tilts the spectrum toward the red. A hot star can then match a cooler
  type, and this is why an O/B/A star's self-determined type commonly comes
  out about one subtype too cool against its catalog type, even for bright,
  high-signal spectra (Vega included). Three different fixes were tried and
  all failed the same way, which is the real reason reddening is NOT
  modelled rather than an oversight:
  1. (2026-09-26) Fitting a reddening term per reference (Cardelli law,
     R_V = 3.1, E(B-V) from 0 to 1.5). It moved gamma Cas (Navi) and Deneb
     to hot types, but with E(B-V) of 0.36 and 0.42 against about 0.1 from
     the catalog colour, and for Deneb the type was still wrong. The fit
     was trading dust against a missing template.
  2. (2026-09-27) A Gaussian-prior-regularized joint fit of type and a
     shared E(B-V) across all candidates, tried both per-candidate and
     with one E(B-V) shared across the whole ladder. Every prior weight
     tried (0 to 0.4) either let a wrong candidate warp itself toward its
     neighbor's shape (self-match regressions failed) or barely moved
     anything.
  3. (2026-09-27) Coarsening `REFERENCE_SPECTRAL_TYPES` so neighboring
     rungs are far enough apart, in root-mean-square terms, that a
     realistic reddening shift cannot bridge them. Measured directly: the
     median gap between adjacent rungs (about 0.043-0.087, depending on
     resolution) is the same size as the shift a typical E(B-V) of 0.065
     produces (about 0.060), and this barely changes between 25 and 70 A
     resolution, so no safety threshold both prunes enough to matter and
     preserves every named rung a test or a catalog lookup expects.
  All three attempts hit the same wall: at this instrument's resolution, in
  this reference library, interstellar reddening and the difference between
  two neighboring spectral types are the same size and shape of effect, so
  nothing this module can fit or threshold tells them apart. Treat a
  self-determined O/B/A type as reading about one subtype cool of the truth,
  rather than expecting it to be corrected.
* Airmass. The spectrum is not corrected for how much air the light crossed.
  On the 2026-09-25 spectra the airmass ran from 1.01 to 1.46 (Mirfak),
  which is too small a range, and the wrong direction, to explain the
  tilts in the stars checked.
* Luminosity class. The ladder is main-sequence only (see
  `GIANT_REFERENCE_SPECTRAL_TYPES`). Deneb (catalog A2Ia) matches A2I best
  at rms 0.036, against 0.063 for its stored A5V, so the class does matter
  for a supergiant; ranking all the classes together mislabelled ordinary
  stars as giants, and it is not done.
* Line depth. Observed Balmer lines are shallower than the references' (see
  the limits in `spectrum_extractor`), so a star with strong Balmer lines
  can look like a type with weaker ones.
* Emission. Emission lines are left out of the comparison when the feature
  test finds them at H-alpha or H-beta, but a star with a disc (a Be star
  such as gamma Cas) also has extra light in the continuum, which no
  reference has. Its stored type stayed A3V against a catalog B0IVe.

Read a type here as "the reference this spectrum most resembles at this
resolution", not as a classification of the star.
"""

import csv
import re
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
from scipy.ndimage import gaussian_filter1d

from astrometricslib.models.stellar_source import (
    NO_GOOD_MATCH_RMS,
    UNRELIABLE_MATCH_RMS,
    is_rms_gap_ambiguous,
    rms_gap_to_next_class,
    rms_gap_to_second_best,
)
from astrometricslib.pipelines.spectroscopy.pre_processing.atmospheric_mask import atmospheric_band_mask
from astrometricslib.pipelines.spectroscopy.pre_processing.spectral_resolution import (
    FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    ResolutionProfile,
    blur_sigma_in_samples,
    blur_to_resolution_profile,
)

_TEMPLATE_DIR = Path(__file__).parent.parent / "data"

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
# reference in the note on a catalog giant (see
# `post_processing.compare_to_catalog.luminosity_class_note`).
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

# The limits that turn a match score into a verdict live in
# `astrometricslib.models.stellar_source` (`NO_GOOD_MATCH_RMS`,
# `UNRELIABLE_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`), so the model, the gates and
# the star summary all read one value. This module imports them and defines
# none of its own.

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
#
# Shared with `post_processing.compare_to_catalog` (imported from here), which
# uses it to write the luminosity-class note; kept here because
# `nearest_reference_type` (below) also needs it to pick the giant reference
# ladder, a classification concern, not a catalog-comparison one.
_LUMINOSITY_CLASS = re.compile(r"[OBAFGKM]\d+(?:\.\d)?(?:-\d)?\s*(Ia\+?|Iab|Ib|III|II|IV|V|I)(?![IVab])")


def luminosity_class(spectral_type_text: str | None) -> str | None:
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
    return luminosity_class(catalog_spectral_type) in ("I", "II", "III")


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

    catalog_class = luminosity_class(spectral_type_text.strip())
    candidates = REFERENCE_SPECTRAL_TYPES
    if catalog_class in ("I", "II", "III"):
        same_class = [
            reference
            for reference in GIANT_REFERENCE_SPECTRAL_TYPES
            if luminosity_class(reference) == catalog_class
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


def _get_blurred_templates(
    resolution_element_angstrom: float, resolution_profile: ResolutionProfile | None = None
) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """Give every reference spectrum blurred to a given resolution.

    The references are much sharper than a grism spectrum, so they are
    blurred to the instrument's resolution before being compared.
    Comparing sharp references with a blurred observation would add a
    mismatch that has nothing to do with the star's type.

    Parameters
    ----------
    resolution_element_angstrom : `float`
        The resolution element to blur to, in Angstroms.
    resolution_profile : `ResolutionProfile`, optional
        How the resolution element changes with wavelength. When given, it
        is used instead of the single width. These blurred copies are not
        kept for reuse, since each spectrum has its own profile.

    Returns
    -------
    templates : `dict`
        Spectral type label mapped to `(wavelength_angstrom, flux)`, the
        flux blurred with a gaussian as wide as one resolution element.
    """
    if resolution_profile is not None:
        return {
            spectral_type: (wavelength, blur_to_resolution_profile(wavelength, flux, resolution_profile))
            for spectral_type, (wavelength, flux) in _get_reference_templates().items()
        }
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


def _rank_by_rms(
    rms_by_type: dict[str, float],
    correlation_by_type: dict[str, float],
    reduced_chi_square_by_type: dict[str, float] | None = None,
) -> list[dict[str, object]]:
    """Sort the compared types from the closest match to the furthest.

    Parameters
    ----------
    rms_by_type : `dict` [`str`, `float`]
        Each compared type's relative RMS.
    correlation_by_type : `dict` [`str`, `float`]
        Each compared type's Pearson correlation.
    reduced_chi_square_by_type : `dict` [`str`, `float`], optional
        Each compared type's reduced chi-square, for the types that have one.

    Returns
    -------
    ranked_types : `list` [`dict`]
        Every compared type, closest first. Each entry has
        ``"spectral_type"``, ``"rms"`` (the relative root-mean-square
        difference; lower is closer), ``"correlation"`` (the Pearson
        coefficient, kept for comparison; it barely separates types) and
        ``"reduced_chi_square"`` (see `classify_spectral_type`; `None` for a
        type without one). The order follows the RMS alone. The scores are
        not probabilities.
    """
    chi_square = reduced_chi_square_by_type or {}
    ranked = [
        {
            "spectral_type": spectral_type,
            "rms": float(rms),
            "correlation": float(correlation_by_type[spectral_type]),
            "reduced_chi_square": chi_square.get(spectral_type),
        }
        for spectral_type, rms in rms_by_type.items()
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
        "classification_rms": None,
        "rms_gap_to_second_best": None,
        "is_ambiguous": None,
        "rms_gap_to_next_class": None,
        "is_class_ambiguous": None,
        "match_quality": None,
        "reason": reason,
        "correlation_by_type": {},
        "ranked_types": [],
        "excluded_windows_angstrom": [],
        "reduced_chi_square": None,
        "second_best_reduced_chi_square": None,
    }


def _reduced_chi_square(
    observed_wavelength: np.ndarray,
    observed: np.ndarray,
    observed_errors: np.ndarray,
    template_wavelength: np.ndarray,
    template_flux: np.ndarray,
    scale: float,
    low: float,
    high: float,
    excluded_windows: Sequence[tuple[float, float]],
    exclude_atmospheric_bands: bool,
) -> float | None:
    """Score one scaled reference against the observation with the errors.

    The sum runs over the observed samples (not the reference's finer
    grid), because the sample errors are independent per sample. It uses the
    samples the RMS comparison uses: inside the overlap, outside the
    atmospheric bands and outside the excluded windows. The reference is
    scaled by the scale the RMS found, so this is the chi-square of the same
    residuals, not the smallest chi-square any scale could give.

    Parameters
    ----------
    observed_wavelength : `numpy.ndarray`
        The observed wavelengths, in Angstroms.
    observed : `numpy.ndarray`
        The observed brightness.
    observed_errors : `numpy.ndarray`
        The 1-sigma error of each observed brightness. NaN or zero marks a
        sample without a usable error.
    template_wavelength : `numpy.ndarray`
        The reference's wavelengths, in Angstroms.
    template_flux : `numpy.ndarray`
        The reference's blurred brightness.
    scale : `float`
        The factor that brings the reference to the observation's brightness.
    low, high : `float`
        The comparison range, in Angstroms.
    excluded_windows : `Sequence` [`tuple` [`float`, `float`]]
        Windows to leave out, as (low, high) pairs.
    exclude_atmospheric_bands : `bool`
        Whether to leave out the atmospheric bands.

    Returns
    -------
    reduced_chi_square : `float` or `None`
        The chi-square divided by its degrees of freedom (the sample count
        minus 1 for the fitted scale), or `None` when fewer than
        `_MINIMUM_OVERLAP_POINTS` samples have a usable error.
    """
    keep = (
        (observed_wavelength >= max(low, template_wavelength.min()))
        & (observed_wavelength <= min(high, template_wavelength.max()))
        & np.isfinite(observed_errors)
        & (observed_errors > 0)
    )
    if exclude_atmospheric_bands:
        keep &= ~atmospheric_band_mask(observed_wavelength)
    for window_low, window_high in excluded_windows:
        keep &= (observed_wavelength < window_low) | (observed_wavelength > window_high)
    if keep.sum() < _MINIMUM_OVERLAP_POINTS:
        return None
    template_on_observed = np.interp(observed_wavelength[keep], template_wavelength, template_flux)
    residual = (observed[keep] - scale * template_on_observed) / observed_errors[keep]
    return float(np.sum(residual**2)) / float(keep.sum() - 1)


def classify_spectral_type(
    wavelength_angstrom: np.ndarray,
    intensity: np.ndarray,
    resolution_element_angstrom: float = FALLBACK_RESOLUTION_ELEMENT_ANGSTROM,
    exclude_atmospheric_bands: bool = True,
    reference_types: Iterable[str] | None = None,
    excluded_windows_angstrom: Sequence[tuple[float, float]] | None = None,
    resolution_profile: ResolutionProfile | None = None,
    intensity_errors: np.ndarray | None = None,
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
    excluded_windows_angstrom : `Sequence`, optional
        Wavelength windows, each a (low, high) pair in Angstroms, to leave
        out of the comparison. Emission lines belong here: the references hold
        absorption, so a star whose Balmer lines are filled with emission
        looks like a different type unless those lines are set aside. The
        windows used are returned in ``"excluded_windows_angstrom"``.
    resolution_profile : `ResolutionProfile`, optional
        How the blur changes along this spectrum. When given, each
        reference is blurred by the resolution element at each wavelength
        instead of by `resolution_element_angstrom` everywhere.
    intensity_errors : `np.ndarray`, optional
        The 1-sigma error of each `intensity` value, in the same units and
        order (see `intensity_variance`). When given, each reference also gets
        a reduced chi-square, stored next to its RMS. The errors never change
        the ranking, the decision or any threshold: those use the RMS alone.

    Returns
    -------
    result : `dict`
        ``"spectral_type"``: the best-matching reference label, or
        ``"Unknown"`` when there was not enough of the spectrum to compare
        (then ``"reason"`` says why).
        ``"classification_rms"``: the best reference's relative
        root-mean-square difference, or `None` when unknown. Lower is
        closer. It is a match score, not a probability.
        ``"rms_gap_to_second_best"``: the second-best reference's RMS minus
        the best one's, in the same RMS units, or `None` when fewer than two
        references were compared. It is a difference in RMS, not a
        probability.
        ``"is_ambiguous"``: `True` when that gap is below
        `AMBIGUOUS_RMS_GAP` (see `stellar_source`), `None` when there is no
        gap. This is the subtype-level statement.
        ``"rms_gap_to_next_class"``: the RMS of the best reference whose
        spectral class letter differs from the best one's, minus the best
        RMS, in the same units, or `None` when no other class was compared.
        ``"is_class_ambiguous"``: `True` when that gap is below
        `AMBIGUOUS_RMS_GAP`, `None` when there is no gap. This is the
        class-level statement.
        ``"match_quality"``: ``"good"`` or ``"poor"`` (see
        `NO_GOOD_MATCH_RMS`).
        ``"correlation_by_type"``: every reference's Pearson correlation.
        ``"ranked_types"``: every compared type, closest first, scored by
        RMS (see `_rank_by_rms`). Each entry also has ``"reduced_chi_square"``,
        or `None` without `intensity_errors`.
        ``"reduced_chi_square"`` and ``"second_best_reduced_chi_square"``: the
        reduced chi-square of the best and of the second-best reference by
        RMS (not the two smallest chi-squares), or `None`. A reduced
        chi-square near 1 means the reference fits within the noise. A value
        far above 1 means the mismatch is larger than the noise, which for a
        bright star is the usual case: the references and the instrument
        response are not exact.
        ``"excluded_windows_angstrom"``: the windows left out of the
        comparison, as (low, high) pairs (empty when there were none).
    """
    wavelength_angstrom = np.asarray(wavelength_angstrom, dtype=float)
    intensity = np.asarray(intensity, dtype=float)
    sample_errors = (
        np.full(intensity.shape, np.nan)
        if intensity_errors is None
        else np.asarray(intensity_errors, dtype=float)
    )
    valid = np.isfinite(wavelength_angstrom) & np.isfinite(intensity) & (intensity > 0)
    wavelength_angstrom = wavelength_angstrom[valid]
    intensity = intensity[valid]
    sample_errors = sample_errors[valid]

    low, high = CLASSIFICATION_WAVELENGTH_RANGE_ANGSTROM
    in_range = (wavelength_angstrom >= low) & (wavelength_angstrom <= high)
    if in_range.sum() < _MINIMUM_OVERLAP_POINTS:
        return unclassified_result("too few samples inside the comparison range")
    order = np.argsort(wavelength_angstrom)
    wavelength_angstrom = wavelength_angstrom[order]
    intensity = intensity[order]
    sample_errors = sample_errors[order]

    coverage = min(wavelength_angstrom.max(), high) - max(wavelength_angstrom.min(), low)
    if coverage < MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:
        return unclassified_result(
            f"the spectrum covers only {max(coverage, 0.0):.0f} A, and at least "
            f"{MINIMUM_CLASSIFICATION_COVERAGE_ANGSTROM:.0f} A is needed"
        )

    excluded_windows = [
        (float(low_edge), float(high_edge)) for low_edge, high_edge in excluded_windows_angstrom or []
    ]
    rms_by_type: dict[str, float] = {}
    correlation_by_type: dict[str, float] = {}
    reduced_chi_square_by_type: dict[str, float] = {}
    allowed_types = set(REFERENCE_SPECTRAL_TYPES if reference_types is None else reference_types)
    for spectral_type, (template_wavelength, template_flux) in _get_blurred_templates(
        resolution_element_angstrom, resolution_profile
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
        for window_low, window_high in excluded_windows:
            common_grid = common_grid[(common_grid < window_low) | (common_grid > window_high)]
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
        if intensity_errors is not None:
            reduced_chi_square = _reduced_chi_square(
                wavelength_angstrom,
                intensity,
                sample_errors,
                template_wavelength,
                template_flux,
                best_fit_scale,
                low,
                high,
                excluded_windows,
                exclude_atmospheric_bands,
            )
            if reduced_chi_square is not None:
                reduced_chi_square_by_type[spectral_type] = reduced_chi_square

    if not rms_by_type:
        return unclassified_result("no reference overlaps the spectrum enough to compare")

    best_type = min(rms_by_type, key=rms_by_type.get)
    best_rms = rms_by_type[best_type]
    if best_rms > UNRELIABLE_MATCH_RMS:
        return unclassified_result(
            f"no reference matches this spectrum: even the closest ({best_type}) is off by "
            f"{best_rms:.0%} of its brightness, and more than {UNRELIABLE_MATCH_RMS:.0%} "
            "is too far to call a match"
        )
    rms_gap = rms_gap_to_second_best(rms_by_type.values())
    class_gap = rms_gap_to_next_class(rms_by_type.items())
    ranked_types = _rank_by_rms(rms_by_type, correlation_by_type, reduced_chi_square_by_type)
    return {
        "spectral_type": best_type,
        "classification_rms": best_rms,
        "rms_gap_to_second_best": rms_gap,
        "is_ambiguous": is_rms_gap_ambiguous(rms_gap),
        "rms_gap_to_next_class": class_gap,
        "is_class_ambiguous": is_rms_gap_ambiguous(class_gap),
        "match_quality": "poor" if best_rms > NO_GOOD_MATCH_RMS else "good",
        "reason": None,
        "correlation_by_type": correlation_by_type,
        "ranked_types": ranked_types,
        "excluded_windows_angstrom": excluded_windows,
        "reduced_chi_square": ranked_types[0]["reduced_chi_square"],
        "second_best_reduced_chi_square": ranked_types[1]["reduced_chi_square"]
        if len(ranked_types) > 1
        else None,
    }
