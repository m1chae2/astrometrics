"""Purpose: Unit tests for the post-processing classification-trust checks.

Description: Verifies the poor-match and class-ambiguity checks, which both
read the relative RMS (root-mean-square difference) the classifier records
and neither of which is a probability. Also verifies that
`build_spectral_classification_concerns` only raises a concern for a
classified star that is actually shaky, not for one left unclassified or one
that matched cleanly, and that `output_quality_checkpoint` reports each
number against the limits `NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`.
"""

import pytest

from astrometricslib.models.quality.spectroscopy_quality import CatalogComparison
from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    NO_GOOD_MATCH_RMS,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    build_spectral_classification_concerns,
    is_classification_class_ambiguous,
    is_classification_poor_match,
    output_quality_checkpoint,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import (
    COLOUR_DISAGREEMENT_MAGNITUDES,
)


def _candidates(best_rms: float, second_rms: float, second_type: str = "M0V") -> list[dict[str, object]]:
    """Build a two-entry ranked list with the given RMS values.

    Parameters
    ----------
    best_rms : `float`
        Relative RMS of the closest reference (type K5V).
    second_rms : `float`
        Relative RMS of the runner-up.
    second_type : `str`, optional
        The runner-up's type. The default, M0V, is a different class from
        K5V; pass a K type for a same-class neighbour.

    Returns
    -------
    candidates : `list` [`dict`]
        Entries shaped like the classifier's ``"ranked_types"``.
    """
    return [
        {"spectral_type": "K5V", "rms": best_rms, "correlation": 0.93},
        {"spectral_type": second_type, "rms": second_rms, "correlation": 0.92},
    ]


def test_is_classification_poor_match_flags_rms_above_the_gate_constant() -> None:
    """Verify `NO_GOOD_MATCH_RMS` separates poor from good matches."""
    assert is_classification_poor_match(NO_GOOD_MATCH_RMS + 0.01) is True
    assert is_classification_poor_match(NO_GOOD_MATCH_RMS - 0.01) is False


def test_is_classification_poor_match_treats_unclassified_as_not_poor() -> None:
    """Verify an unclassified star (RMS `None`) isn't a "poor match".

    It's a separate "nothing to compare" case -- flagging it the same
    way would conflate "no data" with "shaky match".
    """
    assert is_classification_poor_match(None) is False


def test_build_spectral_classification_concerns_flags_poor_match_and_class_ambiguous() -> None:
    """Verify concerns need a poor match or a class-level tie."""
    unclassified = StellarObject(
        id="Unclassified", spectroscopy=SpectroscopyResult(self_determined_spectral_type="Unknown")
    )
    poor_match = StellarObject(
        id="PoorMatchStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="O5V",
            self_determined_spectral_type_rms=0.25,
            self_determined_spectral_type_candidates=_candidates(0.25, 0.40),
        ),
    )
    ambiguous = StellarObject(
        id="AmbiguousStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="K5V",
            self_determined_spectral_type_rms=0.05,
            self_determined_spectral_type_candidates=_candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4),
        ),
    )
    subtype_only = StellarObject(
        id="SubtypeOnlyStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="K5V",
            self_determined_spectral_type_rms=0.05,
            self_determined_spectral_type_candidates=_candidates(
                0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4, second_type="K7V"
            ),
        ),
    )
    clear = StellarObject(
        id="ClearStar",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="M5V",
            self_determined_spectral_type_rms=0.05,
            self_determined_spectral_type_candidates=_candidates(0.05, 0.05 + 2 * AMBIGUOUS_RMS_GAP),
        ),
    )

    concerns = build_spectral_classification_concerns([
        unclassified,
        poor_match,
        ambiguous,
        subtype_only,
        clear,
    ])

    concerns_by_id = {concern["star_id"]: concern for concern in concerns}
    assert set(concerns_by_id) == {"PoorMatchStar", "AmbiguousStar"}
    assert concerns_by_id["PoorMatchStar"]["reason"] == "poor_match"
    assert concerns_by_id["AmbiguousStar"]["reason"] == "class_ambiguous"
    assert concerns_by_id["AmbiguousStar"]["rms_gap_to_next_class"] == pytest.approx(AMBIGUOUS_RMS_GAP / 4)


def test_a_neighbouring_subtype_is_not_class_ambiguous() -> None:
    """Verify a K5V/K7V tie is a subtype tie, not a class tie."""
    neighbours = _candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4, second_type="K7V")
    assert is_classification_class_ambiguous(neighbours) is False


def test_a_different_class_at_a_tiny_gap_is_class_ambiguous() -> None:
    """Verify K5V against M0V at a tiny gap is a class tie."""
    assert is_classification_class_ambiguous(_candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4)) is True
    assert is_classification_class_ambiguous(_candidates(0.05, 0.05 + 2 * AMBIGUOUS_RMS_GAP)) is False


def test_class_ambiguity_looks_past_same_class_neighbours() -> None:
    """Verify the next class is the best candidate of another letter."""
    ranked = [
        {"spectral_type": "K5V", "rms": 0.05},
        {"spectral_type": "K7V", "rms": 0.051},
        {"spectral_type": "M0V", "rms": 0.2},
    ]
    assert is_classification_class_ambiguous(ranked) is False
    assert is_classification_class_ambiguous([{"spectral_type": "K5V", "rms": 0.05}]) is False


def _classification(best_rms: float, gap_second: float | None, gap_class: float | None) -> dict[str, object]:
    """Build the classifier numbers that checkpoint 3 reads.

    Parameters
    ----------
    best_rms : `float`
        Relative RMS of the best reference.
    gap_second : `float`, optional
        Gap to the runner-up.
    gap_class : `float`, optional
        Gap to the best reference of another class.

    Returns
    -------
    classification : `dict`
        The three numbers under the classifier's keys.
    """
    return {
        "classification_rms": best_rms,
        "rms_gap_to_second_best": gap_second,
        "rms_gap_to_next_class": gap_class,
    }


def _checkpoint_outcomes(classification: dict[str, object]) -> dict[str, bool | None]:
    """Run checkpoint 3 and give each metric's check outcome by name.

    Parameters
    ----------
    classification : `dict`
        The classifier numbers.

    Returns
    -------
    outcomes : `dict` [`str`, `bool` or `None`]
        Metric name to ``passed``.
    """
    checkpoint = output_quality_checkpoint(
        classification,
        own_spectral_type="K5V",
        catalog_spectral_type="K5V",
        catalog_comparison=None,
    )
    return {entry.name: entry.passed for entry in checkpoint.metrics}


def test_checkpoint_three_reports_each_reachable_outcome_from_the_numbers() -> None:
    """Verify a tight gap fails only its check and a clean result passes."""
    ambiguous = _checkpoint_outcomes(_classification(0.05, AMBIGUOUS_RMS_GAP / 4, 0.2))
    clean = _checkpoint_outcomes(_classification(0.05, 2 * AMBIGUOUS_RMS_GAP, 2 * AMBIGUOUS_RMS_GAP))
    unclassified = output_quality_checkpoint(
        _classification(0.05, 0.1, 0.1),
        own_spectral_type="Unknown",
        catalog_spectral_type="B0V",
        catalog_comparison=None,
    )

    assert ambiguous["rms_gap_to_second_best"] is False
    assert not any(outcome is False for outcome in clean.values())
    assert [entry.value for entry in unclassified.metrics] == [None, None, None]


def test_checkpoint_three_judges_the_colour_difference_by_its_size() -> None:
    """Verify a B-V difference beyond the limit, of either sign, fails."""
    too_red = CatalogComparison(b_minus_v_difference_mag=COLOUR_DISAGREEMENT_MAGNITUDES + 0.1)
    too_blue = CatalogComparison(b_minus_v_difference_mag=-COLOUR_DISAGREEMENT_MAGNITUDES - 0.1)
    close = CatalogComparison(b_minus_v_difference_mag=-0.1)
    outcomes = []
    for comparison in (too_red, too_blue, close, None):
        checkpoint = output_quality_checkpoint(
            _classification(0.05, 0.1, 0.1),
            own_spectral_type="K5V",
            catalog_spectral_type="K5V",
            catalog_comparison=comparison,
        )
        outcomes.append({entry.name: entry.passed for entry in checkpoint.metrics}["colour_difference_mag"])

    assert outcomes == [False, False, True, None]


def test_checkpoint_three_flags_name_the_failed_checks_and_no_trust_verdict() -> None:
    """Verify flags follow the failed checks, with no trust verdict."""
    checkpoint = output_quality_checkpoint(
        _classification(NO_GOOD_MATCH_RMS + 0.05, AMBIGUOUS_RMS_GAP / 4, AMBIGUOUS_RMS_GAP / 4),
        own_spectral_type="K5V",
        catalog_spectral_type="B0V",
        catalog_comparison=CatalogComparison(b_minus_v_difference_mag=0.9),
    )
    assert checkpoint.flags == [
        "poor_match",
        "subtype_ambiguous",
        "class_ambiguous",
        "catalog_disagrees",
        "colour_disagrees",
    ]
    assert "trustworthy" not in {entry.name for entry in checkpoint.metrics}
    assert "not_trustworthy" not in checkpoint.flags
