"""Purpose: Unit tests for the post-processing classification-trust checks.

Description: Verifies the poor-match and ambiguous-match flags, which both
read the relative RMS (root-mean-square difference) the classifier records
and neither of which is a probability. Also verifies that
`build_spectral_classification_concerns` only raises a concern for a
classified star that is actually shaky, not for one left unclassified or one
that matched cleanly, and that `assess_output_quality` reports each reachable
verdict using the gate constants `NO_GOOD_MATCH_RMS` and `AMBIGUOUS_RMS_GAP`.
"""

import pytest

from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    NO_GOOD_MATCH_RMS,
    SpectroscopyResult,
    StellarObject,
)
from astrometricslib.pipelines.spectroscopy.post_processing.assess_output_quality import (
    assess_output_quality,
    build_spectral_classification_concerns,
    is_classification_ambiguous,
    is_classification_class_ambiguous,
    is_classification_poor_match,
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


def test_is_classification_ambiguous_flags_a_gap_below_the_gate_constant() -> None:
    """Verify an RMS gap under `AMBIGUOUS_RMS_GAP` is caught as ambiguous."""
    assert is_classification_ambiguous(_candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 2)) is True


def test_is_classification_ambiguous_accepts_a_clear_winner() -> None:
    """Verify a gap above `AMBIGUOUS_RMS_GAP` isn't flagged as ambiguous."""
    assert is_classification_ambiguous(_candidates(0.05, 0.05 + 2 * AMBIGUOUS_RMS_GAP)) is False


def test_is_classification_ambiguous_ignores_the_input_order() -> None:
    """Verify the gap is measured between the two smallest RMS values."""
    shuffled = [*_candidates(0.30, 0.05)[::-1], {"spectral_type": "F0V", "rms": 0.06, "correlation": 0.9}]
    assert is_classification_ambiguous(shuffled) is True


def test_is_classification_ambiguous_handles_fewer_than_two_candidates() -> None:
    """Verify a single candidate (or none) can't be "too close to call"."""
    single_candidate = [{"spectral_type": "G0V", "rms": 0.04, "correlation": 0.9}]
    assert is_classification_ambiguous([]) is False
    assert is_classification_ambiguous(single_candidate) is False


def test_assess_output_quality_reports_each_reachable_verdict() -> None:
    """Verify poor, ambiguous and clean results each get a verdict."""
    poor = assess_output_quality(
        {"classification_rms": NO_GOOD_MATCH_RMS + 0.05, "ranked_types": _candidates(0.2, 0.4)}, None, 40.0
    )
    ambiguous = assess_output_quality(
        {"classification_rms": 0.05, "ranked_types": _candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4)},
        None,
        40.0,
    )
    clean = assess_output_quality(
        {"classification_rms": 0.05, "ranked_types": _candidates(0.05, 0.05 + 2 * AMBIGUOUS_RMS_GAP)},
        None,
        40.0,
    )

    assert (poor.is_poor_match, poor.is_ambiguous, poor.is_trustworthy) == (True, False, False)
    assert (ambiguous.is_poor_match, ambiguous.is_ambiguous, ambiguous.is_trustworthy) == (False, True, False)
    assert (clean.is_poor_match, clean.is_ambiguous, clean.is_trustworthy) == (False, False, True)


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


def test_a_neighbouring_subtype_is_ambiguous_but_not_class_ambiguous() -> None:
    """Verify a K5V/K7V tie is a subtype tie, not a class tie."""
    neighbours = _candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4, second_type="K7V")
    assert is_classification_ambiguous(neighbours) is True
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
    assert is_classification_ambiguous(ranked) is True
    assert is_classification_class_ambiguous(ranked) is False
    assert is_classification_class_ambiguous([{"spectral_type": "K5V", "rms": 0.05}]) is False


def test_assess_output_quality_reports_the_class_level_verdict() -> None:
    """Verify the assessment carries both ambiguity levels."""
    subtype_only = assess_output_quality(
        {
            "classification_rms": 0.05,
            "ranked_types": _candidates(0.05, 0.05 + AMBIGUOUS_RMS_GAP / 4, second_type="K7V"),
        },
        None,
        40.0,
    )
    assert (subtype_only.is_ambiguous, subtype_only.is_class_ambiguous) == (True, False)
