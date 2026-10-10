"""Purpose: Tests for the spectral-match verdicts on the star models.

Description: Whether a spectrum's best reference type fits badly, whether
it beats the runner-up by enough RMS (root-mean-square difference), and
whether it disagrees with the catalog used to be decided in the Astronomy
Manager. They are now fields of `SpectroscopyResult` (`is_poor_match`,
`rms_gap_to_second_best`, `is_ambiguous`) and `StellarObject`
(`differs_from_catalog`), sent to the app with the star. The limits behind
them (`NO_GOOD_MATCH_RMS`, `AMBIGUOUS_RMS_GAP`,
`DIFFERS_FROM_CATALOG_SUBTYPES`) are defined once in `stellar_source`.
"""

import pytest

from astrometricslib.models.stellar_source import (
    AMBIGUOUS_RMS_GAP,
    DIFFERS_FROM_CATALOG_SUBTYPES,
    NO_GOOD_MATCH_RMS,
    SpectroscopyResult,
    StellarObject,
    is_rms_gap_ambiguous,
    ladder_position,
    rms_gap_to_next_class,
    rms_gap_to_second_best,
    types_differ_on_ladder,
)
from astrometricslib.pipelines.spectroscopy.post_processing.compare_to_catalog import compare_to_catalog


def test_the_ladder_places_a_type_by_class_and_subtype() -> None:
    """A3V sits at 23 and G2V at 42; a carbon star is off the ladder."""
    assert ladder_position("A3V") == 23
    assert ladder_position("G2V") == 42
    assert ladder_position("K") == 55
    assert ladder_position("B9.5V") == pytest.approx(19.5)
    assert ladder_position("A5V+M3") == 25
    assert ladder_position("C5") is None
    assert ladder_position(None) is None


@pytest.mark.parametrize(
    ("rms", "poor"), [(0.05, False), (NO_GOOD_MATCH_RMS, False), (0.2, True), (None, False)]
)
def test_a_match_above_the_no_good_match_limit_is_poor(rms: float | None, poor: bool) -> None:
    """The poor-match limit is `NO_GOOD_MATCH_RMS` relative RMS."""
    assert SpectroscopyResult(self_determined_spectral_type_rms=rms).is_poor_match is poor


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
    assert spectrum.is_ambiguous is True
    clear = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "A0V", "rms": 0.05},
            {"spectral_type": "A5V", "rms": 0.09},
        ]
    )
    assert clear.rms_gap_to_second_best == pytest.approx(0.04)
    assert clear.is_ambiguous is False


def test_with_fewer_than_two_candidates_there_is_no_gap_and_no_verdict() -> None:
    """No gap is `None`, which is not the same as "clearly separated"."""
    assert SpectroscopyResult().rms_gap_to_second_best is None
    assert SpectroscopyResult().is_ambiguous is None
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
    assert spectrum.is_ambiguous is True
    assert spectrum.rms_gap_to_next_class == pytest.approx(0.10)
    assert spectrum.is_class_ambiguous is False
    close_classes = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "G0V", "rms": 0.05},
            {"spectral_type": "F8V", "rms": 0.06},
        ]
    )
    assert close_classes.is_class_ambiguous is True


def test_with_one_class_compared_there_is_no_class_gap() -> None:
    """No other class means `None`, not "clearly separated"."""
    only_k = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "K5V", "rms": 0.05},
            {"spectral_type": "K7V", "rms": 0.06},
        ]
    )
    assert only_k.rms_gap_to_next_class is None
    assert only_k.is_class_ambiguous is None
    assert SpectroscopyResult().is_class_ambiguous is None
    assert rms_gap_to_next_class([]) is None


def test_a_gap_is_ambiguous_only_below_the_single_ambiguity_limit() -> None:
    """A gap at `AMBIGUOUS_RMS_GAP` is not ambiguous; just under it is."""
    assert is_rms_gap_ambiguous(AMBIGUOUS_RMS_GAP) is False
    assert is_rms_gap_ambiguous(AMBIGUOUS_RMS_GAP * 0.99) is True
    assert is_rms_gap_ambiguous(0.0) is True


def test_differs_from_catalog_needs_both_types_on_the_ladder() -> None:
    """M2 vs A0V differs; A3V vs A0V agrees; a missing type gives None."""

    def star(own: str, catalog: str) -> StellarObject:
        """Build a star with a matched and a catalog type.

        Returns
        -------
        star : `StellarObject`
            The star.
        """
        return StellarObject(
            id="S", spectral_type=catalog, spectroscopy=SpectroscopyResult(self_determined_spectral_type=own)
        )

    assert star("M2", "A0V").differs_from_catalog is True
    assert star("A3V", "A0V").differs_from_catalog is False
    assert star("Unknown", "A0V").differs_from_catalog is None
    assert star("A3V", "").differs_from_catalog is None


def test_differs_from_catalog_uses_the_measured_twenty_step_limit() -> None:
    """F8 matched as K0 (12 steps) agrees; 21 steps apart differs."""
    assert DIFFERS_FROM_CATALOG_SUBTYPES == pytest.approx(20.0)
    assert types_differ_on_ladder("K0V", "F8V") is False
    assert types_differ_on_ladder("F0V", "B0V") is False  # exactly 20 steps
    assert types_differ_on_ladder("F1V", "B0V") is True


@pytest.mark.parametrize(
    ("own", "catalog"),
    [("K0V", "F8V"), ("M2V", "B7III"), ("K4V", "B9.5V"), ("A3V", "A0V"), ("G2V", "K"), ("F0V", "B0V")],
)
def test_the_model_field_and_the_catalog_comparison_never_contradict(own: str, catalog: str) -> None:
    """`differs_from_catalog` and `spectral_type_agrees` use one rule."""
    star = StellarObject(
        id="S", spectral_type=catalog, spectroscopy=SpectroscopyResult(self_determined_spectral_type=own)
    )
    comparison = compare_to_catalog(catalog, own, None, None, None)
    assert comparison.spectral_type_agrees is (not star.differs_from_catalog)


def test_the_verdicts_are_sent_with_the_star() -> None:
    """The app reads the camelCase fields from the star's JSON."""
    data = StellarObject(
        id="S",
        spectral_type="A0V",
        spectroscopy=SpectroscopyResult(
            self_determined_spectral_type="M2", self_determined_spectral_type_rms=0.2
        ),
    ).model_dump(by_alias=True)
    assert data["differsFromCatalog"] is True
    assert data["spectroscopy"]["isPoorMatch"] is True
    assert data["spectroscopy"]["rmsGapToSecondBest"] is None
    assert data["spectroscopy"]["isAmbiguous"] is None
    assert data["spectroscopy"]["rmsGapToNextClass"] is None
    assert data["spectroscopy"]["isClassAmbiguous"] is None
    assert "selfDeterminedSpectralTypeConfidence" not in data["spectroscopy"]
    assert "candidateSeparation" not in data["spectroscopy"]
