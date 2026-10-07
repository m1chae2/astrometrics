"""Purpose: Tests for the spectral-match verdicts on the star models.

Description: Whether a spectrum's best reference type fits badly, whether
it beats the runner-up clearly, and whether it disagrees with the catalog
used to be decided in the Astronomy Manager. They are now fields of
`SpectroscopyResult` (`is_poor_match`, `candidate_separation`) and
`StellarObject` (`differs_from_catalog`), sent to the app with the star.
"""

import pytest

from astrometricslib.models.stellar_source import SpectroscopyResult, StellarObject, ladder_position


def test_the_ladder_places_a_type_by_class_and_subtype() -> None:
    """A3V sits at 23 and G2V at 42; a carbon star is off the ladder."""
    assert ladder_position("A3V") == 23
    assert ladder_position("G2V") == 42
    assert ladder_position("K") == 50
    assert ladder_position("A5V+M3") == 25
    assert ladder_position("C5") is None
    assert ladder_position(None) is None


@pytest.mark.parametrize(("rms", "poor"), [(0.05, False), (0.15, False), (0.2, True), (None, False)])
def test_a_match_more_than_15_percent_off_is_poor(rms: float | None, poor: bool) -> None:
    """The poor-match limit is 15% root-mean-square difference."""
    assert SpectroscopyResult(self_determined_spectral_type_rms=rms).is_poor_match is poor


def test_candidate_separation_ranks_by_rms_and_measures_the_gap() -> None:
    """The gap is in percentage points; 2 points or more is well separated."""
    spectrum = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "B8V", "rms": 0.063},
            {"spectral_type": "A0V", "rms": 0.06},
            {"spectral_type": "F0V", "rms": 0.2},
        ]
    )
    separation = spectrum.candidate_separation
    assert separation.runner_up_type == "B8V"
    assert separation.gap_points == pytest.approx(0.3)
    assert separation.is_well_separated is False
    clear = SpectroscopyResult(
        self_determined_spectral_type_candidates=[
            {"spectral_type": "A0V", "rms": 0.05},
            {"spectral_type": "A5V", "rms": 0.09},
        ]
    )
    assert clear.candidate_separation.is_well_separated is True
    assert SpectroscopyResult().candidate_separation is None


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
    assert data["spectroscopy"]["candidateSeparation"] is None
