"""Checks a spectrum's classification as numbers against limits.

Takes the classifier's own match numbers (how far the best reference is from
the spectrum, and how much closer it is than the runner-up and than the best
reference of another spectral class) and the catalog comparison from
`compare_to_catalog`. It builds quality checkpoint 3 (the final result) in the
common `StageQualityCheckpoint` shape. It also lists the stars whose
classification a reviewer should look at
(`build_spectral_classification_concerns`), which `runner.py` uses to count
shaky stars across a whole batch.

The match numbers are the relative RMS (root-mean-square difference as a
fraction of the spectrum's average brightness) that the classifier records.
None is a probability. The limits, `NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`,
are defined once, in `astrometricslib.models.stellar_source`. The catalog
limit, `DIFFERS_FROM_CATALOG_SUBTYPES`, is imported from the same module, and
the colour limit, `COLOUR_DISAGREEMENT_MAGNITUDES`, from `compare_to_catalog`.

Ambiguity is measured at two levels with the same limit. The subtype level
compares the best reference with the runner-up, usually a neighbouring
subtype of the same class. The class level compares the best reference with
the best reference of a different spectral class letter. The run-level
`spectral_classification` gate fails only on a poor match or a class-level
ambiguity (the concerns built here).

The checkpoint also carries four metrics from the comparison with the
star's Gaia DR3 XP spectrum (`compare_to_gaia_xp`), which tests the
instrument response, the airmass correction and the wavelength scale.
"""

from collections.abc import Iterable
from typing import TYPE_CHECKING

from astrometricslib.models.quality.gaia_xp_comparison import GaiaXpComparison
from astrometricslib.models.quality.spectroscopy_quality import (
    CatalogComparison,
    StageQualityCheckpoint,
    metric,
)
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
    is_rms_gap_ambiguous,
    ladder_position,
    rms_gap_to_next_class,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import (
    COLOUR_DISAGREEMENT_MAGNITUDES,
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


def _optional_float(value: object) -> float | None:
    """Give a stored number as a `float`, or `None` when it is not a number.

    Parameters
    ----------
    value : `object`
        A number, or anything else (usually `None`).

    Returns
    -------
    number : `float` or `None`
        The value as a `float`, or `None` if it is not an int or float.
    """
    return float(value) if isinstance(value, int | float) else None


def output_quality_checkpoint(
    classification: dict[str, object],
    *,
    own_spectral_type: str,
    catalog_spectral_type: str | None,
    catalog_comparison: CatalogComparison | None,
    gaia_xp_comparison: GaiaXpComparison | None = None,
) -> StageQualityCheckpoint:
    """Build quality checkpoint 3 from the classifier's numbers.

    The best match's distance and the gap to the next class are checked at
    checkpoint 2, so this checkpoint does not repeat them as metrics.

    Parameters
    ----------
    classification : `dict`
        The result of `classify_spectral_type` (or an "Unknown" result).
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
        The ``post_processing`` checkpoint. Its metrics are numbers against
        their limits: the gap to the second best reference, the steps from the
        catalog type and the colour difference from the catalog. For an
        unclassified spectrum every classification value is `None` and the
        checkpoint carries the ``unclassified`` flag, because there is
        nothing to measure. A flag names each failed check, and a
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
    signed_colour_difference = (
        catalog_comparison.b_minus_v_difference_mag if catalog_comparison is not None else None
    )
    colour_difference = (
        abs(signed_colour_difference) if is_classified and signed_colour_difference is not None else None
    )

    def when_classified(value: object) -> float | None:
        """Give a classifier number, `None` if unclassified.

        Returns
        -------
        value : `float` or `None`
            The number, or `None`.
        """
        return _optional_float(value) if is_classified else None

    metrics = [
        metric(
            "catalog_type_steps_apart",
            steps_apart,
            "subtype steps",
            limit=DIFFERS_FROM_CATALOG_SUBTYPES,
            higher_is_better=False,
            note="ten steps make one spectral class, so B0 is 10 and A5 is 25",
        ),
        metric(
            "rms_gap_to_second_best",
            when_classified(classification.get("rms_gap_to_second_best")),
            "relative RMS",
            limit=AMBIGUOUS_RMS_GAP,
            note="limit is AMBIGUOUS_RMS_GAP; a smaller gap means the subtype is uncertain",
        ),
        metric(
            "colour_difference_mag",
            colour_difference,
            "magnitude",
            limit=COLOUR_DISAGREEMENT_MAGNITUDES,
            higher_is_better=False,
            note="size of the spectrum's B-V minus the catalog B-V; limit is COLOUR_DISAGREEMENT_MAGNITUDES",
        ),
    ]
    gaia_xp_entries = gaia_xp_metrics(gaia_xp_comparison) if gaia_xp_comparison is not None else []
    metrics.extend(gaia_xp_entries)
    flags = []
    if any(entry.passed is False for entry in gaia_xp_entries):
        flags.append("gaia_xp_disagrees")
    if not is_classified:
        flags.append("unclassified")
    else:
        # The best match and the class gap are checked, with their limits, at
        # checkpoint 2 (`classification_rms` and `rms_gap_to_next_class`); the
        # flags here repeat those outcomes for a reader of this checkpoint.
        passed_by_name = {entry.name: entry.passed for entry in metrics}
        if is_classification_poor_match(_optional_float(classification.get("classification_rms"))):
            flags.append("poor_match")
        if passed_by_name["rms_gap_to_second_best"] is False:
            flags.append("subtype_ambiguous")
        if is_rms_gap_ambiguous(_optional_float(classification.get("rms_gap_to_next_class"))):
            flags.append("class_ambiguous")
        if passed_by_name["catalog_type_steps_apart"] is False:
            flags.append("catalog_disagrees")
        if passed_by_name["colour_difference_mag"] is False:
            flags.append("colour_disagrees")
    return StageQualityCheckpoint(stage="post_processing", metrics=metrics, flags=flags)
