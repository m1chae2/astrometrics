"""Judges how much a spectrum's classification should be trusted.

Combines the classifier's own match statistics (a weak winning score, or
a near-tie with the runner-up) with the catalog comparison from
`compare_to_catalog`, into one overall verdict per star. `runner.py`
still uses `build_spectral_classification_concerns` to flag shaky stars
across a whole batch, the same way it always has.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from astrometricslib.models.spectroscopy_quality import CatalogComparison, OutputQualityAssessment
from astrometricslib.pipelines.spectroscopy.processing.spectral_classifier import POOR_MATCH_RMS_THRESHOLD

if TYPE_CHECKING:
    from astrometricslib.models.stellar_source import StellarObject

# The score reported as "confidence" is 1 minus the root-mean-square
# difference, so the poor-match threshold in the classifier becomes this
# score.
LOW_CONFIDENCE_THRESHOLD = 1.0 - POOR_MATCH_RMS_THRESHOLD

# When the top two ranked types' weights are closer than this, the
# classifier can't meaningfully tell them apart, and reporting only the
# winner would hide a near-tie.
AMBIGUOUS_PROBABILITY_MARGIN = 0.15


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


def assess_output_quality(
    classification: dict[str, object],
    catalog_comparison: CatalogComparison | None,
    resolution_element_angstrom: float,
) -> OutputQualityAssessment:
    """Judge how much to trust one star's classification.

    Parameters
    ----------
    classification : `dict`
        The result of `classify_spectral_type` (or an "Unknown" result).
    catalog_comparison : `CatalogComparison`, optional
        The result of `compare_to_catalog`, when one was made.
    resolution_element_angstrom : `float`
        How much the instrument blurred this spectrum, in Angstroms. Not
        yet used (see `is_subtype_finer_than_resolution` below), but
        threaded through so callers don't need to change again once it is.

    Returns
    -------
    assessment : `OutputQualityAssessment`
        The structured trust verdict; see that class for what each field
        means.
    """
    del resolution_element_angstrom  # not yet used -- see is_subtype_finer_than_resolution
    is_low_confidence = is_classification_low_confidence(classification.get("confidence"))  # type: ignore[arg-type]
    is_ambiguous = is_classification_ambiguous(classification.get("ranked_types") or [])  # type: ignore[arg-type]
    catalog_agrees = catalog_comparison.spectral_type_agrees if catalog_comparison is not None else None

    return OutputQualityAssessment(
        is_low_confidence=is_low_confidence,
        is_ambiguous=is_ambiguous,
        # Needs an Angstrom-per-subtype-step conversion that has not been
        # calibrated anywhere in this codebase yet; left unset (not
        # `False`) until that calibration exists, rather than guessing.
        is_subtype_finer_than_resolution=None,
        catalog_agrees=catalog_agrees,
        is_trustworthy=not (is_low_confidence or is_ambiguous or catalog_agrees is False),
    )
