"""Purpose: Tests for the spectral-match numbers on the star models.

Description: How far a spectrum's best reference type is from it, and how
much closer that reference is than the runner-up (in RMS, the root-mean-square
difference), are numbers on `SpectroscopyResult` (`rms_gap_to_second_best`,
`rms_gap_to_next_class`). The model makes no verdict from them. The limits
the checks compare them with (`NO_GOOD_MATCH_RMS`, `AMBIGUOUS_RMS_GAP`,
`DIFFERS_FROM_CATALOG_SUBTYPES`) are defined once in `stellar_source`. A
record stored with the old verdict fields still loads.
"""

import pytest

from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    SpectroscopyResult,
    StellarObject,
    is_rms_gap_ambiguous,
    ladder_position,
    rms_gap_to_next_class,
    rms_gap_to_second_best,
    types_differ_on_ladder,
)


def test_the_ladder_places_a_type_by_class_and_subtype() -> None:
    """A3V sits at 23 and G2V at 42; a carbon star is off the ladder."""
    assert ladder_position("A3V") == 23
    assert ladder_position("G2V") == 42
    assert ladder_position("K") == 55
    assert ladder_position("B9.5V") == pytest.approx(19.5)
    assert ladder_position("A5V+M3") == 25
    assert ladder_position("C5") is None
    assert ladder_position(None) is None


def test_the_gap_to_the_second_best_is_a_difference_in_rms_whatever_the_order() -> None:
    """The gap is the second-smallest RMS minus the smallest, in RMS units."""
    spectrum = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "B8V", "rms": 0.063},
            {"spectral_type": "A0V", "rms": 0.06},
            {"spectral_type": "F0V", "rms": 0.2},
        ]
    )
    assert spectrum.rms_gap_to_second_best == pytest.approx(0.003)
    assert is_rms_gap_ambiguous(spectrum.rms_gap_to_second_best) is True
    clear = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "A0V", "rms": 0.05},
            {"spectral_type": "A5V", "rms": 0.09},
        ]
    )
    assert clear.rms_gap_to_second_best == pytest.approx(0.04)
    assert is_rms_gap_ambiguous(clear.rms_gap_to_second_best) is False


def test_with_fewer_than_two_candidates_there_is_no_gap_and_no_verdict() -> None:
    """No gap is `None`, which is not the same as "clearly separated"."""
    assert SpectroscopyResult().rms_gap_to_second_best is None
    assert rms_gap_to_second_best([0.1]) is None
    assert is_rms_gap_ambiguous(None) is None


def test_the_gap_to_the_next_class_skips_same_class_neighbours() -> None:
    """The next class is the best candidate with a different class letter."""
    spectrum = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "K7V", "rms": 0.051},
            {"spectral_type": "K5V", "rms": 0.05},
            {"spectral_type": "M0V", "rms": 0.2},
            {"spectral_type": "G8V", "rms": 0.15},
        ]
    )
    assert spectrum.rms_gap_to_second_best == pytest.approx(0.001)
    assert is_rms_gap_ambiguous(spectrum.rms_gap_to_second_best) is True
    assert spectrum.rms_gap_to_next_class == pytest.approx(0.10)
    assert is_rms_gap_ambiguous(spectrum.rms_gap_to_next_class) is False
    close_classes = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "G0V", "rms": 0.05},
            {"spectral_type": "F8V", "rms": 0.06},
        ]
    )
    assert is_rms_gap_ambiguous(close_classes.rms_gap_to_next_class) is True


def test_with_one_class_compared_there_is_no_class_gap() -> None:
    """No other class means `None`, not "clearly separated"."""
    only_k = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "K5V", "rms": 0.05},
            {"spectral_type": "K7V", "rms": 0.06},
        ]
    )
    assert only_k.rms_gap_to_next_class is None
    assert rms_gap_to_next_class([]) is None


def test_a_gap_is_ambiguous_only_below_the_single_ambiguity_limit() -> None:
    """A gap at `AMBIGUOUS_RMS_GAP` is not ambiguous; just under it is."""
    assert is_rms_gap_ambiguous(AMBIGUOUS_RMS_GAP) is False
    assert is_rms_gap_ambiguous(AMBIGUOUS_RMS_GAP * 0.99) is True
    assert is_rms_gap_ambiguous(0.0) is True


def test_types_differ_on_ladder_needs_both_types_on_the_ladder() -> None:
    """M2 vs A0V differs; A3V vs A0V agrees; a missing type gives None."""
    assert types_differ_on_ladder("M2", "A0V") is True
    assert types_differ_on_ladder("A3V", "A0V") is False
    assert types_differ_on_ladder("Unknown", "A0V") is None
    assert types_differ_on_ladder("A3V", "") is None


def test_the_catalog_limit_is_the_measured_twenty_steps() -> None:
    """F8 matched as K0 (12 steps) agrees; 21 steps apart differs."""
    assert DIFFERS_FROM_CATALOG_SUBTYPES == pytest.approx(20.0)
    assert types_differ_on_ladder("K0V", "F8V") is False
    assert types_differ_on_ladder("F0V", "B0V") is False  # exactly 20 steps
    assert types_differ_on_ladder("F1V", "B0V") is True


def test_the_star_is_sent_without_match_verdicts() -> None:
    """The JSON holds the numbers and none of the removed verdicts."""
    data = StellarObject(
        id="S",
        spectral_type="A0V",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="M2", self_determined_spectral_type_rms=0.2
        ),
    ).model_dump(by_alias=True)
    assert data["spectroscopy"]["selfDeterminedSpectralTypeRms"] == pytest.approx(0.2)
    assert data["spectroscopy"]["rmsGapToSecondBest"] is None
    assert data["spectroscopy"]["rmsGapToNextClass"] is None
    for removed in ("differsFromCatalog",):
        assert removed not in data
    for removed in ("isPoorMatch", "isAmbiguous", "isClassAmbiguous", "outputQuality"):
        assert removed not in data["spectroscopy"]
    assert "selfDeterminedSpectralTypeConfidence" not in data["spectroscopy"]
    assert "candidateSeparation" not in data["spectroscopy"]


def test_a_star_saved_with_the_removed_verdicts_still_loads() -> None:
    """A stored record with the removed fields loads and drops them.

    Rows saved before the verdict fields were removed hold ``isPoorMatch``,
    ``isAmbiguous``, ``isClassAmbiguous``, ``outputQuality`` (with
    ``catalogAgrees`` and ``isTrustworthy``) on the spectrum,
    ``differsFromCatalog`` on the star, and ``spectralTypeAgrees`` and
    ``colourAgrees`` on the catalog comparison. The models ignore fields
    they no longer define, so the record still loads and keeps its science
    numbers.
    """
    stored = {
        "id": "S",
        "spectralType": "A0V",
        "differsFromCatalog": True,
        "spectroscopy": {
            "selfDeterminedSpectralType": "A3V",
            "selfDeterminedSpectralTypeRms": 0.07,
            "isPoorMatch": False,
            "isAmbiguous": True,
            "isClassAmbiguous": None,
            "outputQuality": {
                "catalogAgrees": None,
                "isAmbiguous": False,
                "isLowConfidence": False,
                "isSubtypeFinerThanResolution": None,
                "isTrustworthy": True,
            },
            "catalogComparison": {
                "spectralTypeAgrees": True,
                "colourAgrees": False,
                "colourNote": "the spectrum is redder",
                "bMinusVDifferenceMag": 0.5,
            },
        },
    }

    star = StellarObject.model_validate(stored)

    assert star.spectroscopy is not None
    assert star.spectroscopy.self_determined_spectral_type == "A3V"
    assert star.spectroscopy.self_determined_spectral_type_rms == pytest.approx(0.07)
    assert star.spectroscopy.catalog_comparison.b_minus_v_difference_mag == pytest.approx(0.5)
    dumped = star.model_dump(by_alias=True)
    assert "differsFromCatalog" not in dumped
    assert "isPoorMatch" not in dumped["spectroscopy"]
    assert "outputQuality" not in dumped["spectroscopy"]
    assert "colourAgrees" not in dumped["spectroscopy"]["catalogComparison"]
