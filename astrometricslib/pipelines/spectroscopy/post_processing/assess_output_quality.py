"""Judges how much a spectrum's classification should be trusted.

Combines the classifier's own match numbers (a best match that is still far
from the spectrum, or a near-tie with the runner-up) with the catalog
comparison from `compare_to_catalog`, into one overall verdict per star.
`runner.py` uses `build_spectral_classification_concerns` to flag shaky stars
across a whole batch.

The match checks work on the relative RMS (root-mean-square difference as a
fraction of the spectrum's average brightness) that the classifier records.
None is a probability. The limits for the checks, `NO_GOOD_MATCH_RMS` and
`AMBIGUOUS_RMS_GAP`, are defined once, in
`astrometricslib.models.stellar_source`.

Ambiguity is judged at two levels with the same limit. The subtype level
compares the best reference with the runner-up, usually a neighbouring
subtype of the same class. The class level compares the best reference with
the best reference of a different spectral class letter. A star's trust
verdict uses both. The run-level `spectral_classification` gate fails only
on a poor match or a class-level ambiguity (the concerns built here).

The same verdict also forms quality checkpoint 3 (the final result), built by
`output_quality_checkpoint` in the common `StageQualityCheckpoint` shape. Its
catalog limit, `DIFFERS_FROM_CATALOG_SUBTYPES`, is imported from the same
module. The checkpoint also carries four metrics from the comparison with the
star's Gaia DR3 XP spectrum (`compare_to_gaia_xp`), which tests the
instrument response, the airmass correction and the wavelength scale.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from astrometricslib.models.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.spectroscopy_quality import (
    CatalogComparison,
    OutputQualityAssessment,
    StageQualityCheckpoint,
    metric,
)
from astrometricslib.models.stellar_source import (
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
    is_rms_gap_ambiguous,
    ladder_position,
    rms_gap_to_next_class,
    rms_gap_to_second_best,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_gaia_xp import gaia_xp_metrics

if TYPE_CHECKING:
    from astrometricslib.models.stellar_source import StellarObject


def is_classification_poor_match(classification_rms: float | None) -> bool:
    """Decide if the best reference still fits the spectrum badly.

    Parameters
    ----------
    classification_rms : `float`, optional
        The best reference's relative RMS (lower is closer), or `None` for
        an unclassified star.

    Returns
    -------
    is_poor_match : `bool`
        `True` if there was a classification and its RMS is above
        `NO_GOOD_MATCH_RMS`. `False` for an unclassified star (`None`) --
        that is a separate "nothing to compare" case, not a shaky match.
    """
    return classification_rms is not None and classification_rms > NO_GOOD_MATCH_RMS


def is_classification_ambiguous(ranked_types: Iterable[dict[str, object]]) -> bool:
    """Decide if the top two candidate types are too close to call.

    Parameters
    ----------
    ranked_types : `Iterable` [`dict`]
        The candidates, each with an ``"rms"`` entry (relative RMS).

    Returns
    -------
    is_ambiguous : `bool`
        `True` if at least two candidates have an RMS and the gap between
        the best and the second best is below `AMBIGUOUS_RMS_GAP`. The gap
        is a difference in RMS, not a probability. `False` when there is
        only one candidate to judge.
    """
    rms_values = [float(entry["rms"]) for entry in ranked_types if isinstance(entry.get("rms"), int | float)]
    return bool(is_rms_gap_ambiguous(rms_gap_to_second_best(rms_values)))


def is_classification_class_ambiguous(ranked_types: Iterable[dict[str, object]]) -> bool:
    """Decide if another spectral class fits almost as well as the best.

    Parameters
    ----------
    ranked_types : `Iterable` [`dict`]
        The candidates, each with a ``"spectral_type"`` and an ``"rms"``
        (relative RMS).

    Returns
    -------
    is_class_ambiguous : `bool`
        `True` if the best reference and the best reference of a different
        spectral class letter are less than `AMBIGUOUS_RMS_GAP` apart in
        RMS. The gap is a difference in RMS, not a probability. `False` when
        no other class was compared.
    """
    scores = [
        (str(entry.get("spectral_type", "")), float(entry["rms"]))
        for entry in ranked_types
        if isinstance(entry.get("rms"), int | float)
    ]
    return bool(is_rms_gap_ambiguous(rms_gap_to_next_class(scores)))


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
        (``"poor_match"``, ``"class_ambiguous"``, or both joined by a
        comma), ``"spectral_type"``, ``"classification_rms"`` and
        ``"rms_gap_to_next_class"`` (both in relative RMS units). A star that
        is ambiguous only at the subtype level is not a concern.
    """
    concerns: list[dict[str, object]] = []
    for star in stellar_objects:
        spectroscopy = star.spectroscopy
        if spectroscopy is None or spectroscopy.self_determined_spectral_type in ("", "Unknown"):
            continue

        reasons = []
        if is_classification_poor_match(spectroscopy.self_determined_spectral_type_rms):
            reasons.append("poor_match")
        if is_classification_class_ambiguous(spectroscopy.self_determined_spectral_type_candidates):
            reasons.append("class_ambiguous")

        if reasons:
            concerns.append({
                "star_id": star.id,
                "reason": ",".join(reasons),
                "spectral_type": spectroscopy.self_determined_spectral_type,
                "classification_rms": spectroscopy.self_determined_spectral_type_rms,
                "rms_gap_to_next_class": spectroscopy.rms_gap_to_next_class,
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
    classification_rms = classification.get("classification_rms")
    is_poor_match = is_classification_poor_match(
        float(classification_rms) if isinstance(classification_rms, int | float) else None
    )
    ranked_types = classification.get("ranked_types")
    candidates = ranked_types if isinstance(ranked_types, list) else []
    is_ambiguous = is_classification_ambiguous(candidates)
    is_class_ambiguous = is_classification_class_ambiguous(candidates)
    catalog_agrees = catalog_comparison.spectral_type_agrees if catalog_comparison is not None else None

    return OutputQualityAssessment(
        is_poor_match=is_poor_match,
        is_ambiguous=is_ambiguous,
        is_class_ambiguous=is_class_ambiguous,
        # Needs an Angstrom-per-subtype-step conversion that has not been
        # calibrated anywhere in this codebase yet; left unset (not
        # `False`) until that calibration exists, rather than guessing.
        is_subtype_finer_than_resolution=None,
        catalog_agrees=catalog_agrees,
        is_trustworthy=not (is_poor_match or is_ambiguous or catalog_agrees is False),
    )


def _flag_value(flag: bool | None) -> float | None:
    """Turn a yes/no result into 1.0, 0.0 or `None` for a checkpoint metric.

    Parameters
    ----------
    flag : `bool` or `None`
        A yes/no result, or `None` when it was not judged.

    Returns
    -------
    value : `float` or `None`
        1.0 for `True`, 0.0 for `False`, `None` for `None`.
    """
    return None if flag is None else float(flag)


def output_quality_checkpoint(
    assessment: OutputQualityAssessment,
    *,
    own_spectral_type: str,
    catalog_spectral_type: str | None,
    catalog_comparison: CatalogComparison | None,
    gaia_xp_comparison: GaiaXpComparison | None = None,
) -> StageQualityCheckpoint:
    """Build quality checkpoint 3 from an output-quality assessment.

    Parameters
    ----------
    assessment : `OutputQualityAssessment`
        The result of `assess_output_quality`.
    own_spectral_type : `str`
        The type the pipeline measured, or ``"Unknown"`` / an empty string
        when none was.
    catalog_spectral_type : `str`, optional
        The star's catalog type, used to count how many subtype steps apart
        the two types are.
    catalog_comparison : `CatalogComparison`, optional
        The result of `compare_to_catalog`, for the colour check.
    gaia_xp_comparison : `GaiaXpComparison`, optional
        The result of `compare_to_gaia_xp`. When given, its four metrics
        are added whether or not the spectrum was classified, because the
        check tests the instrument response and wavelength scale, not the
        classification.

    Returns
    -------
    checkpoint : `StageQualityCheckpoint`
        The ``post_processing`` checkpoint. Each yes/no verdict is a metric
        with value 1.0 (yes) or 0.0 (no). For an unclassified spectrum every
        classification value is `None` and the checkpoint carries the
        ``unclassified`` flag, because there is nothing to judge. A
        ``gaia_xp_disagrees`` flag is added when a Gaia XP metric failed.
    """
    is_classified = own_spectral_type not in ("", "Unknown")
    own_position = ladder_position(own_spectral_type) if is_classified else None
    catalog_position = ladder_position(catalog_spectral_type)
    steps_apart = (
        abs(own_position - catalog_position)
        if own_position is not None and catalog_position is not None
        else None
    )

    def verdict(value: bool | None) -> float | None:
        """Give a verdict's metric value, `None` if unclassified.

        Returns
        -------
        value : `float` or `None`
            1.0, 0.0 or `None`.
        """
        return _flag_value(value) if is_classified else None

    metrics = [
        metric(
            "catalog_type_steps_apart",
            steps_apart,
            "subtype steps",
            limit=DIFFERS_FROM_CATALOG_SUBTYPES,
            higher_is_better=False,
            note="ten steps make one spectral class, so B0 is 10 and A5 is 25",
        ),
        metric("poor_match", verdict(assessment.is_poor_match), "flag", limit=0.0, higher_is_better=False),
        metric(
            "subtype_ambiguous", verdict(assessment.is_ambiguous), "flag", limit=0.0, higher_is_better=False
        ),
        metric(
            "class_ambiguous",
            verdict(assessment.is_class_ambiguous),
            "flag",
            limit=0.0,
            higher_is_better=False,
        ),
        metric("catalog_agrees", verdict(assessment.catalog_agrees), "flag", limit=1.0),
        metric(
            "colour_agrees",
            verdict(catalog_comparison.colour_agrees if catalog_comparison is not None else None),
            "flag",
            limit=1.0,
            note="1 when the spectrum's own B-V colour is close to the catalog colour",
        ),
        metric("trustworthy", verdict(assessment.is_trustworthy), "flag", limit=1.0),
    ]
    gaia_xp_entries = gaia_xp_metrics(gaia_xp_comparison) if gaia_xp_comparison is not None else []
    metrics.extend(gaia_xp_entries)
    flags = []
    if any(entry.passed is False for entry in gaia_xp_entries):
        flags.append("gaia_xp_disagrees")
    if not is_classified:
        flags.append("unclassified")
    else:
        if assessment.is_poor_match:
            flags.append("poor_match")
        if assessment.is_ambiguous:
            flags.append("subtype_ambiguous")
        if assessment.is_class_ambiguous:
            flags.append("class_ambiguous")
        if assessment.catalog_agrees is False:
            flags.append("catalog_disagrees")
        if catalog_comparison is not None and catalog_comparison.colour_agrees is False:
            flags.append("colour_disagrees")
        if not assessment.is_trustworthy:
            flags.append("not_trustworthy")
    return StageQualityCheckpoint(stage="post_processing", metrics=metrics, flags=flags)
