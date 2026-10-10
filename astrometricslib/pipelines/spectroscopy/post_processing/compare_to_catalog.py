"""Compares a star's self-determined spectrum with its catalog entry.

The catalog (SIMBAD/Gaia) already has a spectral type and a B-V colour
for most stars, measured a different way, so this never feeds back into
the classification itself -- it is a check done after processing, to
catch a spectrum that probably is not this star's at all (a bright
neighbour's light, glare from a nearby bright star, or a name given to
the wrong object).
"""

import numpy as np

from astrometricslib.models.spectroscopy_quality import CatalogComparison
from astrometricslib.models.stellar_source import DIFFERS_FROM_CATALOG_SUBTYPES, types_differ_on_ladder
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import (
    is_catalog_giant,
    luminosity_class,
)

# The limit for "the measured type differs from the catalog type" is
# `DIFFERS_FROM_CATALOG_SUBTYPES` in `astrometricslib.models.stellar_source`,
# and `types_differ_on_ladder` applies it. This module defines no copy.

# A catalog colour redder than this is not compared. The references stop at
# M4V (B-V 1.64), so the map from window colour to B-V is not calibrated for
# later types.
MAXIMUM_CALIBRATED_B_MINUS_V = 1.7

# A spectrum is flagged when its colour differs from the catalog's by more
# than this many magnitudes. On 2026-09-24, 30 of the 36 stars stayed within
# 0.30 mag (the worst, HD 150293, was 0.30 too red). The problem spectra were
# further out: TYC 3105-899-1 -0.58, Elnath +1.04 and HD 172449 +1.0 (which is
# also left unclassified, so gets no note). Arcturus (+0.39 to +0.47 across
# reruns) sits right at the threshold and is not reliably flagged. 0.4 is a
# judgement call from that one data set: it is above every ordinary star seen
# and well above the 0.09 mag median excess for F, G and early K stars, so
# ordinary reddening does not trigger it, but the gap is thin.
COLOUR_DISAGREEMENT_MAGNITUDES = 0.4


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
        The warning, or an empty string when the two types are within
        `DIFFERS_FROM_CATALOG_SUBTYPES` steps, or when either type is
        missing or not on the O-to-M ladder.
    """
    if not types_differ_on_ladder(classified_type, catalog_spectral_type):
        return ""
    return (
        f"the spectrum matches {classified_type} but the catalog gives {catalog_spectral_type}, "
        f"more than {DIFFERS_FROM_CATALOG_SUBTYPES:.0f} subtype steps "
        f"({DIFFERS_FROM_CATALOG_SUBTYPES / 10:g} spectral classes) apart: "
        "the spectrum may not be this star's "
        "(a bright neighbour, glare from a very bright star, or a wrong name)"
    )


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
    catalog_class = luminosity_class(catalog_spectral_type)
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


def colour_disagreement_note(catalog_b_minus_v: float | None, synthetic_colour: float | None) -> str:
    """Warn when the spectrum's colour is far from the catalog's.

    Parameters
    ----------
    catalog_b_minus_v : `float`, optional
        The star's catalog B-V, from SIMBAD.
    synthetic_colour : `float`, optional
        The B-V estimated from the spectrum by `synthetic_b_minus_v`.

    Returns
    -------
    note : `str`
        The warning, or an empty string when either colour is missing, or
        they are within `COLOUR_DISAGREEMENT_MAGNITUDES`. Also empty when
        the catalog colour is redder than `MAXIMUM_CALIBRATED_B_MINUS_V`,
        because the map is not calibrated there.
    """
    if catalog_b_minus_v is None or synthetic_colour is None:
        return ""
    if not (np.isfinite(catalog_b_minus_v) and np.isfinite(synthetic_colour)):
        return ""
    if catalog_b_minus_v > MAXIMUM_CALIBRATED_B_MINUS_V:
        return ""
    difference = synthetic_colour - catalog_b_minus_v
    if abs(difference) <= COLOUR_DISAGREEMENT_MAGNITUDES:
        return ""
    direction = "redder" if difference > 0 else "bluer"
    return (
        f"the spectrum is {abs(difference):.1f} mag {direction} than the catalog colour "
        f"(B-V {catalog_b_minus_v:.2f}, spectrum about {synthetic_colour:.2f}): the spectrum may not be "
        "this star's (a bright neighbour, glare from a very bright star, or a wrong name)"
    )


def compare_to_catalog(
    catalog_spectral_type: str | None,
    classified_type: str | None,
    closest_giant: tuple[str, float] | None,
    catalog_b_minus_v: float | None,
    synthetic_colour: float | None,
) -> CatalogComparison:
    """Build the full catalog comparison for one classified spectrum.

    Parameters
    ----------
    catalog_spectral_type : `str`, optional
        The star's catalog spectral type, when known.
    classified_type : `str`, optional
        The type matched from the spectrum, or "Unknown".
    closest_giant : `tuple` [`str`, `float`], optional
        The closest giant reference and its score, when the catalog calls
        this star a giant and that lookup was done.
    catalog_b_minus_v : `float`, optional
        The star's catalog B-V, from SIMBAD.
    synthetic_colour : `float`, optional
        The B-V estimated from the spectrum (see `synthetic_b_minus_v`),
        or `None` when it could not be measured.

    Returns
    -------
    comparison : `CatalogComparison`
        The structured comparison; see that class for what each field means.
    """
    differs = types_differ_on_ladder(classified_type, catalog_spectral_type)
    spectral_type_agrees = None if differs is None else not differs
    is_luminosity_uncertain = bool(
        is_catalog_giant(catalog_spectral_type) and classified_type and classified_type != "Unknown"
    )
    colour_agrees = None
    if catalog_b_minus_v is not None and synthetic_colour is not None:
        if np.isfinite(catalog_b_minus_v) and np.isfinite(synthetic_colour):
            colour_agrees = (
                None
                if catalog_b_minus_v > MAXIMUM_CALIBRATED_B_MINUS_V
                else abs(synthetic_colour - catalog_b_minus_v) <= COLOUR_DISAGREEMENT_MAGNITUDES
            )

    return CatalogComparison(
        spectral_type_agrees=spectral_type_agrees,
        spectral_type_note=catalog_disagreement_note(catalog_spectral_type, classified_type),
        is_luminosity_class_uncertain=is_luminosity_uncertain,
        luminosity_class_note=luminosity_class_note(catalog_spectral_type, classified_type, closest_giant),
        closest_giant_type=closest_giant[0] if closest_giant is not None else None,
        closest_giant_rms=closest_giant[1] if closest_giant is not None else None,
        colour_agrees=colour_agrees,
        colour_note=colour_disagreement_note(catalog_b_minus_v, synthetic_colour),
    )
