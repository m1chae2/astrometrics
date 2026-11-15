"""Purpose: Test the classification tuners and the statistics they use.

Description: Issue #175 Phase D. The functions in `classification_tuning.py`
take plain numbers, so these tests give them synthetic scores with a known
answer, and check that each tuner refuses to run without data.
"""

from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.foundation.tuning import Tuner, TuningInputs, TuningSkippedError
from astrometricslib.pipelines.spectroscopy import classification_tuning

ALL_TUNERS = (
    classification_tuning.MATCH_LIMITS_TUNER,
    classification_tuning.AMBIGUITY_GAP_TUNER,
    classification_tuning.CLASSIFICATION_LIMITS_TUNER,
)


class _EmptyCatalog:
    """A library catalog that holds no stars."""

    def list_star_summaries(self) -> list:
        """List the stars.

        Returns
        -------
        summaries : `list`
            Always empty.
        """
        return []


def test_separating_limit_sits_between_the_groups() -> None:
    """Right matches below 0.1 and wrong ones above 0.2 give 0.15."""
    assert classification_tuning.separating_limit([0.05, 0.1], [0.2, 0.3]) == pytest.approx(0.15)


def test_separating_limit_needs_both_groups() -> None:
    """Without wrong matches there is nothing to separate."""
    assert classification_tuning.separating_limit([0.05, 0.1], []) is None


def test_limit_above_which_untyped_stops_where_the_share_fails() -> None:
    """The limit is the lowest score from which 90% above it are untyped."""
    typed = [0.1, 0.2, 0.3]
    untyped = [0.5, 0.6, 0.7]
    assert classification_tuning.limit_above_which_untyped(typed, untyped, 0.9) == pytest.approx(0.3)


def test_limit_above_which_untyped_is_none_when_the_top_is_typed() -> None:
    """If the stars at the top have catalog types the share never holds."""
    assert classification_tuning.limit_above_which_untyped([0.9, 0.8], [0.1], 0.9) is None


def test_smallest_neighbour_distance_matches_a_hand_calculation() -> None:
    """A ramp against a constant is 0.447 of the mean apart after scaling."""
    wavelength = np.array([0.0, 1.0, 2.0, 3.0])
    smallest, pair_count = classification_tuning.smallest_neighbour_distance(
        [wavelength, wavelength], [np.array([1.0, 2.0, 3.0, 4.0]), np.ones(4)], 0.0, 3.0
    )
    assert smallest == pytest.approx(np.sqrt(1.25) / 2.5)
    assert pair_count == 1


def test_coverage_levels_run_down_to_the_lowest() -> None:
    """The levels start at the whole spectrum and stop at the lowest."""
    assert classification_tuning.coverage_levels(1000.0, 200.0, 500.0) == [1000.0, 800.0, 600.0]


def test_last_stable_index_stops_at_the_first_change() -> None:
    """Later agreement after a change does not count."""
    assert classification_tuning.last_stable_index(["A", "A", "F", "A"], "A") == 1
    assert classification_tuning.last_stable_index(["F", "A"], "A") == -1


def test_range_from_extents_rounds_outward_and_stays_inside_the_references() -> None:
    """The range is the typical extent, rounded out, cut to the references."""
    result = classification_tuning.range_from_extents(
        [3830.0, 3850.0], [9440.0, 9470.0], 3000.0, 9400.0, 100.0
    )
    assert result == (3800.0, 9400.0)


@pytest.mark.parametrize("tuner", ALL_TUNERS, ids=lambda tuner: tuner.name)
def test_every_tuner_skips_without_stars(tuner: Tuner) -> None:
    """With no stored spectra a tuner raises `TuningSkippedError`."""
    astrometrics = SimpleNamespace(_catalog_access=_EmptyCatalog())
    inputs = TuningInputs(
        SimpleNamespace(camera_name="Test camera"), "SPEC", None, {"astrometrics": astrometrics}
    )
    with pytest.raises(TuningSkippedError):
        tuner.tune(inputs)


def test_no_tuned_parameter_of_these_tuners_is_pending() -> None:
    """Every parameter a tuner sets has its range and sample size."""
    for tuner in ALL_TUNERS:
        for parameter in tuner.parameters:
            assert not parameter.pending_conversion, parameter.name
            assert parameter.allowed_range is not None
            assert parameter.minimum_sample is not None
